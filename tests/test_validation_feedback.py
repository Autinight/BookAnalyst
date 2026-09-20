import json

import pytest
from pydantic import BaseModel

from bookanalyst.llm import parse_json
from bookanalyst.store import WorkflowError
from test_request_retry import ready_run


@pytest.mark.parametrize("text", [
    '{"items": [1,]}',
    '{"name": "中文",\n"count" 2}',
    '{"value": "\\q"}',
    '{"items": [1',
])
def test_syntax_feedback_matches_parser_without_rewriting_candidate(text):
    with pytest.raises(json.JSONDecodeError) as original:
        json.loads(text)
    with pytest.raises(WorkflowError) as caught:
        parse_json(text, {"type": "object"})
    error = caught.value
    issue = error.diagnostics["errors"][0]
    assert error.candidate == text
    assert issue["message"] == original.value.msg
    assert issue["location"] == {
        "line": original.value.lineno, "column": original.value.colno,
        "offset": original.value.pos,
    }
    assert issue["context_before"] + issue["offending_text"] + issue["context_after"] == text
    assert original.value.msg in error.message


def test_schema_feedback_reports_multiple_rules_and_escaped_field_paths():
    schema = {
        "type": "object", "required": ["title"],
        "properties": {"a/b~c": {"type": "array", "items": {"type": "integer"}}},
        "additionalProperties": False,
    }
    with pytest.raises(WorkflowError) as caught:
        parse_json('{"a/b~c": ["wrong"], "extra": true}', schema)
    details = caught.value.diagnostics
    assert details["stage"] == "json_schema"
    issues = {e["rule"]: e for e in details["errors"]}
    assert set(issues) == {"type", "required", "additionalProperties"}
    assert issues["type"]["path"] == "/a~1b~0c/0"
    assert issues["type"]["actual_preview"] == '"wrong"'
    assert issues["type"]["rule_value_preview"] == '"integer"'
    assert not details["has_more_errors"]


@pytest.mark.parametrize("body,stage", [('{"ok": false}', "json_schema"), ('{"ok":}', "json_parse")])
def test_fenced_response_reports_inner_error_instead_of_wrapper(body, stage):
    text = f"Explanation\n```json\n{body}\n```"
    with pytest.raises(WorkflowError) as caught:
        parse_json(text, {"type": "object", "properties": {"ok": {"const": True}}})
    assert caught.value.diagnostics["stage"] == stage
    assert caught.value.diagnostics["source"] == "single_json_code_block"
    assert caught.value.candidate == text


def test_schema_feedback_bounds_large_values_and_reports_omitted_errors():
    with pytest.raises(WorkflowError) as caught:
        parse_json(json.dumps(["x" * 10000] * 15), {"type": "array", "items": {"type": "integer"}})
    details = caught.value.diagnostics
    assert len(details["errors"]) == 10
    assert details["has_more_errors"]
    assert len(json.dumps(details)) < 12000


class Count(BaseModel):
    count: int


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["json_parse", "json_schema", "model_schema", "content"])
async def test_feedback_reaches_next_request_and_checkpoint(app, monkeypatch, failure_stage):
    store, engine, run = ready_run(app)
    prompts = []

    async def generate(current, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            if failure_stage == "json_parse":
                return parse_json('{"count":}', schema)
            if failure_stage == "json_schema":
                return parse_json('{"count": "wrong"}', schema)
            return {"count": "wrong" if failure_stage == "model_schema" else -1}
        feedback = payload["repair_feedback"]
        assert feedback["diagnostics"]["stage"] == failure_stage
        if failure_stage in ("json_schema", "model_schema"):
            assert feedback["diagnostics"]["errors"][0]["path"] == "/count"
        checkpoint = json.loads((store.directory(run["id"]) / "repairs/count.json").read_text(encoding="utf-8"))
        assert checkpoint["feedback"]["diagnostics"] == feedback["diagnostics"]
        return {"count": 2}

    def validate(value):
        if value["count"] < 0:
            raise WorkflowError("NEGATIVE_COUNT", "count must be nonnegative")

    monkeypatch.setattr(engine.providers, "generate", generate)
    assert await engine.checked_ask(run, "count", "convert", {}, Count, [], validate) == {"count": 2}
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_provider_saves_specific_rejection_in_request_history(app, monkeypatch):
    store, engine, run = ready_run(app)

    async def subscription(*args):
        return '{"count":}', {}

    monkeypatch.setattr(engine.providers, "_subscription", subscription)
    with pytest.raises(WorkflowError) as caught:
        await engine.providers.generate(run, "model", "convert", "test", Count.model_json_schema())
    calls = store.calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["metadata"]["error_message"] == caught.value.message
    assert "line 1, column 10" in caught.value.message
