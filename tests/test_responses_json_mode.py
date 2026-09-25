"""Responses JSON mode stays enabled on plain, repair, and streamed requests."""

import copy
import json

import httpx
import pytest

from bookanalyst.config import DEFAULT_SETTINGS
from bookanalyst.llm import parse_json
from bookanalyst.store import WorkflowError


SCHEMA = {
    "type": "object",
    "properties": {"tex": {"type": "string"}},
    "required": ["tex"],
    "additionalProperties": False,
}


def connection():
    conn = copy.deepcopy(DEFAULT_SETTINGS["connections"]["custom_api"])
    conn.update(base_url="http://model.test/v1", protocol="responses", auth_mode="none")
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("repair", [False, True])
async def test_responses_sends_json_mode_and_preserves_latex(app, tmp_path, stream, repair):
    providers = app.state.engine.providers
    conn = connection()
    conn["_stream"] = stream
    expected = {"tex": r"\(\dot\rho^{\,2}\)"}
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/v1/responses"
        assert body["text"] == {"format": {"type": "json_object"}}
        assert "response_format" not in body
        assert body["stream"] is stream
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "high"}
        assert "JSON" in body["instructions"]
        content = body["input"][0]["content"]
        assert json.loads(content[0]["text"]) == prompt
        assert content[1] == {
            "type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U=",
        }
        result = {
            "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(expected)},
            ]}],
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
        if stream:
            assert request.headers["Accept"] == "text/event-stream"
            event = {"type": "response.completed", "response": result}
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  text="event: response.completed\ndata: " + json.dumps(event) + "\n\n")
        return httpx.Response(200, json=result)

    providers.transport = httpx.MockTransport(handle)
    prompt = {"instruction": "Convert the page."}
    if repair:
        prompt["repair_feedback"] = {"error": "Invalid \\escape", "attempt": 1}
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    try:
        text, usage = await providers._custom(
            conn, {"model_id": "vision", "reasoning_effort": "high"},
            json.dumps(prompt), SCHEMA, [image],
        )
        assert parse_json(text, SCHEMA) == expected
        assert usage == {"input_tokens": 3, "output_tokens": 5}
        assert len(seen) == 1
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_responses_does_not_silently_drop_rejected_json_mode(app):
    providers = app.state.engine.providers
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "Unsupported text.format"}})

    providers.transport = httpx.MockTransport(handle)
    try:
        with pytest.raises(WorkflowError) as caught:
            await providers._custom(connection(), {"model_id": "vision"}, "JSON please", SCHEMA, [])
        assert caught.value.code == "REQUEST_FAILED"
        assert not caught.value.retryable
        assert len(seen) == 1
        assert seen[0]["text"] == {"format": {"type": "json_object"}}
    finally:
        await providers.close()


def test_latex_invalid_escape_is_still_rejected_without_silent_rewriting():
    text = r'{"tex":"\\dot\\rho^{\,2}"}'
    with pytest.raises(WorkflowError) as caught:
        parse_json(text, SCHEMA)
    assert caught.value.diagnostics["stage"] == "json_parse"
    assert caught.value.candidate == text


def test_json_mode_does_not_replace_local_schema_validation():
    with pytest.raises(WorkflowError) as caught:
        parse_json('{"tex": 42}', SCHEMA)
    assert caught.value.diagnostics["stage"] == "json_schema"
