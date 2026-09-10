"""Current files and compiler evidence govern the persistent Codex stage."""

import json
import pytest
from pypdf import PdfWriter
from bookanalyst.store import WorkflowError, atomic_json, atomic_text, digest
from test_rebuild import make_run, result


def pdf(directory):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(directory / "main.pdf")


def data():
    value = result([1]) | {"task_id": "batch-0001"}
    value["pages"][0]["tex"] = "BAD original."
    return value


@pytest.mark.asyncio
async def test_compilation_success_needs_no_agent_or_old_acceptance(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    base = engine.store.directory(run["id"])
    atomic_json(base / "finish-project.json", {"phase": "acceptance", "agent_accepted": False})
    async def compiler(directory):
        pdf(directory)
        return {"status": "PASSED"}
    async def forbidden(*args):
        pytest.fail("No model needed when the current files compile")
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    monkeypatch.setattr("bookanalyst.engine.repair_project", forbidden)
    await engine.compile(run, [data()], [], {"documentclass": "article"})
    assert json.loads((base / "finish-report.json").read_text())["accepted_by"] == "compiler"
    assert (base / "final-pages/1.json").exists()
    assert json.loads((base / "finish-project.json").read_text())["phase"] == "acceptance"


@pytest.mark.asyncio
async def test_agent_claim_is_rechecked_and_repairs_continue_past_ten(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    calls = []
    async def compiler(directory):
        if "FIXED" in (directory / "body.tex").read_text():
            pdf(directory)
            return {"status": "PASSED"}
        return {"status": "FAILED", "code": "REFERENCE_ERROR", "log": "Citation A undefined"}
    async def agent(providers, current, project, report):
        calls.append(report)
        assert report["log"] == "Citation A undefined"
        if len(calls) == 11:
            atomic_text(project / "body.tex", "% PDF page 1\nFIXED")
        return "Compilation succeeded"  # Claims never determine acceptance.
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    monkeypatch.setattr("bookanalyst.engine.repair_project", agent)
    await engine.compile(run, [data()], [], {"documentclass": "article"})
    assert len(calls) == 11
    base = engine.store.directory(run["id"])
    assert json.loads((base / "final-pages/1.json").read_text())["tex"] == "FIXED"


@pytest.mark.asyncio
async def test_pause_resume_preserves_direct_edits_instead_of_old_snapshot(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    base = engine.store.directory(run["id"])
    calls = []
    async def compiler(directory):
        if "FIXED" in (directory / "body.tex").read_text():
            pdf(directory)
            return {"status": "PASSED"}
        return {"status": "FAILED", "code": "TEX_ERROR"}
    async def agent(providers, current, project, report):
        body = (project / "body.tex").read_text()
        calls.append(body)
        if len(calls) == 1:
            atomic_text(project / "body.tex", body + "\nPartial edit")
            raise WorkflowError("PAUSED", "Paused")
        assert "Partial edit" in body
        atomic_text(project / "body.tex", body.replace("BAD original.", "FIXED"))
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    monkeypatch.setattr("bookanalyst.engine.repair_project", agent)
    with pytest.raises(WorkflowError, match="Paused"):
        await engine.compile(run, [data()], [], {"documentclass": "article"})
    atomic_json(base / "finish-project.json", {"files": {"body.tex": "STALE"}})
    await engine.compile(run, [data()], [], {"documentclass": "article"})
    assert len(calls) == 2
    assert "Partial edit" in (base / "tex/body.tex").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_pdf", "changed_pdf", "changed_project"])
async def test_resume_always_checks_actual_files(app, monkeypatch, change):
    engine, run = app.state.engine, make_run(app)
    builds = []
    async def compiler(directory):
        builds.append((directory / "body.tex").read_text())
        pdf(directory)
        return {"status": "PASSED"}
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    await engine.compile(run, [data()], [], {"documentclass": "article"})
    project = engine.store.directory(run["id"]) / "tex"
    if change == "missing_pdf":
        (project / "main.pdf").unlink()
    elif change == "changed_pdf":
        (project / "main.pdf").write_bytes(b"broken pdf")
    else:
        atomic_text(project / "body.tex", "% PDF page 1\nUser edit")
    await engine.compile(run, [data()], [], {"documentclass": "article"})
    assert len(builds) == 2
    if change == "changed_project":
        assert "User edit" in builds[-1]


@pytest.mark.asyncio
async def test_missing_pdf_is_not_accepted(app, monkeypatch):
    async def compiler(directory):
        return {"status": "PASSED"}
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    with pytest.raises(WorkflowError) as exc:
        await app.state.engine.compile(make_run(app), [data()], [], {"documentclass": "article"})
    assert exc.value.code == "COMPILE_OUTPUT_MISSING"


@pytest.mark.asyncio
async def test_legacy_saved_project_can_be_restored_once(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    base = engine.store.directory(run["id"])
    value, setup = [data()], {"documentclass": "article"}
    atomic_json(base / "finish-project.json", {
        "input_hash": digest({"results": value, "headings": [], "setup": setup}),
        "files": {"main.tex": "main", "body.tex": "% PDF page 1\nPrevious repair"},
    })
    async def compiler(directory):
        assert "Previous repair" in (directory / "body.tex").read_text()
        pdf(directory)
        return {"status": "PASSED"}
    monkeypatch.setattr("bookanalyst.engine.compile_tex", compiler)
    await engine.compile(run, value, [], setup)
