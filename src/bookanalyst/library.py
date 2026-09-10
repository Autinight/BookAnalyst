"""Book metadata management; originals remain available to existing runs."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

from pydantic import Field, field_validator
from fastapi.responses import FileResponse

from .library_outputs import retain_run, retained_directory

from .reference_review import review_records, review_response
from .models import StrictModel
from .store import WorkflowError


class BookUpdate(StrictModel):
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("书名不能为空或包含控制字符")
        return value


def reveal_file(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise WorkflowError("SOURCE_MISSING", "原 PDF 文件不存在", 404)
    try:
        if os.name == "nt":
            # Explorer's /select argument must contain the filename, not a shell command.
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        raise WorkflowError("EXPLORER_FAILED", "无法打开系统文件管理器") from exc


def register_library_routes(app, store):
    @app.post("/api/runs/{rid}/retain")
    async def retain(rid: str):
        return await asyncio.to_thread(retain_run, store, rid)

    @app.get("/api/books/{bid}/source")
    async def original_pdf(bid: str):
        return FileResponse(store.get("book", bid)["path"], media_type="application/pdf")

    @app.get("/api/books/{bid}/reference-review")
    async def reference_review(bid: str, download: bool = False):
        book = store.get("book", bid)
        return review_response(review_records(retained_directory(store, book)), book["title"],
                               f"/api/books/{bid}/source", download)

    @app.get("/api/books/{bid}/project")
    async def project(bid: str):
        path = retained_directory(store, store.get("book", bid)) / "project.zip"
        if not path.is_file():
            raise WorkflowError("NO_OUTPUT", "工程归档缺失，请重新保留", 404)
        return FileResponse(path, media_type="application/zip", filename="book-project.zip")

    @app.patch("/api/books/{bid}")
    async def rename_book(bid: str, command: BookUpdate):
        book = store.update_book(bid, title=command.title)
        return {"id": book["id"], "title": book["title"]}

    @app.delete("/api/books/{bid}")
    async def delete_book(bid: str):
        store.update_book(bid, deleted=True)
        return {"id": bid, "deleted": True}

    @app.post("/api/books/{bid}/restore")
    async def restore_book(bid: str):
        store.update_book(bid, deleted=False)
        return {"id": bid, "deleted": False}

    @app.post("/api/books/{bid}/reveal")
    async def reveal_book(bid: str):
        book = store.get("book", bid)
        reveal_file(book["path"])
        return {"opened": True}
