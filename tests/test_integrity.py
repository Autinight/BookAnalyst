import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import zipfile

import pytest
from bookanalyst.config import DEFAULT_SETTINGS, validate_settings
from bookanalyst.models import RunCreate, Operation, Rerun
from bookanalyst.store import Store, WorkflowError
from bookanalyst.semantics import freeze_profile, check_math, validate_structure, require_review
from bookanalyst.mineru import unpack_result
from bookanalyst.pdf import split_pdf, inspect_pdf
from bookanalyst.tex import inline

BOOK_ID = "elliptic-pde-second-order"


def new_run(app, profile="cloud_smoke"):
    s=app.state.store
    return s.create_run(RunCreate(book_id=BOOK_ID,profile=profile,start_page=14,end_page=14).model_dump(),
                        s.get("book", BOOK_ID), copy.deepcopy(DEFAULT_SETTINGS["mineru"]))


def test_parallel_budget_is_reserved_atomically(app):
    run=new_run(app)
    app.state.store.change(run["id"],lambda r:r["config"].update(max_llm_requests=3))
    def reserve(_):
        try:
            return app.state.store.reserve(run["id"],1,"llm",1,{"role":"reviewer"})
        except WorkflowError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(reserve,range(20)))
    assert values.count("BUDGET_EXHAUSTED")==17
    assert app.state.store.get("run",run["id"])["usage"]["llm"]==3
    assert len(app.state.store.calls(run["id"]))==3


def test_phase_cannot_skip_preconditions(app):
    run=new_run(app)
    with pytest.raises(WorkflowError,match="前置阶段"):
        app.state.store.transition(run["id"],1,"S2","RUNNING")


def test_operation_idempotency_survives_repeated_command(app):
    run=new_run(app)
    def apply(r):
        r["usage"]["llm"]+=1
    a=app.state.store.operation(run["id"],1,"same-operation",apply)
    b=app.state.store.operation(run["id"],1,"same-operation",apply)
    assert a==b
    assert app.state.store.get("run",run["id"])["usage"]["llm"]==1


def test_stale_revision_never_overwrites(app):
    run=new_run(app)
    app.state.store.change(run["id"],lambda r:r.update(revision=2))
    with pytest.raises(WorkflowError,match="新修订"):
        app.state.store.change(run["id"],lambda r:r.update(state="PASSED"),revision=1)
    with pytest.raises(WorkflowError):
        app.state.store.reserve(run["id"],1,"llm",1,{})


def test_manifest_tamper_is_detected(app):
    s=app.state.store
    run=new_run(app)
    s.transition(run["id"],1,"S0","RUNNING")
    s.commit(run["id"],1,"S0",{"source.json":{"pages":[13]}} ,["G00"],{})
    run=s.get("run",run["id"])
    directory=s.artifact_dir(run,"S0")
    (directory/"source.json").write_text('{"pages":[0]}')
    with pytest.raises(WorkflowError,match="校验失败"):
        s.artifact_dir(run,"S0")


def test_recovery_preserves_usage_and_unknown_calls(app):
    s=app.state.store
    run=new_run(app)
    s.change(run["id"],lambda r:r.update(state="RUNNING"))
    s.transition(run["id"],1,"S0","RUNNING")
    s.reserve(run["id"],1,"llm",1,{})
    s.recover()
    restored=s.get("run",run["id"])
    assert restored["state"]=="NEEDS_REVIEW"
    assert restored["usage"]["llm"]==1
    assert s.calls(run["id"])[0]["state"]=="RESERVED"


def test_offline_profile_refuses_all_real_reservations(app):
    run=new_run(app,"offline_fixture")
    for kind in ("llm","parse"):
        with pytest.raises(WorkflowError,match="离线夹具"):
            app.state.store.reserve(run["id"],1,kind,1,{})


@pytest.mark.parametrize("value", [r"\input{secret}",r"\newcommand{\x}{y}",r"\vspace{1cm}",r"\tag{1}",r"\write18{calc}",r"\begin{document}x\end{document}",r"x{"])
def test_math_rejects_unregistered_commands(value):
    with pytest.raises(WorkflowError):
        check_math(value)


def test_inline_preserves_math_and_escapes_text():
    assert inline(r"A & B: \(x_1\), 10%.")==r"A \& B: \(x_1\), 10\%."


def test_review_must_cover_every_source():
    report={"decision":"PASS","source_ids":["a"],"evidence":"source image","findings":[]}
    with pytest.raises(WorkflowError):
        require_review(report,["a","b"])


def test_numbering_ambiguity_blocks_freeze():
    plan={"nodes":[{"id":"c","kind":"chapter","number":"1","atom_ids":["a"]},
                   {"id":"eq","kind":"equation","number":"1.1","atom_ids":["b"]}],
          "counter_candidates":[{"id":reset,"counters":[{"id":"equations","types":["equation"],
                                    "reset":reset,"prefix":"chapter"}],"exceptions":[]} for reset in ("book","chapter")]}
    with pytest.raises(WorkflowError,match="不能唯一"):
        freeze_profile(plan)


def test_observed_reset_disambiguates_counter():
    plan={"nodes":[{"id":"c1","kind":"chapter","number":"1","atom_ids":["a"]},
                   {"id":"e1","kind":"equation","number":"1.1","atom_ids":["b"]},
                   {"id":"c2","kind":"chapter","number":"2","atom_ids":["c"]},
                   {"id":"e2","kind":"equation","number":"2.1","atom_ids":["d"]}],
          "counter_candidates":[{"id":reset,"counters":[{"id":"equations","types":["equation"],
                                    "reset":reset,"prefix":"chapter"}],"exceptions":[]} for reset in ("book","chapter")]}
    profile, ledger=freeze_profile(plan)
    assert profile["rules"]["id"]=="chapter"
    assert [n["number"] for n in ledger]==["1.1","2.1"]


@pytest.mark.parametrize("name",["../escaped.json","/escaped.json","C:/escaped.json",r"..\escaped.json"])
def test_archive_rejects_path_traversal(tmp_path,name):
    data=io.BytesIO()
    with zipfile.ZipFile(data,"w") as archive:
        archive.writestr(name,"{}")
    with pytest.raises(WorkflowError):
        unpack_result(data.getvalue(),tmp_path/"output")
    assert not (tmp_path/"escaped.json").exists()


def test_actual_pdf_split_has_exact_page_ownership(workspace,tmp_path):
    path=workspace/"tests/data/books/elliptic-pde-second-order.pdf"
    chunks=split_pdf(path,[13,14,15],tmp_path,2,200*1024*1024)
    assert [p for c in chunks for p in c["pages"]]==[13,14,15]
    assert [inspect_pdf(tmp_path/c["path"])["page_count"] for c in chunks]==[2,1]
    assert all(c["size_bytes"]<=200*1024*1024 for c in chunks)


def test_settings_reject_plain_credentials():
    value=copy.deepcopy(DEFAULT_SETTINGS)
    value["connections"]["custom_api"]["api_key"]="not-a-real-key"
    with pytest.raises(WorkflowError):
        validate_settings(value)


def test_structure_duplicate_source_is_not_coverage():
    atoms=[{"atom_id":"a"},{"atom_id":"b"}]
    plan={"nodes":[{"id":"one","parent_id":None,"kind":"paragraph","atom_ids":["a","a"],"title":"","number":None}],
          "toc_mode":"body_reconstructed","toc":[],"references":[],"findings":[]}
    with pytest.raises(WorkflowError,match="唯一覆盖"):
        validate_structure(plan,atoms)
