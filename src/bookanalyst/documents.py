"""Compact model contracts, page ownership, and shared TeX rendering."""

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict
from .store import WorkflowError


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Heading(Result):
    id: str
    page: int
    level: Literal["part", "chapter", "section", "subsection", "subsubsection"]
    number: str
    title: str


class Setup(Result):
    documentclass: Literal["article", "book"]
    title: str
    author: str
    rules: str
    toc: list[str]


class Page(Result):
    page: int
    tex: str


class Asset(Result):
    id: str
    page: int
    bbox: list[float]


class Conversion(Result):
    pages: list[Page]
    headings: list[Heading]
    assets: list[Asset]
    head: Literal["closed", "paragraph", "math", "environment", "unknown"]
    tail: Literal["closed", "paragraph", "math", "environment", "unknown"]


class Seam(Result):
    left_suffix: str
    right_prefix: str
    replacement: str


class Headings(Result):
    headings: list[Heading]


CONVENTIONS = r"""Use standard LaTeX with amsmath, amssymb, amsthm, mathtools.
Return each supplied physical page exactly once, in order, with its TeX text.
Omit repeated running headers, printed page numbers and ornaments. Preserve ALL
other author content, including footnotes, references and affiliation.
Use \( \) for inline math, equation* or align* for displays; preserve printed
numbers with \tag{...}. Never invent references, labels or mathematical content.
Use \begin{theorem}[author number and optional title] and analogous lemma,
proposition, corollary, definition, remark, example, claim, exercise environments;
these are unnumbered common environments. Use proof for proofs.
A heading appears in body as \BAHeading{local-id}; add its visible number, TeX
title and provisional level to headings. Local IDs must contain only letters,
digits or hyphens. Do not put document title or author into section headings.
For diagrams use \BAFigure{local-id} plus assets with normalized [x0,y0,x1,y1]
coordinates on an owned page; do not recreate the diagram or duplicate its text.
Output plain body TeX, no documentclass, preamble, macro definitions or file IO.
The provided settings are rules, not text to insert. Include title/authors only
if visible on your pages. Fix recognition directly without a change ledger.
Connect pages INSIDE your batch. At a batch edge, keep partial sentences and
open environments; do not guess missing beginning/end. Set head/tail to describe
continuation. Do not seek adjacent pages. Do not repeatedly self-review."""

# This blocks file access and code execution, not valid mathematical vocabulary.
FORBIDDEN = re.compile(
    r"\\(?:input|include|includegraphics|openin|openout|read|write|immediate|special|directlua|catcode|csname|usepackage|documentclass|newcommand|renewcommand|def|edef|gdef|let|newread|newwrite|endinput|everyjob|loop|repeat|scantokens|RequirePackage|InputIfFileExists|IfFileExists)(?![A-Za-z])"
)


def safe_tex(text):
    if "^^" in text or FORBIDDEN.search(text):
        raise WorkflowError("UNSAFE_TEX", "正文含文件访问、宏定义或执行命令")
    if "\\begin{document}" in text or "\\end{document}" in text:
        raise WorkflowError("BODY_REQUIRED", "请只返回正文 TeX")


def validate_conversion(result, pages):
    if [p["page"] for p in result["pages"]] != pages:
        raise WorkflowError("PAGE_COVERAGE", "返回页码必须与本批页面按序一致")
    for p in result["pages"]:
        safe_tex(p["tex"])
    for group, macro in [("headings", "BAHeading"), ("assets", "BAFigure")]:
        ids = [item["id"] for item in result[group]]
        if len(ids) != len(set(ids)) or any(
            not re.fullmatch(r"[A-Za-z0-9-]+", x) for x in ids
        ):
            raise WorkflowError("INVALID_ID", "标题或图像 ID 重复或格式无效")
        used = re.findall(
            r"\\" + macro + r"\{([A-Za-z0-9-]+)\}",
            "\n".join(p["tex"] for p in result["pages"]),
        )
        if sorted(used) != sorted(ids):
            raise WorkflowError("MISSING_ANCHOR", "标题或图片标记与记录不一致")
        for item in result[group]:
            if item["page"] not in pages:
                raise WorkflowError("PAGE_SCOPE", "结果引用了本批之外的页面")
    for h in result["headings"]:
        safe_tex(h["title"])
        safe_tex(h["number"])
    for a in result["assets"]:
        b = a["bbox"]
        if len(b) != 4 or not 0 <= b[0] < b[2] <= 1 or not 0 <= b[1] < b[3] <= 1:
            raise WorkflowError("IMAGE_REGION", "图像裁剪范围无效")


