"""Semantic contracts and deterministic gates. Models propose; programs verify."""
import re
from collections import Counter
from .store import WorkflowError, digest


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def array(item):
    return {"type": "array", "items": item}


TEXT = {"type": "string"}
NULL_TEXT = {"type": ["string", "null"]}
STRINGS = array(TEXT)
FINDING = object_schema({"source_ids": STRINGS, "type": TEXT, "evidence": TEXT, "message": TEXT})
REVIEW = object_schema({"decision": {"enum": ["PASS", "BLOCK"]}, "source_ids": STRINGS,
                        "evidence": TEXT, "findings": array(FINDING)})
OBSERVATION = object_schema({"reading": TEXT, "regions": array(object_schema({
    "bbox": array({"type": "number"}), "reading": TEXT})), "unreadable": {"type": "boolean"}})
VISUAL = object_schema({"result": {"enum": ["MATCH", "MISMATCH", "SOURCE_UNREADABLE", "REVIEW_INCONCLUSIVE"]},
                       "source_ids": STRINGS, "evidence": TEXT, "findings": array(FINDING)})
VISUAL_PATCHES = object_schema({"patches": array(object_schema({
    "atom_id": TEXT, "before": TEXT, "after": TEXT, "evidence": TEXT})), "findings": array(FINDING)})
TYPE_POLICY = object_schema({"types": array(object_schema({
    "type": TEXT, "action": {"enum": ["KEEP", "EXCLUDE"]}, "reason": TEXT}))})
DISPOSITIONS = object_schema({"dispositions": array(object_schema({
    "atom_id": TEXT, "action": {"enum": ["KEEP", "EXCLUDE"]}, "reason": TEXT})), "findings": array(FINDING)})

NODE_KINDS = ["part", "chapter", "section", "subsection", "subsubsection", "paragraph",
              "equation", "theorem", "lemma", "definition", "proposition", "corollary",
              "proof", "remark", "example", "exercise", "claim", "metadata", "figure", "table"]
COUNTER = object_schema({"id": TEXT, "types": STRINGS, "reset": {"enum": ["book", "chapter", "section"]},
                        "prefix": {"enum": ["none", "chapter", "section"]}})
RULE = object_schema({"id": TEXT, "counters": array(COUNTER),
                     "exceptions": array(object_schema({"node_id": TEXT, "number": TEXT, "evidence": TEXT}))})
STRUCTURE = object_schema({
    "nodes": array(object_schema({"id": TEXT, "parent_id": NULL_TEXT, "kind": {"enum": NODE_KINDS},
                                  "atom_ids": STRINGS, "title": TEXT, "number": NULL_TEXT, "source_prefix": TEXT})),
    "toc_mode": {"enum": ["original", "body_reconstructed"]},
    "toc": array(object_schema({"atom_ids": STRINGS, "node_id": TEXT, "title": TEXT, "number": NULL_TEXT})),
    "counter_candidates": array(RULE),
    "references": array(object_schema({"source_atom_id": TEXT, "text": TEXT, "target_node_id": NULL_TEXT,
                                       "external": {"type": "boolean"}, "evidence": TEXT})),
    "findings": array(FINDING),
})
FRAGMENT = object_schema({"task_id": TEXT, "profile_hash": TEXT, "input_hash": TEXT,
    "nodes": array({"oneOf": [object_schema({"atom_id": TEXT, "kind": {"const": "source_ref"}}),
                              object_schema({"atom_id": TEXT, "kind": {"const": "math"}, "latex": TEXT})]}),
    "changes": array(object_schema({"atom_id": TEXT, "before": TEXT, "after": TEXT, "reason": TEXT})),
    "findings": array(FINDING)})


def require_review(report, expected_ids):
    if report["decision"] != "PASS" or report["findings"] or not report["evidence"].strip():
        raise WorkflowError("SEMANTIC_REVIEW", "独立审查存在未解决问题", review=True)
    if set(report["source_ids"]) != set(expected_ids):
        raise WorkflowError("REVIEW_COVERAGE", "审查未覆盖本次全部来源", review=True)


