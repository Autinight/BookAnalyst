"""Isolated image jobs: crop verification, resumability, provenance and publishing."""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bookanalyst.figure_assets import MANIFEST, image_inventory, save_manifest, valid_box
from bookanalyst.image_repair import ImageRepairCreate, create_image_run
from bookanalyst.library_outputs import retain_run, retained_directory
from bookanalyst.llm import parse_json
from bookanalyst.store import WorkflowError, atomic_json, atomic_text
from bookanalyst.templates import TemplateApply, TemplateSpec, create_template_run, save_template
from test_library import headers
from test_library_outputs import completed
from test_rebuild import BOOK


def seed(app, count=2):
    ids = [f"batch-0001-assets-{i + 1}" for i in range(count)]
    run, project = completed(app, "% PDF page 1\nOriginal text.\n" + "\n".join(
        r"\begin{figure}\BAFigure{" + i + r"}\caption{An illustration}\end{figure}" for i in ids))
    atomic_text(project / "main.tex", r"\documentclass{article}\input{preamble}\begin{document}\input{body}\end{document}")
    atomic_text(project / "preamble.tex", "\\usepackage{graphicx}\n" +
                r"\newcommand{\BAFigure}[1]{\includegraphics[width=\linewidth,keepaspectratio]{assets/#1.png}}")
    assets = [{"id": i, "page": 1, "bbox": [0.1, 0.1, 0.5, 0.5]} for i in ids]
    for a in assets:
        Image.new("RGB", (40, 40), "gray").save(project / "assets" / (a["id"] + ".png"))
    atomic_json(project.parent / "batches/batch-0001.json", {"assets": assets})
    result = retain_run(app.state.store, run["id"])
    return result, assets


def create(app, result, assets):
    return create_image_run(app.state.engine, BOOK, ImageRepairCreate(
        result_id=result["id"], asset_ids=[a["id"] for a in assets], operation_id="image-test-operation"))


def wire(app, monkeypatch, responder):
    engine = app.state.engine
    async def resolve(binding, **kwargs):
        return dict(binding, model_id="test-vision"), {}
    async def generate(run, role, purpose, prompt, schema, images=()):
        assert purpose == "image_repair" and len(images) in (1, 2)
        payload = json.loads(prompt)
        response = await responder(payload, images)
        return parse_json(json.dumps(response), schema)
    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)


def answer(action="keep", bbox=None, reason="图形与内嵌标签完整"):
    return {"action": action, "bbox": bbox or [], "reason": reason}


def test_legacy_inventory_and_creation_are_read_only_and_idempotent(app):
    result, assets = seed(app)
    store = app.state.store
    original = retained_directory(store, store.get("book", BOOK))
    before = {p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob("*") if p.is_file()}
    _, rows = image_inventory(store, store.get("book", BOOK), original, result["run_id"])
    assert [a["id"] for a in rows] == [a["id"] for a in assets]
    assert all(a["repairable"] for a in rows)
    run = create(app, result, assets[:1])
    assert create(app, result, assets[:1])["id"] == run["id"]
    assert store.tasks(run["id"], "convert") == []
    assert run["stages"]["convert"] == "PASSED"
    assert store.get("book", BOOK)["retained_result"] == result
    assert {p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob("*") if p.is_file()} == before
    assert (store.directory(run["id"]) / "tex" / MANIFEST).is_file()


def test_images_api_requires_token_checks_scope_and_stale_results(app, monkeypatch):
    result, assets = seed(app)
    monkeypatch.setattr(app.state.engine, "launch", lambda rid: None)
    body = {"result_id": result["id"], "asset_ids": [assets[0]["id"]], "operation_id": "image-api-operation"}
    with TestClient(app) as client:
        auth = headers(client)
        listing = client.get(f"/api/books/{BOOK}/images").json()
        assert len(listing["images"]) == 2
        assert client.post(f"/api/books/{BOOK}/repair-images", json=body).status_code == 403
        assert client.post(f"/api/books/{BOOK}/repair-images", json=body | {"result_id": "stale"}, headers=auth).status_code == 409
        assert client.post(f"/api/books/{BOOK}/repair-images", json=body | {"asset_ids": ["../../escape"]}, headers=auth).status_code == 422
        response = client.post(f"/api/books/{BOOK}/repair-images", json=body, headers=auth)
        assert response.status_code == 200, response.text
        rid = response.json()["id"]
        assert client.post(f"/api/books/{BOOK}/repair-images", json=body, headers=auth).json()["id"] == rid
        assert client.get(f"/api/runs/{rid}/images").json()["images"][0]["state"] == "PENDING"
        assert client.get(f"/api/runs/{rid}/images/{assets[0]['id']}?version=before").status_code == 200
        assert client.get(f"/api/runs/{rid}/images/{assets[1]['id']}").status_code == 404
        assert client.get(f"/api/books/{BOOK}/images/{assets[0]['id']}?result_id=stale").status_code == 409


