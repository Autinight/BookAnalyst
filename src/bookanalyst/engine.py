"""Durable ordered execution; only PROGRAM can commit stage state and artifacts."""
import asyncio
import copy
import json
import time
from pathlib import Path

from .models import STAGES
from .store import WorkflowError, atomic_json, digest, file_hash
from .pipeline import Pipeline

GATES = [["G00"], ["G10", "G11"], ["G20", "G21", "G22"], ["G30", "G31"],
         ["G40", "G41"], ["G50", "G51"], ["G60", "G61", "G62"], ["G70", "G71"], ["G80"]]


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
                if current == run["config"].get("stop_after"):
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
        def apply(run):
            run["state"] = state
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
            if any(c["kind"] == "llm" for c in unresolved):
                raise WorkflowError("RESULT_UNKNOWN", "已有结果未知的模型请求，需先核对并登记远端结果", review=True)
            old_revision = run["revision"]
            historical = copy.deepcopy({"revision": old_revision, "stages": run["stages"], "config": run["config"]})
            if command.additional_visual_pages:
                if command.stage != "S2" or any(p < run["config"]["start_page"] or p > run["config"]["end_page"]
                                                for p in command.additional_visual_pages):
                    raise WorkflowError("INVALID_VISUAL_SCOPE", "New image targets must be in scope and resume at S2", 422)
                run["config"]["visual_pages"] = sorted(set(run["config"]["visual_pages"] + command.additional_visual_pages))
                if run["config"]["visual_mode"] == "disabled":
                    run["config"]["visual_mode"] = "targeted"
            if command.task_id:
                count = run["repair_counts"].get(command.task_id, 0)
                if count >= 2:
                    raise WorkflowError("REPAIR_LIMIT", "该片段已达到 2 轮修正上限", review=True)
                run["repair_counts"][command.task_id] = count + 1
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
            run["force_recompute"] = command.force_recompute and not command.task_id
            for stage in STAGES[index:]:
                run["stages"][stage] = {"state": "PENDING", "invalidated_from": old_revision}
            run["state"], run["result"], run["findings"] = "RUNNING", None, []
            run["events"].append({"time": time.time(), "state": "REVISION_CREATED", "reason": command.reason})
        self.store.operation(run_id, command.revision, command.operation_id, apply)
        return self.launch(run_id)
