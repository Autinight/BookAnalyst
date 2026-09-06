"""Scheduling and concurrency checks; not an LLM content-quality evaluation."""
import asyncio
import pytest
from bookanalyst.block_planning import bounded_map
from bookanalyst.store import WorkflowError

@pytest.mark.asyncio
async def test_bounded_queue_stops_dispatch_after_failure_with_thousands_of_pending_items():
    active = peak = 0
    started = []
    async def worker(index):
        nonlocal active, peak
        started.append(index)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.002)
            if index == 7:
                raise WorkflowError("SYNTHETIC_FAILURE", "stop dispatch")
            return index
        finally:
            active -= 1
    with pytest.raises(WorkflowError):
        await bounded_map(list(range(10000)), worker, 4)
    assert peak <= 4 and len(started) <= 11 and active == 0