@pytest.mark.asyncio
async def test_verified_recrop_changes_only_copy_and_compiles_once(app, monkeypatch, tmp_path):
    result, assets = seed(app)
    store, engine = app.state.store, app.state.engine
    original = retained_directory(store, store.get("book", BOOK))
    original_crop = (original / "assets" / (assets[0]["id"] + ".png")).read_bytes()
    original_body = (original / "body.tex").read_bytes()
    run = create(app, result, assets)
    source = tmp_path / "page.png"
    Image.new("RGB", (200, 200), "white").save(source)
    calls, compiles = [], []
    async def image(*args): return source
    async def respond(payload, images):
        calls.append((payload["asset"]["id"], payload["phase"]))
        if payload["asset"]["id"] == assets[1]["id"]:
            return answer("uncertain", reason="需要核对图注，保留原图")
        if payload["phase"] == "check":
            return answer("recrop", [0.1, 0.2, 0.6, 0.8], "下方标签被截断")
        with Image.open(images[1]) as crop:
            assert crop.size == (100, 120)
        return answer()
    from bookanalyst.tex import compile_tex
    async def compile_project(project):
        compiles.append(True)
        return await compile_tex(project)
    monkeypatch.setattr(engine, "image", image)
    monkeypatch.setattr("bookanalyst.image_repair.compile_tex", compile_project)
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    current = store.get("run", run["id"])
    assert current["state"] == "COMPLETED", current.get("error")
    base = store.directory(run["id"])
    review = json.loads((base / "tex/image-review.json").read_text())
    assert [a["state"] for a in review["images"]] == ["FIXED", "DEFERRED"]
    assert len(calls) == 3 and len(compiles) == 1
    assert (original / "assets" / (assets[0]["id"] + ".png")).read_bytes() == original_crop
    assert (base / "tex/body.tex").read_bytes() == original_body
    assert store.get("book", BOOK)["retained_result"]["run_id"] == run["id"]
    with Image.open(base / "tex/assets" / (assets[0]["id"] + ".png")) as crop:
        assert crop.size == (100, 120)
        assert crop.info["dpi"] == pytest.approx((150, 150), abs=0.02)
    saved = retained_directory(store, store.get("book", BOOK))
    assert json.loads((saved / MANIFEST).read_text())["assets"][0]["bbox"] == [0.1, 0.2, 0.6, 0.8]


