"""Tool-enabled image repair uses compiler sessions on a selected project copy."""
import json

import pytest
from fastapi.testclient import TestClient

from bookanalyst.figure_assets import MANIFEST
from bookanalyst.image_repair import ImageRepairSelection, create_image_run, image_width_edits
from bookanalyst.store import WorkflowError, atomic_json, atomic_text
from test_image_repair import create, seed
from test_library import headers
from test_rebuild import BOOK


def special_job(app):
    result, assets = seed(app)
    source = create(app, result, assets)
    store = app.state.store
    store.change(source["id"], lambda r: r.update(state="COMPLETED"))
    for a in assets:
        atomic_json(store.directory(source["id"]) / "image-checks" / (a["id"] + ".json"),
                    {"state": "FAILED", "reason": "子图结构需要修改"})
    command = ImageRepairSelection(asset_ids=[assets[0]["id"]], operation_id="special-image-operation",
                                   mode="special")
    run = create_image_run(app.state.engine, BOOK, command, source_run_id=source["id"])
    return result, assets, source, run


def report(project, identifier, state="FIXED"):
    atomic_json(project / "image-special-review.json", {
        "images": [{"id": identifier, "state": state, "reason": "已检查原页和生成图片"}],
    })


def test_special_api_selects_failed_targets_without_weakening_standard_mode(app, monkeypatch):
    _, assets, source, _ = special_job(app)
    monkeypatch.setattr(app.state.engine, "launch", lambda rid: None)
    body = {"asset_ids": [assets[1]["id"]], "operation_id": "special-api-operation"}
    with TestClient(app) as client:
        auth = headers(client)
        url = f"/api/runs/{source['id']}/repair-images"
        assert client.post(url, json=body, headers=auth).status_code == 422
        assert client.post(url, json=body | {"mode": "special"}).status_code == 403
        response = client.post(url, json=body | {"mode": "special"}, headers=auth)
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["image_repair"]["mode"] == "special"
        assert client.post(url, json=body | {"mode": "special", "llm_concurrency": 4}, headers=auth).status_code == 409
        assert client.post(url, json=body | {"mode": "special", "asset_ids": ["../outside"],
                                           "operation_id": "special-invalid-selection"}, headers=auth).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["FIXED", "DEFERRED"])
async def test_special_runs_even_when_compilable_and_preserves_source(app, monkeypatch, verdict):
    result, assets, source, run = special_job(app)
    store, engine = app.state.store, app.state.engine
    original = store.directory(source["id"]) / "tex"
    before = {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()}
    calls = []
    async def agent(providers, current, project, feedback):
        calls.append(feedback)
        targets = json.loads((project / "image-special-targets.json").read_text(encoding="utf-8"))
        assert [a["id"] for a in targets["images"]] == [assets[0]["id"]]
        assert targets["images"][0]["previous_review"]["reason"] == "子图结构需要修改"
        assert "instructions" not in targets
        from pathlib import Path
        assert Path(targets["images"][0]["source_page_image"]).is_file()
        assert Path(targets["images"][0]["current_crop"]).is_file()
        for path, content in image_width_edits(project, assets[0]["id"], 0.35).items():
            atomic_text(path, content)
        manifest = json.loads((project / MANIFEST).read_text(encoding="utf-8"))
        manifest["assets"][0]["width_ratio"] = 0.35
        atomic_json(project / MANIFEST, manifest)
        report(project, assets[0]["id"], verdict)
    async def no_standard(*args, **kwargs):
        pytest.fail("Special repair must use the native agent, not crop-only requests")
    monkeypatch.setattr("bookanalyst.finisher.repair_project", agent)
    monkeypatch.setattr(engine, "ask", no_standard)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert len(calls) == 1 and calls[0]["status"] == "SPECIAL_IMAGE_REPAIR_REQUESTED"
    assert {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()} == before
    assert store.get("book", BOOK)["retained_result"] == result
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{run['id']}/images").json()["images"][0]
        assert row["state"] == verdict and row["width_ratio"] == 0.35
        assert row["before_width_ratio"] is None


@pytest.mark.asyncio
async def test_special_missing_report_can_resume_and_cannot_publish(app, monkeypatch):
    _, assets, _, run = special_job(app)
    store, engine = app.state.store, app.state.engine
    calls = []
    async def agent(providers, current, project, feedback):
        calls.append(feedback)
        if len(calls) > 1:
            report(project, assets[0]["id"])
    monkeypatch.setattr("bookanalyst.finisher.repair_project", agent)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["error"]["code"] == "IMAGE_SPECIAL_REPORT"
    with TestClient(app) as client:
        assert client.post(f"/api/runs/{run['id']}/retain", headers=headers(client)).status_code != 200
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED" and len(calls) == 2


