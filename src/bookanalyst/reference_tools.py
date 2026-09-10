"""Read-only, paginated book access shared by concurrent reference workers."""

import asyncio
import json

from .store import WorkflowError


def compact_symbol(item):
    return {k: v for k, v in item.items() if k not in ("start", "end")}


BIBLIOGRAPHY_INSTRUCTION = (
    "For bibliography references, first inspect bibliography_entry_pages (or locate the bibliography near tail_pages if none are indexed). "
    "If the identifier is absent, compare the citation context, authors and titles; similar spelling or subject alone is not enough. "
    "After checking, if identity remains uncertain, preserve the original reference and return "
    "unconfirmed_bibliography [{id,reason}] with concise search evidence, candidates and why none is confirmed. "
    "This is a final result, not a tool request; leave those occurrences out of reference_edits and empty the tool arrays. "
    "Only missing bibliography references may use this exit; otherwise unconfirmed_bibliography must be empty. "
)


REFERENCE_CONFIRMATION_INSTRUCTION = (
    "For a missing non-bibliography reference, after checking relevant targets and source pages, "
    "if identity is still uncertain (including a possible source typo), preserve it and return "
    "unconfirmed_references [{id,reason}]. Briefly state where you checked and why no target is confirmed. "
    "This completes the group as pending confirmation; do not repeat searches or return empty edits. "
    "Do not invent targets or bind by matching numbers alone. Confirmed conversion defects use repair_requests instead. "
    "Only this group's unchanged missing references qualify; duplicate labels must be repaired. "
    "Submit with empty tool/repair arrays and no edits to those occurrences. Otherwise leave unconfirmed_references empty. "
)


def unconfirmed_items(changes):
    return changes.get("unconfirmed_bibliography", []) + changes.get("unconfirmed_references", [])


def reference_evidence(history):
    """Keep actual query locations and outcomes, not repeated page bodies or images."""
    return [{"search": [{"request": s["request"], "total": s.get("total"), "error": s.get("error")}
                        for s in record["result"]["search_results"]],
             "read_context": record["request"].get("read_context", []),
             "view_pages": record["result"]["view_pages"]} for record in history]


class ReferenceLibrary:
    def __init__(self, source, results, index):
        self.source = source
        self.source_text = None
        self.source_lock = asyncio.Lock()
        self.update(results, index)

    def update(self, results, index):
        self.pages = {p["page"]: p["tex"] for r in results for p in r["pages"]}
        self.index = index
        self.targets = {t["id"]: t for t in index["targets"]}

    def validate(self, action):
        for query in action.get("search", []):
            if not query["query"].strip():
                raise WorkflowError("SEARCH_QUERY", "搜索词不能为空")
        for request in action.get("read_context", []):
            page = request["page"]
            if page not in self.pages:
                raise WorkflowError("READ_RANGE", "该页没有转换正文，请用 view_pages 查看原页")
            size = len(self.pages[page].splitlines())
            if request["start_line"] > max(1, size) or (
                request["end_line"] and request["end_line"] < request["start_line"]
            ):
                raise WorkflowError("READ_RANGE", "正文行范围无效；end_line=0 表示读到本页末尾")
        if any(not 1 <= p <= self.source["page_count"] for p in action.get("view_pages", [])):
            raise WorkflowError("PAGE_SCOPE", "请求的原页超出原始 PDF 范围")

    async def original_text(self):
        async with self.source_lock:
            if self.source_text is None:
                def extract():
                    from pypdf import PdfReader
                    reader = PdfReader(self.source["path"])
                    return {i + 1: page.extract_text() or "" for i, page in enumerate(reader.pages)}
                self.source_text = await asyncio.to_thread(extract)
        return self.source_text

    async def query(self, action):
        self.validate(action)
        searches, contexts, known = [], [], set()
        for query in action.get("search", []):
            term = query["query"].casefold()
            hits = []
            if query["scope"] == "labels":
                hits = [compact_symbol(t) for t in self.index["targets"]
                        if term in (t["key"] + " " + t["context"]).casefold()]
            else:
                try:
                    pages = await self.original_text() if query["scope"] == "source" else self.pages
                except Exception as exc:
                    searches.append({"request": query, "error": "原 PDF 文字提取失败，可改用 view_pages 查看原页：" + type(exc).__name__})
                    continue
                for page, text in sorted(pages.items()):
                    for line, content in enumerate(text.splitlines(), 1):
                        if term in content.casefold():
                            pos = content.casefold().find(term)
                            hits.append({"page": page, "line": line,
                                         "text": content[max(0, pos - 120):pos + len(term) + 240]})
            start, limit = query["offset"], query["limit"]
            selected = hits[start:start + limit]
            known.update(h["id"] for h in selected if "id" in h)
            searches.append({"request": query, "matches": selected, "total": len(hits),
                             "next_offset": start + limit if start + limit < len(hits) else None})
        for request in action.get("read_context", []):
            page = request["page"]
            text = self.pages[page]
            lines = text.splitlines()
            start, end = request["start_line"], request["end_line"] or len(lines)
            contexts.append({"page": page, "start_line": start, "end_line": min(end, len(lines)),
                             "total_lines": len(lines), "text": "\n".join(lines[start - 1:end])})
            known.update(t["id"] for t in self.index["targets"] if t["page"] == page
                         and start <= text[:t["start"]].count("\n") + 1 <= end)
        viewed = set(action.get("view_pages", []))
        known.update(t["id"] for t in self.index["targets"] if t["page"] in viewed)
        return {"search_results": searches, "contexts": contexts,
                "view_pages": sorted(viewed),
                "discovered_targets": [compact_symbol(t) for t in self.index["targets"] if t["id"] in known]}
