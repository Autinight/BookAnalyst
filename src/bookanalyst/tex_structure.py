"""Agent-led, opt-in restructuring of a retained TeX project copy."""

import base64
import copy
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from pydantic import Field

from .library_outputs import PROJECT_SUFFIXES, retained_directory
from .model_config import settings_models
from .models import STAGES, WORKFLOW_VERSION, StrictModel
from .store import WorkflowError, atomic_json, digest, file_hash
from .tex import compile_tex
from .pdf import inspect_pdf


class StructureCreate(StrictModel):
    result_id: str
    operation_id: str = Field(min_length=8)


STRUCTURE_INSTRUCTIONS = r"""Organize this already compiled TeX project into chapter/section .tex files.
Your ONLY job is file organization. Read main.tex, body.tex, headings.tex and other relevant
files. The working directory is a disposable COPY; never edit the source PDF or the
original retained result. body.tex is the reading-order manifest of \input{...} calls.
Put material before the first chapter/section in a front-matter file; put material
between a chapter and its first section in a chapter-introduction file. Put each
section, with its subsections, in its own file under its chapter. For article-class
papers without chapters, use sections/ at the top level. Preserve appendix order.
Use actual TeX and context, not PDF page breaks or an assumption that headings occupy
whole lines. For headings embedded in a line, keep that entire line in one file;
do not split within a line or insert a newline into the original text. Choose
neutral, stable file names.

Preserve EVERY BYTE of the original body.tex content in reading order when expanding
the new \input calls: do not correct, paraphrase, omit or duplicate text, whitespace,
comments, mathematics, labels, or image paths. Keep main.tex, headings.tex, preamble.tex,
all existing style files and assets unchanged. Do not modify the original body text in
order to make the new structure compile. Every \input in the new body.tex must be a
single line, with a path relative to the project root and no extension. Newly created
TeX files must live inside the project. Do not introduce new input commands into
the source text; existing commands must be preserved exactly. Do not use symlinks. The application checks the exact reconstructed text against the untouched
original and recompiles the result; report ambiguity rather than inventing content.
"""


def create_run(engine, bid, command):
    store = engine.store
    rid = uuid.uuid5(uuid.NAMESPACE_URL, "bookanalyst-tex-structure:" + bid + ":" + command.operation_id).hex
    signature = digest(command.model_dump())
    with store.output_lock:
        try:
            existing = store.get("run", rid)
        except WorkflowError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            if existing.get("structure_request_hash") != signature:
                raise WorkflowError("OPERATION_CONFLICT", "操作编号已用于其他整理请求")
            return existing
        book = store.get("book", bid)
        if book.get("deleted"):
            raise WorkflowError("BOOK_DELETED", "请先恢复这本书")
        if book.get("retained_result", {}).get("id") != command.result_id:
            raise WorkflowError("STALE_RESULT", "书库工程已更新，请重新选择")
        original = retained_directory(store, book)
        if not (original / "body.tex").is_file():
            raise WorkflowError("NO_BODY", "保留工程没有 body.tex，无法自动整理")
        source_run = store.get("run", book["retained_result"]["run_id"])
        config = copy.deepcopy(source_run["config"])
        settings = store.get("settings", "main")
        config.update(stage_models=settings_models(settings), llm_concurrency=settings.get("llm_concurrency", 2))
        now = time.time()
        run = dict(id=rid, revision=1, created_at=now, updated_at=now,
                   workflow_version=WORKFLOW_VERSION, kind="tex_structure", state="PENDING",
                   config=config, source=copy.deepcopy(book), stage="finish",
                   stages={s: "PASSED" if s != "finish" else "PENDING" for s in STAGES},
                   usage={"llm": 0}, error=None, pause_requested=False,
                   structure_request_hash=signature,
                   structure={"source_result_id": command.result_id})
        base = store.root / "runs" / rid / "v7"
        project = base / "tex"
        project.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for path in original.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in PROJECT_SUFFIXES:
                continue
            relative = path.relative_to(original)
            if any(part.startswith(".") for part in relative.parts) or path.name in ("retained.json",):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(original.resolve()):
                raise WorkflowError("INVALID_PATH", "原工程包含外部链接")
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if relative.as_posix() != "main.pdf":
                manifest[relative.as_posix()] = file_hash(path)
        atomic_json(base / "structure-source.json", {
            "files": manifest, "body_base64": base64.b64encode((original / "body.tex").read_bytes()).decode("ascii"),
        })
        store.put("run", rid, run)
        return run


INPUT_LINE = re.compile(r"\\input\{([^{}]+)\}\r?\n?")


