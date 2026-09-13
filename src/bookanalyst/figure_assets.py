"""Portable crop provenance, also recoverable from older conversion runs."""
import json
import math
import re

from .store import WorkflowError, atomic_json


MANIFEST = "image-assets.json"


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(x) in (int, float) and math.isfinite(x) for x in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def asset_path(project, identifier):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise WorkflowError("INVALID_IMAGE_ID", "图片标识无效", 422)
    path = project / "assets" / (identifier + ".png")
    if not path.resolve().is_relative_to(project.resolve()):
        raise WorkflowError("INVALID_PATH", "图片路径超出工程目录", 422)
    return path


def save_manifest(project, results, source):
    # Existing provenance can contain repaired crops; never replace it with old batches.
    if (project / MANIFEST).exists():
        return
    assets = [dict(a, file=f"assets/{a['id']}.png", dpi=150)
              for batch in results for a in batch.get("assets", [])]
    if assets:
        atomic_json(project / MANIFEST, {"source_sha256": source["sha256"], "assets": assets})


def load_manifest(store, book, project, rid):
    if (project / MANIFEST).exists():
        value = json.loads((project / MANIFEST).read_text(encoding="utf-8"))
        if value.get("source_sha256") != book["sha256"]:
            raise WorkflowError("IMAGE_SOURCE_CHANGED", "图片来源记录与原书不一致")
        return value
    seen = set()
    while rid and rid not in seen:
        seen.add(rid)
        try:
            base = store.directory(rid)
        except WorkflowError:
            break
        batches = sorted((base / "batches").glob("*.json"))
        assets = [dict(a, file=f"assets/{a['id']}.png", dpi=150)
                  for path in batches
                  for a in json.loads(path.read_text(encoding="utf-8")).get("assets", [])]
        if assets:
            return {"source_sha256": book["sha256"], "assets": assets}
        try:
            run = store.get("run", rid)
        except WorkflowError:
            break
        # Template jobs already refer to the immutable source result.
        result_id = run.get("template", {}).get("source_result_id")
        rid = run.get("image_repair", {}).get("source_run_id")
        if not rid and result_id:
            meta = project.parent / result_id / "retained.json"
            if meta.is_file():
                rid = json.loads(meta.read_text(encoding="utf-8"))["run_id"]
    return {"source_sha256": book["sha256"], "assets": []}


def image_inventory(store, book, project, rid):
    manifest = load_manifest(store, book, project, rid)
    tex = "\n".join(p.read_text(encoding="utf-8") for p in project.rglob("*.tex"))
    records = {a["id"]: a for a in manifest["assets"]}
    identifiers = set(re.findall(r"\\BAFigure\{([^{}]+)\}", tex))
    for path in (project / "assets").glob("*.png"):
        if path.name in tex or f"assets/{path.stem}" in tex:
            identifiers.add(path.stem)
    rows = []
    for identifier in sorted(identifiers):
        try:
            path = asset_path(project, identifier)
        except WorkflowError:
            continue
        a = records.get(identifier, {})
        page, box = a.get("page"), a.get("bbox")
        known = type(page) is int and 1 <= page <= book["page_count"] and valid_box(box)
        marker = "\\BAFigure{" + identifier + "}"
        position = tex.find(marker)
        if position < 0:
            position = tex.find(path.name)
        rows.append({"id": identifier, "page": page, "bbox": box,
                     "file": f"assets/{identifier}.png", "dpi": a.get("dpi", 150),
                     "available": path.is_file(), "repairable": known,
                     "reason": "" if known else "缺少原页码或裁剪记录，不能自动猜测来源",
                     "context": tex[max(0, position - 300):position + 700] if position >= 0 else ""})
    return manifest, rows
