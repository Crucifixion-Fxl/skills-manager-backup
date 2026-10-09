"""PRIVATE, AST-reviewed only: actual own-home daemon assembly; never executed.

The snapshot collector must install the separately pinned PhysicalWorld test.
Only HTTPS/WebSocket/SDK child/systemctl-journal and portable AES are synthetic.
No Store, catalog record, proof, coordinator, manager or runtime verdict is fake.
"""
import asyncio
import base64
import contextlib
import dataclasses
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_remote_dispatch as dispatch_fixture
import test_hostd_remote_proofs as crypto_fixture
import test_hostd_remote_mapping as keys
import test_hostd_own_home_admission as physical_fixture
from hostd import agent_catalog, store
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli, READ_SCOPE_GROUPS
from hostd.join_effects import ProcessOps
from hostd.onboarding_runtime import OnboardingRuntime, RuntimeConfig
from hostd.remote_manager import RemoteManager
from hostd.remote_proofs import RemoteProofs
from hostd.remote_runtime import RemoteRuntime
from hostd.remote_target import RemoteTargetResolver
import buzz_agent_join_requests as legacy
import buzz_feishu_group_sync as gs
import recovery_authority as authority

PUB, OWNER, APP = crypto_fixture.PUB, crypto_fixture.OWNER, crypto_fixture.APP
CHANNEL, CHAT, ORIGIN, PIN = crypto_fixture.CHANNEL, crypto_fixture.CHAT, crypto_fixture.ORIGIN, crypto_fixture.PIN
OTHER_KEY = '7'.zfill(64)
OTHER_PUB = gs._signer_pubkey(OTHER_KEY)
OTHER_APP, OTHER_CHAT = 'cli_independent', 'oc_independent'
OTHER_CHANNEL = '77777777-7777-4777-8777-777777777777'


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else
        json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Response:
    status = 200
    will_close = False
    def __init__(self, value):
        self.body = json.dumps(value, separators=(',', ':')).encode()
    def read(self, size):
        out, self.body = self.body[:size], self.body[size:]
        return out
    read1 = read
    def getheader(self, name, default=None):
        return default
    def getheaders(self):
        return []


class Connection:
    """Actual HttpPool lower HTTPSConnection wire, no fake pool/token cache."""
    def __init__(self, world, host, *, timeout, context):
        world.assertEqual(host, 'open.feishu.cn')
        self.w, self.closed = world, False
        self.pending = None
        world.connections.append(self)
    def request(self, method, path, body=None, headers=None):
        self.w.assertFalse(self.closed)
        data = json.loads(body) if body is not None else None
        if path == '/open-apis/auth/v3/tenant_access_token/internal':
            app = data['app_id']
            self.w.assertIn(app, self.w.identities)
            self.w.assertEqual(data['app_secret'], 'SYNTHETIC_APP_SECRET')
            self.pending = {'code': 0, 'tenant_access_token': 'SYNTHETIC_TOKEN_' + app, 'expire': 7200}
        else:
            app = headers['Authorization'].removeprefix('Bearer SYNTHETIC_TOKEN_')
            identity = self.w.identities[app]
            parsed = urlsplit(path)
            params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
            params = {k: (v == 'true' if v in ('true', 'false') else v) for k, v in params.items()}
            payload = self.w.request(app, identity['cfg'], identity['data'], method, parsed.path,
                params=params or None, data=data, chat_id=identity['chat'])
            self.pending = {'code': 0, 'data': payload['data']}
    def getresponse(self):
        self.w.assertIsNotNone(self.pending)
        return Response(self.pending)
    def close(self):
        self.closed = True


class Socket(dispatch_fixture.Socket):
    """Own-key feed AUTH/REQ verified for either independently signed identity."""
    async def send(self, raw):
        packet = json.loads(raw)
        if packet[0] == 'AUTH':
            event = packet[1]
            self.w.assertTrue(gs._nip01_event_verified(event))
            self.w.assertIn(event['pubkey'], (PUB, OTHER_PUB))
            authority.exact_tag(event, 'challenge', 'synthetic-challenge')
            authority.exact_tag(event, 'relay', ORIGIN.replace('https:', 'wss:'))
            self.w.assertEqual(authority.attested_owner(event, event['pubkey']), OWNER)
            self.incoming.put_nowait(json.dumps(['OK', event['id'], True, '']))
        elif packet[0] == 'REQ':
            self.sub, fil = packet[1], packet[2]
            pair = (tuple(fil['authors']), tuple(fil['#h']))
            self.w.assertIn(pair, (((PUB,), (CHANNEL,)), ((OTHER_PUB,), (OTHER_CHANNEL,))))
            self.w.assertEqual(fil['kinds'], [9, 7, 5, 40003])
        elif packet[0] == 'CLOSE':
            self.w.assertEqual(packet[1], self.sub)
        else:
            self.w.fail('unexpected synthetic relay packet')


