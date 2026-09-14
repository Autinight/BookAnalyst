"""Retain successful, self-contained output snapshots beside each library source."""
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import zipfile

from .models import WORKFLOW_VERSION
from .pdf import inspect_pdf
from .reference_review import review_records
from .store import WorkflowError, atomic_json, digest, file_hash


PROJECT_SUFFIXES = {".tex", ".sty", ".cls", ".bib", ".bst", ".bbl", ".bbx", ".cbx", ".def", ".cfg",
                    ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".ttf", ".otf", ".pfb", ".tfm",
                    ".map", ".enc", ".ist", ".json", ".csv", ".dat", ".txt", ".lua"}


def output_directory(store, run):
    if run.get("workflow_version") in ("0.6", WORKFLOW_VERSION):
        return store.directory(run["id"]) / "tex"
    root = store.root / "runs" / run["id"]
    paths = list(root.glob("r*/S7/tex/main.tex")) + list(root.glob("r*/S7/candidate/tex/main.tex"))
    if not paths:
        raise WorkflowError("NO_OUTPUT", "此运行尚无 TeX 产物", 404)
    return max(paths, key=lambda p: int(p.relative_to(root).parts[0][1:])).parent


def book_directory(store, bid):
    path = (store.root / "books" / bid).resolve()
    if path.parent != (store.root / "books").resolve():
        raise WorkflowError("INVALID_PATH", "书籍目录无效")
    return path


def retained_directory(store, book):
    result = book.get("retained_result")
    if not result:
        raise WorkflowError("NO_OUTPUT", "这本书尚无已保留的转换结果", 404)
    root = book_directory(store, book["id"]) / "results"
    path = (root / result["id"]).resolve()
    if path.parent != root.resolve():
        raise WorkflowError("INVALID_PATH", "结果目录无效")
    if not (path / "main.pdf").is_file() or not (path / "main.tex").is_file():
        raise WorkflowError("NO_OUTPUT", "保留结果文件缺失，请从成功任务重新保留", 404)
    return path


def public_result(book):
    result = book.get("retained_result")
    return ({k: result[k] for k in ("id", "run_id", "saved_at", "pages", "pdf_pages")}
            | {"review_count": result.get("review_count", 0), "template_name": result.get("template_name")}) if result else None


def retain_run(store, rid):
    # Serialize snapshots, not unrelated model work. Metadata is published only after all files exist.
    with store.output_lock:
        run = store.get("run", rid)
        if run["state"] != "COMPLETED":
            raise WorkflowError("NOT_COMPLETE", "只有编译通过的任务可以保留到书库")
        bid = run["source"]["id"]
        book = store.get("book", bid)
        # Tasks reach this function only on an explicit save. The user
        # may choose any completed candidate; previous book snapshots remain intact.
        project = output_directory(store, run)
        if not (project / "main.tex").is_file() or not (project / "main.pdf").is_file():
            raise WorkflowError("NO_OUTPUT", "成功任务的 TeX 工程或 PDF 缺失", 404)
        files = [p for p in project.rglob("*") if p.is_file() and p.suffix.lower() in PROJECT_SUFFIXES
                 and not any(part.startswith('.') for part in p.relative_to(project).parts)
                 and not p.name.endswith("-session.json")]
        if any(not p.resolve().is_relative_to(project.resolve()) for p in files):
            raise WorkflowError("INVALID_PATH", "工程依赖超出当前工程目录")
        hashes = {p.relative_to(project).as_posix(): file_hash(p) for p in files}
        meta = inspect_pdf(project / "main.pdf")
        report_path = project.parent / "finish-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        tex_files = {p.relative_to(project).as_posix(): p.read_text(encoding="utf-8") for p in files if p.suffix == ".tex"}
        if (report.get("project_hash") and report["project_hash"] != digest(tex_files)
                or report.get("output_pdf_hash") and report["output_pdf_hash"] != meta["sha256"]):
            raise WorkflowError("OUTPUT_CHANGED", "当前工程或 PDF 与编译通过时不一致，请重新编译后保留")
        content_hash = digest(hashes)
        key = rid + "-" + content_hash[:16]
        current = book.get("retained_result", {})
        if current.get("run_id") == rid and current.get("content_hash") == content_hash:
            candidate = (book_directory(store, bid) / "results" / current["id"]).resolve()
            if candidate.parent == (book_directory(store, bid) / "results").resolve():
                key = current["id"]
        root = book_directory(store, bid)
        outputs = root / "results"
        outputs.mkdir(parents=True, exist_ok=True)
        destination = outputs / key
        if destination.exists() and (not (destination / "project.zip").is_file()
                                     or any(not (destination / name).is_file() for name in hashes)):
            key += "-" + uuid.uuid4().hex[:8]
            destination = outputs / key
        if not destination.exists():
            staging = outputs / (".saving-" + uuid.uuid4().hex)
            staging.mkdir()
            try:
                for p in files:
                    target = staging / p.relative_to(project)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
                if any(file_hash(staging / name) != value for name, value in hashes.items()):
                    raise WorkflowError("OUTPUT_CHANGED", "保存时工程发生变化，请编译通过后重试")
                latest = store.get("run", rid)
                if latest["state"] != "COMPLETED" or latest["revision"] != run["revision"]:
                    raise WorkflowError("OUTPUT_CHANGED", "任务已重新开始，请等待编译通过后保留")
                atomic_json(staging / "retained.json", {"run_id": rid, "revision": run["revision"], "files": hashes})
                with zipfile.ZipFile(staging / "project.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                    for name in hashes:
                        archive.write(staging / name, name)
                    archive.write(staging / "retained.json", "retained.json")
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    # The staging path is created above, is a direct child of this book's results directory.
                    if staging.resolve().parent != outputs.resolve():
                        raise WorkflowError("INVALID_PATH", "临时结果目录无效")
                    shutil.rmtree(staging)
        # Built-in/imported external sources get a managed copy; the old source is never moved or deleted.
        source = root / "source.pdf"
        if Path(book["path"]).resolve() != source.resolve():
            if not source.exists():
                temp = root / (".source-" + uuid.uuid4().hex + ".tmp")
                try:
                    shutil.copy2(book["path"], temp)
                    if file_hash(temp) != book["sha256"]:
                        raise WorkflowError("SOURCE_CHANGED", "原书文件与书库记录不符")
                    os.replace(temp, source)
                finally:
                    temp.unlink(missing_ok=True)
            elif file_hash(source) != book["sha256"]:
                raise WorkflowError("SOURCE_CHANGED", "书库中的原书副本不匹配")
        result = {"id": key, "run_id": rid, "revision": run["revision"], "content_hash": content_hash,
                  "saved_at": current.get("saved_at", time.time()) if current.get("id") == key else time.time(),
                  "pages": [run["config"]["start_page"], run["config"]["end_page"]], "pdf_pages": meta["page_count"],
                  "review_count": len(review_records(project)), "template_name": run.get("template", {}).get("name")}
        store.update_book(bid, path=str(source), retained_result=result)
        if run.get("retention_error"):
            store.change(rid, lambda r: r.pop("retention_error", None))
        return result
