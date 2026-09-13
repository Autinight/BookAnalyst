"""Task slots that recheck a run's concurrency without cancelling active work."""
import asyncio


class TaskSlots:
    def __init__(self, get_limit):
        self.get_limit = get_limit
        self.active = 0
        self.changed = asyncio.Event()

    async def __aenter__(self):
        while self.active >= self.get_limit():
            self.changed.clear()
            await self.changed.wait()
        self.active += 1

    async def __aexit__(self, *exc):
        self.active -= 1
        self.changed.set()
