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
from .config import DEFAULT_SETTINGS, validate_settings
from .store import Store, WorkflowError
from .models import RunCreate, Operation, STAGES, STAGE_NAMES, WORKFLOW_VERSION
from .llm import Providers
from .engine import Engine
from .pdf import inspect_pdf


def create_app(workspace=None, data_dir=None):
    workspace = Path(workspace or Path.cwd()).resolve()
    store = Store(data_dir or workspace / ".bookanalyst")
    try:
        previous = store.get("settings", "main")
        if "mineru" in previous:
            store.put("settings", "pre-v6", previous)
            store.put("settings", "main", {k: previous[k] for k in DEFAULT_SETTINGS})
    except WorkflowError:
        store.put("settings", "main", copy.deepcopy(DEFAULT_SETTINGS))
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

    app = FastAPI(title="BookAnalyst", version="0.7.0", lifespan=lifespan)
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

    def books():
        return [
            {k: b[k] for k in ("id", "title", "page_count", "size_bytes")}
            for b in store.list("book")
        ]

    @app.get("/api/bootstrap")
    async def bootstrap():
        return {
            "token": token,
            "version": "0.7.0",
            "books": books(),
            "runs": [store.summary(r) for r in store.list("run")],
            "stages": dict(zip(STAGES, STAGE_NAMES)),
        }

    @app.get("/api/settings")
    async def settings():
        return store.get("settings", "main")

    @app.put("/api/settings")
    async def save_settings(request: Request):
        return store.put("settings", "main", validate_settings(await request.json()))

    @app.get("/api/connections/{cid}/status")
    async def connection(cid: str):
        return await providers.status(cid)

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
        return FileResponse(
            store.get("book", bid)["path"], media_type="application/pdf"
        )

    @app.post("/api/runs")
    async def create(command: RunCreate):
        book = store.get("book", command.book_id)
        if command.end_page > book["page_count"] or any(
            p < 1 or p > book["page_count"] for p in command.setup_pages
        ):
            raise WorkflowError("PAGE_SCOPE", "页码超出原书范围", 422)
        return store.summary(store.create_run(command.model_dump(), book))

    @app.get("/api/runs/{rid}/status")
    async def status(rid: str):
        run = store.get("run", rid)
        result = store.summary(run)
        result["tasks"] = dict(Counter(t["state"] for t in store.tasks(rid, "convert")))
        return result

    @app.get("/api/runs/{rid}/tasks")
    async def tasks(rid: str):
        store.get("run", rid)
        return store.tasks(rid)

    @app.post("/api/runs/{rid}/start")
    async def start(rid: str, command: Operation):
        return await engine.start(rid, command)

    @app.post("/api/runs/{rid}/pause")
    async def pause(rid: str, command: Operation):
        engine.pause(rid, command)
        return {"ok": True}

    @app.get("/api/runs/{rid}/content")
    async def content(rid: str, page: int = Query(1, ge=1)):
        run = store.get("run", rid)
        if run.get("workflow_version") != WORKFLOW_VERSION:
            return {"legacy": True, "tex": "", "page": page}
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
        rid: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)
    ):
        store.get("run", rid)
        calls = store.calls(rid)
        totals = Counter(c["metadata"].get("purpose", c["kind"]) for c in calls)
        rows = []
        for c in list(reversed(calls))[offset : offset + limit]:
            m = c["metadata"]
            rows.append(
                {
                    "id": c["id"],
                    "state": c["state"],
                    "purpose": m.get("purpose", c["kind"]),
                    "model": m.get("model_id"),
                    "effort": m.get("reasoning_effort"),
                    "usage": m.get("usage"),
                    "seconds": round(
                        m.get("finished_at", time.time()) - m.get("started_at", 0), 2
                    ),
                    "error": m.get("error_code"),
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
                actual["total"] += u.get(
                    "total_tokens", u.get("input_tokens", 0) + u.get("output_tokens", 0)
                )
        return {"total": len(calls), "purposes": totals, "rows": rows, "tokens": actual}

    def output_dir(run):
        if run.get("workflow_version") in ("0.6", WORKFLOW_VERSION):
            return store.directory(run["id"]) / "tex"
        root = store.root / "runs" / run["id"]
        paths = list(root.glob("r*/S7/tex/main.tex")) + list(
            root.glob("r*/S7/candidate/tex/main.tex")
        )
        if not paths:
            raise WorkflowError("NO_OUTPUT", "此运行尚无 TeX 产物", 404)
        return max(paths, key=lambda p: int(p.relative_to(root).parts[0][1:])).parent

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
