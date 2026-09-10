"""Transient model request retries stay inside generate, then pause."""

import asyncio
import copy

import httpx
import pytest

from bookanalyst.config import DEFAULT_SETTINGS
from bookanalyst.llm import REQUEST_ATTEMPTS, Providers
from bookanalyst.models import StartOperation
from bookanalyst.store import WorkflowError
from test_rebuild import make_run


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}


def ready_run(app):
    store = app.state.store
    run = make_run(app)
    run["config"]["model"]["model_id"] = "test-model"
    run["config"]["resolved"] = True
    store.put("run", run["id"], run)
    return store, app.state.engine, run


def custom_conn():
    conn = copy.deepcopy(DEFAULT_SETTINGS["connections"]["custom_api"])
    conn.update(
        enabled=True,
        base_url="http://model.test/v1",
        auth_mode="none",
        timeout_seconds=5,
    )
    return conn


@pytest.mark.asyncio
async def test_generate_retries_timeout_then_succeeds(app, monkeypatch):
    store, engine, run = ready_run(app)
    providers = engine.providers
    attempts = []
    slept = []

    async def subscription(*args):
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise TimeoutError()
        return '{"value":1}', {"tokens": {"total": {"totalTokens": 4}}}

    async def sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(providers, "_subscription", subscription)
    monkeypatch.setattr("bookanalyst.llm.asyncio.sleep", sleep)
    assert await providers.generate(run, "model", "setup", "retry-ok", SCHEMA) == {
        "value": 1
    }
    calls = store.calls(run["id"])
    assert [c["state"] for c in calls] == [
        "RESULT_UNKNOWN",
        "RESULT_UNKNOWN",
        "COMPLETED",
    ]
    assert [c["metadata"]["attempt"] for c in calls] == [1, 2, 3]
    assert slept == [2.0, 4.0]
    assert not any(c["metadata"].get("retry_exhausted") for c in calls)


@pytest.mark.asyncio
async def test_generate_retries_five_times_then_resume_continues(app, monkeypatch):
    store, engine, run = ready_run(app)
    providers = engine.providers
    attempts = []
    slept = []

    async def subscription(*args):
        attempts.append(1)
        raise TimeoutError()

    async def sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(providers, "_subscription", subscription)
    monkeypatch.setattr("bookanalyst.llm.asyncio.sleep", sleep)
    with pytest.raises(WorkflowError) as failed:
        await providers.generate(run, "model", "setup", "retry-exhausted", SCHEMA)
    assert failed.value.code == "RESULT_UNKNOWN"
    assert failed.value.retryable
    assert len(attempts) == REQUEST_ATTEMPTS
    assert slept == [2.0, 4.0, 8.0, 16.0]
    calls = store.calls(run["id"])
    assert [c["state"] for c in calls] == ["RESULT_UNKNOWN"] * REQUEST_ATTEMPTS
    assert all(c["metadata"].get("retry_exhausted") for c in calls)

    monkeypatch.setattr(engine.providers, "reconcile", lambda current: asyncio.sleep(0))
    monkeypatch.setattr(engine, "launch", lambda rid: None)
    store.change(run["id"], lambda r: r.update(state="PAUSED"))
    resumed = await engine.start(
        run["id"],
        StartOperation(
            revision=store.get("run", run["id"])["revision"],
            operation_id="resume-after-retries",
        ),
    )
    assert resumed["state"] == "RUNNING"


@pytest.mark.asyncio
async def test_retryable_timeout_pauses_run(app, monkeypatch):
    store, engine, run = ready_run(app)
    store.change(run["id"], lambda r: r.update(state="RUNNING"))

    async def generate(*args, **kwargs):
        raise WorkflowError(
            "RESULT_UNKNOWN",
            "模型请求超时",
            review=True,
            retryable=True,
        )

    monkeypatch.setattr(engine.providers, "generate", generate)
    await engine.execute(run["id"])
    current = store.get("run", run["id"])
    assert current["state"] == "PAUSED"
    assert current["error"]["code"] == "RESULT_UNKNOWN"
    assert current["stages"]["setup"] == "PAUSED"


@pytest.mark.asyncio
async def test_auth_failure_does_not_retry_and_needs_review(app, monkeypatch):
    store, engine, run = ready_run(app)
    providers = engine.providers
    attempts = []

    async def custom(*args, **kwargs):
        attempts.append(1)
        raise WorkflowError("AUTH_REQUIRED", "模型服务鉴权失败")

    monkeypatch.setattr(providers, "connection", lambda cid: custom_conn())
    monkeypatch.setattr(providers, "_custom", custom)
    run["config"]["model"]["connection_id"] = "custom_api"
    store.put("run", run["id"], run)
    with pytest.raises(WorkflowError) as failed:
        await providers.generate(
            store.get("run", run["id"]), "model", "setup", "auth", SCHEMA
        )
    assert failed.value.code == "AUTH_REQUIRED"
    assert not failed.value.retryable
    assert attempts == [1]
    assert [c["state"] for c in store.calls(run["id"])] == ["FAILED"]

    store.change(run["id"], lambda r: r.update(state="RUNNING"))

    async def generate(*args, **kwargs):
        raise WorkflowError("AUTH_REQUIRED", "模型服务鉴权失败")

    monkeypatch.setattr(engine.providers, "generate", generate)
    await engine.execute(run["id"])
    current = store.get("run", run["id"])
    assert current["state"] == "NEEDS_REVIEW"
    assert current["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_custom_http_5xx_retries_with_backoff(app, tmp_path, monkeypatch):
    store, _, run = ready_run(app)
    run["config"]["model"].update(connection_id="custom_api", model_id="custom-vision")
    store.put("run", run["id"], run)
    hits = []
    slept = []

    def handler(request):
        hits.append(request.url.path)
        if len(hits) < 2:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"value":2}'}}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    async def sleep(delay):
        slept.append(delay)

    providers = Providers(store, tmp_path, httpx.MockTransport(handler))
    monkeypatch.setattr(providers, "connection", lambda cid: custom_conn())
    monkeypatch.setattr("bookanalyst.llm.asyncio.sleep", sleep)
    assert await providers.generate(run, "model", "setup", "http-retry", SCHEMA) == {
        "value": 2
    }
    assert len(hits) == 2
    assert slept == [2.0]
