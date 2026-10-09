"""Read-only approval consumer: actual encrypted catalog, crypto and wire adapters.

Relay HTTP, bot HTTP and the explicitly labelled AES primitive are fake in the
portable cases. Catalog parsing and protected config/key/blob reads are real.
An additional dependency case executes real AESGCM. No Store, foreign profile,
public approval producer, CLI subprocess or real credential is used.
"""
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_outlet as wire
import test_hostd_remote_mapping as fixture
from hostd import agent_catalog, remote_approval, store
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli, READ_SCOPE_GROUPS
import buzz_feishu_group_sync as gs

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

CHANNEL, CHAT, ORIGIN, NOW = wire.CHANNEL, wire.CHAT, wire.ORIGIN, wire.NOW
APP = 'cli_ownb'  # Actual encrypted store accepts only alphanumeric app suffixes.
PUB, OWNER, MIRROR, MOWNER, PIN = wire.PUB, wire.OWNER, wire.MIRROR, wire.MOWNER, wire.PIN
SYNC_APP = 'cli_declared_sync'


class DecryptFixture:
    """Low-level decrypt seam only, deliberately not a cryptographic proof."""
    ciphertext = b'LOW_LEVEL_DECRYPT_FIXTURE' + b'T' * 16

    def __init__(self, key):
        if key != b'k' * 32:
            raise ValueError('synthetic invalid key')

    def decrypt(self, nonce, ciphertext, aad):
        if (nonce, ciphertext, aad) != (b'n' * 12, self.ciphertext, None):
            raise ValueError('synthetic invalid blob')
        return b'SYNTHETIC_APP_SECRET'


