"""Independent heading/reference checkpoints and finalized chapter scope."""

import copy
import json

import pytest
from pydantic import ValidationError

from bookanalyst.documents import HeadingLevel, Headings, References
from bookanalyst.models import STAGES
from bookanalyst.store import WorkflowError, atomic_json
from test_numbering import batch, heading
from test_rebuild import make_run


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_reference_resume_reuses_finalized_headings(app, monkeypatch, legacy):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    run["config"]["resolved"] = True
    run.update(state="RUNNING", stage="headings")
    for stage in STAGES[:4]:
        run["stages"][stage] = "PASSED"
    if legacy:
        run["stages"].pop("references")
        run["stages"]["headings"] = "NEEDS_REVIEW"
    store.put("run", run["id"], run)
    base = store.directory(run["id"])
    results = [
        batch(1, r"\BAHeading{h}\label{lemma:1}", [heading("h", 1, "1")]),
        batch(2, r"\BAHeading{h}\label{lemma:1}See \ref{lemma:1}.", [heading("h", 2, "1.1")]),
    ]
    setup = dict(documentclass="article", toc=[], rules="", numbering=dict(rules=[], initial=[]))
    atomic_json(base / "joined.json", results)
    atomic_json(base / "setup.json", setup)
    if legacy:
        atomic_json(base / "repairs/heading-map.json", {
            "input_hash": "old-combined-request",
            "feedback": {"candidate": {"headings": [], "label_edits": [], "reference_edits": []}},
        })
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(purpose)
        if purpose == "headings":
            assert not images and "targets" not in payload and "references_to_check" not in payload
            assert "repair_feedback" not in payload
            assert set(schema["properties"]) == {"headings", "appendix_start"}
            heads = [{"id": h["id"], "level": h["level"]} for h in payload["headings"]]
            heads[1]["level"] = "subsection"
            return {"headings": heads, "appendix_start": ""}
        assert purpose == "references"
        assert set(schema["properties"]) == {"label_edits", "reference_edits", "unconfirmed_bibliography", "unconfirmed_references", "repair_requests", "search", "read_context", "view_pages"}
        assert payload["headings"][1]["level"] == "subsection"
        assert payload["headings"][1]["page"] == 2
        assert payload["headings"][1]["number"] == "1.1"
        assert payload["headings"][1]["title"] == "Section title"
        target = next(t for t in payload["targets"] if t["page"] == 2)
        assert [h["id"] for h in target["scope"]] == ["b1-h", "b2-h"]
        if calls.count("references") == 1:
            raise WorkflowError("REPAIR_LIMIT", "Reference repair paused")
        return {
            "label_edits": [{"id": "p2-label-0", "key": "lemma:1:section-1.1"}],
            "reference_edits": [{"id": "p2-reference-0", "key": "lemma:1:section-1.1"}],
        }

    compiled = []

    async def compile(run, resolved, heads, setup, index):
        assert heads[1]["level"] == "subsection"
        assert r"\ref{lemma:1:section-1.1}" in resolved[1]["pages"][0]["tex"]
        assert index["references"][0]["scope"][-1]["id"] == "b2-h"
        compiled.append(True)

    monkeypatch.setattr(engine.providers, "generate", generate)
    monkeypatch.setattr(engine, "compile", compile)
    await engine.execute(run["id"])
    stopped = store.get("run", run["id"])
    assert stopped["stage"] == "references" and stopped["state"] == "NEEDS_REVIEW"
    assert stopped["stages"]["headings"] == "PASSED"
    assert (base / "headings.json").exists() and (base / "heading-edits.json").exists()
    assert not (base / "structured.json").exists() and not compiled
    assert set(stopped["stages"]) == set(STAGES)
    store.change(run["id"], lambda r: r.update(state="RUNNING", error=None))
    await engine.execute(run["id"])
    completed = store.get("run", run["id"])
    assert completed["state"] == "COMPLETED"
    assert calls == ["headings", "references", "references"] and compiled == [True]
    assert (base / "reference-edits.json").exists()
    assert json.loads((base / "joined.json").read_text()) == results


@pytest.mark.asyncio
async def test_empty_headings_do_not_scan_or_validate_references(app):
    run = make_run(app)
    setup = {"appendix_start": "stale"}
    results = [{"headings": [], "pages": [{"page": 1, "tex": r"\ref{invalid-key}"}]}]
    assert await app.state.engine.headings(run, results, setup) == []
    assert setup["appendix_start"] == ""


@pytest.mark.asyncio
async def test_clean_references_skip_model_request(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    results = [batch(1, r"\label{lemma:1}See \ref{lemma:1}.")]

    async def forbidden(*args, **kwargs):
        pytest.fail("Unique, resolved references need no model request")

    monkeypatch.setattr(engine.providers, "generate", forbidden)
    resolved, index = await engine.references(run, results, [], {})
    assert resolved == results and index["references"][0]["key"] == "lemma:1"
    assert set(Headings.model_fields) == {"headings", "appendix_start"}
    assert set(HeadingLevel.model_fields) == {"id", "level"}
    assert set(References.model_fields) == {"label_edits", "reference_edits", "unconfirmed_bibliography", "unconfirmed_references"}


def test_heading_stage_rejects_source_fields():
    Headings.model_validate({
        "headings": [{"id": "batch-0059-exercises", "level": "subsection"}],
        "appendix_start": "",
    })
    with pytest.raises(ValidationError):
        Headings.model_validate({
            "headings": [{
                "id": "batch-0059-exercises",
                "level": "subsection",
                "page": 295,
                "number": "",
                "title": "EXERCISES",
            }],
            "appendix_start": "",
        })


def test_heading_coverage_names_the_first_rewritten_id(app):
    original = [
        {"id": "batch-0059-exercises", "page": 295, "level": "section", "number": "", "title": "EXERCISES"},
        {"id": "batch-0064-exercises", "page": 318, "level": "section", "number": "", "title": "EXERCISES"},
    ]
    updated = [
        {"id": "batch-0059-exercises-295", "level": "subsection"},
        {"id": "batch-0064-exercises-318", "level": "section"},
    ]
    with pytest.raises(WorkflowError) as error:
        app.state.engine.validate_headings(original, updated, "", {"documentclass": "book"})
    assert error.value.code == "HEADING_COVERAGE"
    assert error.value.message == (
        "第 1 条 id 被改写：原为 batch-0059-exercises，收到 batch-0059-exercises-295。"
    )


def test_heading_coverage_names_a_missing_id(app):
    original = [
        {"id": "a", "page": 1, "level": "section", "number": "", "title": "A"},
        {"id": "b", "page": 2, "level": "section", "number": "", "title": "B"},
    ]
    with pytest.raises(WorkflowError) as error:
        app.state.engine.validate_headings(
            original, [{"id": "a", "level": "chapter"}], "", {"documentclass": "book"},
        )
    assert error.value.message == "标题 id 数量对不上：原有 2 条，收到 1 条。第 2 条原 id 是 b。"
