"""Single owned asyncio worker with named task futures."""

import asyncio


class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.futures = {}
        self.status = {}
        self.worker = None

    async def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self._worker())

    def submit(self, name, callback):
        if name in self.futures:
            raise ValueError("duplicate task")
        future = asyncio.get_running_loop().create_future()
        self.futures[name] = future
        self.status[name] = "queued"
        self.queue.put_nowait((name, callback, future))
        return future

    def cancel(self, name):
        if self.status.get(name) != "queued":
            return False
        self.futures[name].cancel()
        self.status[name] = "cancelled"
        return True

    async def _worker(self):
        while True:
            job = await self.queue.get()
            try:
                if job is None:
                    return
                name, callback, future = job
                self.status[name] = "running"
                try:
                    result = await callback()
                except Exception as exc:
                    self.status[name] = "failed"
                    if not future.done():
                        future.set_exception(exc)
                else:
                    self.status[name] = "completed"
                    if not future.done():
                        future.set_result(result)
            finally:
                self.queue.task_done()

    async def join(self):
        await self.queue.join()

    async def close(self):
        if self.worker is not None:
            await self.join()
            self.queue.put_nowait(None)
            await self.worker
            self.worker = None
