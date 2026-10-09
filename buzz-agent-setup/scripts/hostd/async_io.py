"""Reap dispatched executor IO before propagating caller cancellation.

The caller retains its locks and owned resources until this await completes.
No retry, readback, ACK or exception logging happens here. A cancelled caller
never consumes a successful IO result as an application receipt; the durable
reservation remains for independent recovery after restart.
"""
from __future__ import annotations

import asyncio


async def thread_call(call, /, *args, **kwargs):
    """Run one synchronous IO call, completing it even after repeated cancel."""
    pending = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    cancelled = False
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break  # Consume and propagate the actual IO result below.
    try:
        result = pending.result()
    except BaseException:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result
