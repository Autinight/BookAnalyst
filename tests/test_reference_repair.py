"""Visual escalation restores real targets within explicit fragment grants."""
import asyncio
import copy
import json

import pytest
from PIL import Image

from bookanalyst.documents import ReferenceRepair, ReferenceAction
from bookanalyst.numbering import collect_symbols, reference_groups, symbol_problems
from bookanalyst.reference_repair import apply_fragments, validate_repair_requests
from bookanalyst.store import WorkflowError
from test_numbering import batch
from test_rebuild import make_run
from test_reference_tools import action
from test_subscription_schema import assert_strict_schema


@pytest.fixture
def images(app, monkeypatch, tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "white").save(path)
    async def image(*args, **kwargs):
        return path
    monkeypatch.setattr(app.state.engine, "image", image)


def grant(page, start=1, end=0, authorize=()):
    return {"page": page, "start_line": start, "end_line": end, "authorize": list(authorize),
            "reason": "Original image confirms this numbered target; converted fragment is missing its label/content."}


def no_problems(index):
    assert not any(symbol_problems(index).values())


@pytest.mark.asyncio
async def test_missing_formula_repair_reindexes_new_reference_occurrences(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app)
    data = [batch(1, r"See \eqref{equation:14.53} and \ref{lemma:2}."),
            batch(2, "Before\nMissing formula here\nAfter"),
            batch(3, r"\label{lemma:2:scope} Existing result.")]
    calls = []
    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        calls.append((purpose, p))
        if purpose == "reference_repair":
            assert p["granted_fragments"][0]["start_line"] == 2
            assert p["granted_fragments"][0]["end_line"] == 2
            assert p["page_images"] == [2] and len(images) == 1
            return {"fragments": [{"page": 2, "start_line": 2, "end_line": 2,
                "tex": r"\begin{equation}\label{equation:14.53}x=1\end{equation}" + "\n" + r"By \ref{lemma:2}."}]}
        if p["keys_to_check"] == ["lemma:2"]:
            return action(reference_edits=[{"id": r["id"], "key": "lemma:2:scope"} for r in p["references_to_check"]])
        if not p["tool_round"]:
            assert "repair_requests" in schema["required"]
            return action(view_pages=[2], read_context=[{"page": 2, "start_line": 1, "end_line": 0}])
        return action(repair_requests=[grant(2, 2, 2)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert data[1]["pages"][0]["tex"] == "Before\nMissing formula here\nAfter"
    assert fixed[1]["pages"][0]["tex"].startswith("Before\n\\begin{equation}")
    assert fixed[1]["pages"][0]["tex"].endswith("After")
    assert sum(p == "reference_repair" for p, _ in calls) == 1
    assert len([p for purpose, p in calls if purpose == "references" and p["keys_to_check"] == ["lemma:2"]]) == 2
    before = len(calls)
    assert await engine.references(run, data, [], {}) == (fixed, index)
    assert len(calls) == before


@pytest.mark.asyncio
async def test_two_remark_targets_added_then_occurrences_bound_separately(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app)
    data = [batch(1, r"Remark \ref{remark:4} in 12.1; Remark \ref{remark:4} in 12.4."),
            batch(2, "Remarks. (1) A. (2) B. (3) C. (4) Global estimates."),
            batch(3, "Remarks. (1) A. (2) B. (3) C. (4) Additional smoothness.")]
    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        if purpose == "reference_repair":
            assert p["page_images"] == [2, 3]
            return {"fragments": [{"page": n, "start_line": 1, "end_line": 1,
                "tex": r"\begin{enumerate}\item A.\item B.\item C.\item\label{remark:4:" + scope + "}" + text + r"\end{enumerate}"}
                for n, scope, text in [(2, "12.1", "Global estimates."), (3, "12.4", "Additional smoothness.")]]}
        targets = {t["key"] for t in p["targets"]}
        if {"remark:4:12.1", "remark:4:12.4"} <= targets:
            return action(reference_edits=[{"id": "p1-reference-0", "key": "remark:4:12.1"},
                                           {"id": "p1-reference-1", "key": "remark:4:12.4"}])
        if not p["tool_round"]:
            return action(view_pages=[2, 3])
        return action(repair_requests=[grant(2), grant(3)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert [r["key"] for r in index["references"]] == ["remark:4:12.1", "remark:4:12.4"]
    assert fixed[1]["pages"][0]["tex"].count(r"\item") == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("same_page", [False, True])
async def test_repairs_parallel_on_disjoint_pages_and_rebase_shared_page(app, monkeypatch, images, same_page):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    data = [batch(1, r"\ref{lemma:1} \ref{lemma:2}")]
    data += [batch(2, "Slot A\nSlot B")] if same_page else [batch(2, "Slot A"), batch(3, "Slot B")]
    active = peak = 0
    ready = asyncio.Event()
    async def generate(run, role, purpose, prompt, schema, images=()):
        nonlocal active, peak
        p = json.loads(prompt)
        key = p.get("keys_to_check", p.get("reported_keys"))[0]
        num = int(key.split(":")[1]); page = 2 if same_page else num + 1
        if purpose == "reference_repair":
            active += 1; peak = max(peak, active)
            if same_page or active == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.sleep(0.01)
            g = p["granted_fragments"][0]
            if same_page and num == 2:
                assert g["start_line"] > 2  # First repair inserted lines above this grant.
            active -= 1
            return {"fragments": [{k: g[k] for k in ("page", "start_line", "end_line")} | {
                "tex": "\\begin{lemma}\n\\label{" + key + "}\nRestored.\n\\end{lemma}"}]}
        if not p["tool_round"]:
            return action(view_pages=[page])
        return action(repair_requests=[grant(page, num if same_page else 1, num if same_page else 1)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    _, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert peak == (1 if same_page else 2)


@pytest.mark.asyncio
async def test_pause_keeps_committed_repair_and_resumes_remaining_job(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app, llm_concurrency=1)
    data = [batch(1, r"\ref{lemma:1}\ref{lemma:2}"), batch(2, "Slot A"), batch(3, "Slot B")]
    repaired = []
    stop = True
    async def generate(run, role, purpose, prompt, schema, images=()):
        nonlocal stop
        p = json.loads(prompt)
        key = p.get("keys_to_check", p.get("reported_keys"))[0]
        page = int(key.split(":")[1]) + 1
        if purpose == "reference_repair":
            if page == 3 and stop:
                stop = False
                raise WorkflowError("PAUSED", "Pause second local repair")
            repaired.append(page)
            return {"fragments": [{"page": page, "start_line": 1, "end_line": 1,
                "tex": r"\begin{lemma}\label{" + key + r"}Restored\end{lemma}"}]}
        if not p["tool_round"]:
            return action(view_pages=[page])
        return action(repair_requests=[grant(page)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="Pause second"):
        await engine.references(run, data, [], {})
    path = engine.store.directory(run["id"]) / "reference-progress.json"
    progress = json.loads(path.read_text(encoding="utf-8"))
    assert len(progress["pending_repairs"]) == 1
    assert r"\label{lemma:1}" in progress["results"][1]["pages"][0]["tex"]
    _, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert repaired == [2, 3]


@pytest.mark.parametrize("case", ["unseen", "outside", "mixed", "overlap", "duplicate_phase", "bad_line"])
def test_escalation_scope_is_enforced(case):
    group = {"phase": "missing"}
    a = action(repair_requests=[grant(1)])
    pages, viewed = {1: "Text"}, {1}
    if case == "unseen": viewed = set()
    if case == "outside": pages = {2: "Other"}
    if case == "mixed": a["view_pages"] = [1]
    if case == "overlap": a["repair_requests"].append(grant(1))
    if case == "duplicate_phase": group["phase"] = "duplicates"
    if case == "bad_line": a["repair_requests"][0]["start_line"] = 5
    with pytest.raises(WorkflowError):
        validate_repair_requests(group, a, pages, viewed)


@pytest.mark.parametrize("case", ["scope", "marker", "unsafe"])
def test_local_worker_cannot_exceed_grant_or_fake_completion(case):
    data = [batch(1, r"Text \ref{lemma:9}"), batch(2, r"\label{lemma:9}")]
    data[0]["pages"][0]["tex"] = r"\BAFigure{f} " + data[0]["pages"][0]["tex"]
    grants = [grant(1, 1, 1)]
    candidate = {"fragments": [{"page": 1, "start_line": 1, "end_line": 1,
                               "tex": data[0]["pages"][0]["tex"] + r"\label{lemma:1}"}]}
    f = candidate["fragments"][0]
    if case == "scope": f["page"] = 2
    if case == "marker": f["tex"] = f["tex"].replace(r"\BAFigure{f}", "")
    if case == "unsafe": f["tex"] += r"\input{other.tex}"
    with pytest.raises(WorkflowError):
        apply_fragments(data, [], grants, candidate, ["lemma:1"])


def test_granted_fragment_allows_reference_removal_without_extra_authorization():
    data = [batch(1, r"By \eqref{equation:4.5.32}.")]
    candidate = {"fragments": [{"page": 1, "start_line": 1, "end_line": 1, "tex": "By (4.5.32)."}]}
    authorized = grant(1, 1, 1) | {"authorized_references": [
        {"id": "p1-reference-0", "command": "eqref", "key": "equation:4.5.32"}]}
    fixed = apply_fragments(data, [], [authorized], candidate, ["equation:4.5.32"])
    assert fixed[0]["pages"][0]["tex"] == "By (4.5.32)."
    assert apply_fragments(data, [], [grant(1, 1, 1)], candidate, ["equation:4.5.32"]) == fixed


def test_legacy_grant_is_readable_without_requiring_per_reference_permissions():
    from bookanalyst.documents import ReferenceRepairRequest
    old = grant(1, 1, 1, ["p1-reference-0"])
    assert "authorize" not in ReferenceRepairRequest.model_validate(old).model_dump()
    assert "authorize" not in ReferenceRepairRequest.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_authorized_spurious_reference_is_deleted_instead_of_invented(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app)
    data = [batch(1, r"By \eqref{equation:4.5.32} of Henkin-Leiterer.")]
    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        if purpose == "reference_repair":
            g = p["granted_fragments"][0]
            assert "authorize" not in g and "authorized_references" not in g
            return {"fragments": [{"page": 1, "start_line": 1, "end_line": 1,
                                   "tex": "By (4.5.32) of Henkin-Leiterer."}]}
        if not p["tool_round"]:
            return action(view_pages=[1])
        return action(repair_requests=[grant(1, 1, 1, ["p1-reference-0"])])
    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert fixed[0]["pages"][0]["tex"] == "By (4.5.32) of Henkin-Leiterer."
    assert index["references"] == []


def test_local_repair_contracts_are_strict():
    assert_strict_schema(ReferenceAction.model_json_schema())
    assert_strict_schema(ReferenceRepair.model_json_schema())


@pytest.mark.asyncio
async def test_overlapping_job_already_satisfied_by_prior_repair_is_not_repeated(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app, llm_concurrency=1)
    data = [batch(1, r"\ref{lemma:1}\ref{lemma:2}"), batch(2, "Two missing list labels")]
    repaired = []
    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        if purpose == "reference_repair":
            repaired.append(p["reported_keys"])
            return {"fragments": [{"page": 2, "start_line": 1, "end_line": 1,
                "tex": r"\begin{enumerate}\item\label{lemma:1}A\item\label{lemma:2}B\end{enumerate}"}]}
        if not p["tool_round"]:
            return action(view_pages=[2])
        return action(repair_requests=[grant(2)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    _, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert repaired == [["lemma:1"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_other", [False, True])
async def test_local_repair_and_rebinding_finish_while_other_group_is_still_running(app, monkeypatch, images, pause_other):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    data = [batch(1, r"Remark \ref{remark:4}; see \ref{theorem:9.7}."),
            batch(2, r"\label{theorem:9.7:scope}"), batch(3, "Missing numbered remark")]
    rebound = asyncio.Event()
    calls = []
    paused = False
    async def generate(run, role, purpose, prompt, schema, images=()):
        nonlocal paused
        p = json.loads(prompt)
        key = p.get("keys_to_check", p.get("reported_keys"))[0]
        calls.append((purpose, key))
        if key == "theorem:9.7":
            await asyncio.wait_for(rebound.wait(), 3)
            progress = json.loads((engine.store.directory(run["id"]) / "reference-progress.json").read_text(encoding="utf-8"))
            assert r"\ref{remark:4:scope}" in progress["results"][0]["pages"][0]["tex"]
            assert r"\label{remark:4:scope}" in progress["results"][2]["pages"][0]["tex"]
            if pause_other and not paused:
                paused = True
                raise WorkflowError("PAUSED", "Other reference paused")
            return action(reference_edits=[{"id": p["references_to_check"][0]["id"], "key": "theorem:9.7:scope"}])
        if purpose == "reference_repair":
            return {"fragments": [{"page": 3, "start_line": 1, "end_line": 1,
                "tex": r"\begin{remark}\label{remark:4:scope}Restored.\end{remark}"}]}
        if any(t["key"] == "remark:4:scope" for t in p["targets"]):
            rebound.set()
            return action(reference_edits=[{"id": p["references_to_check"][0]["id"], "key": "remark:4:scope"}])
        if not p["tool_round"]:
            return action(view_pages=[3])
        return action(repair_requests=[grant(3)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    if pause_other:
        with pytest.raises(WorkflowError, match="Other reference paused"):
            await engine.references(run, data, [], {})
    _, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert calls.count(("reference_repair", "remark:4")) == 1
    assert calls.count(("references", "theorem:9.7")) == (2 if pause_other else 1)


@pytest.mark.asyncio
async def test_inflight_reference_edit_is_relocated_after_repair_inserts_an_occurrence(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    data = [batch(1, r"\ref{remark:4}"), batch(2, r"\label{lemma:1}\label{theorem:9.7:scope}"),
            batch(3, "Slot\n" + r"See \ref{theorem:9.7}.")]
    repairing = asyncio.Event()
    locations = []
    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        key = p.get("keys_to_check", p.get("reported_keys"))[0]
        if key == "theorem:9.7":
            await asyncio.wait_for(repairing.wait(), 3)
            target = p["references_to_check"][0]["id"]
            locations.append(target)
            return action(reference_edits=[{"id": target, "key": "theorem:9.7:scope"}])
        if purpose == "reference_repair":
            repairing.set()
            await asyncio.sleep(0.03)
            return {"fragments": [{"page": 3, "start_line": 1, "end_line": 1,
                "tex": r"\begin{remark}\label{remark:4}By \ref{lemma:1}.\end{remark}"}]}
        if not p["tool_round"]:
            return action(view_pages=[3])
        return action(repair_requests=[grant(3, 1, 1)])
    monkeypatch.setattr(engine.providers, "generate", generate)
    _, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert locations == ["p3-reference-0", "p3-reference-1"]
    assert [r["key"] for r in index["references"] if r["page"] == 3] == ["lemma:1", "theorem:9.7:scope"]


@pytest.mark.asyncio
async def test_misread_reference_can_be_corrected_without_creating_the_wrong_target(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app)
    data = [batch(1, r"By \ref{theorem:4.21.1} (ii)."),
            batch(2, r"\begin{theorem}\label{theorem:4.2.1}Statement.\end{theorem}"),
            batch(3, r"By \ref{theorem:4.21.1} (iii).")]
    repairs = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        if purpose == "reference_repair":
            repairs.append(p)
            return {"fragments": [{"page": n, "start_line": 1, "end_line": 1,
                    "tex": data[n - 1]["pages"][0]["tex"].replace("4.21.1", "4.2.1")} for n in [1, 3]]}
        if not p["tool_round"]:
            return action(view_pages=[1, 3])
        return action(repair_requests=[grant(1), grant(3)])

    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, index = await engine.references(run, data, [], {})
    no_problems(index)
    assert len(repairs) == 1
    assert {r["key"] for r in index["references"]} == {"theorem:4.2.1"}
    assert not any(t["key"] == "theorem:4.21.1" for t in index["targets"])
    assert fixed[1] == data[1]


@pytest.mark.asyncio
async def test_unchanged_fragment_returns_to_reference_instead_of_replaying_cached_escalation(app, monkeypatch, images):
    engine, run = app.state.engine, make_run(app)
    data = [batch(1, r"By \ref{theorem:4.21.1}.")]
    repairs = []
    reassessments = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        if purpose == "reference_repair":
            repairs.append(p)
            assert len(repairs) == 1
            return {"fragments": [{"page": 1, "start_line": 1, "end_line": 1, "tex": data[0]["pages"][0]["tex"]}]}
        if p.get("source_repair_result"):
            reassessments.append(p)
            assert p["source_repair_result"]["changed"] is False
            if not p["tool_round"]:
                return action(view_pages=[1])
            return action(unconfirmed_references=[{"id": "p1-reference-0", "reason": "Source has this number; no confirmed target."}])
        if not p["tool_round"]:
            return action(view_pages=[1])
        return action(repair_requests=[grant(1)])

    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, _ = await engine.references(run, data, [], {})
    assert fixed == data and len(repairs) == 1 and reassessments
    path = engine.store.directory(run["id"]) / "reference-progress.json"
    progress = json.loads(path.read_text(encoding="utf-8"))
    assert not progress["pending_repairs"] and len(progress["reviews"]) == 1
