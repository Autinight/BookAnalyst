"""Bounded block batches and semantic clusters; PDF pages are provenance, never task units."""
import asyncio
from collections import defaultdict
from .store import WorkflowError, digest, encode
from .tokens import budget_tokens
from .semantics import equation_content_ids

HEADINGS = {"part", "chapter", "section", "subsection", "subsubsection"}
ENVIRONMENTS = {"theorem", "lemma", "definition", "proposition", "corollary", "proof",
                "remark", "example", "exercise", "claim"}
ATOMIC = {"equation", "figure", "table"}


def compact_atom(atom):
    # Raw MinerU trees stay in source artifacts, not repeated inside every model prompt.
    return {key: atom[key] for key in ("atom_id", "page_idx", "bbox", "type", "text", "discarded_candidate", "number_evidence")
            if key in atom} | {"resource_count": len(atom.get("resources", []))}


def allowance(config, roles=("analyst", "reviewer")):
    available = min(config[role]["context_limit"] - config[role]["output_tokens"] -
                    config[role]["context_limit"] // 10 for role in roles)
    return max(0, (available - 6000) // 3)


def batches(atoms, config, roles=("analyst", "reviewer")):
    limit = allowance(config, roles)
    count_limit = max(1, (min(config[r]["output_tokens"] for r in roles) - 512) // 160)
    result, current, size = [], [], 0
    for atom in atoms:
        cost = budget_tokens(encode(compact_atom(atom)), config, roles) + 160
        if cost > limit:
            raise WorkflowError("ATOM_TOO_LARGE", "单个 block 超出模型容量；保留来源并请求重新解析或扩大模型容量", review=True)
        if current and (size + cost > limit or len(current) >= count_limit):
            result.append(current)
            current, size = [], 0
        current.append(atom)
        size += cost
    if current:
        result.append(current)
    return result


async def bounded_map(items, worker, concurrency):
    """Only N worker coroutines exist; the first failure stops dispatching new work."""
    iterator = iter(enumerate(items))
    results = [None] * len(items)
    failure = None

    async def consume():
        nonlocal failure
        while failure is None:
            try:
                index, item = next(iterator)
            except StopIteration:
                return
            try:
                results[index] = await worker(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if failure is None:
                    failure = exc
                return

    workers = [asyncio.create_task(consume()) for _ in range(min(concurrency, len(items)))]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    if failure is not None:
        raise failure
    return results


def cluster_tasks(atoms, plan, profile, config):
    """Prefer whole semantic subtrees; only split large containers at child-node boundaries."""
    amap = {a["atom_id"]: a for a in atoms}
    nodes = {n["id"]: n for n in plan["nodes"]}
    children = defaultdict(list)
    owner = {}
    for node in plan["nodes"]:
        children[node["parent_id"]].append(node["id"])
        for aid in node["atom_ids"]:
            owner[aid] = node["id"]
    limit = allowance(config, ("converter", "reviewer"))
    atom_cost = {aid: budget_tokens(encode(compact_atom(amap[aid])), config, ("converter", "reviewer")) + 160 for aid in owner}
    output_limit = max(1, (min(config[r]["output_tokens"] for r in ("converter", "reviewer")) - 512) // 120)
    subtree = {}
    # Parent-before-child order makes a reverse walk a bounded postorder traversal.
    for node in reversed(plan["nodes"]):
        ids = list(node["atom_ids"])
        for child in children[node["id"]]:
            ids.extend(subtree[child])
        subtree[node["id"]] = ids

    units = []
    stack = list(reversed(children[None]))
    while stack:
        nid = stack.pop()
        node, ids = nodes[nid], subtree[nid]
        cost = sum(atom_cost[a] for a in ids)
        if cost <= limit and len(ids) <= output_limit:
            if ids:
                units.append({"cluster_id": nid, "atom_ids": ids, "cost": cost})
            continue
        own = node["atom_ids"]
        # An equation/table/image is one semantic object and is never cut to fill a request.
        if node["kind"] in ATOMIC or sum(atom_cost[a] for a in own) > limit or len(own) > output_limit:
            raise WorkflowError("SEMANTIC_UNIT_TOO_LARGE", "完整数学对象超出模型容量，不能按页或字符截断", review=True)
        if own:
            units.append({"cluster_id": nid, "atom_ids": own, "cost": sum(atom_cost[a] for a in own)})
        stack.extend(reversed(children[nid]))

    groups, current, size, count = [], [], 0, 0
    for unit in units:
        # Preserve semantic cluster boundaries; adjacent complete clusters may share a request.
        if current and (size + unit["cost"] > limit or count + len(unit["atom_ids"]) > output_limit):
            groups.append(current)
            current, size, count = [], 0, 0
        current.append(unit)
        size += unit["cost"]
        count += len(unit["atom_ids"])
    if current:
        groups.append(current)

    def path(aid):
        chain, nid = [], owner[aid]
        while nid is not None:
            node = nodes[nid]
            if node["kind"] in HEADINGS | ENVIRONMENTS:
                chain.append({k: node[k] for k in ("id", "parent_id", "kind", "title", "number")})
            nid = node["parent_id"]
        return list(reversed(chain))

    tasks = []
    body_order = [aid for n in plan["nodes"] for aid in n["atom_ids"]]
    positions = {aid: i for i, aid in enumerate(body_order)}
    for i, group in enumerate(groups):
        owned = [aid for unit in group for aid in unit["atom_ids"]]
        first, last = positions[owned[0]], positions[owned[-1]]
        context = body_order[max(0, first - 1):first] + body_order[last + 1:last + 2]
        owner_ids = list(dict.fromkeys(owner[aid] for aid in owned))
        paths = path(owned[0]), path(owned[-1])
        task = {"task_id": f"task-{i:04d}", "owned_atom_ids": owned, "context_atom_ids": context,
                "cluster_ids": [u["cluster_id"] for u in group],
                "profile_hash": profile["profile_hash"], "model": config["converter"], "review_model": config["reviewer"],
                "semantic_nodes": [nodes[nid] for nid in owner_ids],
                "environment_start": paths[0], "environment_end": paths[1],
                "math_groups": [equation_content_ids(nodes[nid], amap) for nid in owner_ids if nodes[nid]["kind"] == "equation"],
                "number_only_atom_ids": [aid for nid in owner_ids if nodes[nid]["kind"] == "equation"
                                         for aid in nodes[nid]["atom_ids"] if aid not in equation_content_ids(nodes[nid], amap)],
                "prompt_version": "0.2.1"}
        task["input_hash"] = digest({"task": task, "source": [compact_atom(amap[a]) for a in owned],
                                    "context": [compact_atom(amap[a]) for a in context]})
        tasks.append(task)
    if [a for t in tasks for a in t["owned_atom_ids"]] != body_order:
        raise WorkflowError("CONTENT_COVERAGE", "语义集群没有按顺序唯一覆盖全部正文")
    boundaries = []
    for left, right in zip(tasks, tasks[1:]):
        left_path = {n["id"] for n in left["environment_end"]}
        continuing = [n for n in right["environment_start"] if n["id"] in left_path]
        boundaries.append({"left": left["task_id"], "right": right["task_id"],
                           "continuing_environments": continuing,
                           "source_ids": left["owned_atom_ids"][-1:] + right["owned_atom_ids"][:1]})
    return {"tasks": tasks, "clusters": units, "boundaries": boundaries,
            "budget_method": "semantic_subtrees_then_bounded_blocks"}
