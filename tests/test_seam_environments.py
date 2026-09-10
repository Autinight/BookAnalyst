"""Open environments are explicit, durable seam work rather than advisory flags."""

import asyncio
import copy
import json

import pytest

from bookanalyst.documents import validate_conversion
from bookanalyst.environments import environment_report, scan_environments
from bookanalyst.seam_environments import resolve_environments
from bookanalyst.store import WorkflowError, atomic_json
from test_rebuild import make_run, result


def batches(*texts):
    return [result([i + 1]) | {"pages": [{"page": i + 1, "tex": text}], "task_id": f"batch-{i+1:04d}"}
            for i, text in enumerate(texts)]


def action(**kwargs):
    return dict(edits=[], read_pages=[], resolved=False, closing_page=None, note="Continue this environment.") | kwargs


def empty_patch():
    return dict(left_suffix="", right_prefix="", replacement="")


def test_report_is_independent_of_tail_and_locates_nested_openings():
    value = result([1, 2])
    value["pages"] = [{"page": 1, "tex": "% \\begin{ignored}\n\\begin{lemma}\\label{lemma:1}\nText."},
                      {"page": 2, "tex": r"\begin{enumerate}\item Continued."}]
    expected = [{"name": "lemma", "label": "lemma:1", "begin_page": 1, "begin_line": 2},
                {"name": "enumerate", "label": "", "begin_page": 2, "begin_line": 1}]
    assert environment_report(value["pages"]) == expected
    with pytest.raises(WorkflowError) as error:
        validate_conversion(value, [1, 2])
    assert error.value.code == "ENVIRONMENT_REPORT"
    value["unclosed_environments"] = [{"name": e["name"], "begin_page": e["begin_page"]} for e in expected]
    validate_conversion(value, [1, 2])
    assert value["tail"] == "closed"  # A complete paragraph does not close its containing environment.
    assert environment_report([{"page": 1, "tex": r"\begin{proof}Text.\label{section:1}"}])[0]["label"] == ""


