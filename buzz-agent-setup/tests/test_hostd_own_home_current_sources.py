"""PRIVATE AST-only draft: actual post-CAS current-source reads, never admission.

The SQLite process tuple is labelled journal-shape metadata, NOT observed process
ownership. Only low relay/native HTTP and existing portable AES are synthetic.
No process/AgentSpec readiness, file write or activation is claimed by receipts.
"""
import asyncio
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
import test_hostd_own_admission_discovery as initial
import test_hostd_remote_proofs as base
import test_hostd_remote_mapping as keys
from hostd import agent_catalog, store
from hostd.agent_signed_reads import OwnAgentReader
from hostd.remote_proofs import RemoteProofs
import buzz_feishu_group_sync as gs


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


class PostWorld(initial.AdmissionWorld):
    def __init__(self, case):
        super().__init__(case)
        # Real RemoteProofs retains its actual message-scope guard; no fabricated
        # returned capabilities or private proof method is used to bypass it.
        self.scopes.add('im:message:send_as_bot')
        self.before_record = self.actual_record
        self.sql = store.Store(self.root / 'sql' / 'state.db')
        case.addCleanup(self.sql.close)
        self.sql.register_agent(base.PUB, owner_pubkey=base.OWNER, app_id=base.APP,
            config_path=str(self.env), now=self.now)
        self.admission_id = 'a' * 64
        self.original_snapshot = None
        self.on_second_member = None

    def request(self, *args, **kwargs):
        if args[4] == '/open-apis/im/v1/chats/oc_second/members/list':
            self.assertEqual((args[0], Path(args[1]), Path(args[2])), (base.APP, self.cfg, self.data))
            self.assertEqual(args[3], 'GET'); self.assertIn(kwargs.get('chat_id'), (None, 'oc_second'))
            self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
            if self.on_second_member:
                action, self.on_second_member = self.on_second_member, None
                action()
            return {'ok': True, 'identity': 'bot', 'data': {'users': [],
                'bots': [{'member_id': 'ou_own', 'app_id': base.APP}], 'user_total': 0, 'bot_total': 1,
                'truncations': [], 'has_more': False, 'page_token': ''}}
        result = super().request(*args, **kwargs)
        if args[4] == '/open-apis/application/v6/scopes':
            result['data']['scopes'] = [
                {'scope_name': name, 'grant_status': self.scope_grant_status, 'scope_type': 'tenant'}
                for name in sorted(self.scopes)]
        return result

    async def seed(self, *, source_kind='own_approval', two=False, aggregate_protectedfiles=False):
        if two:
            self.second_target()
            self.chat_rows.append({'chat_id': 'oc_second', 'name': 'SYNTHETIC_SECOND_CHAT'})
        discovered = await self.adapter().discover()
        self.assertEqual(discovered.status, 'discovered', 'authentic initial signed/native discovery control')
        self.assertEqual(len(discovered.candidates), 2 if two else 1)
        candidate = discovered.candidates[0]
        proposed = tuple(row.channel_id for row in discovered.candidates)
        # This is a synthetic legitimate physical CAS prerequisite, NOT code
        # under test nor a DTO allowlist substitution. Product verifier is READONLY.
        raw = re.sub(r'^BUZZ_ACP_CHANNELS=.*$', 'BUZZ_ACP_CHANNELS=' + ','.join(proposed),
                     self.env.read_text(), flags=re.M).encode()
        self.owned(self.env, raw)
        self.catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_path)
        self.actual_record = self.catalog.records[0]
        self.assertEqual(self.actual_record.channels, proposed)
        self.reader = OwnAgentReader(self.actual_record, origin=base.ORIGIN, relay_pubkey=base.PIN,
            trusted_relays=(base.ORIGIN,), clock=lambda: self.now, http=self.http)
        self.rows = tuple({'channel_id': item.channel_id, 'chat_id': item.chat_id,
            'chat_ref': item.chat_ref, 'source_kind': source_kind,
            'approval_id': item.approval_id, 'approval_hash': item.approval_hash,
            'mirror_pubkey': item.mirror_pubkey, 'mirror_owner_pubkey': item.mirror_owner_pubkey,
            'claimed_at': item.claimed_at, 'claim_event_id': item.claim_event_id,
            'policy_event_id': item.agent_policy_id, 'roster_event_id': item.roster_event_id,
            'authorization_hash': digest({'approval_id': item.approval_id,
                'approval_hash': item.approval_hash, 'claimed_at': item.claimed_at})} for item in discovered.candidates)
        protected_hash = digest(raw)
        if aggregate_protectedfiles:
            protected_hash = digest({
                'env_sha256': digest(raw),
                'prompt_sha256': digest(self.prompt.read_bytes()),
                'responsible_sha256': digest(self.responsible.read_bytes()),
            })
        self.reserve_args = dict(admission_id=self.admission_id, agent_id=base.PUB,
            snapshot_hash=digest(self.rows), scope_hash=digest({'subject': base.PUB, 'channels': proposed}),
            protectedfiles_hash=protected_hash, catalog_hash=candidate.catalog_sha256,
            legacy_join_hash=candidate.legacy_join_sha256, profile_hash=candidate.profile_sha256,
            env_before_hash=candidate.env_sha256, env_after_hash=digest(raw),
            # These fields validate persistence shape only; SourceResult must
            # never call them observed AgentSpec/process/files authority.
            agent_spec_hash=digest({'own_unit': self.actual_record.unit}), prior_channels_hash=digest([]),
            proposed_channels_hash=digest(list(proposed)), approval_set_hash=digest([row.approval_id for row in discovered.candidates]),
            old_process=store.RestartProcess(101, 1001, 'a' * 32), channels=self.rows, now=self.now)
        reservation = self.sql.reserve_own_home_admission(**self.reserve_args)
        self.assertTrue(reservation.created)
        self.original_snapshot = reservation.record
        self.query_hook = None
        self.api_calls.clear(); self.queries.clear(); self.wire_calls.clear()
        self.native_entered.clear(); self.native_release.clear()
        self.pinned_files = {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.env, self.catalog_path, self.legacy_path, self.cfg / 'config.json', self.prompt, self.responsible)}
        self.sql_before = self.sql.conn.total_changes

    def verifier(self, *, clock=None):
        self.assertIsNotNone(importlib.util.find_spec('hostd.own_home_current_sources'),
            'missing actual readonly post-CAS source verifier; do not reuse old zero-channel discovery')
        module = importlib.import_module('hostd.own_home_current_sources')
        return module.OwnHomeCurrentSources(self.sql, self.catalog_path, self.legacy_path,
            reader=self.reader, bot=self.bot, clock=clock or (lambda: self.now))

    def no_effect(self):
        self.no_writes()
        self.assertEqual(self.sql.conn.total_changes, self.sql_before, 'source verifier cannot mutate SQL')
        self.assertEqual(self.sql.own_home_admission(self.admission_id), self.original_snapshot)
        self.assertIsNone(self.sql.own_home_admission_restart(self.admission_id))
        for table in ('binding', 'agent_chat', 'remote_grant', 'effect_plan', 'restart_operation'):
            self.assertEqual(self.sql.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def retained_files(self):
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.pinned_files}, self.pinned_files)

    def renew_claim_and_roster(self, channel=base.CHANNEL):
        row = next(item for item in self.rows if item['channel_id'] == channel)
        old_claim = next(event for event in self.directory if event['id'] == row['claim_event_id'])
        old_roster = next(event for event in self.directory if event['id'] == row['roster_event_id'])
        body = json.loads(old_claim['content'])
        for binding in body['feishu']['bindings']:
            binding['heartbeat'] = self.now + 1
        self.now += 1
        new_claim = gs.sign_event(keys.FOREIGN_OWNER_KEY, 30177, old_claim['tags'],
                                  json.dumps(body), self.now)
        new_roster = gs.sign_event(keys.RELAY_KEY, 39002, old_roster['tags'], '', self.now)
        return row, old_claim, old_roster, new_claim, new_roster


class OwnHomeCurrentSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = PostWorld(self)
        await self.w.seed()

    def pending(self, result):
        self.assertEqual(result.status, 'pending'); self.assertIsNone(result.receipt)
        self.assertIn('怎么解决', result.notice); self.assertIn('复制给 AI', result.notice)
        for key in ('grant_activated', 'sending_ready', 'physical_verified', 'live_verified'):
            self.assertFalse(result.readback()[key])
        for secret in (keys.KEY, 'SYNTHETIC_APP_SECRET', 'SYNTHETIC_PRIVATE_PROMPT', 'Traceback'):
            self.assertNotIn(secret, repr(result))
        self.w.no_effect()

    async def test_control_real_remoteproofs_accepts_actual_reloaded_protected_channels(self):
        w = self.w
        result = await RemoteProofs(w.catalog_path, w.legacy_path, record=w.actual_record,
            reader=w.reader, bot=w.bot, clock=lambda: w.now).verify(w.target)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.approval_id, w.rows[0]['approval_id'])
        w.no_effect(); w.retained_files()

    async def test_control_unchanged_zero_channel_discovery_rejects_post_cas_actual_record(self):
        self.assertEqual((await self.w.adapter().discover()).status, 'pending')
        self.w.no_effect(); self.w.retained_files()

    async def test_original_admission_and_actual_current_signed_native_sources_verify_without_effect(self):
        result = await self.w.verifier().verify(self.w.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.receipt.admission_id, self.w.admission_id)
        self.assertEqual(result.receipt.snapshot_hash, self.w.original_snapshot.snapshot_hash)
        self.assertEqual(result.receipt.proposed_channels_hash, self.w.original_snapshot.proposed_channels_hash)
        self.assertEqual(result.receipt.channel_ids, (base.CHANNEL,))
        self.assertEqual(result.receipt.approval_ids, (self.w.rows[0]['approval_id'],))
        self.assertLessEqual(result.receipt.expires_at, self.w.now + 30)
        for key in ('grant_activated', 'sending_ready', 'physical_verified', 'live_verified'):
            self.assertFalse(result.readback()[key])
        self.w.no_effect(); self.w.retained_files()

    async def test_complete_two_channel_original_source_set_is_observed_without_partial_receipt(self):
        fresh = PostWorld(self)
        await fresh.seed(two=True)
        result = await fresh.verifier().verify(fresh.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.receipt.channel_ids, tuple(row['channel_id'] for row in fresh.rows))
        self.assertEqual(result.receipt.approval_ids, tuple(row['approval_id'] for row in fresh.rows))
        fresh.no_effect(); fresh.retained_files()

    async def test_first_channel_revoked_while_second_native_source_read_runs_rejects_whole_set(self):
        fresh = PostWorld(self)
        await fresh.seed(two=True)
        def revoke_first():
            fresh.now += 1
            roles = dict(fresh.roles); roles[base.PUB] = 'member'
            fresh.directory.append(gs.sign_event(keys.RELAY_KEY, 39002,
                [['d', base.CHANNEL]] + [['p', pub, '', role] for pub, role in roles.items()], '', fresh.now))
        fresh.on_second_member = revoke_first
        result = await fresh.verifier().verify(fresh.admission_id)
        self.assertEqual(result.status, 'pending'); self.assertIsNone(result.receipt)
        self.assertIsNone(fresh.on_second_member, 'negative must reach second channel native IO')
        fresh.no_effect(); fresh.retained_files()

    async def test_extra_or_missing_actual_env_channel_rejects_partial_set_receipt(self):
        for value in ('', base.CHANNEL + ',00000000-0000-0000-0000-000000000002'):
            with self.subTest(value=value):
                self.w.owned(self.w.env, re.sub(r'^BUZZ_ACP_CHANNELS=.*$', 'BUZZ_ACP_CHANNELS=' + value,
                                               self.w.env.read_text(), flags=re.M))
                self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_changed_protected_profile_or_catalog_identity_is_pending(self):
        self.w.owned(self.w.cfg / 'config.json', json.dumps({'apps': [{'appId': 'cli_foreign', 'name': 'local'}]}))
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_retired_current_local_sql_agent_never_returns_source_receipt(self):
        self.w.sql.conn.execute("UPDATE agent SET status='retired' WHERE pubkey=?", (base.PUB,))
        self.w.sql_before = self.w.sql.conn.total_changes
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_original_approval_missing_even_with_new_same_scope_approval_is_pending(self):
        self.w.mutate_approval(request_id='JOIN-5678abcd',
            decision_event_sha256=hashlib.sha256(b'evt_other_decision').hexdigest())
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_forged_original_get_and_valid_current_directory_cannot_ack_source(self):
        original = self.w.rows[0]['approval_id']
        def corrupt(filters, rows):
            if any(original in item.get('ids', []) for item in filters):
                for row in rows:
                    if row['id'] == original: row['sig'] = '0' * 128
            return rows
        self.w.query_hook = corrupt
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_original_policy_missing_or_current_app_revoked_is_pending(self):
        self.w.policy_app = 'cli_revoked'; self.w.directory = self.w.metadata()
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_changed_stable_claim_cannot_repin_original_admission(self):
        self.w.claims[0]['claimed_at'] += 1; self.w.directory = self.w.metadata()
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_same_stable_claim_heartbeat_current_head_preserves_original_pin(self):
        old_claim = next(e for e in self.w.directory if e['id'] == self.w.rows[0]['claim_event_id'])
        body = json.loads(old_claim['content']); body['feishu']['bindings'][0]['heartbeat'] = base.NOW + 1
        self.w.now += 1
        renewed = gs.sign_event(keys.FOREIGN_OWNER_KEY, 30177, old_claim['tags'], json.dumps(body), self.w.now)
        self.w.directory.append(renewed)
        result = await self.w.verifier().verify(self.w.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertNotEqual(renewed['id'], self.w.rows[0]['claim_event_id'])
        self.w.no_effect(); self.w.retained_files()

    async def test_same_own_unconditional_oa_regenerated_profile_does_not_change_original_approval(self):
        self.w.now += 1
        regenerated = gs.sign_event(keys.KEY, 0, [keys.auth(keys.KEY, keys.OWNER_KEY)],
            json.dumps({'channels': [base.CHANNEL], 'name': 'Synthetic'}), self.w.now)
        self.w.directory.append(regenerated)
        result = await self.w.verifier().verify(self.w.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.receipt.approval_ids, (self.w.rows[0]['approval_id'],))
        self.w.no_effect(); self.w.retained_files()

    async def test_regenerated_profile_with_changed_oa_owner_is_pending(self):
        self.w.now += 1
        self.w.directory.append(gs.sign_event(keys.KEY, 0, [keys.auth(keys.KEY, keys.FOREIGN_OWNER_KEY)], '{}', self.w.now))
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_native_partial_members_or_missing_ownbot_is_pending(self):
        for incomplete, absent in ((True, False), (False, True)):
            with self.subTest(incomplete=incomplete, absent=absent):
                self.w.members_incomplete, self.w.bot_absent = incomplete, absent
                self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_native_permission_grant_zero_is_pending_not_fabricated_capability(self):
        self.w.scope_grant_status = 0
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_source_revocation_during_actual_native_io_is_pending(self):
        def revoke():
            self.w.policy_app = 'cli_revoked'; self.w.directory = self.w.metadata()
        self.w.on_member = revoke
        self.pending(await self.w.verifier().verify(self.w.admission_id))
        self.assertTrue(self.w.native_entered.is_set())

    async def test_catalog_changes_during_native_io_is_pending(self):
        self.w.on_member = lambda: self.w.owned(self.w.catalog_path, json.dumps(dict(self.w.doc, agents=[])))
        self.pending(await self.w.verifier().verify(self.w.admission_id))
        self.assertTrue(self.w.native_entered.is_set())

    async def test_actual_local_sql_retirement_during_native_io_invalidates_final_scope(self):
        # Send only actual SQL to the owning loop; no connection use in HTTPthread.
        import concurrent.futures
        loop = asyncio.get_running_loop()
        def retire():
            answer = concurrent.futures.Future()
            def sql_retire():
                try:
                    self.w.sql.conn.execute("UPDATE agent SET status='retired' WHERE pubkey=?", (base.PUB,))
                    self.w.sql_before = self.w.sql.conn.total_changes
                    answer.set_result(True)
                except Exception as exc: answer.set_exception(exc)
            loop.call_soon_threadsafe(sql_retire)
            self.assertTrue(answer.result(timeout=5))
        self.w.on_member = retire
        self.pending(await self.w.verifier().verify(self.w.admission_id))
        self.assertTrue(self.w.native_entered.is_set())

    async def test_user_only_native_scopes_cannot_authorize_bot_source_readback(self):
        original = self.w.request
        def user_scopes(*args, **kwargs):
            payload = original(*args, **kwargs)
            if args[4] == '/open-apis/application/v6/scopes':
                for row in payload['data']['scopes']: row['scope_type'] = 'user'
            return payload
        # Replace low HTTPS response ONLY, not native client or RemoteProofs.
        self.w.request = user_scopes
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_elapsed_complete_set_proof_window_during_native_io_is_pending(self):
        self.w.on_member = lambda: setattr(self.w, 'now', base.NOW + 31)
        self.pending(await self.w.verifier().verify(self.w.admission_id))

    async def test_local_or_remote_source_kind_without_actual_source_adapter_stays_pending(self):
        for kind in ('local_grant', 'remote_grant'):
            with self.subTest(kind=kind):
                fresh = PostWorld(self)
                await fresh.seed(source_kind=kind)
                result = await fresh.verifier().verify(fresh.admission_id)
                self.assertEqual(result.status, 'pending'); self.assertIsNone(result.receipt)
                self.assertIn('怎么解决', result.notice); self.assertIn('复制给 AI', result.notice)
                fresh.no_effect(); fresh.retained_files()

    async def test_unknown_admission_has_get_only_source_readback_no_effect_replay(self):
        self.assertTrue(self.w.sql.mark_own_home_admission_unknown(self.w.admission_id,
            snapshot_hash=self.w.original_snapshot.snapshot_hash, now=self.w.now))
        self.w.original_snapshot = self.w.sql.own_home_admission(self.w.admission_id)
        self.w.sql_before = self.w.sql.conn.total_changes
        result = await self.w.verifier().verify(self.w.admission_id)
        self.assertEqual(result.status, 'verified')
        self.w.no_effect(); self.w.retained_files()

    async def test_repeated_cancel_joins_dispatched_native_source_io(self):
        self.w.native_block = True
        task = asyncio.create_task(self.w.verifier().verify(self.w.admission_id))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.native_entered.wait, 240))
            task.cancel(); await asyncio.sleep(.01); task.cancel(); await asyncio.sleep(.01)
            self.assertFalse(task.done())
            self.w.native_release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.w.no_effect(); self.w.retained_files()
        finally:
            self.w.native_release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_aggregate_protectedfiles_snapshot_hash_is_metadata_not_env_after_hash(self):
        fresh = PostWorld(self)
        await fresh.seed(aggregate_protectedfiles=True)
        self.assertNotEqual(fresh.original_snapshot.protectedfiles_hash,
                            fresh.original_snapshot.env_after_hash)
        result = await fresh.verifier().verify(fresh.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.receipt.env_hash, fresh.original_snapshot.env_after_hash)
        fresh.no_effect(); fresh.retained_files()

    async def test_control_fresh_heartbeat_and_roster_remain_valid_with_original_pins_intact(self):
        fresh = PostWorld(self)
        await fresh.seed()
        original_rows = fresh.sql.own_home_admission_channels(fresh.admission_id)
        row, old_claim, old_roster, new_claim, new_roster = fresh.renew_claim_and_roster()
        fresh.directory.remove(old_claim); fresh.directory.remove(old_roster)
        fresh.directory.extend((old_claim, old_roster, new_claim, new_roster))
        current = await RemoteProofs(fresh.catalog_path, fresh.legacy_path,
            record=fresh.actual_record, reader=fresh.reader, bot=fresh.bot,
            clock=lambda: fresh.now).verify(fresh.target)
        self.assertEqual(current.status, 'verified', 'fresh current heartbeat and roster must be independently valid')
        result = await fresh.verifier().verify(fresh.admission_id)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(fresh.sql.own_home_admission_channels(fresh.admission_id), original_rows)
        self.assertEqual(result.receipt.claim_ids, (row['claim_event_id'],))
        self.assertEqual(result.receipt.roster_ids, (row['roster_event_id'],))
        self.assertNotEqual(result.receipt.current_claim_ids, result.receipt.claim_ids)
        self.assertEqual(result.receipt.heartbeats, (new_claim['created_at'],))
        fresh.no_effect(); fresh.retained_files()

    async def test_original_claim_and_roster_removed_during_later_native_wait_are_refetched(self):
        fresh = PostWorld(self)
        await fresh.seed(two=True)
        original_rows = fresh.sql.own_home_admission_channels(fresh.admission_id)
        base_row, old_claim, old_roster, new_claim, new_roster = fresh.renew_claim_and_roster()
        # Leave originals present for the first original-ID GET, then remove them
        # during the second whole-set pass after its current native wait begins.
        removed = set()
        fresh.original_sources_removed = False
        pinned_ids = {item[name] for item in fresh.rows for name in
                      ('approval_id', 'policy_event_id', 'claim_event_id', 'roster_event_id')}
        original_reads_before_loss = []
        original_reads_after_loss = []
        original_returned_before_loss = set()
        original_get_armed = []

        def remove_after_original_reads():
            fresh.directory.remove(old_claim)
            fresh.directory.remove(old_roster)
            fresh.directory.extend((new_claim, new_roster))
            removed.update((base_row['claim_event_id'], base_row['roster_event_id']))
            fresh.original_sources_removed = True

        def filter_lost_originals(filters, rows):
            requested = {event_id for query in filters for event_id in query.get('ids', [])}
            returned = {event.get('id') for event in rows
                        if type(event) is dict and type(event.get('id')) is str}
            if requested & pinned_ids:
                (original_reads_after_loss if removed else original_reads_before_loss).append(requested & pinned_ids)
                if not removed:
                    original_returned_before_loss.update(returned & pinned_ids)
                    # Arm only from the actual complete original-ID response.
                    # This lower signed-query boundary is independent of how
                    # many repeated native member reads a proof performs.
                    if requested == pinned_ids and returned == pinned_ids:
                        original_get_armed.append((requested.copy(), returned.copy()))
                        fresh.on_second_member = remove_after_original_reads
            if any('ids' in query for query in filters):
                return [event for event in rows if event['id'] not in removed]
            return rows

        fresh.query_hook = filter_lost_originals
        result = await fresh.verifier().verify(fresh.admission_id)
        self.assertEqual(original_get_armed, [(pinned_ids, pinned_ids)],
                         'arming requires a complete actual request and response for every original pin')
        self.assertTrue(fresh.original_sources_removed, 'removal must occur in the later native/second-pass wait')
        self.assertTrue(original_reads_before_loss, 'the initial original-ID GET must succeed before the source change')
        self.assertEqual(original_returned_before_loss, pinned_ids)
        self.assertEqual(result.status, 'pending', 'the original historical source GET must be repeated')
        self.assertIsNone(result.receipt)
        self.assertTrue(original_reads_after_loss, 'the original IDs must be GET again after the later await')
        current = await RemoteProofs(fresh.catalog_path, fresh.legacy_path,
            record=fresh.actual_record, reader=fresh.reader, bot=fresh.bot,
            clock=lambda: fresh.now).verify(fresh.target)
        self.assertEqual(current.status, 'verified', 'fresh same-stable-claim/roster proof remains valid')
        self.assertEqual(fresh.sql.own_home_admission_channels(fresh.admission_id), original_rows)
        fresh.no_effect(); fresh.retained_files()

    async def test_typed_clock_regression_at_native_to_source_boundary_is_not_erased_after_recovery(self):
        fresh = PostWorld(self)
        await fresh.seed()
        state = {'regress': False, 'sampled': False}
        def typed_clock():
            value = fresh.now
            if state['regress']:
                state['regress'] = False
                state['sampled'] = True
                fresh.now = base.NOW
            return value
        def regress_at_native_boundary():
            fresh.now = base.NOW - 1
            state['regress'] = True
        fresh.on_member = regress_at_native_boundary
        result = await fresh.verifier(clock=typed_clock).verify(fresh.admission_id)
        self.assertTrue(fresh.native_entered.is_set(), 'regression must be injected by actual native member GET')
        self.assertTrue(state['sampled'], 'the guarded RemoteProofs clock must sample the transient regressed value')
        self.assertEqual(fresh.now, base.NOW, 'fixture returns to current time after the regressed sample')
        self.assertEqual(result.status, 'pending', 'later current time cannot erase an observed typed-clock regression')
        self.assertIsNone(result.receipt)
        fresh.no_effect(); fresh.retained_files()
