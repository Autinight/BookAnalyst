from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from bookanalyst.library_outputs import retain_run
from test_library_outputs import completed
from test_rebuild import BOOK


def test_preview_keeps_original_and_generated_documents_distinct(app):
    run, _ = completed(app)
    retain_run(app.state.store, run["id"])
    with TestClient(app) as client:
        original = client.get(f"/api/pdf-preview/source/{BOOK}")
        generated = client.get(f"/api/pdf-preview/book/{BOOK}")
        output = client.get(f"/api/pdf-preview/run/{run['id']}")
        assert original.json()["page_count"] == 45
        assert len(original.json()["page_sizes"]) == 45
        assert generated.json()["page_count"] == output.json()["page_count"] == 1
        assert generated.json()["page_sizes"] == output.json()["page_sizes"] == [[100, 100]]
        source_page = client.get(f"/api/pdf-preview/source/{BOOK}/pages/1?dpi=72")
        generated_page = client.get(f"/api/pdf-preview/book/{BOOK}/pages/1?dpi=72")
        assert source_page.status_code == generated_page.status_code == 200
        assert generated_page.headers["content-type"] == "image/png"
        with Image.open(BytesIO(generated_page.content)) as image:
            assert image.size == (100, 100)
        assert source_page.content != generated_page.content
        assert client.get(f"/api/pdf-preview/run/{run['id']}/pages/1?dpi=72").content == generated_page.content


def test_preview_rejects_missing_documents_and_invalid_pages(app, tmp_path):
    with TestClient(app) as client:
        assert client.get("/api/pdf-preview/source/missing").status_code == 404
        for page in (0, -1, 46):
            assert client.get(f"/api/pdf-preview/source/{BOOK}/pages/{page}").status_code == 422
        assert client.get(f"/api/pdf-preview/source/{BOOK}/pages/1?dpi=1000").status_code == 422
        assert client.get(f"/api/pdf-preview/unknown/{BOOK}").status_code == 422
        app.state.store.update_book(BOOK, path=str(tmp_path / "missing.pdf"))
        assert client.get(f"/api/pdf-preview/source/{BOOK}").status_code == 404
        assert client.get(f"/api/pdf-preview/source/{BOOK}/pages/1").status_code == 404


def test_preview_dimensions_match_mixed_and_rotated_pages(app, tmp_path):
    path = tmp_path / "mixed-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_blank_page(width=200, height=300).rotate(90)
    writer.add_blank_page(width=400, height=200)
    writer.write(path)
    app.state.store.update_book(BOOK, path=str(path))
    with TestClient(app) as client:
        info = client.get(f"/api/pdf-preview/source/{BOOK}").json()
        assert info["page_sizes"] == [[200, 300], [300, 200], [400, 200]]
        for page, size in enumerate(info["page_sizes"], start=1):
            response = client.get(f"/api/pdf-preview/source/{BOOK}/pages/{page}?dpi=72")
            with Image.open(BytesIO(response.content)) as image:
                assert list(image.size) == size
