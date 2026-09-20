"""Isolated image jobs: crop verification, resumability, provenance and publishing."""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bookanalyst.figure_assets import MANIFEST, image_inventory, save_manifest, valid_box
from bookanalyst.image_repair import ImageComparison, ImageDecision, ImageRepairCreate, create_image_run, image_width_edits
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
        assert purpose == "image_repair" and len(images) in (1, 2, 3)
        payload = json.loads(prompt)
        response = await responder(payload, images)
        return parse_json(json.dumps(response), schema)
    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)


def answer(action="keep", bbox=None, reason="图形与内嵌标签完整", width_ratio=None):
    return {"action": action, "bbox": bbox or [], "reason": reason, "width_ratio": width_ratio}


def comparison(choice="candidate", confirmed=True, reason="新裁图更完整，图形与内嵌标签完整"):
    return {"choice": choice, "confirmed": confirmed, "reason": reason}


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
async def test_rerun_deferred_uses_completed_project_and_resets_attempts(app, monkeypatch):
    result, assets = seed(app)
    source = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    calls = []
    async def respond(payload, images):
        calls.append(payload["asset"]["id"])
        return answer(width_ratio=0.45) if payload["asset"]["id"] == assets[0]["id"] else answer("uncertain")
    wire(app, monkeypatch, respond)
    await engine.execute(source["id"])
    assert store.get("run", source["id"])["state"] == "COMPLETED"
    original = store.directory(source["id"])
    before = {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()}
    # A different library version must not replace the chosen repair project's content.
    newer, _ = completed(app, "Different library content")
    library_result = retain_run(store, newer["id"])
    settings = store.get("settings", "main")
    settings["stage_models"]["image_repair"]["model_id"] = "new-repair-model"
    store.put("settings", "main", settings)
    launched = []
    monkeypatch.setattr(engine, "launch", launched.append)
    body = {"asset_ids": [assets[1]["id"]], "operation_id": "retry-deferred-operation", "llm_concurrency": 3}
    with TestClient(app) as client:
        auth = headers(client)
        url = f"/api/runs/{source['id']}/repair-images"
        assert client.post(url, json=body).status_code == 403
        response = client.post(url, json=body, headers=auth)
        assert response.status_code == 200, response.text
        retry = response.json()
        assert retry["id"] != source["id"] and retry["concurrency"] == 3
        assert retry["stage_models"]["image_repair"]["model_id"] == "new-repair-model"
        assert client.post(url, json=body, headers=auth).json()["id"] == retry["id"]
        assert client.post(url, json=body | {"llm_concurrency": 4}, headers=auth).status_code == 409
        listing = client.get(f"/api/runs/{retry['id']}/images").json()
        assert len(listing["images"]) == 1 and listing["images"][0]["state"] == "PENDING"
    assert set(launched) == {retry["id"]}
    target = store.directory(retry["id"])
    assert (target / "tex/body.tex").read_bytes() == before[original.joinpath("tex/body.tex").relative_to(original)]
    assert not (target / "tex/image-review.json").exists()
    assert retry["image_repair"]["source_run_id"] == source["id"]
    calls.clear()
    await engine.execute(retry["id"])
    assert store.get("run", retry["id"])["state"] == "COMPLETED"
    assert calls == [assets[1]["id"]] * 3
    assert store.get("book", BOOK)["retained_result"] == library_result
    assert {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()} == before
    assert json.loads((target / "tex" / MANIFEST).read_text(encoding="utf-8"))["assets"][0]["width_ratio"] == 0.45


def test_rerun_requires_completed_image_job_and_deferred_selection(app, monkeypatch):
    result, assets = seed(app)
    source = create(app, result, assets)
    store = app.state.store
    monkeypatch.setattr(app.state.engine, "launch", lambda rid: None)
    body = {"asset_ids": [assets[0]["id"]], "operation_id": "retry-invalid-selection"}
    with TestClient(app) as client:
        auth = headers(client)
        url = f"/api/runs/{source['id']}/repair-images"
        for state in ("PENDING", "RUNNING", "PAUSED", "NEEDS_REVIEW"):
            store.change(source["id"], lambda r: r.update(state=state))
            assert client.post(url, json=body, headers=auth).status_code == 409
        store.change(source["id"], lambda r: r.update(state="COMPLETED"))
        for selection in ([], ["../../escape"], [assets[0]["id"]]):
            assert client.post(url, json=body | {"asset_ids": selection}, headers=auth).status_code == 422
        atomic_json(store.directory(source["id"]) / "image-checks" / (assets[0]["id"] + ".json"), {"state": "FIXED"})
        assert client.post(url, json=body, headers=auth).status_code == 422
        assert client.post(f"/api/runs/{result['run_id']}/repair-images", json=body, headers=auth).status_code == 422


