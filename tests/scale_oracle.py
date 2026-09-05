"""Synthetic semantic oracle mapped onto all physical pages of the real test PDF.

This tests orchestration, not MinerU OCR quality or model correctness. No external service is used.
"""
import asyncio
import json
from collections import Counter
from pathlib import Path
import httpx

from bookanalyst.store import atomic_json, encode


class BookOracle:
    def __init__(self, page_count=532):
        self.nodes, self.by_text, self.paths, self.pages = {}, {}, {}, []
        self.calls, self.in_flight, self.peak = 0, 0, 0
        self.fail_converter_once = False
        self.fail_structure_once = False
        self.structure_failed = False
        self.structured = Counter()
        self.failed = False
        self.converted = Counter()
        self.chunk_pages = []
        chapter = section = proof = None
        eq_count = 0
        pending = None

        def add(kind, text, parent=None, number=None, title="", continuation=None):
            if continuation:
                node = continuation
                node["texts"].append(text)
            else:
                nid = f"node{len(self.nodes)}"
                node = {"id": nid, "parent_id": parent, "kind": kind, "number": number,
                        "title": title, "texts": [text]}
                self.nodes[nid] = node
            self.by_text[text] = node["id"]
            blocks.append({"type": "interline_equation" if kind == "equation" else "title" if kind in (
                "chapter", "section") else "text", "bbox": [40, 30 + len(blocks)*65, 550, 85 + len(blocks)*65],
                "lines": [{"spans": [{"type": "interline_equation" if kind == "equation" else "text",
                                      "content": text}]}]})
            path = [n for n in (chapter, section, proof) if n]
            self.paths[text] = path
            return node

        for page in range(page_count):
            blocks = []
            ch_num = page // 28 + 1
            if page % 28 == 0:
                chapter = add("chapter", f"Chapter {ch_num} — Synthetic estimates",
                              number=str(ch_num), title=f"Synthetic estimates {ch_num}")["id"]
                section, proof, eq_count = None, None, 0
                self.paths[next(reversed(self.by_text))] = [chapter]
            if page % 7 == 0:
                sec_num = f"{ch_num}.{page % 28 // 7 + 1}"
                section = add("section", f"Section {sec_num} — Boundary arguments", chapter,
                              sec_num, f"Boundary arguments {sec_num}")["id"]
                self.paths[next(reversed(self.by_text))] = [chapter, section]
            if page % 28 == 2:
                proof = add("proof", f"We begin the extended proof for chapter {ch_num}.", section)["id"]
                self.paths[next(reversed(self.by_text))] = [chapter, section, proof]
            if pending:
                text = r"+u_2}{2}=0"
                add("equation", text, continuation=pending)
                pending = None
            for paragraph in range(4):
                text = (f"Source block {page}:{paragraph}. " +
                        "The estimate follows by integrating the preceding identity over the domain. " * 5)
                if (page, paragraph) == (0, 0):
                    text += f" See equation ({(page_count+27)//28}.1)."
                elif (page, paragraph) == (page_count-1, 3):
                    text += " See equation (1.1)."
                add("paragraph", text, proof or section)
            eq_count += 1
            add("equation", f"u_{{{page}}}={page}+1", proof or section, f"{ch_num}.{eq_count}")
            if page == 199:
                eq_count += 1
                pending = add("equation", r"\frac{u_1", proof or section, f"{ch_num}.{eq_count}")
            if page % 28 == 4:
                last_text = next(reversed(self.by_text))
                proof = None
                self.paths[last_text] = [chapter, section]
            self.pages.append({"page_idx": page, "page_size": [600, 850], "para_blocks": blocks,
                               "discarded_blocks": [{"type": "page_number", "bbox": [250, 800, 300, 825],
                                                     "text": f"FOOTER {page+1}"}]})
        self.chapter_count = (page_count + 27) // 28
        self.toc_text = "\n".join(["Contents"] + [
            f"{i}. Synthetic estimates {i}" for i in range(1, self.chapter_count + 1)])
        self.by_text[self.toc_text] = None
        self.pages[0]["para_blocks"].insert(0, {"type": "text", "bbox": [20, 10, 550, 200], "text": self.toc_text})

    async def parse(self, run, chunk, source, target):
        # Parser protocol double: physical PDF splitting and the production normalizer still run.
        self.chunk_pages.append(chunk["pages"])
        store = self.store
        key = store.reserve(run["id"], run["revision"], "parse", len(chunk["pages"]), {"test_double": True})
        layout = {"pdf_info": [self.pages[p] | {"page_idx": i} for i, p in enumerate(chunk["pages"])]}
        assets = Path(target) / "result"
        atomic_json(assets / "layout.json", layout)
        store.finish_call(key, "COMPLETED", test_double=True)
        return layout, assets, "layout.json"

    def structure(self, payload):
        source, context = payload["source"], payload["context"]
        texts = [a["text"] for a in source if a["text"] != self.toc_text]
        ids_by_text = {a["text"]: a["atom_id"] for a in source}
        keys = list(dict.fromkeys(self.by_text[t] for t in texts))
        new_keys = [key for key in keys if self.nodes[key]["texts"][0] in texts]
        incoming = context["open_path"]

        def global_parent(key):
            if key is None or key in new_keys:
                return key
            truth = self.nodes[key]
            match = [n for n in incoming if all(n[k] == truth[k] for k in ("kind", "title", "number"))]
            assert len(match) == 1, ("Missing semantic carry", key, truth, incoming)
            return match[0]["id"]

        result, continuations = [], []
        for key in keys:
            node = self.nodes[key]
            owned = [ids_by_text[t] for t in node["texts"] if t in ids_by_text]
            if key not in new_keys:
                continuations.append({"node_id": context["previous_node"]["id"], "atom_ids": owned,
                                      "evidence": "Oracle: the same displayed formula continues at this boundary."})
            else:
                result.append({k: node[k] for k in ("id", "kind", "title", "number")} |
                              {"parent_id": global_parent(node["parent_id"]), "atom_ids": owned})
        toc = []
        if self.toc_text in ids_by_text:
            for index in range(1, self.chapter_count + 1):
                toc.append({"atom_ids": [ids_by_text[self.toc_text]] if index == 1 else [],
                            "target": {"kind": "chapter", "title": f"Synthetic estimates {index}", "number": str(index)}})
        references = []
        for atom in source:
            if " See equation (" in atom["text"]:
                number = atom["text"].rsplit(" See equation (", 1)[1].split(")", 1)[0]
                references.append({"source_atom_id": atom["atom_id"], "text": f"equation ({number})",
                    "target": {"kind": "equation", "number": number, "title": ""},
                    "external": False, "evidence": "Explicit reference in the source paragraph."})
        return {"nodes": result, "continuations": continuations, "toc": toc, "references": references,
                "open_path": [global_parent(key) for key in self.paths[texts[-1]]] if texts else [n["id"] for n in incoming],
                "findings": []}

    async def http(self, request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"][0]["text"])
        schema = json.loads(body["messages"][0]["content"].split("Return JSON satisfying this schema: ", 1)[1])
        fields = set(schema["properties"])
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.001)
            if "task_id" in fields:
                if self.fail_converter_once and not self.failed and sum(self.converted.values()) >= 12:
                    self.failed = True
                    return httpx.Response(503)
                task = payload["task"]
                self.converted[task["task_id"]] += 1
                result = {"task_id": task["task_id"], "profile_hash": task["profile_hash"],
                          "input_hash": task["input_hash"],
                          "nodes": [{"atom_id": aid, "kind": "source_ref"} for aid in task["owned_atom_ids"]],
                          "changes": [], "findings": []}
            elif "decision" in fields:
                result = {"decision": "PASS", "source_ids": [a["atom_id"] for a in payload["source"]],
                          "evidence": "Synthetic oracle review; no real model inference.", "findings": []}
            elif "dispositions" in fields:
                result = {"dispositions": [{"atom_id": a["atom_id"],
                            "action": "EXCLUDE" if a["type"] == "page_number" else "KEEP",
                            "reason": "Synthetic fixture page furniture" if a["type"] == "page_number" else "Content"} 
                           for a in payload["source"]], "findings": []}
            elif "open_path" in fields:
                if self.fail_structure_once and not self.structure_failed and sum(self.structured.values()) >= 12:
                    self.structure_failed = True
                    return httpx.Response(503)
                signature = tuple(a["atom_id"] for a in payload["source"])
                self.structured[signature] += 1
                result = self.structure(payload)
            elif fields == {"counter_candidates", "findings"}:
                result = {"counter_candidates": [
                    {"id": "chapter-reset", "counters": [{"id": "Equations", "types": ["equation"],
                      "reset": "chapter", "prefix": "chapter"}], "exceptions": []},
                    {"id": "book-reset", "counters": [{"id": "Equations", "types": ["equation"],
                      "reset": "book", "prefix": "chapter"}], "exceptions": []}], "findings": []}
            else:
                raise AssertionError(fields)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)},
                                                         "finish_reason": "stop"}]})
        finally:
            self.in_flight -= 1
