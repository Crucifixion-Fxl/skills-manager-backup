"""The console ledger reflects observed daemon state, without raw diagnostics."""
import asyncio
from unittest import mock
import unittest
import test_hostd_wiring_lifecycle as fixture
from store import Store


class RuntimeMetadata(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def test_connected_callback_records_actual_app_connection(self):
        await self.h.on_feishu('cli_alpha', {'type':'_connecting'}, {'oc_alpha':'alpha'})
        await self.h.on_feishu('cli_alpha', {'type':'_connected'}, {'oc_alpha':'alpha'})
        with Store(self.h.store_path) as db:
            row = db.conn.execute("SELECT status,connected_at FROM connection WHERE kind='feishu' AND identity='cli_alpha'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'connected')
            self.assertGreater(row['connected_at'], 0)

    async def test_reaction_without_chat_keeps_durable_target_in_actual_app_scope(self):
        await self.h.on_feishu('cli_alpha', {'type':'im.message.reaction.created_v1',
            'message_id':'om_old_reply', 'body':'never-copy-this'}, {'oc_alpha':'alpha'})
        with Store(self.h.store_path) as db:
            rows = db.pending_targets('alpha')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['message_id'], 'om_old_reply')
            self.assertEqual(rows[0]['app_id'], 'cli_alpha')
            self.assertEqual(db.pending_targets('beta'), [])
            self.assertNotIn('never-copy-this', '\n'.join(db.conn.iterdump()))
        self.assertIn('feishu', self.h.dirty['alpha'])

    async def test_untrusted_app_and_malformed_target_never_enqueue(self):
        for event in ({'type':'im.message.reaction.created_v1','app':'cli_beta','message_id':'om_old'},
                      {'type':'im.message.receive_v1','chat_id':'oc_alpha','message_id':{'body':'secret'}}):
            await self.h.on_feishu('cli_alpha', event, {'oc_alpha':'alpha'})
        with Store(self.h.store_path) as db:
            self.assertEqual(db.pending_targets('alpha'), [])

    async def test_binding_stays_pending_until_observed_worker_success(self):
        with Store(self.h.store_path) as db:
            self.assertEqual(db.bindings()[0]['status'], 'pending')
        with mock.patch.object(fixture.hd, 'DEBOUNCE', 0.001):
            task = asyncio.create_task(self.h.worker('alpha'))
            self.h.mark('alpha', 'buzz')
            try:
                deadline = asyncio.get_running_loop().time() + 3
                while asyncio.get_running_loop().time() < deadline:
                    with Store(self.h.store_path) as db:
                        if db.bindings()[0]['status'] == 'active': break
                    await asyncio.sleep(0.01)
                with Store(self.h.store_path) as db:
                    self.assertEqual(db.bindings()[0]['status'], 'active')
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failed_worker_records_degraded_without_raw_error(self):
        def fail(*args, **kwargs): raise RuntimeError('secret-canary@example.test')
        self.h.workers['alpha'].run = fail
        with mock.patch.object(fixture.hd, 'DEBOUNCE', 0.001):
            task = asyncio.create_task(self.h.worker('alpha'))
            self.h.mark('alpha', 'buzz')
            try:
                deadline = asyncio.get_running_loop().time() + 3
                while asyncio.get_running_loop().time() < deadline:
                    with Store(self.h.store_path) as db:
                        if db.bindings()[0]['status'] == 'degraded': break
                    await asyncio.sleep(0.01)
                with Store(self.h.store_path) as db:
                    self.assertEqual(db.bindings()[0]['status'], 'degraded')
                    self.assertNotIn('secret-canary', '\n'.join(db.conn.iterdump()))
                self.assertNotIn('secret-canary', self.h.status_file.read_text())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__': unittest.main()