def test_image_version_tracks_file_changes_not_task_states(app):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store, rid, identifier = app.state.store, run["id"], assets[0]["id"]
    with TestClient(app) as client:
        def version():
            return client.get(f"/api/runs/{rid}/images").json()["images"][0]["image_version"]
        original = version()
        for state in ("RUNNING", "PASSED", "DEFERRED"):
            store.task(rid, "image-" + identifier, "image_repair", state, [1])
            assert version() == original
        crop = store.directory(rid) / "tex/assets" / (identifier + ".png")
        Image.new("RGB", (80, 100), "white").save(crop)
        assert version() != original
        response = client.get(f"/api/runs/{rid}/images/{identifier}")
        assert response.headers["cache-control"] == "private, no-cache"


def test_image_model_and_concurrency_are_independent_of_conversion(app):
    from test_stage_models import install
    store = app.state.store
    settings = install(store)
    settings.update(llm_concurrency=200, image_repair_concurrency=7)
    store.put("settings", "main", settings)
    result, assets = seed(app, 1)
    source_before = store.get("run", result["run_id"])
    default = create(app, result, assets)
    assert default["config"]["llm_concurrency"] == 7
    assert default["config"]["stage_models"]["image_repair"] == settings["stage_models"]["image_repair"]
    command = ImageRepairCreate(result_id=result["id"], asset_ids=[assets[0]["id"]],
                                operation_id="custom-image-concurrency", llm_concurrency=23)
    custom = create_image_run(app.state.engine, BOOK, command)
    assert custom["config"]["llm_concurrency"] == 23
    assert create_image_run(app.state.engine, BOOK, command)["id"] == custom["id"]
    with pytest.raises(WorkflowError, match="操作编号"):
        create_image_run(app.state.engine, BOOK, command.model_copy(update={"llm_concurrency": 24}))
    assert store.get("run", result["run_id"]) == source_before
    assert store.get("settings", "main") == settings
    # Reject invalid values before creating any task.
    for invalid in [0, -1, 1.5, True]:
        with pytest.raises(ValueError):
            ImageRepairCreate(**(command.model_dump() | {"llm_concurrency": invalid}))


