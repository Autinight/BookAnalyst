"""Grok OAuth login, token refresh, and OpenAI-compatible calls."""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from bookanalyst.app import create_app
from bookanalyst.grok_oauth import API_BASE, CLIENT_ID, REDIRECT_URI
from bookanalyst.store import WorkflowError
from test_providers import client_for
from test_request_retry import SCHEMA, ready_run


def grok_transport(access="access-1", refresh="refresh-1", models=None):
    models = models or [{"id": "grok-4.6"}, {"id": "grok-imagine-image"}]
    seen = []

    def handle(request):
        seen.append(request)
        body = request.content.decode() if request.content else ""
        path = request.url.path
        if request.url.host == "auth.x.ai" and path.endswith("/token"):
            if "grant_type=refresh_token" in body:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-2",
                        "refresh_token": refresh,
                        "expires_in": 3600,
                    },
                )
            if "grant_type=authorization_code" in body:
                assert "code_verifier=" in body
                return httpx.Response(
                    200,
                    json={
                        "access_token": access,
                        "refresh_token": refresh,
                        "expires_in": 3600,
                    },
                )
            return httpx.Response(400, json={"error": "invalid_request"})
        if request.url.host == "api.x.ai" and path.endswith("/models"):
            header = request.headers.get("Authorization")
            if header not in ("Bearer " + access, "Bearer access-2"):
                return httpx.Response(401)
            return httpx.Response(200, json={"data": models})
        if path.endswith("/chat/completions"):
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": '{"value":1}'}}
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handle), seen


async def wait_login(providers):
    task = providers._grok_login_task
    if task is not None:
        await task


def test_settings_include_grok_and_hide_tokens(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        grok = settings["connections"]["grok_subscription"]
        assert grok["kind"] == "grok_oauth"
        assert grok["name"] == "Grok 官方订阅"
        assert grok["enabled"] is True
        assert grok["oauth_configured"] is False
        assert "access_token" not in grok
        app.state.store.put(
            "credential",
            "grok_subscription",
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "expires_at": time.time() + 1000,
            },
        )
        text = client.get("/api/settings").text
        assert "secret-access" not in text
        assert "secret-refresh" not in text
        assert client.get("/api/settings").json()["connections"]["grok_subscription"][
            "oauth_configured"
        ]


def test_omitted_grok_connection_is_restored(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"].pop("grok_subscription")
        saved = client.put("/api/settings", json=settings).json()
        assert saved["connections"]["grok_subscription"]["kind"] == "grok_oauth"


def test_existing_install_gains_grok_connection(workspace, tmp_path):
    first = create_app(workspace, tmp_path / "state")
    settings = first.state.store.get("settings", "main")
    settings["connections"].pop("grok_subscription")
    first.state.store.put("settings", "main", settings)
    restarted = create_app(workspace, tmp_path / "state")
    saved = restarted.state.store.get("settings", "main")
    assert saved["connections"]["grok_subscription"]["kind"] == "grok_oauth"


@pytest.mark.asyncio
async def test_grok_login_exchanges_code_and_lists_models(app):
    providers = app.state.engine.providers
    transport, seen = grok_transport()
    providers.transport = transport
    try:
        result = await providers.login("grok_subscription")
        query = parse_qs(urlparse(result["auth_url"]).query)
        assert query["client_id"] == [CLIENT_ID]
        assert query["redirect_uri"] == [REDIRECT_URI]
        assert query["plan"] == ["generic"]
        state = query["state"][0]
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{REDIRECT_URI}?code=test-code&state={state}")
        assert response.status_code == 200
        assert "登录成功" in response.text
        await wait_login(providers)
        cred = app.state.store.get("credential", "grok_subscription")
        assert cred["access_token"] == "access-1"
        assert cred["refresh_token"] == "refresh-1"
        status = await providers.status("grok_subscription")
        assert status["status"] == "READY"
        assert [m["id"] for m in status["models"]] == ["grok-4.6"]
        assert any(request.url.path.endswith("/token") for request in seen)
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_grok_refresh_and_generate(app):
    store, engine, run = ready_run(app)
    providers = engine.providers
    transport, seen = grok_transport()
    providers.transport = transport
    store.put(
        "credential",
        "grok_subscription",
        {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_at": time.time() - 10,
        },
    )
    run["config"]["model"] = {
        "connection_id": "grok_subscription",
        "model_id": "grok-4.6",
        "reasoning_effort": "medium",
    }
    store.put("run", run["id"], run)
    try:
        token = await providers.ensure_grok_access("grok_subscription")
        assert token == "access-2"
        assert store.get("credential", "grok_subscription")["access_token"] == "access-2"
        result = await providers.generate(run, "model", "convert", "ping", SCHEMA)
        assert result == {"value": 1}
        completion = next(r for r in seen if r.url.path.endswith("/chat/completions"))
        assert completion.headers["Authorization"] == "Bearer access-2"
        assert str(completion.url).startswith(API_BASE)
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_grok_resolve_requires_login(app):
    providers = app.state.engine.providers
    with pytest.raises(WorkflowError) as failed:
        await providers.resolve(
            {
                "connection_id": "grok_subscription",
                "model_id": "",
                "reasoning_effort": "medium",
            },
            require_image=True,
        )
    assert failed.value.code == "AUTH_REQUIRED"


def test_custom_api_login_still_rejected(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"]["custom_api"].update(
            enabled=True,
            base_url="http://127.0.0.1:9",
            auth_mode="none",
            model_id="x",
        )
        assert client.put("/api/settings", json=settings).status_code == 200
        response = client.post("/api/connections/custom_api/login", json={})
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_CHANNEL"
