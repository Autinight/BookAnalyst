"""Keep every MinerU block and its provenance, including discarded-block candidates."""
from pathlib import Path, PurePosixPath
from .store import WorkflowError, digest


def resolve_asset(assets, reference):
    root = Path(assets).resolve()
    relative = PurePosixPath(reference.replace("\\\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
        raise WorkflowError("MISSING_RESOURCE", "解析图像路径无效")
    candidates = [root.joinpath(*relative.parts)]
    if len(relative.parts) == 1:
        candidates.append(root / "images" / relative.name)
    valid = [path.resolve() for path in candidates
             if path.resolve().is_relative_to(root) and path.is_file()]
    if not valid or len({digest(path.read_bytes()) for path in valid}) > 1:
        raise WorkflowError("MISSING_RESOURCE", "解析图像资源缺失或存在同名歧义")
    return valid[0]


def normalize(layouts, source_sha):
    atoms, source_map = [], {}
    for chunk, layout, assets in layouts:
        pages = layout["pdf_info"]
        if len(pages) != len(chunk["pages"]):
            raise WorkflowError("PAGE_COVERAGE", "解析返回页数不符")
        for local_idx, page in enumerate(pages):
            page_idx = chunk["pages"][local_idx]
            if "page_idx" in page and page["page_idx"] != local_idx:
                raise WorkflowError("PAGE_ORDER", "MinerU 页索引不连续")
            width, height = page.get("page_size", [0, 0])
            # MinerU para_blocks may move lines across pages and leave lines_deleted placeholders.
            # Keep physical source regions intact; semantic continuation is S3's responsibility.
            source_layer = "preproc_blocks" if page.get("preproc_blocks") else "para_blocks"
            entries = [(b, False) for b in page.get(source_layer, [])]
            entries += [(b, True) for b in page.get("discarded_blocks", [])]
            for block_idx, (block, discarded) in enumerate(entries):
                raw_id = digest([source_sha, chunk["id"], chunk["sha256"], page_idx, block_idx])[:24]
                parts, resources = [], []

                def visit(item):
                    for line in item.get("lines", []):
                        spans = []
                        for span in line.get("spans", []):
                            kind = span.get("type", "text")
                            if span.get("image_path"):
                                resources.append(str(resolve_asset(assets, span["image_path"])))
                            text = span.get("content", "")
                            if kind in ("inline_equation", "inline_equation_v2") and text:
                                text = r"\(" + text + r"\)"
                            spans.append(text)
                        parts.append("".join(spans))
                    for child in item.get("blocks", []):
                        visit(child)
                visit(block)
                text = "\n".join(parts)
                if not text and block.get("text"):
                    text = block["text"]
                atom_id = "a-" + raw_id
                atom = dict(atom_id=atom_id, raw_id=raw_id, page_idx=page_idx,
                            bbox=block.get("bbox", [0, 0, width, height]), page_size=[width, height],
                            type=block.get("type", "unknown"), text=text, resources=resources,
                            discarded_candidate=discarded, source_block=block, order=len(atoms),
                            source_layer="discarded_blocks" if discarded else source_layer)
                atoms.append(atom)
                source_map[atom_id] = dict(raw_id=raw_id, chunk_id=chunk["id"], page_idx=page_idx,
                                          bbox=atom["bbox"], source_sha256=source_sha, source_layer=atom["source_layer"])
    if not atoms:
        raise WorkflowError("EMPTY_PARSE", "解析结果没有内容块", review=True)
    if len({a["atom_id"] for a in atoms}) != len(atoms):
        raise WorkflowError("DUPLICATE_SOURCE", "来源 ID 不唯一")
    return atoms, source_map
