"""Independent covers survive library edits without changing source or tasks."""

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from bookanalyst.app import create_app
from bookanalyst.book_covers import MAX_COVER_BYTES
from test_library import headers
from test_rebuild import BOOK, make_run


def picture(size=(200, 300), color="green", format="PNG"):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format=format)
    return stream.getvalue()


def test_cover_lifecycle_preserves_source_and_run(app, workspace):
    store = app.state.store
    source = Path(store.get("book", BOOK)["path"])
    original = source.read_bytes()
    run = make_run(app)
    before_run = store.get("run", run["id"])
    url = f"/api/books/{BOOK}/cover"
    with TestClient(app) as client:
        auth = headers(client)
        assert client.get(url).status_code == 404
        response = client.put(url, files={"file": ("cover.png", picture((1600, 2400)))}, headers=auth)
        assert response.status_code == 200
        first = response.json()["cover"]
        assert (first["width"], first["height"]) == (1000, 1500)
        saved = client.get(first["url"])
        assert saved.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(saved.content)) as image:
            assert image.size == (1000, 1500)
        client.delete(f"/api/books/{BOOK}", headers=auth)
        deleted = next(b for b in client.get("/api/bootstrap").json()["deleted_books"] if b["id"] == BOOK)
        assert deleted["cover"] == first
        client.post(f"/api/books/{BOOK}/restore", headers=auth)
    with TestClient(create_app(workspace, store.root)) as client:
        auth = headers(client)
        book = next(b for b in client.get("/api/bootstrap").json()["books"] if b["id"] == BOOK)
        assert book["cover"] == first
        assert client.get(first["url"]).content == saved.content
        second = client.put(url, files={"file": ("new.jpg", picture(color="red", format="JPEG"))}, headers=auth).json()["cover"]
        assert second["url"] != first["url"]
        assert client.get(second["url"]).content != saved.content
        assert client.delete(url, headers=auth).json()["cover"] is None
        assert client.get(url).status_code == 404
        book = next(b for b in client.get("/api/bootstrap").json()["books"] if b["id"] == BOOK)
        assert book["cover"] is None
    assert source.read_bytes() == original
    assert store.get("run", run["id"]) == before_run


def test_invalid_or_unauthorized_upload_keeps_existing_cover(app):
    url = f"/api/books/{BOOK}/cover"
    with TestClient(app) as client:
        auth = headers(client)
        assert client.put(url, files={"file": ("x.webp", picture(format="WEBP"))}, headers=auth).status_code == 200
        saved = client.get(url).content
        for payload in (b"%PDF-not-an-image", b"", picture(format="GIF")):
            assert client.put(url, files={"file": ("fake.png", payload)}, headers=auth).status_code == 422
        assert client.put(url, files={"file": ("big.png", b"x" * (MAX_COVER_BYTES + 1))}, headers=auth).status_code == 413
        assert client.put(url, files={"file": ("x.png", picture())}).status_code == 403
        assert client.delete(url).status_code == 403
        assert client.put("/api/books/missing/cover", files={"file": ("x.png", picture())}, headers=auth).status_code == 404
        assert client.get(url).content == saved


def cover_pdf(password=None):
    writer = PdfWriter()
    for color in ("1 0 0", "0 0 1"):
        page = writer.add_blank_page(width=200, height=300)
        stream = DecodedStreamObject()
        stream.set_data(f"{color} rg 0 0 200 300 re f".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def test_pdf_preview_and_save_use_first_page_without_changing_book(app):
    url = f"/api/books/{BOOK}/cover"
    before = app.state.store.get("book", BOOK)
    with TestClient(app) as client:
        auth = headers(client)
        files = {"file": ("my-cover.pdf", cover_pdf(), "application/pdf")}
        preview = client.post(url + "-preview", files=files, headers=auth)
        assert preview.status_code == 200
        assert preview.json()["data_url"].startswith("data:image/webp;base64,")
        assert app.state.store.get("book", BOOK) == before
        assert client.get(url).status_code == 404
        assert client.put(url, files=files, headers=auth).status_code == 200
        with Image.open(BytesIO(client.get(url).content)) as image:
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
            assert red > 240 and green < 15 and blue < 15
        assert app.state.store.get("book", BOOK)["path"] == before["path"]
        assert client.post(url + "-preview", files=files).status_code == 403


def test_failed_pdf_preview_preserves_saved_cover(app, monkeypatch):
    url = f"/api/books/{BOOK}/cover"
    with TestClient(app) as client:
        auth = headers(client)
        client.put(url, files={"file": ("cover.png", picture())}, headers=auth)
        saved = client.get(url).content
        empty = BytesIO()
        PdfWriter().write(empty)
        for content in (b"%PDF-broken", empty.getvalue(), cover_pdf("private")):
            response = client.post(url + "-preview", files={"file": ("bad.pdf", content)}, headers=auth)
            assert response.status_code == 422
        monkeypatch.setattr("bookanalyst.book_covers.MAX_COVER_PDF_BYTES", 2048)
        assert client.post(url + "-preview", files={"file": ("large.pdf", b"%PDF-" + b"x" * 2048)}, headers=auth).status_code == 413
        assert client.get(url).content == saved