@pytest.mark.asyncio
async def test_image_check_and_verification_use_independent_vision_model(app, monkeypatch, tmp_path):
    from test_stage_models import install
    from bookanalyst.image_repair import repair_images
    store, engine = app.state.store, app.state.engine
    settings = install(store)
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    page = tmp_path / "test-page.png"
    Image.new("RGB", (200, 200), "white").save(page)
    seen = []
    async def image(*args): return page
    async def resolve(binding, require_image=False):
        assert require_image
        assert binding == settings["stage_models"]["image_repair"]
        return dict(binding), {}
    async def generate(snapshot, role, purpose, prompt, schema, images=()):
        phase = json.loads(prompt)["phase"]
        seen.append((phase, snapshot["config"]["model"]))
        return answer("recrop", [0.1, 0.1, 0.9, 0.9]) if phase == "check" else comparison()
    async def compile_stop(*args): return {"status": "FAILED", "code": "TEST_END", "message": "Test end"}
    monkeypatch.setattr(engine, "image", image)
    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)
    monkeypatch.setattr("bookanalyst.image_repair.compile_tex", compile_stop)
    with pytest.raises(WorkflowError, match="Test end"):
        await repair_images(engine, run)
    assert seen == [(phase, settings["stage_models"]["image_repair"]) for phase in ("check", "verify")]


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
        assert len(images) == 3
        with Image.open(images[1]) as old:
            assert old.size == (40, 40)
        with Image.open(images[2]) as crop:
            assert crop.size == (100, 120)
        return comparison()
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
    review = json.loads((base / "tex/image-review.json").read_text(encoding="utf-8"))
    assert [a["state"] for a in review["images"]] == ["FIXED", "DEFERRED"]
    assert len(calls) == 5 and len(compiles) == 1
    assert [a["attempts"] for a in review["images"]] == [1, 3]
    assert (original / "assets" / (assets[0]["id"] + ".png")).read_bytes() == original_crop
    assert (base / "tex/body.tex").read_bytes() == original_body
    assert store.get("book", BOOK)["retained_result"] == result
    with Image.open(base / "tex/assets" / (assets[0]["id"] + ".png")) as crop:
        assert crop.size == (100, 120)
        assert crop.info["dpi"] == pytest.approx((150, 150), abs=0.02)
    with TestClient(app) as client:
        auth = headers(client)
        assert client.post(f"/api/runs/{run['id']}/retain").status_code == 403
        response = client.post(f"/api/runs/{run['id']}/retain", headers=auth)
        assert response.status_code == 200, response.text
        assert client.post(f"/api/runs/{run['id']}/retain", headers=auth).json() == response.json()
    assert store.get("book", BOOK)["retained_result"]["run_id"] == run["id"]
    saved = retained_directory(store, store.get("book", BOOK))
    assert json.loads((saved / MANIFEST).read_text(encoding="utf-8"))["assets"][0]["bbox"] == [0.1, 0.2, 0.6, 0.8]


@pytest.mark.asyncio
async def test_size_only_keeps_pixels_and_is_repairable_again(app, monkeypatch):
    result, assets = seed(app, 1)
    store, engine = app.state.store, app.state.engine
    original = retained_directory(store, store.get("book", BOOK))
    before_body = (original / "body.tex").read_bytes()
    run = create(app, result, assets)
    phases = []
    async def respond(payload, images):
        phases.append(payload["phase"])
        assert len(images) == 2
        assert payload["asset"]["width_ratio"] is None
        return answer(width_ratio=0.45, reason="裁图完整，插入宽度调整为正文的45%")
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert phases == ["check"]
    base = store.directory(run["id"])
    body = (base / "tex/body.tex").read_text(encoding="utf-8")
    assert r"width=0.45\linewidth" in body
    assert r"\caption{An illustration}" in body and "Original text." in body
    assert (original / "body.tex").read_bytes() == before_body
    with Image.open(base / "tex/assets" / (assets[0]["id"] + ".png")) as current:
        with Image.open(original / "assets" / (assets[0]["id"] + ".png")) as before:
            assert current.size == before.size and current.tobytes() == before.tobytes()
    assert store.get("book", BOOK)["retained_result"] == result
    retain_run(store, run["id"])
    saved = retained_directory(store, store.get("book", BOOK))
    _, rows = image_inventory(store, store.get("book", BOOK), saved, run["id"])
    assert rows[0]["id"] == assets[0]["id"] and rows[0]["repairable"]
    assert rows[0]["width_ratio"] == 0.45 and rows[0]["bbox"] == assets[0]["bbox"]
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{run['id']}/images").json()["images"][0]
        assert row["state"] == "FIXED" and row["width_ratio"] == 0.45
        assert row["before_width_ratio"] is None


