"""Collect as test_hostd_onboarding_shutdown.py beside the real wiring fixture.

Private ONE-test proposal; mandatory Python 3.11 execution remains pending.
Source origins and the mandatory target interpreter are pinned by the collector.
"""
import asyncio
import unittest

import test_hostd_delivery_notice_wiring as fixture
from hostd.onboarding_runtime import OnboardingRuntime

setUpModule = fixture.setUpModule
tearDownModule = fixture.tearDownModule


async def cancel_and_join_owned(tasks):
    """Only test-owned tasks; a second cancellation survives the 3.11 race."""
    pending = {task for task in tasks if not task.done()}
    for _ in range(2):
        if not pending:
            break
        for task in pending:
            task.cancel()
        _, pending = await asyncio.wait(pending, timeout=3)
    if pending:
        raise AssertionError('test-owned tasks failed to join during bounded cleanup')
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class OnboardingShutdownPython311(unittest.IsolatedAsyncioTestCase):
    async def test_original_shutdown_finishes_after_onboarding_event_wake_and_cancel(self):
        original_methods = {name: getattr(OnboardingRuntime, name)
                            for name in ('run', 'start', 'drain', 'close')}
        original_create = OnboardingRuntime.create.__func__
        world = fixture.WiringWorld(self)
        host = await world.root()
        runtime = host.onboarding
        self.assertIs(type(host), world.hd.Hostd)
        self.assertIs(type(runtime), OnboardingRuntime)
        self.assertIs(OnboardingRuntime.create.__func__, original_create)
        for name, original in original_methods.items():
            self.assertIs(getattr(runtime, name).__func__, original)

        # Use exactly the root's production spawn/supervision path. Do not use
        # runtime.start(), an alternate runtime, or a direct run task.
        supervisor = host._spawn('onboarding', 'onboarding', runtime.run)
        owned = {supervisor}
        event_wait_task = None
        shutdown = None
        try:
            self.assertIn(supervisor, host._tasks)
            self.assertEqual(host._tasks, {supervisor})
            self.assertIsNone(runtime._task)
            wake = runtime._wake
            self.assertIs(type(wake), asyncio.Event)
            deadline = asyncio.get_running_loop().time() + 3
            while event_wait_task is None:
                # CPython 3.11 wait_for creates an Event.wait child task;
                # 3.12+ can await it inline in the original supervisor. Walk
                # the actual coroutine chain without replacing either path.
                for task in asyncio.all_tasks():
                    coro = task.get_coro()
                    visited = set()
                    while coro is not None and id(coro) not in visited:
                        visited.add(id(coro))
                        frame = getattr(coro, 'cr_frame', None)
                        waiter = task._fut_waiter
                        if (getattr(coro, 'cr_code', None) is asyncio.Event.wait.__code__
                                and frame is not None
                                and frame.f_locals.get('self') is wake
                                and waiter in wake._waiters
                                and not waiter.done()):
                            event_wait_task = task
                            owned.add(task)
                            break
                        coro = (getattr(coro, 'cr_await', None)
                                or getattr(coro, 'gi_yieldfrom', None)
                                or getattr(coro, 'ag_await', None))
                    if event_wait_task is not None:
                        break
                if event_wait_task is not None:
                    break
                self.assertFalse(supervisor.done(), 'original supervisor ended before Event.wait')
                self.assertLess(asyncio.get_running_loop().time(), deadline,
                                'original onboarding Event.wait never installed its waiter')
                await asyncio.sleep(0)
            self.assertFalse(wake.is_set())
            self.assertFalse(runtime._closed)

            shutdown = asyncio.create_task(host._shutdown())
            owned.add(shutdown)
            # Observe the originals without wait_for cancelling them at the
            # assertion deadline and accidentally changing the tested race.
            done, pending = await asyncio.wait({shutdown, supervisor}, timeout=3)
            self.assertFalse(pending,
                             'original _shutdown and onboarding supervisor must both finish within 3s')
            self.assertEqual(done, {shutdown, supervisor})
            self.assertFalse(shutdown.cancelled())
            shutdown.result()
            if not supervisor.cancelled():
                supervisor.result()
            self.assertTrue(event_wait_task.done())
            self.assertIsNone(host.onboarding)
            self.assertIsNone(host.runtime_store)
        finally:
            # On RED, cancellation was consumed and the original supervisor is
            # retrying a closed runtime. Retain and join it before fixture.close
            # can invoke shutdown again, so the failure cannot hang teardown.
            await cancel_and_join_owned(owned - ({shutdown} if shutdown else set()))
            if shutdown is None:
                shutdown = asyncio.create_task(host._shutdown())
                owned.add(shutdown)
            _, pending = await asyncio.wait({shutdown}, timeout=3)
            if pending:
                await cancel_and_join_owned(pending)
            await asyncio.gather(*owned, return_exceptions=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
