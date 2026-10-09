"""Discovery before approval: real SQL, bot HTTPS and strict signed reads.

Only the lowest HTTPS transports and synthetic secret seam are injected. No
foreign profile, env, unit, private key, catalog or high-level proof is read.
"""
import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import test_hostd_claim_sync_app as claims
from hostd import bot_clients, http_pool, store
from hostd.safety import read_owned
from hostd.join_effects import Nip98Relay
from hostd.signed_reads import SignedReader, ReadFailure
import recovery_authority as authority

discovery = (importlib.import_module('hostd.fallback_discovery')
             if importlib.util.find_spec('hostd.fallback_discovery') else None)
base, gs = claims.base, claims.gs
setUpModule, tearDownModule = claims.setUpModule, claims.tearDownModule
SUBJECT_KEY, SUBJECT_OWNER_KEY = '9'.zfill(64), '8'.zfill(64)
SUBJECT, SUBJECT_OWNER = gs._signer_pubkey(SUBJECT_KEY), gs._signer_pubkey(SUBJECT_OWNER_KEY)
SUBJECT_APP, BINDING = 'cli_public_subject', 'local-binding'


class Response:
    will_close = False
    def __init__(self, value):
        self.status, self.body = 200, json.dumps(value).encode()
    def getheader(self, name, default=None): return default
    def read1(self, count):
        value, self.body = self.body[:count], self.body[count:]
        return value


class NativeBotHTTPS:
    def __init__(self, testcase):
        self.testcase, self.calls = testcase, []
        self.grant, self.partial, self.issuer_present = 1, False, True
        self.bot_hook = None
    def connect(self, host, *, timeout, context):
        self.testcase.assertEqual(host, 'open.feishu.cn')
        self.testcase.assertGreater(timeout, 0)
        self.testcase.assertNotEqual(context.verify_mode, 0)
        return self.Connection(self)
    class Connection:
        def __init__(self, server): self.server = server
        def request(self, method, path, body=None, headers=None):
            s = self.server
            s.calls.append((method, path))
            if path.endswith('/tenant_access_token/internal'):
                s.testcase.assertEqual(json.loads(body), {'app_id': base.AGENT_APP, 'app_secret': 'offline-synthetic'})
                value = {'code': 0, 'tenant_access_token': 'offline-only', 'expire': 100}
            elif path == '/open-apis/application/v6/scopes':
                value = {'code': 0, 'data': {'scopes': [dict(scope_name=n, scope_type='tenant', grant_status=s.grant)
                         for n in bot_clients.READ_SCOPE_GROUPS]}}
            else:
                u = urlsplit(path); params = parse_qs(u.query)
                s.testcase.assertEqual(method, 'GET')
                s.testcase.assertEqual(u.path, '/open-apis/im/v1/chats/' + base.CHAT + '/members/list')
                kind = params['member_types'][0]
                s.testcase.assertIn(kind, ('user', 'bot'))
                s.testcase.assertEqual(params['member_id_type'], ['union_id' if kind == 'user' else 'open_id'])
                if kind == 'bot' and s.bot_hook is not None:
                    hook, s.bot_hook = s.bot_hook, None
                    hook()
                bots = ([{'member_id': 'ou_sync', 'app_id': base.AGENT_APP}] if s.issuer_present else [])
                bots.append({'member_id': 'ou_publicsubject', 'app_id': SUBJECT_APP})
                value = {'code': 0, 'data': {'users': [{'member_id': 'on_localowner'}] if kind == 'user' else [],
                    'bots': bots if kind == 'bot' else [], 'has_more': False,
                    'truncations': ['bot'] if s.partial and kind == 'bot' else [],
                    ('user_total' if kind == 'user' else 'bot_total'): 1 if kind == 'user' else len(bots)}}
            self.response = Response(value)
        def getresponse(self): return self.response
        def close(self): pass


