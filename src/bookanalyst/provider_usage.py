"""Local provider usage ledger, normalized from the existing request receipts."""
import json
import time


def usage_ledger(store, days=7):
    cutoff = time.time() - days * 86400 if days else 0
    with store.connection(read_only=True) as db:
        rows = db.execute(
            "SELECT id,run_id,state,metadata FROM calls WHERE "
            "CAST(json_extract(metadata, '$.started_at') AS REAL)>=? ORDER BY rowid DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for row in rows:
        meta = json.loads(row["metadata"])
        usage = meta.get("usage") or {}
        usage = usage.get("tokens") or usage
        usage = usage.get("total") or usage
        if not isinstance(usage, dict):
            usage = {}
        def number(*names):
            return next((usage[n] for n in names if isinstance(usage.get(n), (int, float))), None)
        result.append({
            "id": row["id"], "run_id": row["run_id"], "state": row["state"],
            "provider": meta.get("connection_id", ""),
            "model": meta.get("model_id", ""), "purpose": meta.get("purpose", ""),
            "started_at": meta.get("started_at", 0),
            "input": number("input_tokens", "prompt_tokens", "inputTokens", "promptTokenCount"),
            "output": number("output_tokens", "completion_tokens", "outputTokens", "candidatesTokenCount"),
            "cached": number("cached_input_tokens", "cachedInputTokens", "cachedContentTokenCount", "cache_read_input_tokens")
                or (usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}).get("cached_tokens"),
        })
    return result

