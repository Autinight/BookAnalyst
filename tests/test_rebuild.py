"""Program behavior checks; real-model validation is recorded separately."""

import asyncio, json, shutil
import pytest
from fastapi.testclient import TestClient
from bookanalyst.models import RunCreate, Operation
from bookanalyst.documents import (
    validate_conversion,
    apply_seam,
    render_document,
    safe_tex,
)
from bookanalyst.store import WorkflowError, atomic_json
from bookanalyst.tex import compile_tex

BOOK = "wang-2022-g-invariant-min-max"


def config(**kwargs):
    return RunCreate(book_id=BOOK, end_page=6, **kwargs).model_dump()


def result(pages):
    return {
        "pages": [{"page": p, "tex": f"Original page {p}."} for p in pages],
        "headings": [],
        "assets": [],
        "head": "closed",
        "tail": "closed",
    }


def make_run(app, **kwargs):
    return app.state.store.create_run(
        config(**kwargs), app.state.store.get("book", BOOK)
    )


def test_small_bootstrap_excludes_legacy_history_and_page_content(app):
    store = app.state.store
    run = make_run(app)
    run["history"] = ["a" * 1_000_000]
    run["whole_tex"] = "b" * 1_000_000
    store.put("run", run["id"], run)
    with TestClient(app) as client:
        data = client.get("/api/bootstrap")
        assert len(data.content) < 5000
        status = client.get(f"/api/runs/{run['id']}/status")
        assert len(status.content) < 2000
        assert "history" not in status.json() and "whole_tex" not in status.json()
        assert client.post("/api/runs", json={}).status_code == 403


def test_fixed_page_ownership_at_book_scale(app):
    cfg = RunCreate(
        book_id="elliptic-pde-second-order", end_page=532, pages_per_task=3
    ).model_dump()
    store = app.state.store
    r = store.create_run(cfg, store.get("book", cfg["book_id"]))
    tasks = store.tasks(r["id"], "convert")
    assert len(tasks) == 178
    assert [p for t in tasks for p in t["pages"]] == list(range(1, 533))
    assert tasks[-1]["pages"] == [532]


def test_content_endpoint_returns_only_requested_page(app):
    r = make_run(app)
    store = app.state.store
    base = store.directory(r["id"])
    atomic_json(base / "batches/batch-0001.json", result([1, 2, 3]))
    with TestClient(app) as c:
        response = c.get(f"/api/runs/{r['id']}/content?page=2")
        assert response.json()["tex"] == "Original page 2."
        assert "Original page 1." not in response.text
        assert c.get(f"/api/runs/{r['id']}/content?page=10").status_code == 422


@pytest.mark.parametrize(
    "tex",
    [
        r"\(x\gg y\)",
        r"\(x\nearrow y\)",
        r"\(A\Subset B\)",
        r"\begin{aligned}x&=1\\y&=2\end{aligned}",
    ],
)
def test_normal_math_not_rejected_by_vocabulary(tex):
    safe_tex(tex)


@pytest.mark.parametrize(
    "tex",
    [
        r"\input{private}",
        r"\write18{command}",
        r"\csname input\endcsname{private}",
        "^^69nput",
    ],
)
def test_source_cannot_read_files_or_execute(tex):
    with pytest.raises(WorkflowError):
        safe_tex(tex)


def test_page_scope_and_figure_scope():
    r = result([1, 2])
    validate_conversion(r, [1, 2])
    with pytest.raises(WorkflowError):
        validate_conversion(r, [2, 3])
    r["assets"] = [{"id": "a", "page": 3, "bbox": [0, 0, 1, 1]}]
    r["pages"][0]["tex"] = r"\BAFigure{a}"
    with pytest.raises(WorkflowError):
        validate_conversion(r, [1, 2])


def test_boundary_patch_changes_only_exact_suffix_and_prefix():
    assert apply_seam(
        "prefix contin-",
        "uation suffix",
        {
            "left_suffix": "contin-",
            "right_prefix": "uation",
            "replacement": "continuation",
        },
    ) == ("prefix continuation", " suffix")
    with pytest.raises(WorkflowError):
        apply_seam(
            "abc",
            "def",
            {"left_suffix": "xxx", "right_prefix": "d", "replacement": "new"},
        )
    with pytest.raises(WorkflowError):
        apply_seam(
            r"\BAHeading{h}",
            "text",
            {"left_suffix": r"\BAHeading{h}", "right_prefix": "", "replacement": ""},
        )


