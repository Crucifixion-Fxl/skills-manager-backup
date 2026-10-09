"""Root-only remote authority bridge: actual SQL, catalog, signatures and adapters.

Only native bot/relay HTTP and the explicitly labelled portable AES primitive
are replaced. No caller-supplied verified DTO grants authority, foreign writer
or real network/config/unit is created. The production bridge is absent at RED.
"""
import asyncio
import concurrent.futures
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_proofs as proofs_fixture
import test_hostd_remote_mapping as signed_fixture
import buzz_feishu_group_sync as gs
from hostd import store
from hostd.bot_clients import BotCliError
from hostd.delivery_mapping import message_card
from hostd.remote_mapping import RemoteMappingContext
from hostd.remote_outlet import RemoteOutlet
from hostd.remote_proofs import RemoteProofs

CHANNEL, CHAT, APP, ORIGIN, NOW = (proofs_fixture.CHANNEL, proofs_fixture.CHAT,
    proofs_fixture.APP, proofs_fixture.ORIGIN, proofs_fixture.NOW)
PUB, OWNER = proofs_fixture.PUB, proofs_fixture.OWNER


class World(proofs_fixture.World):
    """Actual consumer/outlet low HTTP seam, enforcing native own-app receipts."""

    def __init__(self, case):
        super().__init__(case)
        self.after_post = None
        self.post_rows = []

    def on_loop(self, action):
        """IO hooks mutate real SQLite only on the owning event loop."""
        result = concurrent.futures.Future()
        def apply():
            try:
                result.set_result(action())
            except BaseException as exc:
                result.set_exception(exc)
        self.loop.call_soon_threadsafe(apply)
        return result.result(timeout=3)

    def request(self, app, config, data_dir, method, path, *, params=None,
                data=None, chat_id=None, priority=None):
        if path == '/open-apis/application/v6/scopes' or path.endswith('/members/list'):
            return super().request(app, config, data_dir, method, path,
                params=params, data=data, chat_id=chat_id, priority=priority)
        self.assertEqual((app, Path(config), Path(data_dir)), (APP, self.cfg, self.data))
        self.assertEqual(chat_id, CHAT)
        self.api_calls.append((method, path, params, data))
        if method == 'POST':
            source = re.search(r'[?&]e=([0-9a-f]{64})', data['content']).group(1)
            def observe():
                row = self.db.conn.execute('SELECT * FROM remote_delivery WHERE source_id=?', (source,)).fetchone()
                return dict(row) if row else None, self.db.conn.in_transaction
            row, transaction = self.on_loop(observe)
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'unknown', 'actual SQL must hold uncertainty before HTTP')
            self.assertFalse(transaction, 'the owning-loop transaction commits before physical POST')
            self.post_rows.append(row)
            self.posts.append((path, params, dict(data)))
            self.assertEqual(data['uuid'], 'hr-' + row['id'][:40])
            self.assertEqual(data['msg_type'], 'interactive')
            parent = None
            if path.endswith('/reply'):
                parent = path.split('/')[-2]
                self.assertTrue(data['reply_in_thread'])
                self.assertIn(parent, self.messages)
            else:
                self.assertEqual(path, '/open-apis/im/v1/messages')
                self.assertEqual(params, {'receive_id_type': 'chat_id'})
                self.assertEqual(data['receive_id'], CHAT)
            self.send_entered.set()
            if self.block_send and not self.send_release.wait(3):
                raise BotCliError('synthetic blocked IO', 500, 'network', definite=False)
            mid = 'om_sent' + str(len(self.posts))
            if not self.response_only:
                self.messages[mid] = dict(message_id=mid, chat_id=CHAT, root_id=parent or mid,
                    thread_id='omt_' + (parent or mid), create_time=str(self.now * 1000),
                    msg_type='interactive', sender={'sender_type': 'app', 'id_type': 'app_id', 'id': APP},
                    body={'content': data['content']})
            if self.after_post:
                action, self.after_post = self.after_post, None
                self.on_loop(action)
            if self.lose_response:
                raise BotCliError('synthetic response unknown', 500, 'network', definite=False)
            result = {'message_id': mid}
        elif method == 'GET' and path == '/open-apis/im/v1/messages':
            params = params or {}
            rows = list(self.messages.values())
            if params.get('container_id_type') == 'thread':
                rows = [r for r in rows if r.get('thread_id') == params.get('container_id')
                        or r.get('root_id') == params.get('container_id', '').removeprefix('omt_')]
            elif params.get('only_thread_root_messages'):
                rows = [r for r in rows if r['chat_id'] == params.get('container_id')
                        and r.get('root_id', r['message_id']) == r['message_id']]
            result = {'items': rows + self.history_extra, 'has_more': self.incomplete, 'page_token': 'same'}
        elif method == 'GET' and path.startswith('/open-apis/im/v1/messages/'):
            mid = path.rsplit('/', 1)[-1]
            result = {'items': [self.messages[mid]] if mid in self.messages else []}
        else:
            raise AssertionError('remote text slice cannot perform another API mutation')
        return {'ok': True, 'identity': 'bot', 'data': result}


class RemoteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World(self)
        self.path = self.w.root / 'metadata' / 'runtime.db'
        self.db = store.Store(self.path)
        self.addCleanup(lambda: self.db.close())
        self.w.db = self.db
        self.db.register_agent(PUB, owner_pubkey=OWNER, app_id=APP,
            config_path=str(self.w.env), now=NOW)
        self.consumer = self.w.consumer()

    async def grant(self):
        self.w.loop = asyncio.get_running_loop()
        proof = await self.consumer.verify(self.w.target)
        self.assertEqual(proof.status, 'verified', 'real signed/catalog/bot fixture must validate before bridge assertion')
        old = self.db.remote_grant(store.Store._remote_digest([PUB, CHANNEL]))
        return self.db.activate_remote_grant(proof.authorization.evidence,
            expected_revision=old.revision if old else 0, now=self.w.now)

    def runtime(self, *, proofs=None, **kwargs):
        self.w.loop = asyncio.get_running_loop()
        self.assertIsNotNone(importlib.util.find_spec('hostd.remote_runtime'),
                             'missing internal runtime authority bridge: tests-first RED')
        module = importlib.import_module('hostd.remote_runtime')
        return module.RemoteRuntime(self.db, record=self.w.actual_record, reader=self.w.reader,
            bot=self.w.bot, proofs=self.consumer if proofs is None else proofs,
            link_base=ORIGIN, clock=lambda: self.w.now, **kwargs)

    async def deliver(self, runtime, event, grant, *, target=None, **pins):
        return await runtime.deliver(target or self.w.target, event,
            target_id=pins.get('target_id', grant.target_id), revision=pins.get('revision', grant.revision),
            scope_hash=pins.get('scope_hash', grant.scope_hash))

    def pending(self, result):
        self.assertEqual(result.status, 'pending')
        self.assertIn('怎么解决', result.notice)
        self.assertIn('复制给 AI', result.notice)
        self.assertFalse(result.readback()['live_verified'])
        for canary in (signed_fixture.KEY, 'SYNTHETIC_APP_SECRET', 'SYNTHETIC_PRIVATE_PROMPT',
                       'SYNTHETIC_BODY_DO_NOT_CACHE', 'Traceback'):
            self.assertNotIn(canary, json.dumps(result.readback()))

    def revoke(self, *, field='status', value='paused'):
        self.db.conn.execute(f'UPDATE agent SET {field}=? WHERE pubkey=?', (value, PUB))

    def no_foreign_writer(self):
        self.assertEqual(self.db.bindings(), [])
        self.assertFalse(self.w.cli_calls)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM app_profile').fetchone()[0], 0)
        self.assertFalse(any(self.w.root.rglob('config.json')) and
                         any(p != self.w.cfg / 'config.json' for p in self.w.root.rglob('config.json')))

    def reopen(self):
        self.db.close()
        self.db = store.Store(self.path)
        self.w.db = self.db

    def pin_unknown(self, grant, event):
        card = message_card(event, self.w.actual_record.name, event['content'], ORIGIN, CHANNEL)
        reservation = self.db.reserve_remote_delivery(grant.target_id, event['id'], 'message',
            revision=grant.revision, scope_hash=grant.scope_hash, source_at=event['created_at'],
            content_hash=hashlib.sha256(card.encode()).hexdigest(), root_id='', now=self.w.now)
        self.assertTrue(reservation.created)
        self.assertTrue(self.db.mark_remote_unknown(reservation.record.id, revision=grant.revision,
            scope_hash=grant.scope_hash, now=self.w.now))
        return reservation.record, card

    async def test_fixture_control_real_consumer_and_sql_grant_do_not_create_foreign_binding(self):
        grant = await self.grant()
        self.assertEqual(grant.evidence.app_id, APP)
        self.assertEqual(grant.evidence.approval_id, self.w.approval['id'])
        self.assertEqual(grant.evidence.allowlist_hash, store.Store._remote_digest(sorted(self.w.actual_record.channels)))
        self.assertEqual(self.db.remote_proof(grant.target_id).revision, grant.revision)
        self.assertFalse(self.w.posts)
        async def authorize(record, channel):
            self.assertIs(record, self.w.actual_record)
            self.assertEqual(channel, CHANNEL)
            return (await self.consumer.verify(self.w.target)).authorization.evidence
        context = RemoteMappingContext(self.w.target, reader=self.w.reader, bot_client=self.w.bot,
            clock=lambda: self.w.now, link_base=ORIGIN)
        outlet = RemoteOutlet(self.db, self.w.actual_record, self.w.reader, self.w.bot, context,
            authorize=authorize, link_base=ORIGIN, clock=lambda: self.w.now)
        event = self.w.event()
        self.assertEqual((await outlet.deliver(event, grant.target_id)).status, 'acked',
                         'the actual accepted adapters and native IO fixture must work before bridge RED')
        self.assertEqual(len(self.w.posts), 1)
        self.no_foreign_writer()

    async def test_existing_explicit_grant_real_consumer_and_native_receipt_ack_once(self):
        grant = await self.grant()
        runtime, event = self.runtime(), self.w.event()
        self.assertEqual((await self.deliver(runtime, event, grant)).status, 'acked')
        self.assertEqual((await self.deliver(runtime, event, grant)).status, 'acked')
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual(self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message').status, 'acked')
        self.no_foreign_writer()

    async def test_discovery_and_valid_signed_approval_without_existing_grant_do_not_activate(self):
        event = self.w.event()
        result = await self.runtime().deliver(self.w.target, event,
            target_id=store.Store._remote_digest([PUB, CHANNEL]), revision=1, scope_hash='a' * 64)
        self.pending(result)
        self.assertFalse(self.w.posts)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM remote_grant').fetchone()[0], 0)

    async def test_missing_actual_approval_cannot_use_cached_grant_metadata_as_authority(self):
        grant = await self.grant()
        self.w.events.remove(self.w.approval)
        self.pending(await self.deliver(self.runtime(), self.w.event(), grant))
        self.assertFalse(self.w.posts)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM remote_delivery').fetchone()[0], 0)

    async def test_revoked_agent_or_changed_sql_app_owner_and_path_prevent_io(self):
        grant = await self.grant()
        event = self.w.event()
        for field, value, original in (('status', 'paused', 'active'), ('app_id', 'cli_other', APP),
                                     ('owner_pubkey', 'a' * 64, OWNER),
                                     ('config_path', str(self.w.root / 'other.env'), str(self.w.env))):
            with self.subTest(field=field):
                self.revoke(field=field, value=value)
                count = len(self.w.wire_calls) + len(self.w.api_calls)
                self.pending(await self.deliver(self.runtime(), event, grant))
                self.assertEqual(len(self.w.wire_calls) + len(self.w.api_calls), count)
                self.revoke(field=field, value=original)
        self.assertFalse(self.w.posts)

    async def test_sql_agent_revocation_during_actual_bot_await_prevents_reservation(self):
        grant = await self.grant()
        self.w.on_member = lambda: self.w.on_loop(self.revoke)
        self.pending(await self.deliver(self.runtime(), self.w.event(), grant))
        self.assertFalse(self.w.posts)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM remote_delivery').fetchone()[0], 0)

    async def test_grant_suspension_during_actual_bot_await_prevents_dispatch(self):
        grant = await self.grant()
        self.w.on_member = lambda: self.w.on_loop(lambda: self.db.suspend_remote_grant(
            grant.target_id, expected_revision=grant.revision, now=self.w.now))
        self.pending(await self.deliver(self.runtime(), self.w.event(), grant))
        self.assertFalse(self.w.posts)

    async def test_exact_target_and_revision_scope_pin_are_required_before_io(self):
        grant, event = await self.grant(), self.w.event()
        for pins, target in (({'revision': grant.revision + 1}, None),
                             ({'scope_hash': 'a' * 64}, None),
                             ({'target_id': 'a' * 64}, None),
                             ({}, dataclasses.replace(self.w.target, chat_id='oc_other'))):
            with self.subTest(pins=pins):
                count = len(self.w.wire_calls) + len(self.w.api_calls)
                self.pending(await self.deliver(self.runtime(), event, grant, target=target, **pins))
                self.assertEqual(len(self.w.wire_calls) + len(self.w.api_calls), count)
        self.assertFalse(self.w.posts)

    async def test_protected_catalog_exclusion_and_profile_changes_remain_pending(self):
        grant, runtime = await self.grant(), self.runtime()
        self.w.owned(self.w.legacy_path, json.dumps(self.w.doc))
        self.pending(await self.deliver(runtime, self.w.event(), grant))
        self.assertFalse(self.w.posts)
        self.w.owned(self.w.legacy_path, json.dumps(dict(self.w.doc, agents=[])))
        self.w.owned(self.w.cfg / 'config.json', json.dumps({'apps': [{'appId': 'cli_other', 'name': 'local'}]}))
        self.pending(await self.deliver(runtime, self.w.event(text='SYNTHETIC_SECOND_BODY'), grant))
        self.assertFalse(self.w.posts)

    async def test_env_change_during_actual_consumer_await_blocks_send(self):
        grant = await self.grant()
        self.w.on_member = lambda: self.w.owned(self.w.env, self.w.env.read_text() + '# changed\n')
        self.pending(await self.deliver(self.runtime(), self.w.event(), grant))
        self.assertFalse(self.w.posts)

    async def test_signed_policy_revocation_or_bad_signature_never_reserves(self):
        grant, runtime = await self.grant(), self.runtime()
        self.w.policy_app = 'cli_revoked'
        self.w.directory = self.w.metadata()
        self.pending(await self.deliver(runtime, self.w.event(), grant))
        self.w.policy_app = APP
        self.w.directory = self.w.metadata()
        self.w.bad_sig = True
        self.pending(await self.deliver(runtime, self.w.event(text='SYNTHETIC_SECOND_BODY'), grant))
        self.assertFalse(self.w.posts)

    async def test_native_incomplete_membership_or_missing_scope_cannot_dispatch(self):
        grant, runtime = await self.grant(), self.runtime()
        self.w.members_incomplete = True
        self.pending(await self.deliver(runtime, self.w.event(), grant))
        self.w.members_incomplete = False
        self.w.scopes.clear()
        self.pending(await self.deliver(runtime, self.w.event(text='SYNTHETIC_SECOND_BODY'), grant))
        self.assertFalse(self.w.posts)

    async def test_consumer_requires_exact_expected_objects_and_cannot_accept_verified_dto(self):
        grant = await self.grant()
        proof = await self.consumer.verify(self.w.target)
        other_reader = type(self.w.reader)(self.w.actual_record, origin=ORIGIN,
            relay_pubkey=proofs_fixture.PIN, trusted_relays=(ORIGIN,), clock=lambda: self.w.now, http=self.w.http)
        other_consumer = RemoteProofs(self.w.catalog_path, self.w.legacy_path,
            record=self.w.actual_record, reader=other_reader, bot=self.w.bot, clock=lambda: self.w.now)
        for invalid in (proof.authorization, other_consumer):
            with self.subTest(kind=type(invalid).__name__):
                count = len(self.w.wire_calls) + len(self.w.api_calls)
                # Invalid factory configuration fails before constructing an
                # authority callback; this is not a caller approval endpoint.
                with self.assertRaises((TypeError, ValueError)):
                    self.runtime(proofs=invalid)
                self.assertEqual(len(self.w.wire_calls) + len(self.w.api_calls), count)
        self.assertFalse(self.w.posts)

    async def test_expired_current_proof_renews_same_original_grant_without_repinning(self):
        grant, event = await self.grant(), self.w.event()
        old, card = self.pin_unknown(grant, event)
        self.w.messages['om_old'] = dict(message_id='om_old', chat_id=CHAT, root_id='om_old',
            thread_id='omt_om_old', create_time=str(NOW * 1000), msg_type='interactive',
            sender={'sender_type': 'app', 'id_type': 'app_id', 'id': APP}, body={'content': card})
        self.w.now += 31
        self.w.claims[0]['heartbeat'] = self.w.now
        self.w.directory = self.w.metadata()
        self.assertLess(self.db.remote_proof(grant.target_id).evidence.valid_until, self.w.now)
        self.reopen()
        self.assertEqual((await self.deliver(self.runtime(), event, grant)).status, 'acked')
        current = self.db.remote_proof(grant.target_id)
        self.assertEqual((current.revision, current.scope_hash), (grant.revision, grant.scope_hash))
        self.assertGreater(current.evidence.valid_until, self.w.now)
        self.assertEqual(self.db.remote_delivery(old.id).content_hash, old.content_hash)
        self.assertFalse(self.w.posts)

    async def test_reopened_unknown_only_adopts_original_own_bot_receipt_without_post(self):
        grant, event = await self.grant(), self.w.event()
        old, card = self.pin_unknown(grant, event)
        self.w.messages['om_old'] = dict(message_id='om_old', chat_id=CHAT, root_id='om_old',
            thread_id='omt_om_old', create_time=str(NOW * 1000), msg_type='interactive',
            sender={'sender_type': 'app', 'id_type': 'app_id', 'id': APP}, body={'content': card})
        self.reopen()
        result = await self.deliver(self.runtime(), event, grant)
        self.assertEqual((result.status, result.message_id), ('acked', 'om_old'))
        row = self.db.remote_delivery(old.id)
        self.assertEqual((row.id, row.revision, row.scope_hash, row.content_hash),
            (old.id, old.revision, old.scope_hash, old.content_hash))
        self.assertFalse(self.w.posts)
        self.no_foreign_writer()

    async def test_new_grant_cannot_resurrect_original_unknown_under_new_revision(self):
        grant, event = await self.grant(), self.w.event()
        old, _ = self.pin_unknown(grant, event)
        self.w.now += 1
        self.w.claims[0]['claimed_at'] += 1
        self.w.claims[0]['heartbeat'] = self.w.now
        self.w.directory = self.w.metadata()
        self.w.target = dataclasses.replace(self.w.target, claimed_at=NOW - 99, heartbeat=self.w.now)
        replacement = self.w.approval_event()
        body = json.loads(replacement['content'])
        body['claimed_at'] = NOW - 99
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        replacement = gs.sign_event(signed_fixture.MIRROR_KEY, 30078,
            [['t', 'hostd-card-approval-v1'], ['h', CHANNEL], ['p', PUB],
             ['d', 'hostd-card-approval-v1:' + digest]], raw, self.w.now)
        self.w.events.remove(self.w.approval)
        self.w.events.append(replacement)
        self.w.approval = replacement
        new = await self.grant()
        self.assertGreater(new.revision, grant.revision)
        self.reopen()
        runtime = self.runtime()
        self.pending(await self.deliver(runtime, event, grant))
        self.pending(await self.deliver(runtime, event, new))
        row = self.db.remote_delivery(old.id)
        self.assertEqual((row.status, row.revision, row.scope_hash, row.content_hash),
            ('unknown', old.revision, old.scope_hash, old.content_hash))
        self.assertFalse(self.w.posts)

    async def test_revocation_after_physical_post_keeps_original_unknown_without_ack(self):
        grant, event = await self.grant(), self.w.event()
        self.w.after_post = self.revoke
        self.pending(await self.deliver(self.runtime(), event, grant))
        self.assertEqual(len(self.w.posts), 1)
        row = self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message')
        self.assertEqual(row.status, 'unknown')
        self.reopen()
        self.pending(await self.deliver(self.runtime(), event, grant))
        self.assertEqual(len(self.w.posts), 1)

    async def test_close_prevents_new_work_and_unknown_absence_never_replays(self):
        grant, event = await self.grant(), self.w.event()
        old, _ = self.pin_unknown(grant, event)
        self.reopen()
        runtime = self.runtime()
        self.pending(await self.deliver(runtime, event, grant))
        runtime.close()
        count = len(self.w.wire_calls) + len(self.w.api_calls)
        self.pending(await self.deliver(runtime, self.w.event(text='SYNTHETIC_SECOND_BODY'), grant))
        self.assertEqual(len(self.w.wire_calls) + len(self.w.api_calls), count)
        self.assertEqual(self.db.remote_delivery(old.id).status, 'unknown')
        self.assertFalse(self.w.posts)

    async def test_cancelled_physical_io_reaps_and_reopened_unknown_uses_receipt_get_only(self):
        grant, event = await self.grant(), self.w.event()
        runtime = self.runtime()
        self.w.block_send = True
        task = asyncio.create_task(self.deliver(runtime, event, grant))
        self.addCleanup(self.w.send_release.set)
        try:
            self.assertTrue(await asyncio.to_thread(self.w.send_entered.wait, 30),
                'bounded fixture rendezvous is not a product latency acceptance threshold')
            task.cancel()
            await asyncio.sleep(.03)
            self.assertFalse(task.done(), 'do not abandon a physically running write on cancellation')
        finally:
            self.w.send_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        row = self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message')
        self.assertEqual(row.status, 'unknown')
        self.reopen()
        self.assertEqual((await self.deliver(self.runtime(), event, grant)).status, 'acked')
        self.assertEqual(len(self.w.posts), 1, 'unknown may reconcile actual receipt, never repeat POST')

    async def test_actual_complete_scan_only_commits_cursor_and_unsupported_holds_it(self):
        grant, runtime = await self.grant(), self.runtime()
        self.w.event(kind=7, tags=[['e', 'a' * 64]])
        result = await runtime.drain(self.w.target, target_id=grant.target_id,
            revision=grant.revision, scope_hash=grant.scope_hash)
        self.pending(result)
        self.assertEqual(self.db.remote_replay_since(grant.target_id), 0)
        self.assertFalse(self.w.posts)


if __name__ == '__main__':
    unittest.main()
