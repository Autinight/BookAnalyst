"""Document-wide counter rules and compact label/reference resolution."""

import copy
import re
from collections import Counter, defaultdict
from difflib import get_close_matches
from typing import Literal
from pydantic import Field
from .models import StrictModel
from .store import WorkflowError, digest

THEOREMS = [
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "definition",
    "remark",
    "example",
    "claim",
    "exercise",
]
HEADINGS = ["part", "chapter", "section", "subsection", "subsubsection"]
BUILTINS = HEADINGS + ["equation", "figure", "table", "footnote"]
CounterName = Literal[
    "part",
    "chapter",
    "section",
    "subsection",
    "subsubsection",
    "equation",
    "figure",
    "table",
    "footnote",
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "definition",
    "remark",
    "example",
    "claim",
    "exercise",
]


class CounterRule(StrictModel):
    name: CounterName
    reset_by: Literal["", "part", "chapter", "section", "subsection"]
    shared_with: str
    style: Literal["arabic", "roman", "Roman", "alph", "Alph"]
    prefix_parent: bool


class CounterSeed(StrictModel):
    name: CounterName
    value: int = Field(ge=-1)


class Numbering(StrictModel):
    rules: list[CounterRule]
    initial: list[CounterSeed]


class SymbolEdit(StrictModel):
    id: str
    key: str


# Source identifiers are plain text, not an ASCII-only numbering vocabulary.
# Exclude TeX syntax/control characters while preserving Unicode author notation.
LABEL_PART = r"[^\s:{}\\%#&$^~\x00-\x1f\x7f]+"
LABEL_NAME = re.compile(
    r"[A-Za-z][A-Za-z0-9._-]*:" + LABEL_PART + r"(?::" + LABEL_PART + r")*"
)
NATIVE = re.compile(r"\\(label|ref|eqref|nameref)\{([^{}]+)\}")
MARKERS = re.compile(r"\\(BAHeading|BAFigure|label|ref|eqref|nameref)\{([^{}]+)\}")


def validate_numbering(plan, documentclass):
    Numbering.model_validate(plan)
    rules = {r["name"]: r for r in plan["rules"]}
    if len(rules) != len(plan["rules"]):
        raise WorkflowError("COUNTER_RULE", "计数器规则重复")
    effective = {
        name: {"shared_with": "" if name == "theorem" else "theorem", "reset_by": ""}
        for name in THEOREMS
    } | rules
    for name, r in rules.items():
        parent, shared = r["reset_by"], r["shared_with"]
        if parent == name or (
            documentclass == "article" and (parent == "chapter" or name == "chapter")
        ):
            raise WorkflowError("COUNTER_RULE", "计数器复位层级与文档类别不符")
        if shared and (
            name not in THEOREMS or shared not in THEOREMS or shared == name or parent
        ):
            raise WorkflowError(
                "COUNTER_RULE", "共享计数器只能引用其他定理环境，复位由被共享计数器负责"
            )
        seen = {name}
        current = shared or parent
        while current:
            if current in seen:
                raise WorkflowError("COUNTER_RULE", "计数器存在循环依赖")
            seen.add(current)
            up = effective.get(current, {})
            current = up.get("shared_with") or up.get("reset_by")
    seeds = [x["name"] for x in plan["initial"]]
    if len(seeds) != len(set(seeds)):
        raise WorkflowError("COUNTER_RULE", "初始计数器重复")
    if documentclass == "article" and "chapter" in seeds:
        raise WorkflowError("COUNTER_RULE", "article 没有 chapter 计数器")


def counter_preamble(plan, documentclass):
    """Compile the analyst's structured rules into ordinary LaTeX declarations."""
    validate_numbering(plan, documentclass)
    rules = {r["name"]: r for r in plan["rules"]}
    lines = []
    created = set()

    def theorem(name):
        if name in created:
            return
        rule = rules.get(name)
        shared = (
            rule["shared_with"] if rule else ("" if name == "theorem" else "theorem")
        )
        if shared:
            theorem(shared)
            lines.append(
                r"\newtheorem{" + name + "}[" + shared + "]{" + name.title() + "}"
            )
        else:
            parent = rule["reset_by"] if rule else ""
            lines.append(
                r"\newtheorem{"
                + name
                + "}{"
                + name.title()
                + "}"
                + ("[" + parent + "]" if parent else "")
            )
        lines.append(r"\newtheorem*{" + name + "*}{" + name.title() + "}")
        created.add(name)

    for name in THEOREMS:
        theorem(name)
    for name, r in rules.items():
        if r["shared_with"]:
            continue
        parent = r["reset_by"]
        # Clear native parent resets before applying the explicitly chosen rule.
        if name in BUILTINS:
            for ancestor in ["part", "chapter", "section", "subsection"]:
                if ancestor != name and not (
                    documentclass == "article" and ancestor == "chapter"
                ):
                    lines.append(r"\counterwithout*{" + name + "}{" + ancestor + "}")
            if parent:
                lines.append(r"\counterwithin*{" + name + "}{" + parent + "}")
        expression = (
            (r"\the" + parent + "." if parent and r["prefix_parent"] else "")
            + "\\"
            + r["style"]
            + "{"
            + name
            + "}"
        )
        lines.append(r"\renewcommand{\the" + name + "}{" + expression + "}")
    return "\n".join(lines) + "\n"


