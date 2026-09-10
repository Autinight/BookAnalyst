"""Validate the subscription wire contract, including nested required fields."""

import json
from types import SimpleNamespace

import httpx
import pytest

from bookanalyst.documents import (
    Conversion, Setup, Seam, SeamEnvironmentAction, Headings,
    References, ReferenceAction,
)
from bookanalyst.store import WorkflowError
from test_request_retry import ready_run


def assert_strict_schema(node):
    if isinstance(node, dict):
        assert "default" not in node
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))
        for value in node.values():
            assert_strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            assert_strict_schema(value)


@pytest.mark.parametrize("contract", [
    Conversion, Setup, Seam, SeamEnvironmentAction, Headings,
    References, ReferenceAction,
])
def test_complete_output_schema_is_strict(contract):
    assert_strict_schema(contract.model_json_schema())


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", [None,
    '400 invalid_json_schema: Missing begin_line in required',
    "Invalid schema for response_format 'codex_output_schema': Missing 'begin_line'.",
])
async def test_subscription_wire_schema_and_rejection(app, monkeypatch, rejection):
    store, engine, run = ready_run(app)
    providers = engine.providers
    starts = []
    value = {
        "pages": [{"page": 1, "tex": "Text."}], "headings": [], "assets": [],
        "head": "closed", "tail": "closed", "unclosed_environments": [],
    }

    async def collect():
        if rejection:
            raise RuntimeError(rejection)
        return SimpleNamespace(
            final_response=json.dumps(value), usage=None, items=[], duration_ms=1,
        )

    async def turn(inputs, *, output_schema, **kwargs):
        assert_strict_schema(output_schema)
        assert "unclosed_environments" in output_schema["required"]
        return SimpleNamespace(id="turn-1", run=collect)

    async def start(**kwargs):
        starts.append(kwargs)
        return SimpleNamespace(id="thread-1", turn=turn)

    async def codex():
        return SimpleNamespace(thread_start=start)

    monkeypatch.setattr(providers, "codex", codex)
    if rejection:
        with pytest.raises(WorkflowError) as error:
            await providers.generate(run, "model", "convert", "page", Conversion.model_json_schema())
        assert error.value.code == "INVALID_OUTPUT_SCHEMA"
        assert error.value.message == rejection
        assert not error.value.retryable
        calls = store.calls(run["id"])
        assert len(calls) == 1
        assert calls[0]["metadata"]["error_message"] == rejection
        receipt = store.root / "runs" / run["id"] / "requests" / calls[0]["id"] / "error.json"
        assert json.loads(receipt.read_text(encoding="utf-8"))["message"] == rejection
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            response = await client.get(f"/api/runs/{run['id']}/requests")
        assert response.status_code == 200
        assert response.json()["rows"][0]["error_message"] == rejection
    else:
        assert await providers.generate(run, "model", "convert", "page", Conversion.model_json_schema()) == value
    assert len(starts) == 1
