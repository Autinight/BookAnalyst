import pytest

from bookanalyst.llm import parse_json
from bookanalyst.store import WorkflowError


SCHEMA = {"type": "object", "properties": {"ok": {"const": True}}, "required": ["ok"]}


@pytest.mark.parametrize("text", ['{"ok":true}', '```json\n{"ok":true}\n```', '```\n{"ok":true}\n```'])
def test_json_response_accepts_single_wrapper(text):
    assert parse_json(text, SCHEMA) == {"ok": True}


@pytest.mark.parametrize("text", ['```json\n{"ok":false}\n```', 'Explanation\n```json\n{"ok":true}\n```', '```json\n{"ok":true}\n```\ntrailing'])
def test_json_response_still_enforces_schema_and_whole_response(text):
    with pytest.raises(WorkflowError):
        parse_json(text, SCHEMA)
