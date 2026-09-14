"""Multiple providers retain independent credentials and stable bindings on rename."""
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from bookanalyst.app import create_app
from bookanalyst.config import DEFAULT_SETTINGS


def client_for(app):
    client = TestClient(app)
    client.headers["x-bookanalyst-token"] = client.get("/api/bootstrap").json()["token"]
    return client


def test_many_providers_rename_and_restart(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        original = copy.deepcopy(settings["connections"])
        for i in range(25):
            settings["connections"][f"api_{i}"] = dict(
                copy.deepcopy(DEFAULT_SETTINGS["connections"]["custom_api"]),
                name=f"供应商 {i}", enabled=True, base_url=f"http://provider-{i}.test/v1",
                model_id=f"model-{i}", api_key=f"test-key-{i}", image_support="supported",
            )
        settings["default_connection"] = "api_7"
        for binding in settings["stage_models"].values():
            binding.update(connection_id="api_7", model_id="model-7")
        response = client.put("/api/settings", json=settings)
        assert response.status_code == 200
        saved = response.json()
        assert len(saved["connections"]) == len(original) + 25
        assert all(f"test-key-{i}" not in response.text for i in range(25))
        assert all(saved["connections"][cid] == conn for cid, conn in original.items())
        before = copy.deepcopy(saved)
        saved["connections"]["api_7"]["name"] = "  主力模型 / 中文供应商  "
        saved["connections"]["api_7"]["api_key"] = ""
        response = client.put("/api/settings", json=saved)
        assert response.status_code == 200
        renamed = response.json()
        assert renamed["connections"]["api_7"]["name"] == "主力模型 / 中文供应商"
        assert renamed["stage_models"] == before["stage_models"]
        assert renamed["default_connection"] == "api_7"
        assert renamed["connections"]["api_8"] == before["connections"]["api_8"]
    restarted = create_app(app.state.workspace, app.state.store.root)
    with client_for(restarted) as client:
        assert client.get("/api/settings").json() == renamed
        providers = restarted.state.engine.providers
        for i in range(25):
            cid = f"api_{i}"
            assert providers.api_key(cid, renamed["connections"][cid]) == f"test-key-{i}"
        seen = []
        def handle(request):
            seen.append((request.url.host, request.headers["Authorization"]))
            return httpx.Response(200, json={"data": [{"id": "model-7"}]})
        providers.transport = httpx.MockTransport(handle)
        response = client.post("/api/connections/api_7/test", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "READY"
        assert seen == [("provider-7.test", "Bearer test-key-7")]


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, None, 42])
def test_invalid_provider_name_does_not_change_settings_or_credentials(app, name):
    with client_for(app) as client:
        original = client.get("/api/settings").json()
        updated = copy.deepcopy(original)
        updated["connections"]["custom_api"].update(name=name, api_key="test-invalid")
        assert client.put("/api/settings", json=updated).status_code == 422
        assert client.get("/api/settings").json() == original


def test_legacy_connections_without_names_can_still_be_saved(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        for conn in settings["connections"].values():
            conn.pop("name", None)
        assert client.put("/api/settings", json=settings).status_code == 200


def test_legacy_provider_limits_are_hidden_and_removed_on_save(app):
    store = app.state.store
    legacy = store.get("settings", "main")
    for conn in legacy["connections"].values():
        conn["max_in_flight"] = 1
    store.put("settings", "main", legacy)
    with client_for(app) as client:
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert all("max_in_flight" not in conn for conn in response.json()["connections"].values())
        # Reading settings must not write to an existing installation.
        assert store.get("settings", "main") == legacy
        response = client.put("/api/settings", json=legacy)
        assert response.status_code == 200
        saved = store.get("settings", "main")
        assert all("max_in_flight" not in conn for conn in saved["connections"].values())
        assert saved["llm_concurrency"] == legacy["llm_concurrency"]
        assert saved["stage_models"] == legacy["stage_models"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex_chatgpt", "openai_compatible"])
async def test_provider_does_not_cap_parallel_requests_even_with_legacy_limit(app, monkeypatch, kind):
    import asyncio
    from test_request_retry import ready_run, custom_conn, SCHEMA

    store, engine, run = ready_run(app)
    settings = store.get("settings", "main")
    cid = "custom_api" if kind == "openai_compatible" else "openai_subscription"
    if kind == "openai_compatible":
        settings["connections"][cid] = custom_conn()
    settings["connections"][cid]["max_in_flight"] = 1
    store.put("settings", "main", settings)
    run["config"]["model"]["connection_id"] = cid
    store.put("run", run["id"], run)
    entered, release = [], asyncio.Event()
    all_started = asyncio.Event()

    async def respond(*args):
        entered.append(True)
        if len(entered) == 4:
            all_started.set()
        await release.wait()
        return '{"value":1}', None

    monkeypatch.setattr(engine.providers, "_custom" if kind == "openai_compatible" else "_subscription", respond)
    requests = [asyncio.create_task(engine.providers.generate(run, "model", "convert", f"batch-{i}", SCHEMA)) for i in range(4)]
    try:
        await asyncio.wait_for(all_started.wait(), 3)
        assert len(entered) == 4
    finally:
        release.set()
        await asyncio.gather(*requests)
