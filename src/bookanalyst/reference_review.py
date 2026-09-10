"""Unconfirmed references are user records, not compiler-agent repair tasks."""
import json
import re
from html import escape

from fastapi.responses import HTMLResponse, JSONResponse


def review_records(directory):
    for name in ("reference-review.json", "bibliography-review.json"):
        path = directory / name
        if path.exists():
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Invalid reference review record")
            return records
    return []


def compiler_reference_policy(project):
    keys = sorted({r["key"] for r in review_records(project) if r.get("key") and r.get("reason", "").strip()})
    if not keys:
        return ""
    return ("\nThe following unresolved references are reserved for USER confirmation, not resolved: "
            + json.dumps(keys, ensure_ascii=False)
            + ". Their undefined-reference warnings do not block completion. "
              "Do not investigate, rebind, delete or create targets for them; leave their commands and review records unchanged. "
              "Do not read the review files for repair tasks. Fix other compilation errors only. "
              "This scope also applies when continuing an older compiler session.")


UNDEFINED_WARNING = re.compile(
    r"(?:LaTeX|Package\s+\S+)\s+Warning:\s+(?:Reference|Citation)\s+[`'](?P<key>[^']+)'"
    r"[^.]*?\bundefined[^.]*?\.", re.IGNORECASE)


def compiler_diagnostics(output, pending):
    """Exclude only named, registered unresolved-reference warnings; keep the actual log intact."""
    deferred = set()
    def known(match):
        key = re.sub(r"\s+", "", match["key"])
        if key in pending:
            deferred.add(key)
            return ""
        return match[0]
    active = UNDEFINED_WARNING.sub(known, output)
    if deferred:
        active = active.replace("LaTeX Warning: There were undefined references.", "")
    return active, sorted(deferred)


def review_response(records, title, source_url, download=False):
    if download:
        return JSONResponse(records, headers={"Content-Disposition": 'attachment; filename="reference-review.json"'})
    rows = []
    for item in records:
        page = item.get("page")
        location = f'<a href="{escape(source_url)}#page={int(page)}" target="_blank" rel="noopener">原书第 {int(page)} 页</a>' if page else "页码未记录"
        evidence = escape(json.dumps(item.get("search_evidence", []), ensure_ascii=False, indent=2))
        rows.append(f'<article><h2>{escape(str(item.get("key", "")))}</h2><p>{location}</p>'
                    f'<p>{escape(str(item.get("reason", "")))}</p>'
                    f'<details><summary>查找记录</summary><pre>{evidence}</pre></details></article>')
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>引用待确认</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;'
                        'color:#243e36;background:#f6f9f7}article{background:white;border:1px solid #dce5df;border-radius:10px;'
                        'padding:20px;margin:16px 0}h2{font-size:18px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
                        'a{color:#216e56}</style><h1>引用待确认</h1><p>' + escape(title)
                        + f'</p><p>共 {len(records)} 条，保留给用户核对；这些记录不阻塞编译。</p>'
                        '<p><a href="?download=true">下载完整记录</a></p>' + ''.join(rows) + '</html>')
