"""Request history preserves audit attempts while explaining batch outcomes."""

from fastapi.testclient import TestClient
from test_rebuild import make_run


def record(store, run, task, state, **metadata):
    call = store.reserve(run["id"], run["revision"], "llm", 1, {
        "task_id": task, "purpose": "convert", "model_id": "test-model",
        "attempt": 1, **metadata,
    })
    if state != "RESERVED":
        store.finish_call(call, state)
    return call


def test_exhausted_attempts_collapse_by_batch_before_pagination(app):
    store = app.state.store
    run = make_run(app)
    for batch in ["batch-0001", "batch-0002"]:
        for attempt in range(1, 6):
            record(store, run, batch, "FAILED", attempt=attempt,
                   error_code="RATE_LIMITED", error_message="429: slow down",
                   retry_exhausted=True)
    with TestClient(app) as client:
        url = f"/api/runs/{run['id']}/requests"
        data = client.get(url + "?grouped=true&limit=1").json()
        assert data["total"] == 2 and data["request_total"] == 10
        assert data["states"] == {"RETRY_EXHAUSTED": 2}
        row = data["rows"][0]
        assert row["attempt_count"] == row["failed_count"] == 5
        assert row["pages"] == [4, 5, 6]
        assert row["error_message"] == "429: slow down"
        assert "429" in row["reason"]
        assert len(row["attempts"]) == 5
        assert client.get(url + "?grouped=true&offset=1").json()["rows"][0]["task_id"] == "batch-0001"
        raw = client.get(url).json()
        assert raw["total"] == 10 and len(raw["rows"]) == 10


def test_retry_then_recovery_retains_error_and_total_usage(app):
    store = app.state.store
    run = make_run(app)
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    store.task(run["id"], "batch-0001", "convert", "RUNNING", [1, 2, 3])
    record(store, run, "batch-0001", "FAILED", error_code="REQUEST_FAILED",
           error_message="Selected model is at capacity. Please try a different model.")
    with TestClient(app) as client:
        url = f"/api/runs/{run['id']}/requests?grouped=true"
        assert client.get(url).json()["rows"][0]["state"] == "RETRYING"
        call = record(store, run, "batch-0001", "RESERVED", attempt=2)
        row = client.get(url).json()["rows"][0]
        assert row["state"] == "RETRYING" and row["reason"] == "模型服务繁忙"
        store.finish_call(call, "COMPLETED", usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})
        data = client.get(url).json()
        row = data["rows"][0]
        assert row["state"] == "RECOVERED" and row["attempt_count"] == 2
        assert row["failed_count"] == 1 and "at capacity" in row["error_message"]
        assert data["tokens"]["total"] == 18
        assert data["tokens"]["reported_calls"] == 1


def test_paused_or_terminal_failure_is_not_reported_as_retrying(app):
    store = app.state.store
    run = make_run(app)
    store.change(run["id"], lambda r: r.update(state="PAUSED"))
    store.task(run["id"], "batch-0001", "convert", "RUNNING", [1, 2, 3])
    record(store, run, "batch-0001", "RESULT_UNKNOWN", error_code="RESULT_UNKNOWN")
    record(store, run, "batch-0002", "FAILED", error_code="AUTH_REQUIRED")
    with TestClient(app) as client:
        data = client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()
        assert data["states"] == {"FAILED": 1, "RESULT_UNKNOWN": 1}
        record(store, run, "batch-0001", "RESERVED")
        assert client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()["rows"][0]["state"] == "RESULT_UNKNOWN"


def test_unrelated_requests_without_task_ids_do_not_merge(app):
    store = app.state.store
    run = make_run(app)
    record(store, run, None, "COMPLETED", input_hash="first")
    record(store, run, None, "COMPLETED", input_hash="second")
    with TestClient(app) as client:
        data = client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()
        assert data["total"] == 2
        assert data["states"] == {"COMPLETED": 2}


def test_completed_seam_waiting_to_merge_is_distinct_from_request_success(app):
    store = app.state.store
    run = make_run(app)
    tid = "seam-env-1-1"
    store.task(run["id"], tid, "seams", "WAITING_MERGE", [1, 2])
    record(store, run, tid, "COMPLETED", purpose="seams")
    with TestClient(app) as client:
        url = f"/api/runs/{run['id']}/requests?grouped=true"
        row = client.get(url).json()["rows"][0]
        assert row["state"] == "WAITING_MERGE"
        assert row["attempts"][0]["state"] == "COMPLETED"
        store.task(run["id"], tid, "seams", "PASSED", [1, 2])
        assert client.get(url).json()["rows"][0]["state"] == "COMPLETED"
