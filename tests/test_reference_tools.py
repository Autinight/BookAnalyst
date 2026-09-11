"""Book queries expand read evidence while preserving each worker's write boundary."""

import json

import pytest
from PIL import Image

from bookanalyst.documents import ReferenceAction
from bookanalyst.numbering import collect_symbols, symbol_problems
from bookanalyst.reference_tools import ReferenceLibrary, BIBLIOGRAPHY_INSTRUCTION, REFERENCE_CONFIRMATION_INSTRUCTION
from bookanalyst.reference_repair import ESCALATION_INSTRUCTION
from bookanalyst.store import WorkflowError, atomic_json, digest
from test_numbering import batch
from test_rebuild import make_run


def action(**kwargs):
    return {"label_edits": [], "reference_edits": [], "search": [],
            "read_context": [], "view_pages": []} | kwargs


def search(query, scope="labels", offset=0, limit=10):
    return {"query": query, "scope": scope, "offset": offset, "limit": limit}


def source():
    return [
        batch(1, r"See \ref{bibliography:aaaaaaaaaaaaaaaaaaaa}."),
        batch(2, "Named author\n" + r"\label{bibliography:zzzzzzzzzzzzzzzzzzzz} Important result."),
    ]


@pytest.mark.asyncio
async def test_queries_paginate_body_and_labels_and_include_context_targets(app):
    run = make_run(app)
    results = source()
    library = ReferenceLibrary(run["source"], results, collect_symbols(results, []))
    reply = await library.query(action(search=[search("bibliography", limit=1), search("result", "body")]))
    assert reply["search_results"][0]["matches"][0]["id"] == "p2-label-0"
    assert reply["search_results"][1]["matches"][0]["line"] == 2
    assert "start" not in reply["discovered_targets"][0]
    reply = await library.query(action(read_context=[{"page": 2, "start_line": 1, "end_line": 0}]))
    assert "Named author" in reply["contexts"][0]["text"]
    assert reply["discovered_targets"][0]["id"] == "p2-label-0"
    reply = await library.query(action(search=[search("bibliography", "body", limit=1)]))
    assert reply["search_results"][0]["next_offset"] == 1
    reply = await library.query(action(search=[search("bibliography", "body", offset=1, limit=1)]))
    assert reply["search_results"][0]["matches"][0]["page"] == 2
    assert reply["search_results"][0]["next_offset"] is None


@pytest.mark.asyncio
async def test_original_search_covers_pages_without_converted_text(app):
    run = make_run(app)
    results = source()
    library = ReferenceLibrary(run["source"], results, collect_symbols(results, []))
    library.source_text = {1: "Introduction", 5: "Schauder fixed point theorem"}
    reply = await library.query(action(search=[search("SCHAUDER", "source")], view_pages=[5]))
    assert reply["search_results"][0]["matches"][0]["page"] == 5
    assert reply["view_pages"] == [5]
    with pytest.raises(WorkflowError, match="没有转换正文"):
        library.validate(action(read_context=[{"page": 5, "start_line": 1, "end_line": 0}]))
    with pytest.raises(WorkflowError, match="超出"):
        library.validate(action(view_pages=[run["source"]["page_count"] + 1]))


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_checkpoint", [False, "uncapped", "capped", "pre_bibliography"])
async def test_worker_searches_reads_views_and_resumes_after_interruption(app, monkeypatch, tmp_path, legacy_checkpoint):
    engine = app.state.engine
    run = make_run(app)
    results = source()
    calls, images_seen, initial_payloads = [], [], []
    png = tmp_path / "evidence.png"
    Image.new("RGB", (8, 8), "white").save(png)

    async def image(book, page, dpi=150):
        images_seen.append(page)
        return png

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload["tool_round"])
        assert {"search", "read_context", "view_pages"} <= set(schema["required"])
        if payload["tool_round"] == 0:
            initial_payloads.append(payload)
            assert not payload["targets"] and not images
            return action(search=[search("zzzz")])
        assert payload["targets"][0]["id"] == "p2-label-0"
        if payload["tool_round"] == 1:
            assert payload["tool_history"][0]["result"]["search_results"][0]["total"] == 1
            if calls.count(1) == 1:
                raise WorkflowError("REPAIR_LIMIT", "Pause after search")
            return action(read_context=[{"page": 2, "start_line": 1, "end_line": 0}], view_pages=[2, 5])
        assert payload["tool_round"] == 2 and len(images) == 2
        assert payload["page_images"] == [2, 5]
        assert "Named author" in payload["tool_history"][1]["result"]["contexts"][0]["text"]
        return action(reference_edits=[{"id": "p1-reference-0", "key": "bibliography:zzzzzzzzzzzzzzzzzzzz"}])

    monkeypatch.setattr(engine, "image", image)
    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError, match="Pause after search"):
        await engine.references(run, results, [], {})
    def mark_legacy_checkpoints():
        payload = {k: v for k, v in initial_payloads[0].items()
                   if k not in {"tool_round", "tool_history", "page_images"}}
        payload["instruction"] = payload["instruction"].removeprefix(REFERENCE_CONFIRMATION_INSTRUCTION).replace(
            "until this group is resolved or explicitly marked for confirmation", "until this group is resolved"
        ).removeprefix(ESCALATION_INSTRUCTION)
        if legacy_checkpoint == "pre_bibliography":
            payload.pop("bibliography_entry_pages")
            payload.pop("tail_pages")
            payload["instruction"] = payload["instruction"].removeprefix(BIBLIOGRAPHY_INSTRUCTION).replace(
                "until this group is resolved or explicitly marked for bibliography confirmation", "until this group is resolved")
        payload["instruction"] = payload["instruction"].replace(
            "Disambiguate duplicate labels as kind:number:scope; preserve kind:number. Example: equation:9.70:problems. ",
            "For duplicate labels preserve the original kind and source number, adding a semantic chapter/section suffix where needed. ",
        )
        if legacy_checkpoint == "capped":
            payload["instruction"] = payload["instruction"].replace(
                "Continue focused queries and repairs until this group is resolved; combine related reads. There is no fixed round limit. ",
                "You can make at most ten continuation/repair requests per automatic run; use focused queries and combine related reads. ",
            )
        for path in (engine.store.directory(run["id"]) / "reference-groups").glob("*.json"):
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved["input_hash"] = digest(payload)
            if legacy_checkpoint == "pre_bibliography" and "result" in saved:
                saved["result"].pop("unconfirmed_bibliography", None)
            atomic_json(path, saved)

    if legacy_checkpoint:
        mark_legacy_checkpoints()
    _, index = await engine.references(run, results, [], {})
    assert calls == [0, 1, 1, 2]
    assert images_seen == [2, 5]
    assert not symbol_problems(index)["unresolved_references"]
    if legacy_checkpoint:
        mark_legacy_checkpoints()
    await engine.references(run, results, [], {})
    assert calls == [0, 1, 1, 2]


