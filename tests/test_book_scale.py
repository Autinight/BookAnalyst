"""Book-scale orchestration acceptance with a declared synthetic semantic oracle."""
import asyncio
import json
import shutil
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from bookanalyst.block_planning import bounded_map, cluster_tasks
from bookanalyst.llm import Providers
from bookanalyst.models import RunCreate, Operation, Rerun
from bookanalyst.store import WorkflowError, atomic_json
from scale_oracle import BookOracle

BOOK_ID = "elliptic-pde-second-order"


@pytest.mark.asyncio
async def test_full_532_page_pipeline_resumes_analysis_and_conversion_without_repeating_passed_work(app):
    if not shutil.which("xelatex"):
        pytest.skip("Book-scale integration requires an actual XeLaTeX on PATH")
    started = time.perf_counter()
    store, engine = app.state.store, app.state.engine
    settings = store.get("settings", "main")
    settings["connections"]["custom_api"].update(enabled=True, auth_mode="none",
        base_url="http://synthetic-model.invalid/v1", model_id="semantic-oracle", max_in_flight=3)
    store.put("settings", "main", settings)
    oracle = BookOracle(532)
    oracle.store = store
    oracle.fail_structure_once = oracle.fail_converter_once = True
    engine.providers = Providers(store, app.state.workspace, transport=httpx.MockTransport(oracle.http))
    engine.mineru.parse = oracle.parse
    config = RunCreate(book_id=BOOK_ID, profile="book", start_page=1, end_page=532, visual_mode="disabled",
        llm_concurrency=4, max_llm_requests=10000, max_parse_submissions=3, max_submitted_pages=532).model_dump()
    for role in ("analyst", "converter", "reviewer", "visual_reviewer"):
        config[role].update(connection_id="custom_api", model_id="semantic-oracle")
    run = store.create_run(config, store.get("book", BOOK_ID), settings["mineru"])
    engine.start(run["id"], Operation(revision=1, operation_id="whole-book-start"))
    await engine.running[run["id"]]
    first = store.get("run", run["id"])
    assert first["stages"]["S3"]["state"] == "FAILED", first["stages"]
    assert oracle.structure_failed
    completed_structure = dict(oracle.structured)
    assert len(completed_structure) >= 12
    engine.rerun(run["id"], Rerun(revision=first["revision"], operation_id="resume-book-analysis",
                                 stage="S3", reason="Recover after a known synthetic service failure"))
    await engine.running[run["id"]]
    second = store.get("run", run["id"])
    assert second["stages"]["S6"]["state"] == "FAILED", second["stages"]
    assert oracle.failed
    for signature, count in completed_structure.items():
        assert oracle.structured[signature] == count, "Passed analysis was repeated"
    completed_conversion = dict(oracle.converted)
    assert len(completed_conversion) >= 12
    engine.rerun(run["id"], Rerun(revision=second["revision"], operation_id="resume-book-conversion",
                                 stage="S6", reason="Recover after a known synthetic service failure"))
    await engine.running[run["id"]]
    final = store.get("run", run["id"])
    assert final["state"] == "BOOK_ACCEPTED", final["stages"]
    assert final["revision"] == 3
    assert final["usage"]["parse"] == 3 and final["usage"]["pages"] == 532
    assert [p for pages in oracle.chunk_pages for p in pages] == list(range(532))
    assert oracle.peak <= 3
    for task_id, count in completed_conversion.items():
        assert oracle.converted[task_id] == count, "Passed conversion was repeated"

    atoms = store.artifact(final, "S2", "content.json")["atoms"]
    plan = store.artifact(final, "S3", "structure.json")
    analysis = store.artifact(final, "S3", "structure_review.json")
    tasks = store.artifact(final, "S5", "tasks.json")
    ledger = store.artifact(final, "S4", "numbering_ledger.json")
    assert len(atoms) == len(oracle.by_text)
    assert len([n for n in plan["nodes"] if n["kind"] == "chapter"]) == 19
    assert len([n for n in plan["nodes"] if n["kind"] == "proof"]) == 19
    assert len(ledger) == 533
    assert len(plan["toc"]) == 19 and plan["toc_mode"] == "original"
    assert len(plan["references"]) == 2
    referenced = {n["id"]: n["number"] for n in plan["nodes"]}
    assert {referenced[r["target_node_id"]] for r in plan["references"]} == {"1.1", "19.1"}
    body_ids = [aid for n in plan["nodes"] for aid in n["atom_ids"]]
    assert Counter(a for t in tasks["tasks"] for a in t["owned_atom_ids"]) == Counter(body_ids)
    assert any(n["kind"] == "proof" for b in tasks["boundaries"] for n in b["continuing_environments"])
    math_groups = [group for t in tasks["tasks"] for group in t["math_groups"] if len(group) > 1]
    assert len(math_groups) == 1 and len(math_groups[0]) == 2
    amap = {a["atom_id"]: a for a in atoms}
    assert [amap[aid]["page_idx"] for aid in math_groups[0]] == [199, 200]
    assert analysis["batch_count"] > 100
    assert len(tasks["tasks"]) > 100
    before_local = dict(oracle.converted)
    previous_usage = final["usage"]["llm"]
    local_task = tasks["tasks"][len(tasks["tasks"]) // 2]["task_id"]
    engine.rerun(run["id"], Rerun(revision=final["revision"], operation_id="rerun-single-semantic-task",
        stage="S6", task_id=local_task, reason="Verify exact local invalidation"))
    await engine.running[run["id"]]
    final = store.get("run", run["id"])
    assert final["state"] == "BOOK_ACCEPTED", final["stages"]
    assert final["usage"]["llm"] - previous_usage == 4  # conversion + review + two adjoining boundaries
    assert oracle.converted[local_task] == before_local[local_task] + 1
    assert all(oracle.converted[key] == count for key, count in before_local.items() if key != local_task)
    compile_report = store.artifact(final, "S7", "compile_report.json")
    assert compile_report["status"] == "PASSED"
    tex_dir = store.artifact_dir(final, "S7") / "tex"
    assert r"\tableofcontents" in (tex_dir / "main.tex").read_text(encoding="utf-8")
    source_map = json.loads((tex_dir / "source_map.json").read_text(encoding="utf-8"))
    assert all(source_map[aid]["file"] == "main.tex" for entry in plan["toc"] for aid in entry["atom_ids"])
    assert (store.directory(final["id"], final["revision"], "S7") / "candidate/tex/main.toc").stat().st_size > 0
    pdf = PdfReader(store.artifact_dir(final, "S7") / "tex/main.pdf")
    report = {
        "evidence_origin": "synthetic_semantic_oracle_not_real_book_conversion",
        "real_pdf_pages_split": 532, "parser_chunks": [len(p) for p in oracle.chunk_pages],
        "semantic_blocks": len(atoms), "chapters": 19, "toc_anchors": 19, "cross_book_references": 2, "cross_block_proofs": 19,
        "numbered_equations": len(ledger), "analysis_batches": analysis["batch_count"],
        "conversion_tasks": len(tasks["tasks"]), "peak_model_requests": oracle.peak,
        "model_requests_used": final["usage"]["llm"], "configured_request_budget": 10000, "actual_external_calls": 0,
        "resumed_revisions": final["revision"], "passed_work_repeated": False, "local_rerun_model_calls": 4,
        "cross_parser_boundary_formula_pages": [200, 201], "compiled_pages": len(pdf.pages),
        "seconds": round(time.perf_counter() - started, 2),
        "run_data": str(store.root / "runs" / run["id"]),
    }
    atomic_json(app.state.workspace / "tmp/book-scale/orchestration-report.json", report)
    print(json.dumps(report))
    await engine.close()


@pytest.mark.asyncio
async def test_bounded_queue_stops_dispatch_after_failure_with_thousands_of_pending_items():
    active = peak = 0
    started = []
    async def worker(index):
        nonlocal active, peak
        started.append(index)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.002)
            if index == 7:
                raise WorkflowError("SYNTHETIC_FAILURE", "stop dispatch")
            return index
        finally:
            active -= 1
    with pytest.raises(WorkflowError):
        await bounded_map(list(range(10000)), worker, 4)
    assert peak <= 4 and len(started) <= 11 and active == 0