def secret_module():
    """Load the real file reader with only its optional AES import seam replaced.

Restore precisely the temporary import entry, not a whole sys.modules snapshot.
The normal/dependency process imports the actual library without this seam.
"""
    if AESGCM is not None:
        return importlib.import_module('hostd.secrets_store')
    name = 'cryptography.hazmat.primitives.ciphers.aead'
    absent = object()
    old = sys.modules.get(name, absent)
    module = types.ModuleType(name)
    module.AESGCM = DecryptFixture
    sys.modules[name] = module
    try:
        return importlib.import_module('hostd.secrets_store')
    finally:
        if old is absent:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class World(wire.WireWorld):
    """Synthetic low-level IO with real protected files, not a verified DTO seam."""

    def __init__(self, case, *, actual_crypto=False):
        self.sync_declared = True
        super().__init__(case)
        self.policy_app = APP
        self.directory = self.metadata()
        self.target = dataclasses.replace(self.target, app_id=APP,
            claim_event_id=next(e['id'] for e in self.directory if e['kind'] == 30177 and e['pubkey'] == MOWNER))
        self.targets = {CHANNEL: self.target}
        self.owned(self.cfg / 'config.json', json.dumps({'apps': [{'appId': APP, 'name': 'local'}]}))
        self.bot = BotLarkCli(APP, self.cfg, self.data, base_env={'HOME': str(self.root)}, http_pool=self, chat_id=CHAT)
        self.prompt, self.responsible = self.root / 'prompt.md', self.root / 'responsible.json'
        self.owned(self.prompt, 'SYNTHETIC_PRIVATE_PROMPT_DO_NOT_READ')
        self.owned(self.responsible, '{"people_file":"SYNTHETIC_DO_NOT_READ_PEOPLE"}')
        self.owned(self.env, self.env.read_text() + f'BUZZ_ACP_AGENT_OWNER={OWNER}\n'
                   f'BUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n')
        private = self.data / 'lark-cli'
        private.mkdir(mode=0o700)
        key, nonce = b'k' * 32, b'n' * 12
        primitive = AESGCM if actual_crypto else DecryptFixture
        patch = mock.patch.object(secret_module(), 'AESGCM', primitive)
        patch.start()
        case.addCleanup(patch.stop)
        self.owned(private / 'master.key', key)
        ciphertext = AESGCM(key).encrypt(nonce, b'SYNTHETIC_APP_SECRET', None) if actual_crypto else DecryptFixture.ciphertext
        self.owned(private / f'appsecret_{APP}.enc', nonce + ciphertext)
        self.agent_doc = dict(name='synthetic', env_file=str(self.env), unit='synthetic.service',
            capabilities={'summary': 'synthetic', 'repos': []},
            feishu={'app_id': APP, 'lark_config_dir': str(self.cfg), 'lark_data_dir': str(self.data)})
        self.doc = dict(version=1, owner_pubkey=OWNER,
            buzz={'cli_path': '/synthetic/buzz', 'cli_sha256': 'a' * 64},
            state_dir=str(self.root / 'state'), lark_cli='/synthetic/lark-cli', agents=[self.agent_doc])
        self.catalog_path, self.legacy_path = self.root / 'catalog.json', self.root / 'legacy.json'
        self.owned(self.catalog_path, json.dumps(self.doc))
        self.owned(self.legacy_path, json.dumps(dict(self.doc, agents=[])))
        self.catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_path)
        self.actual_record = self.catalog.records[0]
        case.assertEqual(self.actual_record.status, 'own_bot_verified', 'fixture must exercise actual catalog decryption')
        self.reader = OwnAgentReader(self.actual_record, origin=ORIGIN, relay_pubkey=PIN,
            trusted_relays=(ORIGIN,), clock=lambda: self.now, http=self.http)
        self.members_incomplete = self.bot_absent = False
        self.scopes = set(READ_SCOPE_GROUPS) | {'im:message:send_as_bot'}
        self.scope_grant_status = 1
        self.scope_identity = 'bot'
        self.on_member = None

    @staticmethod
    def owned(path, value):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, value if isinstance(value, bytes) else value.encode())
        finally:
            os.close(fd)

    def metadata(self):
        rows = super().metadata()
        for index, event in enumerate(rows):
            if event['kind'] == 30177 and event['pubkey'] == MOWNER:
                body = json.loads(event['content'])
                body['feishu']['app_id'] = 'cli_mirror_decoy'
                if self.sync_declared:
                    for binding in body['feishu']['bindings']:
                        binding['sync_app'] = {'version': 1, 'app_id': SYNC_APP}
                rows[index] = gs.sign_event(fixture.FOREIGN_OWNER_KEY, 30177, event['tags'], json.dumps(body), NOW)
        return rows

    def approval_event(self, channel=CHANNEL, chat=CHAT):
        old = super().approval_event(channel, chat)
        body = json.loads(old['content'])
        body['app_id'] = APP
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return gs.sign_event(fixture.MIRROR_KEY, 30078, [['t', remote_approval.PREFIX], ['h', channel],
            ['p', PUB], ['d', remote_approval.PREFIX + ':' + digest]], raw, NOW)

    def request(self, *args, **kwargs):
        self.assertEqual((args[0], Path(args[1]), Path(args[2])), (APP, self.cfg, self.data))
        self.assertEqual(args[3], 'GET')
        self.assertEqual(kwargs['chat_id'], CHAT)
        if args[4] == '/open-apis/application/v6/scopes':
            self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
            return {'ok': True, 'identity': self.scope_identity, 'data': {'scopes': [
                {'scope_name': scope, **({'scope_type': 'tenant'} if self.scope_identity == 'bot' else {}),
                 'grant_status': self.scope_grant_status} for scope in sorted(self.scopes)]}}
        if args[4].endswith('/members/list'):
            if self.on_member:
                action, self.on_member = self.on_member, None
                action()
            self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
            result = {'ok': True, 'identity': 'bot', 'data': {'users': [],
                'bots': [{'member_id': 'ou_own', 'app_id': APP}], 'truncations': [],
                'user_total': 0, 'bot_total': 1, 'has_more': False}}
            if self.members_incomplete:
                result['data'].update(has_more=True, page_token='repeat')
            if self.bot_absent:
                result['data'].update(bots=[], bot_total=0)
            return result
        raise AssertionError('consumer must only read current own-bot scopes/membership, never messages or writes')

    def consumer(self, **kwargs):
        self.case.assertIsNotNone(importlib.util.find_spec('hostd.remote_proofs'),
                                  'missing real remote approval consumer')
        module = importlib.import_module('hostd.remote_proofs')
        return module.RemoteProofs(self.catalog_path, self.legacy_path, record=self.actual_record,
            reader=self.reader, bot=kwargs.pop('bot', self.bot), clock=lambda: self.now, **kwargs)

    def no_writes(self):
        self.case.assertFalse(self.posts)
        self.case.assertFalse(self.cli_calls, 'no borrowed identity or CLI fallback')
        self.case.assertTrue(all(call[0] == 'GET' for call in self.api_calls))
        self.case.assertFalse(any(self.root.rglob('*.sqlite*')), 'consumer cannot activate a SQL grant')


class RemoteProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = World(self)

    def pending(self, result):
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.authorization)
        self.assertIn('怎么解决', result.notice)
        self.assertIn('复制给 AI', result.notice)
        self.assertFalse(result.readback()['live_verified'])
        for canary in (fixture.KEY, 'SYNTHETIC_APP_SECRET', 'SYNTHETIC_PRIVATE_PROMPT', 'Traceback'):
            self.assertNotIn(canary, str(result))
        self.world.no_writes()

    async def test_actual_signed_approval_own_reader_catalog_and_bot_produce_metadata_only(self):
        w = self.world
        result = await w.consumer().verify(w.target)
        self.assertEqual(result.status, 'verified')
        auth = result.authorization
        self.assertIs(type(auth.evidence), store.RemoteGrantEvidence)
        evidence = auth.evidence
        self.assertEqual((evidence.agent_id, evidence.owner_pubkey, evidence.app_id), (PUB, OWNER, APP))
        self.assertEqual((evidence.channel_id, evidence.chat_id, evidence.chat_ref), (CHANNEL, CHAT, gs.chat_ref(CHAT)))
        self.assertEqual((evidence.mirror_pubkey, evidence.mirror_owner_pubkey), (MIRROR, MOWNER))
        self.assertNotEqual(MOWNER, OWNER, 'the remote mirror owner must not be replaced by the local owner')
        self.assertEqual((evidence.approval_kind, evidence.approval_id), ('mirror_approval', w.approval['id']))
        self.assertEqual(evidence.approval_hash, hashlib.sha256(w.approval['content'].encode()).hexdigest())
        self.assertEqual(evidence.capabilities, ('message',))
        self.assertEqual(evidence.claimed_at, NOW - 100)
        self.assertEqual(evidence.checked_at, NOW)
        self.assertGreater(evidence.valid_until, NOW)
        self.assertLessEqual(evidence.valid_until, NOW + 30)
        self.assertEqual(evidence.allowlist_hash, hashlib.sha256(json.dumps(sorted(w.actual_record.channels), separators=(',', ':')).encode()).hexdigest())
        self.assertEqual(auth.catalog_sha256, w.catalog.catalog_sha256)
        self.assertEqual(auth.legacy_join_sha256, w.catalog.legacy_join_sha256)
        self.assertIs(type(auth.sync_app), remote_approval.SyncAppProof)
        self.assertEqual(auth.sync_app.app_id, SYNC_APP)
        self.assertNotIn(auth.sync_app.app_id, (APP, 'cli_mirror_decoy'))
        readback = result.readback()
        self.assertFalse(readback['live_verified'])
        self.assertFalse(readback['grant_activated'])
        for canary in (fixture.KEY, 'SYNTHETIC_APP_SECRET', 'SYNTHETIC_PRIVATE_PROMPT'):
            self.assertNotIn(canary, json.dumps(readback))
        filters = [f for _, _, body in w.wire_calls for f in json.loads(body)]
        for kind in (0, 30177, 39002, 30078):
            self.assertTrue(any(kind in f.get('kinds', []) for f in filters))
        self.assertTrue(any(f.get('kinds') == [30177] and 'authors' not in f for f in filters), 'must enumerate all candidate policies')
        self.assertTrue(all(0 < f['limit'] <= 257 for f in filters))
        self.assertTrue(any(path == '/open-apis/application/v6/scopes' for _, path, _, _ in w.api_calls),
                        'must obtain current grants through actual own-bot scopes GET')
        w.no_writes()

    async def test_each_required_read_scope_group_must_have_actual_granted_alternative(self):
        w = self.world
        consumer = w.consumer()
        for group, choices in READ_SCOPE_GROUPS.items():
            with self.subTest(group=group):
                w.scopes = (set(READ_SCOPE_GROUPS) | {'im:message:send_as_bot'}) - choices
                self.pending(await consumer.verify(w.target))
        w.scopes = set(READ_SCOPE_GROUPS) | {'im:message:send_as_bot'}
        w.scope_grant_status = 0
        self.pending(await consumer.verify(w.target))
        self.assertTrue(any(path == '/open-apis/application/v6/scopes' for _, path, _, _ in w.api_calls))
        w.scope_grant_status = 1
        # Shared narrower grants satisfy all four N-of-1 groups. Requiring every
        # listed scope instead would incorrectly block a valid current handoff.
        w.scopes = {'im:chat.group_info:readonly', 'im:message:readonly', 'im:message:send_as_bot'}
        self.assertEqual((await consumer.verify(w.target)).status, 'verified')
        w.no_writes()

    async def test_wrong_native_scope_response_identity_is_not_authority(self):
        w = self.world
        consumer = w.consumer()
        w.scope_identity = 'user'
        self.pending(await consumer.verify(w.target))
        self.assertTrue(any(path == '/open-apis/application/v6/scopes' for _, path, _, _ in w.api_calls))
        w.scope_identity = 'bot'
        self.assertEqual((await consumer.verify(w.target)).status, 'verified')
        w.no_writes()

    async def test_discovery_allowlist_and_signatures_without_approval_remain_pending(self):
        w = self.world
        consumer = w.consumer()
        raw = w.env.read_bytes()
        w.events.clear()
        self.pending(await consumer.verify(w.target))
        self.assertEqual(w.env.read_bytes(), raw)

    async def test_missing_binding_sync_app_does_not_guess_mirror_or_own_application(self):
        w = self.world
        consumer = w.consumer()
        w.sync_declared = False
        w.directory = w.metadata()
        result = await consumer.verify(w.target)
        self.assertEqual(result.status, 'verified')
        self.assertIsNone(result.authorization.sync_app)
        self.assertEqual(result.authorization.evidence.app_id, APP)
        self.assertEqual(result.readback()['sync_app_status'], 'pending')
        w.no_writes()

    async def test_stable_claim_heartbeat_renews_fresh_metadata_not_original_approval(self):
        w = self.world
        consumer = w.consumer()
        first = await consumer.verify(w.target)
        self.assertEqual(first.status, 'verified')
        w.now += 10
        w.claims[0]['heartbeat'] = w.now
        w.directory = w.metadata()
        second = await consumer.verify(w.target)
        self.assertEqual(second.status, 'verified')
        a, b = first.authorization.evidence, second.authorization.evidence
        self.assertEqual((a.claimed_at, a.approval_id, a.approval_hash, a.allowlist_hash),
                         (b.claimed_at, b.approval_id, b.approval_hash, b.allowlist_hash))
        self.assertNotEqual(a.claim_event_id, b.claim_event_id)
        self.assertEqual(b.checked_at, w.now)
        self.assertGreater(b.valid_until, a.valid_until)
        w.no_writes()

    async def test_reclaim_cannot_reuse_old_card_approval(self):
        w = self.world
        consumer = w.consumer()
        w.claims[0]['claimed_at'] = NOW - 50
        w.directory = w.metadata()
        self.pending(await consumer.verify(w.target))

    async def test_current_own_app_policy_and_empty_owner_attestation_are_required(self):
        w = self.world
        consumer = w.consumer()
        w.policy_app = 'cli_revoked'
        w.directory = w.metadata()
        self.pending(await consumer.verify(w.target))
        w.policy_app = APP
        w.directory = w.metadata()
        w.directory[0] = gs.sign_event(fixture.KEY, 0, [fixture.auth(fixture.KEY, fixture.OWNER_KEY, CHANNEL)], '{}', NOW)
        self.pending(await consumer.verify(w.target))

    async def test_current_mirror_owner_and_revocation_override_old_approval(self):
        w = self.world
        consumer = w.consumer()
        w.mirror_policy = False
        w.directory = w.metadata()
        self.pending(await consumer.verify(w.target))
        w.mirror_policy = True
        w.directory = w.metadata()
        profile = next(index for index, event in enumerate(w.directory) if event['kind'] == 0 and event['pubkey'] == MIRROR)
        w.directory[profile] = gs.sign_event(fixture.MIRROR_KEY, 0, [fixture.auth(fixture.MIRROR_KEY, fixture.OWNER_KEY)], '{}', NOW)
        self.pending(await consumer.verify(w.target))

    async def test_relay_pin_roster_roles_and_current_claim_lease_are_required(self):
        w = self.world
        consumer = w.consumer()
        for who, role in ((PUB, 'member'), (MIRROR, 'member'), (MOWNER, 'member')):
            with self.subTest(identity=who[:8]):
                before = w.roles[who]
                w.roles[who] = role
                w.directory = w.metadata()
                self.pending(await consumer.verify(w.target))
                w.roles[who] = before
        w.now = NOW + gs.CLAIM_LEASE_SECONDS + 1
        self.pending(await consumer.verify(w.target))
        w.now = NOW
        w.directory = w.metadata()
        index = next(i for i, e in enumerate(w.directory) if e['kind'] == 39002)
        old = w.directory[index]
        w.directory[index] = gs.sign_event(fixture.OWNER_KEY, 39002, old['tags'], '', NOW)
        self.pending(await consumer.verify(w.target))

    async def test_all_mirror_candidates_must_be_read_not_only_discovered_winner(self):
        w = self.world
        consumer = w.consumer()
        rival = fixture.HUMAN
        w.roles[rival] = 'bot'
        w.directory = w.metadata()
        rival_profile = gs.sign_event(fixture.HUMAN_KEY, 0, [fixture.auth(fixture.HUMAN_KEY, fixture.FOREIGN_OWNER_KEY)], '{}', NOW)
        rival_policy = gs.sign_event(fixture.FOREIGN_OWNER_KEY, 30177, [['d', rival]], json.dumps({'feishu': {'mirror': True,
            'bindings': [dict(channel=CHANNEL, chat_ref=gs.chat_ref(CHAT), claimed_at=NOW-200, heartbeat=NOW)]}}), NOW)
        w.directory.extend((rival_profile, rival_policy))
        self.pending(await consumer.verify(w.target))
        w.directory.remove(rival_profile)
        self.pending(await consumer.verify(w.target))

    async def test_saturated_policy_query_and_bad_signature_cannot_be_complete(self):
        w = self.world
        consumer = w.consumer()
        policies = [event for event in w.directory if event['kind'] == 30177]
        original = list(w.directory)
        w.directory.extend([policies[0]] * (256 - len(policies)))
        self.pending(await consumer.verify(w.target))
        w.directory = original
        w.bad_sig = True
        self.pending(await consumer.verify(w.target))

    async def test_two_distinct_valid_card_approvals_are_ambiguous(self):
        w = self.world
        consumer = w.consumer()
        body = json.loads(w.approval['content'])
        body['request_id'] = 'JOIN-abcdef12'
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        w.events.append(gs.sign_event(fixture.MIRROR_KEY, 30078,
            [['t', remote_approval.PREFIX], ['h', CHANNEL], ['p', PUB], ['d', remote_approval.PREFIX + ':' + digest]], raw, NOW))
        self.pending(await consumer.verify(w.target))

    async def test_exact_bot_profile_data_and_own_reader_paths_checked_before_wire(self):
        w = self.world
        wrong = self.world.root / 'foreign-data'
        wrong.mkdir(mode=0o700)
        bot = BotLarkCli(APP, w.cfg, wrong, base_env={'HOME': str(w.root)}, http_pool=w, chat_id=CHAT)
        self.pending(await w.consumer(bot=bot).verify(w.target))
        self.assertFalse(w.wire_calls)
        self.assertFalse(w.api_calls)
        w.reader.env_file = str(w.root / 'not-this-agent.env')
        self.pending(await w.consumer().verify(w.target))
        self.assertFalse(w.wire_calls)

    async def test_actual_legacy_exclusion_and_protected_env_fail_without_fallback(self):
        w = self.world
        consumer = w.consumer()
        w.owned(w.legacy_path, json.dumps(w.doc))
        self.pending(await consumer.verify(w.target))
        self.assertFalse(w.wire_calls)
        w.owned(w.legacy_path, json.dumps(dict(w.doc, agents=[])))
        w.env.chmod(0o644)
        self.pending(await consumer.verify(w.target))
        self.assertFalse(w.wire_calls)

    async def test_protected_files_and_signed_app_rechecked_after_membership_await(self):
        w = self.world
        consumer = w.consumer()
        raw = w.env.read_bytes()
        w.on_member = lambda: w.owned(w.env, raw + b'# changed after signed read\n')
        self.pending(await consumer.verify(w.target))
        self.assertTrue(w.api_calls, 'must reach actual membership IO before file change')
        w.owned(w.env, raw)
        def revoke():
            w.policy_app = 'cli_revoked'
            w.directory = w.metadata()
        w.on_member = revoke
        self.pending(await consumer.verify(w.target))

    async def test_current_allowlist_is_required_and_never_automatically_edited(self):
        w = self.world
        consumer = w.consumer()
        raw = w.env.read_bytes()
        edited = raw.replace(('BUZZ_ACP_CHANNELS=' + CHANNEL).encode(), b'BUZZ_ACP_CHANNELS=')
        w.owned(w.env, edited)
        self.pending(await consumer.verify(w.target))
        self.assertEqual(w.env.read_bytes(), edited)
        self.assertFalse(w.wire_calls)
        w.owned(w.env, raw)
        self.pending(await consumer.verify(dataclasses.replace(w.target, chat_ref='a' * 64)))

    async def test_complete_same_app_membership_is_required_no_dto_override(self):
        w = self.world
        consumer = w.consumer()
        w.members_incomplete = True
        self.pending(await consumer.verify(w.target))
        w.members_incomplete = False
        w.bot_absent = True
        self.pending(await consumer.verify(w.target))
        w.no_writes()

    @unittest.skipUnless(AESGCM, 'additional genuine AESGCM profile integration requires cryptography')
    async def test_actual_aesgcm_profile_decryption_without_primitive_seam(self):
        w = World(self, actual_crypto=True)
        result = await w.consumer().verify(w.target)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.app_id, APP)
        self.assertEqual(result.authorization.evidence.allowlist_hash,
                         store.Store._remote_digest(sorted(w.actual_record.channels)))
        w.no_writes()


if __name__ == '__main__':
    unittest.main()
