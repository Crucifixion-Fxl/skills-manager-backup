"""PRIVATE unexecuted tests-first draft: own zero-channel readonly discovery.

Only native HTTP, own signed relay HTTP, and the portable AES primitive are
synthetic. Catalog/files/keys/signatures/protocol/native pagination are actual.
No foreign env, SQL, grant, process action or write is exercised.
"""
import asyncio
import base64
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_proofs as base
import test_hostd_remote_mapping as keys
from hostd import agent_catalog, remote_approval
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli
import buzz_feishu_group_sync as gs


class AdmissionWorld(base.World):
    def __init__(self, case):
        super().__init__(case)
        # Change the actual protected own allowlist, then load the actual record.
        # Never replace record.channels or supply a verified DTO.
        content = re.sub(r'^BUZZ_ACP_CHANNELS=.*$', 'BUZZ_ACP_CHANNELS=', self.env.read_text(), flags=re.M)
        self.owned(self.env, content)
        self.catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_path)
        self.actual_record = self.catalog.records[0]
        case.assertEqual(self.actual_record.channels, ())
        case.assertEqual(self.actual_record.status, 'own_bot_verified')
        self.reader = OwnAgentReader(self.actual_record, origin=base.ORIGIN, relay_pubkey=base.PIN,
            trusted_relays=(base.ORIGIN,), clock=lambda: self.now, http=self.http)
        self.bot = BotLarkCli(base.APP, self.cfg, self.data, base_env={'HOME': str(self.root)},
                              http_pool=self, chat_id=None)
        self.chat_rows = [{'chat_id': base.CHAT, 'name': 'SYNTHETIC_CHAT'}]
        self.chat_more = False
        self.native_block = False
        self.native_entered = threading.Event()
        self.native_release = threading.Event()
        self.query_hook = None
        self.queries = []

    def http(self, url, headers, timeout, *, body=None):
        filters = json.loads(body)
        self.queries.extend(filters)
        status, raw = super().http(url, headers, timeout, body=body)
        if self.query_hook:
            rows = self.query_hook(filters, json.loads(raw))
            raw = json.dumps(rows).encode()
        return status, raw

    def request(self, *args, **kwargs):
        self.assertEqual((args[0], Path(args[1]), Path(args[2])), (base.APP, self.cfg, self.data))
        self.assertEqual(args[3], 'GET', 'readonly admission must never mutate native APIs')
        self.assertIn(kwargs.get('chat_id'), (None, base.CHAT))
        path = args[4]
        self.api_calls.append((args[3], path, kwargs.get('params'), kwargs.get('data')))
        if path == '/open-apis/application/v6/scopes':
            return {'ok': True, 'identity': self.scope_identity, 'data': {'scopes': [
                {'scope_name': name, **({'scope_type': 'tenant'} if self.scope_identity == 'bot' else {}),
                 'grant_status': self.scope_grant_status} for name in sorted(self.scopes)]}}
        if path == '/open-apis/im/v1/chats':
            return {'ok': True, 'identity': 'bot', 'data': {'items': self.chat_rows,
                'has_more': self.chat_more, 'page_token': 'repeat' if self.chat_more else ''}}
        if path == '/open-apis/im/v1/chats/' + base.CHAT + '/members/list':
            self.native_entered.set()
            if self.native_block:
                self.assertTrue(self.native_release.wait(3), 'cleanup must join original native IO')
            if self.on_member:
                callback, self.on_member = self.on_member, None
                callback()
            bots = [] if self.bot_absent else [{'member_id': 'ou_own', 'app_id': base.APP}]
            return {'ok': True, 'identity': 'bot', 'data': {'users': [], 'bots': bots,
                'user_total': 0, 'bot_total': len(bots), 'truncations': [],
                'has_more': self.members_incomplete, 'page_token': 'repeat' if self.members_incomplete else ''}}
        raise AssertionError('only own scopes/chats/member native GETs are permitted')

    def adapter(self, **kwargs):
        self.case.assertIsNotNone(importlib.util.find_spec('hostd.own_admission_discovery'),
            'missing actual readonly own admission discovery; do not waive existing allowlist gates')
        module = importlib.import_module('hostd.own_admission_discovery')
        return module.OwnAdmissionDiscovery(self.catalog_path, self.legacy_path,
            record=kwargs.pop('record', self.actual_record), reader=kwargs.pop('reader', self.reader),
            bot=kwargs.pop('bot', self.bot), clock=lambda: self.now, **kwargs)

    def mutate_approval(self, **changes):
        body = json.loads(self.approval['content']); body.update(changes)
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        event = gs.sign_event(keys.MIRROR_KEY, 30078,
            [['t', remote_approval.PREFIX], ['h', body['channel_id']], ['p', body['agent_pubkey']],
             ['d', remote_approval.PREFIX + ':' + digest]], raw, self.now)
        self.events = [event]
        return event

    def no_mutation(self, original):
        self.no_writes()
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in original}, original)
        self.assertEqual(agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_path).records[0].channels, ())


class OwnAdmissionDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = AdmissionWorld(self)
        self.original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.w.root.rglob('*') if p.is_file()}

    def pending(self, result):
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.candidates, ())
        self.assertIn('怎么解决', result.notice)
        self.assertIn('复制给 AI', result.notice)
        self.assertFalse(result.readback()['sending_ready'])
        self.assertFalse(result.readback()['grant_activated'])
        for canary in (keys.KEY, 'SYNTHETIC_APP_SECRET', 'SYNTHETIC_PRIVATE_PROMPT', 'SYNTHETIC_CHAT', 'Traceback'):
            self.assertNotIn(canary, repr(result))
        self.w.no_writes()

    async def test_control_real_protected_catalog_zero_channels_is_own_bot_verified(self):
        record = agent_catalog.load(self.w.catalog_path, legacy_join_path=self.w.legacy_path).records[0]
        self.assertEqual(record.channels, ())
        self.assertEqual((record.pubkey, record.owner_pubkey, record.app_id), (base.PUB, base.OWNER, base.APP))
        self.assertTrue(record.local_bot_verified)
        self.w.no_mutation(self.original)

    async def test_control_actual_own_key_signed_read_and_native_chat_members(self):
        rows = await self.w.reader.query([{'kinds': [30078], 'limit': 257}])
        self.assertEqual([row['id'] for row in rows], [self.w.approval['id']])
        chats = await asyncio.to_thread(self.w.bot.call, 'own chats',
            ['im', 'chats', 'list', '--page-all', '--page-limit', '10'], full=True)
        self.assertIs(chats['meta']['pagination']['complete'], True)
        self.assertEqual(chats['data']['items'][0]['chat_id'], base.CHAT)
        members = await asyncio.to_thread(self.w.bot.member_listing, base.CHAT, 'open_id')
        self.assertTrue(members.complete); self.assertIn(base.APP, members.bots)
        self.assertTrue(all('#p' not in query for query in self.w.queries))
        self.w.no_mutation(self.original)

    async def test_control_existing_remote_consumer_does_not_grant_zero_channel_record(self):
        result = await self.w.consumer().verify(self.w.target)
        self.assertEqual(result.status, 'pending'); self.assertIsNone(result.authorization)
        self.w.no_mutation(self.original)

    async def test_real_signed_approval_native_membership_discovers_metadata_without_grant(self):
        result = await self.w.adapter().discover()
        self.assertEqual(result.status, 'discovered'); self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual((candidate.agent_pubkey, candidate.owner_pubkey, candidate.app_id), (base.PUB, base.OWNER, base.APP))
        self.assertEqual((candidate.channel_id, candidate.chat_id, candidate.chat_ref),
                         (base.CHANNEL, base.CHAT, gs.chat_ref(base.CHAT)))
        self.assertEqual((candidate.approval_id, candidate.approval_hash),
                         (self.w.approval['id'], remote_approval.decode(self.w.approval, now=self.w.now).content_hash))
        self.assertLessEqual(candidate.expires_at, self.w.now + 30)
        self.assertFalse(result.readback()['sending_ready']); self.assertFalse(result.readback()['grant_activated'])
        self.assertFalse(result.readback()['live_verified'])
        self.assertTrue(all('#p' not in query for query in self.w.queries))
        self.w.no_mutation(self.original)

    async def test_missing_approval_is_pending_not_empty_success(self):
        self.w.events = []
        self.pending(await self.w.adapter().discover())

    async def test_forged_approval_signature_rejects_whole_actual_packet(self):
        self.w.approval['sig'] = '0' * 128
        self.pending(await self.w.adapter().discover())

    async def test_valid_approval_plus_forged_signed_policy_cannot_select_good_subset(self):
        self.w.directory.append({**self.w.directory[0], 'sig': '0' * 128})
        self.pending(await self.w.adapter().discover())

    async def test_approval_duplicate_or_saturated_packet_is_pending(self):
        for copies in (2, 256):
            with self.subTest(copies=copies):
                self.w.events = [self.w.approval] * copies
                self.pending(await self.w.adapter().discover())

    async def test_wrong_approval_owner_or_subject_app_is_pending(self):
        for changes in ({'agent_owner_pubkey': base.MOWNER}, {'app_id': 'cli_foreign'}):
            with self.subTest(changes=tuple(changes)):
                self.w.mutate_approval(**changes)
                self.pending(await self.w.adapter().discover())

    async def test_selected_profile_substitution_is_pending(self):
        self.w.owned(self.w.cfg / 'config.json', json.dumps({'apps': [{'appId': 'cli_foreign', 'name': 'local'}]}))
        self.pending(await self.w.adapter().discover())

    async def test_own_reader_mismatched_protected_env_or_owner_is_pending(self):
        other = self.w.root / 'other.env'
        self.w.owned(other, self.w.env.read_bytes())
        # An actual OwnAgentReader with another protected path is still not the
        # selected catalog reader. No forged verified DTO is passed.
        from types import SimpleNamespace
        reader = OwnAgentReader(SimpleNamespace(pubkey=base.PUB, owner_pubkey=base.OWNER, env_file=other),
            origin=base.ORIGIN, relay_pubkey=base.PIN, trusted_relays=(base.ORIGIN,),
            clock=lambda: self.w.now, http=self.w.http)
        self.pending(await self.w.adapter(reader=reader).discover())

    async def test_subject_policy_revoked_app_is_pending(self):
        self.w.policy_app = 'cli_revoked'; self.w.directory = self.w.metadata()
        self.pending(await self.w.adapter().discover())

    async def test_conditional_mirror_oa_is_pending(self):
        self.w.mirror_conditions = 'channel:' + base.CHANNEL; self.w.directory = self.w.metadata()
        self.pending(await self.w.adapter().discover())

    async def test_current_roster_subject_absent_or_wrong_role_is_pending(self):
        for role in (None, 'member'):
            with self.subTest(role=role):
                if role is None: self.w.roles.pop(base.PUB, None)
                else: self.w.roles[base.PUB] = role
                self.w.directory = self.w.metadata()
                self.pending(await self.w.adapter().discover())

    async def test_changed_stable_claim_or_expired_heartbeat_is_pending(self):
        for claim in (dict(self.w.claims[0], claimed_at=base.NOW-99),
                      dict(self.w.claims[0], heartbeat=base.NOW-gs.CLAIM_LEASE_SECONDS-1)):
            with self.subTest(claimed_at=claim['claimed_at'], heartbeat=claim['heartbeat']):
                self.w.claims = [claim]; self.w.directory = self.w.metadata()
                self.pending(await self.w.adapter().discover())

    async def test_wrong_native_scope_identity_or_grant_is_pending(self):
        for identity, grant in (('user', 1), ('bot', 0), ('bot', True)):
            with self.subTest(identity=identity, grant=grant):
                self.w.scope_identity, self.w.scope_grant_status = identity, grant
                self.pending(await self.w.adapter().discover())

    async def test_partial_native_chat_paging_is_pending(self):
        self.w.chat_more = True
        self.pending(await self.w.adapter().discover())
        self.assertLessEqual(sum(path == '/open-apis/im/v1/chats' for _, path, *_ in self.w.api_calls), 10)

    async def test_duplicate_native_chat_identity_is_pending(self):
        self.w.chat_rows *= 2
        self.pending(await self.w.adapter().discover())

    async def test_canonical_chat_ref_has_no_actual_own_chat_match_is_pending(self):
        self.w.chat_rows = [{'chat_id': 'oc_unrelated', 'name': 'SYNTHETIC_CHAT'}]
        self.pending(await self.w.adapter().discover())

    async def test_native_members_incomplete_or_own_app_absent_is_pending(self):
        for incomplete, absent in ((True, False), (False, True)):
            with self.subTest(incomplete=incomplete, absent=absent):
                self.w.members_incomplete, self.w.bot_absent = incomplete, absent
                self.pending(await self.w.adapter().discover())

    async def test_policy_revocation_during_actual_native_io_is_pending(self):
        def revoke():
            self.w.policy_app = 'cli_revoked'; self.w.directory = self.w.metadata()
        self.w.on_member = revoke
        self.pending(await self.w.adapter().discover())
        self.assertTrue(self.w.native_entered.is_set(), 'negative must cross actual native member IO')

    async def test_catalog_change_during_actual_native_io_is_pending(self):
        self.w.on_member = lambda: self.w.owned(self.w.catalog_path, json.dumps(dict(self.w.doc, agents=[])))
        self.pending(await self.w.adapter().discover())
        self.assertTrue(self.w.native_entered.is_set())

    async def test_elapsed_proof_window_during_actual_native_io_is_pending(self):
        self.w.on_member = lambda: setattr(self.w, 'now', base.NOW + 31)
        self.pending(await self.w.adapter().discover())
        self.assertTrue(self.w.native_entered.is_set())

    async def test_same_stable_claim_monotonic_heartbeat_during_native_io_is_allowed(self):
        def renew():
            self.w.now += 1; self.w.claims[0]['heartbeat'] = self.w.now
            self.w.directory = self.w.metadata()
        self.w.on_member = renew
        result = await self.w.adapter().discover()
        self.assertEqual(result.status, 'discovered'); self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].heartbeat, base.NOW + 1)
        self.w.no_mutation(self.original)

    async def test_distinct_current_approvals_for_same_scope_are_ambiguous(self):
        original = self.w.approval
        other = self.w.mutate_approval(request_id='JOIN-5678abcd',
            decision_event_sha256=hashlib.sha256(b'evt_distinct_decision').hexdigest())
        self.w.events = [original, other]
        self.pending(await self.w.adapter().discover())

    async def test_complete_rival_claim_same_channel_or_same_chat_changes_winner(self):
        for channel in (base.CHANNEL, '00000000-0000-0000-0000-000000000002'):
            with self.subTest(channel=channel):
                self.w.directory = self.w.metadata()
                rival = dict(channel=channel, chat_ref=gs.chat_ref(base.CHAT),
                             claimed_at=base.NOW-200, heartbeat=base.NOW)
                self.w.directory.extend([
                    gs.sign_event(keys.HUMAN_KEY, 0,
                        [keys.auth(keys.HUMAN_KEY, keys.FOREIGN_OWNER_KEY)], '{}', base.NOW),
                    gs.sign_event(keys.FOREIGN_OWNER_KEY, 30177, [['d', keys.HUMAN]],
                        json.dumps({'feishu': {'mirror': True, 'bindings': [rival]}}), base.NOW)])
                self.w.roles[keys.HUMAN] = 'bot'
                # Fresh complete pinned roster authorizes this actual rival.
                self.w.directory = [event for event in self.w.directory if event['kind'] != 39002]
                self.w.directory.append(gs.sign_event(keys.RELAY_KEY, 39002,
                    [['d', base.CHANNEL]] + [['p', pub, '', role] for pub, role in self.w.roles.items()],
                    '', base.NOW))
                self.pending(await self.w.adapter().discover())

    async def test_repeated_cancel_joins_original_native_io_without_writes(self):
        self.w.native_block = True
        task = asyncio.create_task(self.w.adapter().discover())
        try:
            entered = await asyncio.to_thread(self.w.native_entered.wait, 30)
            self.assertTrue(entered, 'rendezvous must observe actual native IO, not signed-proof timing')
            task.cancel(); await asyncio.sleep(.01); task.cancel(); await asyncio.sleep(.01)
            self.assertFalse(task.done(), 'cancel must retain ownership while actual native IO is dispatched')
            self.w.native_release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.w.no_mutation(self.original)
        finally:
            self.w.native_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
