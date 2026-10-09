"""Daemon remote dispatch: actual protected/signed/SQL authority, synthetic IO.

Only bot/relay HTTP, relay socket and the inherited labelled AES primitive are
offline seams. No resolver, manager, verified target or authorize callback is
mocked. Runtime source is read-only until root approves the tests-first API.
"""
import ast
import asyncio
import dataclasses
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_manager as fixture
from hostd import agent_catalog, store
# The portable process lacks the optional socket package. Load the actual feed
# and assembler with only this dependency import placeholder; every socket in
# these cases uses the lower _connect seam, never a fake follow/daemon factory.
if importlib.util.find_spec('websockets') is None:
    previous_socket_module = sys.modules.get('websockets')
    sys.modules['websockets'] = types.ModuleType('websockets')
    try:
        from hostd import relay_feed
        importlib.import_module('hostd.__main__')
    finally:
        if previous_socket_module is None:
            sys.modules.pop('websockets', None)
        else:
            sys.modules['websockets'] = previous_socket_module
else:
    from hostd import relay_feed
from hostd.onboarding_runtime import OnboardingRuntime, RuntimeConfig
from hostd.scheduler import Scheduler
from hostd.bot_clients import BotLarkCli
from hostd.remote_target import RemoteTargetResolver
import buzz_feishu_group_sync as gs
import recovery_authority as authority

CHANNEL, CHAT, APP, ORIGIN, NOW = fixture.CHANNEL, fixture.CHAT, fixture.APP, fixture.ORIGIN, fixture.NOW
PUB, OWNER = fixture.PUB, fixture.OWNER


class Socket:
    """Actual feed's synthetic WebSocket peer verifies own-key NIP42 AUTH."""
    def __init__(self, world):
        self.w = world
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait(json.dumps(['AUTH', 'synthetic-challenge']))
        self.sub = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send(self, raw):
        packet = json.loads(raw)
        if packet[0] == 'AUTH':
            ev = packet[1]
            self.w.assertTrue(gs._nip01_event_verified(ev))
            self.w.assertEqual((ev['pubkey'], ev['kind']), (PUB, 22242))
            authority.exact_tag(ev, 'challenge', 'synthetic-challenge')
            authority.exact_tag(ev, 'relay', ORIGIN.replace('https:', 'wss:'))
            self.w.assertEqual(authority.attested_owner(ev, PUB), OWNER)
            self.incoming.put_nowait(json.dumps(['OK', ev['id'], True, '']))
        elif packet[0] == 'REQ':
            self.sub = packet[1]
            fil = packet[2]
            self.w.assertEqual(fil['authors'], [PUB])
            self.w.assertEqual(fil['#h'], [CHANNEL])
            self.w.assertEqual(fil['kinds'], [9, 7, 5, 40003])
        elif packet[0] == 'CLOSE':
            self.w.assertEqual(packet[1], self.sub)
        else:
            self.w.fail('unexpected remote feed packet')

    def hint(self, event):
        self.incoming.put_nowait(json.dumps(['EVENT', self.sub, event]))


class World(fixture.World):
    def __init__(self, case):
        super().__init__(case)
        self.bot_lanes = []
        self.sockets = []
        self.closed = False

    def transport(self, url, headers, timeout, *, body=None):
        if 'x-auth-tag' in headers:
            return self.http(url, headers, timeout, body=body)
        return self.owner_http(url, headers, timeout, body=body)

    def connect(self, url):
        self.assertEqual(url, ORIGIN.replace('https:', 'wss:'))
        sock = Socket(self)
        self.sockets.append(sock)
        return sock

    def close(self):
        self.closed = True

    def request(self, app, config, data_dir, method, path, **kwargs):
        self.bot_lanes.append((path, kwargs['chat_id']))
        if path == '/open-apis/im/v1/chats' and kwargs['chat_id'] is None:
            self.assertEqual((app, Path(config), Path(data_dir)), (APP, self.cfg, self.data))
            self.api_calls.append((method, path, kwargs.get('params'), kwargs.get('data')))
            return {'ok': True, 'identity': 'bot', 'data': {'items': [{'chat_id': CHAT}],
                'has_more': self.chats_incomplete, 'page_token': 'same'}}
        # Resolver reads the actual per-chat roster on its unpinned discovery
        # client. HTTP lane is per requested chat without changing that client.
        return super().request(app, config, data_dir, method, path, **kwargs)


class RemoteDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World(self)
        self.db = store.Store(self.w.root / 'metadata' / 'dispatch.db')
        self.addCleanup(self.db.close)
        self.w.db = self.db
        self.scheduler = Scheduler()
        self.addCleanup(self.scheduler.close)
        self.addCleanup(self.w.scope_release.set)
        self.w.owned(self.w.root / 'template.json', '{}')
        (self.w.root / 'bindings').mkdir(mode=0o700)
        self.config = RuntimeConfig(1, str(self.w.owner_env), ORIGIN, fixture.PIN,
            str(self.w.root / 'template.json'), str(self.w.root / 'bindings'),
            str(self.w.legacy_path), str(self.w.catalog_path), (ORIGIN,), remote_link_base=ORIGIN)
        self.target_id = store.Store._remote_digest([PUB, CHANNEL])
        patch = mock.patch.object(relay_feed, '_connect', self.w.connect)
        patch.start()
        self.addCleanup(patch.stop)

    async def service(self):
        self.w.loop = asyncio.get_running_loop()
        service = await OnboardingRuntime.create(self.config, self.db, registrar=None,
            scheduler=self.scheduler, http_pool=self.w, http=self.w.transport,
            base_env={'HOME': str(self.w.root)}, clock=lambda: self.w.now)
        self.addCleanup(service.close)
        self.assertEqual(service.eligible_apps, (APP,), 'actual protected/signed startup control')
        return service

    async def dispatcher(self, service=None):
        service = service or await self.service()
        self.assertIsNotNone(importlib.util.find_spec('hostd.remote_dispatch'),
            'missing root remote dispatch coordinator: genuine feature RED')
        dispatch = importlib.import_module('hostd.remote_dispatch').RemoteDispatch(
            service, scheduler=self.scheduler, http_pool=self.w,
            base_env={'HOME': str(self.w.root)}, clock=lambda: self.w.now)
        self.addAsyncCleanup(dispatch.close)
        return dispatch

    async def wait_for(self, predicate, message):
        # Portable pure-Python signing verifies every repeated proof. This is
        # a fixture rendezvous bound, never a production latency assertion.
        for _ in range(20000):
            if predicate():
                return
            await asyncio.sleep(.01)
        self.fail(message)

    def assert_no_foreign(self):
        self.assertEqual(self.db.bindings(), [])
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM app_profile').fetchone()[0], 0)
        self.assertFalse(self.w.cli_calls)
        self.assertFalse(self.w.closed, 'borrowed HTTP pool remains root-owned')
        self.assertFalse(self.scheduler.closed, 'borrowed scheduler remains root-owned')
        self.assertNotIn('SYNTHETIC_BODY_DO_NOT_CACHE', '\n'.join(self.db.conn.iterdump()))

    def local_binding(self, status='pending'):
        path = self.w.root / 'local.json'
        self.w.owned(path, json.dumps({'channel_id': CHANNEL, 'chat_id': CHAT}))
        self.db.reconcile_bindings([store.BindingRecord('local', CHANNEL, CHAT, APP,
            str(path), str(self.w.cfg), str(self.w.data), self.w.target.mirror_pubkey,
            status=status)], now=NOW)

    async def test_control_actual_zero_binding_startup_and_unpinned_signed_discovery(self):
        service = await self.service()
        self.assertEqual(self.db.bindings(), [])
        bot = BotLarkCli(APP, self.w.cfg, self.w.data, base_env={'HOME':str(self.w.root)},
            http_pool=self.w, chat_id=None)
        def guard(record, channel):
            row = self.db.conn.execute('SELECT * FROM agent WHERE pubkey=?', (PUB,)).fetchone()
            return (record is service.records[APP] and channel == CHANNEL
                    and row['status'] == 'active' and row['app_id'] == APP)
        resolver = RemoteTargetResolver(self.w.catalog_path, self.w.legacy_path,
            relay=service.relay, clients={APP:bot}, authorize=guard, clock=lambda:self.w.now)
        answer = await resolver.resolve(service.records[APP], CHANNEL)
        self.assertEqual(answer.status, 'verified')
        self.assertEqual(answer.target.chat_id, CHAT)
        self.assertIsNone(bot.chat_id)
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assert_no_foreign()

    async def test_control_actual_own_feed_authenticates_and_provides_verified_hint(self):
        await self.service()
        hints = []
        async def event(identity, value):
            hints.append(value)
        task = asyncio.create_task(relay_feed.follow('synthetic-remote', str(self.w.env),
            CHANNEL, event, lambda *_:None, trusted_relays=(ORIGIN,), author=PUB,
            status_kind='outlet'))
        try:
            await self.wait_for(lambda:any(s.sub for s in self.w.sockets), 'actual own NIP42 feed control')
            expected = self.w.event()
            self.w.sockets[0].hint(expected)
            await self.wait_for(lambda:expected in hints, 'actual feed signature/author/channel control')
            self.assertEqual(hints[0], {'type':'_reconnected'})
            self.assertFalse(self.w.posts)
            self.assertIsNone(self.db.remote_grant(self.target_id))
        finally:
            await self.cancel_task(task)
        self.assertTrue(self.w.sockets[0].closed)

    async def test_control_actual_service_manager_repeats_signed_proof_and_drains(self):
        service = await self.service()
        from hostd.agent_signed_reads import OwnAgentReader
        from hostd.remote_manager import RemoteManager
        record = service.records[APP]
        reader = OwnAgentReader(record, origin=service.config.relay_url,
            relay_pubkey=service.config.relay_pubkey, trusted_relays=service.config.trusted_relays,
            clock=lambda:self.w.now, http=service.http)
        bot = BotLarkCli(APP,self.w.cfg,self.w.data,base_env={'HOME':str(self.w.root)},
            http_pool=self.w,chat_id=CHAT)
        manager = RemoteManager(self.db,self.w.catalog_path,self.w.legacy_path,
            record=record,reader=reader,bot=bot,discovery_relay=service.relay,
            link_base=ORIGIN,clock=lambda:self.w.now)
        self.addAsyncCleanup(manager.close)
        active = await manager.reconcile(CHANNEL,expected_revision=0)
        self.assertEqual(active.status,'active')
        expected = self.w.event()
        drained = await manager.drain(CHANNEL,expected_revision=active.revision)
        self.assertEqual(drained.status,'complete')
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id,expected['id'],'message').status,'acked')
        self.assert_no_foreign()

    async def test_remote_only_refresh_actual_manager_activates_and_drains_without_binding(self):
        service = await self.service()
        shared = service.clients[APP]
        event = self.w.event()
        dispatch = await self.dispatcher(service)
        await dispatch.refresh()
        self.assertEqual(self.db.remote_grant(self.target_id).revision, 1)
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id, event['id'], 'message').status, 'acked')
        self.assertEqual(len(self.w.posts), 1)
        self.assertIs(service.clients[APP], shared)
        self.assertIsNone(shared.chat_id)
        self.assertIn(('/open-apis/im/v1/chats', None), self.w.bot_lanes)
        self.assertTrue(all(chat == CHAT for path, chat in self.w.bot_lanes if path.endswith('/scopes')))
        self.assert_no_foreign()

    async def test_pending_paused_and_active_local_channels_all_skip_before_remote_io(self):
        dispatch = await self.dispatcher()
        for status in ('pending', 'paused', 'active'):
            with self.subTest(status=status):
                self.local_binding(status)
                self.w.owner_calls.clear()
                self.w.wire_calls.clear()
                self.w.api_calls.clear()
                await dispatch.refresh()
                self.assertFalse(self.w.owner_calls or self.w.wire_calls or self.w.api_calls)
                self.assertIsNone(self.db.remote_grant(self.target_id))
                self.assertFalse(self.w.sockets)

    async def test_sql_revoke_during_discovery_await_prevents_manager_activation(self):
        dispatch = await self.dispatcher()
        self.w.on_owner = lambda:self.w.on_loop(lambda:self.db.conn.execute(
            "UPDATE agent SET status='paused' WHERE pubkey=?", (PUB,)))
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)
        self.assertFalse(self.w.wire_calls)

    async def test_local_binding_inserted_between_discovery_awaits_prevents_activation(self):
        dispatch = await self.dispatcher()
        self.w.on_owner = lambda:self.w.on_loop(self.local_binding)
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)
        self.assertFalse(self.w.wire_calls)

    async def test_local_binding_inserted_during_manager_scope_await_prevents_late_cas(self):
        dispatch = await self.dispatcher()
        def insert_after_approval_read():
            # Discovery also reads scopes. Keep the one-shot hook armed until
            # the actual manager has independently read the signed approval.
            if not self.w.wire_calls:
                self.w.on_scope = insert_after_approval_read
                return
            self.w.on_loop(self.local_binding)
        self.w.on_scope = insert_after_approval_read
        await dispatch.refresh()
        self.assertEqual([(r['binding_id'], r['channel_id'], r['chat_id'], r['status'])
                          for r in self.db.bindings()], [('local', CHANNEL, CHAT, 'pending')])
        self.assertTrue(self.w.wire_calls, 'actual manager independently read signed approval')
        self.assertIsNone(self.db.remote_grant(self.target_id), 'post-await local binding must hold final CAS')
        self.assertFalse(self.w.posts)

    async def test_restart_uses_current_sql_revision_with_actual_existing_grant(self):
        service = await self.service()
        proof = await self.w.consumer().verify(self.w.target)
        self.assertEqual(proof.status,'verified')
        seeded = self.db.activate_remote_grant(proof.authorization.evidence,expected_revision=0,now=NOW)
        self.w.event()
        dispatch = await self.dispatcher(service)
        await dispatch.refresh()
        self.assertEqual(self.db.remote_grant(self.target_id),seeded)
        self.assertEqual(len(self.w.posts),1, 'existing revision must be supplied to actual manager, not zero')
        self.assert_no_foreign()

    async def actual_manager(self):
        service = await self.service()
        from hostd.agent_signed_reads import OwnAgentReader
        from hostd.remote_manager import RemoteManager
        record = service.records[APP]
        reader = OwnAgentReader(record,origin=ORIGIN,relay_pubkey=fixture.PIN,
            trusted_relays=(ORIGIN,),clock=lambda:self.w.now,http=service.http)
        bot = BotLarkCli(APP,self.w.cfg,self.w.data,base_env={'HOME':str(self.w.root)},
            http_pool=self.w,chat_id=CHAT)
        manager = RemoteManager(self.db,self.w.catalog_path,self.w.legacy_path,
            record=record,reader=reader,bot=bot,discovery_relay=service.relay,
            link_base=ORIGIN,clock=lambda:self.w.now)
        self.addAsyncCleanup(manager.close)
        return manager

    async def test_reached_red_actual_manager_local_binding_during_scope_blocks_activation(self):
        # This bypasses the absent coordinator to reach the existing Manager's
        # exact authority gap. Actual signed approval and own-app scopes pass.
        manager = await self.actual_manager()
        for status in ('pending','paused','active'):
            with self.subTest(status=status):
                self.db.conn.execute('DELETE FROM binding')
                current = self.db.remote_grant(self.target_id)
                self.w.on_scope = lambda status=status:self.w.on_loop(lambda:self.local_binding(status))
                result = await manager.reconcile(CHANNEL,expected_revision=current.revision if current else 0)
                self.assertEqual(result.status,'pending','local channel appeared during real manager proof IO')
                self.assertEqual(self.db.remote_grant(self.target_id),current)
                self.assertFalse(self.w.posts)

    async def actual_runtime_grant(self):
        await self.service()
        from hostd.remote_runtime import RemoteRuntime
        proofs = self.w.consumer()
        proof = await proofs.verify(self.w.target)
        self.assertEqual(proof.status,'verified')
        grant = self.db.activate_remote_grant(proof.authorization.evidence,expected_revision=0,now=NOW)
        runtime = RemoteRuntime(self.db,record=self.w.actual_record,reader=self.w.reader,
            bot=self.w.bot,proofs=proofs,link_base=ORIGIN,clock=lambda:self.w.now)
        self.addCleanup(runtime.close)
        return runtime,grant

    async def test_reached_red_runtime_local_binding_during_proof_blocks_native_post(self):
        runtime,grant = await self.actual_runtime_grant()
        for status in ('pending','paused','active'):
            with self.subTest(status=status):
                self.db.conn.execute('DELETE FROM binding')
                event = self.w.event(text='SYNTHETIC_SCOPE_'+status)
                self.w.on_scope = lambda status=status:self.w.on_loop(lambda:self.local_binding(status))
                result = await runtime.deliver(self.w.target,event,target_id=grant.target_id,
                    revision=grant.revision,scope_hash=grant.scope_hash)
                self.assertEqual(result.status,'pending','changed local authority must block native POST')
                self.assertFalse(self.w.posts)
                self.assertIsNone(self.db.remote_delivery_by_source(self.target_id,event['id'],'message'))

    async def test_reached_red_runtime_local_binding_after_post_preserves_unknown_and_blocks_ack(self):
        runtime,grant = await self.actual_runtime_grant()
        for status in ('pending','paused','active'):
            with self.subTest(status=status):
                self.db.conn.execute('DELETE FROM binding')
                event = self.w.event(text='SYNTHETIC_AFTER_POST_'+status)
                self.w.after_post = lambda status=status:self.local_binding(status)
                result = await runtime.deliver(self.w.target,event,target_id=grant.target_id,
                    revision=grant.revision,scope_hash=grant.scope_hash)
                self.assertEqual(result.status,'pending','POST response cannot authorize ACK after local authority change')
                row = self.db.remote_delivery_by_source(self.target_id,event['id'],'message')
                self.assertEqual(row.status,'unknown')

    async def test_missing_signed_approval_never_promotes_discovery_to_grant(self):
        dispatch = await self.dispatcher()
        self.w.events.remove(self.w.approval)
        await dispatch.refresh()
        self.assertTrue(self.w.owner_calls)
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)
        self.assert_no_foreign()

    async def test_existing_suspended_sql_target_never_resumes_from_dirty_hint(self):
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        grant = self.db.remote_grant(self.target_id)
        self.db.suspend_remote_grant(self.target_id, expected_revision=grant.revision, now=NOW)
        self.w.event()
        dispatch.mark_dirty(PUB, CHANNEL)
        await dispatch.refresh()
        self.assertEqual(self.db.conn.execute('SELECT status FROM remote_target').fetchone()[0], 'suspended')
        self.assertFalse(self.w.posts)

    async def test_feed_payload_is_only_hint_own_signed_query_is_delivery_authority(self):
        dispatch = await self.dispatcher()
        await dispatch.refresh()
        task = asyncio.create_task(dispatch.run())
        self.addAsyncCleanup(self.cancel_task, task)
        await self.wait_for(lambda:any(s.sub for s in self.w.sockets), 'actual own NIP42 feed did not subscribe')
        hinted = self.w.event(text='SYNTHETIC_FEED_ONLY_PAYLOAD')
        self.w.events.remove(hinted)  # Valid own signature on wire is still only a wake hint.
        self.w.sockets[-1].hint(hinted)
        before = len(self.w.wire_calls)
        await self.wait_for(lambda:len(self.w.wire_calls)>before, 'feed hint failed to trigger signed query')
        self.assertIsNone(self.db.remote_delivery_by_source(self.target_id, hinted['id'], 'message'))
        self.assertFalse(self.w.posts)
        actual = self.w.event(text='SYNTHETIC_QUERY_AUTHORITY')
        self.w.sockets[-1].hint(hinted)
        await self.wait_for(lambda:len(self.w.posts)==1, 'hint did not drain independent signed query')
        await self.wait_for(lambda: (row := self.db.remote_delivery_by_source(
            self.target_id, actual['id'], 'message')) is not None and row.status == 'acked',
            'independent query delivery did not complete actual receipt proof and SQL ACK')
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id, actual['id'], 'message').status, 'acked')

    async def test_stale_catalog_allowlist_holds_old_lane_and_recreates_from_fresh_record(self):
        service = await self.service()
        dispatch = await self.dispatcher(service)
        await dispatch.refresh()
        self.w.owned(self.w.env, self.w.env.read_text().replace('BUZZ_ACP_CHANNELS='+CHANNEL,
            'BUZZ_ACP_CHANNELS='))
        self.w.event()
        await dispatch.refresh()
        self.assertFalse(self.w.posts)
        self.w.owned(self.w.env, self.w.env.read_text().replace('BUZZ_ACP_CHANNELS=',
            'BUZZ_ACP_CHANNELS='+CHANNEL))
        await dispatch.refresh()
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual(self.db.remote_grant(self.target_id).revision, 1)
        self.assertIsNone(service.clients[APP].chat_id)

    async def test_wrong_own_origin_and_legacy_overlap_hold_without_foreign_fallback(self):
        dispatch = await self.dispatcher()
        self.w.owned(self.w.env, self.w.env.read_text().replace(ORIGIN, 'https://foreign.test'))
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.w.owned(self.w.env, self.w.env.read_text().replace('https://foreign.test', ORIGIN))
        self.w.owned(self.w.legacy_path, json.dumps(self.w.doc))
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)
        self.assert_no_foreign()

    async def test_foreign_owner_and_notown_records_excluded_from_current_catalog(self):
        dispatch = await self.dispatcher()
        self.w.owned(self.w.catalog_path,json.dumps(dict(self.w.doc,owner_pubkey='a'*64)))
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.w.owned(self.w.catalog_path,json.dumps(self.w.doc))
        self.w.owned(self.w.cfg/'config.json',json.dumps({'apps':[{'appId':'cli_foreign','name':'foreign'}]}))
        current = agent_catalog.load(self.w.catalog_path,legacy_join_path=self.w.legacy_path)
        self.assertNotEqual(current.records[0].status,'own_bot_verified')
        await dispatch.refresh()
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)
        self.assert_no_foreign()

    async def test_stop_gate_reaps_blocked_io_before_close_without_closing_borrowed_resources(self):
        dispatch = await self.dispatcher()
        self.w.block_scope = True
        refresh = asyncio.create_task(dispatch.refresh())
        self.addAsyncCleanup(self.cancel_task, refresh)
        try:
            await self.wait_for(self.w.scope_entered.is_set, 'actual manager native scopes boundary not reached')
            dispatch.stop()
            closing = asyncio.create_task(dispatch.close())
            await asyncio.sleep(.02)
            self.assertFalse(closing.done(), 'close must wait for dispatched native IO reap')
            self.assertIsNone(self.db.remote_grant(self.target_id))
            dispatch.mark_dirty(PUB, CHANNEL)
        finally:
            self.w.scope_release.set()
        await closing
        await asyncio.gather(refresh, return_exceptions=True)
        self.assertIsNone(self.db.remote_grant(self.target_id))
        count = len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
        await dispatch.refresh()
        self.assertEqual(count, len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls))
        self.assert_no_foreign()

    async def test_repeated_dirty_hints_are_coalesced_and_no_second_post(self):
        dispatch = await self.dispatcher()
        event = self.w.event()
        await dispatch.refresh()
        for _ in range(1000):
            dispatch.mark_dirty(PUB, CHANNEL)
            dispatch.mark_dirty('a'*64, CHANNEL)
        await dispatch.refresh()
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id,event['id'],'message').status,'acked')
        self.assertLessEqual(len(self.w.sockets), 1, 'one own feed per eligible pair, no foreign hint lane')
        self.assert_no_foreign()

    async def cancel_task(self, task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def test_daemon_main_wires_remote_start_stop_and_close_before_shared_teardown(self):
        # Structural root-entry acceptance complements actual coordinator tests;
        # no high-level factory/main mock can manufacture startup authority.
        module = importlib.import_module('hostd.__main__')
        tree = ast.parse(Path(module.__file__).read_text())
        host = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Hostd')
        methods = {n.name:n for n in host.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        startup = ast.unparse(methods['start_onboarding'])
        shutdown = ast.unparse(methods['_shutdown'])
        combined = '\n'.join(ast.unparse(n) for n in host.body)
        self.assertTrue('RemoteDispatch' in combined, 'daemon never constructs remote coordinator: feature RED')
        self.assertTrue('remote_dispatch' in startup, 'actual onboarding startup must adopt coordinator')
        self.assertTrue('remote_dispatch.stop()' in shutdown)
        self.assertTrue('await self.remote_dispatch.close()' in shutdown)
        self.assertLess(shutdown.index('remote_dispatch.stop()'), shutdown.index('task.cancel()'))
        self.assertLess(shutdown.index('await self.remote_dispatch.close()'), shutdown.index('await self.close_onboarding()'))
        self.assertLess(shutdown.index('await self.close_onboarding()'), shutdown.index('self.http_pool.close()'))

    def public_config(self, value='https://buzz-ui.example.test'):
        self.assertIn('remote_link_base', RuntimeConfig.__dataclass_fields__,
            'protected RuntimeConfig cannot express an independent public Buzz origin: feature RED')
        return dataclasses.replace(self.config, remote_link_base=value)

    async def test_public_base_explicit_protected_config_load_keeps_private_transport_independent(self):
        config = self.public_config()
        path = self.w.root/'public-startup.json'
        value = dataclasses.asdict(config)
        value['trusted_relays'] = list(config.trusted_relays)
        self.w.owned(path,json.dumps(value))
        loaded = RuntimeConfig.load(path)
        self.assertEqual(loaded,config)
        self.assertEqual(loaded.relay_url,ORIGIN)
        self.assertEqual(loaded.remote_link_base,'https://buzz-ui.example.test')
        self.assertFalse(self.w.owner_calls or self.w.wire_calls or self.w.api_calls)

    def test_public_base_invalid_explicit_origin_rejected_before_any_adapter_io(self):
        self.public_config()  # Baseline missing field is a reached feature RED.
        from hostd.onboarding_runtime import RuntimeErrorNotice
        for base in ('http://buzz-ui.example.test','https://user@buzz-ui.example.test',
                     'https://buzz-ui.example.test/path','https://buzz-ui.example.test?e=bad',
                     'https://buzz-ui.example.test#bad',' https://buzz-ui.example.test',
                     'https://buzz-ui.example.test:0',None,False):
            with self.subTest(base=base):
                with self.assertRaises(RuntimeErrorNotice):
                    self.public_config(base)
        self.assertFalse(self.w.owner_calls or self.w.wire_calls or self.w.api_calls)

    async def test_public_base_missing_holds_remote_before_io_preserves_actual_zero_binding_startup(self):
        # RuntimeConfig's optional default keeps legacy local startup valid;
        # independent remote authority needs an explicitly protected publicbase.
        if 'remote_link_base' in RuntimeConfig.__dataclass_fields__:
            self.config = dataclasses.replace(self.config,remote_link_base='')
        service = await self.service()
        dispatch = await self.dispatcher(service)
        before = len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
        await dispatch.refresh()
        self.assertEqual(before,len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls),
            'missing public base must not guess the signed transport origin')
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertEqual(service.eligible_apps,(APP,))
        self.assertEqual(self.db.bindings(),[])
        self.assert_no_foreign()

    async def test_public_base_actual_factory_card_footer_uses_public_origin_signed_reads_use_private_origin(self):
        self.config = self.public_config()
        service = await self.service()
        event = self.w.event()
        dispatch = await self.dispatcher(service)
        await dispatch.refresh()
        self.assertEqual(self.db.remote_delivery_by_source(self.target_id,event['id'],'message').status,'acked')
        content = self.w.posts[0][2]['content']
        self.assertIn('https://buzz-ui.example.test',content)
        self.assertNotIn(ORIGIN,content)
        self.assertTrue(self.w.owner_calls and self.w.wire_calls)
        self.assertTrue(all(call[0]==ORIGIN+'/query' for call in self.w.owner_calls+self.w.wire_calls))
        self.assertIsNone(service.clients[APP].chat_id)
        self.assert_no_foreign()

    async def test_root_missing_public_base_preserves_actual_zero_binding_startup_without_remote(self):
        hd = importlib.import_module('hostd.__main__')
        config = dataclasses.replace(self.config, remote_link_base='')
        host = hd.Hostd(hd.registry.Registry(), self.w.root / 'root-status.json',
            state_db=self.w.root / 'metadata' / 'root.db', onboarding_config=config)
        self.w.loop = asyncio.get_running_loop()
        try:
            # Only lower HTTP/clock defaults change; the real factory and every
            # protected catalog, own-key, signed and SQL check execute unchanged.
            with mock.patch.dict(OnboardingRuntime.create.__func__.__kwdefaults__,
                    {'http': self.w.transport, 'clock': lambda: self.w.now}):
                await host.start_onboarding()
            self.assertIsInstance(host.onboarding, OnboardingRuntime)
            self.assertIsInstance(host.runtime_store, store.Store)
            self.assertEqual(host.runtime_store.bindings(), [])
            self.assertEqual(host.onboarding.eligible_apps, (APP,))
            self.assertIsNone(host.remote_dispatch,
                'missing public base must leave actual root remote service disabled')
            self.assertFalse(self.w.posts)
            self.assertFalse(self.w.cli_calls)
        finally:
            if host.remote_dispatch is not None:
                await host.remote_dispatch.close()
            await host.close_onboarding()
            host.http_pool.close()
            host.scheduler.close()

    async def test_root_none_remote_spawns_actual_onboarding_and_claims_then_reaps(self):
        hd = importlib.import_module('hostd.__main__')
        config = dataclasses.replace(self.config, remote_link_base='')
        doc = json.loads(self.w.catalog_path.read_text())
        doc['agents'] = []
        self.w.owned(self.w.catalog_path, json.dumps(doc))
        host = hd.Hostd(hd.registry.Registry(), self.w.root / 'root-status.json',
            state_db=self.w.root / 'metadata' / 'root.db', onboarding_config=config)
        db = store.Store(host.store_path)
        service = await OnboardingRuntime.create(config, db, registrar=None,
            scheduler=host.scheduler, http_pool=host.http_pool, http=self.w.transport,
            base_env={'HOME': str(self.w.root)}, clock=lambda: self.w.now)
        self.assertEqual(service.eligible_apps, ())
        self.assertEqual(db.bindings(), [])
        host.onboarding, host.runtime_store = service, db
        self.assertIsNone(host.remote_dispatch)
        task = asyncio.create_task(host.main())
        try:
            await self.wait_for(lambda: task.done() or len(host._tasks) >= 3,
                'actual root did not start onboarding and claims')
            self.assertFalse(task.done(),
                'actual root must run with onboarding enabled and remote service absent')
            self.assertEqual(len(host._tasks), 3)
            self.assertEqual(host.app_tasks, {})
            self.assertEqual(host.workers, {})
            self.assertFalse(self.w.posts)
            self.assertFalse(self.w.cli_calls)
        finally:
            await self.cancel_task(task)
        self.assertTrue(service._closed)
        self.assertIsNone(host.runtime_store)
        self.assertIsNone(host.onboarding)
        self.assertTrue(host.scheduler.closed)
        self.assertTrue(all(t.done() for t in host._tasks))


if __name__ == '__main__':
    unittest.main()
