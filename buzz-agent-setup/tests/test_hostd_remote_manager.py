"""Internal manager tests: real SQL/catalog/signed discovery/proof/runtime.

Only synthetic relay/bot HTTP and the labelled portable AES primitive are
replaced. No verified high-level domain object is mocked or live action taken.
"""
import asyncio
import base64
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_runtime as runtime_fixture
import test_hostd_remote_mapping as signed_fixture
from hostd import store
from hostd.bot_clients import BotLarkCli
from hostd.agent_signed_reads import OwnAgentReader
from hostd.join_effects import Nip98Relay
from hostd.remote_target import RemoteTargetResolver
from hostd.remote_runtime import RemoteRuntime
import buzz_feishu_group_sync as gs
import recovery_authority as authority

CHANNEL, CHAT, APP, ORIGIN, NOW = (runtime_fixture.CHANNEL, runtime_fixture.CHAT,
    runtime_fixture.APP, runtime_fixture.ORIGIN, runtime_fixture.NOW)
PUB, OWNER = runtime_fixture.PUB, runtime_fixture.OWNER
PIN = runtime_fixture.proofs_fixture.PIN


class World(runtime_fixture.World):
    def __init__(self, case):
        super().__init__(case)
        self.owner_calls = []
        self.on_owner = self.on_scope = None
        self.block_scope = False
        self.scope_entered, self.scope_release = threading.Event(), threading.Event()
        self.chats_incomplete = False
        self.owner_env = self.root / 'owner.env'
        self.owned(self.owner_env, 'BUZZ_PRIVATE_KEY=' + signed_fixture.OWNER_KEY + '\n')
        self.discovery = Nip98Relay(ORIGIN, self.owner_env, PIN,
            trusted_relays=(ORIGIN,), clock=lambda: self.now, http=self.owner_http)

    def owner_http(self, url, headers, timeout, *, body=None):
        self.owner_calls.append((url, headers, body))
        self.assertEqual(url, ORIGIN + '/query')
        signed = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
        self.assertTrue(gs._nip01_event_verified(signed))
        self.assertEqual(signed['pubkey'], OWNER)
        self.assertNotIn('x-auth-tag', headers, 'owner discovery never masquerades as own-agent OA')
        authority.exact_tag(signed, 'u', url)
        authority.exact_tag(signed, 'method', 'POST')
        authority.exact_tag(signed, 'payload', hashlib.sha256(body).hexdigest())
        rows = []
        for filt in json.loads(body):
            for event in self.directory + self.events:
                if 'kinds' in filt and event['kind'] not in filt['kinds']: continue
                if 'authors' in filt and event['pubkey'] not in filt['authors']: continue
                if any(k.startswith('#') and not any(len(t)>1 and t[0]==k[1:] and t[1] in vals
                       for t in event['tags']) for k, vals in filt.items()): continue
                if event not in rows: rows.append(event)
        if self.on_owner:
            action, self.on_owner = self.on_owner, None
            action()
        rows = json.loads(json.dumps(rows))
        if self.bad_sig and rows: rows[0]['sig'] = '0' * 128
        return self.status, json.dumps(rows).encode()

    def request(self, app, config, data_dir, method, path, **kwargs):
        if path == '/open-apis/im/v1/chats':
            self.assertEqual((app, Path(config), Path(data_dir)), (APP, self.cfg, self.data))
            self.assertEqual((method, kwargs['chat_id']), ('GET', CHAT))
            self.api_calls.append((method, path, kwargs.get('params'), kwargs.get('data')))
            return {'ok': True, 'identity': 'bot', 'data': {'items': [{'chat_id': CHAT}],
                'has_more': self.chats_incomplete, 'page_token': 'same'}}
        if path == '/open-apis/application/v6/scopes':
            if self.on_scope:
                action, self.on_scope = self.on_scope, None
                action()
            if self.block_scope:
                self.scope_entered.set()
                if not self.scope_release.wait(5):
                    raise ValueError('synthetic scope transport did not release')
        return super().request(app, config, data_dir, method, path, **kwargs)


class RemoteManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World(self)
        self.path = self.w.root / 'metadata' / 'manager.db'
        self.db = store.Store(self.path)
        self.addCleanup(lambda: self.db.close())
        self.addCleanup(self.w.scope_release.set)
        self.w.db = self.db
        self.db.register_agent(PUB, owner_pubkey=OWNER, app_id=APP,
            config_path=str(self.w.env), now=NOW)
        self.target_id = store.Store._remote_digest([PUB, CHANNEL])

    def manager(self, **changes):
        self.w.loop = asyncio.get_running_loop()
        self.assertIsNotNone(importlib.util.find_spec('hostd.remote_manager'),
                             'missing internal remote manager: tests-first RED')
        args = dict(record=self.w.actual_record, reader=self.w.reader, bot=self.w.bot,
            discovery_relay=self.w.discovery, link_base=ORIGIN, clock=lambda: self.w.now)
        args.update(changes)
        return importlib.import_module('hostd.remote_manager').RemoteManager(
            self.db, self.w.catalog_path, self.w.legacy_path, **args)

    def pending(self, result):
        self.assertEqual(result.status, 'pending')
        self.assertIn('怎么解决', result.notice)
        self.assertIn('复制给 AI', result.notice)
        self.assertFalse(result.readback()['live_verified'])
        for canary in (signed_fixture.KEY, signed_fixture.OWNER_KEY, 'SYNTHETIC_APP_SECRET',
                       'SYNTHETIC_PRIVATE_PROMPT', 'SYNTHETIC_BODY_DO_NOT_CACHE', 'Traceback'):
            self.assertNotIn(canary, json.dumps(result.readback()))

    def revoke(self, field='status', value='paused'):
        self.db.conn.execute(f'UPDATE agent SET {field}=? WHERE pubkey=?', (value, PUB))

    def no_foreign(self):
        self.assertEqual(self.db.bindings(), [])
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM app_profile').fetchone()[0], 0)
        self.assertFalse(self.w.cli_calls)
        self.assertFalse([p for p in self.w.root.rglob('config.json') if p != self.w.cfg / 'config.json'])

    def reopen(self):
        self.db.close()
        self.db = store.Store(self.path)
        self.w.db = self.db

    async def wait_scope(self):
        # A deterministic fixture rendezvous, not a p95/production latency gate.
        for _ in range(1200):
            if self.w.scope_entered.is_set(): return
            await asyncio.sleep(.025)
        self.fail('actual native scope IO boundary not reached')

    async def real_proof(self):
        self.w.loop = asyncio.get_running_loop()
        result = await self.w.consumer().verify(self.w.target)
        self.assertEqual(result.status, 'verified')
        return result.authorization.evidence

    async def test_fixture_control_actual_resolver_consumer_and_runtime_create_verified_receipt(self):
        self.w.loop = asyncio.get_running_loop()
        def guard(record, channel):
            row = self.db.conn.execute('SELECT status FROM agent WHERE pubkey=?', (record.pubkey,)).fetchone()
            return record is self.w.actual_record and channel == CHANNEL and row['status'] == 'active'
        resolver = RemoteTargetResolver(self.w.catalog_path, self.w.legacy_path,
            relay=self.w.discovery, clients={APP: self.w.bot}, authorize=guard, clock=lambda: self.w.now)
        discovery = await resolver.resolve(self.w.actual_record, CHANNEL)
        self.assertEqual(discovery.status, 'verified', 'actual signed resolver fixture must work before bridge RED')
        self.assertIsNone(self.db.remote_grant(self.target_id), 'discovery is not SQL approval')
        proofs = self.w.consumer()
        verified = await proofs.verify(discovery.target)
        self.assertEqual(verified.status, 'verified')
        grant = self.db.activate_remote_grant(verified.authorization.evidence, expected_revision=0, now=NOW)
        runtime = RemoteRuntime(self.db, record=self.w.actual_record, reader=self.w.reader,
            bot=self.w.bot, proofs=proofs, link_base=ORIGIN, clock=lambda: self.w.now)
        event = self.w.event()
        result = await runtime.deliver(discovery.target, event, target_id=grant.target_id,
            revision=grant.revision, scope_hash=grant.scope_hash)
        self.assertEqual(result.status, 'acked')
        self.assertEqual(len(self.w.posts), 1)
        self.no_foreign()

    async def test_actual_approved_target_activates_metadata_then_accepted_runtime_sends_once(self):
        m, event = self.manager(), self.w.event()
        active = await m.reconcile(CHANNEL, expected_revision=0)
        self.assertEqual(active.status, 'active')
        self.assertEqual((active.target_id, active.revision), (self.target_id, 1))
        self.assertFalse(active.readback()['live_verified'])
        self.assertFalse(self.w.posts)
        grant = self.db.remote_grant(self.target_id)
        self.assertEqual(grant.evidence.approval_id, self.w.approval['id'])
        self.assertEqual((await m.deliver(CHANNEL, event, expected_revision=1)).status, 'acked')
        self.assertEqual((await m.deliver(CHANNEL, event, expected_revision=1)).status, 'acked')
        self.assertEqual(len(self.w.posts), 1)
        self.no_foreign()
        sql = '\n'.join(self.db.conn.iterdump())
        self.assertNotIn(event['content'], sql)
        self.assertNotIn('SYNTHETIC_APP_SECRET', sql)
        self.assertNotIn(signed_fixture.KEY, sql)

    async def test_discovery_without_actual_signed_approval_never_creates_grant(self):
        self.w.events.remove(self.w.approval)
        self.pending(await self.manager().reconcile(CHANNEL, expected_revision=0))
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertTrue(self.w.owner_calls, 'actual discovery succeeded separately from approval')
        self.assertFalse(self.w.posts)

    async def test_current_sql_agent_status_app_owner_env_and_missing_row_gate_before_io(self):
        m = self.manager()
        for field, value, old in (('status','paused','active'), ('app_id','cli_other',APP),
            ('owner_pubkey','a'*64,OWNER), ('config_path',str(self.w.root/'wrong.env'),str(self.w.env))):
            with self.subTest(field=field):
                self.revoke(field, value)
                count = len(self.w.owner_calls) + len(self.w.wire_calls) + len(self.w.api_calls)
                self.pending(await m.reconcile(CHANNEL, expected_revision=0))
                self.assertEqual(len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls), count)
                self.revoke(field, old)
        self.db.conn.execute('DELETE FROM agent WHERE pubkey=?', (PUB,))
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertFalse(self.w.owner_calls)

    async def test_explicit_revision_and_allowed_channel_required_before_io(self):
        m = self.manager()
        for channel, revision in ((CHANNEL,1), (CHANNEL,True), (CHANNEL,-1), ('not-channel',0),
                                   ('00000000-0000-0000-0000-000000000002',0)):
            with self.subTest(channel=channel, revision=revision):
                self.pending(await m.reconcile(channel, expected_revision=revision))
        self.assertFalse(self.w.owner_calls)
        self.assertFalse(self.w.wire_calls)
        self.assertFalse(self.w.api_calls)

    async def test_agent_revoked_during_discovery_await_stops_further_io_and_activation(self):
        m = self.manager()
        self.w.on_owner = lambda: self.w.on_loop(self.revoke)
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertEqual(len(self.w.owner_calls), 1)
        self.assertFalse(self.w.wire_calls)
        self.assertFalse(self.w.api_calls)
        self.assertIsNone(self.db.remote_grant(self.target_id))

    async def test_agent_revoked_during_actual_consumer_scope_await_never_activates(self):
        m = self.manager()
        self.w.on_scope = lambda: self.w.on_loop(self.revoke)
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertTrue(self.w.wire_calls, 'actual own reader independently checked approval')
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)

    async def test_current_revision_changes_during_proof_await_do_not_adopt_competing_grant(self):
        evidence = await self.real_proof()
        m = self.manager()
        self.w.on_scope = lambda: self.w.on_loop(lambda: self.db.activate_remote_grant(
            evidence, expected_revision=0, now=self.w.now))
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertEqual(self.db.remote_grant(self.target_id).revision, 1)
        self.assertFalse(self.w.posts)

    async def test_legacy_exclusion_profile_and_env_change_during_read_hold_activation(self):
        m = self.manager()
        self.w.on_scope = lambda: self.w.owned(self.w.legacy_path, json.dumps(self.w.doc))
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.w.owned(self.w.legacy_path, json.dumps(dict(self.w.doc, agents=[])))
        self.w.on_scope = lambda: self.w.owned(self.w.cfg/'config.json', json.dumps({'apps':[{'appId':'cli_other','name':'local'}]}))
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.w.owned(self.w.cfg/'config.json', json.dumps({'apps':[{'appId':APP,'name':'local'}]}))
        self.w.on_scope = lambda: self.w.owned(self.w.env, self.w.env.read_text()+'# changed\n')
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)

    async def test_bad_signed_policy_or_incomplete_native_scopes_roster_and_chat_pages_pending(self):
        m = self.manager()
        self.w.chats_incomplete = True
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.w.chats_incomplete = False
        self.w.members_incomplete = True
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.w.members_incomplete = False
        self.w.bad_sig = True
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.w.bad_sig = False
        self.w.scopes.clear()
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertIsNone(self.db.remote_grant(self.target_id))
        self.assertFalse(self.w.posts)

    async def test_wrong_bot_lane_or_reader_origin_pin_and_path_cannot_activate(self):
        wrong = BotLarkCli(APP, self.w.cfg, self.w.data, base_env={'HOME':str(self.w.root)},
            http_pool=self.w, chat_id='oc_other')
        m = self.manager(bot=wrong)
        self.pending(await m.reconcile(CHANNEL, expected_revision=0))
        self.assertEqual(self.w.bot.chat_id, CHAT, 'manager cannot mutate the shared own bot lane')
        self.assertFalse(self.w.posts)
        self.assertIsNone(self.db.remote_grant(self.target_id))

    async def test_factory_cannot_accept_caller_verified_evidence_or_wrong_own_reader(self):
        evidence = await self.real_proof()
        wrong_reader = OwnAgentReader(self.w.actual_record, origin=ORIGIN,relay_pubkey='a'*64,
            trusted_relays=(ORIGIN,),clock=lambda:self.w.now,http=self.w.http)
        for changes in ({'reader':wrong_reader}, {'reader':evidence}, {'discovery_relay':evidence},
                        {'record':dataclasses.replace(self.w.actual_record, app_id='cli_other')}):
            with self.subTest(field=next(iter(changes))):
                before = len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
                with self.assertRaises((TypeError, ValueError)):
                    self.manager(**changes)
                self.assertEqual(before, len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls))
        self.assertIsNone(self.db.remote_grant(self.target_id))

    async def test_expired_current_proof_same_heartbeat_renews_original_scope_and_revision(self):
        m = self.manager()
        first = await m.reconcile(CHANNEL, expected_revision=0)
        self.assertEqual(first.status, 'active')
        old = self.db.remote_grant(self.target_id)
        self.w.now += 31
        self.w.claims[0]['heartbeat'] = self.w.now
        self.w.directory = self.w.metadata()
        self.assertLess(self.db.remote_proof(self.target_id).evidence.valid_until, self.w.now)
        fresh = await m.reconcile(CHANNEL, expected_revision=first.revision)
        self.assertEqual((fresh.status,fresh.revision,fresh.scope_hash), ('active',first.revision,first.scope_hash))
        self.assertEqual(self.db.remote_grant(self.target_id), old)
        self.assertGreater(self.db.remote_proof(self.target_id).evidence.valid_until, self.w.now)
        self.assertFalse(self.w.posts)

    async def test_suspended_existing_target_is_held_not_implicitly_resumed(self):
        m = self.manager()
        active = await m.reconcile(CHANNEL, expected_revision=0)
        self.db.suspend_remote_grant(self.target_id, expected_revision=active.revision, now=NOW)
        before = len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
        self.pending(await m.reconcile(CHANNEL, expected_revision=active.revision))
        self.assertEqual(before, len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls))
        self.assertEqual(self.db.conn.execute('SELECT status FROM remote_target').fetchone()[0], 'suspended')

    async def test_reopen_unknown_uses_original_own_receipt_only_and_never_second_post(self):
        m, event = self.manager(), self.w.event()
        active = await m.reconcile(CHANNEL, expected_revision=0)
        self.w.after_post = self.revoke
        self.pending(await m.deliver(CHANNEL, event, expected_revision=active.revision))
        row = self.db.remote_delivery_by_source(self.target_id,event['id'],'message')
        self.assertEqual(row.status, 'unknown')
        self.assertEqual(len(self.w.posts), 1)
        self.revoke(value='active')
        self.reopen()
        restored = self.manager()
        self.assertEqual((await restored.deliver(CHANNEL,event,expected_revision=active.revision)).status,'acked')
        current = self.db.remote_delivery(row.id)
        self.assertEqual((current.id,current.revision,current.scope_hash,current.content_hash),
                         (row.id,row.revision,row.scope_hash,row.content_hash))
        self.assertEqual(len(self.w.posts), 1)
        self.no_foreign()

    async def test_unknown_absence_after_reopen_never_dispatches_and_preserves_pin(self):
        m, event = self.manager(), self.w.event()
        active = await m.reconcile(CHANNEL,expected_revision=0)
        self.w.response_only = True
        self.pending(await m.deliver(CHANNEL,event,expected_revision=active.revision))
        row = self.db.remote_delivery_by_source(self.target_id,event['id'],'message')
        self.assertEqual(row.status,'unknown')
        self.reopen()
        self.pending(await self.manager().deliver(CHANNEL,event,expected_revision=active.revision))
        self.assertEqual(self.db.remote_delivery(row.id),row)
        self.assertEqual(len(self.w.posts),1)

    async def test_real_authority_change_new_revision_cannot_repin_original_unknown(self):
        m, event = self.manager(), self.w.event()
        first = await m.reconcile(CHANNEL,expected_revision=0)
        self.w.response_only = True
        self.pending(await m.deliver(CHANNEL,event,expected_revision=first.revision))
        original = self.db.remote_delivery_by_source(self.target_id,event['id'],'message')
        self.w.now += 1
        self.w.claims[0]['claimed_at'] += 1
        self.w.claims[0]['heartbeat'] = self.w.now
        self.w.directory = self.w.metadata()
        body = json.loads(self.w.approval['content']);body['claimed_at'] += 1
        raw = json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        replacement = gs.sign_event(signed_fixture.MIRROR_KEY,30078,
            [['t','hostd-card-approval-v1'],['h',CHANNEL],['p',PUB],['d','hostd-card-approval-v1:'+digest]],raw,self.w.now)
        self.w.events.remove(self.w.approval);self.w.events.append(replacement);self.w.approval=replacement
        changed = await m.reconcile(CHANNEL,expected_revision=first.revision)
        self.assertEqual(changed.status,'active')
        self.assertGreater(changed.revision,first.revision)
        self.pending(await m.deliver(CHANNEL,event,expected_revision=changed.revision))
        self.assertEqual(self.db.remote_delivery(original.id),original)
        self.assertEqual(len(self.w.posts),1)

    async def test_close_reaps_blocked_consumer_and_prevents_late_activation_with_store_open(self):
        m = self.manager();self.w.block_scope=True
        task = asyncio.create_task(m.reconcile(CHANNEL,expected_revision=0))
        try:
            await self.wait_scope()
            close = asyncio.create_task(m.close())
            await asyncio.sleep(.1)
            self.assertFalse(close.done(), 'close must hold until actual IO is reaped')
            self.assertIsNone(self.db.remote_grant(self.target_id))
            self.w.scope_release.set()
            await asyncio.wait_for(close,20)
            self.pending(await task)
            count=len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
            self.pending(await m.reconcile(CHANNEL,expected_revision=0))
            self.assertEqual(count,len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls))
            self.assertEqual(self.db.conn.execute('SELECT count(*) FROM agent').fetchone()[0],1)
            self.assertIsNone(self.db.remote_grant(self.target_id))
            self.assertFalse(self.w.posts)
        finally:
            self.w.scope_release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_cancelled_blocked_consumer_reaps_without_activation_or_followup_io(self):
        m = self.manager();self.w.block_scope=True
        task = asyncio.create_task(m.reconcile(CHANNEL,expected_revision=0))
        try:
            await self.wait_scope();task.cancel();task.cancel()
            await asyncio.sleep(.1)
            self.assertFalse(task.done(), 'caller cancellation cannot abandon actual transport')
            self.w.scope_release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            count=len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls)
            await asyncio.sleep(.05)
            self.assertEqual(count,len(self.w.owner_calls)+len(self.w.wire_calls)+len(self.w.api_calls))
            self.assertIsNone(self.db.remote_grant(self.target_id))
            self.assertFalse(self.w.posts)
            await m.close()
        finally:
            self.w.scope_release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_unsupported_backlog_scan_stays_pending_without_cursor_advance(self):
        m = self.manager()
        first = await m.reconcile(CHANNEL,expected_revision=0)
        self.w.event(kind=7,text='+',tags=[['e','a'*64]])
        result = await m.drain(CHANNEL,expected_revision=first.revision)
        self.pending(result)
        self.assertEqual(self.db.remote_replay_since(self.target_id),0)
        self.assertIsNone(self.db.conn.execute('SELECT position FROM remote_cursor WHERE target_id=?',
                                             (self.target_id,)).fetchone())
        self.assertFalse(self.w.posts)
        self.no_foreign()


if __name__ == '__main__':unittest.main()