class DaemonWorld(physical_fixture.PhysicalWorld):
    def __init__(self, case):
        super().__init__(case)
        self.identities = {APP: dict(pub=PUB, key=keys.KEY, cfg=self.cfg, data=self.data,
            env=self.env, chat=CHAT, channel=CHANNEL, prompt=self.prompt, responsible=self.responsible)}
        self.sdk_children, self.connections, self.sockets = [], [], []
        self.native_inflight = 0
        self.native_requests = []
        self.native_lock = threading.Lock()
        self.block_other_send = False
        self.other_send_entered, self.other_send_release = threading.Event(), threading.Event()
        self.on_native = None
        self.first_scope_status = 1
        self.owner_env = self.root / 'owner.env'
        self.owned(self.owner_env, 'BUZZ_PRIVATE_KEY=' + keys.OWNER_KEY + '\n')
        self.owned(self.root / 'template.json', '{}')
        (self.root / 'bindings').mkdir(mode=0o700)
        Path(self.doc['state_dir']).mkdir(mode=0o700)
        self.add_independent_identity()
        case.addCleanup(self.other_send_release.set)
        case.addCleanup(self.final_member_release.set)

    def add_independent_identity(self):
        cfg, data = self.root / 'other-config', self.root / 'other-data'
        cfg.mkdir(mode=0o700); data.mkdir(mode=0o700)
        private = data / 'lark-cli'; private.mkdir(mode=0o700)
        self.owned(cfg / 'config.json', json.dumps({'apps': [{'appId': OTHER_APP, 'name': 'local'}]}))
        self.owned(private / 'master.key', b'k' * 32)
        self.owned(private / ('appsecret_' + OTHER_APP + '.enc'),
            b'n' * 12 + crypto_fixture.DecryptFixture.ciphertext)
        env, prompt, responsible = (self.root / 'other.env', self.root / 'other-prompt.md',
                                   self.root / 'other-responsible.json')
        self.owned(prompt, 'SYNTHETIC_OTHER_PRIVATE_PROMPT\n' + legacy.PROMPT_BEGIN + '\n' +
            OTHER_CHANNEL + '\n' + legacy.PROMPT_END + '\n')
        self.owned(responsible, json.dumps({'people_file': 'SYNTHETIC_OTHER_PEOPLE', 'channels': [OTHER_CHANNEL]}))
        self.owned(env, 'BUZZ_PRIVATE_KEY=' + OTHER_KEY + '\nBUZZ_AUTH_TAG=' +
            json.dumps(keys.auth(OTHER_KEY, keys.OWNER_KEY), separators=(',', ':')) +
            '\nBUZZ_RELAY_URL=' + ORIGIN + '\nBUZZ_ACP_CHANNELS=' + OTHER_CHANNEL +
            '\nBUZZ_ACP_AGENT_OWNER=' + OWNER + '\nBUZZ_ACP_SYSTEM_PROMPT_FILE=' + str(prompt) +
            '\nBUZZ_RESPONSIBLE_CONFIG=' + str(responsible) + '\n')
        self.doc['agents'].append(dict(name='independent', env_file=str(env), unit='independent.service',
            capabilities={'summary': 'synthetic independent', 'repos': []},
            feishu={'app_id': OTHER_APP, 'lark_config_dir': str(cfg), 'lark_data_dir': str(data)}))
        self.owned(self.catalog_path, json.dumps(self.doc))
        self.identities[OTHER_APP] = dict(pub=OTHER_PUB, key=OTHER_KEY, cfg=cfg, data=data, env=env,
            chat=OTHER_CHAT, channel=OTHER_CHANNEL, prompt=prompt, responsible=responsible)
        self.claims.append({'channel': OTHER_CHANNEL, 'chat_ref': gs.chat_ref(OTHER_CHAT),
                            'claimed_at': self.now - 100, 'heartbeat': self.now})
        self.directory = self.metadata()
        self.directory.extend([
            gs.sign_event(OTHER_KEY, 0, [keys.auth(OTHER_KEY, keys.OWNER_KEY)], '{}', self.now),
            gs.sign_event(keys.OWNER_KEY, 30177, [['d', OTHER_PUB]],
                json.dumps({'feishu': {'app_id': OTHER_APP}}), self.now)])
        # Current pinned roster for the second independent channel, not a DTO.
        self.directory = [event for event in self.directory if not
            (event['kind'] == 39002 and authority.tags(event, 'd')[0][1] == OTHER_CHANNEL)]
        roles = {OTHER_PUB: 'bot', crypto_fixture.MIRROR: 'bot', OWNER: 'member', crypto_fixture.MOWNER: 'owner'}
        self.directory.append(gs.sign_event(keys.RELAY_KEY, 39002, [['d', OTHER_CHANNEL]] +
            [['p', pub, '', role] for pub, role in roles.items()], '', self.now))
        body = json.loads(self.approval['content'])
        body.update(agent_pubkey=OTHER_PUB, app_id=OTHER_APP, channel_id=OTHER_CHANNEL,
            chat_ref=gs.chat_ref(OTHER_CHAT), request_id='JOIN-7654abcd')
        body['card_message_sha256'] = digest(b'SYNTHETIC_OTHER_CARD')
        body['decision_event_sha256'] = digest(b'SYNTHETIC_OTHER_DECISION')
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        self.events.append(gs.sign_event(keys.MIRROR_KEY, 30078,
            [['t', 'hostd-card-approval-v1'], ['h', OTHER_CHANNEL], ['p', OTHER_PUB],
             ['d', 'hostd-card-approval-v1:' + digest(raw.encode())]], raw, self.now))
        self.reload()
        self.assertEqual(set(self.catalog.records[i].pubkey for i in range(2)), {PUB, OTHER_PUB})
        self.assertTrue(all(row.status == 'own_bot_verified' for row in self.catalog.records))
        self.assertEqual(self.records[OTHER_PUB].channels, (OTHER_CHANNEL,))

    def add_catalog_selection_exclusions(self):
        """Actual protected/catalog rejection; never manufacture record/spec status.

        Catalog rows inherit the local document owner. A valid foreign-owner
        OA/env under that document is therefore blocked by agent_identity_invalid,
        rather than returned as a fake own_bot_verified foreign-owner record.
        """
        legacy_doc = json.loads(self.legacy_path.read_text())
        expectations = {}
        for label, scalar, owner_key in (('legacy', '8', keys.OWNER_KEY),
                ('profileblocked', '9', keys.OWNER_KEY), ('foreignowner', 'a', keys.FOREIGN_OWNER_KEY)):
            key = scalar.zfill(64); pub = gs._signer_pubkey(key)
            owner = gs._signer_pubkey(owner_key); app = 'cli_selection' + label
            directory = self.root / ('selection-' + label); directory.mkdir(mode=0o700)
            cfg, data = directory / 'config', directory / 'data'
            cfg.mkdir(mode=0o700); data.mkdir(mode=0o700)
            private = data / 'lark-cli'; private.mkdir(mode=0o700)
            actual_app = app if label != 'profileblocked' else 'cli_unrelatedprofile'
            self.owned(cfg / 'config.json', json.dumps({'apps': [{'appId': actual_app, 'name': 'local'}]}))
            self.owned(private / 'master.key', b'k' * 32)
            self.owned(private / ('appsecret_' + app + '.enc'),
                b'n' * 12 + crypto_fixture.DecryptFixture.ciphertext)
            env, prompt, responsible = directory / 'agent.env', directory / 'prompt.md', directory / 'responsible.json'
            self.owned(prompt, 'SYNTHETIC_EXCLUDED_PROMPT\n' + legacy.PROMPT_BEGIN + '\n' + legacy.PROMPT_END + '\n')
            self.owned(responsible, '{"people_file":"SYNTHETIC_EXCLUDED_PEOPLE","channels":[]}')
            self.owned(env, 'BUZZ_PRIVATE_KEY=' + key + '\nBUZZ_AUTH_TAG=' +
                json.dumps(keys.auth(key, owner_key), separators=(',', ':')) + '\nBUZZ_RELAY_URL=' + ORIGIN +
                '\nBUZZ_ACP_CHANNELS=\nBUZZ_ACP_AGENT_OWNER=' + owner +
                '\nBUZZ_ACP_SYSTEM_PROMPT_FILE=' + str(prompt) + '\nBUZZ_RESPONSIBLE_CONFIG=' + str(responsible) + '\n')
            doc = dict(name='selection-' + label, env_file=str(env), unit='selection-' + label + '.service',
                capabilities={'summary': 'synthetic excluded', 'repos': []},
                feishu={'app_id': app, 'lark_config_dir': str(cfg), 'lark_data_dir': str(data)})
            self.doc['agents'].append(doc)
            if label == 'legacy': legacy_doc['agents'].append(dict(doc))
            profile = gs.sign_event(key, 0, [keys.auth(key, owner_key)], '{}', self.now)
            self.assertTrue(gs._nip01_event_verified(profile))
            self.assertEqual(authority.attested_owner(profile, pub), owner)
            self.directory.extend([profile, gs.sign_event(owner_key, 30177, [['d', pub]],
                json.dumps({'feishu': {'app_id': app}}), self.now)])
            expectations[label] = dict(pub=pub, owner=owner, env=env, app=app,
                reason={'legacy': 'legacy_join_overlap', 'profileblocked': 'bot_profile_invalid',
                        'foreignowner': 'agent_identity_invalid'}[label])
        self.owned(self.catalog_path, json.dumps(self.doc))
        self.owned(self.legacy_path, json.dumps(legacy_doc))
        self.reload()
        for label, item in expectations.items():
            row = self.records[item['pub']]
            self.assertEqual(row.status, 'blocked')
            self.assertIn(item['reason'], row.reasons)
            self.assertEqual(row.owner_pubkey, OWNER, 'actual catalog declares the local owner')
            self.assertEqual(row.channels, ())
            self.assertEqual(row.local_bot_verified, label != 'profileblocked')
            parsed = legacy.parse_env(item['env'].read_text())
            self.assertEqual(parsed['BUZZ_ACP_AGENT_OWNER'], item['owner'])
            if label == 'foreignowner': self.assertNotEqual(item['owner'], OWNER)
        self.assertEqual(self.records[PUB].status, 'own_bot_verified')
        self.assertEqual(self.records[OTHER_PUB].status, 'own_bot_verified')
        return expectations

    def reload(self):
        self.catalog = agent_catalog.load(self.catalog_path, legacy_join_path=self.legacy_path)
        self.records = {record.pubkey: record for record in self.catalog.records}
        self.actual_record = self.records[PUB]
        return self.catalog

    def transport(self, url, headers, timeout, *, body=None):
        self.assertEqual(url, ORIGIN + '/query')
        signed = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
        self.assertTrue(gs._nip01_event_verified(signed))
        pub = signed['pubkey']
        self.assertIn(pub, (OWNER, PUB, OTHER_PUB))
        authority.exact_tag(signed, 'u', url); authority.exact_tag(signed, 'method', 'POST')
        authority.exact_tag(signed, 'payload', hashlib.sha256(body).hexdigest())
        if pub == OWNER:
            self.assertNotIn('x-auth-tag', headers)
        else:
            identity = next(row for row in self.identities.values() if row['pub'] == pub)
            self.assertEqual(json.loads(headers['x-auth-tag']), keys.auth(identity['key'], keys.OWNER_KEY))
        rows = []
        filters = json.loads(body)
        self.wire_calls.append((url, headers, body))
        for fil in filters:
            matches = [event for event in self.directory + self.events
                if ('kinds' not in fil or event['kind'] in fil['kinds'])
                and ('authors' not in fil or event['pubkey'] in fil['authors'])
                and ('ids' not in fil or event['id'] in fil['ids'])
                and fil.get('since', 0) <= event['created_at'] <= fil.get('until', 2**63 - 1)
                and all(not key.startswith('#') or any(len(tag) > 1 and tag[0] == key[1:]
                    and tag[1] in values for tag in event['tags']) for key, values in fil.items())]
            rows.extend(matches[:fil.get('limit', 256)])
        return 200, json.dumps(rows).encode()

    def request(self, app, config, data_dir, method, path, *, params=None, data=None,
                chat_id=None, priority=None):
        identity = self.identities[app]
        self.assertEqual((Path(config), Path(data_dir)), (identity['cfg'], identity['data']))
        self.assertIn(chat_id, (None, identity['chat']))
        with self.native_lock: self.native_inflight += 1
        try:
            self.api_calls.append((method, path, params, data))
            self.native_requests.append((app, method, path))
            if self.on_native: self.on_native(app, method, path)
            if method == 'GET' and path == '/open-apis/application/v6/scopes':
                names = set(READ_SCOPE_GROUPS) | {'im:message:send_as_bot'}
                result = {'scopes': [{'scope_name': name, 'scope_type': 'tenant',
                    'grant_status': self.first_scope_status if app == APP else 1} for name in sorted(names)]}
            elif method == 'GET' and path == '/open-apis/im/v1/chats':
                result = {'items': [{'chat_id': identity['chat']}], 'has_more': False, 'page_token': ''}
            elif method == 'GET' and path == '/open-apis/im/v1/chats/' + identity['chat'] + '/members/list':
                if app == APP and self.system.restart_calls and self.block_final_member:
                    self.final_member_entered.set()
                    self.assertTrue(self.final_member_release.wait(600), 'actual final source IO release')
                result = {'users': [], 'bots': [{'member_id': 'ou_native', 'app_id': app}],
                    'user_total': 0, 'bot_total': 1, 'has_more': False, 'truncations': [], 'page_token': ''}
            elif method == 'POST' and path == '/open-apis/im/v1/messages':
                source = re.search(r'[?&]e=([0-9a-f]{64})', data['content']).group(1)
                row, active = self.on_loop(lambda: (dict(self.db.conn.execute(
                    'SELECT * FROM remote_delivery WHERE source_id=?', (source,)).fetchone()), self.db.conn.in_transaction))
                self.assertEqual(row['status'], 'unknown'); self.assertFalse(active)
                self.assertEqual((params, data['receive_id'], data['msg_type']),
                    ({'receive_id_type': 'chat_id'}, identity['chat'], 'interactive'))
                if app == OTHER_APP and self.block_other_send:
                    self.other_send_entered.set()
                    self.assertTrue(self.other_send_release.wait(600), 'actual own second-app POST release')
                self.posts.append((app, path, dict(data)))
                mid = 'om_native' + str(len(self.posts))
                self.messages[mid] = dict(message_id=mid, chat_id=identity['chat'], root_id=mid,
                    thread_id='omt_' + mid, create_time=str(self.now * 1000), msg_type='interactive',
                    sender={'sender_type': 'app', 'id_type': 'app_id', 'id': app}, body={'content': data['content']})
                result = {'message_id': mid}
            elif method == 'GET' and path == '/open-apis/im/v1/messages':
                result = {'items': [row for row in self.messages.values() if row['chat_id'] == identity['chat']],
                    'has_more': False, 'page_token': ''}
            elif method == 'GET' and path.startswith('/open-apis/im/v1/messages/'):
                row = self.messages.get(path.rsplit('/', 1)[-1])
                result = {'items': [row] if row is not None else []}
            else:
                raise AssertionError('unapproved daemon fixture API boundary')
            return {'ok': True, 'identity': 'bot', 'data': result}
        finally:
            with self.native_lock: self.native_inflight -= 1

    def connection_factory(self, host, **kwargs):
        return Connection(self, host, **kwargs)

    def connect(self, url):
        self.assertEqual(url, ORIGIN.replace('https:', 'wss:'))
        sock = Socket(self); self.sockets.append(sock)
        return sock

    def new_event(self, pub=PUB, marker='one'):
        identity = next(row for row in self.identities.values() if row['pub'] == pub)
        event = gs.sign_event(identity['key'], 9, [['h', identity['channel']], ['t', marker]],
                              'SYNTHETIC_BODY_DO_NOT_CACHE', self.now)
        self.events.append(event)
        return event

    def adapters(self, record, chat_id=None):
        reader = OwnAgentReader(record, origin=ORIGIN, relay_pubkey=PIN,
            trusted_relays=(ORIGIN,), clock=lambda: self.now, http=self.transport)
        bot = BotLarkCli(record.app_id, record.lark_config_dir, record.lark_data_dir,
            base_env={'HOME': str(self.root)}, http_pool=self, chat_id=chat_id)
        return reader, bot


class OwnHomeDaemonWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = DaemonWorld(self)
        self.db = self.w.db
        canonical_secret = crypto_fixture.secret_module()
        absent = object()
        previous_secret = sys.modules.get('secrets_store', absent)
        sys.modules['secrets_store'] = canonical_secret
        def restore_secret_module():
            if previous_secret is absent:
                sys.modules.pop('secrets_store', None)
            else:
                sys.modules['secrets_store'] = previous_secret
        self.addCleanup(restore_secret_module)
        self.addCleanup(lambda: self.db.close())
        self.config = RuntimeConfig(1, str(self.w.owner_env), ORIGIN, PIN,
            str(self.w.root / 'template.json'), str(self.w.root / 'bindings'),
            str(self.w.legacy_path), str(self.w.catalog_path), (ORIGIN,), remote_link_base=ORIGIN)
        self.hosts = []
        # These are the real factory's documented low-I/O kwargs defaults;
        # the classmethod itself, every constructor and every result stay real.
        factory = OnboardingRuntime.create.__func__
        defaults = dict(factory.__kwdefaults__, http=self.w.transport,
            clock=lambda: self.w.now, runtime_operations=self.w.ops)
        patch = mock.patch.object(factory, '__kwdefaults__', defaults)
        patch.start(); self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {'HOME': str(self.w.root), 'PATH': '/usr/bin:/bin'}, clear=True)
        env.start(); self.addCleanup(env.stop)
        # Default process operations are retained; only subprocess/cgroup inputs
        # are replaced. Root constructs a fresh actual ScopedProcessOps itself.
        init = ProcessOps.__init__
        patch = mock.patch.object(init, '__defaults__', (self.w.system.run,))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(init, '__kwdefaults__', dict(init.__kwdefaults__,
            base_env={'PATH': '/usr/bin:/bin'}, cgroup_root=self.w.ops.cgroup_root))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(dispatch_fixture.relay_feed, '_connect', self.w.connect)
        patch.start(); self.addCleanup(patch.stop)
        self.original_spawn = asyncio.create_subprocess_exec
        patch = mock.patch.object(asyncio, 'create_subprocess_exec', self.sdk_spawn)
        patch.start(); self.addCleanup(patch.stop)

    async def sdk_spawn(self, *argv, **kwargs):
        self.assertEqual(Path(argv[1]).name, 'feishu_feed.py')
        self.assertIn(argv[2], (APP, OTHER_APP))
        identity = self.w.identities[argv[2]]
        self.assertEqual(tuple(argv[3:]), (str(identity['cfg']), str(identity['data'])))
        # This is only the SDK child transport: the actual root feed reader,
        # cross-process lock, process stop/escalation/reap remain real.
        child = await self.original_spawn(sys.executable, '-c',
            'import json,sys; print(json.dumps({"type":"_connecting","chat_id":""}),flush=True); sys.stdin.buffer.read()',
            stdin=asyncio.subprocess.PIPE, stdout=kwargs['stdout'], stderr=kwargs['stderr'],
            env={'PATH': '/usr/bin:/bin'})
        self.w.sdk_children.append(child)
        return child

    async def wait_for(self, predicate, message, timeout=240):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.01)
        self.assertTrue(predicate(), message)

    @staticmethod
    def runtime_supervisor_tasks(host, runtime):
        """Inspect the actual wrapper's bound start callable, not inner cr_code."""
        tasks = []
        for task in host._tasks:
            coroutine = task.get_coro()
            if getattr(coroutine, 'cr_code', None) is not host.supervise.__func__.__code__:
                continue
            frame = getattr(coroutine, 'cr_frame', None)
            if frame is None:
                continue
            start = frame.f_locals.get('start')
            if (getattr(start, '__self__', None) is runtime
                    and getattr(start, '__func__', None) is runtime.run.__func__):
                tasks.append(task)
        return tasks

    async def event_arrival(self, event, message):
        # Rendezvous belongs to fixture setup, not a product latency assertion.
        self.assertTrue(await asyncio.to_thread(event.wait, 240), message)

    def make_host(self):
        hd = importlib.import_module('hostd.__main__')
        host = hd.Hostd(hd.registry.Registry(), self.w.root / 'hostd-status.json',
                        state_db=self.db.path, onboarding_config=self.config)
        host.http_pool.factory = self.w.connection_factory
        host.app_lock_dir = self.w.root / 'app-locks'
        self.hosts.append(host)
        return host

    async def started_host(self):
        self.w.loop = asyncio.get_running_loop()
        host = self.make_host()
        await host.start_onboarding()
        self.assertIs(type(host.onboarding), OnboardingRuntime)
        self.assertEqual(set(host.onboarding.records), {APP, OTHER_APP})
        self.db = self.w.db = host.runtime_store
        self.addAsyncCleanup(host._shutdown)
        return host

    def admission_runtime(self, host):
        self.assertIsNotNone(importlib.util.find_spec('hostd.own_admission_runtime'),
            'missing own-home daemon scheduler is the intended feature assertion')
        module = importlib.import_module('hostd.own_admission_runtime')
        self.assertIsInstance(getattr(host, 'own_home_admission_runtime', None), module.OwnHomeAdmissionRuntime)
        coordinator = importlib.import_module('hostd.own_admission').OwnHomeAdmissionCoordinator
        self.assertIs(type(getattr(host, 'own_home_admission', None)), coordinator)
        return host.own_home_admission_runtime

    async def resolve(self, service, pub):
        record = self.w.reload().records
        record = next(row for row in record if row.pubkey == pub)
        identity = next(row for row in self.w.identities.values() if row['pub'] == pub)
        reader, bot = self.w.adapters(record, identity['chat'])
        discovery_bot = BotLarkCli(record.app_id, record.lark_config_dir, record.lark_data_dir,
            base_env={'HOME': str(self.w.root)}, http_pool=self.w, chat_id=None)
        def authorize(actual, channel):
            row = self.db.conn.execute('SELECT * FROM agent WHERE pubkey=?', (actual.pubkey,)).fetchone()
            return actual is record and channel in record.channels and row is not None and row['status'] == 'active' and (
                row['owner_pubkey'], row['app_id'], row['config_path']) == (record.owner_pubkey, record.app_id, str(record.env_file))
        answer = await RemoteTargetResolver(self.w.catalog_path, self.w.legacy_path,
            relay=service.relay, clients={record.app_id: discovery_bot}, authorize=authorize,
            clock=lambda: self.w.now).resolve(record, identity['channel'])
        self.assertEqual(answer.status, 'verified', 'actual signed normal-target prerequisite')
        proofs = RemoteProofs(self.w.catalog_path, self.w.legacy_path, record=record,
            reader=reader, bot=bot, clock=lambda: self.w.now)
        proof = await proofs.verify(answer.target)
        self.assertEqual(proof.status, 'verified', 'actual signed/native normal-proof prerequisite')
        return record, answer.target, reader, bot, proofs, proof.authorization.evidence

    def manager(self, service, parts):
        record, target, reader, bot, _proofs, _evidence = parts
        manager = RemoteManager(self.db, self.w.catalog_path, self.w.legacy_path,
            record=record, reader=reader, bot=bot, discovery_relay=service.relay,
            link_base=ORIGIN, clock=lambda: self.w.now)
        self.addAsyncCleanup(manager.close)
        return manager

    def runtime(self, parts):
        record, target, reader, bot, proofs, _evidence = parts
        return RemoteRuntime(self.db, record=record, reader=reader, bot=bot,
            proofs=proofs, link_base=ORIGIN, clock=lambda: self.w.now)

    def dispatcher(self, service):
        module = importlib.import_module('hostd.remote_dispatch')
        dispatch = module.RemoteDispatch(service, scheduler=service._scheduler,
            http_pool=self.w, base_env={'HOME': str(self.w.root)}, clock=lambda: self.w.now)
        self.addAsyncCleanup(dispatch.close)
        return dispatch

    async def initial_discovery(self):
        record = self.w.reload().records
        record = next(row for row in record if row.pubkey == PUB)
        reader, bot = self.w.adapters(record)
        module = importlib.import_module('hostd.own_admission_discovery')
        answer = await module.OwnAdmissionDiscovery(self.w.catalog_path, self.w.legacy_path,
            record=record, reader=reader, bot=bot, clock=lambda: self.w.now).discover()
        self.assertEqual(answer.status, 'discovered', 'actual zero-channel signed/native discovery prerequisite')
        self.assertEqual(len(answer.candidates), 1)
        return record, answer.candidates

    def append_reviewed_fixture_channel(self):
        """Test setup CAS, explicitly not physical admission or a granted DTO."""
        self.w.owned(self.w.env, re.sub(r'^BUZZ_ACP_CHANNELS=.*$', 'BUZZ_ACP_CHANNELS=' + CHANNEL,
                                      self.w.env.read_text(), flags=re.M))
        self.w.owned(self.w.prompt, self.w.prompt.read_text().replace(legacy.PROMPT_BEGIN,
            legacy.PROMPT_BEGIN + '\n' + CHANNEL))
        raw = json.loads(self.w.responsible.read_text()); raw['channels'] = [CHANNEL]
        self.w.owned(self.w.responsible, json.dumps(raw))
        self.w.reload()

    async def reserve_marker(self, service, original, candidates, old_process, prior_files):
        """PostWorld.seed pattern, with actual process/spec/full file metadata.

        This marker is NOT a receipt, binding, approval, grant or effect. Normal
        proof/send control must have succeeded against the current env first.
        """
        spec = service.effects.specs[PUB]
        rows = tuple(dict(channel_id=item.channel_id, chat_id=item.chat_id, chat_ref=item.chat_ref,
            source_kind='own_approval', approval_id=item.approval_id, approval_hash=item.approval_hash,
            mirror_pubkey=item.mirror_pubkey, mirror_owner_pubkey=item.mirror_owner_pubkey,
            claimed_at=item.claimed_at, claim_event_id=item.claim_event_id, policy_event_id=item.agent_policy_id,
            roster_event_id=item.roster_event_id, authorization_hash=digest([item.approval_id, item.approval_hash,
                item.claimed_at])) for item in candidates)
        aggregate = b''.join(len(prior_files[path]).to_bytes(8, 'big') + prior_files[path] for path in sorted(prior_files))
        self.assertEqual(self.w.records[PUB].channels, (CHANNEL,))
        return self.db.reserve_own_home_admission(admission_id='d' * 64, agent_id=PUB,
            snapshot_hash=digest(rows), scope_hash=digest({'spec': dataclasses.asdict(spec), 'channels': [CHANNEL]}),
            protectedfiles_hash=digest(aggregate), catalog_hash=candidates[0].catalog_sha256,
            legacy_join_hash=candidates[0].legacy_join_sha256, profile_hash=candidates[0].profile_sha256,
            env_before_hash=original.env_sha256, env_after_hash=self.w.records[PUB].env_sha256,
            agent_spec_hash=digest(dataclasses.asdict(spec)), prior_channels_hash=digest([]),
            proposed_channels_hash=digest([CHANNEL]), approval_set_hash=digest([item.approval_id for item in candidates]),
            old_process=old_process, channels=rows, now=self.w.now)

    async def test_root_factory_owns_one_scheduler_task_and_borrowed_lifetimes(self):
        self.w.first_scope_status = 0  # Actual native denial prevents setup effects.
        host = self.make_host(); self.w.loop = asyncio.get_running_loop()
        task = asyncio.create_task(host.main())
        owned_tasks = []
        try:
            await self.wait_for(lambda: host.onboarding is not None, 'actual root service factory arrival')
            runtime = self.admission_runtime(host)
            await self.wait_for(lambda: bool(self.runtime_supervisor_tasks(host, runtime)),
                'actual Hostd.supervise wrapper holds the bound admission run callable')
            owned_tasks = list(host._tasks)
            runs = self.runtime_supervisor_tasks(host, runtime)
            self.assertEqual(len(runs), 1)
            self.assertIs(runtime.store, host.runtime_store)
            self.assertIs(runtime.service, host.onboarding)
            self.assertIs(runtime.scheduler, host.scheduler)
            self.assertIs(runtime.http_pool, host.http_pool)
            self.assertIs(host.own_home_admission.store, host.runtime_store)
            self.assertEqual(runtime.spec_for(PUB), host.onboarding.effects.specs[PUB])
            self.assertEqual(host.runtime_store.bindings(), [])
            self.assertFalse(self.w.posts)
        finally:
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(all(t.done() for t in owned_tasks))
        self.assertTrue(all(child.returncode is not None for child in self.w.sdk_children))
        self.assertTrue(host.scheduler.closed); self.assertTrue(host.http_pool.closed)
        self.assertIsNone(host.runtime_store)

    async def test_scan_schedules_only_pubkey_and_unknown_reopen_is_readback_only(self):
        exclusions = self.w.add_catalog_selection_exclusions()
        host = await self.started_host(); runtime = self.admission_runtime(host)
        self.assertEqual(self.w.records[PUB].channels, ())
        self.assertEqual(self.w.records[OTHER_PUB].channels, (OTHER_CHANNEL,))
        self.assertEqual(set(host.onboarding.records), {APP, OTHER_APP})
        self.assertEqual(set(host.onboarding.effects.specs), {PUB, OTHER_PUB})
        self.assertEqual(runtime.spec_for(PUB), host.onboarding.effects.specs[PUB])
        for item in exclusions.values():
            self.assertNotIn(item['pub'], host.onboarding.effects.specs)
            self.assertIsNone(runtime.spec_for(item['pub']))
            self.assertIsNone(self.db.conn.execute('SELECT 1 FROM agent WHERE pubkey=?',
                                                  (item['pub'],)).fetchone())
        coordinator = host.own_home_admission
        calls = []
        admit, readback = coordinator.admit, coordinator.readback
        async def observed_admit(pub):
            self.assertIs(type(pub), str); calls.append(('admit', pub))
            return await admit(pub)
        async def observed_readback(ident):
            calls.append(('readback', ident)); return await readback(ident)
        coordinator.admit, coordinator.readback = observed_admit, observed_readback
        self.w.system.lose_restart_response = True
        await runtime.scan_once()
        self.assertEqual(calls, [('admit', PUB)])
        row = self.db.active_own_home_admission(PUB)
        self.assertIsNotNone(row); self.assertEqual(row.state, 'unknown')
        self.assertEqual(self.w.system.restart_calls, 1)
        snapshot = {path: path.read_bytes() for path in (self.w.env, self.w.prompt, self.w.responsible)}
        runtime.stop(); await runtime.close()
        await host.close_onboarding()
        # Real new root/service/Store after UNKNOWN; no old runtime/store pointer.
        fresh = await self.started_host(); newer = self.admission_runtime(fresh)
        actual = fresh.own_home_admission; reads = []
        original_admit, original_read = actual.admit, actual.readback
        async def prohibited_admit(pub):
            reads.append(('admit', pub)); return await original_admit(pub)
        async def observed_read(ident):
            reads.append(('readback', ident)); return await original_read(ident)
        actual.admit, actual.readback = prohibited_admit, observed_read
        await newer.scan_once()
        self.assertEqual(reads, [('readback', row.admission_id)])
        self.assertEqual(self.w.system.restart_calls, 1)
        self.assertEqual({path: path.read_bytes() for path in snapshot}, snapshot)
        self.assertEqual(self.db.active_own_home_admission(PUB).admission_id, row.admission_id)
        self.assertFalse(self.w.posts)

    async def test_reserved_unknown_reopen_gate_real_dispatch_manager_runtime_and_sql_other_agent_progresses(self):
        host = await self.started_host(); service = host.onboarding
        original, candidates = await self.initial_discovery()
        adapter = importlib.import_module('hostd.own_admission').OwnHomeRestartAdapter(self.w.ops, service.effects.specs[PUB])
        old_process, _env = await asyncio.to_thread(adapter.capture, ())
        spec = service.effects.specs[PUB]
        prior_files = {path: Path(path).read_bytes() for path in (spec.env_file, spec.prompt_file, spec.responsible_file)}
        self.append_reviewed_fixture_channel()
        parts = await self.resolve(service, PUB)
        manager = self.manager(service, parts)
        active = await manager.reconcile(CHANNEL, expected_revision=0)
        self.assertEqual(active.status, 'active')
        control = self.w.new_event(marker='normal-before-marker')
        self.assertEqual((await manager.deliver(CHANNEL, control, expected_revision=active.revision)).status, 'acked')
        self.assertEqual(len(self.w.posts), 1, 'real normal control before race marker')
        grant = self.db.remote_grant(active.target_id)
        marker = await self.reserve_marker(service, original, candidates, old_process, prior_files)
        self.assertTrue(marker.created)
        event = self.w.new_event(marker='gated-source')
        runtime = self.runtime(parts)
        dispatch = self.dispatcher(service)
        api_before = sum(app == APP for app, _, _ in self.w.native_requests)
        for phase in ('reserved', 'unknown', 'reopened_unknown'):
            with self.subTest(phase=phase):
                result = await runtime.deliver(parts[1], event, target_id=grant.target_id,
                    revision=grant.revision, scope_hash=grant.scope_hash)
                self.assertEqual(result.status, 'pending')
                self.assertEqual((await manager.deliver(CHANNEL, event, expected_revision=grant.revision)).status, 'pending')
                await dispatch.refresh()
                self.assertNotIn((PUB, CHANNEL), dispatch._lanes)
                self.assertEqual(len(self.w.posts), 1)
                self.assertEqual(sum(app == APP for app, _, _ in self.w.native_requests), api_before,
                    'same-agent SQL admission gate reached before further native IO')
                self.assertFalse(self.db.refresh_remote_proof(grant.target_id, parts[-1],
                    revision=grant.revision, scope_hash=grant.scope_hash, now=self.w.now))
                with self.assertRaises(store.StoreError):
                    self.db.activate_remote_grant(parts[-1], expected_revision=grant.revision, now=self.w.now)
            if phase == 'reserved':
                self.assertTrue(self.db.mark_own_home_admission_unknown(marker.record.admission_id,
                    marker.record.snapshot_hash, now=self.w.now))
            elif phase == 'unknown':
                await manager.close(); await dispatch.close(); runtime.close()
                await host.close_onboarding()
                host = await self.started_host(); service = host.onboarding
                # Real fresh protected record/adapters, not cached old-env rejection.
                record = self.w.reload().records
                record = next(row for row in record if row.pubkey == PUB)
                reader, bot = self.w.adapters(record, CHAT)
                proofs = RemoteProofs(self.w.catalog_path, self.w.legacy_path, record=record, reader=reader, bot=bot, clock=lambda:self.w.now)
                parts = (record, parts[1], reader, bot, proofs, parts[-1])
                runtime = self.runtime(parts); manager = self.manager(service, parts); dispatch = self.dispatcher(service)
                api_before = sum(app == APP for app, _, _ in self.w.native_requests)
        # Same borrowed SQL and same actual signed remote dispatcher; the other
        # app/key/channel has no active admission and completes its own delivery.
        other = self.w.new_event(OTHER_PUB, 'independent-after-marker')
        await dispatch.refresh()
        other_id = store.Store._remote_digest([OTHER_PUB, OTHER_CHANNEL])
        self.assertEqual(self.db.remote_delivery_by_source(other_id, other['id'], 'message').status, 'acked')
        self.assertEqual(self.db.active_own_home_admission(PUB).state, 'unknown')
        self.assertIn((OTHER_PUB, OTHER_CHANNEL), dispatch._lanes)
        self.assertIsInstance(dispatch._lanes[(OTHER_PUB, OTHER_CHANNEL)].manager, RemoteManager)

    async def test_physical_ack_then_fresh_normal_proof_is_required_before_send(self):
        host = await self.started_host(); runtime = self.admission_runtime(host)
        host.http_pool.timeout = 600
        # Hold Source after the actual replacement, avoiding a fixture race
        # against the systemctl transport's five-second request timeout.
        self.w.block_final_member = True
        scan = asyncio.create_task(runtime.scan_once())
        try:
            await self.event_arrival(self.w.final_member_entered, 'actual postrestart Source-native UNKNOWN barrier')
            self.assertTrue(self.w.system.restart_entered.is_set(), 'the real restart boundary was reached')
            row = self.db.active_own_home_admission(PUB)
            self.assertIsNotNone(row); self.assertEqual(row.state, 'unknown')
            self.assertIsNone(self.db.remote_grant(store.Store._remote_digest([PUB, CHANNEL])))
            self.assertFalse(self.w.posts)
            # The first coordinator remains at real Source IO while the other
            # protected identity makes full signed/native/SQL dispatch progress.
            independent = self.w.new_event(OTHER_PUB, 'independent-during-own-unknown')
            parallel_dispatch = self.dispatcher(host.onboarding)
            await parallel_dispatch.refresh()
            other_id = store.Store._remote_digest([OTHER_PUB, OTHER_CHANNEL])
            other_receipt = self.db.remote_delivery_by_source(other_id, independent['id'], 'message')
            self.assertIsNotNone(other_receipt); self.assertEqual(other_receipt.status, 'acked')
            self.assertEqual(self.db.active_own_home_admission(PUB).admission_id, row.admission_id)
            self.assertEqual(self.db.active_own_home_admission(PUB).state, 'unknown')
            self.assertNotIn((PUB, CHANNEL), parallel_dispatch._lanes)
            self.assertIsNone(self.db.remote_grant(store.Store._remote_digest([PUB, CHANNEL])))
            self.assertEqual([app for app, _, _ in self.w.posts], [OTHER_APP])
            self.assertFalse(scan.done(), 'original own admission remains blocked until Source release')
        finally:
            self.w.final_member_release.set()
            await asyncio.gather(scan, return_exceptions=False)
        completed = self.db.own_home_admission(row.admission_id)
        self.assertEqual(completed.state, 'acked')
        self.assertEqual(self.w.system.restart_calls, 1)
        link = self.db.own_home_admission_restart(row.admission_id)
        self.assertEqual(self.db.restart_record(link.restart_operation_id).state, 'acked')
        self.assertIsNone(self.db.remote_grant(store.Store._remote_digest([PUB, CHANNEL])), 'physical ACK is never a grant')
        calls_before = len(self.w.api_calls); wires_before = len(self.w.wire_calls)
        self.w.reload()
        parts = await self.resolve(host.onboarding, PUB)
        expected = store.Store._remote_digest(sorted(self.w.records[PUB].channels))
        self.assertEqual(parts[-1].allowlist_hash, expected)
        dispatch = self.dispatcher(host.onboarding)
        event = self.w.new_event(marker='fresh-after-physical-ack')
        await dispatch.refresh()
        target_id = store.Store._remote_digest([PUB, CHANNEL])
        self.assertEqual(self.db.remote_delivery_by_source(target_id, event['id'], 'message').status, 'acked')
        self.assertGreater(len(self.w.wire_calls), wires_before)
        paths = [path for _, path, _, _ in self.w.api_calls[calls_before:]]
        self.assertIn('/open-apis/application/v6/scopes', paths)
        self.assertIn('/open-apis/im/v1/chats/' + CHAT + '/members/list', paths)
        self.assertIsInstance(dispatch._lanes[(PUB, CHANNEL)].manager, RemoteManager)
        self.assertEqual(self.db.bindings(), [])

    async def test_real_root_shutdown_repeated_cancel_joins_dispatched_io_before_borrowed_close(self):
        host = self.make_host(); self.w.loop = asyncio.get_running_loop()
        self.w.block_final_member = self.w.block_other_send = True
        self.w.new_event(OTHER_PUB, 'shutdown-owned-io')
        task = asyncio.create_task(host.main())
        try:
            await self.wait_for(lambda: host.onboarding is not None, 'actual root service factory arrival')
            runtime = self.admission_runtime(host)
            await self.event_arrival(self.w.final_member_entered, 'physical Source native postrestart IO arrival')
            await self.event_arrival(self.w.other_send_entered, 'independent actual remote native POST arrival')
            runtime = self.admission_runtime(host)
            db = host.runtime_store
            row = db.active_own_home_admission(PUB)
            self.assertIsNotNone(row); self.assertEqual(row.state, 'unknown')
            owned_tasks = list(host._tasks)
            task.cancel(); await asyncio.sleep(0)
            task.cancel(); await asyncio.sleep(0)
            self.assertFalse(task.done(), 'repeated root cancellation must join dispatched native IO')
            self.assertEqual(db.conn.execute('SELECT status FROM agent WHERE pubkey=?', (PUB,)).fetchone()[0], 'active')
            self.assertFalse(host.http_pool.closed); self.assertFalse(host.scheduler.closed)
            self.assertGreater(self.w.native_inflight, 0)
        finally:
            self.w.final_member_release.set(); self.w.other_send_release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(all(t.done() for t in owned_tasks))
        self.assertEqual(self.w.native_inflight, 0)
        self.assertTrue(all(child.returncode is not None for child in self.w.sdk_children))
        self.assertTrue(all(sock.closed for sock in self.w.sockets))
        self.assertTrue(host.http_pool.closed); self.assertTrue(host.scheduler.closed)
        self.assertIsNone(host.runtime_store)
        self.assertIsNone(db.conn, 'real Store connection closes only after original IO joins')
        reopened = store.Store(self.w.db.path)
        try:
            self.assertEqual(reopened.active_own_home_admission(PUB).state, 'unknown')
        finally:
            reopened.close()
        # Actual advisory lock is released after joined original cleanup.
        spec = runtime.spec_for(PUB)
        fd = os.open(Path(spec.timer_state_dir) / 'join.lock', os.O_RDWR | os.O_NOFOLLOW)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally: os.close(fd)
