"""Running stages resize task slots while preserving already dispatched requests."""
import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from bookanalyst.model_config import MODEL_STAGES, request_run
from bookanalyst.models import RunModelUpdate
from bookanalyst.store import atomic_json
from test_rebuild import make_run, result
from test_reference_concurrency import source, repair
from test_seam_concurrency import batches, empty_patch


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["convert", "seams", "references"])
@pytest.mark.parametrize("initial,target", [(1, 3), (3, 1)])
async def test_resize_running_stage_without_interrupting_active_requests(app, monkeypatch, stage, initial, target):
    store, engine = app.state.store, app.state.engine
    run = make_run(app, llm_concurrency=initial, pages_per_task=1)
    old = {s: {"connection_id": "openai_subscription", "model_id": "old-model", "reasoning_effort": "medium"} for s in MODEL_STAGES}
    run["config"]["stage_models"] = copy.deepcopy(old)
    run["state"] = "RUNNING"
    store.put("run", run["id"], run)
    settings = store.get("settings", "main")
    settings.update(stage_models={s: dict(b, model_id="new-model") for s, b in old.items()}, llm_concurrency=7)
    store.put("settings", "main", settings)
    queue = asyncio.Queue()
    gates, models = {}, {}
    finish = False

    async def resolve(binding, require_image=False):
        return dict(binding), {}
    monkeypatch.setattr(engine.providers, "resolve", resolve)

    async def enter(key, model):
        models[key] = model
        gate = gates[key] = asyncio.Event()
        if finish:
            gate.set()
        queue.put_nowait(key)
        await gate.wait()

    if stage == "convert":
        async def convert(snapshot, task, setup):
            bound = await request_run(engine.providers, run["id"], "convert")
            await enter(task["id"], bound["config"]["model"]["model_id"])
            value = result(task["pages"])
            atomic_json(store.directory(run["id"]) / "batches" / f"{task['id']}.json", value)
            store.task(run["id"], task["id"], "convert", "PASSED", task["pages"])
            return value
        monkeypatch.setattr(engine, "convert", convert)
        work = engine.convert_all(run, {})
    elif stage == "seams":
        async def ask(snapshot, key, purpose, *args):
            bound = await request_run(engine.providers, run["id"], purpose)
            await enter(key, bound["config"]["model"]["model_id"])
            return empty_patch()
        monkeypatch.setattr(engine, "checked_ask", ask)
        work = engine.seams(run, batches(7))
    else:
        async def generate(snapshot, role, purpose, prompt, schema, images=()):
            payload = json.loads(prompt)
            await enter(payload["keys_to_check"][0], snapshot["config"]["model"]["model_id"])
            return repair(payload)
        monkeypatch.setattr(engine.providers, "generate", generate)
        work = engine.references(run, source(6), [], {})

    task = asyncio.create_task(work)
    async def next_request():
        return await asyncio.wait_for(queue.get(), 3)
    try:
        active = [await next_request() for _ in range(initial)]
        assert all(models[key] == "old-model" for key in active)
        # Saving global settings alone has no effect on this active task.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), .05)
        command = RunModelUpdate(revision=run["revision"], operation_id="resize-current-stage", llm_concurrency=target)
        await engine.rebind(run["id"], command)
        if target > initial:
            added = [await next_request() for _ in range(target - initial)]
            assert all(models[key] == "new-model" for key in added)
            assert all(not gates[key].is_set() for key in active)
        else:
            for key in active[:-1]:
                gates[key].set()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(queue.get(), .1)
            gates[active[-1]].set()
            added = [await next_request()]
            assert models[added[0]] == "new-model"
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(queue.get(), .05)
        assert store.get("run", run["id"])["config"]["llm_concurrency"] == target
        assert store.get("settings", "main")["llm_concurrency"] == 7
        await engine.rebind(run["id"], command)
        assert not store.get("run", run["id"])["model_refresh_pending"]
    finally:
        finish = True
        for gate in gates.values():
            gate.set()
        await asyncio.wait_for(task, 5)
    assert len(models) == 6
    assert not engine.task_limits


def test_run_update_stores_override_without_changing_global_settings(app):
    run = make_run(app)
    store = app.state.store
    before = store.get("settings", "main")
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert bootstrap["live_concurrency_updates"]
        client.headers["x-bookanalyst-token"] = bootstrap["token"]
        url = f"/api/runs/{run['id']}/model"
        body = {"revision": run["revision"], "operation_id": "change-task-concurrency", "llm_concurrency": 6}
        response = client.post(url, json=body)
        assert response.status_code == 200
        assert response.json()["pending_llm_concurrency"] == 6
        assert response.json()["concurrency"] == 2
        assert client.get(f"/api/runs/{run['id']}/status").json()["pending_llm_concurrency"] == 6
        assert store.get("settings", "main") == before
        for invalid in (0, -1, 1.5):
            assert client.post(url, json=body | {"llm_concurrency": invalid}).status_code == 422