@pytest.mark.asyncio
async def test_old_conversion_report_is_filled_without_another_model_call(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    task = engine.store.tasks(run["id"], "convert")[0]
    value = result(task["pages"])
    value["pages"][-1]["tex"] = r"\begin{lemma}\label{lemma:1}Continues."
    path = engine.store.directory(run["id"]) / "batches" / (task["id"] + ".json")
    atomic_json(path, value)

    async def forbidden(*args, **kwargs):
        pytest.fail("Reporting metadata must not reconvert old pages")

    monkeypatch.setattr(engine, "checked_ask", forbidden)
    restored = await engine.convert(run, task, {})
    assert restored["pages"] == value["pages"]
    assert restored["unclosed_environments"][0]["name"] == "lemma"
    assert restored["unclosed_environments"][0]["begin_page"] == task["pages"][-1]


@pytest.mark.asyncio
async def test_closed_flags_and_empty_junction_cannot_bypass_environment_todo(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{lemma}\label{lemma:1}Statement continues", "and ends.\n\\begin{proof}Proof.\\end{proof}")
    calls = []

    async def junction(run, key, purpose, payload, schema, pages, validate):
        assert payload["unclosed_environments"][0]["name"] == "lemma"
        return empty_patch()

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        assert payload["todo"]["name"] == "lemma"
        if len(calls) == 1:
            return action(resolved=True, closing_page=2, note="Unsubstantiated approval")
        assert payload["repair_feedback"]["code"] == "SEAM_TODO_PENDING"
        return action(resolved=True, closing_page=2, note="Statement ends before the proof.",
                      edits=[{"page": 2, "old": r"\begin{proof}", "new": "\\end{lemma}\n\\begin{proof}"}])

    monkeypatch.setattr(engine, "checked_ask", junction)
    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await engine.seams(run, source)
    assert len(calls) == 2 and not scan_environments([p for r in joined for p in r["pages"]])["unclosed"]
    report = json.loads((engine.store.directory(run["id"]) / "seam-environment-todos.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASSED" and report["pending_count"] == 0
    assert report["items"][0]["end"]["page"] == 2
    assert source[1]["pages"][0]["tex"].startswith("and ends.\n\\begin{proof}")


@pytest.mark.asyncio
async def test_follow_continuation_to_later_pages_and_resume_with_notes(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{lemma}Long statement", "continues", "continues", "continues", "ends.\n\\begin{proof}Done.\\end{proof}")
    calls = []
    pause = True

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        calls.append(p)
        if len(calls) == 1:
            return action(read_pages=[4, 5], note="The statement continues past page 2; inspect its ending.")
        assert [page["page"] for page in p["pages"]] == [4, 5]
        assert "continues past page 2" in p["recent_actions"][-1]["note"]
        if pause:
            raise WorkflowError("PAUSED", "Test pause")
        return action(resolved=True, closing_page=5, note="The lemma ends at the proof on page 5.",
                      edits=[{"page": 5, "old": r"\begin{proof}", "new": "\\end{lemma}\n\\begin{proof}"}])

    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="Test pause"):
        await resolve_environments(engine, run, source)
    base = engine.store.directory(run["id"])
    assert json.loads((base / "seam-environment-todos.json").read_text())["status"] == "PENDING"
    pause = False
    joined = await resolve_environments(engine, run, source)
    assert len(calls) == 3
    assert r"\end{lemma}" in joined[4]["pages"][0]["tex"]
    assert await resolve_environments(engine, run, source) == joined
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["remove_opening", "change_content", "wrong_end", "unseen_page"])
async def test_invalid_fixes_remain_todos_and_get_precise_feedback(app, monkeypatch, bad):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{lemma}Statement", "ends.", "Next.")
    calls = []
    failures = {
        "remove_opening": ({"page": 1, "old": r"\begin{lemma}", "new": ""}, "SEAM_OPENING_REMOVED"),
        "change_content": ({"page": 2, "old": "ends.", "new": r"Changed.\end{lemma}"}, "SEAM_CONTENT_CHANGE"),
        "wrong_end": ({"page": 2, "old": "ends.", "new": r"ends.\end{theorem}"}, "SEAM_ENVIRONMENT_MISMATCH"),
        "unseen_page": ({"page": 3, "old": "Next.", "new": r"Next.\end{lemma}"}, "SEAM_PAGE_SCOPE"),
    }

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        calls.append(p)
        if len(calls) == 1:
            return action(edits=[failures[bad][0]], resolved=True, closing_page=2)
        assert p["repair_feedback"]["code"] == failures[bad][1]
        return action(edits=[{"page": 2, "old": "ends.", "new": r"ends.\end{lemma}"}], resolved=True, closing_page=2)

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert len(calls) == 2 and r"\begin{lemma}" in joined[0]["pages"][0]["tex"]


@pytest.mark.asyncio
async def test_already_matching_end_is_recorded_without_extra_request(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{lemma}Statement", r"ends.\end{lemma}")

    async def forbidden(*args, **kwargs):
        pytest.fail("An actual matching end is already a verifiable resolution")

    monkeypatch.setattr(engine.providers, "generate", forbidden)
    assert await resolve_environments(engine, run, source) == [r | {"unclosed_environments": environment_report(r["pages"])} for r in source]
    report = json.loads((engine.store.directory(run["id"]) / "seam-environment-todos.json").read_text())
    assert report["items"][0]["status"] == "resolved" and report["items"][0]["end"] == {"page": 2, "line": 1}


@pytest.mark.asyncio
async def test_environment_todos_run_concurrently_and_merge_independent_fixes(app, monkeypatch):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    source = batches(r"\begin{lemma}A", "ends.\n\\begin{proof}A proof.\\end{proof}",
                     r"\begin{theorem}B", "ends.\n\\begin{proof}B proof.\\end{proof}")
    active = peak = 0
    ready = asyncio.Event()

    async def generate(run, role, purpose, prompt, schema, images=()):
        nonlocal active, peak
        p = json.loads(prompt)
        active += 1
        peak = max(peak, active)
        if active == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        active -= 1
        page = 2 if p["todo"]["name"] == "lemma" else 4
        return action(edits=[{"page": page, "old": r"\begin{proof}",
                              "new": "\\end{" + p["todo"]["name"] + "}\n\\begin{proof}"}],
                      resolved=True, closing_page=page)

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert peak == 2
    assert not scan_environments([p for r in joined for p in r["pages"]])["unclosed"]


@pytest.mark.asyncio
async def test_orphan_end_is_an_explicit_todo(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"Text.\end{proof}")

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        assert p["todo"]["kind"] == "unmatched_end"
        return action(edits=[{"page": 1, "old": r"\end{proof}", "new": ""}], resolved=True,
                      note="The source has no proof here; remove its stray closing command.")

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert joined[0]["pages"][0]["tex"] == "Text."


@pytest.mark.asyncio
async def test_inflight_completed_fix_survives_another_worker_failure_and_prior_commit(app, monkeypatch):
    engine, run = app.state.engine, make_run(app, llm_concurrency=3)
    source = batches(r"\begin{lemma}A", "ends.\n\\begin{proof}A.\\end{proof}",
                     r"\begin{lemma}B", "ends.\n\\begin{proof}B.\\end{proof}",
                     r"\begin{lemma}C", "ends.\n\\begin{proof}C.\\end{proof}")
    calls = []
    ready = asyncio.Event()
    fail = True

    async def generate(run, role, purpose, prompt, schema, images=()):
        p = json.loads(prompt)
        start = p["todo"]["begin_page"]
        calls.append(start)
        if len(calls) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if start == 3 and fail:
            raise WorkflowError("AUTH_REQUIRED", "Test failed worker")
        return action(edits=[{"page": start + 1, "old": r"\begin{proof}",
                              "new": "\\end{lemma}\n\\begin{proof}"}], resolved=True, closing_page=start + 1)

    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="Test failed worker"):
        await resolve_environments(engine, run, source)
    base = engine.store.directory(run["id"])
    saved = json.loads((base / "seam-environments-progress.json").read_text())
    assert len(saved["edits"]) == 1
    states = {t["pages"][0]: t["state"] for t in engine.store.tasks(run["id"], "seams")}
    assert states[1] == "PASSED"
    assert states[5] == "WAITING_MERGE"
    fail = False
    joined = await resolve_environments(engine, run, source)
    assert calls.count(1) == 1 and calls.count(3) == 2 and calls.count(5) == 1
    assert not scan_environments([p for r in joined for p in r["pages"]])["unclosed"]


@pytest.mark.asyncio
async def test_unresolved_environment_cannot_pass_at_the_request_limit(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{lemma}Still open", "Continuation.")
    calls = []

    async def junction(*args, **kwargs):
        return empty_patch()

    async def generate(run, role, purpose, prompt, schema, images=()):
        calls.append(json.loads(prompt))
        return action(resolved=True, closing_page=2, note="No actual closing command.")

    monkeypatch.setattr(engine, "checked_ask", junction)
    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError) as error:
        await engine.seams(run, source)
    assert error.value.code == "REPAIR_LIMIT"
    base = engine.store.directory(run["id"])
    assert not (base / "joined.json").exists()
    assert json.loads((base / "seam-environment-todos.json").read_text())["pending_count"] == 1
    from bookanalyst.engine import REPAIR_ATTEMPTS_PER_ROUND
    assert len(calls) == 1 + REPAIR_ATTEMPTS_PER_ROUND



def test_proof_pair_accepts_existing_end_despite_unclosed_alignment():
    from bookanalyst.seam_environments import verify

    source = batches(r"\begin{proof}Text.\begin{align*}x=1", r"\end{proof}")
    pages = [p for r in source for p in r["pages"]]
    strict = scan_environments(pages)
    assert strict["opened"]["env-1-1"]["end"] is None
    node = strict["opened"]["env-1-1"] | {"kind": "unclosed"}
    verify(node, pages, pages, action(resolved=True, closing_page=2), {1, 2})
    # Existing closure is accepted even when strict discovery raised a todo.
    assert strict["unmatched"][0]["name"] == "proof"


def test_same_name_nested_pairs_still_match_their_own_end():
    from bookanalyst.seam_environments import verify

    pages = [p for r in batches(r"\begin{proof}Outer.\begin{proof}Inner.\begin{align*}x",
                                r"\end{proof}", r"\end{proof}") for p in r["pages"]]
    scan = scan_environments(pages, independent=True)
    outer = scan["opened"]["env-1-1"] | {"kind": "unclosed"}
    assert scan["opened"]["env-1-2"]["end"]["page"] == 2
    assert outer["end"]["page"] == 3
    with pytest.raises(WorkflowError, match="匹配"):
        verify(outer, pages, pages, action(resolved=True, closing_page=2), {1, 2, 3})
    verify(outer, pages, pages, action(resolved=True, closing_page=3), {1, 2, 3})


@pytest.mark.asyncio
async def test_alignment_fix_and_existing_proof_confirmation_do_not_block_each_other(app, monkeypatch):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    source = batches(r"\begin{proof}Text.\begin{align*}x=1", r"\end{proof}")
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload["todo"]["name"])
        if payload["todo"]["name"] == "proof":
            return action(resolved=True, closing_page=2, note="The proof already ends on page 2.")
        return action(edits=[{"page": 1, "old": "x=1", "new": r"x=1\end{align*}"}],
                      resolved=True, closing_page=1)

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert calls.count("align*") == 1
    assert len(calls) <= 4
    assert not scan_environments([p for r in joined for p in r["pages"]])["unclosed"]
    assert await resolve_environments(engine, run, source) == joined
    assert calls.count("align*") == 1
    assert len(calls) <= 4


@pytest.mark.asyncio
async def test_matched_pairs_do_not_trigger_whole_document_nesting_gate(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{proof}\begin{align*}x", r"\end{proof}\end{align*}")

    calls = []

    async def confirm(run, role, purpose, prompt, schema, images=()):
        calls.append(json.loads(prompt))
        return action(resolved=True, closing_page=2, note="This pair already has its ending on page 2.")

    monkeypatch.setattr(engine.providers, "generate", confirm)
    joined = await resolve_environments(engine, run, source)
    assert len(calls) == 2
    assert await resolve_environments(engine, run, source) == joined
    assert len(calls) == 2
    assert [p["tex"] for r in joined for p in r["pages"]] == [p["tex"] for r in source for p in r["pages"]]


@pytest.mark.asyncio
async def test_read_continuations_still_count_toward_configured_round_limit(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        calls.append(json.loads(prompt))
        return action(read_pages=[2])

    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError) as error:
        await resolve_environments(engine, run, batches(r"\begin{proof}Long proof", "Still continuing."))
    assert error.value.code == "REPAIR_LIMIT"
    from bookanalyst.engine import REPAIR_ATTEMPTS_PER_ROUND
    assert len(calls) == 1 + REPAIR_ATTEMPTS_PER_ROUND



@pytest.mark.parametrize("closing_page", [None, 406])
def test_orphan_end_feedback_identifies_early_closure(closing_page):
    from bookanalyst.seam_environments import verify, apply_edits

    pages = [{"page": 405, "tex": "\\begin{align*}\nx=1\n\\end{align*}"},
             {"page": 406, "tex": "&=2\n\\end{align*}"}]
    end = scan_environments(pages)["unmatched"][0]
    node = end | {"kind": "unmatched_end"}
    with pytest.raises(WorkflowError) as error:
        verify(node, pages, pages, action(resolved=True, closing_page=closing_page), {405, 406})
    message = error.value.message
    assert "第 406 页第 2 行" in message and r"\end{align*}" in message
    assert "第 405 页第 1 行" in message and "第 405 页第 3 行" in message
    assert "提前闭合" in message and "仅填写 closing_page" in message
    fixed = action(edits=[{"page": 405, "old": r"\end{align*}", "new": ""}],
                   resolved=True, closing_page=406)
    after = apply_edits(pages, fixed["edits"], {405, 406})
    verify(node, pages, after, fixed, {405, 406})


@pytest.mark.parametrize("closing_page,seen,expected", [(405, {405, 406}, "closing_page=405"),
                                                       (406, {405}, "尚未查看第 406 页")])
def test_paired_end_feedback_separates_wrong_page_from_unseen_page(closing_page, seen, expected):
    from bookanalyst.seam_environments import verify

    pages = [{"page": 405, "tex": r"\begin{proof}\begin{align*}x"},
             {"page": 406, "tex": r"\end{proof}"}]
    node = scan_environments(pages)["unmatched"][0] | {"kind": "unmatched_end"}
    with pytest.raises(WorkflowError) as error:
        verify(node, pages, pages, action(resolved=True, closing_page=closing_page), seen)
    assert expected in error.value.message
    assert "仍没有可配对" not in error.value.message


@pytest.mark.asyncio
async def test_precise_end_feedback_reaches_worker_and_fix_passes(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches("\\begin{align*}\nx=1\n\\end{align*}", "&=2\n\\end{align*}")
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if len(calls) == 1:
            return action(resolved=True, closing_page=2, note="Claimed already closed.")
        feedback = payload["repair_feedback"]["error"]
        assert "第 1 页第 3 行" in feedback and "提前闭合" in feedback
        return action(edits=[{"page": 1, "old": r"\end{align*}", "new": ""}],
                      resolved=True, closing_page=2)

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert len(calls) == 2
    assert not scan_environments([p for r in joined for p in r["pages"]])["unmatched"]


@pytest.mark.asyncio
async def test_resolved_with_inspected_pages_gets_actionable_feedback_before_retry(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    source = batches(r"\begin{proof}Proof", "ends.")
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        fixed = action(edits=[{"page": 2, "old": "ends.", "new": r"ends.\end{proof}"}],
                       resolved=True, closing_page=2)
        if len(calls) == 1:
            return fixed | {"read_pages": [1, 2]}
        assert len(calls) == 2
        message = payload["repair_feedback"]["error"]
        assert "read_pages=[]" in message and "resolved=false" in message
        assert "尚未应用" in message
        assert payload["pages"][1]["tex"] == "ends."
        return fixed

    monkeypatch.setattr(engine.providers, "generate", generate)
    joined = await resolve_environments(engine, run, source)
    assert len(calls) == 2
    assert joined[1]["pages"][0]["tex"] == r"ends.\end{proof}"
    assert all(t["state"] == "PASSED" for t in engine.store.tasks(run["id"], "seams"))


@pytest.mark.asyncio
async def test_completed_worker_is_waiting_while_earlier_worker_is_active(app, monkeypatch):
    engine, run = app.state.engine, make_run(app, llm_concurrency=2)
    source = batches(r"\begin{lemma}A", "ends.", r"\begin{theorem}B", "ends.")
    waiting = asyncio.Event()
    original_task = engine.store.task

    def task(rid, tid, stage, state, pages, error=None):
        original_task(rid, tid, stage, state, pages, error)
        if state == "WAITING_MERGE" and pages == [3, 4]:
            waiting.set()

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        node = payload["todo"]
        if node["begin_page"] == 1:
            await asyncio.wait_for(waiting.wait(), 2)
            states = {t["pages"][0]: t["state"] for t in engine.store.tasks(run["id"], "seams")}
            assert states == {1: "RUNNING", 3: "WAITING_MERGE"}
        page = node["begin_page"] + 1
        return action(edits=[{"page": page, "old": "ends.", "new": "ends.\\end{" + node["name"] + "}"}],
                      resolved=True, closing_page=page)

    monkeypatch.setattr(engine.store, "task", task)
    monkeypatch.setattr(engine.providers, "generate", generate)
    await resolve_environments(engine, run, source)
    assert all(t["state"] == "PASSED" for t in engine.store.tasks(run["id"], "seams"))
