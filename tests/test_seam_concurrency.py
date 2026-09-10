"""Concurrent seam scheduling preserves ordered edits and durable progress."""

import asyncio
import copy
import json

import pytest

from bookanalyst.store import WorkflowError
from test_rebuild import make_run, result


def batches(count, size=2):
    return [
        result(list(range(i * size + 1, (i + 1) * size + 1)))
        | {"head": "paragraph", "tail": "paragraph", "task_id": f"batch-{i + 1:04d}"}
        for i in range(count)
    ]


def empty_patch():
    return {"left_suffix": "", "right_prefix": "", "replacement": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 6])
async def test_seams_reach_configured_concurrency_and_resume_without_requests(
    app, monkeypatch, concurrency
):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=concurrency)
    source = batches(9)
    original = copy.deepcopy(source)
    active = peak = calls = 0
    filled = asyncio.Event()

    async def ask(run, key, purpose, payload, schema, pages, validate):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == concurrency:
            filled.set()
        await asyncio.wait_for(filled.wait(), 2)
        await asyncio.sleep(0)
        active -= 1
        assert purpose == "seams" and len(pages) == 2
        patch = {"left_suffix": ".", "right_prefix": "", "replacement": "!"}
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    joined = await engine.seams(run, source)
    assert peak == concurrency and calls == 8 and active == 0
    assert source == original
    assert all(item["pages"][-1]["tex"].endswith("!") for item in joined[:-1])
    assert all(t["state"] == "PASSED" for t in engine.store.tasks(run["id"], "seams"))
    assert await engine.seams(run, source) == joined
    assert calls == 8


