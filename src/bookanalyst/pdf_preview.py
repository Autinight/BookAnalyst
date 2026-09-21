"""On-demand PDF pages for the shared desktop/browser preview panel."""

from pathlib import Path
from typing import Literal

from fastapi import Query
from fastapi.responses import Response
import pypdfium2 as pdfium

from .library_outputs import output_directory, retained_directory
from .pdf import PDF_LOCK, render_page
from .store import WorkflowError

PreviewKind = Literal["source", "book", "run"]


def preview_path(store, kind, key):
    if kind == "run":
        run = store.get("run", key)
        path = output_directory(store, run) / "main.pdf"
        title = run["source"]["title"]
    else:
        book = store.get("book", key)
        path = (retained_directory(store, book) / "main.pdf"
                if kind == "book" and book.get("retained_result") else Path(book["path"]))
        title = book["title"]
    if not path.is_file():
        raise WorkflowError("NO_PDF", "PDF 文件不存在或尚未生成", 404)
    return path, title


def register_pdf_preview_routes(app, store):
    @app.get("/api/pdf-preview/{kind}/{key}")
    def metadata(kind: PreviewKind, key: str):
        path, title = preview_path(store, kind, key)
        try:
            with PDF_LOCK, pdfium.PdfDocument(str(path)) as pdf:
                count = len(pdf)
                if not count:
                    raise ValueError("Empty PDF")
                page_sizes = [pdf.get_page_size(index) for index in range(count)]
        except Exception as exc:
            raise WorkflowError("INVALID_PDF", "无法预览此 PDF", 422) from exc
        return {"title": title, "page_count": count, "page_sizes": page_sizes}

    @app.get("/api/pdf-preview/{kind}/{key}/pages/{page}")
    def page_image(kind: PreviewKind, key: str, page: int, dpi: int = Query(150, ge=72, le=160)):
        path, _ = preview_path(store, kind, key)
        try:
            content, _ = render_page(path, page - 1, dpi=dpi)
        except WorkflowError:
            raise
        except Exception as exc:
            raise WorkflowError("INVALID_PDF", "PDF 页面无法读取", 422) from exc
        return Response(content, media_type="image/png")
