"""Opt-in Agent structure edits must not change the saved original or source body."""
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookanalyst.library_outputs import retain_run, retained_directory
from bookanalyst.store import WorkflowError, atomic_text
from bookanalyst.tex_structure import StructureCreate, create_run, validate_structure
from bookanalyst.project_body import read_project_body
from bookanalyst.tex import compile_tex
from test_library import headers
from test_library_outputs import completed
from test_rebuild import BOOK
from bookanalyst.templates import TemplateSpec, TemplateApply, create_template_run, save_template


BODY = b"% PDF page 1\nIntro\n\\BAHeading{chapter-1}\nChapter intro\n\\BAHeading{section-1}\nFirst section\n% PDF page 2\nEnd.\n"


def test_structure_book_select_does_not_trigger_conversion_book_handler():
    static = Path(__file__).resolve().parents[1] / "src/bookanalyst/static"
    structure = (static / "tex-structure.js").read_text(encoding="utf-8")
    app = (static / "app.js").read_text(encoding="utf-8")
    assert 'name="structure_book_id"' in structure
    assert "form.elements.structure_book_id.value" in structure
    assert 'event.target.name === "book_id" && event.target.closest("#run-form")' in app


def setup_run(app):
    store = app.state.store
    source, project = completed(app, BODY.decode())
    atomic_text(project / "main.tex", r"\documentclass{book}\begin{document}\input{body}\end{document}")
    result = retain_run(store, source["id"])
    command = StructureCreate(result_id=result["id"], operation_id="tex-structure-test-1")
    run = create_run(app.state.engine, BOOK, command)
    return run, command, result


def write_valid(project):
    (project / "chapters/01/sections").mkdir(parents=True, exist_ok=True)
    (project / "chapters/01/intro.tex").write_bytes(BODY[:BODY.index(b"\\BAHeading{section-1}")])
    (project / "chapters/01/sections/01.tex").write_bytes(BODY[BODY.index(b"\\BAHeading{section-1}"):])
    atomic_text(project / "body.tex", "\\input{chapters/01/intro}\n\\input{chapters/01/sections/01}\n")


def test_structure_task_is_idempotent_and_retained_output_untouched(app):
    run, command, result = setup_run(app)
    store = app.state.store
    assert create_run(app.state.engine, BOOK, command)["id"] == run["id"]
    assert run["kind"] == "tex_structure" and run["stages"]["finish"] == "PENDING"
    assert store.get("book", BOOK)["retained_result"] == result
    project = store.directory(run["id"]) / "tex"
    snapshot = json.loads((project.parent / "structure-source.json").read_text())
    assert base64.b64decode(snapshot["body_base64"]) == BODY
    assert (project / "body.tex").read_bytes() == retained_directory(store, store.get("book", BOOK)).joinpath("body.tex").read_bytes()
    with TestClient(app) as client:
        auth = headers(client)
        assert client.post(f"/api/books/{BOOK}/organize-tex", json=command.model_dump(), headers=auth).json()["id"] == run["id"]
        assert client.get(f"/api/runs/{run['id']}/structured-export").status_code != 200
    write_valid(project)
    assert validate_structure(project, snapshot) == 2
    assert read_project_body(project).encode() == BODY
    assert (project / "body.tex").read_bytes() != BODY
    assert retained_directory(store, store.get("book", BOOK)).joinpath("body.tex").read_bytes() == BODY


