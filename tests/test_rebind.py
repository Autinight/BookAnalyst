"""Rebind the run model for later requests without discarding completed work."""

import pytest
from pydantic import BaseModel
from fastapi.testclient import TestClient
from bookanalyst.models import RunModelUpdate
from test_rebuild import make_run


def client_for(app):
    client = TestClient(app)
    client.headers["x-bookanalyst-token"] = client.get("/api/bootstrap").json()["token"]
    return client


class Tiny(BaseModel):
    ok: bool


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_rebind_changes_later_requests_and_keeps_completed_work(app, monkeypatch, effort):
    store = app.state.store
    engine = app.state.engine
    run = make_run(app)
    run["config"]["model"]["model_id"] = "old-model"
    run["config"]["resolved"] = True
    store.put("run", run["id"], run)
    store.task(run["id"], "batch-0001", "convert", "PASSED", [1, 2, 3])
    seen = []

    async def resolve(binding, require_image=False):
        assert require_image
        return dict(binding), object()

    async def generate(run, role, purpose, prompt, schema, images=(), semaphore=None):
        seen.append((run["config"]["model"]["model_id"], run["config"]["model"]["reasoning_effort"]))
        return {"ok": True}

    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)
    summary = await engine.rebind(
        run["id"],
        RunModelUpdate(
            revision=run["revision"],
            operation_id="rebind-later-1",
            model={
                "connection_id": "openai_subscription",
                "model_id": "new-model",
                "reasoning_effort": "high",
            },
            structure_effort=effort,
            llm_concurrency=5,
        ),
    )
    assert summary["model"]["model_id"] == "new-model"
    assert summary["model"]["reasoning_effort"] == "high"
    assert summary["structure_effort"] == effort
    assert summary["concurrency"] == 5
    assert store.tasks(run["id"], "convert")[0]["state"] == "PASSED"
    await engine.ask(store.get("run", run["id"]), "probe", "setup", {"n": 1}, Tiny, [])
    assert seen == [("new-model", effort)]


def test_status_exposes_live_model_and_completed_run_rejects_rebind(app):
    store = app.state.store
    run = make_run(app)
    run["config"]["model"]["model_id"] = "frozen-model"
    run["state"] = "COMPLETED"
    store.put("run", run["id"], run)
    with client_for(app) as client:
        status = client.get(f"/api/runs/{run['id']}/status").json()
        assert status["model"]["model_id"] == "frozen-model"
        assert status["structure_effort"] == "xhigh"
        response = client.post(
            f"/api/runs/{run['id']}/model",
            json={
                "revision": status["revision"],
                "operation_id": "rebind-complete-1",
                "model": {
                    "connection_id": "openai_subscription",
                    "model_id": "other",
                    "reasoning_effort": "medium",
                },
                "structure_effort": "high",
                "llm_concurrency": 2,
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "NOT_RUNNING"


def test_status_payload_stays_small_after_model_fields(app):
    run = make_run(app)
    with TestClient(app) as client:
        status = client.get(f"/api/runs/{run['id']}/status")
        assert len(status.content) < 2000
        body = status.json()
        assert body["model"]["connection_id"] == "openai_subscription"
        assert "history" not in body