def validate_structure(plan, atoms):
    if plan["findings"]:
        raise WorkflowError("STRUCTURE_REVIEW", "标题或数学结构存在未解决问题", review=True)
    nodes = plan["nodes"]
    ids = [n["id"] for n in nodes]
    if not nodes or len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
        raise WorkflowError("INVALID_STRUCTURE", "节点 ID 缺失、重复或无效")
    available, owned, path = set(), [], []
    for node in nodes:
        if node["kind"] == "metadata":
            if node["parent_id"] is not None or node["number"] is not None:
                raise WorkflowError("INVALID_METADATA", "Publication metadata must be separate from body hierarchy")
            available.add(node["id"])
            owned.extend(node["atom_ids"])
            continue
        if node["parent_id"] is not None and node["parent_id"] not in available:
            raise WorkflowError("INVALID_STRUCTURE", "父节点必须存在且先于子节点")
        while path and path[-1] != node["parent_id"]:
            path.pop()
        if node["parent_id"] is not None and not path:
            raise WorkflowError("READING_ORDER", "结构树回到了已经关闭的父环境", review=True)
        path.append(node["id"])
        available.add(node["id"])
        owned.extend(node["atom_ids"])
    toc_owned = [a for t in plan["toc"] for a in t["atom_ids"]]
    if Counter(owned + toc_owned) != Counter(a["atom_id"] for a in atoms):
        raise WorkflowError("CONTENT_COVERAGE", "结构树与目录证据不满足内容唯一覆盖")
    for node in nodes:
        if node.get("proves_id"):
            target = next((n for n in nodes if n["id"] == node["proves_id"]), None)
            if (node["kind"] != "proof" or not target or target["kind"] not in
                    ("theorem", "lemma", "proposition", "corollary", "claim") or ids.index(target["id"]) >= ids.index(node["id"])):
                raise WorkflowError("INVALID_PROOF_RELATION", "Proof must reference an earlier mathematical statement")
    for entry in plan["toc"]:
        target = next((n for n in nodes if n["id"] == entry["node_id"]), None)
        if not target or " ".join(entry["title"].split()) != " ".join(target["title"].split()) or entry["number"] != target["number"]:
            raise WorkflowError("TOC_MISMATCH", "目录与正文标题不匹配", review=True)
    if plan["toc_mode"] == "original" and not plan["toc"]:
        raise WorkflowError("TOC_MISSING", "没有目录证据，不能声明目录已校验")
    amap = {a["atom_id"]: a for a in atoms}
    ownership = {aid: n for n in nodes for aid in n["atom_ids"]}
    for atom in atoms:
        evidence = atom.get("number_evidence")
        if evidence:
            owner = ownership.get(atom["atom_id"])
            if (not owner or owner["kind"] != "equation" or owner["number"] != evidence["number"]
                    or ownership.get(evidence["formula_atom_id"]) is not owner):
                raise WorkflowError("NUMBER_EVIDENCE_OWNERSHIP", "Transferred number must belong to its corresponding equation", review=True)
    for node in nodes:
        prefix = node.get("source_prefix", "")
        if prefix and (not node["atom_ids"] or not amap[node["atom_ids"][0]]["text"].startswith(prefix)):
            raise WorkflowError("INVALID_PREFIX", "Heading/environment prefix does not match the exact source", review=True)
    indices = [ids.index(t["node_id"]) for t in plan["toc"]]
    if indices != sorted(set(indices)):
        raise WorkflowError("TOC_ORDER", "目录锚点不唯一或顺序错误", review=True)
    order = {a["atom_id"]: i for i, a in enumerate(atoms)}
    metadata_ids = {aid for n in nodes if n["kind"] == "metadata" for aid in n["atom_ids"]}
    positions = [order[a] for a in owned if a not in metadata_ids]
    if positions != sorted(positions):
        raise WorkflowError("READING_ORDER", "结构树改变了来源阅读顺序", review=True)
    for reference in plan["references"]:
        if reference["source_atom_id"] not in order or not reference["evidence"]:
            raise WorkflowError("INVALID_REFERENCE", "引用来源无效")
        if not reference["external"] and reference["target_node_id"] not in available:
            raise WorkflowError("UNRESOLVED_REFERENCE", "内部引用目标无法确定", review=True)


