"""Native tool events and durable Codex thread ownership."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from bookanalyst.finisher import repair_project
from bookanalyst.store import WorkflowError, atomic_json
from test_request_retry import ready_run


def event(method, data):
    return SimpleNamespace(method=method, payload=SimpleNamespace(model_dump=lambda **_: data))


class NativeTurn:
    id = "turn-native"

    def __init__(self, project, pause=False, failed=False, tokens=7):
        self.project, self.pause, self.failed = project, pause, failed
        self.tokens = tokens
        self.interrupted = asyncio.Event()

    async def interrupt(self):
        self.interrupted.set()

    async def stream(self):
        (self.project / "body.tex").write_text("% PDF page 1\nNative edit", encoding="utf-8")
        yield event("item/completed", {"item": {"type": "fileChange", "changes": [{"path": "body.tex"}]}})
        if self.pause:
            await self.interrupted.wait()
        yield event("item/completed", {"item": {"type": "contextCompaction"}})
        yield event("thread/tokenUsage/updated", {"tokenUsage": {"total": {"totalTokens": self.tokens}}})
        status = "interrupted" if self.pause else "failed" if self.failed else "completed"
        yield event("turn/completed", {"turn": {"id": self.id, "status": status,
                    "error": {"message": "Model at capacity"} if self.failed else None}})


class NativeSDK:
    def __init__(self, project, pause=False, failed=False):
        self.project, self.pause, self.failed = project, pause, failed
        self.started, self.resumed, self.prompts, self.handles = [], [], [], []
        self.id = "persistent-thread"

    async def thread_start(self, **kwargs):
        self.started.append(kwargs)
        return self

    async def thread_resume(self, thread_id, **kwargs):
        assert thread_id == self.id
        self.resumed.append(kwargs)
        return self

    async def read(self, **kwargs):
        return SimpleNamespace(model_dump=lambda **_: {"thread": {"turns": []}})

    async def turn(self, prompt, **kwargs):
        assert "output_schema" not in kwargs
        assert "recent_actions" not in prompt and "file_contents" not in prompt
        self.prompts.append(prompt)
        handle = NativeTurn(self.project, self.pause, self.failed, tokens=(len(self.handles) + 1) * 7)
        self.handles.append(handle)
        return handle


def wire(app, monkeypatch, **kwargs):
    store, engine, run = ready_run(app)
    project = store.directory(run["id"]) / "tex"
    project.mkdir(parents=True)
    sdk = NativeSDK(project, **kwargs)
    async def codex():
        return sdk
    monkeypatch.setattr(engine.providers, "codex_agent", codex)
    return store, engine, run, project, sdk


@pytest.mark.asyncio
async def test_same_thread_native_edits_usage_and_compaction_events_survive_resume(app, monkeypatch):
    store, engine, run, project, sdk = wire(app, monkeypatch)
    atomic_json(project / "bibliography-review.json", [{"key": "bibliography:MM", "reason": "Unconfirmed"}])
    feedback = {"status": "FAILED", "code": "REFERENCE_ERROR", "log": "Citation A undefined"}
    await repair_project(engine.providers, run, project, feedback)
    await repair_project(engine.providers, run, project, feedback)
    assert len(sdk.started) == 1 and len(sdk.resumed) == 1
    assert all("reserved for USER confirmation" in p and "Do not investigate" in p for p in sdk.prompts)
    assert "Native edit" in (project / "body.tex").read_text()
    assert "base_instructions" not in sdk.started[0]  # Keep Codex's native agent instructions.
    permissions = sdk.started[0]["config"]["permissions"]["bookanalyst-compile"]
    assert permissions["filesystem"][str(project.resolve())] == "write"
    assert permissions["filesystem"][":tmpdir"] == "write"
    assert permissions["network"]["enabled"] is False
    calls = store.calls(run["id"])
    assert all(c["state"] == "COMPLETED" and c["metadata"]["agent"] == "codex" for c in calls)
    assert calls[-1]["metadata"]["usage"]["tokens"]["total"]["totalTokens"] == 7
    assert sum(c["metadata"]["usage"]["tokens"]["total"]["totalTokens"] for c in calls) == 14
    journal = store.root / "runs" / run["id"] / "requests" / calls[0]["id"] / "events.jsonl"
    assert "contextCompaction" in journal.read_text()
    session = json.loads((project.parent / "codex-compiler.json").read_text())
    assert session["thread_id"] == sdk.id


@pytest.mark.asyncio
async def test_pause_interrupts_native_turn_and_keeps_files_for_same_thread(app, monkeypatch):
    store, engine, run, project, sdk = wire(app, monkeypatch, pause=True)
    task = asyncio.create_task(repair_project(engine.providers, run, project, {}))
    while not sdk.handles:
        await asyncio.sleep(0)
    store.change(run["id"], lambda r: r.update(pause_requested=True))
    with pytest.raises(WorkflowError) as exc:
        await asyncio.wait_for(task, 3)
    assert exc.value.code == "PAUSED" and sdk.handles[0].interrupted.is_set()
    assert "Native edit" in (project / "body.tex").read_text()
    store.change(run["id"], lambda r: r.update(pause_requested=False))
    sdk.pause = False
    await repair_project(engine.providers, run, project, {})
    assert len(sdk.started) == 1 and len(sdk.resumed) == 1
    assert not engine._unknown_blocks_resume(run["id"])


@pytest.mark.asyncio
async def test_native_error_keeps_reason_and_never_restarts_a_new_thread(app, monkeypatch):
    store, engine, run, project, sdk = wire(app, monkeypatch, failed=True)
    with pytest.raises(WorkflowError, match="Model at capacity"):
        await repair_project(engine.providers, run, project, {})
    assert len(sdk.started) == 1
    assert store.calls(run["id"])[0]["metadata"]["error_message"] == "Model at capacity"
    assert "Native edit" in (project / "body.tex").read_text()


@pytest.mark.asyncio
async def test_host_cancellation_interrupts_native_turn(app, monkeypatch):
    store, engine, run, project, sdk = wire(app, monkeypatch, pause=True)
    task = asyncio.create_task(repair_project(engine.providers, run, project, {}))
    while not sdk.handles:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sdk.handles[0].interrupted.is_set()
    assert json.loads((project.parent / "codex-compiler.json").read_text())["status"] == "INTERRUPTED"
