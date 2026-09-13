"""High-concurrency model work must leave the local web server responsive."""
import asyncio
import json
import threading
import time

import httpx
import pytest

from bookanalyst.image_repair import ImageDecision
from test_request_retry import custom_conn, ready_run


@pytest.mark.asyncio
async def test_200_image_requests_leave_http_responsive(app, monkeypatch, tmp_path):
    store, engine, run = ready_run(app)
    providers = engine.providers
    conn = custom_conn() | {"timeout_seconds": 60, "protocol": "chat_completions"}
    settings = store.get("settings", "main")
    settings["connections"]["custom_api"] = conn
    store.put("settings", "main", settings)
    run["config"].pop("stage_models", None)
    run["config"]["model"].update(connection_id="custom_api", model_id="test-vision")
    run["config"]["llm_concurrency"] = 200
    store.put("run", run["id"], run)
    image = tmp_path / "page.png"
    image.write_bytes(b"test-image" * 10000)

    async def page(*args):
        return image

    monkeypatch.setattr(engine, "image", page)
    entered, all_started, release = [], asyncio.Event(), asyncio.Event()

    async def upstream(request):
        entered.append(request)
        if len(entered) == 200:
            all_started.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"action": "keep", "bbox": [], "reason": "完整"})
        }}]})

    providers.transport = httpx.MockTransport(upstream)
    web = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    original_client = httpx.AsyncClient
    created, preparation_threads = [], []
    event_thread = threading.get_ident()

    class SlowClient(original_client):
        def __init__(self, **kwargs):
            created.append((threading.get_ident(), kwargs["limits"].max_connections))
            time.sleep(0.05)  # Certificate loading must not block the event loop.
            super().__init__(**kwargs)

    monkeypatch.setattr("bookanalyst.llm.httpx.AsyncClient", SlowClient)
    prepare = providers._custom_payload

    def slow_prepare(*args):
        preparation_threads.append(threading.get_ident())
        time.sleep(0.01)  # Simulated slow image reads/encoding.
        return prepare(*args)

    monkeypatch.setattr(providers, "_custom_payload", slow_prepare)
    latencies, loop_gaps = [], []
    stop = asyncio.Event()

    async def probe():
        while not stop.is_set():
            start = time.perf_counter()
            response = await web.get("/api/bootstrap")
            latencies.append(time.perf_counter() - start)
            assert response.status_code == 200
            await asyncio.sleep(0.01)

    async def heartbeat():
        previous = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            loop_gaps.append(now - previous)
            previous = now

    monitor = asyncio.create_task(probe())
    clock = asyncio.create_task(heartbeat())
    requests = [asyncio.create_task(engine.ask(
        run, f"image-{i}", "image_repair", {"index": i}, ImageDecision, [1]
    )) for i in range(200)]
    try:
        await asyncio.wait_for(all_started.wait(), 30)
        assert len(entered) == 200  # No implicit 100-connection/provider cap.
    finally:
        release.set()
        results = await asyncio.gather(*requests, return_exceptions=True)
        stop.set()
        await monitor
        await clock
        await providers.close()
        await web.aclose()
    assert all(isinstance(result, dict) and result["action"] == "keep" for result in results), results
    assert len(created) == 1 and created[0][0] != event_thread and created[0][1] is None
    assert len(preparation_threads) == 200 and event_thread not in preparation_threads
    assert len(latencies) >= 3
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    print(f"200 requests: HTTP p95={p95:.3f}s max={max(latencies):.3f}s; loop gap max={max(loop_gaps):.3f}s; samples={len(latencies)}")
    # Exercise a worst-case burst (all 200 responses finish together). Require
    # normal interactive latency plus a bounded spike, not a subsecond SLA for
    # every disk operation on a shared Windows development machine.
    assert p95 < 0.5
    assert max(latencies) < 2.0
    assert max(loop_gaps) < 1.0