def simulate(plan, candidate):
    nodes = plan["nodes"]
    exceptions = {e["node_id"]: e for e in candidate["exceptions"]}
    if len(exceptions) != len(candidate["exceptions"]) or any(not e["evidence"] for e in exceptions.values()):
        return None
    rules, state, paths, ledger = {}, {}, {}, []
    for counter in candidate["counters"]:
        if counter["id"] in rules or not re.fullmatch(r"[A-Za-z]+", counter["id"]):
            return None
        rules[counter["id"]] = counter
    assigned = [kind for c in rules.values() for kind in c["types"]]
    if len(assigned) != len(set(assigned)):
        return None
    chapter, section = "", ""
    for node in nodes:
        kind, number = node["kind"], node["number"]
        if kind == "chapter":
            chapter, section = number or "", ""
        if kind == "section":
            section = number or ""
        paths[node["id"]] = {"chapter": chapter, "section": section}
        if number is None or kind in ("part", "chapter", "section", "subsection", "subsubsection"):
            continue
        rule = next((r for r in rules.values() if kind in r["types"]), None)
        if not rule:
            return None
        scope = "" if rule["reset"] == "book" else paths[node["id"]][rule["reset"]]
        if rule["reset"] != "book" and not scope:
            return None
        key = (rule["id"], scope)
        state[key] = state.get(key, 0) + 1
        prefix = "" if rule["prefix"] == "none" else paths[node["id"]][rule["prefix"]]
        computed = (prefix + "." if prefix else "") + str(state[key])
        if node["id"] in exceptions:
            computed = exceptions[node["id"]]["number"]
        if computed != number:
            return None
        ledger.append(dict(node_id=node["id"], kind=kind, number=number, simulated=computed,
                           counter=rule["id"], source_ids=node["atom_ids"]))
    if set(exceptions) - {n["id"] for n in nodes}:
        return None
    return ledger


def freeze_profile(plan):
    matches, seen = [], set()
    for candidate in plan["counter_candidates"]:
        fingerprint = digest({k: v for k, v in candidate.items() if k != "id"})
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        ledger = simulate(plan, candidate)
        if ledger is not None:
            matches.append((candidate, ledger))
    numbered = [n for n in plan["nodes"] if n["number"] is not None and n["kind"] not in
                ("part", "chapter", "section", "subsection", "subsubsection")]
    if not numbered and not matches:
        matches = [({"id": "unnumbered", "counters": [], "exceptions": []}, [])]
    if len(matches) != 1:
        raise WorkflowError("COUNTER_AMBIGUITY", "编号候选不能唯一复现全部观察，请补充范围或证据", review=True)
    candidate, ledger = matches[0]
    profile = {"documentclass": "book" if any(n["kind"] == "chapter" for n in plan["nodes"]) else "article", "rules": candidate, "environments": NODE_KINDS,
               "macros": [], "renderer_version": "0.1.0"}
    profile["profile_hash"] = digest(profile)
    return profile, ledger


ALLOWED_COMMANDS = set(r"""sqrt frac dfrac tfrac cfrac partial sum prod int iint iiint oint lim inf sup min max
sin cos tan log ln exp det dim ker deg gcd Pr operatorname text mathrm mathbf mathit mathsf mathtt mathcal
mathbb mathfrak boldsymbol vec hat bar tilde widehat widetilde overline underline dot ddot
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma
Upsilon Phi Psi Omega infty nabla Delta forall exists neg in notin subset subseteq supset supseteq
cup cap setminus emptyset varnothing le leq ge geq ne neq approx sim simeq equiv cong propto
times cdot div pm mp circ ast star bullet land lor wedge vee oplus otimes perp parallel
to mapsto rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
longrightarrow longleftarrow Longrightarrow Longleftarrow Longleftrightarrow hookrightarrow
langle rangle lvert rvert lVert rVert vert Vert left right big Big bigg Bigg bigl bigr
dots ldots cdots vdots ddots quad qquad hspace phantom binom dbinom tbinom
begin end cases matrix pmatrix bmatrix vmatrix Vmatrix smallmatrix aligned gathered split
underbrace overbrace underset overset stackrel substack limits nolimits
Re Im ell hbar imath jmath top bot prime angle triangle uparrow downarrow
mod bmod pmod nonumber notag tag
""".split())
# Supported mathematical symbols, operators and alphabet declarations.
ALLOWED_COMMANDS.update("backslash bf bigcap bigcup bigtriangleup breve cal check colon dotsc iff it "
    "joinrel lfloor liminf limsup llcorner mathring mid ni not pmb prec rfloor sb scriptscriptstyle "
    "scriptstyle smile sqcup subsetneq textstyle xleftarrow xrightarrow square".split())
# Layout commands, even if technically valid TeX, are not accepted in model fragments.
FORBIDDEN = {"hspace", "phantom", "tag", "nonumber", "notag"}


UNRESOLVED_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def control_character_findings(atoms):
    """Detect invalid transcription bytes without assigning them mathematical meaning."""
    findings = []
    for atom in atoms:
        matches = list(UNRESOLVED_CONTROLS.finditer(atom["text"]))
        if matches:
            findings.append({"source_ids": [atom["atom_id"]], "page_idx": atom["page_idx"],
                "type": "UNRESOLVED_CONTROL_CHARACTER",
                "occurrences": [{"offset": m.start(), "codepoint": f"U+{ord(m.group()):04X}"} for m in matches],
                "evidence": "Parser text contains control characters without a verified visible meaning.",
                "message": "需依据原始 PDF 图像核对字符，禁止猜测替换或静默删除"})
    return findings


