"""Bounded validator diagnostics shared by model parsing and automatic repair."""

import json
from itertools import islice

from .store import WorkflowError

MAX_ERRORS = 10


def preview(value, limit=300):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def pointer(parts):
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def validation_error(stage, issues, **context):
    errors = list(islice(issues, MAX_ERRORS + 1))
    diagnostics = {
        "stage": stage,
        "errors": errors[:MAX_ERRORS],
        "has_more_errors": len(errors) > MAX_ERRORS,
        **context,
    }
    first = errors[0]
    location = first.get("path") or "/"
    if "location" in first:
        loc = first["location"]
        location = f"line {loc['line']}, column {loc['column']}"
    error = WorkflowError(
        "SCHEMA_ERROR", f"{stage} ({location}): {first['message']}",
        review=True, diagnostics=diagnostics,
    )
    return error


def syntax_error(exc, source):
    return validation_error("json_parse", [{
        "code": "JSON_SYNTAX",
        "message": exc.msg,
        "location": {"line": exc.lineno, "column": exc.colno, "offset": exc.pos},
        "context_before": exc.doc[max(0, exc.pos - 100):exc.pos],
        "offending_text": exc.doc[exc.pos:exc.pos + 1],
        "context_after": exc.doc[exc.pos + 1:exc.pos + 101],
    }], source=source)


def schema_issues(errors):
    for exc in errors:
        yield {
            "code": "SCHEMA_RULE",
            "path": pointer(exc.absolute_path),
            "schema_path": pointer(exc.absolute_schema_path),
            "rule": exc.validator,
            "rule_value_preview": preview(exc.validator_value),
            "actual_preview": preview(exc.instance),
            "message": exc.message[:500],
        }


def model_validation_error(exc):
    return validation_error("model_schema", ({
        "code": item["type"],
        "path": pointer(item["loc"]),
        "message": item["msg"][:500],
        "actual_preview": preview(item.get("input")),
    } for item in exc.errors(include_url=False, include_context=False)))
