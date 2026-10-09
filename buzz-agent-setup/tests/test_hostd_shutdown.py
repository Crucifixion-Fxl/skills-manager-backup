"""Actual executor and application locks must outlive a cancelled root."""
import asyncio
import threading
import unittest
from unittest import mock

import test_hostd_wiring_lifecycle as fixture
from hostd.store import Store


class Shutdown(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def test_shutdown_reaps_worker_io_before_releasing_app_lock_and_store(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        connected = asyncio.Event()
        db = Store(self.h.store_path)
        self.h.runtime_store = db

        def run(*args, **kwargs):
            entered.set()
            try:
                if not release.wait(10):
                    raise AssertionError('offline worker transport was not released')
                return {'hostd': {'verdict': 'off'}}
            finally:
                finished.set()

        async def feed(app, bindings):
            with fixture.hd.app_lock(app, self.h.app_lock_dir):
                if app == 'cli_alpha':
                    connected.set()
                await asyncio.Event().wait()

        async def follow(*args, **kwargs):
            await asyncio.Event().wait()

        with mock.patch.object(self.h.workers['alpha'], 'run', run), \
                mock.patch.object(self.h, 'feishu_child', feed), \
                mock.patch.object(fixture.hd.relay_feed, 'follow', follow), \
                mock.patch.object(fixture.hd, 'DEBOUNCE', .001):
            task = asyncio.create_task(self.h.main())
            try:
                await asyncio.wait_for(connected.wait(), 2)
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                for _ in range(20):
                    await asyncio.sleep(.002)
                self.assertFalse(task.done())
                self.assertIsNotNone(db.conn)
                with self.assertRaises(ValueError):
                    with fixture.hd.app_lock('cli_alpha', self.h.app_lock_dir):
                        pass
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
                with self.assertRaises(ValueError):
                    with fixture.hd.app_lock('cli_alpha', self.h.app_lock_dir):
                        pass
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
                db.close()
        self.assertTrue(finished.is_set())
        self.assertTrue(task.cancelled())
        self.assertIsNone(db.conn)
        with fixture.hd.app_lock('cli_alpha', self.h.app_lock_dir):
            pass


if __name__ == '__main__':
    unittest.main()