@pytest.mark.asyncio
async def test_special_pause_after_agent_reuses_files_and_resume_compiles(app, monkeypatch):
    _, assets, _, run = special_job(app)
    store, engine = app.state.store, app.state.engine
    calls = []
    async def agent(providers, current, project, feedback):
        calls.append(feedback)
        report(project, assets[0]["id"])
    compile_calls = []
    original_compile = engine.compile
    async def compile_once(*args):
        compile_calls.append(True)
        if len(compile_calls) == 1:
            store.change(run["id"], lambda r: r.update(pause_requested=True))
            raise WorkflowError("PAUSED", "test pause")
        return await original_compile(*args)
    monkeypatch.setattr("bookanalyst.finisher.repair_project", agent)
    monkeypatch.setattr(engine, "compile", compile_once)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "PAUSED"
    store.change(run["id"], lambda r: r.update(pause_requested=False))
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED" and len(calls) == 1


@pytest.mark.asyncio
async def test_special_compiler_feedback_returns_to_same_agent_scope(app, monkeypatch):
    _, assets, _, run = special_job(app)
    store, engine = app.state.store, app.state.engine
    calls = []
    async def agent(providers, current, project, feedback):
        calls.append(feedback)
        assert current["image_repair"]["mode"] == "special"
        body = project / "body.tex"
        text = body.read_text(encoding="utf-8")
        if len(calls) == 1:
            atomic_text(body, text + "\n\\UndefinedSpecialImageMacro\n")
            report(project, assets[0]["id"])
        else:
            assert feedback["status"] == "FAILED"
            atomic_text(body, text.replace(r"\UndefinedSpecialImageMacro", ""))
    monkeypatch.setattr("bookanalyst.finisher.repair_project", agent)
    monkeypatch.setattr("bookanalyst.engine.repair_project", agent)
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_special_codex_has_compiler_permissions_and_image_model(app, monkeypatch):
    from test_codex_compiler import wire
    from bookanalyst.finisher import repair_project
    from bookanalyst.model_config import legacy_models
    store, engine, run, project, sdk = wire(app, monkeypatch)
    run.update(kind="image_repair", image_repair={"mode": "special"})
    run["config"]["stage_models"] = legacy_models(run["config"])
    run["config"]["stage_models"]["image_repair"]["model_id"] = "special-vision-model"
    store.put("run", run["id"], run)
    async def resolve(binding, require_image=False):
        assert require_image
        return binding, {}
    monkeypatch.setattr(engine.providers, "resolve", resolve)
    await repair_project(engine.providers, run, project, {"status": "SPECIAL_IMAGE_REPAIR_REQUESTED"})
    options = sdk.started[0]
    assert options["model"] == "special-vision-model"
    permissions = options["config"]["permissions"]["bookanalyst-compile"]
    assert permissions["filesystem"][str(project.resolve())] == "write"
    assert permissions["network"]["enabled"] is False
    assert "SPECIAL IMAGE REPAIR" in options["developer_instructions"]
    assert "image-special-review.json" in sdk.prompts[0]
    assert store.calls(run["id"])[0]["metadata"]["purpose"] == "image_repair"


@pytest.mark.asyncio
async def test_special_api_model_routes_to_pi_with_image_scope(app, monkeypatch):
    from bookanalyst.finisher import repair_project
    _, _, _, run = special_job(app)
    engine, store = app.state.engine, app.state.store
    binding = {"connection_id": "custom_api", "model_id": "special-api-model", "reasoning_effort": "high"}
    run["config"]["stage_models"]["image_repair"] = binding
    store.put("run", run["id"], run)
    settings = store.get("settings", "main")
    settings["connections"]["custom_api"]["enabled"] = True
    store.put("settings", "main", settings)
    async def resolve(selected, require_image=False):
        assert require_image and selected == binding
        return selected, {}
    calls = []
    async def pi(providers, current, project, feedback, instructions, **kwargs):
        calls.append(kwargs)
        assert "SPECIAL IMAGE REPAIR" in instructions
        assert current["config"]["model"] == binding
    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr("bookanalyst.pi_compiler.repair_project", pi)
    await repair_project(engine.providers, run, store.directory(run["id"]) / "tex", {})
    assert calls == [{"binding": binding, "purpose": "image_repair"}]
