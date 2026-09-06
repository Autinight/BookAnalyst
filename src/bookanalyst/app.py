"""Local Web application. Settings, workflow operations, and artifact serving are separate."""
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import secrets
import uuid
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import DEFAULT_SETTINGS, validate_settings, secret
from .engine import Engine
from .llm import Providers
from .mineru import MinerU
from .models import RunCreate, Operation, Rerun, Correction
from .pdf import inspect_pdf, render_page
from .store import Store, WorkflowError, atomic_json


def create_app(workspace=None, data_dir=None):
    workspace = Path(workspace or os.environ.get("BOOKANALYST_WORKSPACE", Path.cwd())).resolve()
    store = Store(data_dir or os.environ.get("BOOKANALYST_DATA_DIR", workspace / ".bookanalyst"))
    try:
        store.get("settings", "main")
    except WorkflowError:
        store.put("settings", "main", copy.deepcopy(DEFAULT_SETTINGS))
    manifest = workspace / "tests/data/books.json"
    if manifest.exists():
        for item in json.loads(manifest.read_text(encoding="utf-8"))["books"]:
            path = (manifest.parent / item["path"]).resolve()
            if not any(b["id"] == item["id"] for b in store.list("book")):
                metadata = inspect_pdf(path)
                if metadata["sha256"] != item["sha256"]:
                    raise RuntimeError("Registered test book hash mismatch")
                store.put("book", item["id"], dict(id=item["id"], title=item["title"], path=str(path), **metadata))
    providers = Providers(store, workspace)
    engine = Engine(store, workspace, providers, MinerU(store, workspace))
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await engine.close()

    app = FastAPI(title="BookAnalyst", version="0.5.0", lifespan=lifespan)
    app.state.store, app.state.engine, app.state.workspace = store, engine, workspace
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_commands(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"code": "INVALID_ORIGIN", "message": "仅接受本地界面操作"}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-bookanalyst-token", ""), token):
                return JSONResponse({"code": "INVALID_TOKEN", "message": "页面会话已变化，请刷新"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    @app.exception_handler(WorkflowError)
    async def error_handler(request, error):
        return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)

    @app.get("/api/bootstrap")
    async def bootstrap():
        settings = store.get("settings", "main")
        return {"token": token, "version": "0.5.0", "books": store.list("book"), "runs": store.list("run"),
                "settings": settings, "credentials": {
                    "mineru": bool(secret(workspace, settings["mineru"]["api_key_env"]))},
                "stages": [s["name"] for s in json.loads(
                    (workspace / "docs/workflow.contract.json").read_text(encoding="utf-8"))["stages"]]}

    @app.post("/api/books")
    async def add_book(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise WorkflowError("INVALID_PDF", "请选择 PDF 文件", 422)
        key = uuid.uuid4().hex
        path = store.root / "books" / (key + ".pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with path.open("wb") as stream:
                while data := await file.read(1024 * 1024):
                    size += len(data)
                    if size > 512 * 1024 * 1024:
                        raise WorkflowError("FILE_TOO_LARGE", "本地导入限 512 MiB", 422)
                    stream.write(data)
            metadata = await asyncio.to_thread(inspect_pdf, path)
            existing = next((b for b in store.list("book") if b["sha256"] == metadata["sha256"]), None)
            if existing:
                path.unlink()
                return existing
            return store.put("book", key, dict(id=key, title=Path(file.filename).stem, path=str(path), **metadata))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/books/{book_id}/image")
    async def page_image(book_id: str, page_idx: int = Query(0, ge=0), dpi: int = Query(150, ge=72, le=300)):
        book = store.get("book", book_id)
        if page_idx >= book["page_count"]:
            raise WorkflowError("INVALID_PAGE", "页序号超出范围", 422)
        path = store.root / "previews" / book["sha256"] / f"{page_idx}-{dpi}.png"
        if not path.exists():
            data, metadata = await asyncio.to_thread(render_page, book["path"], page_idx, dpi)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            atomic_json(path.with_suffix(".json"), metadata)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/books/{book_id}/pdf")
    async def original(book_id: str):
        return FileResponse(store.get("book", book_id)["path"], media_type="application/pdf")

    @app.put("/api/settings")
    async def settings_save(request: Request):
        if any(r["state"] == "RUNNING" for r in store.list("run")):
            raise WorkflowError("RUNNING", "运行期间不能更改连接；当前运行结束后可保存设置")
        value = validate_settings(await request.json())
        return store.put("settings", "main", value)

    @app.get("/api/connections/{connection_id}/status")
    async def connection_status(connection_id: str):
        return await providers.status(connection_id)

    @app.post("/api/connections/{connection_id}/login")
    async def connection_login(connection_id: str):
        return await providers.login(connection_id)

    @app.post("/api/runs")
    async def create_run(command: RunCreate):
        book = store.get("book", command.book_id)
        config = command.model_dump()
        if config["end_page"] > book["page_count"]:
            raise WorkflowError("INVALID_SCOPE", "处理范围超出书籍", 422)
        if command.profile in ("cloud_smoke", "local_smoke"):
            config.update(max_llm_requests=0, max_parse_submissions=1,
                          max_submitted_pages=3 if command.profile == "cloud_smoke" else 1,
                          parser="cloud" if command.profile == "cloud_smoke" else "local",
                          visual_mode="disabled", visual_pages=[])
        elif command.profile == "llm_smoke":
            config.update(max_parse_submissions=0, max_submitted_pages=0, visual_mode="sampled")
        elif command.profile == "offline_fixture":
            config.update(max_llm_requests=0, max_parse_submissions=0, max_submitted_pages=0,
                          visual_mode="sampled", visual_pages=[14])
            if book["id"] != "elliptic-pde-second-order" or (command.start_page, command.end_page) != (14, 14):
                raise WorkflowError("FIXTURE_SCOPE", "离线夹具固定使用默认书籍 PDF 第 14 页", 422)
        return store.create_run(config, book, store.get("settings", "main")["mineru"])

    @app.get("/api/runs/{run_id}")
    async def run_status(run_id: str):
        return store.get("run", run_id) | {"calls": store.calls(run_id)}

    @app.post("/api/runs/{run_id}/start")
    async def start(run_id: str, command: Operation):
        return engine.start(run_id, command)

    @app.get("/api/runs/{run_id}/rerun-preview")
    async def rerun_preview(run_id: str, stage: str = "S6", task_id: str | None = None):
        if stage not in [f"S{i}" for i in range(9)]:
            raise WorkflowError("INVALID_STAGE", "阶段不存在", 422)
        return engine.preview(run_id, stage, task_id)

    @app.post("/api/runs/{run_id}/rerun")
    async def rerun(run_id: str, command: Rerun):
        await providers.reconcile(store.get("run", run_id))
        return engine.rerun(run_id, command)

    @app.post("/api/runs/{run_id}/pause")
    async def pause(run_id: str, command: Operation):
        return engine.pause(run_id, command)

    @app.post("/api/runs/{run_id}/reconcile")
    async def reconcile(run_id: str):
        run = store.get("run", run_id)
        if run["state"] == "RUNNING":
            raise WorkflowError("RUNNING", "请等待当前运行停止后核对已有请求")
        return {"calls": await providers.reconcile(run)}

    @app.post("/api/runs/{run_id}/corrections")
    async def correction(run_id: str, command: Correction):
        def apply(run):
            if run["state"] == "RUNNING":
                raise WorkflowError("RUNNING", "请等待当前运行结束后提交修正")
            atoms, _ = engine.pipeline.normalized(run)
            atom = next((a for a in atoms if a["atom_id"] == command.atom_id), None)
            if not atom or atom["text"] != command.before or atom["page_idx"] != command.page_idx:
                raise WorkflowError("STALE_CORRECTION", "修正与来源不匹配")
            record = command.model_dump()
            record["source_sha256"] = run["source"]["sha256"]
            run["corrections"].append(record)
            # Correction is a proposal until a fresh S2 audit passes.
            run["events"].append({"state": "CORRECTION_PROPOSED", "atom_id": command.atom_id})
        return store.operation(run_id, command.revision, command.operation_id, apply)

    @app.get("/api/runs/{run_id}/view")
    async def view(run_id: str, page_idx: int = Query(0, ge=0)):
        run = store.get("run", run_id)
        atoms, structure, fragments, visual, tasks = [], None, [], None, []
        if run["stages"]["S1"]["state"] == "PASSED":
            atoms, _ = engine.pipeline.normalized(run)
        if run["stages"]["S2"]["state"] == "PASSED":
            atoms = store.artifact(run, "S2", "content.json")["atoms"]
            visual = store.artifact(run, "S2", "visual_review/index.json")
        else:
            pending = store.directory(run_id, run["revision"], "S2") / "visual_work/index.json"
            if pending.exists():
                visual = json.loads(pending.read_text(encoding="utf-8"))
        if run["stages"]["S3"]["state"] == "PASSED":
            structure = store.artifact(run, "S3", "structure.json")
        if run["stages"]["S5"]["state"] == "PASSED":
            tasks = store.artifact(run, "S5", "tasks.json")["tasks"]
        if run["stages"]["S6"]["state"] == "PASSED":
            fragments = store.artifact(run, "S6", "fragments/index.json")["fragments"]
            visual = store.artifact(run, "S6", "visual_review/index.json")
        elif tasks:
            for task in tasks:
                pending = store.directory(run_id, run["revision"], "S6") / "conversion_work" / (task["task_id"] + ".json")
                if pending.exists():
                    saved = json.loads(pending.read_text(encoding="utf-8"))
                    if saved.get("status") == "PASSED" and saved.get("input_hash") == task["input_hash"]:
                        fragments.append(saved["fragment"])
        if run["stages"]["S7"]["state"] == "PASSED":
            fragments = store.artifact(run, "S7", "fragments/index.json")["fragments"]
            structure = store.artifact(run, "S7", "merged_structure.json")
        tex, compile_report = None, None
        path = store.directory(run_id, run["revision"], "S7") / "candidate"
        if run["stages"]["S7"]["state"] == "PASSED":
            path = store.artifact_dir(run, "S7")
        if (path / "tex/body.tex").exists():
            tex = (path / "tex/body.tex").read_text(encoding="utf-8")
            if (path / "compile_report.json").exists():
                compile_report = json.loads((path / "compile_report.json").read_text(encoding="utf-8"))
        parser_progress = []
        for receipt_path in sorted((store.root / "runs" / run_id / "parse-work").glob("*/receipt.json")):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            parser_progress.append({
                "chunk_id": receipt_path.parent.name, "state": receipt["state"],
                "remote_completed": receipt.get("remote_state") == "COMPLETED" or
                    receipt.get("phase") == "download" or receipt["state"] == "COMPLETED",
                "downloaded": receipt["state"] == "COMPLETED",
                "last_error": receipt.get("last_error"),
            })
        return {"run": run, "parser_progress": parser_progress,
                "atoms": [a for a in atoms if a["page_idx"] == page_idx],
                "structure": structure, "fragments": fragments, "visual": visual, "tasks": tasks,
                "locations": {a["atom_id"]: {"page_idx": a["page_idx"], "bbox": a["bbox"]} for a in atoms},
                "tex": tex, "compile_report": compile_report}

    @app.get("/api/runs/{run_id}/export")
    async def export(run_id: str, candidate: bool = False):
        run = store.get("run", run_id)
        accepted = run["state"] in ("BOOK_ACCEPTED", "SAMPLE_ACCEPTED", "FIXTURE_PASSED")
        if not accepted and not candidate:
            raise WorkflowError("NOT_ACCEPTED", "当前修订尚未验收；可选择下载候选代码")
        if run["stages"]["S7"]["state"] == "PASSED":
            base = store.artifact_dir(run, "S7")
        else:
            base = store.directory(run_id, run["revision"], "S7") / "candidate"
        if not (base / "tex/main.tex").is_file():
            raise WorkflowError("NO_TEX", "尚未生成 TeX 工程", 404)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in (base / "tex").rglob("*"):
                if path.is_file() and path.suffix.lower() in (".tex", ".json", ".pdf", ".png", ".jpg", ".jpeg"):
                    archive.write(path, path.relative_to(base).as_posix())
            if (base / "compile_report.json").exists():
                archive.write(base / "compile_report.json", "compile_report.json")
            archive.writestr("status.json", json.dumps({
                "state": run["state"], "scope": run["scope"], "revision": run["revision"], "accepted": accepted}, ensure_ascii=False))
            if accepted:
                directory = store.artifact_dir(run, "S8")
                for name in ("audit.json", "result_manifest.json"):
                    archive.write(directory / name, name)
        label = "accepted" if accepted else "candidate"
        return Response(stream.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="bookanalyst-{run_id[:8]}-r{run["revision"]}-{label}.zip"'})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app
