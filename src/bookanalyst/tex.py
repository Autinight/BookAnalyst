"""One common semantic TeX style, with compiler evidence kept separate from acceptance."""
import asyncio
import re
import shutil
from pathlib import Path

from .semantics import check_math, equation_content_ids, require_resolved_characters
from .store import WorkflowError, digest

THEOREMS = ("theorem", "lemma", "definition", "proposition", "corollary", "remark", "example", "exercise", "claim")
HEADINGS = ("part", "chapter", "section", "subsection", "subsubsection")


def escape(text):
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
                    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                    "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in text)


def inline(text):
    require_resolved_characters(text)
    pattern = r"(\\\([\s\S]*?\\\)|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)"
    result = []
    for part in re.split(pattern, text):
        if part.startswith(r"\(") and part.endswith(r"\)"):
            math = part[2:-2]
            check_math(math)
            result.append(r"\(" + math + r"\)")
        elif part.startswith("$") and part.endswith("$") and len(part) > 1:
            math = part[1:-1]
            check_math(math)
            result.append(r"\(" + math + r"\)")
        else:
            result.append(escape(part))
    return "".join(result)


def render(atoms, structure, profile, fragments):
    for atom in atoms:
        require_resolved_characters(atom["text"])
    atom_map = {a["atom_id"]: a for a in atoms}
    fragment_map = {n["atom_id"]: n for f in fragments for n in f["nodes"]}
    preamble = [r"\usepackage{amsmath,amssymb,amsthm}", r"\usepackage{graphicx}", r"\usepackage{hyperref}"]
    # Every environment uses the one shared profile. Fragment authors cannot define counters.
    covered = set()
    for rule in profile["rules"]["counters"]:
        counter = "ba" + rule["id"]
        reset = "" if rule["reset"] == "book" else f'[{rule["reset"]}]'
        preamble.append(r"\newcounter{" + counter + "}" + reset)
        prefix = "" if rule["prefix"] == "none" else "\\" + "the" + rule["prefix"] + "."
        preamble.append(r"\renewcommand{\the" + counter + "}{" + prefix + r"\arabic{" + counter + "}}")
        for kind in rule["types"]:
            if kind == "equation":
                preamble += [r"\makeatletter", r"\let\c@equation\c@" + counter,
                             r"\makeatother", r"\renewcommand{\theequation}{\the" + counter + "}"]
            elif kind in THEOREMS:
                preamble.append(r"\newtheorem{" + kind + "}[" + counter + "]{" + kind.title() + "}")
            else:
                raise WorkflowError("UNSUPPORTED_COUNTER", "计数器绑定了不支持的环境", review=True)
            covered.add(kind)
    for kind in THEOREMS:
        if kind not in covered:
            preamble.append(r"\newtheorem{" + kind + "}{" + kind.title() + "}")
        preamble.append(r"\newtheorem*{" + kind + "star}{" + kind.title() + "}")

    exceptions = {e["node_id"]: e for e in profile["rules"]["exceptions"]}
    children = {}
    for node in structure["nodes"]:
        children.setdefault(node["parent_id"], []).append(node)
    lines, mappings, resources = [], {}, {}
    consumed = []
    last_source = {}
    for node in reversed(structure["nodes"]):
        tail = next((last_source[c["id"]] for c in reversed(children.get(node["id"], []))
                     if last_source.get(c["id"])), None)
        last_source[node["id"]] = tail or (node["atom_ids"][-1] if node["atom_ids"] else None)
    proof_ends = {last_source[n["id"]] for n in structure["nodes"] if n["kind"] == "proof"}

    def atom_text(aid, prefix=""):
        atom = atom_map[aid]
        candidate = fragment_map[aid]
        text = candidate.get("latex", atom["text"])
        if prefix:
            if not text.startswith(prefix):
                raise WorkflowError("INVALID_PREFIX", "Converted opening prefix changed")
            text = text[len(prefix):]
        if aid in proof_ends and candidate["kind"] == "source_ref":
            # The common proof environment emits the semantic end marker once.
            for marker in (r"\(\square\)", "□"):
                if text.rstrip().endswith(marker):
                    text = text.rstrip()[:-len(marker)].rstrip()
                    break
        if candidate["kind"] == "math" or atom["type"] in ("interline_equation", "equation"):
            check_math(text)
            return text
        return inline(text)

    def emit(node):
        first = len(lines) + 1
        kind, aids, number = node["kind"], node["atom_ids"], node["number"]
        consumed.extend(aids)
        label = "ba:" + node["id"]
        prefix = node.get("source_prefix", "")
        if prefix and (not aids or not atom_map[aids[0]]["text"].startswith(prefix)):
            raise WorkflowError("INVALID_PREFIX", "Semantic prefix does not match source")
        if kind in HEADINGS:
            title = node["title"]
            if not title:
                raise WorkflowError("EMPTY_TITLE", "标题不能为空", review=True)
            lines.append("\\" + kind + ("" if number is not None else "*") + "{" + inline(title) + "}")
            if number is not None:
                lines.append(r"\label{" + label + "}")
            if prefix:
                lines.extend(atom_text(aid, prefix if i == 0 else "") for i, aid in enumerate(aids))
        elif kind == "equation":
            latex = "\n".join(fragment_map[aid].get("latex", atom_map[aid]["text"])
                              for aid in equation_content_ids(node, atom_map))
            check_math(latex)
            if not latex:
                raise WorkflowError("EMPTY_FORMULA", "公式为空")
            if number is None:
                lines.extend([r"\[", latex, r"\]"])
            else:
                lines.extend([r"\begin{equation}", latex])
                if node["id"] in exceptions:
                    lines.append(r"\tag{" + escape(number) + "}")
                lines.extend([r"\label{" + label + "}", r"\end{equation}"])
        elif kind in ("figure", "table"):
            for aid in aids:
                atom = atom_map[aid]
                if not atom["resources"]:
                    raise WorkflowError("MISSING_RESOURCE", "图表缺少可恢复的原始资源", review=True)
                for source in atom["resources"]:
                    path = Path(source)
                    name = "assets/" + digest(path.read_bytes())[:24] + path.suffix.lower()
                    resources[name] = path.read_bytes()
                    lines.append(r"\includegraphics{" + name + "}")
                if atom["text"]:
                    lines.append(atom_text(aid))
        else:
            env = kind if kind in THEOREMS or kind == "proof" else None
            if env and kind != "proof" and number is None:
                env += "star"
            if node["id"] in exceptions and kind in THEOREMS:
                env = "exception" + digest(node["id"])[:24].translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
                preamble.append(r"\newtheorem*{" + env + "}{" + kind.title() + " " + escape(number) + "}")
                # Starred exceptional theorem labels require an explicit semantic reference value.
            if env:
                subtitle = node["title"]
                option = "[{" + inline(subtitle) + "}]" if subtitle else ""
                lines.append(r"\begin{" + env + "}" + option)
                if number is not None:
                    if node["id"] in exceptions:
                        lines.extend([r"\makeatletter", r"\def\@currentlabel{" + escape(number) + "}",
                                      r"\makeatother"])
                    lines.append(r"\label{" + label + "}")
            lines.extend(atom_text(aid, prefix if i == 0 else "") for i, aid in enumerate(aids))
            for child in children.get(node["id"], []):
                emit(child)
            if env:
                lines.append(r"\end{" + env + "}")
        if kind in HEADINGS or kind in ("equation", "figure", "table"):
            for child in children.get(node["id"], []):
                emit(child)
        for aid in aids:
            mappings[aid] = {"file": "body.tex", "line": first, "node_id": node["id"],
                             "page_idx": atom_map[aid]["page_idx"], "bbox": atom_map[aid]["bbox"]}
        lines.append("")

    for node in children.get(None, []):
        emit(node)
    expected = [a for n in structure["nodes"] for a in n["atom_ids"]]
    if consumed != expected or set(fragment_map) != set(expected):
        raise WorkflowError("CONTENT_COVERAGE", "合并结果未按结构树完整覆盖正文")
    main = [r"\documentclass{" + profile["documentclass"] + "}", r"\input{preamble}", r"\begin{document}"]
    if structure.get("toc"):
        main.append(r"\tableofcontents")
        for entry in structure["toc"]:
            for aid in entry["atom_ids"]:
                mappings[aid] = {"file": "main.tex", "line": len(main), "node_id": entry["node_id"],
                                 "page_idx": atom_map[aid]["page_idx"], "bbox": atom_map[aid]["bbox"]}
    main.extend([r"\input{body}", r"\end{document}"])
    return {"tex/main.tex": "\n".join(main) + "\n",
            "tex/preamble.tex": "\n".join(preamble) + "\n",
            "tex/body.tex": "\n".join(lines) + "\n",
            "tex/source_map.json": mappings,
            **{"tex/" + name: data for name, data in resources.items()}}


