"""Process adapter for pi.dev; Pi owns model/tool iterations and compaction."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

from .store import WorkflowError, atomic_json
from .reference_review import compiler_reference_policy


def node_executable():
    node = shutil.which("node")
    if not node and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs/node.exe"
        if candidate.exists():
            node = str(candidate)
    if not node:
        raise WorkflowError("DEPENDENCY_MISSING", "Pi 编译需要 Node.js 22.19 或更高版本")
    return node


async def launch(config):
    runtime = Path(__file__).parent / "pi_runtime"
    if not (runtime / "node_modules/@earendil-works/pi-coding-agent").exists():
        raise WorkflowError("DEPENDENCY_MISSING", f"请先运行 npm ci --prefix {runtime} --ignore-scripts")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(
        node_executable(), str(runtime / "agent.mjs"), cwd=config["project"],
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024, **kwargs,
    )
    process.stdin.write((json.dumps(config, ensure_ascii=False) + "\n").encode("utf-8"))
    await process.stdin.drain()
    return process


def event_activity(event):
    kind = event["type"]
    if kind.startswith("tool_execution"):
        args = event.get("args", {})
        return f"{event.get('toolName', 'tool')} · {args.get('command') or args.get('path') or args.get('pattern') or ''}"[:500]
    if kind == "auto_retry_start":
        return f"重试 {event['attempt']}/{event['maxAttempts']} · {event.get('errorMessage', '')}"[:500]
    if kind == "compaction_start":
        return "正在压缩会话上下文"
    if kind == "compaction_end":
        return "上下文压缩已结束"
    if kind == "message_end":
        return "".join(c.get("text", "") for c in event.get("message", {}).get("content", []) if c.get("type") == "text")[:500]
    return ""


async def repair_project(providers, run, project, feedback, instructions, binding=None, purpose=None):
    store = providers.store
    purpose = purpose or ("image_repair" if run.get("image_repair", {}).get("mode") == "special"
                          else "template_apply" if run.get("kind") == "template" else "compile_repair")
    if binding is None:
        from .model_config import request_run
        run = await request_run(providers, run["id"], purpose, require_image=purpose == "image_repair")
        binding = run["config"]["model"]
    conn = providers.connection(binding["connection_id"])
    if conn["kind"] == "grok_oauth":
        conn = await providers.grok_http_conn(
            binding["connection_id"], conn, binding.get("model_id") or ""
        )
        key = conn["_access_token"]
    else:
        key = providers.api_key(binding["connection_id"], conn) if conn.get("auth_mode") == "bearer" else ""
        if conn.get("auth_mode") == "bearer" and not key:
            raise WorkflowError("AUTH_REQUIRED", "请配置自定义 API 凭据")
    state_path = project.parent / "pi-compiler.json"
    session = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    prompt = (f"Current project: {project.resolve()}\n"
              f"Source PDF (read only): {run['source']['path']}\n"
              f"Compiler result: {json.dumps(feedback, ensure_ascii=False)}\n"
              + ("Organize the project according to the instructions. The application validates and recompiles."
                 if run.get("kind") == "tex_structure" else
                 "Read the actual files as needed. Repair and compile until successful."))
    if run.get("kind") != "tex_structure":
        prompt += compiler_reference_policy(project)
    metadata = dict(agent="pi", purpose=purpose, phase="repair", role="model",
                    task_id="finish-project", model_id=binding["model_id"],
                    connection_id=binding["connection_id"], repair=True, attempt=1,
                    reasoning_effort=binding.get("reasoning_effort", "medium"))
    model_meta = next((m for m in conn.get("models", []) if m["id"] == binding["model_id"]), {})
    config = dict(project=str(project.resolve()), agent_dir=str((store.root / "pi-agent").resolve()),
                  session_dir=str((project.parent / "pi-sessions").resolve()),
                  session_file=session.get("session_file"), instructions=instructions, prompt=prompt,
                  model_id=binding["model_id"], effort=metadata["reasoning_effort"],
                  base_url=conn["base_url"].rstrip("/"), protocol=conn["protocol"],
                  auth_mode=conn["auth_mode"], api_key=key,
                  image_support="unsupported" if model_meta.get("image") is False else "supported",
                  model_meta=model_meta, timeout_seconds=conn["timeout_seconds"],
                  headers=(conn.get("headers") or {}) | (conn.get("_extra_headers") or {}))
    # Anthropic's SDK appends /v1 itself; the HTTP worker accepts either root form.
    if conn["protocol"] == "anthropic":
        config["base_url"] = config["base_url"].removesuffix("/v1")
    if store.get("run", run["id"]).get("pause_requested"):
        raise WorkflowError("PAUSED", "已保存当前工程")
    call_id = store.reserve(run["id"], run["revision"], "llm", 1, metadata)
    directory = store.root / "runs" / run["id"] / "requests" / call_id
    atomic_json(directory / "request.json", metadata | {"prompt": prompt})
    session.update(agent="pi", call_id=call_id, status="RUNNING", activity="正在启动 Pi")
    session.pop("error", None)
    atomic_json(state_path, session)
    process = watcher = stderr_reader = None
    stderr_tail = bytearray()
    paused = False
    stop_lock = asyncio.Lock()
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_input_tokens": 0}
    reported = False

    async def stop():
        if not process:
            return
        async with stop_lock:
            if process.returncode is not None:
                return
            try:
                process.stdin.write(b'{"type":"abort"}\n')
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                if os.name == "nt":
                    await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.kill()
                await process.wait()

    async def watch_pause():
        nonlocal paused
        while True:
            if store.get("run", run["id"]).get("pause_requested"):
                paused = True
                await stop()
                return
            await asyncio.sleep(0.5)

    async def read_stderr():
        while chunk := await process.stderr.read(4096):
            stderr_tail.extend(chunk)
            del stderr_tail[:-8192]

    try:
        process = await launch(config)
        config.pop("api_key", None)
        watcher = asyncio.create_task(watch_pause())
        stderr_reader = asyncio.create_task(read_stderr())
        done = None
        with (directory / "events.jsonl").open("a", encoding="utf-8") as journal:
            while line := await process.stdout.readline():
                event = json.loads(line)
                journal.write(json.dumps(event, ensure_ascii=False) + "\n")
                journal.flush()
                if event["type"] == "ready":
                    session.update(thread_id=event["session_id"], session_file=event["session_file"],
                                   context_window=event["context_window"], max_output_tokens=event["max_output_tokens"],
                                   thinking_level=event["thinking_level"])
                    atomic_json(directory / "upstream.json", {"agent": "pi", "session_id": event["session_id"], "session_file": event["session_file"]})
                if event["type"] == "done":
                    done = event
                activity = event_activity(event)
                if activity:
                    session["activity"] = activity
                message = event.get("message", {})
                tokens = message.get("usage") if event["type"] == "message_end" and message.get("role") == "assistant" else None
                if event["type"] == "compaction_end":
                    tokens = (event.get("result") or {}).get("usage")
                if tokens:
                    reported = True
                    usage["input_tokens"] += tokens.get("input", 0) + tokens.get("cacheRead", 0) + tokens.get("cacheWrite", 0)
                    usage["output_tokens"] += tokens.get("output", 0)
                    usage["total_tokens"] += tokens.get("totalTokens", 0)
                    usage["cached_input_tokens"] += tokens.get("cacheRead", 0)
                    if "reasoning" in tokens:
                        usage["reasoning_tokens"] = usage.get("reasoning_tokens", 0) + tokens["reasoning"]
                atomic_json(state_path, session)
        await process.wait()
        await stderr_reader
        if paused or (done or {}).get("status") == "INTERRUPTED":
            raise WorkflowError("PAUSED", "Pi 已停止，当前文件和会话已保存")
        if not done:
            detail = stderr_tail.decode("utf-8", errors="replace")
            raise WorkflowError("PI_ERROR", detail or "Pi 未返回结束事件；当前文件和会话已保留")
        if done["status"] != "COMPLETED" or process.returncode:
            raise WorkflowError("PI_ERROR", done.get("error") or "Pi 编译任务失败")
        atomic_json(directory / "response.json", {"text": done.get("text", ""), "usage": usage if reported else None})
        store.finish_call(call_id, "COMPLETED", usage=usage if reported else None)
        session["status"] = "COMPLETED"
    except BaseException as exc:
        await asyncio.shield(stop())
        interrupted = isinstance(exc, asyncio.CancelledError) or (isinstance(exc, WorkflowError) and exc.code == "PAUSED")
        error = WorkflowError("PAUSED", "Pi 已停止，当前文件和会话已保存") if interrupted else (
            exc if isinstance(exc, WorkflowError) else WorkflowError("PI_ERROR", str(exc)))
        session.update(status="INTERRUPTED" if interrupted else "FAILED", error={"code": error.code, "message": error.message})
        store.finish_call(call_id, "FAILED", error_code=error.code, error_message=error.message, usage=usage if reported else None)
        atomic_json(directory / "error.json", session["error"])
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise error from exc
    finally:
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if stderr_reader:
            await stderr_reader
        atomic_json(state_path, session)
