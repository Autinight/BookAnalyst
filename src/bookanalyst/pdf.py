"""Original PDF rendering; conversion receives only selected page images."""

import io
import threading
from pathlib import Path
from pypdf import PdfReader
import pypdfium2 as pdfium

from .store import WorkflowError, digest, file_hash

PDF_LOCK = threading.RLock()


def inspect_pdf(path):
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise WorkflowError("ENCRYPTED_PDF", "请提供可直接读取的 PDF", 422)
        return {
            "page_count": len(reader.pages),
            "size_bytes": Path(path).stat().st_size,
            "sha256": file_hash(path),
        }
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
    return data, dict(
        page_idx=page_idx,
        pdf_region=bbox or [0, 0, width, height],
        rotation=rotation,
        render_dpi=dpi,
        image_sha256=digest(data),
        width=im.width,
        height=im.height,
        coordinate_system="top_left_display_points",
    )
