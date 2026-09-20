"""Selected image repair using the final compiler's persistent native agent."""
from typing import Literal
import sys

from pydantic import Field, ValidationError

from .documents import Result
from .figure_assets import asset_path, image_inventory
from .store import WorkflowError, atomic_json, write_async


class SpecialImageResult(Result):
    id: str
    state: Literal["PASSED", "FIXED", "DEFERRED"]
    reason: str = Field(min_length=1)


class SpecialImageReport(Result):
    images: list[SpecialImageResult]


def special_instructions(run, project):
    return r"""
This is SPECIAL IMAGE REPAIR, not just a compilation task. Even if the project already
compiles, inspect and repair the selected targets in image-special-targets.json.
Use the compiler's native tools: read/search the project and source PDF, view images,
run local image-processing/rendering commands or scripts, edit images and related TeX,
and compile and inspect the affected output PDF pages. Read files on demand.
You may correct the selected figures' crops, embedded labels, captions, subfigure
structure, insertion commands and local layout when the source provides evidence.
Preserve the author's mathematics and content; do not invent missing details. Keep
unselected figures and unrelated text unchanged except necessary shared compilation fixes.
Compare old and new crops against the source and retain the better version. Inspect
the rendered result; a successful compilation alone does not confirm visual correctness.
Keep each target's original asset ID and assets/<id>.png as its current preview, including
when repairing a composite figure or TeX rendering. Update image-assets.json with actual
source page, normalized bbox and width_ratio when changed; preserve other asset records.
Before returning, write image-special-review.json with exactly one record per selected
target: {"images":[{"id":"...","state":"FIXED","reason":"具体中文理由"}]}.
Use FIXED for a repaired and visually verified target, PASSED for an already correct
target, DEFERRED for remaining uncertainty. Explain remaining defects honestly and retain
the best available version. Do not mark images confirmed solely because TeX compiles.
The target file includes prior failure/deferred reasons and crop/comparison feedback.
Use that evidence to diagnose and repair each target automatically; do not ask the user
to restate the problem or supply repair instructions. Open the source page, original
crop and current crop yourself. Resolve the reported defects with your tools.
On continuation, preserve completed repairs and address the new feedback in this scope.
This image scope takes priority over the general instruction to fix unrelated layout.
"""


def read_special_report(project, selected):
    from .image_repair import read_json
    try:
        report = SpecialImageReport.model_validate(read_json(project / "image-special-review.json"))
        ids = [item.id for item in report.images]
        if len(ids) != len(selected) or set(ids) != {a["id"] for a in selected}:
            raise ValueError("特殊修复报告必须逐一包含所有选中图片")
        if any(not item.reason.strip() for item in report.images):
            raise ValueError("特殊修复报告缺少具体理由")
        return {item.id: item.model_dump() for item in report.images}
    except (OSError, ValueError, ValidationError) as exc:
        raise WorkflowError("IMAGE_SPECIAL_REPORT", "特殊修复报告未完成，请继续修复：" + str(exc)) from exc


def special_rows(store, run, project, selected):
    results = read_special_report(project, selected)
    _, inventory = image_inventory(store, run["source"], project, run["id"])
    current = {a["id"]: a for a in inventory}
    rows = []
    for a in selected:
        updated = current.get(a["id"])
        if not updated or not updated["available"]:
            raise WorkflowError("IMAGE_SPECIAL_OUTPUT", "特殊修复缺少目标图片或插入位置：" + a["id"])
        rows.append(updated | results[a["id"]] | {"before_bbox": a["bbox"], "before_width_ratio": a.get("width_ratio")})
    return rows


async def repair_special_images(engine, run):
    from .finisher import repair_project
    from .image_repair import read_json
    store, rid = engine.store, run["id"]
    base = store.directory(rid)
    project = base / "tex"
    selected = read_json(base / "image-selection.json")
    marker = base / "image-special-applied.json"
    engine.check_pause(run)
    feedback = {"status": "SPECIAL_IMAGE_REPAIR_REQUESTED", "message": "Inspect and repair the selected images even if TeX already compiles."}
    if marker.exists():
        try:
            special_rows(store, run, project, selected)
        except WorkflowError as exc:
            feedback = {"status": "FAILED", "code": exc.code, "message": exc.message}
        else:
            feedback = None
    elif run.get("error"):
        feedback["previous_error"] = run["error"]
    if feedback:
        targets = []
        for a in selected:
            engine.check_pause(run)
            page_image = await engine.image(run["source"], a["page"]) if a["page"] else None
            targets.append(a | {
                "source_page_image": str(page_image.resolve()) if page_image else None,
                "current_crop": str(asset_path(project, a["id"]).resolve()),
                "original_crop": str(asset_path(base / "image-original", a["id"]).resolve()),
            })
        await write_async(atomic_json, project / "image-special-targets.json", {
            "images": targets, "python_executable": sys.executable,
            "original_crops": str((base / "image-original").resolve()),
            "source_pdf": run["source"]["path"],
        })
        for a in selected:
            store.task(rid, "image-" + a["id"], "image_repair", "RUNNING", [a["page"]] if a["page"] else [])
        await repair_project(engine.providers, engine.live(rid), project, feedback)
        engine.check_pause(run)
        special_rows(store, run, project, selected)
        await write_async(atomic_json, marker, {"applied": True})
    # Reuse the compiler's continuation loop and its final PDF validation.
    await engine.compile(run, [], [], {})
    engine.check_pause(run)
    rows = special_rows(store, run, project, selected)
    for row in rows:
        await write_async(atomic_json, base / "image-checks" / (row["id"] + ".json"), row)
        store.task(rid, "image-" + row["id"], "image_repair", "DEFERRED" if row["state"] == "DEFERRED" else "PASSED",
                   [row["page"]] if row["page"] else [], {"message": row["reason"]})
    await write_async(atomic_json, project / "image-review.json", {
        "source_result_id": run["image_repair"]["source_result_id"], "images": rows,
    })
