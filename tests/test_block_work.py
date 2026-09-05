import asyncio
import copy
from collections import Counter

import pytest

from bookanalyst.hierarchy import merge_local, resolve_anchor
from bookanalyst.models import RunCreate
from bookanalyst.semantics import validate_structure
from bookanalyst.store import WorkflowError, digest

BOOK_ID = "elliptic-pde-second-order"


def test_local_continuation_cannot_target_a_nonadjacent_node():
    nodes = [
        {"id": "first", "parent_id": None, "kind": "paragraph", "atom_ids": ["a"], "title": "", "number": None},
        {"id": "last", "parent_id": None, "kind": "paragraph", "atom_ids": ["b"], "title": "", "number": None}]
    candidate = {"findings": [], "nodes": [], "toc": [], "references": [], "open_path": [],
                 "continuations": [{"node_id": "first", "atom_ids": ["c"], "evidence": "Unjustified distant continuation"}]}
    with pytest.raises(WorkflowError) as error:
        merge_local(candidate, [{"atom_id": "c"}], nodes, [])
    assert error.value.code == "INVALID_CONTINUATION"
    assert nodes[0]["atom_ids"] == ["a"]


def test_structure_cannot_reopen_an_already_closed_proof():
    nodes = [
        {"id": "chapter", "parent_id": None, "kind": "chapter", "atom_ids": ["a"], "title": "Chapter", "number": "1"},
        {"id": "proof", "parent_id": "chapter", "kind": "proof", "atom_ids": ["b"], "title": "", "number": None},
        {"id": "after", "parent_id": "chapter", "kind": "paragraph", "atom_ids": ["c"], "title": "", "number": None},
        {"id": "wrong", "parent_id": "proof", "kind": "paragraph", "atom_ids": ["d"], "title": "", "number": None}]
    plan = {"nodes": nodes, "findings": [], "toc": [], "toc_mode": "body_reconstructed", "references": []}
    with pytest.raises(WorkflowError) as error:
        validate_structure(plan, [{"atom_id": aid} for aid in "abcd"])
    assert error.value.code == "READING_ORDER"


def test_ambiguous_book_anchor_blocks_instead_of_choosing_first():
    nodes = [{"id": "one", "kind": "equation", "number": "1", "title": ""},
             {"id": "two", "kind": "equation", "number": "1", "title": ""}]
    with pytest.raises(WorkflowError) as error:
        resolve_anchor({"kind": "equation", "number": "1", "title": ""}, nodes)
    assert error.value.code == "ANCHOR_AMBIGUITY"


@pytest.mark.asyncio
async def test_visual_resume_reuses_independent_reading_and_only_rechecks_changed_content(app, monkeypatch):
    pipeline, store = app.state.engine.pipeline, app.state.store
    config = RunCreate(book_id=BOOK_ID, profile="book", start_page=1, end_page=532,
                       visual_mode="sampled", visual_pages=[14, 15], max_llm_requests=100,
                       max_submitted_pages=532).model_dump()
    run = store.create_run(config, store.get("book", BOOK_ID), store.get("settings", "main")["mineru"])
    atoms = [{"atom_id": "a-" + str(p), "page_idx": p, "type": "text", "text": "Source " + str(p),
              "bbox": [0, 0, 100, 100], "resources": [], "discarded_candidate": False} for p in (13, 14)]
    monkeypatch.setattr(pipeline, "normalized", lambda _: (copy.deepcopy(atoms), {}))
    monkeypatch.setattr(pipeline, "load", lambda _run, stage, name: (
        {"pages": list(range(532))} if stage == "S0" else {"chunks": [{"id": "all"}]}))
    def image(_source, page):
        data = f"synthetic image {page}".encode()
        return data, {"page_idx": page, "image_sha256": digest(data)}
    monkeypatch.setattr("bookanalyst.pipeline.render_page", image)
    seen = Counter()
    failure = True
    async def call(_run, role, purpose, payload, schema, semaphore, images):
        nonlocal failure
        page = int(images[0].stem.split("-")[-1])
        seen[purpose, page] += 1
        if purpose == "image_only_observation":
            assert isinstance(payload, str) and "Source " not in payload
            return {"reading": f"Independent image {page}", "regions": [], "unreadable": False}
        assert payload["independent_reading"]["reading"] == f"Independent image {page}"
        if page == 14 and failure:
            failure = False
            raise WorkflowError("REQUEST_FAILED", "Synthetic known server failure")
        return {"result": "MATCH", "source_ids": [a["atom_id"] for a in payload["parser_atoms"]],
                "evidence": "Synthetic comparison", "findings": []}
    monkeypatch.setattr(pipeline, "call", call)
    with pytest.raises(WorkflowError):
        await pipeline.S2(run, asyncio.Semaphore(2))
    run["revision"] = 2
    outputs = await pipeline.S2(run, asyncio.Semaphore(2))
    assert outputs["visual_review/index.json"]["full_pages"] == [13, 14]
    assert seen == Counter({("image_only_observation", 13): 1, ("image_only_observation", 14): 1,
                            ("image_and_parser_comparison", 13): 1, ("image_and_parser_comparison", 14): 2})
    run["revision"] = 3
    run["corrections"] = [{"atom_id": "a-13", "page_idx": 13, "before": "Source 13", "after": "Corrected source",
                          "evidence": "Source image basis"}]
    await pipeline.S2(run, asyncio.Semaphore(2))
    assert seen["image_only_observation", 13] == 1
    assert seen["image_and_parser_comparison", 13] == 2
    assert seen["image_and_parser_comparison", 14] == 2
    # A damaged checkpoint must be detected before any new service call.
    import json
    for path in (store.root / "runs" / run["id"] / "visual-work").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["output"].get("reading") == "Independent image 13":
            record["output"]["reading"] = "tampered"
            path.write_text(json.dumps(record), encoding="utf-8")
            break
    before = seen.copy()
    with pytest.raises(WorkflowError) as error:
        await pipeline.S2(run, asyncio.Semaphore(2))
    assert error.value.code == "HASH_MISMATCH"
    assert seen == before
