"""High-frequency formatting rejections are resolved without another model call."""

import copy
import json
import re

import pytest
from PIL import Image

from bookanalyst.documents import (
    Conversion, SeamEnvironmentAction, namespace, validate_conversion,
)
from bookanalyst.environments import environment_report
from bookanalyst.llm import parse_json
from bookanalyst.store import WorkflowError
from test_rebuild import make_run, result


def heading(identifier, page=1):
    return {"id": identifier, "page": page, "level": "section", "number": "", "title": "Title"}


def test_long_environment_note_survives_wire_schema_and_model_validation():
    note = "Explanation of the actual proof ending. " * 30
    value = {"edits": [], "read_pages": [2], "resolved": False,
             "closing_page": None, "note": note}
    parsed = parse_json(json.dumps(value), SeamEnvironmentAction.model_json_schema())
    assert SeamEnvironmentAction.model_validate(parsed).note == note
    with pytest.raises(WorkflowError):
        parse_json(json.dumps(value | {"note": 42}), SeamEnvironmentAction.model_json_schema())


def test_local_ids_are_renamed_without_collisions_or_cascading_replacements():
    value = result([1])
    value["headings"] = [heading(x) for x in ("ch:4", "headings-1", "batch-0001-headings-2")]
    value["assets"] = [{"id": "fig:cover", "page": 1, "bbox": [0, 0, 1, 1]}]
    value["pages"][0]["tex"] = (
        r"\BAHeading{ch:4}\label{section:4} "
        r"\BAHeading{headings-1} "
        r"\BAHeading{batch-0001-headings-2} "
        r"\BAFigure{fig:cover}\label{figure:4}\ref{section:4}\eqref{equation:4.1}"
    )
    before = copy.deepcopy(value)
    validate_conversion(value, [1])
    fixed = namespace(value, "batch-0001")
    assert value == before
    assert [h["id"] for h in fixed["headings"]] == [
        "batch-0001-headings-2", "batch-0001-headings-1", "batch-0001-batch-0001-headings-2",
    ]
    assert fixed["assets"][0]["id"] == "batch-0001-assets-1"
    validate_conversion(fixed, [1])
    for marker in (r"\label{section:4}", r"\label{figure:4}", r"\ref{section:4}", r"\eqref{equation:4.1}"):
        assert marker in fixed["pages"][0]["tex"]
    strip_markers = lambda text: re.sub(r"\\BA(?:Heading|Figure)\{[^{}]*\}", "", text)
    assert strip_markers(fixed["pages"][0]["tex"]) == strip_markers(before["pages"][0]["tex"])


@pytest.mark.parametrize("identifier", ["", "two words", r"bad\name", "bad{id}", "bad%id"])
def test_ambiguous_local_ids_still_fail(identifier):
    value = result([1])
    value["headings"] = [heading(identifier)]
    value["pages"][0]["tex"] = "\\BAHeading{" + identifier + "}"
    with pytest.raises(WorkflowError) as error:
        validate_conversion(value, [1])
    assert error.value.code == "INVALID_ID"


@pytest.mark.parametrize("case,code", [
    ("duplicate", "INVALID_ID"), ("missing", "MISSING_ANCHOR"), ("wrong_page", "ANCHOR_PAGE"),
])
def test_local_id_normalization_does_not_hide_structural_errors(case, code):
    value = result([1, 2])
    value["headings"] = [heading("ch:4")]
    value["pages"][0]["tex"] = r"\BAHeading{ch:4}"
    if case == "duplicate":
        value["assets"] = [{"id": "ch:4", "page": 1, "bbox": [0, 0, 1, 1]}]
    elif case == "missing":
        value["pages"][0]["tex"] = r"\BAHeading{ch:5}"
    else:
        value["headings"][0]["page"] = 2
    with pytest.raises(WorkflowError) as error:
        validate_conversion(value, [1, 2])
    assert error.value.code == code


@pytest.mark.asyncio
async def test_convert_normalizes_ids_and_derives_report_in_one_request(app, monkeypatch, tmp_path):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    task = store.tasks(run["id"], "convert")[0]
    value = result(task["pages"])
    value["headings"] = [heading("ch:4", task["pages"][0])]
    value["assets"] = [{"id": "fig:cover", "page": task["pages"][0], "bbox": [0, 0, 1, 1]}]
    value["pages"][0]["tex"] = r"\BAHeading{ch:4}\label{section:4}\BAFigure{fig:cover}\ref{section:4}"
    value["pages"][-1]["tex"] = r"\begin{proof}The proof continues."
    value["unclosed_environments"] = [{"name": "lemma", "begin_page": task["pages"][0]}]
    original = copy.deepcopy(value)
    page = tmp_path / "page.png"
    Image.new("RGB", (20, 20), "white").save(page)
    calls = []

    async def image(*args):
        return page

    async def generate(current, role, purpose, prompt, schema, images=()):
        calls.append(current["request_is_repair"])
        # Use the real wire schema as well as the engine's content validator.
        return parse_json(json.dumps(value), schema)

    monkeypatch.setattr(engine, "image", image)
    monkeypatch.setattr(engine.providers, "generate", generate)
    converted = await engine.convert(run, task, {"rules": ""})
    assert calls == [False] and value == original
    assert converted["unclosed_environments"] == environment_report(original["pages"])
    assert converted["unclosed_environments"][0]["name"] == "proof"
    assert converted["pages"][-1]["tex"] == original["pages"][-1]["tex"]
    assert converted["assets"][0]["id"] == task["id"] + "-assets-1"
    assert (store.directory(run["id"]) / "tex/assets" / (converted["assets"][0]["id"] + ".png")).exists()
    with Image.open(store.directory(run["id"]) / "tex/assets" / (converted["assets"][0]["id"] + ".png")) as crop:
        assert crop.size == (20, 20)
        assert crop.info["dpi"] == pytest.approx((150, 150), abs=0.02)
    assert r"\label{section:4}" in converted["pages"][0]["tex"]
    assert store.tasks(run["id"], "convert")[0]["state"] == "PASSED"
    assert not (store.directory(run["id"]) / "repair-attempts" / (task["id"] + ".json")).exists()
