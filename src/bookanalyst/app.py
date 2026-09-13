"""Local API: small status responses, on-demand document data, cached images."""

import asyncio, copy, json, secrets, time, uuid, zipfile
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import DEFAULT_SETTINGS, prepare_settings
from .store import Store, WorkflowError
from .models import RunCreate, Operation, StartOperation, RunModelUpdate, STAGES, STAGE_NAMES, WORKFLOW_VERSION
from .llm import Providers
from .engine import Engine
from .pdf import inspect_pdf
from .request_history import group_requests, validation_history
from .reference_review import review_records, review_response
from .templates import register_template_routes
from .library import register_library_routes
from .image_repair import register_image_routes
from .library_outputs import output_directory, retained_directory, public_result
from .model_config import MODEL_STAGES, settings_models


def create_app(workspace=None, data_dir=None):
    workspace = Path(workspace or Path.cwd()).resolve()
    store = Store(data_dir or workspace / ".bookanalyst")
    try:
        previous = store.get("settings", "main")
        if "mineru" in previous:
            store.put("settings", "pre-v6", previous)
            store.put("settings", "main", {k: previous.get(k, copy.deepcopy(v)) for k, v in DEFAULT_SETTINGS.items()})
    except WorkflowError:
        store.put("settings", "main", copy.deepcopy(DEFAULT_SETTINGS))
    saved = store.get("settings", "main")
    if not saved.get("stage_models"):
        recent = next((r["config"] for r in store.list("run") if r.get("workflow_version") == WORKFLOW_VERSION), None)
        saved["stage_models"] = settings_models(saved, recent)
        saved.setdefault("llm_concurrency", (recent or {}).get("llm_concurrency", 2))
        store.put("settings", "main", saved)
    manifest = workspace / "tests/data/books.json"
    if manifest.exists():
        for item in json.loads(manifest.read_text(encoding="utf-8"))["books"]:
            try:
                store.get("book", item["id"])
            except WorkflowError:
                path = (manifest.parent / item["path"]).resolve()
                meta = inspect_pdf(path)
                if meta["sha256"] != item["sha256"]:
                    raise RuntimeError("Test document hash mismatch")
                store.put(
                    "book",
                    item["id"],
                    dict(id=item["id"], title=item["title"], path=str(path), **meta),
                )
    providers = Providers(store, workspace)
    engine = Engine(store, workspace, providers)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await engine.close()

    app = FastAPI(title="BookAnalyst", version="0.7.1", lifespan=lifespan)
    app.state.store = store
    app.state.engine = engine
    app.state.workspace = workspace
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_only(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"message": "仅接受本地页面操作"}, status_code=403)
            if not secrets.compare_digest(
                request.headers.get("x-bookanalyst-token", ""), token
            ):
                return JSONResponse(
                    {"message": "页面会话已更新，请刷新"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = (
                "no-store" if request.url.path.startswith("/api") else "no-cache"
            )
        return response

    @app.exception_handler(WorkflowError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status
        )

    register_library_routes(app, store)
    register_template_routes(app, store, engine)
    register_image_routes(app, store, engine)

    def books(deleted=False):
        return [
            {k: b[k] for k in ("id", "title", "page_count", "size_bytes")} | {"result": public_result(b)}
            for b in store.list("book", limit=10000)
            if bool(b.get("deleted")) == deleted
        ]

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "token": token,
            "version": "0.7.1",
            "books": books(),
            "deleted_books": books(deleted=True),
            "library_management": True,
            "live_concurrency_updates": True,
            "runs": [store.summary(r) for r in store.list("run")],
            "stages": dict(zip(STAGES, STAGE_NAMES)),
            "model_stages": MODEL_STAGES,
        }

    def public_settings():
        value = store.get("settings", "main")
        value["stage_models"] = settings_models(value)
        value.setdefault("image_repair_concurrency", value.get("llm_concurrency", 2))
        for name, conn in value["connections"].items():
            conn.pop("max_in_flight", None)
            if conn["kind"] == "openai_compatible":
                conn["api_key_configured"] = bool(providers.api_key(name, conn))
                conn.pop("api_key_env", None)
        return value

    @app.get("/api/settings")
    async def settings():
        return public_settings()

    @app.put("/api/settings")
    async def save_settings(request: Request):
        value, credentials = prepare_settings(
            await request.json(), store.get("settings", "main")
        )
        store.save_settings(value, credentials)
        return public_settings()

    @app.get("/api/connections/{cid}/status")
    async def connection(cid: str):
        return await providers.status(cid)

    @app.post("/api/connections/{cid}/test")
    async def test_connection(cid: str):
        return await providers.test(cid)

    @app.post("/api/connections/{cid}/login")
    async def login(cid: str):
        return await providers.login(cid)

    @app.post("/api/books")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "Document.pdf").name
        key = uuid.uuid4().hex
        directory = store.root / "books" / key
        directory.mkdir(parents=True)
        path = directory / "source.pdf"
        try:
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    output.write(chunk)
            meta = await asyncio.to_thread(inspect_pdf, path)
        finally:
            await file.close()
        book = dict(id=key, title=name.removesuffix(".pdf"), path=str(path), **meta)
        store.put("book", key, book)
        return {k: book[k] for k in ("id", "title", "page_count", "size_bytes")}

    @app.get("/api/books/{bid}/image")
    async def page_image(
        bid: str, page: int = Query(1, ge=1), dpi: int = Query(110, ge=40, le=240)
    ):
        book = store.get("book", bid)
        if page > book["page_count"]:
            raise WorkflowError("INVALID_PAGE", "页码超出范围", 422)
        path = await engine.image(book, page, dpi)
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @app.get("/api/books/{bid}/pdf")
    async def pdf(bid: str):
        book = store.get("book", bid)
        path = retained_directory(store, book) / "main.pdf" if book.get("retained_result") else book["path"]
        return FileResponse(path, media_type="application/pdf")

    @app.post("/api/runs")
    async def create(command: RunCreate):
        book = store.get("book", command.book_id)
        if book.get("deleted"):
            raise WorkflowError("BOOK_DELETED", "请先从回收站恢复这本书", 409)
        if command.end_page > book["page_count"] or any(
            p < 1 or p > book["page_count"] for p in command.setup_pages
        ):
            raise WorkflowError("PAGE_SCOPE", "页码超出原书范围", 422)
        config = command.model_dump()
        if not ({"model", "structure_effort"} & command.model_fields_set):
            saved = store.get("settings", "main")
            config["stage_models"] = settings_models(saved)
            if "llm_concurrency" not in command.model_fields_set:
                config["llm_concurrency"] = saved.get("llm_concurrency", 2)
        return store.summary(store.create_run(config, book))

    @app.get("/api/runs/{rid}/status")
    def status(rid: str):
        run = store.get("run", rid)
        result = store.summary(run)
        result["reference_review_count"] = len(review_records(store.directory(rid)))
        result["tasks"] = dict(Counter(t["state"] for t in store.tasks(rid, "convert")))
        sessions = [store.directory(rid) / name for name in ("codex-compiler.json", "pi-compiler.json")]
        sessions = [p for p in sessions if p.exists()]
        if sessions:
            session = max(sessions, key=lambda p: p.stat().st_mtime_ns)
            value = json.loads(session.read_text(encoding="utf-8"))
            result["compiler"] = {k: value.get(k) for k in ("thread_id", "status", "activity")}
            result["compiler"]["agent"] = "pi" if session.name == "pi-compiler.json" else "codex"
        return result

    @app.get("/api/runs/{rid}/reference-review")
    async def user_reference_review(rid: str, download: bool = False):
        run = store.get("run", rid)
        return review_response(review_records(store.directory(rid)), run["source"]["title"],
                               f"/api/books/{run['source']['id']}/source", download)

    @app.get("/api/runs/{rid}/tasks")
    def tasks(rid: str):
        store.get("run", rid)
        return store.tasks(rid)

    @app.post("/api/runs/{rid}/start")
    async def start(rid: str, command: StartOperation):
        return await engine.start(rid, command)

    @app.post("/api/runs/{rid}/pause")
    def pause(rid: str, command: Operation):
        engine.pause(rid, command)
        return {"ok": True}

    @app.post("/api/runs/{rid}/model")
    async def rebind(rid: str, command: RunModelUpdate):
        return await engine.rebind(rid, command)

    @app.get("/api/runs/{rid}/content")
    async def content(rid: str, page: int = Query(1, ge=1)):
        run = store.get("run", rid)
        if run.get("workflow_version") != WORKFLOW_VERSION:
            return {"legacy": True, "tex": "", "page": page}
        if run.get("kind") in ("template", "manual_layout"):
            if not run["config"]["start_page"] <= page <= run["config"]["end_page"]:
                raise WorkflowError("PAGE_SCOPE", "页面不属于本次运行", 422)
            project = store.directory(rid) / "tex"
            body = (project / "body.tex").read_text(encoding="utf-8")
            rows = engine.project_pages({"body.tex": body}, [page], strict=False)
            return next((p for p in rows if p["page"] == page), {"page": page, "tex": ""}) | {
                "task": {"id": "finish-project", "state": run["state"]}, "final": run["state"] == "COMPLETED"}
        task = next(
            (t for t in store.tasks(rid, "convert") if page in t["pages"]), None
        )
        if not task:
            raise WorkflowError("PAGE_SCOPE", "页面不属于本次运行", 422)
        base = store.directory(rid)
        final = base / "final-pages" / f"{page}.json"
        if run["state"] == "COMPLETED" and final.exists():
            return json.loads(final.read_text(encoding="utf-8")) | {
                "task": task,
                "final": True,
            }
        path = base / "batches" / f"{task['id']}.json"
        if not path.exists():
            return {"tex": "", "page": page, "task": task}
        result = json.loads(path.read_text(encoding="utf-8"))
        return {
            "tex": next(p["tex"] for p in result["pages"] if p["page"] == page),
            "page": page,
            "task": task,
        }

    @app.get("/api/runs/{rid}/requests")
    async def requests(
        rid: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
        grouped: bool = False,
    ):
        run = store.get("run", rid)
        tasks = store.tasks(rid)
        calls = await asyncio.to_thread(validation_history, store.calls(rid), store.directory(rid), tasks)
        totals = Counter(c["metadata"].get("purpose", c["kind"]) for c in calls)
        rows = []
        for c in reversed(calls):
            m = c["metadata"]
            rows.append(
                {
                    "id": c["id"],
                    "task_id": m.get("task_id"),
                    "input_hash": m.get("input_hash"),
                    "attempt": m.get("attempt", 1),
                    "repair": m.get("repair", False),
                    "validation_state": m.get("validation_state"),
                    "validation_error": m.get("validation_error"),
                    "retry_exhausted": m.get("retry_exhausted", False),
                    "state": c["state"],
                    "purpose": m.get("purpose", c["kind"]),
                    "phase": m.get("phase"),
                    "model": m.get("model_id"),
                    "effort": m.get("reasoning_effort"),
                    "usage": m.get("usage"),
                    "seconds": round(
                        m.get("finished_at", time.time()) - m.get("started_at", 0), 2
                    ),
                    "error": m.get("error_code"),
                    "error_message": m.get("error_message"),
                }
            )
        actual = {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cached_input": 0,
            "total": 0,
            "reported_calls": 0,
        }
        for c in calls:
            u = c["metadata"].get("usage") or {}
            t = (u.get("tokens") or {}).get("total")
            if t:
                actual["reported_calls"] += 1
                for key, upstream in [
                    ("input", "inputTokens"),
                    ("output", "outputTokens"),
                    ("reasoning", "reasoningOutputTokens"),
                    ("cached_input", "cachedInputTokens"),
                    ("total", "totalTokens"),
                ]:
                    actual[key] += t.get(upstream, 0)
            elif "input_tokens" in u or "prompt_tokens" in u:
                actual["reported_calls"] += 1
                actual["input"] += u.get("input_tokens", u.get("prompt_tokens", 0))
                actual["output"] += u.get(
                    "output_tokens", u.get("completion_tokens", 0)
                )
                actual["reasoning"] += u.get("reasoning_tokens", 0)
                actual["cached_input"] += u.get("cached_input_tokens", 0)
                actual["total"] += u.get(
                    "total_tokens", u.get("input_tokens", 0) + u.get("output_tokens", 0)
                )
        states = {}
        if grouped:
            rows, states = group_requests(rows, run, tasks)
        return {
            "total": len(rows), "request_total": len(calls), "purposes": totals,
            "rows": rows[offset : offset + limit], "tokens": actual, "states": states,
        }

    def output_dir(run):
        return output_directory(store, run)

    @app.get("/api/runs/{rid}/pdf")
    async def result_pdf(rid: str):
        run = store.get("run", rid)
        if (
            run.get("workflow_version") in ("0.6", WORKFLOW_VERSION)
            and run["state"] != "COMPLETED"
        ):
            raise WorkflowError("NOT_COMPLETE", "当前编译尚未通过")
        path = output_dir(run) / "main.pdf"
        if not path.exists():
            raise WorkflowError("NO_OUTPUT", "尚未生成 PDF", 404)
        return FileResponse(path, media_type="application/pdf")

    @app.get("/api/runs/{rid}/export")
    async def export(rid: str):
        run = store.get("run", rid)
        base = output_dir(run)
        if not (base / "main.tex").exists():
            raise WorkflowError("NO_OUTPUT", "尚无 TeX 工程", 404)
        archive = store.root / "exports" / f"{rid}-{run['revision']}.zip"
        archive.parent.mkdir(exist_ok=True)

        def write():
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                for p in base.rglob("*"):
                    if p.is_file() and p.suffix in (
                        ".tex",
                        ".json",
                        ".pdf",
                        ".png",
                        ".jpg",
                    ):
                        z.write(p, p.relative_to(base))
                z.writestr(
                    "status.json", json.dumps(store.summary(run), ensure_ascii=False)
                )

        await asyncio.to_thread(write)
        return FileResponse(
            archive, filename=archive.name, media_type="application/zip"
        )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app
