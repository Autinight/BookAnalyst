from unittest.mock import AsyncMock

import pytest

from bookanalyst.models import RunCreate, StartOperation
from bookanalyst.store import WorkflowError


@pytest.mark.asyncio
async def test_explicit_retry_preserves_history_and_is_idempotent(app, monkeypatch):
    store, engine = app.state.store, app.state.engine
    book = store.get("book", "wang-2022-g-invariant-min-max")
    run = store.create_run(RunCreate(book_id=book["id"], end_page=3).model_dump(), book)
    rid = run["id"]
    call = store.reserve(rid, run["revision"], "llm", 1, {"model_id": "test"})
    store.finish_call(call, "RESULT_UNKNOWN")
    artifact = store.directory(rid) / "saved-result.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(engine.providers, "reconcile", AsyncMock())
    monkeypatch.setattr(engine, "launch", lambda rid: None)
    with pytest.raises(WorkflowError, match="重试未返回请求"):
        await engine.start(rid, StartOperation(revision=run["revision"], operation_id="ordinary-start"))
    with pytest.raises(WorkflowError):
        await engine.start(rid, StartOperation(revision=999, operation_id="stale-retry", retry_unknown=True))
    assert store.calls(rid)[0]["state"] == "RESULT_UNKNOWN"
    command = StartOperation(revision=run["revision"], operation_id="explicit-retry", retry_unknown=True)
    first = await engine.start(rid, command)
    history = store.calls(rid)
    assert history[0]["state"] == "ABANDONED"
    assert history[0]["metadata"]["upstream_outcome"] == "unknown"
    assert first["usage"]["llm"] == 1
    assert artifact.read_text(encoding="utf-8") == "keep"
    second = await engine.start(rid, command)
    assert second["revision"] == first["revision"]
    assert store.calls(rid) == history
