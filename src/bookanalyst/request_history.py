"""Summarize request returns and validation outcomes without changing execution."""

import json
from collections import Counter
from .store import digest


def validation_history(calls, directory, tasks):
    """Recover old rejection evidence from receipts, without rewriting history."""
    calls = [c | {"metadata": dict(c["metadata"])} for c in calls]
    previous = {}

    def rejected(task_id, purpose, feedback):
        if not feedback or feedback.get("candidate") is None:
            return
        call = previous.get((task_id, purpose, digest(feedback["candidate"])))
        if call is not None:
            meta = call["metadata"]
            meta.setdefault("validation_state", "FAILED")
            meta.setdefault("validation_error", {
                "code": feedback.get("code"), "message": feedback.get("error"),
            })

    def read_feedback(path, request=False):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if request:
                data = json.loads(data["prompt"])
            return data.get("repair_feedback" if request else "feedback") if isinstance(data, dict) else None
        except (OSError, ValueError, KeyError, TypeError):
            # Other agents use plain-text prompts; only structured feedback is evidence.
            return None

    for call in calls:
        meta = call["metadata"]
        if meta.get("repair"):
            rejected(meta.get("task_id"), meta.get("purpose"),
                     read_feedback(directory.parent / "requests" / call["id"] / "request.json", True))
        if meta.get("output_hash"):
            previous[(meta.get("task_id"), meta.get("purpose"), meta["output_hash"])] = call
    # The last rejection at the cap has no subsequent request carrying feedback.
    for task in tasks:
        if (task.get("error") or {}).get("code") == "REPAIR_LIMIT" or (task.get("error") or {}).get("repairing"):
            rejected(task["id"], task["stage"],
                     read_feedback(directory / "repairs" / (task["id"] + ".json")))
    return calls


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
    for row in rows:  # newest first
        key = (row["purpose"], row.get("task_id") or row.get("input_hash") or row["id"])
        groups.setdefault(key, []).append(row)
    result = []
    for key, attempts in groups.items():
        latest = attempts[0]
        task = tasks.get(latest.get("task_id"), {})
        review = task.get("error") or {}
        validations = [r for r in attempts if r.get("validation_error") or r.get("error") == "SCHEMA_ERROR"]
        failures = [r for r in attempts if r["state"] in ("FAILED", "RESULT_UNKNOWN") and r.get("error") != "SCHEMA_ERROR"]
        repair_count = sum(bool(r.get("repair")) and r.get("attempt", 1) == 1 for r in attempts)
        state = latest["state"]
        active = run["state"] == "RUNNING" and not run.get("pause_requested")
        if state == "COMPLETED":
            state = "RETURNED"
        elif state == "RESERVED":
            state = ("RETRYING" if failures else "RUNNING") if active else "RESULT_UNKNOWN"
        elif state in ("FAILED", "RESULT_UNKNOWN"):
            if latest.get("retry_exhausted"):
                state = "RETRY_EXHAUSTED"
            elif active and task.get("state") == "RUNNING":
                state = "RETRYING"
        if latest.get("validation_state") == "FAILED" or latest.get("error") == "SCHEMA_ERROR":
            state = "REPAIRING" if active and task.get("state") == "RUNNING" else "VALIDATION_FAILED"
        task_state = task.get("state")
        if task_state in ("PASSED", "WAITING_MERGE", "DEFERRED", "REPAIR_REQUIRED", "PAUSED", "NEEDS_REVIEW"):
            state = "REPAIR_EXHAUSTED" if review.get("code") == "REPAIR_LIMIT" else task_state
        elif task_state == "RUNNING":
            if active and (review.get("repairing") or (latest.get("repair") and latest["state"] == "RESERVED")):
                state = "REPAIRING"
            elif latest["state"] == "COMPLETED" and state == "RETURNED":
                state = "VALIDATING"

        recent_error = failures[0] if failures else latest
        reason = failure_reason(recent_error)
        error, message = recent_error.get("error"), recent_error.get("error_message")
        if validations:
            item = validations[0]
            rejection = item.get("validation_error") or {"code": item.get("error"), "message": item.get("error_message")}
            reason, error, message = "最近一次校验失败", rejection.get("code"), rejection.get("message")
        if review:
            error, message = review.get("code"), review.get("message")
            reason = {
                "REPAIR_EXHAUSTED": "本轮自动修复已达上限",
                "DEFERRED": "文献引用待确认，已交给编译阶段",
                "REPAIRING": "校验未通过，正在自动修复",
            }.get(state, "条目需要处理")
        result.append(latest | {
            "group_id": ":".join(key),
            "state": state,
            "pages": task.get("pages", []),
            "attempt_count": len(attempts),
            "failed_count": len(failures),
            "validation_failed_count": len(validations),
            "repair_count": repair_count,
            "reason": reason,
            "error": error,
            "error_message": message,
            "attempts": attempts,
        })
    return result, dict(Counter(row["state"] for row in result))
