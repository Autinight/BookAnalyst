"""Program regressions; these checks do not claim live model accuracy."""
import asyncio
import copy

import pytest

from bookanalyst.hierarchy import resolve_anchor
from bookanalyst.models import RunCreate
from bookanalyst.semantics import check_math, control_character_findings
from bookanalyst.store import WorkflowError, digest
from bookanalyst.tex import inline, render


@pytest.mark.parametrize("prefix", ["(Theorem 2)", "Theorem 2.", "（定理 2）"])
def test_punctuation_cannot_resolve_duplicate_targets(prefix):
    nodes = [{"id": "a", "kind": "theorem", "number": "2", "title": "", "source_prefix": prefix},
             {"id": "b", "kind": "theorem", "number": "2", "title": "", "source_prefix": "Theorem 2."}]
    with pytest.raises(WorkflowError) as error:
        resolve_anchor({"kind": "theorem", "number": "2", "title": ""}, nodes)
    assert error.value.code == "ANCHOR_AMBIGUITY"


@pytest.mark.parametrize("control", ["\x00", "\x03", "\x0c", "\x1f", "\x7f", "\x85"])
def test_unverified_controls_cannot_be_silently_rendered(control):
    for renderer in (inline, check_math):
        with pytest.raises(WorkflowError) as error:
            renderer("x" + control)
        assert error.value.code == "UNRESOLVED_CONTROL_CHARACTER"
    assert not control_character_findings([{"atom_id": "a", "page_idx": 3, "text": "x\n\ty\r\n"}])


@pytest.mark.parametrize("text,prefix", [("Proof. Done.\x03", "Proof."), ("Proof.\x03 Done.", "Proof.\x03")])
def test_proof_renderer_cannot_hide_unknown_character_in_marker_or_prefix(text, prefix):
    atom = {"atom_id": "a", "type": "text", "text": text, "page_idx": 0, "bbox": [0, 0, 1, 1]}
    structure = {"nodes": [{"id": "p", "parent_id": None, "kind": "proof", "atom_ids": ["a"],
                            "title": "", "number": None, "source_prefix": prefix}]}
    profile = {"rules": {"counters": [], "exceptions": []}, "documentclass": "article"}
    with pytest.raises(WorkflowError) as error:
        render([atom], structure, profile, [{"nodes": [{"atom_id": "a", "kind": "source_ref"}]}])
    assert error.value.code == "UNRESOLVED_CONTROL_CHARACTER"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,repair", [("sampled", True), ("sampled", False), ("disabled", False)])
async def test_s2_routes_retained_anomalies_to_images_and_requires_resolution(app, monkeypatch, mode, repair):
    pipeline, store = app.state.engine.pipeline, app.state.store
    book_id = "elliptic-pde-second-order"
    config = RunCreate(book_id=book_id, profile="book", start_page=1, end_page=532,
                       visual_mode=mode, visual_pages=[], max_submitted_pages=532).model_dump()
    run = store.create_run(config, store.get("book", book_id), store.get("settings", "main")["mineru"])
    atoms = [{"atom_id": aid, "type": kind, "text": text, "page_idx": page, "bbox": [0, 0, 100, 100],
              "resources": [], "discarded_candidate": kind == "header"}
             for aid, kind, text, page in [("body", "text", "Section \x03", 2), ("head", "header", "\x03", 4)]]
    monkeypatch.setattr(pipeline, "normalized", lambda _: (copy.deepcopy(atoms), {}))
    monkeypatch.setattr(pipeline, "load", lambda *_: {"pages": list(range(532))})
    rendered, calls = [], []

    def fake_render(_path, page):
        rendered.append(page)
        data = b"fixture-image"
        return data, {"page_idx": page, "image_sha256": digest(data)}

    async def fake_policy(_run, role, purpose, payload, schema, semaphore):
        calls.append(purpose)
        assert purpose == "box_type_policy"
        assert {item["type"] for item in payload["inventory"]} == {"text", "header"}
        return {"types": [{"type": "text", "action": "KEEP", "reason": "Content"},
                          {"type": "header", "action": "EXCLUDE", "reason": "Running header"}]}

    async def fake_visual(_run, purpose, payload, schema, semaphore, images):
        calls.append(purpose)
        if purpose == "image_only_observation":
            return {"reading": "Section §", "regions": [], "unreadable": False}
        assert [a["atom_id"] for a in payload["parser_atoms"]] == ["body"]
        if purpose == "image_grounded_parser_repair":
            return {"patches": [{"atom_id": "body", "before": "\x03", "after": "§",
                                 "evidence": "Fixture image identifies a section sign"}], "findings": []}
        mismatch = repair and "\x03" in payload["parser_atoms"][0]["text"]
        return {"result": "MISMATCH" if mismatch else "MATCH", "source_ids": ["body"],
                "evidence": "Fixture comparison", "findings": []}

    monkeypatch.setattr("bookanalyst.pipeline.render_page", fake_render)
    monkeypatch.setattr(pipeline, "call", fake_policy)
    monkeypatch.setattr(pipeline, "visual_call", fake_visual)
    if repair:
        outputs = await pipeline.S2(run, asyncio.Semaphore(1))
        assert outputs["content.json"]["atoms"][0]["text"] == "Section §"
        plan = outputs["visual_review/plan.json"]
        assert plan["pages"] == [2]
        assert plan["automatic_findings"][0]["source_ids"] == ["body"]
        assert outputs["visual_review/index.json"]["reports"][0]["repairs"][0]["patches"][0]["after"] == "§"
    else:
        with pytest.raises(WorkflowError) as error:
            await pipeline.S2(run, asyncio.Semaphore(1))
        assert error.value.code == "UNRESOLVED_CONTROL_CHARACTER"
        assert store.get("run", run["id"])["findings"][0]["source_ids"] == ["body"]
    assert rendered == ([2] if mode != "disabled" else [])
    assert calls.count("box_type_policy") == 1
    assert atoms[0]["text"] == "Section \x03"
