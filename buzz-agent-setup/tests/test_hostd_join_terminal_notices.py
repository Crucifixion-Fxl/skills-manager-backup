"""Persisted terminal cards recover via real Runtime/SQL and pooled fake HTTP."""
import json
import unittest
from unittest import mock

import test_hostd_onboarding_runtime as fixture
import test_hostd_http_pool as hp
from hostd.http_pool import HttpPool
from hostd.store import Store


class TerminalNotices(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.RuntimeTests.setUp

    async def service(self):
        self.server = hp.Server()
        pool = HttpPool(connection_factory=self.server.connect, scheduler=self.scheduler)
        self.addCleanup(pool.close)
        service = await fixture.runtime.OnboardingRuntime.create(
            fixture.runtime.RuntimeConfig(**self.kw), self.db, registrar=None,
            scheduler=self.scheduler, http_pool=pool, http=self.w.http,
            runner=self.w.runner, clock=lambda: self.w.now)
        service._retry_clock = lambda: self.w.now
        self.addCleanup(service.close)
        return service

    def seed(self, index=1, state='done', *, notice=True):
        # A persisted terminal snapshot, not an approval/callback simulation.
        rid, mid = 'JOIN-%08x' % index, 'om_terminal%d' % index
        self.db.register_agent(self.f.pub, owner_pubkey=self.f.owner, app_id='cli_agent', now=100)
        self.db.create_join(rid, self.f.pub, self.f.owner, 'cli_agent', 'oc_group%d' % index,
                            kind='new_binding', now=100)
        self.db.rotate_card(rid, mid, now=101)
        self.db.conn.execute('UPDATE join_request SET status=?,updated_at=102 WHERE request_id=?', (state, rid))
        if notice:self.db.queue_join_notice(rid, mid, state, now=103)
        return rid, mid

    def patches(self):
        return [c for c in self.server.calls if c['method']=='PATCH']

    def unchanged_authority(self):
        return [tuple(r) for r in self.db.conn.execute('SELECT * FROM join_request')], [tuple(r) for r in self.db.conn.execute('SELECT * FROM agent_chat')], [tuple(r) for r in self.db.conn.execute('SELECT * FROM join_decision')]

    async def test_runtime_restart_recovers_done_denied_expired_bounded_without_effect_replay(self):
        refs=[self.seed(i, state) for i,state in enumerate(('done','denied','expired'),1)]
        # Reopen actual SQLite before constructing the actual runtime.
        path=self.db.path;self.db.close();self.db=Store(path);self.addCleanup(self.db.close)
        service=await self.service();before=self.unchanged_authority()
        with mock.patch.object(service.effects,'apply',new_callable=mock.AsyncMock) as apply, \
             mock.patch.object(service.effects,'cleanup',new_callable=mock.AsyncMock) as cleanup:
            for count in range(1,4):
                await service.drain()
                self.assertEqual(len(self.patches()),count)
                await service.drain()  # A hot reconnect loop cannot spend another slot.
                self.assertEqual(len(self.patches()),count)
                self.w.now+=60
            apply.assert_not_awaited();cleanup.assert_not_awaited()
        self.assertEqual(self.unchanged_authority(),before)
        self.assertEqual({c['path'] for c in self.patches()}, {'/open-apis/im/v1/messages/'+mid for _,mid in refs})
        self.assertEqual(self.db.join_notices(),[])
        self.assertFalse(any(c['method']=='POST' and '/im/v1/messages' in c['path'] for c in self.server.calls))
        for call in self.patches():
            payload=json.loads(json.loads(call['body'])['content'])
            self.assertFalse(any(e.get('tag')=='action' for e in payload['elements']))

    async def test_present_reserves_with_current_time_after_effect_await(self):
        rid,mid=self.seed(notice=False);service=await self.service()
        await service.coordinator._present(self.db.join_request(rid),'done',100)
        self.assertEqual([c['path'] for c in self.patches()],['/open-apis/im/v1/messages/'+mid])
        self.assertEqual(self.db.join_notices(),[])

    async def test_unknown_patch_keeps_original_target_payload_and_backoff(self):
        rid,mid=self.seed();service=await self.service();before=self.unchanged_authority()
        self.server.responses=[TimeoutError('synthetic lost PATCH response')]
        await service.drain()
        self.assertEqual(len(self.patches()),1)
        pending=self.db.join_notices()[0]
        self.assertEqual(pending['status'],'pending');self.assertEqual(pending['attempts'],1)
        self.w.now=pending['next_due']-1
        await service.drain();self.assertEqual(len(self.patches()),1)
        self.w.now=pending['next_due']+60
        await service.drain();self.assertEqual(len(self.patches()),2)
        self.assertEqual(self.patches()[0]['path'],self.patches()[1]['path'])
        self.assertEqual(self.patches()[0]['body'],self.patches()[1]['body'])
        self.assertEqual(self.db.join_notices(),[])
        self.assertEqual(self.unchanged_authority(),before)

    async def test_reserved_notice_lease_and_backoff_survive_runtime_restart(self):
        rid,mid=self.seed();self.assertEqual(self.db.reserve_join_notice(rid,mid,now=990),'done')
        service=await self.service()
        await service.drain();self.assertEqual(self.patches(),[])
        self.w.now=1289;await service.drain();self.assertEqual(self.patches(),[])
        self.w.now=1350;await service.drain();self.assertEqual(len(self.patches()),1)

    async def test_stale_generation_notice_cannot_patch_replacement_or_original(self):
        rid,mid=self.seed()
        self.db.conn.execute('UPDATE join_request SET card_message_id=?,card_generation=2 WHERE request_id=?',('om_replacement',rid))
        service=await self.service();before=self.unchanged_authority()
        await service.drain()
        self.assertEqual(self.patches(),[])
        self.assertEqual(self.unchanged_authority(),before)
        self.assertEqual(self.db.join_notices()[0]['status'],'pending')

    async def test_requested_without_notice_stays_unapproved(self):
        rid,mid=self.seed(state='requested',notice=False);service=await self.service()
        # Existing original card is already known: no send/recovery is needed.
        before=self.unchanged_authority()
        await service._foreground()
        self.assertEqual(self.patches(),[])
        self.assertEqual(self.unchanged_authority(),before)

    async def test_stale_state_cannot_restore_approval_ui_after_done(self):
        rid,mid=self.seed()
        self.db.conn.execute("UPDATE join_notice SET state='approved' WHERE request_id=?",(rid,))
        service=await self.service()
        await service._foreground()
        self.assertEqual(self.patches(),[])
        self.assertEqual(self.db.join_request(rid)['status'],'done')

    async def test_future_notice_does_not_starve_due_terminal_notice(self):
        rid,mid=self.seed(1);other,othermid=self.seed(2,'denied')
        self.db.reserve_join_notice(rid,mid,now=990)
        service=await self.service()
        await service._foreground()
        self.assertEqual([c['path'] for c in self.patches()],['/open-apis/im/v1/messages/'+othermid])
        self.assertEqual(self.db.join_notices()[0]['message_id'],mid)

    async def test_closed_runtime_and_live_fifo_prevent_background_notice_patch(self):
        self.seed();service=await self.service()
        service._queue.append(('card','cli_agent',None,None,None))
        await service._retry_notices()
        self.assertEqual(self.patches(),[])
        service._queue.clear();service.close()
        await service._retry_notices()
        await service.coordinator.retry_notices(now=self.w.now)
        self.assertEqual(self.patches(),[])
