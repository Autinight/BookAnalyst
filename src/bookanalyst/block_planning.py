"""Shared compact source representation and bounded worker queue; legacy hierarchy helpers."""
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
