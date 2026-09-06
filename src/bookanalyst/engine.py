"""Durable ordered execution; only PROGRAM can commit stage state and artifacts."""
import asyncio
import copy
import json
import time
from pathlib import Path

from .models import STAGES
from .store import WorkflowError, atomic_json, digest, file_hash
from .pipeline import Pipeline

GATES = [['G00'], ['G10', 'G11'], ['G20', 'G21'], ['G30'], ['G40'], ['G50'], ['G60'], ['G70', 'G71'], ['G80']]


class Engine:
    def __init__(self, store, workspace, providers, mineru):
        self.store, self.workspace = store, Path(workspace)
        self.providers, self.mineru = providers, mineru
        self.running = {}
        self.pipeline = Pipeline(self)
        self.store.recover()

    async def close(self):
        for task in list(self.running.values()):
            task.cancel()
        await asyncio.gather(*self.running.values(), return_exceptions=True)
        await self.providers.close()

    def launch(self, run_id):
        if run_id not in self.running or self.running[run_id].done():
            self.running[run_id] = asyncio.create_task(self.execute(run_id))
        return self.store.get("run", run_id)

    async def execute(self, run_id):
        run = self.store.get("run", run_id)
        revision = run["revision"]
        if run["state"] != "RUNNING":
            return
        semaphore = asyncio.Semaphore(run["config"]["llm_concurrency"])
        current = "S0"
        try:
            if run.get("workflow_version") != "0.5":
                raise WorkflowError("WORKFLOW_VERSION", "旧流程运行请使用原执行器；新版需建立独立运行并复用解析")
            for current in STAGES:
                run = self.store.get("run", run_id)
                if run["revision"] != revision:
                    return
                if run["stages"][current]["state"] == "PASSED":
                    self.store.artifact_dir(run, current)
                    continue
                self.store.transition(run_id, revision, current, "RUNNING")
                run = self.store.get("run", run_id)
                outputs = await getattr(self.pipeline, current)(run, semaphore)
                run = self.store.get("run", run_id)
                inputs = {"source_sha256": run["source"]["sha256"]}
                inputs.update({s: run["stages"][s]["manifest_hash"]
                               for s in STAGES[:STAGES.index(current)]})
                self.store.commit(run_id, revision, current, outputs,
                                  GATES[STAGES.index(current)], inputs)
                if run.get("pause_requested") or current == run["config"].get("stop_after"):
                    self.store.change(run_id, lambda r: r.update(state="PAUSED"), revision)
                    return
                if current == "S1" and run["config"]["profile"] in ("cloud_smoke", "local_smoke"):
                    refreshed = self.store.get("run", run_id)
                    atoms, mapping = self.pipeline.normalized(refreshed)
                    atomic_json(self.store.directory(run_id, revision, "S2") / "s2_checks.json",
                                {"json_structure": True, "resource_integrity": True, "id_uniqueness": True,
                                 "source_mapping": True, "atom_count": len(atoms),
                                 "semantic_checks": False, "phase_state": "PENDING"})
                    self.store.change(run_id, lambda r: r.update(state="TEST_COMPLETED"), revision)
                    return
            self.store.change(run_id, lambda r: r.update(
                state="FIXTURE_PASSED" if r["scope"] == "fixture" else
                "BOOK_ACCEPTED" if r["scope"] == "full" else "SAMPLE_ACCEPTED"), revision)
        except asyncio.CancelledError:
            self.fail(run_id, revision, current, WorkflowError("INTERRUPTED", "执行器已停止，需恢复运行", review=True))
            raise
        except WorkflowError as exc:
            self.fail(run_id, revision, current, exc)
        except Exception as exc:
            # Never expose provider exception strings, which can contain signed URLs or headers.
            self.fail(run_id, revision, current, WorkflowError(
                "EXECUTION_ERROR", f"阶段执行异常（{type(exc).__name__}），请检查该阶段输入和产物"))

    def fail(self, run_id, revision, stage, error):
        state = "NEEDS_REVIEW" if error.review else "FAILED"
        run_state = "PAUSED" if error.code == "WORKFLOW_PAUSED" else state
        def apply(run):
            run["state"] = run_state
            phase = run["stages"][stage]
            if phase["state"] == "RUNNING":
                phase["state"] = state
            phase["error"] = {"code": error.code, "message": error.message}
            run["events"].append({"time": time.time(), "stage": stage, "state": state,
                                  "code": error.code, "message": error.message})
        try:
            self.store.change(run_id, apply, revision)
        except WorkflowError:
            pass

    def start(self, run_id, command):
        def apply(run):
            if run["state"] != "PENDING":
                raise WorkflowError("ALREADY_STARTED", "该运行已启动；失败或待审查运行请使用恢复操作")
            run["state"] = "RUNNING"
        self.store.operation(run_id, command.revision, command.operation_id, apply)
        return self.launch(run_id)

    def pause(self, run_id, command):
        def apply(run):
            if run["state"] != "RUNNING":
                raise WorkflowError("NOT_RUNNING", "当前没有正在执行的运行")
            run["pause_requested"] = True
            run["events"].append({"time": time.time(), "state": "PAUSE_REQUESTED",
                "message": "不再派发新请求，等待在途请求返回后保存结果"})
        return self.store.operation(run_id, command.revision, command.operation_id, apply)

    def preview(self, run_id, stage, task_id=None):
        run = self.store.get("run", run_id)
        tasks = []
        if run["stages"]["S5"]["state"] == "PASSED":
            tasks = self.store.artifact(run, "S5", "tasks.json")["tasks"]
        if task_id and (stage != "S6" or task_id not in {t["task_id"] for t in tasks}):
            raise WorkflowError("INVALID_TASK", "局部任务只适用于 S6，且任务必须存在", 422)
        return {"revision": run["revision"], "stages": STAGES[STAGES.index(stage):],
                "task_ids": [task_id] if task_id else [t["task_id"] for t in tasks],
                "usage": run["usage"], "scope": "fragment_and_boundaries" if task_id else "stage_and_dependencies"}

    def rerun(self, run_id, command):
        self.preview(run_id, command.stage, command.task_id)
        def apply(run):
            if run["state"] == "RUNNING":
                raise WorkflowError("RUNNING", "请等待当前执行结束后重跑")
            index = STAGES.index(command.stage)
            if any(run["stages"][s]["state"] != "PASSED" for s in STAGES[:index]):
                raise WorkflowError("PRECONDITION", "请选择首个未通过的阶段恢复")
            unresolved = [c for c in self.store.calls(run_id) if c["state"] in ("RESERVED", "RESULT_UNKNOWN")]
            allowed = set(run.get("authorized_unknown_retries", []))
            if command.retry_unrecoverable:
                missing = [c["id"] for c in unresolved if c["kind"] == "llm" and not
                    (self.store.root / "runs" / run_id / "requests" / c["id"] / "upstream.json").exists()]
                allowed.update(missing)
                run["authorized_unknown_retries"] = sorted(allowed)
                run["events"].append({"time": time.time(), "state": "UNKNOWN_RETRY_AUTHORIZED",
                    "call_ids": missing, "reason": command.reason,
                    "note": "原请求仍为未知；旧用量保留。只重发缺少恢复句柄的请求。"})
            if any(c["kind"] == "llm" and c["id"] not in allowed for c in unresolved):
                raise WorkflowError("RESULT_UNKNOWN", "已有结果未知的模型请求，需先核对并登记远端结果", review=True)
            released = set(run.get("released_interrupted_repairs", []))
            for call in self.store.calls(run_id):
                if (call["id"] in released or call["state"] != "FAILED"
                        or call["metadata"].get("error_code") != "UPSTREAM_INTERRUPTED"
                        or call["metadata"].get("purpose") != "convert_pages"):
                    continue
                directory = self.store.root / "runs" / run_id / "requests" / call["id"]
                if not (directory / "request.json").exists() or (directory / "response.json").exists():
                    continue
                request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                payload = json.loads(request["prompt"])
                task_id = payload.get("task", {}).get("task_id")
                if payload.get("error") and task_id:
                    run["repair_counts"][task_id] = max(0, run["repair_counts"].get(task_id, 0) - 1)
                    released.add(call["id"])
                    run["events"].append({"time": time.time(), "state": "INTERRUPTED_REPAIR_RELEASED",
                        "call_id": call["id"], "task_id": task_id,
                        "note": "上游确认中断且无候选；恢复内容修正轮次，真实请求用量不变"})
            run["released_interrupted_repairs"] = sorted(released)
            old_revision = run["revision"]
            historical = copy.deepcopy({"revision": old_revision, "stages": run["stages"], "config": run["config"]})
            if command.llm_concurrency is not None:
                run["config"]["llm_concurrency"] = command.llm_concurrency
            if command.additional_visual_pages:
                if command.stage != "S2" or any(p < run["config"]["start_page"] or p > run["config"]["end_page"]
                                                for p in command.additional_visual_pages):
                    raise WorkflowError("INVALID_VISUAL_SCOPE", "New image targets must be in scope and resume at S2", 422)
                run["config"]["visual_pages"] = sorted(set(run["config"]["visual_pages"] + command.additional_visual_pages))
                if run["config"]["visual_mode"] == "disabled":
                    run["config"]["visual_mode"] = "targeted"
            # A manual retry renews exhausted automatic repair allowances. Keep
            # cumulative counts and call usage so history remains truthful.
            if index <= STAGES.index("S7"):
                limits = run.setdefault("repair_limits", {})
                renewed = {}
                for task_id, count in run["repair_counts"].items():
                    if command.task_id and task_id != command.task_id:
                        continue
                    if count >= limits.get(task_id, 2):
                        limits[task_id] = count + 2
                        renewed[task_id] = {"used": count, "limit": count + 2}
                if renewed:
                    run["events"].append({"time": time.time(), "state": "REPAIR_ALLOWANCE_RENEWED",
                        "tasks": renewed, "reason": command.reason,
                        "note": "手动恢复为耗尽的批次追加两轮自动修复；累计次数与实际用量保留"})
            for field in ("max_llm_requests", "max_parse_submissions", "max_submitted_pages"):
                proposed = getattr(command, field)
                if proposed is not None:
                    if run["config"][field] is None or proposed < run["config"][field]:
                        raise WorkflowError("INVALID_BUDGET", "恢复时只能明确增加预算", 422)
                    if run["config"]["profile"] == "llm_smoke" and field == "max_llm_requests" and proposed > 8:
                        raise WorkflowError("PROFILE_LIMIT", "LLM 最小测试最多 8 次请求", 422)
                    if run["config"]["profile"] in ("cloud_smoke", "local_smoke"):
                        caps = {"max_llm_requests": 0, "max_parse_submissions": 1,
                                "max_submitted_pages": 3 if run["config"]["profile"] == "cloud_smoke" else 1}
                        if proposed > caps[field]:
                            raise WorkflowError("PROFILE_LIMIT", "不能提升最小解析测试的固定上限", 422)
                    run["config"][field] = proposed
            run.setdefault("history", []).append(historical)
            run["revision"] += 1
            run["retry_from_revision"] = old_revision
            run["rerun_task_id"] = command.task_id
            run["rerun_reason"] = command.reason
            run["force_recompute"] = command.force_recompute and not command.task_id
            for stage in STAGES[index:]:
                run["stages"][stage] = {"state": "PENDING", "invalidated_from": old_revision}
            run["state"], run["result"], run["findings"] = "RUNNING", None, []
            run["pause_requested"] = False
            run["events"].append({"time": time.time(), "state": "REVISION_CREATED", "reason": command.reason})
        self.store.operation(run_id, command.revision, command.operation_id, apply)
        return self.launch(run_id)