class FallbackDiscoveryTests(base.TmpCase, unittest.IsolatedAsyncioTestCase):
    assembly, seed = claims.RoundPublisher.assembly, claims.RoundPublisher.seed

    def setup_discovery(self):
        self.env, self.cfg, self.world, run = self.assembly(verified=False)
        self.seed(self.world, run, sync={'version': 1, 'app_id': base.AGENT_APP})
        self.now = int(self.world.clock.timestamp())
        self.subject_profile = claims.profile(SUBJECT_KEY, SUBJECT_OWNER_KEY, self.now - 10)
        self.subject_policy = gs.sign_event(SUBJECT_OWNER_KEY, 30177, [['d', SUBJECT]],
            json.dumps({'feishu': {'app_id': SUBJECT_APP}}), self.now - 10)
        self.world.relay_events += [self.subject_profile, self.subject_policy]
        selected = self.cfg['agents'][self.cfg['desk_pubkey']]
        self.db = store.Store(self.tmp / 'sql' / 'hostd.db'); self.addCleanup(self.db.close)
        self.db.reconcile_bindings([store.BindingRecord(BINDING, base.CHANNEL, base.CHAT, base.AGENT_APP,
            str(self.env.config), selected['lark_config_dir'], selected['lark_data_dir'], base.MIRROR_PK)], now=self.now)
        self.https = NativeBotHTTPS(self)
        self.pool = http_pool.HttpPool(connection_factory=self.https.connect)
        self.addCleanup(self.pool.close)
        # Explicit fake protected-secret seam; AES/decryption is outside this
        # read-only test. Preserve real HttpPool token/API/profile validation,
        # and keep the same tests executable with Python -S.
        data_dir = Path(selected['lark_data_dir']); data_dir.mkdir(parents=True, exist_ok=True)
        secret_file = base.write_owner_only(data_dir / 'offline-secret', 'offline-synthetic')
        def app_secret(app_id, config_dir, requested_data_dir):
            self.assertEqual((app_id, config_dir, requested_data_dir),
                (base.AGENT_APP, selected['lark_config_dir'], selected['lark_data_dir']))
            return read_owned(secret_file).decode()
        secret_module = types.ModuleType('hostd.secrets_store'); secret_module.app_secret = app_secret
        synthetic_secret = mock.patch.dict(sys.modules, {'hostd.secrets_store': secret_module})
        synthetic_secret.start(); self.addCleanup(synthetic_secret.stop)
        self.client = bot_clients.BotLarkCli(base.AGENT_APP, selected['lark_config_dir'], selected['lark_data_dir'],
            base_env={}, http_pool=self.pool, runner=lambda *a, **kw: self.fail('no CLI allowed'))
        self.clients = {base.AGENT_APP: self.client}
        self.query_mode, self.query_hook, self.queries, self.posts = '', None, [], []
        def relay_http(url, headers, timeout, body=None):
            self.assertEqual(url, 'https://relay.test/query')
            auth = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
            self.assertTrue(gs._nip01_event_verified(auth)); self.assertEqual(auth['pubkey'], base.OWNER_PK)
            authority.exact_tag(auth, 'u', url); authority.exact_tag(auth, 'method', 'POST')
            authority.exact_tag(auth, 'payload', hashlib.sha256(body).hexdigest())
            filters = json.loads(body); self.queries.append(copy.deepcopy(filters))
            if self.query_hook is not None:
                hook, self.query_hook = self.query_hook, None; hook()
            rows = list(self.world.relay_events)
            tags = [['d', base.CHANNEL]] + [['p', m['pubkey'], '', m['role']] for m in self.world.members]
            rows.append(gs.sign_event(claims.PIN_KEY, 39002, tags, '', self.now))
            def matches(event, f):
                return (('kinds' not in f or event['kind'] in f['kinds'])
                    and ('authors' not in f or event['pubkey'] in f['authors'])
                    and ('#d' not in f or any(t[0] == 'd' and t[1] in f['#d'] for t in event['tags'])))
            rows = [e for e in rows if any(matches(e, f) for f in filters)]
            if rows and self.query_mode:
                if self.query_mode == 'forged':
                    bad = copy.deepcopy(rows[-1]); bad['sig'] = '0' * 128; rows.append(bad)
                elif self.query_mode == 'saturated': rows = rows * 257
                elif self.query_mode == 'partial': return 503, b'{}'
                elif self.query_mode == 'wrong-scope':
                    rows.append(gs.sign_event(claims.PIN_KEY, 39002, [['d', claims.OTHER_CHANNEL]], '', self.now))
            return 200, json.dumps(rows).encode()
        self.relay = Nip98Relay('https://relay.test', self.env.signer_env, claims.PIN,
            http=relay_http, trusted_relays=('https://relay.test',), clock=lambda: self.now)
        return self

    def instance(self):
        self.assertIsNotNone(discovery, 'native fallback discovery module is missing')
        return discovery.FallbackDiscoverer(self.db, self.clients, self.relay, clock=lambda: self.now)

    async def result(self):
        before = tuple(self.db.conn.iterdump())
        value = await self.instance().discover(BINDING)
        self.assertEqual(tuple(self.db.conn.iterdump()), before)
        self.assertEqual(self.posts, [])
        return value

    async def pending(self):
        value = await self.result()
        self.assertEqual(value.status, 'pending'); self.assertEqual(value.candidates, ())
        self.assertIn('怎么解决', value.notice); self.assertIn('复制给 AI', value.notice)

    def policy(self, pubkey, owner_key, body, when=None):
        event = gs.sign_event(owner_key, 30177, [['d', pubkey]], json.dumps(body), self.now if when is None else when)
        self.world.relay_events.append(event)
        return event

    def mirror_change(self, change):
        previous = claims.latest_policy(self.world.relay_events, base.MIRROR_PK)
        body = json.loads(previous['content']); change(body)
        return self.policy(base.MIRROR_PK, base.OWNER_KEY, body)

    async def test_native_bot_control_uses_real_bot_identity_granted_scopes_and_complete_both_buckets(self):
        self.setup_discovery()
        self.assertEqual(self.client.identity(), (base.AGENT_APP, ''))
        self.assertEqual(self.client.bot_scopes(), set(bot_clients.READ_SCOPE_GROUPS))
        listing = await asyncio.to_thread(self.client.member_listing, base.CHAT, 'union_id')
        self.assertTrue(listing.complete); self.assertEqual(set(listing.bots), {base.AGENT_APP, SUBJECT_APP})
        self.assertEqual(listing.users, {'on_localowner': ''})
        self.assertTrue(all(m == 'GET' or p.endswith('/tenant_access_token/internal') for m, p in self.https.calls))

    async def test_signed_reader_control_real_nip98_query_verifies_foreign_public_profile_without_private_files(self):
        self.setup_discovery()
        rows = await SignedReader(self.relay).read('query', filters=[{'kinds': [0], 'authors': [SUBJECT], 'limit': 257}])
        self.assertEqual(rows, [self.subject_profile]); self.assertTrue(gs._nip01_event_verified(rows[0]))
        self.assertEqual(authority.attested_owner(rows[0], SUBJECT), SUBJECT_OWNER)

    async def test_low_boundary_negative_controls_reject_forged_frame_and_mark_actual_member_page_partial(self):
        self.setup_discovery(); self.query_mode = 'forged'
        with self.assertRaises(ReadFailure):
            await SignedReader(self.relay).read('query', filters=[{'kinds': [0], 'authors': [SUBJECT], 'limit': 257}])
        self.https.grant = 0; self.assertEqual(self.client.bot_scopes(), set())
        self.https.partial = True
        self.assertFalse((await asyncio.to_thread(self.client.member_listing, base.CHAT, 'union_id')).complete)

    async def test_absent_subject_roster_yields_only_public_subject_and_distinct_local_issuer_metadata(self):
        self.setup_discovery(); self.assertNotIn(SUBJECT, {m['pubkey'] for m in self.world.members})
        value = await self.result(); self.assertEqual(value.status, 'complete'); self.assertEqual(len(value.candidates), 1)
        c = value.candidates[0]
        self.assertEqual((c.subject_pubkey, c.subject_owner_pubkey, c.subject_app_id), (SUBJECT, SUBJECT_OWNER, SUBJECT_APP))
        self.assertEqual((c.issuer_app_id, c.binding_id, c.channel_id, c.chat_ref), (base.AGENT_APP, BINDING, base.CHANNEL, gs.chat_ref(base.CHAT)))
        self.assertEqual((c.mirror_pubkey, c.mirror_owner_pubkey, c.claimed_at), (base.MIRROR_PK, base.OWNER_PK, self.now - 100))
        self.assertEqual(c.checked_at, self.now); self.assertGreater(c.expires_at, self.now); self.assertLessEqual(c.expires_at, self.now + 30)
        self.assertTrue(c.proof_hashes); self.assertTrue(all(gs.HEX64_RE.fullmatch(h) for h in c.proof_hashes))
        self.assertFalse(any(hasattr(c, field) for field in ('approved', 'grant', 'card_ready', 'private_key', 'config_path', 'body')))

    async def test_own_public_subject_is_not_excluded_by_owner_string_or_claimed_as_foreign(self):
        self.setup_discovery()
        self.world.relay_events = [e for e in self.world.relay_events if e['id'] != self.subject_profile['id'] and e['id'] != self.subject_policy['id']]
        self.world.relay_events += [claims.profile(SUBJECT_KEY, base.OWNER_KEY, self.now - 10)]
        self.policy(SUBJECT, base.OWNER_KEY, {'feishu': {'app_id': SUBJECT_APP}}, self.now - 10)
        value = await self.result(); self.assertEqual(value.status, 'complete')
        self.assertEqual(value.candidates[0].subject_owner_pubkey, base.OWNER_PK)
        self.assertFalse(hasattr(value.candidates[0], 'foreign'))

    async def test_subject_conditional_oa_is_pending(self):
        self.setup_discovery(); self.world.relay_events.append(claims.profile(SUBJECT_KEY, SUBJECT_OWNER_KEY, self.now, 'kind=9'))
        await self.pending()

    async def test_latest_subject_oa_owner_change_cannot_reuse_previous_owners_app_policy(self):
        self.setup_discovery(); self.world.relay_events.append(claims.profile(SUBJECT_KEY, base.OWNER_KEY, self.now))
        await self.pending()

    async def test_subject_latest_policy_revocation_is_pending_not_old_app_match(self):
        self.setup_discovery(); self.policy(SUBJECT, SUBJECT_OWNER_KEY, {'feishu': {}})
        await self.pending()

    async def test_two_valid_subject_profiles_for_same_member_app_are_ambiguous(self):
        self.setup_discovery(); other_key = '6'.zfill(64); other = gs._signer_pubkey(other_key)
        self.world.relay_events.append(claims.profile(other_key, SUBJECT_OWNER_KEY, self.now - 10))
        self.policy(other, SUBJECT_OWNER_KEY, {'feishu': {'app_id': SUBJECT_APP}}, self.now - 10)
        await self.pending()

    async def test_latest_mirror_policy_removed_claim_or_sync_app_is_pending(self):
        self.setup_discovery(); self.mirror_change(lambda d: d['feishu']['bindings'][0].pop('sync_app'))
        await self.pending()

    async def test_claim_sync_app_must_be_exact_local_sql_selected_issuer(self):
        self.setup_discovery(); self.mirror_change(lambda d: d['feishu']['bindings'][0].update(sync_app={'version': 1, 'app_id': SUBJECT_APP}))
        await self.pending()

    async def test_actual_bot_grant_zero_partial_listing_and_missing_issuer_are_pending(self):
        self.setup_discovery(); self.https.grant = 0; await self.pending()
        self.https.grant = 1; self.https.partial = True; await self.pending()
        self.https.partial = False; self.https.issuer_present = False; await self.pending()

    async def test_protected_selected_profile_pair_mismatch_cannot_discover(self):
        self.setup_discovery()
        base.write_owner_only(self.client.config_dir / 'config.json', json.dumps({'apps': [{'appId': SUBJECT_APP}]}))
        await self.pending()

    async def test_real_bot_with_another_local_profile_path_pair_cannot_satisfy_sql_binding(self):
        self.setup_discovery(); other_cfg, other_data = self.tmp / 'other-cfg', self.tmp / 'other-data'
        other_cfg.mkdir(); other_data.mkdir()
        base.write_owner_only(other_cfg / 'config.json', json.dumps({'apps': [{'appId': base.AGENT_APP}]}))
        self.clients[base.AGENT_APP] = bot_clients.BotLarkCli(base.AGENT_APP, other_cfg, other_data,
            base_env={}, http_pool=self.pool, runner=lambda *a, **kw: self.fail('no CLI allowed'))
        await self.pending()

    async def test_signed_malformed_unrelated_policy_cannot_be_filtered_from_complete_package(self):
        self.setup_discovery()
        self.world.relay_events.append(gs.sign_event(SUBJECT_OWNER_KEY, 30177, [['d', 'f' * 64]],
            '{"feishu":{},"feishu":{"mirror":true}}', self.now))
        await self.pending()

    async def test_policy_set_above_256_batches_all_profile_subjects(self):
        self.setup_discovery()
        extra = {f'{n:064x}' for n in range(1, 301)}
        for pub in sorted(extra): self.policy(pub, SUBJECT_OWNER_KEY, {})
        value = await self.result()
        self.assertEqual(value.status, 'complete')
        batches = [f['authors'] for filters in self.queries for f in filters
                   if f.get('kinds') == [0]]
        self.assertTrue(batches)
        self.assertTrue(all(len(group) <= 64 for group in batches))
        self.assertTrue(extra <= {pub for group in batches for pub in group})

    async def test_retired_or_nonactive_binding_cannot_discover(self):
        self.setup_discovery(); self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,))
        await self.pending()

    async def test_mixed_forged_saturated_partial_or_wrong_query_scope_is_pending(self):
        self.setup_discovery()
        for mode in ('forged', 'saturated', 'partial', 'wrong-scope'):
            self.query_mode = mode
            with self.subTest(mode=mode): await self.pending()

    async def test_current_local_mirror_roster_revoked_is_pending(self):
        self.setup_discovery(); self.world.members = [dict(m, role='member') if m['pubkey'] == base.MIRROR_PK else m for m in self.world.members]
        await self.pending()

    async def test_earlier_signed_competing_claim_wins_over_selected_local_claim(self):
        self.setup_discovery(); rival_key = '6'.zfill(64); rival = gs._signer_pubkey(rival_key)
        self.world.relay_events.append(claims.profile(rival_key, base.OWNER_KEY, self.now - 10))
        self.world.members.append({'pubkey': rival, 'role': 'bot'})
        self.policy(rival, base.OWNER_KEY, {'feishu': {'mirror': True, 'bindings': [{'channel': base.CHANNEL,
            'chat_ref': gs.chat_ref(base.CHAT), 'claimed_at': self.now - 200, 'heartbeat': self.now,
            'sync_app': {'version': 1, 'app_id': base.AGENT_APP}}]}})
        await self.pending()

    async def test_current_issuer_owner_signed_app_policy_must_match_real_selected_bot(self):
        self.setup_discovery(); self.policy(claims.AGENT, base.OWNER_KEY, {'feishu': {'app_id': SUBJECT_APP}})
        await self.pending()

    async def test_protected_local_config_changed_during_native_read_is_pending(self):
        self.setup_discovery()
        def change():
            cfg = copy.deepcopy(self.cfg); cfg['chat_id'] = 'oc_other'
            base.write_owner_only(self.env.config, json.dumps(cfg))
        self.https.bot_hook = change
        await self.pending(); self.assertTrue(self.https.calls)

    async def test_signed_revocation_during_native_members_read_is_seen_in_final_complete_snapshot(self):
        self.setup_discovery()
        self.https.bot_hook = lambda: self.mirror_change(lambda d: d['feishu'].update(bindings=[]))
        await self.pending(); self.assertTrue(self.https.calls)

    async def test_mainloop_sql_revocation_during_native_io_stays_pending_and_does_not_write(self):
        self.setup_discovery(); loop = asyncio.get_running_loop(); reached = threading.Event()
        def revoke():
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,)); reached.set()
        def hook():
            loop.call_soon_threadsafe(revoke); self.assertTrue(reached.wait(5))
        self.https.bot_hook = hook
        # The only SQL change is the actual main-loop revocation, never a
        # worker-thread authority read or discovery effect write.
        value = await self.instance().discover(BINDING)
        self.assertEqual(value.status, 'pending'); self.assertEqual(value.candidates, ())
        self.assertTrue(reached.is_set()); self.assertEqual(self.posts, [])

    async def test_claim_lease_and_freshness_cap_cannot_extend_expired_authority(self):
        self.setup_discovery()
        self.mirror_change(lambda d: d['feishu']['bindings'][0].update(heartbeat=self.now - gs.CLAIM_LEASE_SECONDS - 1))
        await self.pending()