def namespace(result, tid):
    import copy

    result = copy.deepcopy(result)
    for group, macro in [("headings", "BAHeading"), ("assets", "BAFigure")]:
        for item in result[group]:
            old = item["id"]
            item["id"] = tid + "-" + old
            for p in result["pages"]:
                p["tex"] = p["tex"].replace(
                    "\\" + macro + "{" + old + "}",
                    "\\" + macro + "{" + item["id"] + "}",
                )
    return result


def apply_seam(left, right, patch):
    a, b = patch["left_suffix"], patch["right_prefix"]
    if not a and not b:
        if patch["replacement"]:
            raise WorkflowError("INVALID_PATCH", "空接缝不能插入额外内容")
        return left, right
    if not left.endswith(a) or not right.startswith(b):
        raise WorkflowError("STALE_PATCH", "接缝补丁与原片段不匹配")
    safe_tex(patch["replacement"])
    before = re.findall(r"\\BA(?:Heading|Figure)\{[^}]+\}", a + b)
    after = re.findall(r"\\BA(?:Heading|Figure)\{[^}]+\}", patch["replacement"])
    if before != after:
        raise WorkflowError("ANCHOR_CHANGE", "接缝修复不能删除标题或图像")
    return (left[: -len(a)] if a else left) + patch["replacement"], right[len(b) :]


PREAMBLE = (
    r"""\usepackage{amsmath,amssymb,amsthm,mathtools,graphicx,hyperref}
\newcommand{\BAHeading}[1]{\csname bah#1\endcsname}
\newcommand{\BAFigure}[1]{\includegraphics[width=\linewidth,keepaspectratio]{assets/#1.png}}
"""
    + "\n".join(
        r"\newtheorem*{" + name + "}{" + name.title() + "}"
        for name in [
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
    )
    + "\n"
)


def render_document(setup, results, headings):
    definitions = []
    for h in headings:
        safe_tex(h["title"])
        safe_tex(h["number"])
        level = h["level"]
        if setup["documentclass"] == "article" and level == "chapter":
            level = "section"
        title = (h["number"] + " " if h["number"] else "") + h["title"]
        definitions.append(
            r"\expandafter\def\csname bah"
            + h["id"]
            + "\\endcsname{\\"
            + level
            + "*{"
            + title
            + "}}"
        )
    body = []
    mapping = []
    line = 1
    for result in results:
        for p in result["pages"]:
            marker = f"% PDF page {p['page']}\n"
            text = marker + p["tex"] + "\n"
            mapping.append(
                {"page": p["page"], "line": line + 1, "task_id": result["task_id"]}
            )
            body.append(text)
            line += text.count("\n")
    main = (
        r"\documentclass{"
        + setup["documentclass"]
        + "}\n"
        + r"\input{preamble}\input{headings}\begin{document}\input{body}\end{document}"
        + "\n"
    )
    return {
        "main.tex": main,
        "preamble.tex": PREAMBLE,
        "headings.tex": "\n".join(definitions) + "\n",
        "body.tex": "".join(body),
    }, mapping


def open_environments(fragments):
    """Carry only open environment names to the junction task, never earlier prose."""
    stack = []
    for fragment in fragments:
        for line in fragment.splitlines():
            line = re.split(r"(?<!\\)%", line, 1)[0]
            for operation, name in re.findall(r"\\(begin|end)\{([A-Za-z*]+)\}", line):
                if operation == "begin":
                    stack.append(name)
                elif stack and stack[-1] == name:
                    stack.pop()
    return stack
