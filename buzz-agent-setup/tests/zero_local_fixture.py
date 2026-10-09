"""Test-owned protected topology and low transport containment; no product API.

Imported only AFTER the test's missing-support assertion. Never imports the
candidate's fixtures or replaces Hostd, factories, authority, Registry or Store.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import uuid

CHANNEL = '00000000-0000-0000-0000-000000000001'
SECRET = 'ZERO_LOCAL_SYNTHETIC_SECRET'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(mode)


# This is installed ONLY by the test's Popen seam, never plan.environment().
# It changes native socket/DNS transports only. No Hostd modules are imported.
STARTUPWIRE = '''import ipaddress, os, socket
_origin_port = int(os.environ['ZERO_LOCAL_RELAY_PORT'])
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
_sendto = socket.socket.sendto
def _admit(sock, address):
    if sock.family == socket.AF_UNIX:
        return
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        raise OSError('test transport denied')
    try:
        host, port = address[:2]
        if str(ipaddress.ip_address(host)) != '127.0.0.1' or port != _origin_port:
            raise ValueError()
    except (ValueError, TypeError):
        raise OSError('test transport denied') from None
def _guard_connect(sock, address):
    _admit(sock, address)
    return _connect(sock, address)
def _guard_connect_ex(sock, address):
    _admit(sock, address)
    return _connect_ex(sock, address)
def _guard_addrinfo(host, port, *args, **kwargs):
    if host != '127.0.0.1' or int(port) != _origin_port:
        raise OSError('test DNS denied')
    return _getaddrinfo(host, port, *args, **kwargs)
def _guard_sendto(sock, *args):
    _admit(sock, args[-1])
    return _sendto(sock, *args)
socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex
socket.getaddrinfo = _guard_addrinfo
socket.socket.sendto = _guard_sendto
'''


class SignedRelay:
    """Actual TLS and NIP98 owner proof, actual canonical signed event bytes."""
    def __init__(self, root, gs, owner_key, agent_key, auth, relay_key):
        # Dependency failure is explicit; no broad skip or fake crypto fallback.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        self.gs, self.owner = gs, gs._signer_pubkey(owner_key)
        self.received, self.failures = [], []
        self.thread = None
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        self.cert, key_path = root/'ca.pem', root/'tls-key.pem'
        write(self.cert, cert.public_bytes(serialization.Encoding.PEM))
        write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        stamp = int(time.time())
        pub = gs._signer_pubkey(agent_key)
        self.events = [
            gs.sign_event(agent_key, 0, [auth], '{}', stamp),
            gs.sign_event(owner_key, 30177, [['d', pub]], json.dumps({'feishu': {'app_id': 'cli_agent'}}), stamp),
            gs.sign_event(relay_key, 39002, [['d', CHANNEL], ['p', self.owner, '', 'owner']], '', stamp),
        ]
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                try:
                    assert self.path == '/query'
                    size = int(self.headers['Content-Length'])
                    assert 0 < size <= 65536
                    body = self.rfile.read(size)
                    header = self.headers['Authorization']
                    assert header.startswith('Nostr ')
                    event = json.loads(base64.b64decode(header.split(' ', 1)[1], validate=True))
                    assert outer.gs._nip01_event_verified(event)
                    assert event['pubkey'] == outer.owner and event['kind'] == 27235
                    assert abs(event['created_at']-int(time.time())) <= 60
                    for name, value in (('u', outer.origin+'/query'), ('method', 'POST'), ('payload', hashlib.sha256(body).hexdigest())):
                        assert [t for t in event['tags'] if t[0] == name] == [[name, value]]
                    filters = json.loads(body)
                    assert isinstance(filters, list) and 0 < len(filters) <= 256
                    outer.received.append(filters)  # No keys or request signatures logged.
                    result = []
                    for fil in filters:
                        for row in outer.events:
                            if 'kinds' in fil and row['kind'] not in fil['kinds']: continue
                            if 'authors' in fil and row['pubkey'] not in fil['authors']: continue
                            if '#d' in fil and not any(t[0] == 'd' and t[1] in fil['#d'] for t in row['tags']): continue
                            if row not in result: result.append(row)
                    answer = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(answer)))
                    self.end_headers()
                    self.wfile.write(answer)
                except Exception:
                    outer.failures.append('signed TLS request rejected')
                    self.send_error(400, 'fixture request rejected')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = 'https://127.0.0.1:'+str(self.server.server_port)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, key_path)
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)

    def start(self):
        assert self.thread is None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=5)
            assert not self.thread.is_alive()
        self.server.server_close()


class Fixture:
    def __init__(self, parent, source_root, revision, source_hashes):
        import buzz_feishu_group_sync as gs
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.source_root, self.revision, self.source_hashes = source_root, revision, source_hashes
        self.run_id = uuid.uuid4().hex
        self.root = parent/('run-'+self.run_id)
        self.root.mkdir(mode=0o700)
        self.home, self.runtime = self.root/'home', self.root/'runtime'
        self.registry = self.home/'.config'/'buzz-feishu-sync'
        self.binding_dir = self.root/'bindings'
        for directory in (self.home, self.runtime, self.registry, self.binding_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # All task-created directories are owner-only, including parents.
        for directory in self.root.rglob('*'):
            if directory.is_dir(): directory.chmod(0o700)
        self.agent_key, self.owner_key, relay_key, source_key = [str(n).zfill(64) for n in (1, 2, 3, 4)]
        self.agent_pub = gs._signer_pubkey(self.agent_key)
        self.owner = gs._signer_pubkey(self.owner_key)
        self.relay_pub = gs._signer_pubkey(relay_key)
        self.source_owner = gs._signer_pubkey(source_key)
        signature = gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{self.agent_pub}:'.encode()).digest(), bytes.fromhex(self.owner_key), bytes(32)).hex()
        auth = ['auth', self.owner, '', signature]
        self.relay = SignedRelay(self.root, gs, self.owner_key, self.agent_key, auth, relay_key)
        self.env, self.owner_env = self.root/'agent.env', self.root/'owner.env'
        self.prompt, self.responsible = self.root/'prompt.md', self.root/'responsible.json'
        write(self.prompt, 'synthetic local agent')
        write(self.responsible, '{}')
        write(self.env, f'BUZZ_PRIVATE_KEY={self.agent_key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_AUTH_TAG=\'{json.dumps(auth)}\'\nBUZZ_RELAY_URL={self.relay.origin}\nBUZZ_ACP_CHANNELS={CHANNEL}\nBUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n')
        write(self.owner_env, f'BUZZ_PRIVATE_KEY={self.owner_key}\nBUZZ_RELAY_URL={self.relay.origin}\n')
        self.cfg, self.data = self.root/'profile', self.root/'data'
        self.profile = self.cfg/'config.json'
        write(self.profile, json.dumps({'apps': [{'appId': 'cli_agent', 'name': 'local'}]}))
        key, nonce = b'k'*32, b'n'*12
        write(self.data/'lark-cli'/'master.key', key)
        write(self.data/'lark-cli'/'appsecret_cli_agent.enc', nonce+AESGCM(key).encrypt(nonce, SECRET.encode(), None))
        self.node, self.entry = self.root/'lark-cli', self.root/'run.js'
        # This executable is a low CLI fail-closed boundary, not an authority.
        write(self.node, '#!'+sys.executable+'\nimport sys\nsys.exit(97)\n', 0o700)
        write(self.entry, '// test-only CLI transport: no service calls\n')
        self.catalog, self.legacy, self.template, self.onboarding = [self.root/name for name in ('catalog.json', 'legacy.json', 'template.json', 'onboarding.json')]
        doc = {'version': 1, 'owner_pubkey': self.owner, 'buzz': {'cli_path': str(self.node), 'cli_sha256': digest(self.node)}, 'state_dir': str(self.root/'join-state'), 'lark_cli': str(self.node), 'agents': [{'name': 'local', 'env_file': str(self.env), 'unit': 'buzz-local-local.service', 'capabilities': {'summary': 'test', 'repos': []}, 'feishu': {'app_id': 'cli_agent', 'lark_config_dir': str(self.cfg), 'lark_data_dir': str(self.data)}}]}
        write(self.catalog, json.dumps(doc))
        write(self.legacy, json.dumps(dict(doc, agents=[])))
        write(self.template, '{}')
        write(self.onboarding, json.dumps({'version': 1, 'owner_env_file': str(self.owner_env), 'relay_url': self.relay.origin, 'relay_pubkey': self.relay_pub, 'template_config': str(self.template), 'binding_dir': str(self.binding_dir), 'legacy_join_path': str(self.legacy), 'catalog_path': str(self.catalog), 'trusted_relays': [self.relay.origin]}))
        self.canary = self.root/'canary'
        write(self.canary, 'input must remain unchanged')
        self.wire_dir = self.root/'startupwire'
        self.wire_file = self.wire_dir/'sitecustomize.py'
        write(self.wire_file, STARTUPWIRE)
        for directory in self.root.rglob('*'):
            if directory.is_dir(): directory.chmod(0o700)
        self.outputs = (self.runtime/'hostd.sqlite3', self.runtime/'status.json', self.runtime/'console')
        self.manifest = self.root/'manifest.json'
        self.doc = {'version': 1, 'run_id': self.run_id, 'run_dir': str(self.root), 'source_root': str(source_root), 'revision': revision, 'source_hashes': source_hashes, 'python': str(Path(sys.executable).absolute()), 'home': str(self.home), 'runtime_dir': str(self.runtime), 'registry_root': str(self.registry), 'state_db': str(self.outputs[0]), 'status_file': str(self.outputs[1]), 'console_dir': str(self.outputs[2]), 'onboarding_config': str(self.onboarding), 'owner_env_file': str(self.owner_env), 'relay_url': self.relay.origin, 'relay_pubkey': self.relay_pub, 'trusted_relays': [self.relay.origin], 'template_config': str(self.template), 'binding_dir': str(self.binding_dir), 'legacy_join_path': str(self.legacy), 'catalog_path': str(self.catalog), 'trust_bundle': str(self.relay.cert), 'node_binary': str(self.node), 'lark_cli_entry': str(self.entry), 'selector': 'hostd-msg003-none-'+self.run_id, 'channel_id': CHANNEL, 'source_owner_pubkey': self.source_owner, 'agent_pubkeys': [self.agent_pub], 'app_profiles': {'cli_agent': {'profile': 'local', 'config_dir': str(self.cfg), 'data_dir': str(self.data)}}, 'input_sha256': {}}
        self.repin()
        self.check_args = {'reviewed_source': source_root, 'reviewed_revision': revision, 'reviewed_source_hashes': dict(source_hashes), 'expected_host_b_owner': self.owner, 'expected_relay_pubkey': self.relay_pub, 'expected_channel_id': CHANNEL, 'expected_source_owner_pubkey': self.source_owner, 'expected_agent_pubkeys': (self.agent_pub,), 'expected_app_profiles': json.loads(json.dumps(self.doc['app_profiles']))}

    def repin(self):
        self.doc['input_sha256'] = {str(p.relative_to(self.root)): digest(p) for p in self.root.rglob('*') if p.is_file() and p != self.manifest}
        write(self.manifest, json.dumps(self.doc, sort_keys=True))

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in self.root.rglob('*') if p.is_file()}

    def mutate(self, name):
        if name in ('selector_collision', 'local_target_binding'):
            selector = self.doc['selector'] if name == 'selector_collision' else 'other-local'
            cfg = {'channel_id': CHANNEL, 'chat_id': 'oc_fixture', 'desk_pubkey': self.agent_pub, 'mirror_pubkey': self.agent_pub, 'mirror_env_file': str(self.env), 'agents': {self.agent_pub: {'app_id': 'cli_agent', 'lark_config_dir': str(self.cfg), 'lark_data_dir': str(self.data)}}}
            write(self.registry/selector/'config.json', json.dumps(cfg))
            self.repin()
        elif name == 'wrong_owner':
            write(self.owner_env, f'BUZZ_PRIVATE_KEY={self.agent_key}\nBUZZ_RELAY_URL={self.relay.origin}\n')
            self.repin()
        elif name == 'wrong_source_owner':
            self.doc['source_owner_pubkey'] = self.owner
            self.repin()
        elif name == 'wrong_relay':
            self.doc['relay_pubkey'] = self.source_owner
            self.repin()
        elif name == 'wrong_profile':
            self.doc['app_profiles']['cli_agent']['profile'] = 'other'
            self.repin()
        elif name == 'run_id_drift':
            self.doc['run_id'] = 'f'*32
            self.repin()
        elif name == 'source_revision_drift':
            self.doc['revision'] = 'f'*40
            self.repin()
        elif name == 'catalog_drift':
            write(self.catalog, self.catalog.read_bytes()+b' ')
        elif name == 'profile_drift':
            write(self.profile, self.profile.read_bytes()+b' ')
        else:
            raise AssertionError(name)

    def close(self):
        self.relay.close()


def db_readback(path, channel=CHANNEL):
    """Independent mode=ro only. Never instantiate Store in prepared phase."""
    assert path.is_file()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        bindings = db.execute('SELECT binding_id,channel_id FROM binding ORDER BY binding_id').fetchall()
        agents = db.execute('SELECT pubkey,owner_pubkey,app_id,status FROM agent ORDER BY pubkey').fetchall()
    return bindings, [b for b in bindings if b[1] == channel], agents


def process_identity(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        fields = text[text.rindex(')')+2:].split()
        return (int(pid), int(fields[3]), int(fields[19]))  # pid, session, starttime
    except (FileNotFoundError, ProcessLookupError):
        return None


def session_members(session):
    found = set()
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            ident = process_identity(int(entry.name))
            if ident is not None and ident[1] == session: found.add(ident)
    return found


class CapturePopen:
    """Preserve exact normal argv and Popen; add only child-local wire seam."""
    def __init__(self, case, plan):
        self.case, self.plan = case, plan
        self.process = self.identity = self.env = self.argv = None
        self.observed_members = set()
        self.original_env = dict(plan.environment())
        self.wire_hash = digest(case.wire_file)

    def __call__(self, argv, **kwargs):
        assert self.process is None  # Cannot adopt or relaunch a different PID.
        assert list(argv) == self.plan.argv()
        assert dict(kwargs['env']) == self.original_env
        assert kwargs.get('start_new_session') is True
        assert kwargs.get('close_fds') is True and not kwargs.get('pass_fds')
        assert kwargs.get('umask') == 0o077
        assert self.original_env.get('PYTHONDONTWRITEBYTECODE') == '1'
        assert kwargs.get('stdin') == subprocess.DEVNULL
        assert digest(self.case.wire_file) == self.wire_hash
        self.argv, self.env = list(argv), dict(kwargs['env'])
        environment = dict(self.env)
        assert environment['PYTHONPATH'] == str(self.case.source_root/'skills/agent-harness/buzz-agent-setup/scripts')
        environment['PYTHONPATH'] = str(self.case.wire_dir)+os.pathsep+environment['PYTHONPATH']
        environment['ZERO_LOCAL_RELAY_PORT'] = str(self.case.relay.server.server_port)
        kwargs['env'] = environment
        self.process = subprocess.Popen(argv, **kwargs)
        self.identity = process_identity(self.process.pid)
        assert self.identity is not None and self.identity[1] == self.process.pid
        self.observed_members.add(self.identity)
        return self.process

    def emergency_reap(self):
        # Failsafe only the original, still-owned session; no reused PID kill.
        if self.process is not None:
            if any(process_identity(member[0]) == member for member in self.observed_members):
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            self.process.wait(timeout=10)


def sibling_canary():
    return subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, start_new_session=True, close_fds=True)


def reap_sibling(proc):
    if proc.poll() is None: proc.terminate()
    proc.wait(timeout=5)
