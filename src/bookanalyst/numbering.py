"""Document-wide counter rules and compact label/reference resolution."""

import copy
import re
from typing import Literal
from pydantic import Field
from .models import StrictModel
from .store import WorkflowError

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


LABEL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]*:[A-Za-z0-9._-]+(?::[A-Za-z0-9._-]+)*")
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
    from collections import Counter

    keys = Counter(x["key"] for x in index["targets"])
    return {
        "duplicate_labels": sorted(k for k, n in keys.items() if n > 1),
        "unresolved_references": [
            r for r in index["references"] if keys[r["key"]] != 1
        ],
    }


def apply_symbol_edits(results, headings, changes):
    index = collect_symbols(results, headings)
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
                    "重名消解只能追加作用域，不能改写隐藏的原书环境和编号",
                )
            edits.append(old | {"replacement": "\\" + old["command"] + "{" + key + "}"})
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
    if problems["duplicate_labels"] or problems["unresolved_references"]:
        raise WorkflowError(
            "UNRESOLVED_REFERENCE",
            "仍存在重名标签或无法解析的引用；须核对环境、原书编号及章节作用域",
        )
    return output, updated


def numbering_report(aux, index):
    actual = dict(re.findall(r"\\newlabel\{([^}]+)\}\{\{([^{}]*)\}", aux))
    checks = []
    for target in index["targets"]:
        label = target["key"]
        printed = actual.get(label)
        observed = target["number"].strip().strip("().[] ")
        checks.append(
            {
                "id": target["id"],
                "label": label,
                "page": target["page"],
                "observed": observed,
                "rendered": printed,
                "matches": printed is not None
                and printed.strip().rstrip(".") == observed.rstrip("."),
            }
        )
    return {
        "checked": len(checks),
        "mismatches": [c for c in checks if not c["matches"]],
        "targets": checks,
    }
