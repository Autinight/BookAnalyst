"""Eight automatic repairs per round; an explicit continuation opens another round."""

from unittest.mock import AsyncMock, Mock

import pytest
from bookanalyst.engine import REPAIR_ATTEMPTS_PER_ROUND
from bookanalyst.numbering import Numbering
from bookanalyst.models import Operation
from bookanalyst.store import WorkflowError, atomic_json
from test_rebuild import make_run, result


@pytest.mark.asyncio
async def test_failed_conversion_repairs_stop_and_revision_alone_does_not_reset(
    app, monkeypatch
):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    task = store.tasks(run["id"], "convert")[0]
    calls = []

    async def generate(*args):
        calls.append(1)
        return result([1])

    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError) as error:
        await engine.convert(run, task, {"rules": ""})
    assert error.value.code == "REPAIR_LIMIT"
    assert len(calls) == REPAIR_ATTEMPTS_PER_ROUND + 1  # One original conversion plus the repairs.
    store.change(run["id"], lambda r: r.update(revision=r["revision"] + 1))
    with pytest.raises(WorkflowError) as error:
        await engine.convert(store.get("run", run["id"]), task, {"rules": ""})
    assert error.value.code == "REPAIR_LIMIT" and len(calls) == REPAIR_ATTEMPTS_PER_ROUND + 1
    assert (
        next(t for t in store.tasks(run["id"]) if t["id"] == task["id"])["state"]
        == "NEEDS_REVIEW"
    )


@pytest.mark.asyncio
async def test_last_counter_request_can_succeed_but_one_more_is_not_sent(
    app, monkeypatch
):
    from bookanalyst.numbering import Numbering

    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(*args):
        calls.append(1)
        return {"edits": [], "read_files": [], "view_pages": []}

    monkeypatch.setattr(engine.providers, "generate", generate)
    for i in range(REPAIR_ATTEMPTS_PER_ROUND):
        await engine.ask(
            run, "counter-rules", "counter_repair", {"round": i}, Numbering, []
        )
    with pytest.raises(WorkflowError) as error:
        await engine.ask(
            run, "counter-rules", "counter_repair", {"round": REPAIR_ATTEMPTS_PER_ROUND}, Numbering, []
        )
    assert error.value.code == "REPAIR_LIMIT" and len(calls) == REPAIR_ATTEMPTS_PER_ROUND
    # Re-reading an existing receipt is allowed even after the request limit.
    await engine.ask(
        run, "counter-rules", "counter_repair", {"round": REPAIR_ATTEMPTS_PER_ROUND - 1}, Numbering, []
    )
    assert len(calls) == REPAIR_ATTEMPTS_PER_ROUND


def test_existing_repair_history_already_above_limit_is_counted(app):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    for _ in range(REPAIR_ATTEMPTS_PER_ROUND + 4):
        call = store.reserve(run["id"], 1, "llm", 1, {"purpose": "counter_repair"})
        store.finish_call(call, "COMPLETED")
    with pytest.raises(WorkflowError) as error:
        engine.consume_repair_attempt(run, "counter-rules", "counter_repair")
    assert error.value.code == "REPAIR_LIMIT"


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_calls", [REPAIR_ATTEMPTS_PER_ROUND, REPAIR_ATTEMPTS_PER_ROUND * 2])
async def test_manual_continue_gets_a_full_round_and_keeps_history(
    app, monkeypatch, previous_calls
):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    rid = run["id"]
    base = store.directory(rid)
    for _ in range(previous_calls):
        call = store.reserve(
            rid, run["revision"], "llm", 1, {"purpose": "counter_repair"}
        )
        store.finish_call(call, "COMPLETED", usage={"total_tokens": 123})
    atomic_json(
        base / "repair-attempts/counter-rules.json", {"attempts": previous_calls}
    )
    artifact = base / "batches/batch-0001.json"
    atomic_json(artifact, result([1, 2, 3]))
    original_artifact = artifact.read_bytes()
    original_history = store.calls(rid)
    store.change(
        rid, lambda r: r.update(state="NEEDS_REVIEW", error={"code": "REPAIR_LIMIT"})
    )
    launch = Mock()
    monkeypatch.setattr(engine, "launch", launch)
    monkeypatch.setattr(engine.providers, "reconcile", AsyncMock())

    async def generate(current, _binding, purpose, *_args):
        call = store.reserve(rid, current["revision"], "llm", 1, {"purpose": purpose})
        store.finish_call(call, "COMPLETED")
        return {"edits": [], "read_files": [], "view_pages": []}

    monkeypatch.setattr(engine.providers, "generate", generate)
    for round_index in range(2):
        before = store.get("run", rid)
        command = Operation(
            revision=before["revision"], operation_id=f"continue-{round_index}"
        )
        summary = await engine.start(rid, command)
        current = store.get("run", rid)
        assert summary["state"] == "RUNNING" and summary["error"] is None
        assert current["repair_epoch"] == before["revision"] + 1
        assert current["usage"] == before["usage"]
        assert artifact.read_bytes() == original_artifact
        assert store.calls(rid)[:previous_calls] == original_history
        for i in range(REPAIR_ATTEMPTS_PER_ROUND):
            await engine.ask(
                current,
                "counter-rules",
                "counter_repair",
                {"round": round_index, "turn": i},
                Numbering,
                [],
            )
        with pytest.raises(WorkflowError) as error:
            await engine.ask(
                current,
                "counter-rules",
                "counter_repair",
                {"round": round_index, "turn": REPAIR_ATTEMPTS_PER_ROUND},
                Numbering,
                [],
            )
        assert error.value.code == "REPAIR_LIMIT"
        assert "点击继续" in error.value.message
        assert len(store.calls(rid)) == previous_calls + REPAIR_ATTEMPTS_PER_ROUND * (round_index + 1)
        assert store.get("run", rid)["usage"]["llm"] == len(store.calls(rid))

        # Restart recovery preserves the current round's exhausted allowance.
        store.recover()
        assert store.get("run", rid)["state"] == "PAUSED"
        # A delayed duplicate of the same click must not start another round.
        launches = launch.call_count
        replay = await engine.start(rid, command)
        assert replay["state"] == "PAUSED"
        assert launch.call_count == launches
        assert store.get("run", rid)["repair_epoch"] == current["repair_epoch"]
        with pytest.raises(WorkflowError) as error:
            engine.consume_repair_attempt(
                store.get("run", rid), "counter-rules", "counter_repair"
            )
        assert error.value.code == "REPAIR_LIMIT"
