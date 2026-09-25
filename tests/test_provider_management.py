"""Provider management exercises persistence, draft probes, and wire protocols."""
import copy
import json
import httpx
import pytest
from bookanalyst.app import create_app
from bookanalyst.store import WorkflowError
from test_providers import client_for


def test_delete_removes_credentials_and_survives_restart(app):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"]["custom_api"].update(base_url="http://models.test/v1", api_key="test-delete-key")
        settings["default_connection"] = "custom_api"
        for binding in settings["stage_models"].values():
            binding.update(connection_id="custom_api", model_id="gone")
        assert client.put("/api/settings", json=settings).status_code == 200
        deleted = client.delete("/api/connections/custom_api")
        assert deleted.status_code == 200
        assert "custom_api" not in deleted.json()["connections"]
        assert all(b["connection_id"] == "openai_subscription" and not b["model_id"] for b in deleted.json()["stage_models"].values())
        with pytest.raises(WorkflowError):
            app.state.store.get("credential", "custom_api")
    restarted = create_app(app.state.workspace, app.state.store.root)
    with client_for(restarted) as client:
        assert "custom_api" not in client.get("/api/settings").json()["connections"]


def test_probe_uses_unsaved_fields_and_keeps_saved_config(app):
    seen = []
    def handle(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-draft-key"
        assert request.headers["X-Project"] == "draft"
        return httpx.Response(200, json={"data": [{"id": f"model-{n}"} for n in range(75)]})
    app.state.engine.providers.transport = httpx.MockTransport(handle)
    with client_for(app) as client:
        saved = client.get("/api/settings").json()
        conn = copy.deepcopy(saved["connections"]["custom_api"])
        conn.update(base_url="http://draft.test/v1", api_key="test-draft-key", headers={"X-Project": "draft"})
        result = client.post("/api/providers/probe", json={"id": "new_draft", "connection": conn, "discover": True})
        assert result.status_code == 200
        assert len(result.json()["models"]) == 75
        assert seen[0].url.host == "draft.test"
        assert client.get("/api/settings").json() == saved
        assert "test-draft-key" not in result.text
        conn["headers"] = {"X-Invalid": "first\nsecond"}
        assert client.post("/api/providers/probe", json={"id": "new_draft", "connection": conn}).status_code == 422
        assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,endpoint,auth", [
    ("chat_completions", "/v1/chat/completions", "authorization"),
    ("responses", "/v1/responses", "authorization"),
    ("anthropic", "/v1/messages", "x-api-key"),
    ("gemini", "/v1/models/vision:generateContent", "x-goog-api-key"),
])
async def test_protocols_use_model_metadata_and_images(app, tmp_path, protocol, endpoint, auth):
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"]["custom_api"].update(
            base_url="http://models.test/v1", protocol=protocol, api_key="test-wire-key",
            model_id="vision", models=[{"id": "vision", "image": True, "reasoning": False, "context": 65536, "max_output": 1234}],
        )
        assert client.put("/api/settings", json=settings).status_code == 200
    providers = app.state.engine.providers
    def handle(request):
        assert request.url.path == endpoint
        assert "test-wire-key" in request.headers[auth]
        body = json.loads(request.content)
        assert "aW1hZ2U=" in request.content.decode()
        if protocol == "anthropic":
            assert body["max_tokens"] == 1234
            return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 5}})
        if protocol == "gemini":
            assert body["generationConfig"]["maxOutputTokens"] == 1234
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]})
        if protocol == "responses":
            assert body["text"] == {"format": {"type": "json_object"}}
            assert "response_format" not in body
            assert "reasoning" not in body and body["max_output_tokens"] == 1234
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]})
        assert "reasoning_effort" not in body and body["max_completion_tokens"] == 1234
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    providers.transport = httpx.MockTransport(handle)
    binding, conn = await providers.resolve({"connection_id": "custom_api", "model_id": "vision"}, require_image=True)
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    assert (await providers._custom(conn, binding, "read page", {"type": "object"}, [image]))[0] == "{}"
    await providers.close()


@pytest.mark.asyncio
async def test_unknown_image_capability_does_not_block_and_explicit_false_does(app):
    store = app.state.store
    settings = store.get("settings", "main")
    conn = settings["connections"]["custom_api"]
    conn.update(base_url="http://models.test/v1", auth_mode="none", model_id="unknown", enabled=True)
    store.put("settings", "main", settings)
    binding = {"connection_id": "custom_api", "model_id": "unknown"}
    await app.state.engine.providers.resolve(binding, require_image=True)
    conn["models"] = [{"id": "unknown", "image": False}]
    store.put("settings", "main", settings)
    with pytest.raises(WorkflowError, match="不支持图像"):
        await app.state.engine.providers.resolve(binding, require_image=True)


