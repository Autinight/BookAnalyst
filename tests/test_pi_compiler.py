"""Exercise the real pi.dev SDK through a local model endpoint and native tools."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from bookanalyst.finisher import repair_project
from bookanalyst.store import WorkflowError, atomic_json
from bookanalyst.tex import compile_tex
from test_rebuild import make_run


@pytest.fixture
def model_server():
    received = []
    controls = {"error": False, "hold": False, "compaction": False, "agent_calls": 0}
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            if self.path.endswith("/responses"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                part = {"type": "output_text", "text": "Responses connected.", "annotations": []}
                message = {"id": "msg-test", "type": "message", "role": "assistant", "status": "completed", "content": [part]}
                response = {"id": "resp-test", "object": "response", "model": "pi-test-model", "status": "completed", "output": [message], "usage": {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18}}
                events = [
                    {"type": "response.created", "response": response | {"status": "in_progress", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0, "item": message | {"status": "in_progress", "content": []}},
                    {"type": "response.content_part.added", "item_id": "msg-test", "output_index": 0, "content_index": 0, "part": part | {"text": ""}},
                    {"type": "response.output_text.delta", "item_id": "msg-test", "output_index": 0, "content_index": 0, "delta": "Responses connected."},
                    {"type": "response.output_text.done", "item_id": "msg-test", "output_index": 0, "content_index": 0, "text": "Responses connected."},
                    {"type": "response.output_item.done", "output_index": 0, "item": message},
                    {"type": "response.completed", "response": response},
                ]
                for event in events:
                    self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                self.wfile.flush()
                return
            if controls["error"]:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"test model rejected tools"}}')
                return
            if controls["hold"] and len(received) >= 2:
                release.wait(20)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            operations = [
                ("read", {"path": "main.tex"}),
                ("edit", {"path": "main.tex", "oldText": r"\badcommand", "newText": "Repaired"}),
                ("powershell", {"command": "xelatex -no-shell-escape -interaction=nonstopmode -file-line-error main.tex"}),
            ]
            index = controls["agent_calls"]
            if not payload.get("tools"):
                delta = {"role": "assistant", "content": "Summary: main.tex has an undefined macro. Read the current file, repair it and compile."}
                finish = "stop"
            elif index < len(operations):
                controls["agent_calls"] += 1
                name, args = operations[index]
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
                if controls["compaction"] and index == 0:
                    delta["content"] = "Earlier investigation notes. " * 4000
            else:
                controls["agent_calls"] += 1
                delta = {"role": "assistant", "content": "Compilation repaired."}
                finish = "stop"
            input_tokens = 127000 if controls["compaction"] and len(received) == 1 else 10
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": input_tokens, "completion_tokens": 5, "total_tokens": input_tokens + 5}},
            ]
            try:
                for chunk in chunks:
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", received, controls
    release.set()
    server.shutdown()
    server.server_close()


def ready(app, url):
    store = app.state.store
    run = make_run(app)
    run["config"]["model"] = {"connection_id": "custom_api", "model_id": "pi-test-model", "reasoning_effort": "high"}
    run["config"]["structure_effort"] = "max"
    run["state"] = "RUNNING"
    store.put("run", run["id"], run)
    settings = store.get("settings", "main")
    settings["connections"]["custom_api"].update(enabled=True, base_url=url, auth_mode="none", image_support="supported", timeout_seconds=20)
    store.put("settings", "main", settings)
    project = store.directory(run["id"]) / "tex"
    project.mkdir(parents=True)
    (project / "main.tex").write_text(r"\documentclass{article}\begin{document}\badcommand\end{document}", encoding="utf-8")
    return store, run, project


@pytest.mark.asyncio
@pytest.mark.parametrize("template_job", [False, True])
async def test_pi_native_tools_compile_and_resume_same_session(app, model_server, template_job):
    url, requests, _ = model_server
    store, run, project = ready(app, url)
    if template_job:
        run['kind'] = 'template'
        store.put('run', run['id'], run)
        atomic_json(project.parent / 'template.json', {'entrypoint':'main.tex','description':'Use compact typography'})
    atomic_json(project / "bibliography-review.json", [{"key": "bibliography:MM", "reason": "Unconfirmed"}])
    await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, {"status": "FAILED"}), 90)
    assert "Repaired" in (project / "main.tex").read_text(encoding="utf-8")
    assert (project / "main.pdf").exists()
    assert (await compile_tex(project))["status"] == "PASSED"
    state = json.loads((project.parent / "pi-compiler.json").read_text(encoding="utf-8"))
    original_session = state["thread_id"]
    assert Path(state["session_file"]).exists()
    first = store.calls(run["id"])[0]
    assert first["state"] == "COMPLETED" and first["metadata"]["agent"] == "pi"
    assert first["metadata"]["usage"]["total_tokens"] == 60
    assert len(requests) == 4
    assert "reserved for USER confirmation" in json.dumps(requests[0])
    if template_job:
        assert 'byte-for-byte' in json.dumps(requests[0])
        assert 'template-example' in json.dumps(requests[0])
        assert first['metadata']['purpose'] == 'template_apply' 
    assert "Do not investigate" in json.dumps(requests[0])
    assert all(r["model"] == "pi-test-model" for r in requests)
    assert requests[0]["reasoning_effort"] == "max"
    assert "tool_calls" in json.dumps(requests[-1])
    await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, {"status": "FAILED", "message": "Check current files again"}), 45)
    state = json.loads((project.parent / "pi-compiler.json").read_text(encoding="utf-8"))
    assert state["thread_id"] == original_session
    assert "Compilation repaired." in json.dumps(requests[-1])
    assert "reserved for USER confirmation" in json.dumps(requests[-1])
    assert store.calls(run["id"])[1]["metadata"]["usage"]["total_tokens"] == 15
    assert not (project.parent / "codex-compiler.json").exists()


@pytest.mark.asyncio
async def test_pi_keeps_upstream_error(app, model_server):
    url, _, controls = model_server
    controls["error"] = True
    store, run, project = ready(app, url)
    with pytest.raises(WorkflowError, match="test model rejected tools"):
        await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, {"status": "FAILED"}), 45)
    call = store.calls(run["id"])[0]
    assert call["state"] == "FAILED"
    assert "test model rejected tools" in call["metadata"]["error_message"]
    assert json.loads((project.parent / "pi-compiler.json").read_text(encoding="utf-8"))["status"] == "FAILED"


@pytest.mark.asyncio
async def test_pi_pause_aborts_and_keeps_session(app, model_server):
    url, requests, controls = model_server
    controls["hold"] = True
    store, run, project = ready(app, url)
    task = asyncio.create_task(repair_project(app.state.engine.providers, run, project, {"status": "FAILED"}))
    try:
        async with asyncio.timeout(40):
            while len(requests) < 2:
                if task.done():
                    await task
                await asyncio.sleep(0.05)
        store.change(run["id"], lambda r: r.update(pause_requested=True))
        with pytest.raises(WorkflowError) as stopped:
            await asyncio.wait_for(task, 25)
        assert stopped.value.code == "PAUSED"
        state = json.loads((project.parent / "pi-compiler.json").read_text(encoding="utf-8"))
        assert state["status"] == "INTERRUPTED" and Path(state["session_file"]).exists()
        assert store.calls(run["id"])[0]["state"] == "FAILED"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pi_automatically_compacts_and_records_usage(app, model_server):
    url, requests, controls = model_server
    controls["compaction"] = True
    store, run, project = ready(app, url)
    feedback = {"status": "FAILED"}
    await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, feedback), 90)
    call = store.calls(run["id"])[0]
    events = store.root / "runs" / run["id"] / "requests" / call["id"] / "events.jsonl"
    entries = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "compaction_start" for e in entries)
    compacted = next(e for e in entries if e["type"] == "compaction_end")
    assert compacted["result"]["summary"]
    assert call["metadata"]["usage"]["total_tokens"] == 127005 + 15 * (len(requests) - 1)
    assert (project / "main.pdf").exists()


@pytest.mark.asyncio
async def test_pi_responses_protocol_preserves_model_and_effort(app, model_server):
    url, requests, _ = model_server
    store, run, project = ready(app, url)
    settings = store.get("settings", "main")
    settings["connections"]["custom_api"]["protocol"] = "responses"
    store.put("settings", "main", settings)
    await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, {"status": "FAILED"}), 45)
    assert len(requests) == 1
    assert requests[0]["model"] == "pi-test-model"
    assert requests[0]["reasoning"]["effort"] == "max"
    assert requests[0]["stream"] is True and requests[0]["tools"]
    call = store.calls(run["id"])[0]
    assert call["state"] == "COMPLETED"
    assert call["metadata"]["usage"]["total_tokens"] == 18
