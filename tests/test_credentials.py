"""Write-only credentials: persistence, replacement, clearing and actual adapter headers."""

import json
import httpx
import pytest
from fastapi.testclient import TestClient
from bookanalyst.app import create_app
from bookanalyst.llm import Providers


def client_for(app):
    client = TestClient(app)
    client.headers["x-bookanalyst-token"] = client.get("/api/bootstrap").json()["token"]
    return client


def test_key_is_write_only_preserved_and_persistent(app):
    marker = "test-key-never-return-this-value"
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        conn = settings["connections"]["custom_api"]
        assert "api_key_env" not in conn and "api_key" not in conn
        conn["api_key"] = marker
        response = client.put("/api/settings", json=settings)
        assert response.status_code == 200 and marker not in response.text
        assert response.json()["connections"]["custom_api"]["api_key_configured"]
        saved = response.json()
        saved["connections"]["custom_api"]["api_key"] = ""
        assert client.put("/api/settings", json=saved).status_code == 200
        assert marker not in client.get("/api/settings").text
        assert marker not in client.get("/api/bootstrap").text
        assert marker not in json.dumps(app.state.store.get("settings", "main"))
    restarted = create_app(app.state.workspace, app.state.store.root)
    provider = restarted.state.engine.providers
    conn = restarted.state.store.get("settings", "main")["connections"]["custom_api"]
    assert provider.api_key("custom_api", conn) == marker
    assert (
        provider.api_key("another_connection", {"api_key_env": "MISSING_TEST_KEY"})
        == ""
    )


def test_rotation_clear_and_invalid_settings_are_atomic(app, monkeypatch):
    monkeypatch.setenv("LLM_CUSTOM_API_KEY", "test-legacy-env-key")
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        assert settings["connections"]["custom_api"]["api_key_configured"]
        settings["connections"]["custom_api"]["api_key"] = "test-new-key"
        response = client.put("/api/settings", json=settings)
        assert response.status_code == 200
        conn = app.state.store.get("settings", "main")["connections"]["custom_api"]
        key = lambda: app.state.engine.providers.api_key("custom_api", conn)
        assert key() == "test-new-key"
        invalid = response.json()
        invalid["connections"]["custom_api"].update(
            api_key="test-invalid-save", enabled=True, base_url="invalid"
        )
        assert client.put("/api/settings", json=invalid).status_code == 422
        assert key() == "test-new-key"
        settings = response.json()
        settings["connections"]["custom_api"]["api_key"] = "test-rotated-key"
        response = client.put("/api/settings", json=settings)
        assert key() == "test-rotated-key"
        settings = response.json()
        settings["connections"]["custom_api"]["clear_api_key"] = True
        response = client.put("/api/settings", json=settings)
        assert (
            response.status_code == 200
            and not response.json()["connections"]["custom_api"]["api_key_configured"]
        )
        assert key() == ""  # Clearing must not fall back to the old environment key.
        assert client.put("/api/settings", json=response.json()).status_code == 200
        assert key() == ""


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.asyncio
async def test_pasted_key_is_used_only_in_authorization_header(app, protocol, tmp_path):
    key = "test-local-adapter-key"
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"]["custom_api"].update(
            enabled=True,
            base_url="http://model.test/v1",
            model_id="test-model",
            protocol=protocol,
            api_key=key,
            image_support="supported",
        )
        response = client.put("/api/settings", json=settings)
        assert response.status_code == 200 and key not in response.text
    seen = []

    def handle(request):
        assert request.headers["Authorization"] == "Bearer " + key
        assert key not in request.content.decode()
        seen.append(request.url.path)
        data = (
            {"status": "completed", "output": []}
            if protocol == "responses"
            else {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        )
        return httpx.Response(200, json=data)

    provider = Providers(app.state.store, tmp_path, httpx.MockTransport(handle))
    binding = dict(
        connection_id="custom_api", model_id="test-model", reasoning_effort="medium"
    )
    assert (await provider.status("custom_api"))["status"] == "CONFIGURED_UNTESTED"
    resolved, conn = await provider.resolve(binding, require_image=True)
    await provider._custom(conn, resolved, "content", {"type": "object"}, [])
    assert len(seen) == 1