@pytest.mark.asyncio
async def test_shared_client_keeps_credentials_and_timeouts_request_local(app, monkeypatch):
    providers = app.state.engine.providers
    conn = custom_conn() | {"auth_mode": "bearer", "protocol": "chat_completions"}
    key = "first-key"
    monkeypatch.setattr(providers, "api_key", lambda *args: key)
    seen = []

    def upstream(request):
        seen.append((request.url.host, request.headers["Authorization"], request.extensions["timeout"]["read"]))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    providers.transport = httpx.MockTransport(upstream)
    binding = {"connection_id": "custom_api", "model_id": "test"}
    try:
        await providers._custom(conn, binding, "one", {}, [])
        key = "changed-key"
        await providers._custom(conn | {"timeout_seconds": 17}, binding, "two", {}, [])
        assert len(providers.http_clients) == 1
        await providers._custom(conn | {"base_url": "http://second.test/v1"}, binding, "three", {}, [])
        assert len(providers.http_clients) == 2
        clients = [await task for task in providers.http_clients.values()]
    finally:
        await providers.close()
    assert seen == [("model.test", "Bearer first-key", 5), ("model.test", "Bearer changed-key", 17),
                    ("second.test", "Bearer changed-key", 5)]
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_cancelling_client_initialization_does_not_cancel_shared_pool(app, monkeypatch):
    providers = app.state.engine.providers
    providers.transport = httpx.MockTransport(lambda request: httpx.Response(200))
    original = httpx.AsyncClient
    started, release = threading.Event(), threading.Event()

    class SlowClient(original):
        def __init__(self, **kwargs):
            started.set()
            assert release.wait(5)
            super().__init__(**kwargs)

    monkeypatch.setattr("bookanalyst.llm.httpx.AsyncClient", SlowClient)
    first = asyncio.create_task(providers.custom_client("custom_api", custom_conn()))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(providers.custom_client("custom_api", custom_conn()))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        client = await second
        assert not client.is_closed
    finally:
        release.set()
        await providers.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_status_reads_do_not_wait_for_writer_lock(app):
    store, _, run = ready_run(app)
    entered, release = threading.Event(), threading.Event()

    def writer():
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            entered.set()
            assert release.wait(5)

    pending = asyncio.create_task(asyncio.to_thread(writer))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        current = await asyncio.wait_for(asyncio.to_thread(store.get, "run", run["id"]), 1)
        assert current["id"] == run["id"]
    finally:
        release.set()
        await pending


@pytest.mark.asyncio
async def test_cancelled_reservation_is_not_left_unresolved(app, monkeypatch):
    store, providers, run = app.state.store, app.state.engine.providers, ready_run(app)[2]
    entered, release = threading.Event(), threading.Event()
    original = store.reserve

    def reserve(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(store, "reserve", reserve)
    pending = asyncio.create_task(providers._reserve_call(run, {"purpose": "test"}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        release.set()
    assert [call["state"] for call in store.calls(run["id"])] == ["FAILED"]


@pytest.mark.asyncio
async def test_limit_change_during_async_read_is_not_lost():
    from bookanalyst.concurrency import TaskSlots
    entered, release = asyncio.Event(), asyncio.Event()
    reads = 0

    async def limit():
        nonlocal reads
        reads += 1
        if reads == 1:
            entered.set()
            await release.wait()
            return 1  # Snapshot taken before the user increased concurrency.
        return 2

    slots = TaskSlots(limit)
    slots.active = 1
    waiting = asyncio.create_task(slots.__aenter__())
    await entered.wait()
    slots.changed.set()
    release.set()
    await asyncio.wait_for(waiting, 1)
    assert slots.active == 2


@pytest.mark.asyncio
async def test_concurrent_image_read_waits_for_cancelled_writer(app, monkeypatch):
    from pathlib import Path
    _, engine, run = ready_run(app)
    entered, release = threading.Event(), threading.Event()
    original = Path.write_bytes
    monkeypatch.setattr("bookanalyst.engine.render_page", lambda *args: (b"complete-image", {}))

    def slow_write(path, data):
        original(path, b"partial")
        entered.set()
        assert release.wait(5)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", slow_write)
    first = asyncio.create_task(engine.image(run["source"], 1))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        second = asyncio.create_task(engine.image(run["source"], 1))
        first.cancel()
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done()
    finally:
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1].read_bytes() == b"complete-image"
