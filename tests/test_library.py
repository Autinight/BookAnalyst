"""Library edits preserve PDF sources and already-created conversion runs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookanalyst.app import create_app
from bookanalyst.library import reveal_file
from test_rebuild import BOOK, config, make_run


def headers(client):
    return {"X-Bookanalyst-Token": client.get("/api/bootstrap").json()["token"]}


def test_rename_changes_metadata_without_touching_running_job_or_pdf(app):
    store = app.state.store
    run = make_run(app)
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    before_run = store.get("run", run["id"])
    before_book = store.get("book", BOOK)
    with TestClient(app) as client:
        response = client.patch(f"/api/books/{BOOK}", json={"title": "  新书名  "}, headers=headers(client))
        assert response.status_code == 200
        book = next(b for b in client.get("/api/bootstrap").json()["books"] if b["id"] == BOOK)
        assert book["title"] == "新书名"
        assert store.get("book", BOOK)["path"] == before_book["path"]
        assert store.get("run", run["id"]) == before_run
        assert client.get(f"/api/books/{BOOK}/pdf").status_code == 200


def test_delete_restore_preserves_source_and_current_run(app):
    store = app.state.store
    run = make_run(app)
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    before = store.get("run", run["id"])
    source = Path(store.get("book", BOOK)["path"])
    with TestClient(app) as client:
        auth = headers(client)
        assert client.delete(f"/api/books/{BOOK}", headers=auth).status_code == 200
        assert client.delete(f"/api/books/{BOOK}", headers=auth).status_code == 200
        data = client.get("/api/bootstrap").json()
        assert BOOK not in [b["id"] for b in data["books"]]
        assert BOOK in [b["id"] for b in data["deleted_books"]]
        assert store.get("run", run["id"]) == before and source.exists()
        assert client.get(f"/api/books/{BOOK}/pdf").status_code == 200
        assert client.get(f"/api/books/{BOOK}/image?page=1&dpi=40").status_code == 200
        assert client.post("/api/runs", json=config(), headers=auth).json()["code"] == "BOOK_DELETED"
        assert client.post(f"/api/books/{BOOK}/restore", headers=auth).status_code == 200
        assert BOOK in [b["id"] for b in client.get("/api/bootstrap").json()["books"]]
        assert client.post("/api/runs", json=config(), headers=auth).status_code == 200


def test_deleted_builtin_book_does_not_reappear_after_restart(app, workspace):
    with TestClient(app) as client:
        client.delete(f"/api/books/{BOOK}", headers=headers(client))
    restarted = create_app(workspace, app.state.store.root)
    with TestClient(restarted) as client:
        data = client.get("/api/bootstrap").json()
        assert BOOK not in [b["id"] for b in data["books"]]
        assert BOOK in [b["id"] for b in data["deleted_books"]]


@pytest.mark.parametrize("title", ["", "   ", "a\nb", "x" * 301])
def test_invalid_titles_are_rejected(app, title):
    with TestClient(app) as client:
        response = client.patch(f"/api/books/{BOOK}", json={"title": title}, headers=headers(client))
        assert response.status_code == 422


def test_management_requires_local_token_and_reveal_uses_stored_path(app, monkeypatch):
    revealed = []
    monkeypatch.setattr("bookanalyst.library.reveal_file", lambda p: revealed.append(p))
    with TestClient(app) as client:
        assert client.patch(f"/api/books/{BOOK}", json={"title": "test"}).status_code == 403
        assert client.delete(f"/api/books/{BOOK}").status_code == 403
        assert client.post(f"/api/books/{BOOK}/restore").status_code == 403
        assert client.post(f"/api/books/{BOOK}/reveal").status_code == 403
        assert not revealed
        auth = headers(client)
        assert client.post(f"/api/books/{BOOK}/reveal", headers=auth).status_code == 200
        assert revealed == [app.state.store.get("book", BOOK)["path"]]
        assert client.patch("/api/books/missing", json={"title": "test"}, headers=auth).status_code == 404


def test_reveal_filename_is_never_executed_as_shell_code(tmp_path, monkeypatch):
    source = tmp_path / "a & b $name.pdf"
    source.write_bytes(b"pdf")
    calls = []
    monkeypatch.setattr("bookanalyst.library.subprocess.Popen", lambda args: calls.append(args))
    reveal_file(source)
    assert calls[0][0] in ("explorer.exe", "open", "xdg-open")
    assert str(source.resolve()) in calls[0] or str(source.parent.resolve()) in calls[0]
