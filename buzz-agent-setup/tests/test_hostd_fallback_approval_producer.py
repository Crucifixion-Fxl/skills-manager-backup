"""Foreign fallback outward boundaries: real SQL/crypto/native IO, offline.

Only the lowest HTTPS endpoints are synthetic. No foreign private file,
AgentSpec, local effect, caller proof flag, grant or runtime adapter is used.
"""
import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
import unittest

import test_hostd_fallback_onboarding as cards
from hostd import fallback_discovery, remote_approval, store
from hostd.join_effects import Nip98Relay
from hostd.signed_reads import SignedReader, ReadFailure
import recovery_authority as authority

producer = (importlib.import_module('hostd.fallback_approval_producer')
            if importlib.util.find_spec('hostd.fallback_approval_producer') else None)
base, gs, claims = cards.base, cards.gs, cards.claims
SUBJECT, SUBJECT_OWNER, SUBJECT_APP, BINDING = cards.SUBJECT, cards.SUBJECT_OWNER, cards.SUBJECT_APP, cards.BINDING
setUpModule, tearDownModule = cards.setUpModule, cards.tearDownModule
FILES = '1' * 64
FROZEN_SQL = dict(cards.FROZEN_SQL)
# Frozen schema8 bytes, captured before any producer/journal implementation.
FROZEN_SQL['_UPGRADE_V8_SCHEMA'] = 'a05f4b5913bfd3984d31c4584affc4a17612c6ca681ef282ca6fa33d2d3ce8c5'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class OutwardTests(base.TmpCase, unittest.IsolatedAsyncioTestCase):
    assembly, seed = cards.FallbackOnboardingTests.assembly, cards.FallbackOnboardingTests.seed
    setup_discovery = cards.FallbackOnboardingTests.setup_discovery
    setup_fallback = cards.FallbackOnboardingTests.setup_fallback
    policy = cards.FallbackOnboardingTests.policy
    mirror_change = cards.FallbackOnboardingTests.mirror_change
    save_catalog = cards.FallbackOnboardingTests.save_catalog
    decision = cards.FallbackOnboardingTests.decision
    assert_no_runtime = cards.FallbackOnboardingTests.assert_no_runtime

    def service(self, db=None):
        return cards.FallbackOnboardingTests.instance(self, db)

    async def setup_outward(self, *, approved=True):
        self.setup_fallback()
        self.add_policy('anyone')
        self.outward, self.outward_gets, self.timeline = [], [], []
        self.mode, self.readback_mode, self.roster_mode = '', '', ''
        self.post_hook, self.read_hook = None, None
        self.entered, self.release = threading.Event(), threading.Event()
        self.check_unknown = False
        original = self.relay.http
        def low(url, headers, timeout, body=None):
            if url.endswith('/events'):
                event = json.loads(body)
                auth = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
                self.assertTrue(gs._nip01_event_verified(auth))
                authority.exact_tag(auth, 'u', url); authority.exact_tag(auth, 'method', 'POST')
                authority.exact_tag(auth, 'payload', hashlib.sha256(body).hexdigest())
                self.assertTrue(gs._nip01_event_verified(event))
                stage = {9000: 'member', 30078: 'approval'}[event['kind']]
                self.assertEqual(auth['pubkey'], event['pubkey'])
                if stage == 'member':
                    self.assertEqual(event['pubkey'], base.OWNER_PK)
                    self.assertEqual(event['tags'], [['h', base.CHANNEL], ['p', SUBJECT], ['role', 'bot']])
                    self.assertEqual(event['content'], '')
                else:
                    self.assertEqual(event['pubkey'], base.MIRROR_PK)
                    self.assertIn('x-auth-tag', headers)
                    remote_approval.decode(event, now=self.now)
                    self.assertEqual({m['pubkey']: m['role'] for m in self.world.members}.get(SUBJECT), 'bot')
                if self.check_unknown:
                    # A separately opened SQL connection observes the committed
                    # UNKNOWN before physical mutation; no worker-thread SQL.
                    with sqlite3.connect('file:' + str(self.db.path) + '?mode=ro', uri=True) as observed:
                        row = observed.execute('SELECT state,event_id FROM fallback_outward WHERE request_id=? AND stage=?',
                            (self.request_id, stage)).fetchone()
                    self.assertEqual(row, ('unknown', event['id']))
                self.timeline.append(('POST', stage)); self.outward.append(copy.deepcopy(event))
                if self.post_hook is not None:
                    hook, self.post_hook = self.post_hook, None; hook(stage)
                if self.mode == 'blocked-' + stage:
                    self.entered.set(); self.assertTrue(self.release.wait(10))
                if self.mode == 'unreachable-' + stage: raise OSError('offline unknown outcome')
                if stage == 'member':
                    policies = [e for e in self.world.relay_events if e['kind'] == 10100 and e['pubkey'] == SUBJECT]
                    current = authority.latest(policies)
                    policy = json.loads(current['content'])['channel_add_policy']
                    if policy == 'nobody' or (policy == 'owner_only' and event['pubkey'] != SUBJECT_OWNER):
                        return 200, json.dumps({'accepted': False, 'event_id': event['id'], 'message': 'policy:' + policy}).encode()
                    if self.mode != 'no-roster': self.member_present()
                if not any(e['id'] == event['id'] for e in self.world.relay_events):
                    self.world.relay_events.append(copy.deepcopy(event))
                if self.mode == 'lost-' + stage: raise OSError('offline lost response')
                return 200, json.dumps({'accepted': True, 'event_id': event['id'], 'message': 'duplicate:'}).encode()
            if url.endswith('/query'):
                # Use the actual native owner-authenticated query first. This
                # retains transport/signature checks for every public frame.
                status, raw = original(url, headers, timeout, body)
                filters, rows = json.loads(body), json.loads(raw)
                rows = [e for e in rows if any(self.matches(e, f) for f in filters)]
                outward_query = any('ids' in f or any(k in (9000, 30078) for k in f.get('kinds', [])) for f in filters)
                if outward_query:
                    self.outward_gets.append(copy.deepcopy(filters)); self.timeline.append(('GET', 'outward'))
                    if self.read_hook is not None:
                        hook, self.read_hook = self.read_hook, None; hook()
                    if self.readback_mode == 'empty': rows = []
                    elif self.readback_mode == 'forged' and rows: rows[-1]['sig'] = '0' * 128
                    elif self.readback_mode == 'alternate' and rows:
                        key = base.OWNER_KEY if rows[-1]['kind'] == 9000 else base.MIRROR_KEY
                        rows[-1]['sig'] = gs.sync.nk.schnorr_sign(bytes.fromhex(rows[-1]['id']), bytes.fromhex(key), b'\1' * 32).hex()
                    elif self.readback_mode == 'partial': return 503, b'{}'
                    elif self.readback_mode == 'saturated': rows = rows * 257
                if self.roster_mode and any(39002 in f.get('kinds', []) for f in filters):
                    for i, e in enumerate(rows):
                        if e['kind'] == 39002:
                            if self.roster_mode == 'forged': rows[i]['sig'] = '0' * 128
                            elif self.roster_mode == 'stale':
                                rows[i] = gs.sign_event(claims.PIN_KEY, 39002, e['tags'], '', self.now - 100000)
                return status, json.dumps(rows).encode()
            return original(url, headers, timeout, body)
        self.relay = Nip98Relay('https://relay.test', self.env.signer_env, claims.PIN,
            http=low, trusted_relays=('https://relay.test',), clock=lambda: self.now)
        self.card_service = self.service()
        result = await self.card_service.request(BINDING, SUBJECT, 'native-member-producer-1')
        self.assertEqual(result.status, 'requested')
        self.request_id = result.request_id; self.row = self.db.join_request(self.request_id)
        if approved:
            result = await self.card_service.decide(self.decision(self.row))
            self.assertEqual(result.status, 'approved')
        return self

    @staticmethod
    def matches(event, f):
        return (('ids' not in f or event['id'] in f['ids'])
            and ('kinds' not in f or event['kind'] in f['kinds'])
            and ('authors' not in f or event['pubkey'] in f['authors'])
            and all(any(len(t) > 1 and t[0] == name[1:] and t[1] in values for t in event['tags'])
                for name, values in f.items() if name.startswith('#')))

    def add_policy(self, value):
        # Primary relay-v0.2.1 channels.rs:1037-1044: subject signs 10100,
        # tags=[], JSON channel_add_policy. This is not owner30177 policy.
        event = gs.sign_event(cards.discovery_tests.SUBJECT_KEY, 10100, [],
            json.dumps({'channel_add_policy': value}), self.now)
        self.world.relay_events.append(event)
        return event

    def member_present(self):
        self.world.members[:] = [m for m in self.world.members if m['pubkey'] != SUBJECT]
        self.world.members.append({'pubkey': SUBJECT, 'role': 'bot'})

    def member_event(self):
        return self.relay.event(9000, [['h', base.CHANNEL], ['p', SUBJECT], ['role', 'bot']], '', self.now)

    def approval_event(self, **changes):
        row, marker = self.db.join_request(self.request_id), self.db.fallback_provenance(self.request_id)
        decision = self.db.fallback_approval_decision(self.request_id)
        body = dict(version=1, decision='approve', request_id=self.request_id, agent_pubkey=SUBJECT,
            agent_owner_pubkey=SUBJECT_OWNER, app_id=SUBJECT_APP, channel_id=base.CHANNEL,
            chat_ref=gs.chat_ref(base.CHAT), mirror_pubkey=base.MIRROR_PK, mirror_owner_pubkey=base.OWNER_PK,
            claimed_at=marker.claimed_at, request_created_at=row['created_at'], request_deadline=row['deadline'],
            card_generation=decision.card_generation, card_message_sha256=decision.card_message_sha256,
            decision_event_sha256=decision.decision_event_sha256, decision_at=decision.decision_at)
        body.update(changes)
        content = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        event = gs.sign_event(base.MIRROR_KEY, 30078, [['t', remote_approval.PREFIX], ['h', base.CHANNEL],
            ['p', SUBJECT], ['d', remote_approval.PREFIX + ':' + hashlib.sha256(content.encode()).hexdigest()]], content, self.now)
        return event

    async def verify_current(self, event):
        reader = SignedReader(self.relay)
        policies = await reader.read('query', filters=[{'kinds': [30177], 'limit': 257}])
        profiles = await reader.read('query', filters=[{'kinds': [0], 'limit': 257}])
        roster = await reader.read('query', filters=[{'kinds': [39002], 'authors': [claims.PIN], '#d': [base.CHANNEL], 'limit': 257}])
        self.assertLess(len(policies), 256); self.assertLess(len(profiles), 256); self.assertLess(len(roster), 256)
        decoded = remote_approval.decode(event, now=self.now)
        context = remote_approval.ProofContext(decoded.scope, claims.PIN,
            authority.latest([e for e in profiles if e['pubkey'] == SUBJECT]),
            claims.latest_policy(policies, SUBJECT), tuple(e for e in profiles if e['pubkey'] == base.MIRROR_PK),
            tuple(policies), authority.latest(roster), self.now, complete=True)
        return remote_approval.verify(event, context)

    def instance(self, db=None):
        self.assertIsNotNone(producer, 'actual fallback approval producer is missing')
        actual = db or self.db
        discoverer = fallback_discovery.FallbackDiscoverer(actual, self.clients, self.relay, clock=lambda: self.now)
        return producer.FallbackApprovalProducer(actual, discoverer, self.clients, self.relay,
            catalog_path=self.catalog_path, legacy_join_path=self.legacy_path, clock=lambda: self.now)

    async def pending(self, value=None):
        result = await (value or self.instance()).run(self.request_id)
        self.assertEqual(result.status, 'pending'); self.assertIn('怎么解决', result.notice); self.assertIn('复制给 AI', result.notice)
        self.assert_no_runtime()
        return result

    def record(self, stage):
        self.assertTrue(callable(getattr(self.db, 'fallback_outward', None)), 'dedicated outward journal is missing')
        return self.db.fallback_outward(self.request_id, stage)

    def assert_approved_only(self):
        self.assertEqual(self.db.join_request(self.request_id)['status'], 'approved')
        self.assertEqual(self.db.conn.execute('SELECT config_path,status FROM agent WHERE pubkey=?', (SUBJECT,)).fetchone()[:], (None, 'paused'))
        self.assert_no_runtime()

    async def test_control_actual_first_owner_card_has_separate_issuer_subject_and_no_local_effect(self):
        await self.setup_outward()
        decision = self.db.fallback_approval_decision(self.request_id)
        self.assertEqual((decision.subject_app_id, decision.issuer_app_id), (SUBJECT_APP, base.AGENT_APP))
        self.assertEqual(len(self.https.card_posts), 1); self.assertEqual(self.outward, [])
        self.assertIsNone(self.db.card_approval_decision(self.request_id)); self.assert_approved_only()

    async def test_control_actual_admin_nip98_9000_then_signed_get_and_pinned_roster(self):
        await self.setup_outward(); event = self.member_event()
        self.assertEqual(await asyncio.to_thread(self.relay.publish, event), event['id'])
        rows = await SignedReader(self.relay).read('query', filters=[{'ids': [event['id']], 'limit': 257}])
        self.assertEqual(rows, [event])
        roster = await SignedReader(self.relay).read('query', filters=[{'kinds': [39002], 'authors': [claims.PIN], '#d': [base.CHANNEL], 'limit': 257}])
        self.assertEqual(authority.membership(authority.latest(roster), base.CHANNEL)[SUBJECT], 'bot')
        self.assert_approved_only()

    async def test_control_subject_10100_policy_refuses_distinct_local_admin_not_subject_self_add(self):
        await self.setup_outward()
        for policy in ('owner_only', 'nobody'):
            with self.subTest(policy=policy):
                self.world.relay_events = [e for e in self.world.relay_events if e['kind'] != 10100]
                event = self.add_policy(policy)
                self.assertEqual(event['tags'], []); self.assertEqual(event['pubkey'], SUBJECT)
                rows = await SignedReader(self.relay).read('query', filters=[
                    {'kinds': [10100], 'authors': [SUBJECT], 'limit': 257}])
                self.assertEqual(rows, [event])
                with self.assertRaises(gs.RelayRefused): await asyncio.to_thread(self.relay.publish, self.member_event())
                self.assertNotIn(SUBJECT, {m['pubkey'] for m in self.world.members})
        self.assertEqual(len(self.outward), 2); self.assert_approved_only()

    async def test_control_strict_consumer_requires_subject_bot_after_approval_then_accepts_actual_mirror_record(self):
        await self.setup_outward(); event = self.approval_event()
        with self.assertRaises(remote_approval.ApprovalPending): await self.verify_current(event)
        await asyncio.to_thread(self.relay.publish, self.member_event())
        tag = json.dumps(claims.profile(base.MIRROR_KEY, base.OWNER_KEY, self.now)['tags'][0])
        self.assertEqual(await asyncio.to_thread(self.relay.publish_as, event, base.MIRROR_KEY, tag), event['id'])
        proof = await self.verify_current(event)
        self.assertEqual((proof.scope.app_id, proof.scope.agent_pubkey), (SUBJECT_APP, SUBJECT))
        self.assertEqual([e['pubkey'] for e in self.outward], [base.OWNER_PK, base.MIRROR_PK])
        self.assert_approved_only()

    async def test_fresh_approved_card_runs_member_before_subject_app_approval_committed_unknown_before_both_posts(self):
        await self.setup_outward(); self.check_unknown = True
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); self.assertEqual([e['kind'] for e in self.outward], [9000, 30078])
        proof = await self.verify_current(self.outward[-1]); self.assertEqual(result.event_id, proof.record_id)
        for stage, event in zip(('member', 'approval'), self.outward):
            record = self.record(stage); self.assertEqual(record.state, 'acked')
            self.assertEqual((record.pin.event_id, record.pin.signature), (event['id'], event['sig']))
        self.assert_approved_only()

    async def test_existing_subject_bot_does_not_manufacture_or_post_membership_receipt(self):
        await self.setup_outward(); self.member_present()
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); self.assertEqual([e['kind'] for e in self.outward], [30078])
        self.assertIsNone(self.record('member')); self.assert_approved_only()

    async def test_request_without_actual_first_owner_approve_never_posts(self):
        await self.setup_outward(approved=False); await self.pending(); self.assertEqual(self.outward, [])

    async def test_policy_denied_under_actual_signed_subject_10100_never_publishes_approval(self):
        await self.setup_outward()
        for policy in ('owner_only', 'nobody'):
            with self.subTest(policy=policy):
                self.world.relay_events = [e for e in self.world.relay_events if e['kind'] != 10100]
                self.add_policy(policy); await self.pending()
                self.assertFalse(any(e['kind'] == 30078 for e in self.outward))
        self.assert_approved_only()

    async def test_actual_policy_refusal_at_member_post_retains_original_unknown_without_approval_or_retry(self):
        await self.setup_outward()
        def deny(stage):
            self.assertEqual(stage, 'member')
            self.world.relay_events[:] = [e for e in self.world.relay_events if e['kind'] != 10100]
            self.add_policy('nobody')
        self.post_hook = deny
        await self.pending(); self.assertEqual([e['kind'] for e in self.outward], [9000])
        original = self.record('member'); self.assertEqual(original.state, 'unknown')
        await self.pending(self.instance()); self.assertEqual(len(self.outward), 1)
        self.assertEqual(self.record('member').pin, original.pin); self.assert_approved_only()

    async def test_actual_current_roster_removal_of_local_owner_admin_authority_blocks_member_post(self):
        await self.setup_outward()
        for member in self.world.members:
            if member['pubkey'] == base.OWNER_PK: member['role'] = 'member'
        await self.pending(); self.assertEqual(self.outward, [])

    async def test_membership_post_ack_without_fresh_subject_bot_roster_is_pending_no_approval(self):
        await self.setup_outward(); self.mode = 'no-roster'; await self.pending()
        self.assertEqual([e['kind'] for e in self.outward], [9000]); self.assertNotEqual(self.record('member').state, 'acked')

    async def test_lost_member_response_close_reopen_reads_original_only_then_may_create_first_approval(self):
        await self.setup_outward(); self.mode = 'lost-member'; await self.pending()
        original = copy.deepcopy(self.outward[0]); self.assertEqual(self.record('member').state, 'unknown')
        path = self.db.path; self.db.close(); self.db = store.Store(path); self.addCleanup(self.db.close)
        self.mode = ''; self.now += 1
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); self.assertEqual([e['kind'] for e in self.outward], [9000, 30078])
        self.assertEqual(self.record('member').pin.signature, original['sig']); self.assertTrue(self.outward_gets)
        self.assert_approved_only()

    async def test_unobserved_unknown_member_never_resends_or_replaces_original_event(self):
        await self.setup_outward(); self.mode = 'unreachable-member'; await self.pending()
        original = self.record('member'); self.mode = ''; self.now += 1
        await self.pending(self.instance()); self.assertEqual(len(self.outward), 1)
        self.assertEqual(self.record('member').pin, original.pin); self.assertEqual(self.record('member').state, 'unknown')

    async def test_lost_approval_response_reopen_gets_same_signature_only_and_never_reposts(self):
        await self.setup_outward(); self.mode = 'lost-approval'; await self.pending()
        original = self.record('approval'); self.assertEqual(original.state, 'unknown')
        path = self.db.path; self.db.close(); self.db = store.Store(path); self.addCleanup(self.db.close)
        self.mode = ''; self.now += 1
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); self.assertEqual(len(self.outward), 2)
        self.assertEqual(self.record('approval').pin, original.pin); self.assert_approved_only()

    async def test_acked_repeated_call_reads_current_authority_without_new_post_or_signature(self):
        await self.setup_outward()
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); original = self.record('approval').pin
        self.now += 1
        result = await self.instance().run(self.request_id)
        self.assertEqual(result.status, 'record_verified'); self.assertEqual(len(self.outward), 2)
        self.assertEqual(self.record('approval').pin, original); self.assert_approved_only()

    async def test_unknown_approval_exact_get_needs_original_signature_and_full_nonpartial_packet(self):
        await self.setup_outward(); self.mode = 'lost-approval'; await self.pending(); self.mode = ''
        for mode in ('empty', 'forged', 'alternate', 'partial', 'saturated'):
            with self.subTest(mode=mode):
                self.readback_mode = mode; await self.pending(self.instance())
                self.assertEqual(len(self.outward), 2); self.assertEqual(self.record('approval').state, 'unknown')
        self.assert_approved_only()

    async def test_actual_native_scope_or_listing_revocation_blocks_outward_io(self):
        await self.setup_outward()
        for mode in ('scope', 'partial', 'issuer'):
            with self.subTest(mode=mode):
                self.https.grant, self.https.partial, self.https.issuer_present = 1, False, True
                if mode == 'scope': self.https.grant = 0
                elif mode == 'partial': self.https.partial = True
                else: self.https.issuer_present = False
                await self.pending(); self.assertEqual(self.outward, [])

    async def test_wrong_selected_issuer_profile_never_posts_or_borrows_subject_app(self):
        await self.setup_outward()
        path = Path(self.client.config_dir) / 'config.json'
        base.write_owner_only(path, json.dumps({'apps': [{'appId': SUBJECT_APP}]}))
        await self.pending(); self.assertEqual(self.outward, [])

    async def test_wrong_local_owner_signer_or_mirror_key_never_posts(self):
        await self.setup_outward()
        for path, original_key, wrong_key in ((self.env.mirror_env, base.MIRROR_KEY, base.OWNER_KEY),
                (self.env.signer_env, base.OWNER_KEY, base.MIRROR_KEY)):
            with self.subTest(file_kind='mirror' if path == self.env.mirror_env else 'owner'):
                original = Path(path).read_text()
                base.write_owner_only(path, original.replace(original_key, wrong_key))
                await self.pending(); self.assertEqual(self.outward, [])
                base.write_owner_only(path, original)

    async def test_reclaimed_stable_scope_and_owner_policy_revocation_cannot_reuse_click(self):
        await self.setup_outward()
        self.mirror_change(lambda body: body['feishu']['bindings'][0].update(claimed_at=self.now + 1))
        await self.pending(); self.assertEqual(self.outward, [])
        self.policy(SUBJECT, cards.discovery_tests.SUBJECT_OWNER_KEY, {'feishu': {}})
        await self.pending(); self.assertEqual(self.outward, [])

    async def test_subject_conditional_or_changed_empty_oa_owner_cannot_borrow_original_app_policy(self):
        await self.setup_outward()
        for mode in ('conditional', 'owner'):
            with self.subTest(mode=mode):
                self.world.relay_events[:] = [e for e in self.world.relay_events if not (e['kind'] == 0 and e['pubkey'] == SUBJECT)]
                self.world.relay_events.append(claims.profile(cards.discovery_tests.SUBJECT_KEY,
                    cards.discovery_tests.SUBJECT_OWNER_KEY if mode == 'conditional' else base.OWNER_KEY,
                    self.now, 'kind=9' if mode == 'conditional' else ''))
                await self.pending(); self.assertEqual(self.outward, [])

    async def test_sql_revoke_during_member_native_io_stops_approval_and_ack(self):
        await self.setup_outward(); self.mode = 'blocked-member'
        task = asyncio.create_task(self.instance().run(self.request_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,))
        finally: self.release.set()
        result = await task; self.assertEqual(result.status, 'pending')
        self.assertEqual([e['kind'] for e in self.outward], [9000]); self.assertEqual(self.record('member').state, 'unknown')

    async def test_owner_policy_change_during_member_io_stops_approval(self):
        await self.setup_outward(); self.mode = 'blocked-member'
        task = asyncio.create_task(self.instance().run(self.request_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
            self.policy(SUBJECT, cards.discovery_tests.SUBJECT_OWNER_KEY, {'feishu': {'app_id': 'cli_changed'}})
        finally: self.release.set()
        result = await task; self.assertEqual(result.status, 'pending')
        self.assertEqual([e['kind'] for e in self.outward], [9000])

    async def test_protected_mirror_change_during_exact_approval_get_blocks_ack(self):
        await self.setup_outward(); self.mode = 'lost-approval'; await self.pending(); self.mode = ''
        # Hook SQL must run on owning loop, never the low HTTP worker. Gate
        # the physical GET in the worker and mutate SQL on this async thread.
        self.read_hook = lambda: (self.entered.set(), self.release.wait(10))
        task = asyncio.create_task(self.instance().run(self.request_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
            base.write_owner_only(self.env.mirror_env, self.env.mirror_env.read_text() + '\nchanged-after-get=1\n')
        finally: self.release.set()
        result = await task; self.assertEqual(result.status, 'pending')
        self.assertEqual(self.record('approval').state, 'unknown'); self.assertEqual(len(self.outward), 2)

    async def test_current_sql_decision_revoked_during_exact_approval_get_cannot_ack_original_event(self):
        await self.setup_outward(); self.mode = 'lost-approval'; await self.pending(); self.mode = ''
        self.read_hook = lambda: (self.entered.set(), self.release.wait(10))
        task = asyncio.create_task(self.instance().run(self.request_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 5))
            self.db.conn.execute("UPDATE join_request SET status='denied' WHERE request_id=?", (self.request_id,))
        finally: self.release.set()
        result = await task; self.assertEqual(result.status, 'pending')
        self.assertEqual(self.record('approval').state, 'unknown'); self.assertEqual(len(self.outward), 2)

    async def test_forged_or_stale_pinned_roster_is_pending_and_never_posts(self):
        await self.setup_outward()
        for mode in ('forged', 'stale'):
            with self.subTest(mode=mode):
                self.roster_mode = mode; await self.pending(); self.assertEqual(self.outward, [])

    async def test_repeated_cancel_reaps_dispatched_member_io_holds_lock_unknown_and_no_second_post(self):
        await self.setup_outward(); self.mode = 'blocked-member'; value = self.instance()
        task = asyncio.create_task(value.run(self.request_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 5)); task.cancel(); await asyncio.sleep(0); task.cancel()
            self.assertFalse(task.done()); self.assertEqual(self.record('member').state, 'unknown')
            duplicate = asyncio.create_task(value.run(self.request_id)); await asyncio.sleep(.01)
            self.assertEqual(len(self.outward), 1)
        finally: self.release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        result = await duplicate; self.assertIn(result.status, ('pending', 'record_verified'))
        self.assertEqual(sum(e['kind'] == 9000 for e in self.outward), 1)
        self.assert_approved_only()

    async def test_expired_claim_cannot_dispatch_foreign_producer(self):
        await self.setup_outward(); self.now += 100000
        await self.pending(); self.assertEqual(self.outward, [])

    async def test_actual_protected_own_catalog_presence_holds_foreign_producer_even_for_same_public_subject(self):
        await self.setup_outward()
        cards.FallbackOnboardingTests.own_subject_catalog(self, blocked=True)
        await self.pending(); self.assertEqual(self.outward, [])

    async def test_signed_subject_policy_mixed_forgery_or_saturation_is_not_permission_to_post(self):
        await self.setup_outward()
        for mode in ('forged', 'saturated', 'partial'):
            with self.subTest(mode=mode):
                self.query_mode = mode; await self.pending(); self.assertEqual(self.outward, [])

    async def test_native_card_app_chat_or_generation_changed_after_sql_approve_cannot_publish(self):
        await self.setup_outward()
        view = self.https.views[self.row['card_message_id']]
        original = copy.deepcopy(view)
        for mode in ('app', 'chat', 'generation'):
            with self.subTest(mode=mode):
                view.clear(); view.update(copy.deepcopy(original))
                if mode == 'app': view['sender']['id'] = 'cli_other'
                elif mode == 'chat': view['chat_id'] = 'oc_other'
                else: view['body']['content'] = view['body']['content'].replace('卡片 1', '卡片 2')
                await self.pending(); self.assertEqual(self.outward, [])


class OutwardJournalTests(OutwardTests):
    # Do not inherit the producer cases: this class adds only the durable seam
    # methods below; loader construction is restricted explicitly in runner.
    async def reserve(self, stage, event=None):
        self.assertTrue(callable(getattr(self.db, 'reserve_fallback_outward', None)), 'dedicated outward journal API is missing')
        marker = self.db.fallback_provenance(self.request_id)
        return self.db.reserve_fallback_outward(self.request_id, stage, event or self.member_event(),
            marker.scope_hash, FILES, now=self.now)

    async def test_journal_reserve_unknown_is_immutable_across_sqlite_reopen(self):
        await self.setup_outward(); event = self.member_event(); first = await self.reserve('member', event)
        self.assertTrue(first.created); self.assertEqual(first.record.state, 'reserved')
        self.assertTrue(self.db.mark_fallback_outward_unknown(self.request_id, 'member', event['id'], now=self.now))
        path = self.db.path; self.db.close(); self.db = store.Store(path); self.addCleanup(self.db.close); self.now += 1
        again = await self.reserve('member', event); self.assertFalse(again.created)
        self.assertEqual(again.record.pin, first.record.pin); self.assertEqual(again.record.state, 'unknown')
        changed = gs.sign_event(base.OWNER_KEY, 9000, event['tags'], '', self.now)
        with self.assertRaises(store.StoreError): await self.reserve('member', changed)
        self.assertEqual(self.db._fallback_outward_event(again.record.pin), event)

    async def test_journal_ack_requires_original_id_and_current_marker_first_decision_scope(self):
        await self.setup_outward(); event = self.member_event(); await self.reserve('member', event)
        self.assertFalse(self.db.ack_fallback_outward(self.request_id, 'member', event['id'], digest(event), now=self.now))
        self.db.mark_fallback_outward_unknown(self.request_id, 'member', event['id'], now=self.now)
        self.assertFalse(self.db.ack_fallback_outward(self.request_id, 'member', 'f' * 64, digest(event), now=self.now))
        self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,))
        self.assertFalse(self.db.ack_fallback_outward(self.request_id, 'member', event['id'], digest(event), now=self.now))
        self.assertEqual(self.record('member').state, 'unknown')

    async def test_journal_reserved_reopen_is_get_only_never_first_dispatch_or_new_signature(self):
        await self.setup_outward(); event = self.member_event(); await self.reserve('member', event)
        path = self.db.path; self.db.close(); self.db = store.Store(path); self.addCleanup(self.db.close)
        await self.pending(self.instance()); self.assertEqual(self.outward, [])
        self.assertEqual(self.record('member').state, 'reserved')
        self.assertEqual(self.db._fallback_outward_event(self.record('member').pin), event)

    async def test_journal_wrong_signer_scope_subject_app_and_invalid_signature_are_rejected(self):
        await self.setup_outward()
        events = [gs.sign_event(base.MIRROR_KEY, 9000, self.member_event()['tags'], '', self.now),
            gs.sign_event(base.OWNER_KEY, 9000, [['h', claims.OTHER_CHANNEL], ['p', SUBJECT], ['role', 'bot']], '', self.now),
            self.approval_event(app_id=base.AGENT_APP)]
        bad = self.member_event(); bad['sig'] = '0' * 128; events.append(bad)
        for event in events:
            with self.subTest(kind=event['kind']):
                with self.assertRaises(store.StoreError): await self.reserve('member' if event['kind'] == 9000 else 'approval', event)

    async def test_journal_stores_only_metadata_original_signature_no_body_card_or_callback(self):
        await self.setup_outward(); event = self.approval_event(); reservation = await self.reserve('approval', event)
        self.assertEqual(self.db._fallback_outward_event(reservation.record.pin), event)
        columns = [r[1] for r in self.db.conn.execute('PRAGMA table_info(fallback_outward)')]
        self.assertFalse({'body', 'content', 'token', 'secret', 'card_message_id', 'decision_event_id'} & set(columns))
        data = repr([tuple(r) for r in self.db.conn.execute('SELECT * FROM fallback_outward')])
        self.assertNotIn(self.row['card_message_id'], data); self.assertNotIn('native-owner-click-1', data)
        self.assertNotIn(base.MIRROR_KEY, data); self.assertNotIn(base.OWNER_KEY, data)

    async def test_journal_populated_schema_eight_upgrade_preserves_all_29_old_tables_fk_and_unknown_pin(self):
        await self.setup_outward()
        helper = cards.FallbackSchemaTests()
        helper.setUp(); self.addCleanup(helper.doCleanups)
        path, _, original_pin = helper.seed_old_seven()
        # Advance the immutable, populated schema-seven fixture by exactly the
        # frozen v8 addition. Store(path) would migrate it through current v12.
        with sqlite3.connect(path) as old:
            old.executescript(store._UPGRADE_V8_SCHEMA)
            old.execute('PRAGMA user_version=8')
            names = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            # Seed the schema8 marker/first native owner decision, in addition
            # to all historical 28 tables and original UNKNOWN publication.
            # The historic fixtures already occupy this channel/chat. Preserve
            # their rows; retire only our new synthetic binding after its real
            # decision so both distinct historical bindings can coexist safely.
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,))
            for name in ('binding', 'agent', 'join_request', 'join_transport', 'join_decision', 'fallback_request'):
                rows = [tuple(r) for r in self.db.conn.execute(f'SELECT * FROM "{name}"')]
                for row in rows:
                    old.execute(f'INSERT INTO "{name}" VALUES({",".join("?" for _ in row)})', row)
            before = {name: [tuple(r) for r in old.execute(f'SELECT * FROM "{name}"')] for name in names}
        self.assertEqual(len(before), 29); self.assertTrue(all(before.values()))
        # Build actual old schema8 rather than changing user_version of latest.
        frozen = store._SCHEMA_V7 + store._UPGRADE_V8_SCHEMA
        baseline = self.tmp / 'schema8.db'
        with sqlite3.connect(baseline) as raw:
            raw.executescript(frozen)
            for name, rows in before.items():
                raw.executemany(f'INSERT INTO "{name}" VALUES({",".join("?" for _ in rows[0])})', rows)
            raw.execute('PRAGMA user_version=8')
        baseline.chmod(0o600)
        with store.Store(baseline) as migrated:
            self.assertEqual(migrated.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
            for name, rows in before.items(): self.assertEqual([tuple(r) for r in migrated.conn.execute(f'SELECT * FROM "{name}"')], rows)
            self.assertEqual(migrated.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            original = migrated.approval_publication(cards.journal.REQUEST)
            self.assertEqual(original.pin, original_pin); self.assertEqual(original.state, 'unknown')
            self.assertEqual(migrated.conn.execute('SELECT count(*) FROM fallback_outward').fetchone()[0], 0)

    async def test_journal_original_schema_one_through_eight_bytes_remain_exact_control(self):
        for name, expected in FROZEN_SQL.items():
            self.assertEqual(hashlib.sha256(getattr(store, name).encode()).hexdigest(), expected, name)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(OutwardTests))
    for name in loader.getTestCaseNames(OutwardJournalTests):
        if name.startswith('test_journal_'): suite.addTest(OutwardJournalTests(name))
    return suite