@pytest.mark.asyncio
async def test_visual_conversion_uses_only_owned_pages_and_reuses_success(
    app, monkeypatch
):
    engine = app.state.engine
    run = make_run(app)
    task = engine.store.tasks(run["id"], "convert")[0]
    calls = []

    async def ask(run, key, purpose, payload, schema, pages, extra_images=()):
        assert not extra_images
        calls.append((payload, pages))
        return result(pages)

    monkeypatch.setattr(engine, "ask", ask)
    a = await engine.convert(run, task, {"rules": "Preserve author numbers"})
    b = await engine.convert(run, task, {"rules": "Preserve author numbers"})
    assert a == b and len(calls) == 1
    assert calls[0][1] == [1, 2, 3]
    assert "neighbors" not in calls[0][0] and "source" not in calls[0][0]


@pytest.mark.asyncio
async def test_image_cache_does_not_render_twice(app, monkeypatch):
    import bookanalyst.engine as module
    from bookanalyst.pdf import render_page

    calls = []

    def render(*args):
        calls.append(args)
        return render_page(*args)

    monkeypatch.setattr(module, "render_page", render)
    book = app.state.store.get("book", BOOK)
    a = await app.state.engine.image(book, 1, 55)
    b = await app.state.engine.image(book, 1, 55)
    assert a == b and len(calls) == 1


@pytest.mark.asyncio
async def test_seven_stages_compile_and_resume_without_duplicate_requests(
    app, monkeypatch
):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def resolve(binding, require_image=False):
        return binding | {"model_id": "test-model"}, {}

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append((purpose, payload["page_images"]))
        if purpose == "setup":
            return {
                "documentclass": "article",
                "public_tex": "",
                "title": "",
                "author": "",
                "rules": "",
                "toc": [],
                "numbering": {"rules": [], "initial": []},
            }
        assert purpose != "compile_repair", "A clean build needs no final agent request"
        return result(payload["owned_pages"])

    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)
    engine.store.change(run["id"], lambda r: r.update(state="RUNNING"))
    await engine.execute(run["id"])
    current = engine.store.get("run", run["id"])
    assert current["state"] == "COMPLETED", current
    assert all(s == "PASSED" for s in current["stages"].values()) and len(calls) == 3
    engine.store.change(run["id"], lambda r: r.update(state="RUNNING"))
    await engine.execute(run["id"])
    assert len(calls) == 3
    assert (engine.store.directory(run["id"]) / "tex/main.pdf").exists()


@pytest.mark.asyncio
async def test_compiler_rejects_missing_glyph(app, tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    setup = {"documentclass": "article"}
    data = result([1])
    data["task_id"] = "batch-0001"
    data["pages"][0]["tex"] = "Missing ∂ symbol."
    files, mapping = render_document(setup, [data], [])
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "FAILED" and "Missing character" in report["log"]


@pytest.mark.asyncio
async def test_receipt_recovery_reuses_completed_upstream_response(app, monkeypatch):
    store = app.state.store
    providers = app.state.engine.providers
    run = make_run(app)
    run["config"]["model"]["model_id"] = "test-model"
    store.put("run", run["id"], run)
    sent = []

    async def subscription(*args):
        sent.append(1)
        return '{"value":1}', {
            "tokens": {
                "total": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}
            }
        }

    monkeypatch.setattr(providers, "_subscription", subscription)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    assert await providers.generate(run, "model", "setup", "same prompt", schema) == {
        "value": 1
    }
    call = store.calls(run["id"])[0]
    store.finish_call(call["id"], "RESULT_UNKNOWN")
    report = await providers.reconcile(run)
    assert report[0]["status"] == "RECOVERED"
    assert await providers.generate(run, "model", "setup", "same prompt", schema) == {
        "value": 1
    }
    assert len(sent) == 1 and len(store.calls(run["id"])) == 1
    assert (
        store.calls(run["id"])[0]["metadata"]["usage"]["tokens"]["total"]["totalTokens"]
        == 12
    )


@pytest.mark.asyncio
async def test_validation_repairs_automatically_then_resume_reuses_result(
    app, monkeypatch
):
    engine = app.state.engine
    store = app.state.store
    run = make_run(app)
    run["config"]["model"]["model_id"] = "test-model"
    store.put("run", run["id"], run)
    task = store.tasks(run["id"], "convert")[0]
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        calls.append(json.loads(prompt))
        return result([1] if len(calls) <= 3 else [1, 2, 3])

    monkeypatch.setattr(engine.providers, "generate", generate)
    await engine.convert(run, task, {"rules": ""})
    store.change(run["id"], lambda r: r.update(revision=r["revision"] + 1))
    recovered = await engine.convert(store.get("run", run["id"]), task, {"rules": ""})
    assert len(calls) == 4 and len(recovered["pages"]) == 3