def initial_counters(plan):
    return (
        "\n".join(
            r"\setcounter{" + s["name"] + "}{" + str(s["value"]) + "}"
            for s in plan["initial"]
        )
        + "\n"
    )


def collect_symbols(results, headings):
    """Derive hidden numbering and references directly from ordinary TeX commands."""
    heading_by_id = {h["id"]: h for h in headings}
    targets = []
    references = []
    scope = []
    for batch in results:
        for page in batch["pages"]:
            counts = {"label": 0, "reference": 0}
            # Preserve offsets while ignoring source comments.
            text = re.sub(
                r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group()), page["tex"]
            )
            for match in MARKERS.finditer(text):
                command, key = match.groups()
                if command == "BAHeading":
                    h = heading_by_id[key]
                    level = HEADINGS.index(h["level"])
                    scope = [x for x in scope if x["level"] < level]
                    scope.append({"level": level, "number": h["number"], "id": key})
                    continue
                if command == "BAFigure":
                    continue
                if not LABEL_NAME.fullmatch(key):
                    raise WorkflowError(
                        "LABEL_NAME", "标签必须采用 环境:原书编号 的统一名称：" + key
                    )
                category = "label" if command == "label" else "reference"
                ordinal = counts[category]
                counts[category] += 1
                pieces = key.split(":")
                item = {
                    "id": f"p{page['page']}-{category}-{ordinal}",
                    "page": page["page"],
                    "key": key,
                    "kind": pieces[0],
                    "number": pieces[1],
                    "command": command,
                    "scope": [x.copy() for x in scope],
                    "context": text[max(0, match.start() - 100) : match.end() + 100],
                    "start": match.start(),
                    "end": match.end(),
                }
                (targets if category == "label" else references).append(item)
    return {"targets": targets, "references": references}


def symbol_problems(index):
    keys = Counter(x["key"] for x in index["targets"])
    return {
        "duplicate_labels": sorted(k for k, n in keys.items() if n > 1),
        "unresolved_references": [
            r for r in index["references"] if keys[r["key"]] != 1
        ],
    }


def symbol_edits(index, changes):
    """Validate occurrence edits without copying or rescanning the book."""
    edits = []
    for group, field in [("targets", "label_edits"), ("references", "reference_edits")]:
        available = {x["id"]: x for x in index[group]}
        seen = set()
        for edit in changes[field]:
            key = edit["key"]
            id = edit["id"]
            if id not in available or id in seen or not LABEL_NAME.fullmatch(key):
                raise WorkflowError("SYMBOL_EDIT", "标签或引用修改的定位无效")
            seen.add(id)
            old = available[id]
            if group == "targets" and key.split(":")[:2] != old["key"].split(":")[:2]:
                raise WorkflowError(
                    "SYMBOL_EDIT",
                    f"{key} 改变了类型或原书编号；保留前两段，用冒号追加作用域，如 {':'.join(old['key'].split(':')[:2])}:scope",
                )
            edits.append(old | {"replacement": "\\" + old["command"] + "{" + key + "}"})
    return edits


def unconfirmed_reference_ids(index, changes, *, deferrable_ids=()):
    """Only explicit, unchanged missing occurrences can be left for confirmation."""
    references = {r["id"]: r for r in index["references"]}
    targets = {t["key"] for t in index["targets"]}
    edited = {e["id"] for e in changes["reference_edits"]}
    deferrable = set(deferrable_ids)
    pending = set()
    for field, bibliography, code in (("unconfirmed_bibliography", True, "BIBLIOGRAPHY_REVIEW"),
                                       ("unconfirmed_references", False, "REFERENCE_REVIEW")):
        for item in changes.get(field, []):
            ref = references.get(item["id"])
            if (not ref or (ref["kind"] == "bibliography") != bibliography
                    or (ref["key"] in targets and item["id"] not in deferrable)
                    or item["id"] in edited | pending or not item["reason"].strip()):
                raise WorkflowError(code, "只能保留未修改、缺少目标的对应类型引用，并说明查找依据及无法确认的原因")
            pending.add(item["id"])
    return pending


def apply_symbol_edits(results, headings, changes, *, require_resolved=True):
    index = collect_symbols(results, headings)
    edits = symbol_edits(index, changes)
    pending = unconfirmed_reference_ids(index, changes)
    output = copy.deepcopy(results)
    for batch in output:
        for page in batch["pages"]:
            for edit in sorted(
                (e for e in edits if e["page"] == page["page"]),
                key=lambda e: e["start"],
                reverse=True,
            ):
                page["tex"] = (
                    page["tex"][: edit["start"]]
                    + edit["replacement"]
                    + page["tex"][edit["end"] :]
                )
    updated = collect_symbols(output, headings)
    problems = symbol_problems(updated)
    unresolved = [r for r in problems["unresolved_references"] if r["id"] not in pending]
    if require_resolved and (problems["duplicate_labels"] or unresolved):
        raise WorkflowError(
            "UNRESOLVED_REFERENCE",
            "仍存在重名标签或无法解析的引用；须核对环境、原书编号及章节作用域",
        )
    return output, updated