@pytest.mark.parametrize("mutation,code", [
    ("missing", "STRUCTURE_CONTENT"),
    ("preamble", "STRUCTURE_CHANGED"),
    ("extra", "STRUCTURE_FILES"),
    ("path", "STRUCTURE_MANIFEST"),
    ("boundary", "STRUCTURE_BOUNDARY"),
])
def test_validator_rejects_mutations(app, mutation, code):
    run, _, _ = setup_run(app)
    project = app.state.store.directory(run["id"]) / "tex"
    snapshot = json.loads((project.parent / "structure-source.json").read_text())
    write_valid(project)
    if mutation == "missing":
        (project / "chapters/01/sections/01.tex").write_bytes(b"Different\n")
    elif mutation == "preamble":
        atomic_text(project / "main.tex", "Changed")
    elif mutation == "extra":
        atomic_text(project / "unlisted.tex", "Something")
    elif mutation == "path":
        atomic_text(project / "body.tex", "\\input{chapters/01/intro} and words\n\\input{chapters/01/sections/01}\n")
    elif mutation == "boundary":
        split = BODY.index(b"\\BAHeading{section-1}") + 5
        (project / "chapters/01/intro.tex").write_bytes(BODY[:split])
        (project / "chapters/01/sections/01.tex").write_bytes(BODY[split:])
    with pytest.raises(WorkflowError) as err:
        validate_structure(project, snapshot)
    assert err.value.code == code


@pytest.mark.asyncio
async def test_real_xelatex_compiles_nested_sections(tmp_path):
    project = tmp_path / "project"
    (project / "chapters/01/sections").mkdir(parents=True)
    (project / "main.tex").write_text(
        "\\documentclass{book}\n\\begin{document}\n\\input{body}\n\\end{document}\n", encoding="utf-8"
    )
    (project / "body.tex").write_text("\\input{chapters/01/sections/01}\n", encoding="utf-8")
    (project / "chapters/01/sections/01.tex").write_text(
        "\\chapter{First}\\section{One}\nSome text.\n", encoding="utf-8"
    )
    report = await compile_tex(project)
    if report.get("code") == "DEPENDENCY_MISSING":
        pytest.skip("XeLaTeX unavailable")
    assert report["status"] == "PASSED", report


@pytest.mark.asyncio
async def test_structure_finish_compiles_and_downloads_only_after_validation(app, monkeypatch):
    run, _, result = setup_run(app)
    store = app.state.store
    project = store.directory(run["id"]) / "tex"
    async def agent(providers, current, path, feedback):
        assert current["kind"] == "tex_structure"
        assert feedback["status"] == "STRUCTURE_REQUESTED"
        write_valid(path)
    async def compiler(path):
        assert path == project
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path / "main.pdf")
        return {"status": "PASSED", "passes": 1}
    monkeypatch.setattr("bookanalyst.finisher.repair_project", agent)
    monkeypatch.setattr("bookanalyst.tex_structure.compile_tex", compiler)
    await app.state.engine.start(run["id"], type("Cmd", (), {"revision": 1, "operation_id": "start-structure-test", "retry_unknown": False})())
    await app.state.engine.running[run["id"]]
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert store.get("book", BOOK)["retained_result"] == result
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run['id']}/structured-export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert z.read("chapters/01/sections/01.tex") == BODY[BODY.index(b"\\BAHeading{section-1}"):]
        assert client.get(f"/api/runs/{run['id']}/pdf").status_code == 200
        preview = client.get(f"/api/pdf-preview/run/{run['id']}")
        assert preview.status_code == 200 and preview.json()["page_count"] == 1
        saved = client.post(f"/api/runs/{run['id']}/retain", headers=headers(client))
        assert saved.status_code == 200, saved.text
        assert saved.json()["run_id"] == run["id"]
        assert client.post(f"/api/runs/{run['id']}/retain", headers=headers(client)).status_code == 200
    assert store.get("book", BOOK)["retained_result"]["run_id"] == run["id"]
    retained = retained_directory(store, store.get("book", BOOK))
    assert read_project_body(retained).encode() == BODY
    assert (retained / "chapters/01/sections/01.tex").is_file()
    assert (retained.parent / result["id"] / "body.tex").read_bytes() == BODY
    template = save_template(store, TemplateSpec(name="Example", entrypoint="main.tex", files=[
        {"path": "main.tex", "content": "\\documentclass{book}\\begin{document}Sample\\end{document}"}]))
    task = create_template_run(app.state.engine, BOOK, TemplateApply(
        result_id=saved.json()["id"], template_id=template["id"], operation_id="structure-template-follow-up"))
    assert store.directory(task["id"]).joinpath("structured.json").is_file()
    assert read_project_body(store.directory(task["id"]) / "tex").encode() == BODY
