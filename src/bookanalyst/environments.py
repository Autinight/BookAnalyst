"""Literal environment evidence and durable seam obligations."""

import re

TOKEN = re.compile(r"(?<!\\)(?:\\(begin|end|label)\{([^{}\s]+)\}|\\([\[\]]))")
ENV_TOKEN = re.compile(r"(?<!\\)(?:\\(?:begin|end)\{[^{}\s]+\}|\\[\[\]])")


def environment_token_name(token):
    """Return the environment type represented by a literal TeX token."""
    if token in (r"\[", r"\]"):
        return "displaymath"
    return token.partition("{")[2][:-1]


def scan_environments(pages, *, independent=False):
    """Optionally pair each environment name independently for local seam work."""
    stack, opened, unmatched, after_page = [], {}, [], {}
    for page in pages:
        ordinal = end_ordinal = 0
        line_offset = 0
        for line_number, line in enumerate(page["tex"].split("\n"), 1):
            offset = line_offset
            line_offset += len(line) + 1
            # A percent is escaped only after an odd number of backslashes.
            for i, char in enumerate(line):
                if char == "%" and (i - len(line[:i].rstrip("\\"))) % 2 == 0:
                    line = line[:i]
                    break
            for match in TOKEN.finditer(line):
                operation, name, delimiter = match.groups()
                if delimiter:
                    operation, name = ("begin", "displaymath") if delimiter == "[" else ("end", "displaymath")
                if operation == "label":
                    if stack and not stack[-1]["label"]:
                        kind = stack[-1]["name"].rstrip("*")
                        if kind in ("align", "alignat", "gather", "multline", "eqnarray", "flalign", "displaymath"):
                            kind = "equation"
                        if name.partition(":")[0] == kind:
                            stack[-1]["label"] = name
                    continue
                location = {"page": page["page"], "line": line_number}
                if operation == "begin":
                    ordinal += 1
                    item = {"id": f"env-{page['page']}-{ordinal}", "name": name,
                            "label": "", "begin_page": page["page"], "begin_line": line_number,
                            "begin_offset": offset + match.start(), "end": None}
                    opened[item["id"]] = item
                    stack.append(item)
                else:
                    match_index = next((i for i in range(len(stack) - 1, -1, -1)
                                        if stack[i]["name"] == name), None) if independent else (
                        len(stack) - 1 if stack and stack[-1]["name"] == name else None
                    )
                    if match_index is not None:
                        item = stack.pop(match_index)
                        item["end"] = location
                        item["end_offset"] = offset + match.start()
                    else:
                        end_ordinal += 1
                        unmatched.append({"id": f"end-{page['page']}-{end_ordinal}", "name": name,
                                          "offset": offset + match.start(), **location})
        after_page[page["page"]] = [item["id"] for item in stack]
    return {"opened": opened, "unclosed": [item["id"] for item in stack],
            "unmatched": unmatched, "after_page": after_page}


def environment_report(pages):
    scan = scan_environments(pages)
    return [{k: scan["opened"][key][k] for k in ("name", "label", "begin_page", "begin_line")}
            for key in scan["unclosed"]]


def seam_obligations(results):
    pages = [p for result in results for p in result["pages"]]
    scan = scan_environments(pages)
    boundary = {}
    for result in results[:-1]:
        page = result["pages"][-1]["page"]
        for key in scan["after_page"][page]:
            boundary.setdefault(key, page)
    # A still-open environment at the end of the selected document is a todo too.
    for key in scan["unclosed"]:
        boundary.setdefault(key, scan["opened"][key]["begin_page"])
    items = []
    for key, page in boundary.items():
        node = scan["opened"][key]
        items.append(node | {"boundary_page": page, "status": "resolved" if node["end"] else "pending"})
    return scan, items