@pytest.mark.asyncio
async def test_recrop_and_width_applied_together_only_after_verification(app, monkeypatch, tmp_path):
    result, assets = seed(app)
    run = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    project = store.directory(run["id"]) / "tex"
    page = tmp_path / "source-page.png"
    Image.new("RGB", (400, 300), "blue").save(page)
    body_before = (project / "body.tex").read_text(encoding="utf-8")
    accepted, rejected = (a["id"] for a in assets)
    async def image(*args): return page
    async def respond(payload, images):
        assert images[0] == page
        identifier = payload["asset"]["id"]
        if payload["phase"] == "check":
            return answer("recrop", [0.05, 0.05, 0.95, 0.95], "扩大原页截取范围，补齐漏截标签", width_ratio=0.6)
        assert payload["proposed_width_ratio"] == 0.6
        with Image.open(images[2]) as crop:
            assert crop.size == (360, 270)  # Far larger than the old 40x40 crop.
        assert "width=0.6" not in (project / "body.tex").read_text(encoding="utf-8") or identifier == rejected
        return comparison() if identifier == accepted else comparison("original", False, "新图仍然漏截，旧图更好")
    async def stop(project): return {"status": "FAILED", "code": "TEST_END"}
    monkeypatch.setattr(engine, "image", image)
    monkeypatch.setattr("bookanalyst.image_repair.compile_tex", stop)
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    report = json.loads((project / "image-review.json").read_text(encoding="utf-8"))["images"]
    assert [r["state"] for r in report] == ["FIXED", "DEFERRED"]
    assert report[0]["width_ratio"] == 0.6 and report[1]["width_ratio"] is None
    body = (project / "body.tex").read_text(encoding="utf-8")
    assert body == body_before.replace(r"\BAFigure{" + accepted + "}",
        r"\includegraphics[width=0.6\linewidth,height=.8\textheight,keepaspectratio]{assets/" + accepted + ".png}")
    manifest = json.loads((project / MANIFEST).read_text(encoding="utf-8"))["assets"]
    assert manifest[0]["bbox"] == [0.05, 0.05, 0.95, 0.95] and manifest[0]["width_ratio"] == 0.6
    assert "width_ratio" not in manifest[1] and manifest[1]["bbox"] == assets[1]["bbox"]
    assert store.get("book", BOOK)["retained_result"] == result


def test_width_edits_preserve_other_images_and_support_repeat_repairs(tmp_path):
    body = tmp_path / "body.tex"
    atomic_text(body, r"""% \BAFigure{selected}
\begin{figure}\BAFigure{selected}\caption{Unchanged}\end{figure}
\includegraphics[
angle=0,width=\linewidth,scale=2,keepaspectratio]{assets/selected.png}
\BAFigure{untouched}
""")
    for ratio in (0.4, 0.7):
        edits = image_width_edits(tmp_path, "selected", ratio)
        for path, content in edits.items():
            atomic_text(path, content)
        text = body.read_text(encoding="utf-8")
        assert text.count(f"width={ratio}\\linewidth") == 2
        assert text.count(r"\includegraphics") == 2 and text.count("keepaspectratio") == 2
        assert "scale=2" not in text and "angle=0" in text
        assert r"\BAFigure{untouched}" in text and r"\caption{Unchanged}" in text
        assert text.startswith(r"% \BAFigure{selected}")
    with pytest.raises(WorkflowError, match="未找到"):
        image_width_edits(tmp_path, "missing", 0.5)


@pytest.mark.parametrize("ratio", [0, -0.1, 1.01, True, "0.5", float("nan"), float("inf")])
def test_invalid_insertion_width_is_rejected(ratio):
    with pytest.raises(ValueError):
        ImageDecision.model_validate(answer(width_ratio=ratio))


def test_image_decision_schema_keeps_nullable_width_required_on_wire():
    from test_subscription_schema import assert_strict_schema
    assert_strict_schema(ImageDecision.model_json_schema())
    assert_strict_schema(ImageComparison.model_json_schema())
    assert ImageDecision.model_validate({"action": "keep", "bbox": [], "reason": "Legacy receipt"}).width_ratio is None


