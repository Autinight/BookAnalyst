"""Original-page rendering and physical splitting; extraction is always MinerU's job."""
import io
import threading
from pathlib import Path
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium

from .store import WorkflowError, digest, file_hash

PDF_LOCK = threading.RLock()


def inspect_pdf(path):
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise WorkflowError("ENCRYPTED_PDF", "请提供可直接读取的 PDF", 422)
        return {"page_count": len(reader.pages), "size_bytes": Path(path).stat().st_size,
                "sha256": file_hash(path)}
    except WorkflowError:
        raise
    except Exception:
        raise WorkflowError("INVALID_PDF", "PDF 无法读取", 422) from None


def render_page(path, page_idx, dpi=150, bbox=None):
    with PDF_LOCK:
        with pdfium.PdfDocument(str(path)) as pdf:
            if not 0 <= page_idx < len(pdf):
                raise WorkflowError("INVALID_PAGE", "PDF 页序号超出范围", 422)
            page = pdf[page_idx]
            width, height = page.get_size()
            rotation = page.get_rotation()
            bitmap = page.render(scale=dpi / 72)
            im = bitmap.to_pil().copy()
            bitmap.close()
            if bbox:
                x0, y0, x1, y1 = bbox
                if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                    raise WorkflowError("INVALID_REGION", "图像区域超出原页", 422)
                im = im.crop(tuple(round(n * dpi / 72) for n in bbox))
            stream = io.BytesIO()
            im.save(stream, format="PNG")
            page.close()
            data = stream.getvalue()
    return data, dict(page_idx=page_idx, pdf_region=bbox or [0, 0, width, height],
                      rotation=rotation, render_dpi=dpi, image_sha256=digest(data),
                      width=im.width, height=im.height, coordinate_system="top_left_display_points")


def split_pdf(path, pages, directory, max_pages, max_bytes):
    """Split by both constraints, then verify actual serialized bytes and exact page ownership."""
    reader = PdfReader(path)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = []

    def write_group(indices):
        writer = PdfWriter()
        for idx in indices:
            writer.add_page(reader.pages[idx])
        stream = io.BytesIO()
        writer.write(stream)
        data = stream.getvalue()
        if len(data) > max_bytes:
            if len(indices) == 1:
                raise WorkflowError("PAGE_TOO_LARGE", "单页超过解析文件大小限制", 422)
            middle = len(indices) // 2
            write_group(indices[:middle])
            write_group(indices[middle:])
            return
        key = f"chunk-{len(result):04d}"
        (directory / f"{key}.pdf").write_bytes(data)
        result.append(dict(id=key, pages=indices, sha256=digest(data),
                           size_bytes=len(data), path=f"{key}.pdf"))

    for offset in range(0, len(pages), max_pages):
        write_group(pages[offset:offset + max_pages])
    if [i for chunk in result for i in chunk["pages"]] != pages or len(pages) != len(set(pages)):
        raise WorkflowError("PAGE_COVERAGE", "拆分页集合不满足精确覆盖")
    return result
