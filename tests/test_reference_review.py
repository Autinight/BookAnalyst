"""Uncertain source cross-references may finish explicitly without invented targets."""
import json

import pytest

from bookanalyst.numbering import apply_symbol_edits, collect_symbols, deferrable_duplicate_reference_ids, reference_groups, symbol_problems, validate_reference_group
from bookanalyst.reference_tools import REFERENCE_CONFIRMATION_INSTRUCTION
from bookanalyst.reference_repair import validate_repair_requests
from bookanalyst.store import WorkflowError, atomic_json, digest
from test_bibliography_review import pending
from test_numbering import batch
from test_rebuild import make_run
from test_reference_tools import action, search


def source():
    return [batch(1, r"See Theorem \ref{theorem:9.7}, also \ref{lemma:2}."),
            batch(2, r"\label{lemma:9.7} Distribution functions. \label{lemma:2:scope} Another result.")]


def uncertain():
    return pending(reason="Searched Theorem 9.7; source citation says theorem, but the only item 9.7 is an unrelated lemma. Target uncertain.")


@pytest.mark.asyncio
async def test_general_deferral_keeps_source_completes_other_group_and_resumes_old_queries(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    calls = []
    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if payload["keys_to_check"] == ["lemma:2"]:
            return action(reference_edits=[{"id": "p1-reference-1", "key": "lemma:2:scope"}])
        assert "unconfirmed_references" in schema["required"]
        assert "This completes the group as pending confirmation" in payload["instruction"]
        if not payload["tool_round"]:
            return action(search=[search("theorem:9.7")], read_context=[{"page": 2, "start_line": 1, "end_line": 0}])
        raise WorkflowError("PAUSED", "pause with prior search evidence")
    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="pause with prior search evidence"):
        await engine.references(run, source(), [], {})
    # Simulate query records written by the version before general deferral existed.
    original = next(p for p in calls if p["keys_to_check"] == ["theorem:9.7"])
    payload = {k: v for k, v in original.items() if k not in {"tool_round", "tool_history", "page_images"}}
    payload["instruction"] = payload["instruction"].removeprefix(REFERENCE_CONFIRMATION_INSTRUCTION).replace(
        "until this group is resolved or explicitly marked for confirmation", "until this group is resolved")
    base = engine.store.directory(run["id"])
    for path in (base / "reference-groups").glob("*-queries.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["input_hash"] = digest(payload)
        atomic_json(path, data)
    # Use the worker snapshot directly to verify compatible queries independently of other committed edits.
    index = collect_symbols(source(), [])
    from bookanalyst.reference_tools import ReferenceLibrary
    group = next(g for g in reference_groups(index, "missing") if g["keys"] == ["theorem:9.7"])
    async def finish(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        assert payload["tool_round"] == 1
        return action(unconfirmed_references=[uncertain()])
    monkeypatch.setattr(engine.providers, "generate", finish)
    patches = await engine.reference_workers(run, index, [group], [], {}, ReferenceLibrary(run["source"], source(), index))
    assert patches[0]["unconfirmed_references"] == [uncertain()]
    # Complete against the original snapshot, then verify durable resume makes no more model calls.
    async def current(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        if payload["keys_to_check"] == ["lemma:2"]:
            return action(reference_edits=[{"id": "p1-reference-1", "key": "lemma:2:scope"}])
        if not payload["tool_round"]:
            return action(search=[search("theorem:9.7")])
        return action(unconfirmed_references=[uncertain()])
    monkeypatch.setattr(engine.providers, "generate", current)
    fixed, index = await engine.references(run, source(), [], {})
    assert r"\ref{theorem:9.7}" in fixed[0]["pages"][0]["tex"]
    assert r"\ref{lemma:2:scope}" in fixed[0]["pages"][0]["tex"]
    assert [r["key"] for r in symbol_problems(index)["unresolved_references"]] == ["theorem:9.7"]
    reviews = json.loads((base / "reference-review.json").read_text(encoding="utf-8"))
    assert reviews[0]["key"] == "theorem:9.7" and reviews[0]["search_evidence"]
    assert json.loads((base / "bibliography-review.json").read_text(encoding="utf-8")) == []
    assert any(t["state"] == "DEFERRED" and t["error"]["code"] == "REFERENCE_UNCONFIRMED" for t in engine.store.tasks(run["id"], "references"))
    async def unexpected(*args, **kwargs):
        pytest.fail("Confirmed deferral should not dispatch again on resume")
    monkeypatch.setattr(engine.providers, "generate", unexpected)
    assert await engine.references(run, source(), [], {}) == (fixed, index)


@pytest.mark.parametrize("case", ["foreign_group", "edited", "blank", "duplicate", "existing_target", "duplicate_phase", "bibliography"])
def test_general_deferral_has_narrow_ownership(case):
    results = source()
    original_index = collect_symbols(results, [])
    group = next(g for g in reference_groups(original_index, "missing") if g["keys"] == ["theorem:9.7"])
    changes = action(unconfirmed_references=[uncertain()])
    if case == "foreign_group":
        changes["unconfirmed_references"][0]["id"] = "p1-reference-1"
    elif case == "edited":
        changes["reference_edits"] = [{"id": "p1-reference-0", "key": "lemma:9.7"}]
    elif case == "blank":
        changes["unconfirmed_references"][0]["reason"] = "  "
    elif case == "duplicate":
        changes["unconfirmed_references"].append(uncertain())
    elif case == "existing_target":
        results[1]["pages"][0]["tex"] += r"\label{theorem:9.7}"
    elif case == "duplicate_phase":
        group["phase"] = "duplicates"
    elif case == "bibliography":
        results[0]["pages"][0]["tex"] = r"\ref{bibliography:MM}"
    with pytest.raises(WorkflowError) as exc:
        validate_reference_group(collect_symbols(results, []), group, changes)
    assert exc.value.code == "REFERENCE_REVIEW"


def test_duplicate_phase_defers_only_unbound_cross_group_references():
    results = [
        batch(1, r"See \ref{equation:3.3}."),
        batch(2, r"\begin{equation}x=1\label{equation:3.3}\end{equation}"),
        batch(3, r"\begin{equation}y=2\label{equation:3.3}\end{equation}"),
    ]
    index = collect_symbols(results, [])
    group = next(g for g in reference_groups(index, "duplicates") if g["keys"] == ["equation:3.3"])
    ref = group["references"][0]
    edits = [{"id": item, "key": f"equation:3.3:s{i}"} for i, item in enumerate(group["label_ids"])]
    changes = action(label_edits=edits, unconfirmed_references=[
        {"id": ref["id"], "reason": "The matching target is outside this duplicate-label group."},
    ])
    assert deferrable_duplicate_reference_ids(index, group, changes) == {ref["id"]}
    validate_reference_group(index, group, changes)
    fixed, updated = apply_symbol_edits(
        results, [], {"label_edits": edits, "reference_edits": []}, require_resolved=False
    )
    assert r"\ref{equation:3.3}" in fixed[0]["pages"][0]["tex"]
    assert not symbol_problems(updated)["duplicate_labels"]
    assert [r["id"] for r in symbol_problems(updated)["unresolved_references"]] == [ref["id"]]
    with pytest.raises(WorkflowError):
        validate_reference_group(index, group, action(unconfirmed_references=changes["unconfirmed_references"]))


def test_general_deferral_does_not_hide_other_errors_or_mix_with_repair():
    with pytest.raises(WorkflowError) as exc:
        apply_symbol_edits(source(), [], action(unconfirmed_references=[uncertain()]))
    assert exc.value.code == "UNRESOLVED_REFERENCE"
    group = next(g for g in reference_groups(collect_symbols(source(), []), "missing") if g["keys"] == ["theorem:9.7"])
    with pytest.raises(WorkflowError) as exc:
        validate_repair_requests(group, action(unconfirmed_references=[uncertain()],
            repair_requests=[{"page": 2, "start_line": 1, "end_line": 0, "reason": "Missing label"}]), {2: "body"}, {2})
    assert exc.value.code == "REFERENCE_ACTION"


@pytest.mark.asyncio
async def test_general_deferral_requires_prior_read_and_separate_final(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    errors = []
    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        if payload["tool_round"]:
            return action(unconfirmed_references=[uncertain()])
        if "repair_feedback" not in payload:
            return action(unconfirmed_references=[uncertain()])
        errors.append(payload["repair_feedback"]["code"])
        assert len(errors) <= 2
        if len(errors) == 1:
            return action(search=[search("theorem:9.7")], unconfirmed_references=[uncertain()])
        return action(search=[search("theorem:9.7")])
    monkeypatch.setattr(engine.providers, "generate", generate)
    results = [batch(1, r"\ref{theorem:9.7}")]
    fixed, _ = await engine.references(run, results, [], {})
    assert fixed == results
    assert errors == ["REFERENCE_REVIEW", "REFERENCE_ACTION"]
