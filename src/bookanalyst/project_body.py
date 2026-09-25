"""Read the body of a single-file or organized TeX project for page attribution."""

import re
from pathlib import Path

from .store import WorkflowError


INPUT = re.compile(rb"\\input\{([A-Za-z0-9_./-]+)\}\r?\n")


def read_project_body(project):
    project = Path(project).resolve()
    body = (project / "body.tex").read_bytes()
    # A regular document is authoritative as-is; only expand a pure input manifest.
    matches = list(INPUT.finditer(body))
    if not matches or b"".join(m.group() for m in matches) != body:
        return body.decode("utf-8")
    parts = []
    for match in matches:
        name = match[1].decode("ascii")
        if name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/")):
            raise WorkflowError("STRUCTURE_PATH", "正文入口存在无效路径")
        path = project / (name + ".tex")
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(project):
            raise WorkflowError("STRUCTURE_PATH", "正文入口引用的文件缺失或越界")
        parts.append(path.read_bytes())
    return b"".join(parts).decode("utf-8")