@pytest.mark.asyncio
async def test_search_cannot_expand_write_permissions(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if payload["tool_round"] == 0:
            return action(search=[search("zzzz")])
        if "repair_feedback" not in payload:
            return action(label_edits=[{"id": "p2-label-0", "key": "bibliography:zzzzzzzzzzzzzzzzzzzz:renamed"}])
        assert payload["repair_feedback"]["code"] == "SYMBOL_EDIT"
        return action(reference_edits=[{"id": "p1-reference-0", "key": "bibliography:zzzzzzzzzzzzzzzzzzzz"}])

    monkeypatch.setattr(engine.providers, "generate", generate)
    fixed, _ = await engine.references(run, source(), [], {})
    assert len(calls) == 3 and fixed[1] == source()[1]


@pytest.mark.asyncio
async def test_tool_continuations_exceed_ten_and_ignore_exhausted_counter(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload["tool_round"])
        assert "at most ten" not in payload["instruction"]
        if payload["tool_round"] == 0:
            atomic_json(engine.store.directory(run["id"]) / "repair-attempts" / f"{run['request_task_id']}.json",
                        {"epoch": run.get("repair_epoch", 0), "attempts": 10})
        if payload["tool_round"] < 12:
            return action(search=[search("zzzz")])
        return action(reference_edits=[{"id": "p1-reference-0", "key": "bibliography:zzzzzzzzzzzzzzzzzzzz"}])

    monkeypatch.setattr(engine.providers, "generate", generate)
    _, index = await engine.references(run, source(), [], {})
    assert not symbol_problems(index)["unresolved_references"]
    assert calls == list(range(13))
    assert set(ReferenceAction.model_fields) == {
        "label_edits", "reference_edits", "unconfirmed_bibliography", "unconfirmed_references", "repair_requests", "search", "read_context", "view_pages",
    }


@pytest.mark.asyncio
async def test_validation_repairs_stop_after_six(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if payload["tool_round"] == 0:
            return action(search=[search("zzzz")])
        return action()  # Every repair is validly shaped but leaves the reference unresolved.

    monkeypatch.setattr(engine.providers, "generate", generate)
    with pytest.raises(WorkflowError) as error:
        await engine.references(run, source(), [], {})
    assert error.value.code == "REPAIR_LIMIT"
    assert "6" in error.value.message
    assert len(calls) == 8  # Initial query, one tool round, and six repair requests.


@pytest.mark.asyncio
async def test_concurrent_source_queries_share_one_pdf_extraction(app, monkeypatch):
    import asyncio
    import pypdf

    run = make_run(app)
    results = source()
    library = ReferenceLibrary(run["source"], results, collect_symbols(results, []))
    calls = []

    class Page:
        def extract_text(self):
            return "Searchable source evidence"

    class Reader:
        def __init__(self, path):
            calls.append(path)
            self.pages = [Page(), Page()]

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    replies = await asyncio.gather(*(
        library.query(action(search=[search("evidence", "source")])) for _ in range(4)
    ))
    assert len(calls) == 1
    assert all(r["search_results"][0]["total"] == 2 for r in replies)


@pytest.mark.asyncio
async def test_pdf_text_failure_returns_actionable_tool_result(app, monkeypatch):
    run = make_run(app)
    results = source()
    library = ReferenceLibrary(run["source"], results, collect_symbols(results, []))

    async def unavailable():
        raise ValueError("Broken text layer")

    monkeypatch.setattr(library, "original_text", unavailable)
    reply = await library.query(action(search=[search("anything", "source")]))
    assert "view_pages" in reply["search_results"][0]["error"]