@pytest.mark.asyncio
async def test_rejected_new_crop_keeps_original_and_compile_failure_does_not_publish(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    base = store.directory(run["id"])
    before = (base / "tex/assets" / (assets[0]["id"] + ".png")).read_bytes()
    async def respond(payload, images):
        return answer("recrop", [0.1, 0.1, 0.8, 0.8]) if payload["phase"] == "check" else answer("uncertain", reason="新裁图仍缺标签")
    async def fail(project): return {"status": "FAILED", "code": "TEX_ERROR"}
    wire(app, monkeypatch, respond)
    monkeypatch.setattr("bookanalyst.image_repair.compile_tex", fail)
    monkeypatch.setattr("bookanalyst.image_repair.correct_legacy_size", lambda *args: None)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "NEEDS_REVIEW"
    assert store.get("book", BOOK)["retained_result"] == result
    assert (base / "tex/assets" / (assets[0]["id"] + ".png")).read_bytes() == before
    assert json.loads((base / "image-checks" / (assets[0]["id"] + ".json")).read_text())["state"] == "DEFERRED"
    async def no_repeat(*args): pytest.fail("Resume must reuse the completed image check")
    monkeypatch.setattr(engine, "ask", no_repeat)
    await engine.execute(run["id"])
    assert store.get("book", BOOK)["retained_result"] == result


@pytest.mark.asyncio
async def test_one_invalid_crop_does_not_block_other_images(app, monkeypatch):
    result, assets = seed(app)
    run = create(app, result, assets)
    async def respond(payload, images):
        return answer("recrop", [0, 0, 2, 1]) if payload["asset"]["id"] == assets[0]["id"] else answer()
    wire(app, monkeypatch, respond)
    await app.state.engine.execute(run["id"])
    store = app.state.store
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    report = json.loads((store.directory(run["id"]) / "tex/image-review.json").read_text())
    assert [a["state"] for a in report["images"]] == ["FAILED", "PASSED"]


@pytest.mark.asyncio
async def test_pause_before_verification_resumes_from_cached_check(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    engine, store = app.state.engine, app.state.store
    calls = []
    async def respond(payload, images):
        calls.append(payload["phase"])
        if payload["phase"] == "check":
            store.change(run["id"], lambda r: r.update(pause_requested=True))
            return answer("recrop", [0.1, 0.1, 0.8, 0.8])
        return answer()
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "PAUSED"
    store.change(run["id"], lambda r: r.update(pause_requested=False, state="RUNNING"))
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert calls == ["check", "verify"]


def test_missing_provenance_is_visible_but_not_repairable(app):
    result, assets = seed(app, 1)
    store = app.state.store
    (store.directory(result["run_id"]) / "batches/batch-0001.json").unlink()
    _, rows = image_inventory(store, store.get("book", BOOK), retained_directory(store, store.get("book", BOOK)), result["run_id"])
    assert len(rows) == 1 and rows[0]["available"] and not rows[0]["repairable"]
    with pytest.raises(WorkflowError, match="缺少来源"):
        create(app, result, assets)


def test_manifest_is_portable_and_existing_repaired_boxes_are_preserved(app):
    result, assets = seed(app, 1)
    store = app.state.store
    original = retained_directory(store, store.get("book", BOOK))
    source = store.get("book", BOOK)
    changed = [assets[0] | {"bbox": [0.2, 0.2, 0.8, 0.8]}]
    save_manifest(original, [{"assets": changed}], source)
    save_manifest(original, [{"assets": assets}], source)
    # Source run files are no longer needed once provenance is in the project.
    _, rows = image_inventory(store, source, original, "not-a-run")
    assert rows[0]["bbox"] == changed[0]["bbox"]


@pytest.mark.asyncio
async def test_normal_compilation_saves_manifest_without_visual_requests(app, monkeypatch):
    result, assets = seed(app, 1)
    store, engine = app.state.store, app.state.engine
    run = store.get("run", result["run_id"])
    async def unexpected(*args, **kwargs): pytest.fail("Normal compilation must not request image review")
    monkeypatch.setattr(engine, "ask", unexpected)
    await engine.compile(run, [{"pages": [{"page": 1, "tex": "Original"}], "assets": assets}], [], {})
    manifest = json.loads((store.directory(run["id"]) / "tex" / MANIFEST).read_text())
    assert manifest["assets"][0]["id"] == assets[0]["id"]
    assert manifest["source_sha256"] == run["source"]["sha256"]


def test_old_template_results_recover_original_crop_provenance(app):
    result, assets = seed(app, 1)
    store = app.state.store
    template = save_template(store, TemplateSpec(name="Example", files=[{"path": "main.tex", "content": "Example"}]))
    run = create_template_run(app.state.engine, BOOK, TemplateApply(template_id=template["id"], result_id=result["id"], operation_id="template-image-test"))
    store.change(run["id"], lambda r: r.update(state="COMPLETED"))
    retained = retain_run(store, run["id"])
    _, rows = image_inventory(store, store.get("book", BOOK), retained_directory(store, store.get("book", BOOK)), retained["run_id"])
    assert rows[0]["repairable"] and rows[0]["bbox"] == assets[0]["bbox"]


def test_image_job_cannot_replace_a_newer_book_result(app):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    newer, _ = completed(app, "Newer user result")
    latest = retain_run(app.state.store, newer["id"])
    app.state.store.change(run["id"], lambda r: r.update(state="COMPLETED"))
    with pytest.raises(WorkflowError, match="书库已有更新结果"):
        retain_run(app.state.store, run["id"])
    assert app.state.store.get("book", BOOK)["retained_result"] == latest


@pytest.mark.parametrize("box", [[0, 0, float("nan"), 1], [0, 0, 0, 1], [0, 0, 2, 1], [True, 0, 1, 1]])
def test_invalid_boxes(box):
    assert not valid_box(box)