def test_usage_normalizes_native_protocols(app):
    from bookanalyst.store import encode
    import time
    with app.state.store.connection() as db:
        db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?)", ("test-usage", "run", 1, "llm", 1, "COMPLETED", encode({
            "connection_id": "example", "model_id": "vision", "started_at": time.time(),
            "usage": {"promptTokenCount": 11, "candidatesTokenCount": 7, "cachedContentTokenCount": 2},
        })))
    with client_for(app) as client:
        row = client.get("/api/providers/usage").json()["entries"][0]
        assert (row["input"], row["output"], row["cached"]) == (11, 7, 2)


def test_registered_capabilities_round_trip_and_reject_invalid_types(app):
    model = dict(id="registered", name="注册模型", context=262144, max_output=65536,
                 image=True, video=False, audio=True, reasoning=True, xhigh=True, max=False)
    with client_for(app) as client:
        settings = client.get("/api/settings").json()
        settings["connections"]["custom_api"].update(base_url="http://models.test/v1", models=[model])
        assert client.put("/api/settings", json=settings).status_code == 200
        assert client.get("/api/settings").json()["connections"]["custom_api"]["models"] == [model]
        for field in ("video", "audio", "xhigh", "max"):
            bad = copy.deepcopy(settings)
            bad["connections"]["custom_api"]["models"][0][field] = "true"
            assert client.put("/api/settings", json=bad).status_code == 422


@pytest.mark.asyncio
async def test_registered_reasoning_limits_apply_to_requests(app):
    store = app.state.store
    settings = store.get("settings", "main")
    conn = settings["connections"]["custom_api"]
    conn.update(base_url="http://models.test/v1", enabled=True, auth_mode="none", model_id="limited",
                models=[dict(id="limited", reasoning=True, xhigh=False, max=False)])
    store.put("settings", "main", settings)
    for effort in ("xhigh", "max"):
        with pytest.raises(WorkflowError, match="注册信息"):
            await app.state.engine.providers.resolve(dict(connection_id="custom_api", model_id="limited", reasoning_effort=effort))
    await app.state.engine.providers.resolve(dict(connection_id="custom_api", model_id="limited", reasoning_effort="high"))
    conn["models"] = [dict(id="limited")]
    store.put("settings", "main", settings)
    await app.state.engine.providers.resolve(dict(connection_id="custom_api", model_id="limited", reasoning_effort="max"))


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,path", [("anthropic", "/v1/messages"), ("gemini", "/v1/models/pi-test-model:streamGenerateContent")])
async def test_native_protocol_reaches_pi_compiler(app, protocol, path):
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from bookanalyst.pi_compiler import repair_project
    from test_pi_compiler import ready
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path.split("?")[0], body, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if protocol == "anthropic":
                events = [
                    {"type": "message_start", "message": {"id": "msg-test", "type": "message", "role": "assistant", "model": "pi-test-model", "content": [], "stop_reason": None, "usage": {"input_tokens": 11, "output_tokens": 0}}},
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done."}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}},
                    {"type": "message_stop"},
                ]
                wire = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events)
            else:
                wire = "data: " + json.dumps({"candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": "Done."}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18}}) + "\n\n"
            self.wfile.write(wire.encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        store, run, project = ready(app, f"http://127.0.0.1:{server.server_port}/v1")
        settings = store.get("settings", "main")
        settings["connections"]["custom_api"].update(protocol=protocol, headers={"X-Project": "native-test"}, models=[{"id": "pi-test-model", "context": 32768, "max_output": 2048, "reasoning": False}])
        store.put("settings", "main", settings)
        await asyncio.wait_for(repair_project(app.state.engine.providers, run, project, {"status": "FAILED"}, "Read files as needed.", binding=run["config"]["model"]), 30)
        assert len(seen) == 1
        assert seen[0][0] == path
        assert any(k.lower() == "x-project" and v == "native-test" for k,v in seen[0][2].items())
        assert store.calls(run["id"])[0]["state"] == "COMPLETED"
        state = json.loads((project.parent / "pi-compiler.json").read_text(encoding="utf-8"))
        assert state["context_window"] == 32768
        assert state["max_output_tokens"] == 2048
    finally:
        server.shutdown()
        server.server_close()