async def compile_tex(directory, ledger, structure, executable=None):
    executable = executable or shutil.which("xelatex")
    if not executable:
        return {"status": "FAILED", "code": "DEPENDENCY_MISSING", "message": "未找到 XeLaTeX",
                "passes": 0, "evidence_origin": "live"}
    directory = Path(directory)
    output = ""
    for attempt in range(1, 4):
        process = await asyncio.create_subprocess_exec(executable, "-no-shell-escape", "-interaction=nonstopmode",
            "-halt-on-error", "-file-line-error", "main.tex", cwd=directory,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), 90)
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"status": "FAILED", "code": "COMPILE_TIMEOUT", "passes": attempt, "evidence_origin": "live"}
        output = raw.decode("utf-8", errors="replace")
        if process.returncode:
            return {"status": "FAILED", "code": "TEX_ERROR", "log": output[-12000:],
                    "passes": attempt, "evidence_origin": "live"}
        rerun = bool(re.search(r"Rerun to get|Please rerun|Label\(s\) may have changed|There were undefined references|rerun LaTeX", output))
        if not rerun:
            break
    if rerun or re.search(r"LaTeX Warning:.*undefined|Reference .* undefined|Citation .* undefined|multiply defined|Undefined control sequence", output):
        return {"status": "FAILED", "code": "REFERENCE_ERROR", "log": output[-12000:],
                "passes": attempt, "evidence_origin": "live"}
    if not (directory / "main.pdf").is_file() or not (directory / "main.aux").is_file():
        return {"status": "FAILED", "code": "COMPILE_OUTPUT_MISSING",
                "passes": attempt, "evidence_origin": "live"}
    aux = (directory / "main.aux").read_text(encoding="utf-8", errors="replace")
    labels = dict(re.findall(r"\\newlabel\{(ba:[^}]+)\}\{\{([^}]*)\}", aux))
    expected = {f"ba:{row['node_id']}": row["number"] for row in ledger}
    expected.update({f"ba:{n['id']}": n["number"] for n in structure["nodes"]
                     if n["kind"] in HEADINGS and n["number"] is not None})
    if any(labels.get(key) != number for key, number in expected.items()):
        return {"status": "FAILED", "code": "NUMBER_MISMATCH", "actual": labels, "expected": expected,
                "passes": attempt, "evidence_origin": "live"}
    return {"status": "PASSED", "passes": attempt, "labels": labels, "evidence_origin": "live"}