@pytest.mark.asyncio
async def test_rejected_new_crop_keeps_original_and_compile_failure_does_not_publish(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    base = store.directory(run["id"])
    before = (base / "tex/assets" / (assets[0]["id"] + ".png")).read_bytes()
    async def respond(payload, images):
        return answer("recrop", [0.1, 0.1, 0.8, 0.8]) if payload["phase"] == "check" else comparison("original", False, "新裁图仍缺标签")
    async def fail(project): return {"status": "FAILED", "code": "TEX_ERROR"}
    wire(app, monkeypatch, respond)
    monkeypatch.setattr("bookanalyst.image_repair.compile_tex", fail)
    monkeypatch.setattr("bookanalyst.image_repair.correct_legacy_size", lambda *args: None)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "NEEDS_REVIEW"
    assert store.get("book", BOOK)["retained_result"] == result
    assert (base / "tex/assets" / (assets[0]["id"] + ".png")).read_bytes() == before
    assert json.loads((base / "image-checks" / (assets[0]["id"] + ".json")).read_text(encoding="utf-8"))["state"] == "DEFERRED"
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
    report = json.loads((store.directory(run["id"]) / "tex/image-review.json").read_text(encoding="utf-8"))
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
        return comparison()
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "PAUSED"
    store.change(run["id"], lambda r: r.update(pause_requested=False, state="RUNNING"))
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert calls == ["check", "verify"]


@pytest.mark.asyncio
@pytest.mark.parametrize("success_attempt", [2, 3])
async def test_uncertain_images_retry_with_feedback_and_stop_when_confirmed(app, monkeypatch, success_attempt):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    calls = []
    async def respond(payload, images):
        attempt = payload.get("retry_attempt", 1)
        calls.append(attempt)
        assert payload["phase"] == "check"
        if attempt > 1:
            assert payload["previous_attempt"]["decision"]["action"] == "uncertain"
            assert payload["previous_attempt"]["reason"] == "需要再次核对标签"
        return answer() if attempt == success_attempt else answer("uncertain", reason="需要再次核对标签")
    wire(app, monkeypatch, respond)
    await app.state.engine.execute(run["id"])
    assert calls == list(range(1, success_attempt + 1))
    assert app.state.store.get("run", run["id"])["state"] == "COMPLETED"
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{run['id']}/images").json()["images"][0]
        assert row["state"] == "PASSED"
        assert row["attempts"] == row["attempt"] == success_attempt
        assert row["max_attempts"] == 3


@pytest.mark.asyncio
async def test_better_unconfirmed_crop_is_used_by_next_attempt(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    project = store.directory(run["id"]) / "tex"
    before = (project / "assets" / (assets[0]["id"] + ".png")).read_bytes()
    calls = []
    box = [0.1, 0.1, 0.8, 0.8]
    async def respond(payload, images):
        attempt = payload.get("retry_attempt", 1)
        calls.append((attempt, payload["phase"]))
        if payload["phase"] == "verify":
            assert len(images) == 3
            assert images[1].read_bytes() == before
            return comparison("candidate", False, "新图补全了坐标轴，但需再核对标签")
        if attempt == 1:
            return answer("recrop", box, width_ratio=0.6)
        assert payload["asset"]["bbox"] == box
        assert payload["asset"]["width_ratio"] == 0.6
        assert r"width=0.6\linewidth" in payload["asset"]["context"]
        assert payload["previous_attempt"]["verification"]["choice"] == "candidate"
        assert images[1].read_bytes() != before
        return answer()
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    row = json.loads((project / "image-review.json").read_text(encoding="utf-8"))["images"][0]
    assert row["state"] == "FIXED" and row["attempts"] == 2 and row["bbox"] == box
    assert calls == [(1, "check"), (1, "verify"), (2, "check")]
    assert (store.directory(run["id"]) / "image-original/assets" / (assets[0]["id"] + ".png")).read_bytes() == before
    assert store.get("book", BOOK)["retained_result"] == result


@pytest.mark.asyncio
async def test_comparison_can_confirm_original_and_discard_proposed_size(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store = app.state.store
    project = store.directory(run["id"]) / "tex"
    before = (project / "assets" / (assets[0]["id"] + ".png")).read_bytes()
    calls = []
    async def respond(payload, images):
        calls.append(payload["phase"])
        return answer("recrop", [0.1, 0.1, 0.8, 0.8], width_ratio=0.6) if payload["phase"] == "check" else comparison("original", True, "旧图完整，新图混入旁边文字")
    wire(app, monkeypatch, respond)
    monkeypatch.setattr("bookanalyst.image_repair.correct_legacy_size", lambda *args: None)
    await app.state.engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    row = json.loads((project / "image-review.json").read_text(encoding="utf-8"))["images"][0]
    assert row["state"] == "PASSED" and row["attempts"] == 1
    assert row["width_ratio"] is None and row["bbox"] == assets[0]["bbox"]
    assert (project / "assets" / (assets[0]["id"] + ".png")).read_bytes() == before
    assert calls == ["check", "verify"]


@pytest.mark.asyncio
async def test_three_attempt_limit_survives_pause_and_keeps_best_unconfirmed_crop(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store, engine = app.state.store, app.state.engine
    base = store.directory(run["id"])
    calls = []
    boxes = {n: [0.1, 0.1, 0.5 + 0.1 * n, 0.8] for n in (1, 2, 3)}
    async def respond(payload, images):
        attempt = payload.get("retry_attempt", 1)
        calls.append((attempt, payload["phase"]))
        if payload["phase"] == "check":
            assert payload["asset"]["bbox"] == (boxes[attempt - 1] if attempt > 1 else assets[0]["bbox"])
            return answer("recrop", boxes[attempt])
        if attempt == 2:
            store.change(run["id"], lambda r: r.update(pause_requested=True))
        return comparison("candidate", False, "新图更完整，但仍有标签需要核对")
    wire(app, monkeypatch, respond)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "PAUSED"
    checkpoint = base / "image-checks" / (assets[0]["id"] + ".json")
    row = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert row["attempts"] == 1 and row["attempt"] == 2 and row["bbox"] == boxes[1]
    store.change(run["id"], lambda r: r.update(pause_requested=False, state="RUNNING"))
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert calls == [(n, phase) for n in (1, 2, 3) for phase in ("check", "verify")]
    row = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert row["attempts"] == 3 and row["state"] == "DEFERRED" and row["bbox"] == boxes[3]
    assert "已尝试 3 次" in row["reason"]
    manifest = json.loads((base / "tex" / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["assets"][0]["bbox"] == boxes[3]
    assert store.get("book", BOOK)["retained_result"] == result


@pytest.mark.asyncio
async def test_legacy_deferred_checkpoint_counts_as_first_attempt(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store = app.state.store
    checkpoint = store.directory(run["id"]) / "image-checks" / (assets[0]["id"] + ".json")
    atomic_json(checkpoint, {"state": "DEFERRED", "reason": "旧任务仍需核对标签"})
    calls = []
    async def respond(payload, images):
        calls.append(payload["retry_attempt"])
        if len(calls) == 1:
            assert payload["previous_attempt"] == {"reason": "旧任务仍需核对标签"}
        return answer("uncertain", reason="标签无法确认")
    wire(app, monkeypatch, respond)
    await app.state.engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert calls == [2, 3]
    row = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert row["state"] == "DEFERRED" and row["attempts"] == 3


@pytest.mark.asyncio
async def test_comparison_handles_missing_original_crop(app, monkeypatch):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    store = app.state.store
    current = store.directory(run["id"]) / "tex/assets" / (assets[0]["id"] + ".png")
    current.unlink()
    async def respond(payload, images):
        if payload["phase"] == "check":
            assert len(images) == 1
            return answer("recrop", [0.1, 0.1, 0.8, 0.8])
        assert len(images) == 2 and not payload["current_crop_present"]
        return comparison()
    wire(app, monkeypatch, respond)
    await app.state.engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert current.is_file()


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
    manifest = json.loads((store.directory(run["id"]) / "tex" / MANIFEST).read_text(encoding="utf-8"))
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


def test_user_can_save_selected_image_candidate_after_another_version(app):
    result, assets = seed(app, 1)
    run = create(app, result, assets)
    newer, _ = completed(app, "Newer user result")
    latest = retain_run(app.state.store, newer["id"])
    app.state.store.change(run["id"], lambda r: r.update(state="COMPLETED"))
    newer_directory = retained_directory(app.state.store, app.state.store.get("book", BOOK))
    with TestClient(app) as client:
        saved = client.post(f"/api/runs/{run['id']}/retain", headers=headers(client))
        assert saved.status_code == 200, saved.text
    assert app.state.store.get("book", BOOK)["retained_result"]["run_id"] == run["id"]
    assert newer_directory.name == latest["id"]
    assert (newer_directory / "body.tex").read_text(encoding="utf-8") == "Newer user result"


@pytest.mark.parametrize("box", [[0, 0, float("nan"), 1], [0, 0, 0, 1], [0, 0, 2, 1], [True, 0, 1, 1]])
def test_invalid_boxes(box):
    assert not valid_box(box)