def reference_groups(index, phase):
    """Keep writable label namespaces together; share only relevant target evidence."""
    problems = symbol_problems(index)
    duplicates = set(problems["duplicate_labels"])
    targets_by_key = defaultdict(list)
    for target in index["targets"]:
        targets_by_key[target["key"]].append(target)
    buckets = defaultdict(list)
    if phase == "duplicates":
        # Different suffixes of the same kind/number can collide after editing.
        for key in sorted(duplicates):
            buckets[":".join(key.split(":")[:2])].append(key)
    else:
        for ref in problems["unresolved_references"]:
            if ref["key"] not in targets_by_key:
                buckets[ref["key"]] = [ref["key"]]

    def normalized(number):
        return number.replace("prime", "'").replace("′", "'").replace("″", "''").casefold()

    def family(kind):
        if kind in THEOREMS and kind != "exercise":
            return "statement"
        return "exercise" if kind in ("exercise", "problem") else kind

    groups = []
    for namespace, keys in sorted(buckets.items()):
        references = [r for r in index["references"] if r["key"] in keys]
        if phase == "duplicates":
            targets = [t for t in index["targets"] if ":".join(t["key"].split(":")[:2]) == namespace]
            editable = [t["id"] for t in targets if t["key"] in keys]
        else:
            kind, number = keys[0].split(":")[:2]
            candidates = [t for t in index["targets"] if family(t["kind"]) == family(kind)]
            targets = [t for t in candidates if normalized(t["number"]) == normalized(number)]
            # Similar keys are evidence for the worker, never automatic replacements.
            nearby = set(get_close_matches(keys[0], sorted({t["key"] for t in candidates}), n=5, cutoff=0.6))
            targets += [t for t in candidates if t not in targets and t["key"] in nearby]
            editable = []
        groups.append({
            "id": "reference-" + phase + "-" + digest(keys)[:16],
            "phase": phase,
            "keys": keys,
            "targets": targets,
            "references": references,
            "label_ids": editable,
        })
    return groups


def deferrable_duplicate_reference_ids(index, group, changes):
    if group["phase"] != "duplicates" or not group["label_ids"]:
        return set()
    label_changes = {e["id"]: e["key"] for e in changes["label_edits"]}
    ref_changes = {e["id"]: e["key"] for e in changes["reference_edits"]}
    keys = Counter(t["key"] for t in index["targets"])
    for target in group["targets"]:
        if target["id"] in label_changes:
            keys[target["key"]] -= 1
            keys[label_changes[target["id"]]] += 1
    return {
        ref["id"]
        for ref in group["references"]
        if ref["key"] in group["keys"]
        and ref["id"] not in ref_changes
        and keys[ref["key"]] == 0
    }


def validate_reference_group(index, group, changes):
    edits = symbol_edits(index, changes)
    deferrable = deferrable_duplicate_reference_ids(index, group, changes)
    pending = unconfirmed_reference_ids(index, changes, deferrable_ids=deferrable)
    label_ids = set(group["label_ids"])
    ref_ids = {r["id"] for r in group["references"]}
    if not pending <= ref_ids or (pending and group["phase"] != "missing" and not pending <= deferrable):
        raise WorkflowError("REFERENCE_REVIEW" if changes.get("unconfirmed_references") else "BIBLIOGRAPHY_REVIEW",
                            "只能标记当前组拥有的引用；重复标签必须修复，无法绑定本组标签的跨组引用可保留原键并稍后解析")
    if any(e["id"] not in label_ids | ref_ids for e in edits):
        raise WorkflowError("SYMBOL_EDIT", "只能修改当前引用组拥有的标签和引用位置")
    label_changes = {e["id"]: e["key"] for e in changes["label_edits"]}
    ref_changes = {e["id"]: e["key"] for e in changes["reference_edits"]}
    keys = Counter(t["key"] for t in index["targets"])
    for t in group["targets"]:
        if t["id"] in label_changes:
            keys[t["key"]] -= 1
            keys[label_changes[t["id"]]] += 1
    supported = {label_changes.get(t["id"], t["key"]) for t in group["targets"]}
    unresolved = [
        {"id": r["id"], "key": ref_changes.get(r["id"], r["key"])}
        for r in group["references"]
        if r["id"] not in pending and (keys[ref_changes.get(r["id"], r["key"])] != 1
        or ref_changes.get(r["id"], r["key"]) not in supported)
    ]
    collisions = sorted({
        label_changes.get(t["id"], t["key"])
        for t in group["targets"]
        if t["id"] in label_ids and keys[label_changes.get(t["id"], t["key"])] != 1
    })
    if collisions or unresolved:
        import json
        raise WorkflowError(
            "UNRESOLVED_REFERENCE",
            "本组仍有异常：" + json.dumps({"duplicate_labels": collisions, "unresolved_references": unresolved}, ensure_ascii=False),
        )