def test_semantic_grouping_crosses_pages_and_does_not_cut_large_formulas():
    config = RunCreate(book_id=BOOK_ID).model_dump()
    for role in ("converter", "reviewer"):
        config[role].update(context_limit=12000, output_tokens=512)
    atoms = [{"atom_id": "a-first", "page_idx": 0, "type": "interline_equation", "text": r"\frac{x",
              "discarded_candidate": False},
             {"atom_id": "a-second", "page_idx": 1, "type": "interline_equation", "text": r"}{y}",
              "discarded_candidate": False}]
    plan = {"nodes": [{"id": "formula", "parent_id": None, "kind": "equation", "title": "",
                      "number": None, "atom_ids": ["a-first", "a-second"]}]}
    # Use a normal output budget for the complete two-block semantic object.
    for role in ("converter", "reviewer"):
        config[role].update(context_limit=32768, output_tokens=4096)
    tasks = cluster_tasks(atoms, plan, {"profile_hash": "profile"}, config)["tasks"]
    assert len(tasks) == 1 and tasks[0]["math_groups"] == [["a-first", "a-second"]]
    for a in atoms:
        a["text"] = "x" * 4000
    with pytest.raises(WorkflowError) as error:
        cluster_tasks(atoms, plan, {"profile_hash": "profile"}, config)
    assert error.value.code == "SEMANTIC_UNIT_TOO_LARGE"
