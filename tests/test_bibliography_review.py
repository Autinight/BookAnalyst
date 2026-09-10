"""Uncertain bibliography identity is preserved and explicitly handed off."""

import json

import pytest
from fastapi.testclient import TestClient

from bookanalyst.numbering import (
    apply_symbol_edits, collect_symbols, reference_groups, symbol_problems,
    validate_reference_group,
)
from bookanalyst.store import WorkflowError, atomic_json
from test_numbering import batch
from test_rebuild import make_run
from test_reference_tools import action, search


def source():
    return [batch(1, r"Degenerate operators [\ref{bibliography:MM}]. See \ref{lemma:2}."),
            batch(2, r"\label{bibliography:MV2} Boundary behavior of degenerate equations."
                     r"\label{lemma:2:scope} A result.")]


def pending(id="p1-reference-0", reason="Checked the bibliography: MM absent; MV2 has a similar topic, but authors/title do not establish identity."):
    return {"id": id, "reason": reason}


@pytest.mark.asyncio
async def test_preserves_citation_finishes_other_group_and_resumes_without_requery(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if payload["keys_to_check"] == ["lemma:2"]:
            return action(reference_edits=[{"id": "p1-reference-1", "key": "lemma:2:scope"}])
        assert payload["bibliography_entry_pages"] == [2]
        assert "similar spelling or subject alone is not enough" in payload["instruction"]
        assert "unconfirmed_bibliography" in schema["required"]
        if not payload["tool_round"]:
            return action(search=[search("bibliography:MM")],
                          read_context=[{"page": 2, "start_line": 1, "end_line": 0}])
        return action(unconfirmed_bibliography=[pending()])

    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, index = await engine.references(run, source(), [], {})
    assert r"\ref{bibliography:MM}" in fixed[0]["pages"][0]["tex"]
    assert r"\ref{lemma:2:scope}" in fixed[0]["pages"][0]["tex"]
    assert [r["key"] for r in symbol_problems(index)["unresolved_references"]] == ["bibliography:MM"]
    records = engine.store.tasks(run["id"], "references")
    assert sorted(t["state"] for t in records) == ["DEFERRED", "PASSED"]
    base = engine.store.directory(run["id"])
    review = json.loads((base / "bibliography-review.json").read_text(encoding="utf-8"))
    assert review[0]["key"] == "bibliography:MM" and review[0]["page"] == 1
    assert review[0]["search_evidence"][0]["search"][0]["total"] == 0
    assert review[0]["search_evidence"][0]["read_context"][0]["page"] == 2
    assert "contexts" not in review[0]["search_evidence"][0]
    assert await engine.references(run, source(), [], {}) == (fixed, index)
    assert len(calls) == 3
    with TestClient(app) as client:
        # Mock providers do not write call receipts; record one to test the UI-facing summary.
        from test_request_history import record
        task = next(t for t in records if t["state"] == "DEFERRED")
        record(engine.store, run, task["id"], "COMPLETED", purpose="references")
        row = client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()["rows"][0]
        assert row["state"] == "DEFERRED" and row["failed_count"] == 0
        assert row["error_message"] == pending()["reason"]


@pytest.mark.parametrize("case", ["non_bibliography", "edited", "duplicate", "blank_reason", "foreign_group", "existing_target"])
def test_deferral_cannot_bypass_reference_ownership_or_validation(case):
    results = source()
    if case == "existing_target":
        results[1]["pages"][0]["tex"] += r"\label{bibliography:MM}"
    index = collect_symbols(results, [])
    # Keep the original missing group for the existing-target validation case.
    group = next(g for g in reference_groups(collect_symbols(source(), []), "missing") if g["keys"] == ["bibliography:MM"])
    changes = {"label_edits": [], "reference_edits": [], "unconfirmed_bibliography": [pending()]}
    if case == "non_bibliography":
        changes["unconfirmed_bibliography"] = [pending("p1-reference-1")]
    elif case == "edited":
        changes["reference_edits"] = [{"id": "p1-reference-0", "key": "bibliography:MV2"}]
    elif case == "duplicate":
        changes["unconfirmed_bibliography"].append(pending())
    elif case == "blank_reason":
        changes["unconfirmed_bibliography"] = [pending(reason="  ")]
    elif case == "foreign_group":
        group = next(g for g in reference_groups(index, "missing") if g["keys"] == ["lemma:2"])
    with pytest.raises(WorkflowError) as exc:
        validate_reference_group(index, group, changes)
    assert exc.value.code == "BIBLIOGRAPHY_REVIEW"


def test_deferral_does_not_hide_other_unresolved_references():
    with pytest.raises(WorkflowError) as exc:
        apply_symbol_edits(source(), [], {"label_edits": [], "reference_edits": [],
                                          "unconfirmed_bibliography": [pending()]})
    assert exc.value.code == "UNRESOLVED_REFERENCE"


@pytest.mark.asyncio
async def test_cannot_declare_uncertain_before_reading_and_cannot_mix_query_and_final(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    results = [batch(1, r"\ref{bibliography:MM}"), batch(2, r"\label{bibliography:MV2}")]
    feedback = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        if payload["tool_round"]:
            return action(unconfirmed_bibliography=[pending()])
        if "repair_feedback" not in payload:
            return action(unconfirmed_bibliography=[pending()])
        feedback.append(payload["repair_feedback"]["code"])
        if len(feedback) == 1:
            return action(search=[search("MM")], unconfirmed_bibliography=[pending()])
        return action(read_context=[{"page": 2, "start_line": 1, "end_line": 0}])

    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, _ = await engine.references(run, results, [], {})
    assert fixed == results
    assert feedback == ["BIBLIOGRAPHY_REVIEW", "REFERENCE_ACTION"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bibliography", "theorem"])
async def test_deferred_stage_advances_to_compiler_and_resume_skips_reference_workers(app, monkeypatch, kind):
    key = f"{kind}:MM"
    field = "unconfirmed_bibliography" if kind == "bibliography" else "unconfirmed_references"
    review_file = "bibliography-review.json" if kind == "bibliography" else "reference-review.json"
    engine, store, run = app.state.engine, app.state.store, make_run(app)
    run["config"]["resolved"] = True
    run["stages"] = {s: "PASSED" if s not in ("references", "finish") else "PENDING" for s in run["stages"]}
    store.put("run", run["id"], run)
    base = store.directory(run["id"])
    atomic_json(base / "joined.json", [batch(1, rf"\ref{{{key}}}")])
    atomic_json(base / "setup.json", {})
    atomic_json(base / "headings.json", [])
    generated, compiled = [], []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        generated.append(payload)
        if not payload["tool_round"]:
            return action(search=[search(key)])
        return action(**{field: [pending()]})

    async def compile(*args):
        compiled.append(True)
        assert store.get("run", run["id"])["stages"]["references"] == "DEFERRED"
        raise WorkflowError("PAUSED", "Pause final compiler")

    monkeypatch.setattr(engine.providers, "generate", generate)
    monkeypatch.setattr(engine, "compile", compile)
    await engine.execute(run["id"])
    await engine.execute(run["id"])
    assert len(generated) == 2 and len(compiled) == 2
    assert store.get("run", run["id"])["stages"]["references"] == "DEFERRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bibliography", "theorem"])
async def test_compile_receives_review_file_and_preserves_undefined_reference(app, monkeypatch, kind):
    key = f"{kind}:MM"
    field = "unconfirmed_bibliography" if kind == "bibliography" else "unconfirmed_references"
    review_file = "bibliography-review.json" if kind == "bibliography" else "reference-review.json"
    from bookanalyst.reference_review import compiler_reference_policy
    engine, run = app.state.engine, make_run(app)
    base = engine.store.directory(run["id"])
    notes = [pending() | {"key": key, "page": 1, "search_evidence": []}]
    atomic_json(base / review_file, notes)
    seen = []

    async def compiler(project):
        assert rf"\ref{{{key}}}" in (project / "body.tex").read_text(encoding="utf-8")
        return {"status": "FAILED", "code": "REFERENCE_ERROR"}

    async def agent(providers, current, project, report):
        seen.append(True)
        assert json.loads((project / review_file).read_text(encoding="utf-8")) == notes
        assert "not resolved" in compiler_reference_policy(project)
        assert report["status"] == "FAILED"
        raise WorkflowError("PAUSED", "Keep unresolved citation")

    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    monkeypatch.setattr("bookanalyst.engine.repair_project", agent)
    with pytest.raises(WorkflowError, match="Keep unresolved citation"):
        await engine.compile(run, [batch(1, rf"\ref{{{key}}}")], [], {"documentclass": "article"})
    assert seen == [True]
