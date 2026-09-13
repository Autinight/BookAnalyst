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
        assert row["state"] == "VALIDATING" and row["attempt_count"] == 2
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
        assert data["states"] == {"RETURNED": 2}


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
        assert client.get(url).json()["rows"][0]["state"] == "PASSED"


def test_repair_limit_overrides_returned_requests_and_keeps_raw_states(app):
    store = app.state.store
    run = make_run(app)
    tid = "seam-0004"
    for i in range(9):
        record(store, run, tid, "COMPLETED", purpose="seams", repair=i > 0,
               validation_state="FAILED",
               validation_error={"code": "STALE_PATCH", "message": "wrong boundary"})
    store.task(run["id"], tid, "seams", "NEEDS_REVIEW", [25, 26],
               {"code": "REPAIR_LIMIT", "message": "8 repairs exhausted"})
    before = store.calls(run["id"])
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()["rows"][0]
    assert row["state"] == "REPAIR_EXHAUSTED"
    assert row["error"] == "REPAIR_LIMIT" and row["error_message"] == "8 repairs exhausted"
    assert row["failed_count"] == 0 and row["validation_failed_count"] == 9
    assert row["repair_count"] == 8
    assert all(a["state"] == "COMPLETED" for a in row["attempts"])
    assert store.calls(run["id"]) == before
    assert store.tasks(run["id"], "seams")[0]["state"] == "NEEDS_REVIEW"


def test_repairing_then_passed_retains_validation_failure_history(app):
    store = app.state.store
    run = make_run(app)
    tid = "batch-0001"
    record(store, run, tid, "COMPLETED", validation_state="FAILED",
           validation_error={"code": "INVALID_RESULT", "message": "<bad page>"})
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    store.task(run["id"], tid, "convert", "RUNNING", [1, 2, 3],
               {"code": "INVALID_RESULT", "message": "<bad page>", "repairing": True})
    with TestClient(app) as client:
        url = f"/api/runs/{run['id']}/requests?grouped=true"
        row = client.get(url).json()["rows"][0]
        assert row["state"] == "REPAIRING"
        assert row["error_message"] == "<bad page>"
        repair = record(store, run, tid, "RESERVED", repair=True)
        # ask() clears task.error while the next request is in flight.
        store.task(run["id"], tid, "convert", "RUNNING", [1, 2, 3])
        assert client.get(url).json()["rows"][0]["state"] == "REPAIRING"
        store.finish_call(repair, "COMPLETED", validation_state="PASSED")
        store.task(run["id"], tid, "convert", "PASSED", [1, 2, 3])
        row = client.get(url).json()["rows"][0]
    assert row["state"] == "PASSED" and row["repair_count"] == 1
    assert row["failed_count"] == 0 and row["validation_failed_count"] == 1
    assert row["attempts"][1]["validation_error"]["message"] == "<bad page>"


def test_schema_errors_are_validation_failures_not_transport_failures(app):
    store = app.state.store
    run = make_run(app)
    record(store, run, "batch-0001", "FAILED", error_code="SCHEMA_ERROR")
    record(store, run, "batch-0001", "FAILED", error_code="RATE_LIMITED")
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{run['id']}/requests?grouped=true").json()["rows"][0]
    assert row["failed_count"] == row["validation_failed_count"] == 1


def test_old_receipts_recover_rejections_including_final_repair_cap(app):
    import json
    from bookanalyst.store import atomic_json, digest

    store = app.state.store
    run = make_run(app)
    rid, tid = run["id"], "seam-0004"
    first, last = {"value": 1}, {"value": 2}
    one = record(store, run, tid, "COMPLETED", purpose="seams", output_hash=digest(first))
    two = record(store, run, tid, "COMPLETED", purpose="seams", output_hash=digest(last), repair=True)
    atomic_json(store.root / "runs" / rid / "requests" / two / "request.json",
                {"prompt": json.dumps({"repair_feedback": {"candidate": first, "code": "STALE_PATCH", "error": "first rejected"}})})
    atomic_json(store.directory(rid) / "repairs" / (tid + ".json"),
                {"feedback": {"candidate": last, "code": "STALE_PATCH", "error": "last rejected"}})
    store.task(rid, tid, "seams", "NEEDS_REVIEW", [25, 26],
               {"code": "REPAIR_LIMIT", "message": "cap"})
    before = store.calls(rid)
    with TestClient(app) as client:
        row = client.get(f"/api/runs/{rid}/requests?grouped=true").json()["rows"][0]
    assert row["validation_failed_count"] == 2 and row["state"] == "REPAIR_EXHAUSTED"
    assert [r["validation_error"]["message"] for r in row["attempts"]] == ["last rejected", "first rejected"]
    assert store.calls(rid) == before


def test_validation_record_keeps_receipt_timing_usage_and_prior_error(app):
    from bookanalyst.store import WorkflowError, digest

    store = app.state.store
    run = make_run(app)
    candidate = {"value": "same cached result"}
    call = record(store, run, "batch-0001", "COMPLETED", output_hash=digest(candidate), usage={"total_tokens": 42})
    before = store.calls(run["id"])[0]
    store.record_validation(run["id"], "batch-0001", candidate, WorkflowError("STALE_PATCH", "old validator"))
    store.record_validation(run["id"], "batch-0001", candidate)
    after = store.calls(run["id"])[0]
    assert after["state"] == before["state"] == "COMPLETED"
    assert after["metadata"]["finished_at"] == before["metadata"]["finished_at"]
    assert after["metadata"]["usage"] == before["metadata"]["usage"]
    assert after["metadata"]["validation_state"] == "PASSED"
    assert after["metadata"]["validation_error"]["message"] == "old validator"


def test_transport_retries_do_not_inflate_repair_round_count():
    from bookanalyst.request_history import group_requests

    attempts = [
        {"id": "retry", "task_id": "x", "purpose": "seams", "state": "COMPLETED", "repair": True, "attempt": 2},
        {"id": "first", "task_id": "x", "purpose": "seams", "state": "FAILED", "repair": True, "attempt": 1, "error": "RATE_LIMITED"},
    ]
    rows, _ = group_requests(attempts, {"state": "RUNNING"}, [{"id": "x", "state": "PASSED"}])
    assert rows[0]["repair_count"] == 1
    assert rows[0]["failed_count"] == 1 and rows[0]["state"] == "PASSED"


def test_plain_text_repair_receipt_has_no_invented_validation_failure(tmp_path):
    from bookanalyst.request_history import validation_history
    from bookanalyst.store import atomic_json

    directory = tmp_path / "v7"
    atomic_json(tmp_path / "requests" / "call" / "request.json", {"prompt": "Compile this project."})
    calls = [{"id": "call", "state": "COMPLETED", "metadata": {"repair": True, "purpose": "compile_repair"}}]
    assert validation_history(calls, directory, []) == calls
