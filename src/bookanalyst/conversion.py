"""Original PDF image inputs for direct page-batch conversion."""
import asyncio
from collections import defaultdict
from .store import WorkflowError, atomic_json, digest, file_hash


async def source_images(pipeline, run, stage, atoms, label):
    if run["scope"] == "fixture" or run["config"]["visual_mode"] == "disabled":
        return [], []
    if file_hash(run["source"]["path"]) != run["source"]["sha256"]:
        raise WorkflowError("SOURCE_CHANGED", "原始 PDF 已变化，不能继续引用旧证据")
    from .pdf import render_page
    by_page = defaultdict(list)
    for atom in atoms:
        by_page[atom["page_idx"]].append(atom)
    root = pipeline.store.directory(run["id"], run["revision"], stage) / "image_work" / digest(label)[:20]
    images, evidence = [], []
    for page_idx, selected in sorted(by_page.items()):
        boxes = [a.get("bbox") for a in selected]
        sizes = [a.get("page_size") for a in selected]
        bbox = None
        if all(b and len(b) == 4 for b in boxes) and all(z and len(z) == 2 and min(z) > 0 for z in sizes):
            width, height = sizes[0]
            bbox = [max(0, min(b[0] for b in boxes) - 8), max(0, min(b[1] for b in boxes) - 8),
                    min(width, max(b[2] for b in boxes) + 8), min(height, max(b[3] for b in boxes) + 8)]
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                bbox = None
        data, item = await asyncio.to_thread(render_page, run["source"]["path"], page_idx, 200, bbox)
        path = root / f"page-{page_idx}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        item.update(source_sha256=run["source"]["sha256"], source_ids=[a["atom_id"] for a in selected],
                    scope="owned_block_regions")
        item["evidence_id"] = "img-" + digest(item)[:24]
        atomic_json(path.with_suffix(".json"), item)
        images.append(path)
        evidence.append(item)
    return images, evidence