def test_final_content_shows_compiler_result(app):
    run = make_run(app)
    store = app.state.store
    base = store.directory(run["id"])
    atomic_json(base / "batches/batch-0001.json", result([1, 2, 3]))
    atomic_json(
        base / "final-pages/2.json", {"page": 2, "tex": "Repaired final content"}
    )
    store.change(run["id"], lambda r: r.update(state="COMPLETED"))
    with TestClient(app) as c:
        data = c.get(f"/api/runs/{run['id']}/content?page=2").json()
        assert data["final"] and data["tex"] == "Repaired final content"


def test_start_operation_is_atomic_and_idempotent(app):
    from concurrent.futures import ThreadPoolExecutor

    run = make_run(app)
    store = app.state.store
    calls = []
    command = Operation(revision=1, operation_id="operation-idempotent")

    def callback(r):
        calls.append(1)
        r["revision"] += 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: store.operation(run["id"], command, callback), range(2))
        )
    assert len(calls) == 1 and results[0] == results[1] and results[0]["revision"] == 2


def test_seam_receives_only_inherited_environment_names():
    from bookanalyst.documents import open_environments

    assert open_environments(
        [r"\begin{proof} earlier private prose", r"\begin{align*} a=b \end{align*}"]
    ) == ["proof"]
    assert open_environments([r"\begin{proof}", r"\end{proof}"]) == []


def test_request_totals_keep_actual_usage_and_show_newest_first(app):
    store = app.state.store
    run = make_run(app)
    first = store.reserve(run["id"], 1, "llm", 1, {"purpose": "setup"})
    store.finish_call(
        first,
        "COMPLETED",
        usage={
            "tokens": {
                "total": {
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "reasoningOutputTokens": 2,
                    "totalTokens": 14,
                }
            }
        },
    )
    second = store.reserve(run["id"], 1, "llm", 1, {"purpose": "convert"})
    with TestClient(app) as c:
        data = c.get(f"/api/runs/{run['id']}/requests").json()
        assert data["rows"][0]["id"] == second and data["tokens"]["total"] == 14
        assert (
            data["tokens"]["reported_calls"] == 1 and data["tokens"]["reasoning"] == 2
        )


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.asyncio
async def test_custom_api_sends_owned_images_without_token_limits(
    app, tmp_path, protocol
):
    import httpx
    from bookanalyst.llm import Providers
    from bookanalyst.config import DEFAULT_SETTINGS
    import copy

    captured = []

    def handler(request):
        data = json.loads(request.content)
        captured.append(data)
        if protocol == "responses":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "{}"}],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    conn = copy.deepcopy(DEFAULT_SETTINGS["connections"]["custom_api"])
    conn.update(base_url="http://model.test/v1", protocol=protocol, auth_mode="none")
    image = tmp_path / "owned.png"
    image.write_bytes(b"owned page bytes")
    providers = Providers(app.state.store, tmp_path, httpx.MockTransport(handler))
    text, usage = await providers._custom(
        conn, {"model_id": "custom-vision"}, "convert", {"type": "object"}, [image]
    )
    assert text == "{}" and usage["total_tokens"] == 5
    payload = captured[0]
    assert payload["model"] == "custom-vision"
    if protocol == "responses":
        assert payload["reasoning"] == {"effort": "medium"}
    else:
        assert payload["reasoning_effort"] == "medium"
        assert payload["response_format"] == {"type": "json_object"}
    assert not any(k in payload for k in ("tools", "max_tokens", "max_output_tokens"))
    assert "data:image/png;base64,b3duZWQgcGFnZSBieXRlcw==" in json.dumps(payload)


@pytest.mark.asyncio
async def test_pause_prevents_new_request_after_waiting_for_connection(
    app, monkeypatch
):
    providers = app.state.engine.providers
    store = app.state.store
    run = make_run(app)
    run["config"]["model"]["model_id"] = "test-model"
    store.put("run", run["id"], run)
    limit = asyncio.Semaphore(0)
    providers.limits[("openai_subscription", 2)] = limit
    task = asyncio.create_task(
        providers.generate(run, "model", "convert", "prompt", {"type": "object"})
    )
    await asyncio.sleep(0)
    store.change(run["id"], lambda r: r.update(pause_requested=True))
    limit.release()
    with pytest.raises(WorkflowError, match="停止派发"):
        await task
    assert store.calls(run["id"]) == []