@pytest.mark.asyncio
async def test_one_page_batches_keep_both_junction_edits(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=6)
    source = batches(4, size=1)
    calls = []
    ready = asyncio.Event()

    async def ask(run, key, purpose, payload, schema, pages, validate):
        calls.append(key)
        if len(calls) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        # Complete later junctions first to exercise ordered merging.
        if key == "seam-0000":
            await asyncio.sleep(0.01)
        patch = {"left_suffix": ".", "right_prefix": "Original ", "replacement": "; "}
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    joined = await engine.seams(run, source)
    assert [r["pages"][0]["tex"] for r in joined] == [
        "Original page 1; ",
        "page 2; ",
        "page 3; ",
        "page 4.",
    ]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_remote_environment_change_does_not_refresh_later_seam(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    source = batches(3)
    source[-1]["pages"][-1]["tex"] += r"\end{proof}"
    calls = []
    ready = asyncio.Event()

    async def ask(run, key, purpose, payload, schema, pages, validate):
        calls.append((key, [e["name"] for e in payload["unclosed_environments"]]))
        if len(calls) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        patch = empty_patch()
        if key == "seam-0000":
            patch = {
                "left_suffix": ".",
                "right_prefix": "",
                "replacement": r".\begin{proof}",
            }
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    joined = await engine.seams(run, source)
    assert calls == [("seam-0000", []), ("seam-0001", [])]
    assert joined[0]["pages"][-1]["tex"].endswith(r"\begin{proof}")


@pytest.mark.asyncio
async def test_overlapping_patch_is_refreshed_against_updated_page(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    source = batches(3, size=1)
    ready = asyncio.Event()
    seen = []

    async def ask(run, key, purpose, payload, schema, pages, validate):
        seen.append((key, payload["left_tex"]))
        if len(seen) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if key == "seam-0000":
            patch = {
                "left_suffix": ".",
                "right_prefix": "Original ",
                "replacement": "; ",
            }
        else:
            patch = {
                "left_suffix": payload["left_tex"],
                "right_prefix": "",
                "replacement": payload["left_tex"] + " Updated.",
            }
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    joined = await engine.seams(run, source)
    assert seen == [
        ("seam-0000", "Original page 1."),
        ("seam-0001", "Original page 2."),
        ("seam-0001", "page 2."),
    ]
    assert joined[1]["pages"][0]["tex"] == "page 2. Updated."


@pytest.mark.asyncio
async def test_failure_drains_inflight_results_and_resume_uses_checkpoints(
    app, monkeypatch
):
    engine, store = app.state.engine, app.state.store
    run = make_run(app, llm_concurrency=3)
    source = batches(4)
    calls = []
    ready = asyncio.Event()
    fail = True

    async def no_image(*args):
        return None

    async def generate(run, role, purpose, prompt, schema, images):
        payload = json.loads(prompt)
        page = payload["page_images"][0]
        calls.append(page)
        if len(calls) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if page == 4 and fail:
            raise WorkflowError("AUTH_REQUIRED", "Test transport failure")
        return empty_patch()

    monkeypatch.setattr(engine, "image", no_image)
    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="Test transport"):
        await engine.seams(run, source)
    checkpoint = json.loads(
        (store.directory(run["id"]) / "seams-progress.json").read_text()
    )
    assert list(checkpoint["patches"]) == ["0"]
    assert not (store.directory(run["id"]) / "joined.json").exists()
    fail = False
    await engine.seams(run, source)
    assert calls.count(2) == 1 and calls.count(4) == 2 and calls.count(6) == 1
    assert all(t["state"] == "PASSED" for t in store.tasks(run["id"], "seams"))


@pytest.mark.asyncio
async def test_closed_flags_consult_only_the_left_batch_report(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    source = batches(3)
    for batch in source:
        batch.update(head="closed", tail="closed")
    source[0]["pages"][-1]["tex"] += r"\begin{proof}"
    source[-1]["pages"][-1]["tex"] += r"\end{proof}"
    seen = []

    async def ask(run, key, purpose, payload, schema, pages, validate):
        seen.append((key, [e["name"] for e in payload["unclosed_environments"]]))
        return empty_patch()

    monkeypatch.setattr(engine, "checked_ask", ask)
    await engine.seams(run, source)
    assert seen == [("seam-0000", ["proof"])]


@pytest.mark.asyncio
async def test_changed_local_batch_report_refreshes_its_seam(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=2)
    source = batches(3)
    source[1]["pages"][0]["tex"] = r"\begin{proof}" + source[1]["pages"][0]["tex"]
    source[2]["pages"][0]["tex"] = r"\end{proof}" + source[2]["pages"][0]["tex"]
    ready = asyncio.Event()
    seen = []

    async def ask(run, key, purpose, payload, schema, pages, validate):
        names = [e["name"] for e in payload["unclosed_environments"]]
        seen.append((key, names))
        if len(seen) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        patch = empty_patch()
        if key == "seam-0000":
            patch = dict(left_suffix="", right_prefix=r"\begin{proof}", replacement="")
        elif not names:
            patch = dict(left_suffix="", right_prefix=r"\end{proof}", replacement="")
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    await engine.seams(run, source)
    assert seen == [("seam-0000", []), ("seam-0001", ["proof"]), ("seam-0001", [])]


@pytest.mark.asyncio
async def test_page_50_seam_gets_only_pages_46_to_50_report(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app, llm_concurrency=4)
    source = batches(12, size=5)
    source[2]["pages"][-1]["tex"] = r"\begin{example*}Example."
    source[9]["pages"][2]["tex"] = r"\begin{lemma}Statement."
    source[10]["pages"][0]["tex"] = r"Continued.\end{lemma}"
    seen = []
    ready = asyncio.Event()

    async def ask(run, key, purpose, payload, schema, pages, validate):
        seen.append(key)
        if len(seen) == 4:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if key == "seam-0009":
            assert pages == [50, 51]
            assert payload["left_batch_pages"] == [46, 47, 48, 49, 50]
            assert payload["unclosed_environments"] == [
                dict(name="lemma", label="", begin_page=48, begin_line=1)
            ]
        patch = empty_patch()
        if key == "seam-0002":
            patch = dict(left_suffix=".", right_prefix="", replacement=r".\end{example*}")
        validate(patch)
        return patch

    monkeypatch.setattr(engine, "checked_ask", ask)
    await engine.seams(run, source)
    assert len(seen) == len(set(seen)) == 11