def require_resolved_characters(text):
    if UNRESOLVED_CONTROLS.search(text):
        raise WorkflowError("UNRESOLVED_CONTROL_CHARACTER", "内容包含未核对的控制字符，需回到 S2 原图核对", review=True)


def check_math(text):
    require_resolved_characters(text)
    if re.search(r"(?<!\\)[%#]", text):
        raise WorkflowError("TEX_STYLE", "数学片段包含不允许的字符")
    for command in re.findall(r"\\([A-Za-z]+)", text):
        if command not in ALLOWED_COMMANDS or command in FORBIDDEN:
            raise WorkflowError("TEX_STYLE", f"数学命令未登记: {command}", review=True)
    stack = []
    for match in re.finditer(r"\\(begin|end)\{([^}]+)\}", text):
        action, name = match.groups()
        if name not in {"cases", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "aligned", "gathered", "split"}:
            raise WorkflowError("TEX_STYLE", "不允许片段自行声明外层环境")
        if action == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            raise WorkflowError("TEX_STYLE", "数学环境不配对")
    if stack:
        raise WorkflowError("TEX_STYLE", "数学环境未关闭")
    braces = 0
    for match in re.finditer(r"(?<!\\)[{}]", text):
        braces += 1 if match.group() == "{" else -1
        if braces < 0:
            raise WorkflowError("TEX_STYLE", "数学括号不配对")
    if braces:
        raise WorkflowError("TEX_STYLE", "数学括号未关闭")


def equation_content_ids(node, atoms):
    """A separately parsed author label is source evidence for PROGRAM's counter, not formula text."""
    number = node.get("number")
    result = []
    for aid in node["atom_ids"]:
        atom = atoms[aid]
        compact = re.sub(r"\s+", "", atom["text"])
        is_label = number is not None and atom["type"] == "text" and (
            compact in (number, "(" + number + ")") or atom.get("number_evidence", {}).get("number") == number)
        if not is_label:
            result.append(aid)
    if not result:
        raise WorkflowError("EMPTY_FORMULA", "An equation cannot consist only of its author number")
    return result


def validate_fragment(fragment, task, atoms):
    if fragment["task_id"] != task["task_id"] or fragment["input_hash"] != task["input_hash"] or fragment["profile_hash"] != task["profile_hash"]:
        raise WorkflowError("STALE_FRAGMENT", "转换片段版本不一致")
    if [n["atom_id"] for n in fragment["nodes"]] != task["owned_atom_ids"]:
        raise WorkflowError("CONTENT_COVERAGE", "片段存在遗漏、重复或越权来源")
    if fragment["findings"]:
        raise WorkflowError("CONTENT_REVIEW", "转换片段仍有疑点", review=True)
    changes = {c["atom_id"]: c for c in fragment["changes"]}
    grouped = {aid for group in task.get("math_groups", []) for aid in group}
    fragment_nodes = {n["atom_id"]: n for n in fragment["nodes"]}
    for group in task.get("math_groups", []):
        if not set(group) <= set(task["owned_atom_ids"]):
            raise WorkflowError("SPLIT_FORMULA", "公式的 block 集群跨越转换任务")
        check_math("\n".join(fragment_nodes[aid].get("latex", atoms[aid]["text"]) for aid in group))
    used = set()
    for node in fragment["nodes"]:
        atom = atoms[node["atom_id"]]
        if node["kind"] == "math":
            if atom["type"] not in ("interline_equation", "equation"):
                raise WorkflowError("CONTENT_REWRITE", "普通文本必须引用原文，不能自由改写")
            if node["atom_id"] not in grouped:
                check_math(node["latex"])
            if node["latex"] != atom["text"]:
                change = changes.get(node["atom_id"])
                if not change or change["before"] != atom["text"] or change["after"] != node["latex"] or not change["reason"]:
                    raise WorkflowError("UNDOCUMENTED_CHANGE", "公式变更缺少精确记录")
                used.add(node["atom_id"])
        elif atom["type"] in ("interline_equation", "equation") and node["atom_id"] not in grouped:
            check_math(atom["text"])
    if set(changes) != used:
        raise WorkflowError("INVALID_CHANGE", "变更记录包含未应用的变更")
