"""Dependency-free drain for already-owned asynchronous/worker IO."""

import asyncio


async def await_durable(awaitable, *, on_cancel=None):
    """Join owned IO before propagating even repeated caller cancellation.

    Cancelling a to_thread future does not stop its worker. Persistence and
    process IO must settle before the owner releases resources/terminal state.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if on_cancel is not None:
                try:
                    on_cancel()  # Original owner fence BEFORE joining entered IO.
                except BaseException:
                    pass
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        else:
            if cancelled:
                raise asyncio.CancelledError
            return result