def validate_structure(project, snapshot):
    """Allow only body.tex plus new, flat TeX fragments; verify exact ordered payload."""
    project = Path(project)
    files = snapshot["files"]
    for name, expected in files.items():
        path = project / name
        if not path.is_file() or path.is_symlink() or (name != "body.tex" and file_hash(path) != expected):
            raise WorkflowError("STRUCTURE_CHANGED", f"原工程文件被改动或删除：{name}")
    body = (project / "body.tex").read_bytes().decode("utf-8")
    fragments = []
    position = 0
    seen = set()
    for match in INPUT_LINE.finditer(body):
        if match.start() != position or (position and body[position - 1] != "\n"):
            raise WorkflowError("STRUCTURE_MANIFEST", "body.tex 只能包含逐行的 \\input 清单")
        name = match[1]
        if (not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or name.startswith("/")
                or any(part in (".", "..", "") for part in name.split("/")) or name.endswith(".tex")):
            raise WorkflowError("STRUCTURE_PATH", f"不安全的正文路径：{name}")
        rel = Path(name + ".tex")
        path = project / rel
        if rel.as_posix() in files or name in seen or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(project.resolve()):
            raise WorkflowError("STRUCTURE_PATH", f"重复、缺失或越界的正文文件：{name}")
        seen.add(name)
        text = path.read_bytes()
        if fragments and not fragments[-1].endswith(b"\n"):
            raise WorkflowError("STRUCTURE_BOUNDARY", "正文片段不得在一行中途截断")
        fragments.append(text)
        position = match.end()
    if not fragments or position != len(body):
        raise WorkflowError("STRUCTURE_MANIFEST", "body.tex 必须仅包含逐行的 \\input 清单")
    if b"".join(fragments) != base64.b64decode(snapshot["body_base64"]):
        raise WorkflowError("STRUCTURE_CONTENT", "整理前后的正文未能逐字重组，不能交付")
    actual = {p.relative_to(project).as_posix() for p in project.rglob("*.tex")}
    if any(p.is_symlink() for p in project.rglob("*")):
        raise WorkflowError("STRUCTURE_PATH", "工程中存在符号链接")
    additions = {p.relative_to(project).as_posix() for p in project.rglob("*")
                 if p.is_file() and p.suffix.lower() in PROJECT_SUFFIXES}
    if additions - set(files) - {name + ".tex" for name in seen} - {"main.pdf"}:
        raise WorkflowError("STRUCTURE_FILES", "工程中存在未授权的新增文件")
    original_tex = {name for name in files if name.endswith(".tex")}
    if actual != original_tex | {name + ".tex" for name in seen}:
        raise WorkflowError("STRUCTURE_FILES", "存在未列入正文清单的新增 TeX 文件")
    return len(fragments)


async def finish(engine, run):
    """Resume agent on the copy; validate its edits before compiling and publishing."""
    from .finisher import repair_project

    base = engine.store.directory(run["id"])
    project = base / "tex"
    snapshot = json.loads((base / "structure-source.json").read_text(encoding="utf-8"))
    completed = base / "structure-validated.json"
    if not completed.exists():
        engine.check_pause(engine.live(run["id"]))
        feedback_path = base / "compile-report.json"
        feedback = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.exists() else {
            "status": "STRUCTURE_REQUESTED", "message": "Organize files; original text must remain byte-identical."
        }
        await repair_project(engine.providers, engine.live(run["id"]), project, feedback)
        engine.check_pause(engine.live(run["id"]))
    try:
        count = validate_structure(project, snapshot)
    except WorkflowError as exc:
        atomic_json(base / "compile-report.json", {"status": "FAILED", "code": exc.code,
                    "message": exc.message})
        raise
    report = await compile_tex(project)
    atomic_json(base / "compile-report.json", report)
    if report.get("status") != "PASSED":
        raise WorkflowError("STRUCTURE_COMPILE", "分层工程编译失败，副本已保存，可在任务中继续")
    try:
        validate_structure(project, snapshot)
    except WorkflowError as exc:
        atomic_json(base / "compile-report.json", {"status": "FAILED", "code": exc.code,
                    "message": exc.message})
        raise
    output = inspect_pdf(project / "main.pdf")
    tex_files = {p.relative_to(project).as_posix(): p.read_text(encoding="utf-8") for p in project.rglob("*.tex")}
    atomic_json(base / "finish-report.json", {"status": "PASSED", "accepted_by": "compiler",
                "compile_result": report, "project_hash": digest(tex_files), "output_pdf_hash": output["sha256"]})
    atomic_json(completed, {"fragments": count})
    engine.store.task(run["id"], "finish-project", "compile_repair", "PASSED", [])


def register_structure_routes(app, store, engine):
    from fastapi.responses import FileResponse
    import zipfile

    @app.post("/api/books/{bid}/organize-tex")
    def create(bid: str, command: StructureCreate):
        return store.summary(create_run(engine, bid, command))

    @app.get("/api/runs/{rid}/structured-export")
    def export(rid: str):
        run = store.get("run", rid)
        if run.get("kind") != "tex_structure" or run["state"] != "COMPLETED":
            raise WorkflowError("NOT_COMPLETE", "分层工程尚未编译通过")
        project = store.directory(rid) / "tex"
        archive = store.root / "exports" / f"{rid}-structured-{run['revision']}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in project.rglob("*"):
                if path.is_file() and path.suffix.lower() in PROJECT_SUFFIXES and not path.is_symlink():
                    zipped.write(path, path.relative_to(project))
        return FileResponse(archive, filename=archive.name, media_type="application/zip")
