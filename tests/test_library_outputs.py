"""Successful outputs persist with their book, independently of transient run files."""
from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from bookanalyst.library_outputs import retain_run, retained_directory, book_directory
from bookanalyst.store import WorkflowError, atomic_text, atomic_json, digest
from test_library import headers
from test_rebuild import BOOK, make_run


def completed(app, text="Saved text"):
    run = make_run(app)
    project = app.state.store.directory(run["id"]) / "tex"
    atomic_text(project / "main.tex", r"\input{body.tex}")
    atomic_text(project / "body.tex", text)
    atomic_text(project / "custom.sty", "% local package")
    atomic_text(project / "assets/data.csv", "1,2,3")
    atomic_text(project / "pi-agent-session.json", '{"transcript":"not an output"}')
    atomic_text(project / "main.log", "temporary log")
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=100)
    pdf.write(project / "main.pdf")
    app.state.store.change(run["id"], lambda r: r.update(state="COMPLETED"))
    return run, project


def test_retained_output_is_independent_and_source_stays_available(app):
    store = app.state.store
    run, project = completed(app)
    original = Path(store.get("book", BOOK)["path"])
    original_bytes = original.read_bytes()
    generated = (project / "main.pdf").read_bytes()
    result = retain_run(store, run["id"])
    saved = retained_directory(store, store.get("book", BOOK))
    assert saved.parent.parent == book_directory(store, BOOK)
    assert saved.parent.parent / "source.pdf" == Path(store.get("book", BOOK)["path"])
    assert original.read_bytes() == original_bytes
    (project / "main.pdf").unlink()
    (project / "body.tex").write_text("New in-progress work")
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    with TestClient(app) as client:
        book = next(b for b in client.get("/api/bootstrap").json()["books"] if b["id"] == BOOK)
        assert book["result"]["run_id"] == run["id"]
        assert "path" not in book["result"]
        assert client.get(f"/api/books/{BOOK}/pdf").content == generated
        assert client.get(f"/api/books/{BOOK}/source").content == original_bytes
        response = client.get(f"/api/books/{BOOK}/project")
        assert response.status_code == 200
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert archive.read("body.tex") == b"Saved text"
            assert {"custom.sty", "assets/data.csv", "main.pdf"} <= set(archive.namelist())
            assert "pi-agent-session.json" not in archive.namelist() and "main.log" not in archive.namelist()
        auth = headers(client)
        client.delete(f"/api/books/{BOOK}", headers=auth)
        assert client.get(f"/api/books/{BOOK}/pdf").content == generated
        client.post(f"/api/books/{BOOK}/restore", headers=auth)
        assert client.get(f"/api/books/{BOOK}/project").status_code == 200


def test_completed_output_retain_is_idempotent_and_new_results_preserve_old_versions(app):
    store = app.state.store
    run, project = completed(app)
    first = retain_run(store, run["id"])
    original_snapshot = retained_directory(store, store.get("book", BOOK))
    assert retain_run(store, run["id"]) == first
    later, later_project = completed(app, "New version")
    second = retain_run(store, later["id"])
    assert first["id"] != second["id"]
    assert original_snapshot.joinpath("body.tex").read_text() == "Saved text"
    assert retained_directory(store, store.get("book", BOOK)).joinpath("body.tex").read_text() == "New version"
    assert len(list(original_snapshot.parent.iterdir())) == 2


def test_retention_requires_success_and_token_and_never_replaces_good_default_on_failure(app):
    store = app.state.store
    good, _ = completed(app)
    before = retain_run(store, good["id"])
    bad, project = completed(app)
    (project / "main.pdf").unlink()
    with TestClient(app) as client:
        assert client.post(f"/api/runs/{good['id']}/retain").status_code == 403
        auth = headers(client)
        assert client.post(f"/api/runs/{good['id']}/retain", headers=auth).status_code == 200
        assert client.post(f"/api/runs/{bad['id']}/retain", headers=auth).json()["code"] == "NO_OUTPUT"
        store.change(bad["id"], lambda r: r.update(state="RUNNING"))
        assert client.post(f"/api/runs/{bad['id']}/retain", headers=auth).json()["code"] == "NOT_COMPLETE"
        assert store.get("book", BOOK)["retained_result"] == before


def test_default_is_original_until_success_and_missing_archive_is_reported(app):
    store = app.state.store
    with TestClient(app) as client:
        assert client.get(f"/api/books/{BOOK}/pdf").content == client.get(f"/api/books/{BOOK}/source").content
        assert client.get(f"/api/books/{BOOK}/project").status_code == 404
        run, _ = completed(app)
        retain_run(store, run["id"])
        saved = retained_directory(store, store.get("book", BOOK))
        (saved / "main.pdf").unlink()
        assert client.get(f"/api/books/{BOOK}/pdf").status_code == 404
        assert client.get(f"/api/books/{BOOK}/source").status_code == 200
        repaired = retain_run(store, run["id"])
        assert client.get(f"/api/books/{BOOK}/pdf").status_code == 200
        assert retain_run(store, run["id"]) == repaired


def test_modified_project_is_not_retained_as_compiled(app):
    store = app.state.store
    run, project = completed(app)
    files = {p.relative_to(project).as_posix(): p.read_text() for p in project.rglob("*.tex")}
    atomic_json(project.parent / "finish-report.json", {"status": "PASSED", "project_hash": digest(files)})
    (project / "body.tex").write_text("uncompiled changes")
    with pytest.raises(WorkflowError) as exc:
        retain_run(store, run["id"])
    assert exc.value.code == "OUTPUT_CHANGED"
    assert not store.get("book", BOOK).get("retained_result")


@pytest.mark.asyncio
async def test_execute_retains_success_automatically_and_reports_only_storage_failure(app, monkeypatch):
    engine, store = app.state.engine, app.state.store
    run, project = completed(app)
    store.change(run["id"], lambda r: r.update(config=r["config"] | {"resolved": True},
                 stages={s: "PASSED" for s in r["stages"]}, state="PENDING"))
    async def compile(*args):
        pass
    monkeypatch.setattr(engine, "compile", compile)
    base = store.directory(run["id"])
    for name, data in (("structured.json", []), ("headings.json", []), ("setup.json", {}), ("symbols.json", {})):
        atomic_json(base / name, data)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert store.get("book", BOOK)["retained_result"]["run_id"] == run["id"]
    def failed(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("bookanalyst.engine.retain_run", failed)
    await engine.execute(run["id"])
    current = store.get("run", run["id"])
    assert current["state"] == "COMPLETED" and current["retention_error"]["code"] == "RETAIN_FAILED"
