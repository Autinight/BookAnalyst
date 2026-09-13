"""Task slots that recheck a run's concurrency without cancelling active work."""
import asyncio
import inspect


class TaskSlots:
    def __init__(self, get_limit):
        self.get_limit = get_limit
        self.active = 0
        self.changed = asyncio.Event()

    async def __aenter__(self):
        while True:
            self.changed.clear()
            limit = self.get_limit()
            if inspect.isawaitable(limit):
                limit = await limit
            if self.active < limit:
                break
            await self.changed.wait()
        self.active += 1

    async def __aexit__(self, *exc):
        self.active -= 1
        self.changed.set()
