"""Summarize request attempts without changing their stored audit records."""

from collections import Counter


def failure_reason(row):
    message = row.get("error_message") or ""
    code = row.get("error") or ("RESULT_UNKNOWN" if row["state"] == "RESULT_UNKNOWN" else None)
    if "at capacity" in message.lower():
        return "模型服务繁忙"
    return {
        "RATE_LIMITED": "服务限流或额度不足（HTTP 429）",
        "AUTH_REQUIRED": "模型服务鉴权失败",
        "INVALID_OUTPUT_SCHEMA": "请求格式被服务拒绝",
        "SCHEMA_ERROR": "返回内容格式不符合要求",
        "RESULT_UNKNOWN": "未收到完整结果",
        "REQUEST_FAILED": "模型请求失败",
        "PAUSED": "请求已暂停",
    }.get(code, "请求失败" if row["state"] == "FAILED" else "")


def group_requests(rows, run, tasks):
    tasks = {t["id"]: t for t in tasks}
    groups = {}
    # Input is newest first. Keep the latest attempt as each group's current state.
    for row in rows:
        key = (row["purpose"], row.get("task_id") or row.get("input_hash") or row["id"])
        groups.setdefault(key, []).append(row)
    result = []
    for key, attempts in groups.items():
        latest = attempts[0]
        task = tasks.get(latest.get("task_id"), {})
        failures = [r for r in attempts if r["state"] in ("FAILED", "RESULT_UNKNOWN")]
        state = latest["state"]
        active = run["state"] == "RUNNING" and not run.get("pause_requested")
        if state == "COMPLETED":
            state = "RECOVERED" if failures else "COMPLETED"
        elif state == "RESERVED":
            state = ("RETRYING" if failures else "RUNNING") if active else "RESULT_UNKNOWN"
        elif state in ("FAILED", "RESULT_UNKNOWN"):
            if latest.get("retry_exhausted"):
                state = "RETRY_EXHAUSTED"
            elif active and task.get("state") == "RUNNING":
                state = "RETRYING"
        if task.get("state") == "WAITING_MERGE" and latest["state"] == "COMPLETED":
            state = "WAITING_MERGE"
        deferred = task.get("state") == "DEFERRED" and latest["state"] == "COMPLETED"
        if deferred:
            state = "DEFERRED"
        if task.get("state") == "REPAIR_REQUIRED" and latest["state"] == "COMPLETED":
            state = "REPAIR_REQUIRED"
        review = task.get("error") or {}
        recent_error = failures[0] if failures else latest
        result.append(latest | {
            "group_id": ":".join(key),
            "state": state,
            "pages": task.get("pages", []),
            "attempt_count": len(attempts),
            "failed_count": len(failures),
            "reason": "文献引用待确认，已交给编译阶段" if deferred else failure_reason(recent_error),
            "error": review.get("code") if deferred else recent_error.get("error"),
            "error_message": review.get("message") if deferred else recent_error.get("error_message"),
            "attempts": attempts,
        })
    return result, dict(Counter(row["state"] for row in result))
