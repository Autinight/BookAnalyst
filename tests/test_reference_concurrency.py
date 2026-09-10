"""Bounded parallel reference repair, occurrence ownership and durable group results."""

import asyncio
import copy
import json

import pytest

from bookanalyst.numbering import (
    collect_symbols, reference_groups, symbol_problems, validate_reference_group,
)
from bookanalyst.store import WorkflowError
from test_numbering import batch, heading
from test_rebuild import make_run


def source(count=6):
    # All groups edit the same physical page; offset-based merging must not overwrite.
    tex = " ".join(
        rf"\label{{lemma:{n}}} A. \label{{lemma:{n}}} B. See \ref{{lemma:{n}}}."
        for n in range(1, count + 1)
    )
    return [batch(1, tex)]


def repair(payload):
    targets = [t for t in payload["targets"] if t["id"] in payload["editable_label_ids"]]
    labels = [{"id": t["id"], "key": t["key"] + ":scope-" + t["id"]} for t in targets]
    key = labels[0]["key"] if labels else payload["targets"][0]["key"]
    return {
        "label_edits": labels,
        "reference_edits": [{"id": r["id"], "key": key} for r in payload["references_to_check"]],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_parallel_groups_merge_same_page_and_resume(app, monkeypatch, concurrency):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=concurrency)
    results = source()
    original = copy.deepcopy(results)
    active = peak = calls = 0
    ready = asyncio.Event()

    async def generate(run, role, purpose, prompt, schema, images=()):
        nonlocal active, peak, calls
        payload = json.loads(prompt)
        assert purpose == "references" and not images
        assert len(payload["keys_to_check"]) == 1 and len(payload["targets"]) == 2
        assert len(payload["references_to_check"]) == 1
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == concurrency:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        # Finish the first group last to check order-independent persistence.
        await asyncio.sleep(0.02 if payload["keys_to_check"] == ["lemma:1"] else 0)
        active -= 1
        return repair(payload)

    monkeypatch.setattr(engine.providers, "generate", generate)
    resolved, index = await engine.references(run, results, [], {})
    assert calls == 6 and peak == concurrency and active == 0
    assert results == original and not symbol_problems(index)["duplicate_labels"]
    assert not symbol_problems(index)["unresolved_references"]
    assert resolved[0]["pages"][0]["tex"].count(":scope-") == 18
    assert await engine.references(run, results, [], {}) == (resolved, index)
    assert calls == 6
    records = engine.store.tasks(run["id"], "references")
    assert len(records) == 6 and all(t["state"] == "PASSED" for t in records)


@pytest.mark.asyncio
async def test_failure_keeps_other_inflight_group_and_stops_queue(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    calls = []
    ready = asyncio.Event()

    async def fail_one(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        key = payload["keys_to_check"][0]
        calls.append(key)
        if len(calls) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if key == "lemma:1":
            raise WorkflowError("REPAIR_LIMIT", "Stopped group")
        await asyncio.sleep(0.02)
        return repair(payload)

    monkeypatch.setattr(engine.providers, "generate", fail_one)
    with pytest.raises(WorkflowError, match="Stopped group"):
        await engine.references(run, source(4), [], {})
    assert calls == ["lemma:1", "lemma:2"]
    base = engine.store.directory(run["id"])
    assert len(list((base / "reference-groups").glob("*.json"))) == 1
    assert not (base / "reference-edits.json").exists()

    async def finish(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload["keys_to_check"][0])
        return repair(payload)

    monkeypatch.setattr(engine.providers, "generate", finish)
    _, index = await engine.references(run, source(4), [], {})
    assert calls.count("lemma:2") == 1
    assert set(calls[2:]) == {"lemma:1", "lemma:3", "lemma:4"}
    assert not symbol_problems(index)["unresolved_references"]


@pytest.mark.asyncio
async def test_missing_refs_use_completed_duplicate_targets_and_local_headings(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    results = [
        batch(1, r"\BAHeading{h}\label{exercise:1} A. \label{exercise:1} B. See \ref{problem:1}.", [heading("h", 1, "1")]),
        batch(2, r"\BAHeading{h}\label{theorem:99}", [heading("h", 2, "2")]),
    ]
    heads = [h for b in results for h in b["headings"]]
    phases = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        phases.append(payload["phase"])
        assert [h["id"] for h in payload["headings"]] == ["b1-h"]
        assert all(t["key"] != "theorem:99" for t in payload["targets"])
        if payload["phase"] == "missing":
            assert phases == ["duplicates", "missing"]
            assert not payload["editable_label_ids"]
            assert all(":scope-" in t["key"] for t in payload["targets"])
        return repair(payload)

    monkeypatch.setattr(engine.providers, "generate", generate)
    _, index = await engine.references(run, results, heads, {})
    assert not symbol_problems(index)["unresolved_references"]


def test_worker_cannot_edit_other_groups_or_readonly_candidates():
    index = collect_symbols(source(2), [])
    groups = reference_groups(index, "duplicates")
    foreign = {"label_edits": [{"id": groups[1]["targets"][0]["id"], "key": "lemma:2:foreign"}], "reference_edits": []}
    with pytest.raises(WorkflowError, match="当前引用组"):
        validate_reference_group(index, groups[0], foreign)
    missing = collect_symbols([batch(1, r"\label{exercise:1} See \ref{problem:1}.")], [])
    group = reference_groups(missing, "missing")[0]
    with pytest.raises(WorkflowError, match="当前引用组"):
        validate_reference_group(missing, group, {"label_edits": [{"id": "p1-label-0", "key": "exercise:1:new"}], "reference_edits": []})


def test_group_validation_reports_remaining_occurrences_and_ignores_other_groups():
    index = collect_symbols(source(2), [])
    group = reference_groups(index, "duplicates")[0]
    with pytest.raises(WorkflowError) as exc:
        validate_reference_group(index, group, {"label_edits": [], "reference_edits": []})
    assert "p1-reference-0" in exc.value.message and "lemma:1" in exc.value.message
    assert "lemma:2" not in exc.value.message
    validate_reference_group(index, group, repair({
        "targets": group["targets"], "editable_label_ids": group["label_ids"],
        "references_to_check": group["references"],
    }))


def test_same_source_number_namespaces_share_one_owner():
    index = collect_symbols([batch(1, r"\label{lemma:1} \label{lemma:1} \label{lemma:1:x} \label{lemma:1:x}")], [])
    groups = reference_groups(index, "duplicates")
    assert len(groups) == 1 and len(groups[0]["label_ids"]) == 4


@pytest.mark.asyncio
async def test_changed_evidence_invalidates_group_checkpoint(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        return repair(payload)

    monkeypatch.setattr(engine.providers, "generate", generate)
    results = source(1)
    await engine.references(run, results, [], {})
    results[0]["pages"][0]["tex"] += " Changed source context."
    await engine.references(run, results, [], {})
    assert len(calls) == 2
