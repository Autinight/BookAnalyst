"""User-selected library covers, independent of source PDFs and retained output."""

import asyncio
import base64
from io import BytesIO
import os
import uuid

from fastapi import File, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
import pypdfium2 as pdfium

from .library_outputs import book_directory
from .store import WorkflowError
from .pdf import PDF_LOCK

MAX_COVER_BYTES = 12 * 1024 * 1024
MAX_COVER_PDF_BYTES = 200 * 1024 * 1024


def public_cover(book):
    cover = book.get("cover")
    return ({"url": f"/api/books/{book['id']}/cover?v={cover['version']}",
             "width": cover["width"], "height": cover["height"]} if cover else None)


def cover_image(content):
    if b"%PDF-" in content[:1024]:
        try:
            with PDF_LOCK, pdfium.PdfDocument(content) as pdf:
                if not len(pdf):
                    raise ValueError("Empty PDF")
                page = pdf[0]
                try:
                    width, height = page.get_size()
                    bitmap = page.render(scale=min(1000 / width, 1500 / height))
                    try:
                        return bitmap.to_pil().convert("RGBA")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        except Exception as exc:
            raise WorkflowError("INVALID_COVER_PDF", "无法读取 PDF 第一页，请选择未加密且有效的 PDF", 422) from exc
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in ("JPEG", "PNG", "WEBP"):
                raise WorkflowError("INVALID_COVER", "封面支持 JPG、PNG、WebP 或 PDF", 422)
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise WorkflowError("INVALID_COVER", "无法读取文件，请选择有效的图片或 PDF", 422) from exc


def encode_cover(content):
    image = cover_image(content)
    image.thumbnail((1000, 1500), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", image.size, "#faf9f5")
    canvas.paste(image, mask=image.getchannel("A"))
    output = BytesIO()
    canvas.save(output, format="WEBP", quality=90)
    return output.getvalue(), canvas.width, canvas.height


def save_cover(store, bid, content):
    store.get("book", bid)
    encoded, width, height = encode_cover(content)
    cover = {"version": uuid.uuid4().hex, "width": width, "height": height}
    with store.output_lock:
        directory = book_directory(store, bid)
        directory.mkdir(parents=True, exist_ok=True)
        staging = directory / (".cover-" + cover["version"] + ".tmp")
        try:
            staging.write_bytes(encoded)
            os.replace(staging, directory / "cover.webp")
            book = store.update_book(bid, cover=cover)
        finally:
            staging.unlink(missing_ok=True)
    return public_cover(book)


def remove_cover(store, bid):
    with store.output_lock:
        store.get("book", bid)
        (book_directory(store, bid) / "cover.webp").unlink(missing_ok=True)
        store.update_book(bid, cover=None)


def register_cover_routes(app, store):
    async def read_upload(file):
        try:
            prefix = await file.read(1024)
            is_pdf = b"%PDF-" in prefix
            limit = MAX_COVER_PDF_BYTES if is_pdf else MAX_COVER_BYTES
            content = prefix + await file.read(limit + 1 - len(prefix))
        finally:
            await file.close()
        if len(content) > limit:
            message = "PDF 不能超过 200 MB" if is_pdf else "封面图片不能超过 12 MB"
            raise WorkflowError("COVER_TOO_LARGE", message, 413)
        return content

    @app.post("/api/books/{bid}/cover-preview")
    async def preview_cover(bid: str, file: UploadFile = File(...)):
        store.get("book", bid)
        content = await read_upload(file)
        encoded, width, height = await asyncio.to_thread(encode_cover, content)
        return {"data_url": "data:image/webp;base64," + base64.b64encode(encoded).decode("ascii"),
                "width": width, "height": height}

    @app.get("/api/books/{bid}/cover")
    async def get_cover(bid: str):
        book = store.get("book", bid)
        path = book_directory(store, bid) / "cover.webp"
        if not book.get("cover") or not path.is_file():
            raise WorkflowError("NO_COVER", "这本书尚未设置独立封面", 404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-store"})

    @app.put("/api/books/{bid}/cover")
    async def upload_cover(bid: str, file: UploadFile = File(...)):
        content = await read_upload(file)
        return {"id": bid, "cover": await asyncio.to_thread(save_cover, store, bid, content)}

    @app.delete("/api/books/{bid}/cover")
    async def clear_cover(bid: str):
        await asyncio.to_thread(remove_cover, store, bid)
        return {"id": bid, "cover": None}
