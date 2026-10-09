"""Synthetic native TLS service and signed Relay; no high-level runtime seam.

This extends only generated private inputs, the test-owned TLS request handler,
and one child's native HTTPS connection transport. Original Fixture is unchanged.
No Hostd/Manager/Proofs/Runtime/Pool/Store import or replacement occurs here.
"""
from __future__ import annotations

import base64
from contextlib import closing
import copy
import hashlib
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

CHANNEL = '00000000-0000-0000-0000-000000000001'
APP = 'cli_agent'
SCOPES = ('im:chat.group_info:readonly', 'im:message:readonly', 'im:message:send')
PREFIX = 'hostd-card-approval-v1'
TOKEN_PATH = '/open-apis/auth/v3/tenant_access_token/internal'
TEXT = 'SYNTHETIC_NORMAL_REMOTE_TEXT_REPLY'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def readonly(path):
    """Only actual original runtime SQL; never opens a Store or creates a file."""
    path = Path(path)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return {table: [dict(row) for row in db.execute('SELECT * FROM '+table)]
                for table in ('binding', 'agent', 'remote_target', 'remote_grant', 'remote_delivery')}


def redirect_wire(chat, root_mid, sent_mid):
    """Literal exact native host/path allowlist; retains real verified TLS context."""
    native_paths = [TOKEN_PATH, '/open-apis/im/v1/chats',
                    '/open-apis/im/v1/chats/'+chat+'/members/list',
                    '/open-apis/application/v6/scopes', '/open-apis/im/v1/messages',
                    '/open-apis/im/v1/messages/'+root_mid,
                    '/open-apis/im/v1/messages/'+sent_mid,
                    '/open-apis/im/v1/messages/'+root_mid+'/reply']
    return '''
import http.client, ssl
from urllib.parse import urlsplit as _zero_urlsplit
_zero_https = http.client.HTTPSConnection
_zero_https_init = _zero_https.__init__
_zero_https_request = _zero_https.request
_zero_native_paths = frozenset('''+repr(native_paths)+''')
class _ZeroNativeHTTPS(_zero_https):
    def __init__(self, host, port=None, *args, **kwargs):
        context = kwargs.get('context')
        if context is None or context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise OSError('test verified TLS required')
        self._zero_native = host == 'open.feishu.cn' and port in (None, 443)
        if self._zero_native:
            host, port = '127.0.0.1', _origin_port
        elif host != '127.0.0.1' or port != _origin_port:
            raise OSError('test HTTPS endpoint denied')
        _zero_https_init(self, host, port, *args, **kwargs)
    def request(self, method, url, *args, **kwargs):
        parts = _zero_urlsplit(url)
        if parts.scheme or parts.netloc or parts.fragment:
            raise OSError('test HTTP path denied')
        if self._zero_native:
            if parts.path not in _zero_native_paths or method not in ('GET', 'POST'):
                raise OSError('test native path denied')
            if method == 'POST' and parts.path not in (
                '''+repr(TOKEN_PATH)+''', '''+repr('/open-apis/im/v1/messages/'+root_mid+'/reply')+'''):
                raise OSError('test native write denied')
        elif method != 'POST' or url != '/query':
            raise OSError('test Relay path denied')
        return _zero_https_request(self, method, url, *args, **kwargs)
_zero_https.__init__ = _ZeroNativeHTTPS.__init__
_zero_https.request = _ZeroNativeHTTPS.request
'''


class RemoteTextFixture:
    """All service identities are synthetic; only low wire/native APIs are built."""
    def __init__(self, case, original_fixture, *, denial=None, ambiguous=False):
        if denial not in (None, 'missing_approval', 'incomplete_scopes'):
            raise ValueError('unknown finite denial')
        self.case, self.original, self.gs = case, original_fixture, case.relay.gs
        self.denial, self.ambiguous = denial, ambiguous
        self.lock = threading.Lock()
        self.release_get = threading.Event()
        if not ambiguous:
            self.release_get.set()
        self.posted = threading.Event()
        self.blocked_get = threading.Event()
        self.failures, self.trace, self.posts, self.unknown_before_post = [], [], [], []
        self.signed_queries, self.native_calls, self.exact_gets, self.list_reads = [], [], [], []
        self.chat = 'oc_'+case.run_id
        self.root_mid, self.sent_mid = 'om_root'+case.run_id, 'om_sent'+case.run_id
        self.token = 'synthetic-token-'+case.run_id
        self.now = int(time.time())
        self.mirror_key = '5'.zfill(64)
        self.mirror = self.gs._signer_pubkey(self.mirror_key)
        self.claimed = self.now-120
        self._build_packets()
        self.messages = {self.root_mid: {
            'message_id': self.root_mid, 'chat_id': self.chat, 'root_id': self.root_mid,
            'thread_id': 'omt_'+self.root_mid, 'create_time': str((self.now-2)*1000),
            'msg_type': 'text', 'sender': {'sender_type': 'user', 'id_type': 'open_id',
                                        'id': 'ou_synthetic'+case.run_id},
            'body': {'content': encoded({'text': 'SYNTHETIC_ORIGINAL_FOREIGN_ROOT'})}}}
        # Real packets, exact native fixture schema, and wire bytes are protected
        # inputs; all modifications finish BEFORE Plan.check and full real repin.
        self.wire_path = case.root/'remote-text-wire.json'
        self.wire = {'version': 1, 'events': self.events, 'native_root': self.messages[self.root_mid],
                     'approval_card': self.approval_card, 'decision_event': self.decision_event,
                     'scope_names': list(SCOPES), 'denial': denial, 'ambiguous': ambiguous,
                     'chat': self.chat, 'sent_mid': self.sent_mid, 'token': self.token}
        original_fixture.write(self.wire_path, encoded(self.wire))
        self.wire_hash = original_fixture.digest(self.wire_path)
        config = json.loads(case.onboarding.read_text())
        config['remote_link_base'] = case.relay.origin
        original_fixture.write(case.onboarding, encoded(config))
        original_fixture.write(case.wire_file, original_fixture.STARTUPWIRE+
                               redirect_wire(self.chat, self.root_mid, self.sent_mid)+
                               '\nimport faulthandler\nfaulthandler.enable()\n')
        self._install_handler()
        case.repin()

    def _auth(self, key, owner_key):
        pub, owner = self.gs._signer_pubkey(key), self.gs._signer_pubkey(owner_key)
        message = hashlib.sha256(('nostr:agent-auth:'+pub+':').encode()).digest()
        signature = self.gs.sync.nk.schnorr_sign(message, bytes.fromhex(owner_key), bytes(32)).hex()
        return ['auth', owner, '', signature]

    def _build_packets(self):
        c, gs = self.case, self.gs
        self.agent_auth = self._auth(c.agent_key, c.owner_key)
        source_key, relay_key = '4'.zfill(64), '3'.zfill(64)
        assert gs._signer_pubkey(source_key) == c.source_owner
        assert len({c.agent_pub, c.owner, c.source_owner, self.mirror, c.relay_pub}) == 5
        own_profile = gs.sign_event(c.agent_key, 0, [self.agent_auth], '{}', self.now)
        own_policy = gs.sign_event(c.owner_key, 30177, [['d', c.agent_pub]],
                                   encoded({'feishu': {'app_id': APP}}), self.now)
        mirror_profile = gs.sign_event(self.mirror_key, 0, [self._auth(self.mirror_key, source_key)], '{}', self.now)
        mirror_policy = gs.sign_event(source_key, 30177, [['d', self.mirror]], encoded({'feishu': {
            'mirror': True, 'bindings': [{'channel': CHANNEL, 'chat_ref': gs.chat_ref(self.chat),
                                         'claimed_at': self.claimed, 'heartbeat': self.now}]}}), self.now)
        roster = gs.sign_event(relay_key, 39002, [['d', CHANNEL], ['p', c.agent_pub, '', 'bot'],
            ['p', c.owner, '', 'member'], ['p', self.mirror, '', 'bot'],
            ['p', c.source_owner, '', 'owner']], '', self.now)
        self.approval_card = {'version': 1, 'synthetic': True, 'request_id': 'JOIN-'+c.run_id[:8],
                              'agent': c.agent_pub, 'channel': CHANNEL, 'chat_ref': gs.chat_ref(self.chat)}
        card_hash = hashlib.sha256(encoded(self.approval_card).encode()).hexdigest()
        self.decision_event = gs.sign_event(source_key, 7,
            [['h', CHANNEL], ['e', card_hash]], '+', self.now-1)
        body = dict(version=1, decision='approve', agent_pubkey=c.agent_pub,
                    agent_owner_pubkey=c.owner, app_id=APP, channel_id=CHANNEL,
                    chat_ref=gs.chat_ref(self.chat), mirror_pubkey=self.mirror,
                    mirror_owner_pubkey=c.source_owner, claimed_at=self.claimed,
                    request_id='JOIN-'+c.run_id[:8], request_created_at=self.now-60,
                    request_deadline=self.now-60+7*86400, card_generation=1,
                    card_message_sha256=card_hash,
                    decision_event_sha256=hashlib.sha256(encoded(self.decision_event).encode()).hexdigest(),
                    decision_at=self.now-1)
        raw = encoded(body)
        self.approval_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.approval = gs.sign_event(self.mirror_key, 30078,
            [['t', PREFIX], ['h', CHANNEL], ['p', c.agent_pub], ['d', PREFIX+':'+self.approval_hash]], raw, self.now)
        self.root_event = gs.sign_event(self.mirror_key, 9,
            [['h', CHANNEL], ['feishu', self.root_mid], ['feishu-root', self.root_mid]],
            'SYNTHETIC_ORIGINAL_FOREIGN_ROOT', self.now-2)
        self.reply = gs.sign_event(c.agent_key, 9,
            [['h', CHANNEL], ['e', self.root_event['id'], '', 'root'],
             ['e', self.root_event['id'], '', 'reply']], TEXT, self.now-1)
        self.events = [own_profile, own_policy, mirror_profile, mirror_policy, roster,
                       self.root_event, self.reply]
        if self.denial != 'missing_approval':
            self.events.append(self.approval)
        self.event_ids = {row['kind']: row['id'] for row in (own_profile, own_policy, roster)}
        self.mirror_policy_id = mirror_policy['id']

    def record(self, kind, value):
        with self.lock:
            self.trace.append((len(self.trace), kind, copy.deepcopy(value)))

    def snapshot(self):
        with self.lock:
            return {name: copy.deepcopy(getattr(self, name)) for name in
                    ('failures', 'trace', 'posts', 'unknown_before_post', 'signed_queries',
                     'native_calls', 'exact_gets', 'list_reads', 'messages')}

    def _seal(self):
        assert self.original.digest(self.wire_path) == self.wire_hash
        assert json.loads(self.wire_path.read_text()) == self.wire

    def _query_clock(self, principal):
        return time.time()

    def _query(self, headers, body):
        self._seal()
        header = headers.get('Authorization', '')
        assert header.startswith('Nostr ')
        event = json.loads(base64.b64decode(header.split(' ', 1)[1], validate=True))
        assert self.gs._nip01_event_verified(event) and event['kind'] == 27235
        principal = event['pubkey']
        assert abs(event['created_at']-int(self._query_clock(principal))) <= 60
        for name, value in (('u', self.case.relay.origin+'/query'), ('method', 'POST'),
                            ('payload', hashlib.sha256(body).hexdigest())):
            assert [t for t in event['tags'] if t[0] == name] == [[name, value]]
        assert principal in (self.case.owner, self.case.agent_pub)
        if principal == self.case.agent_pub:
            auth = json.loads(headers.get('x-auth-tag', 'null'))
            assert auth == self.agent_auth and len(auth) == 4 and auth[2] == ''
            digest = hashlib.sha256(('nostr:agent-auth:'+principal+':').encode()).digest()
            assert self.gs.sync.nk.schnorr_verify(digest, bytes.fromhex(self.case.owner), bytes.fromhex(auth[3]))
        else:
            assert headers.get('x-auth-tag') is None
        filters = json.loads(body)
        assert isinstance(filters, list) and 1 <= len(filters) <= 256
        result = {}
        for fil in filters:
            assert isinstance(fil, dict) and fil and not set(fil)-{
                'kinds','authors','ids','#d','#h','#feishu','#e','since','until','limit'}
            # The production owner reader has one dedicated complete policy
            # snapshot contract; ordinary queries retain their smaller budget.
            policy_snapshot = filters == [{'kinds': [30177], 'limit': 1000}]
            assert type(fil.get('limit', 257)) is int and 1 <= fil.get('limit', 257) <= (1000 if policy_snapshot else 257)
            if 'since' in fil: assert type(fil['since']) is int and fil['since'] >= 0
            if 'until' in fil: assert type(fil['until']) is int and fil['until'] >= 0
            if 'since' in fil and 'until' in fil: assert fil['since'] <= fil['until']
            for name, values in fil.items():
                if name not in ('limit', 'since', 'until'):
                    assert isinstance(values, list) and 1 <= len(values) <= 256
            if '#h' in fil: assert fil['#h'] == [CHANNEL]
            if fil.get('kinds') == [39002] and '#d' in fil: assert fil['#d'] == [CHANNEL]
            matches = []
            for row in self.wire['events']:
                if 'kinds' in fil and row['kind'] not in fil['kinds']: continue
                if 'authors' in fil and row['pubkey'] not in fil['authors']: continue
                if 'ids' in fil and row['id'] not in fil['ids']: continue
                if 'since' in fil and row['created_at'] < fil['since']: continue
                if 'until' in fil and row['created_at'] > fil['until']: continue
                if any(name.startswith('#') and not any(len(t) >= 2 and t[0] == name[1:] and t[1] in values
                    for t in row['tags']) for name, values in fil.items()): continue
                matches.append(row)
            for row in sorted(matches, key=lambda r: (-r['created_at'], r['id']))[:fil.get('limit', 257)]:
                result[row['id']] = row
        with self.lock:
            self.signed_queries.append({'principal': principal, 'filters': copy.deepcopy(filters)})
        self.record('signed_query', {'principal': principal, 'filters': filters})
        return list(result.values())

    def _native(self, method, path, headers, body):
        self._seal()
        parts = urlsplit(path)
        assert not parts.scheme and not parts.netloc and not parts.fragment
        params = parse_qs(parts.query, strict_parsing=True)
        assert all(len(v) == 1 for v in params.values())
        params = {k: v[0] for k, v in params.items()}
        endpoint = parts.path
        with self.lock:
            self.native_calls.append((method, endpoint, dict(params)))
        self.record('native_request', (method, endpoint))
        if endpoint == TOKEN_PATH:
            assert method == 'POST' and not params
            payload = json.loads(body)
            assert set(payload) == {'app_id', 'app_secret'}
            assert payload['app_id'] == APP and payload['app_secret'] == self.original.SECRET
            return 200, {'code': 0, 'tenant_access_token': self.token, 'expire': 7200}, False
        assert headers.get('Authorization') == 'Bearer '+self.token
        if method == 'GET' and endpoint == '/open-apis/im/v1/chats':
            assert params == {'page_size': '100'}
            data = {'items': [{'chat_id': self.chat}], 'has_more': False, 'page_token': ''}
        elif method == 'GET' and endpoint == '/open-apis/im/v1/chats/'+self.chat+'/members/list':
            assert params.get('page_size') == '100' and set(params) == {'page_size','member_id_type','member_types'}
            pair = (params['member_id_type'], params['member_types'])
            assert pair in (('union_id', 'user'), ('open_id', 'bot'), ('open_id', 'user,bot'))
            data = {'users': [], 'bots': [] if pair[1] == 'user' else [{'member_id': 'ou_own'+self.case.run_id, 'app_id': APP}],
                    'user_total': 0, 'bot_total': 1, 'truncations': [], 'has_more': False, 'page_token': ''}
            if pair[1] == 'user': data.pop('bot_total')
        elif method == 'GET' and endpoint == '/open-apis/application/v6/scopes':
            assert not params
            data = {'scopes': [{'scope_name': name, 'scope_type': 'tenant', 'grant_status': 1} for name in SCOPES]}
            if self.denial == 'incomplete_scopes':
                data.update(has_more=True, page_token='unread-native-scope-page')
        elif method == 'POST' and endpoint == '/open-apis/im/v1/messages/'+self.root_mid+'/reply':
            assert not params
            payload = json.loads(body)
            assert set(payload) == {'msg_type','content','uuid','reply_in_thread'}
            assert payload['msg_type'] == 'interactive' and payload['reply_in_thread'] is True
            assert re.fullmatch('hr-[0-9a-f]{40}', payload['uuid'])
            card = json.loads(payload['content'])
            elements = card['elements']
            assert any(e.get('tag') == 'div' and e.get('text', {}).get('content') == TEXT for e in elements)
            state = readonly(self.case.outputs[0])
            assert state is not None and state['binding'] == []
            deliveries = [r for r in state['remote_delivery'] if r['source_id'] == self.reply['id'] and r['action'] == 'message']
            assert len(deliveries) == 1 and deliveries[0]['status'] == 'unknown'
            delivery = deliveries[0]
            assert delivery['root_id'] == self.root_event['id']
            assert delivery['content_hash'] == hashlib.sha256(payload['content'].encode()).hexdigest()
            assert payload['uuid'] == 'hr-'+delivery['id'][:40]
            grants = [g for g in state['remote_grant'] if (g['target_id'],g['revision'],g['scope_hash']) ==
                      (delivery['target_id'],delivery['revision'],delivery['scope_hash'])]
            assert len(grants) == 1 and grants[0]['approval_id'] == self.approval['id']
            with self.lock:
                assert not self.posts  # A recovery resend is an immediate fixture failure.
                self.unknown_before_post.append(copy.deepcopy(state))
                self.posts.append({'path': endpoint, 'data': copy.deepcopy(payload), 'delivery': copy.deepcopy(delivery)})
                self.messages[self.sent_mid] = {'message_id': self.sent_mid, 'chat_id': self.chat,
                    'root_id': self.root_mid, 'thread_id': 'omt_'+self.root_mid,
                    'create_time': str(int(time.time())*1000), 'msg_type': 'interactive',
                    'sender': {'sender_type':'app','id_type':'app_id','id':APP},
                    'body': {'content':payload['content']}}
            self.record('physical_post_persisted', {'source': self.reply['id'], 'mid': self.sent_mid, 'unknown': True})
            self.posted.set()
            return 200, {'code':0, 'data':{'message_id':self.sent_mid}}, self.ambiguous
        elif method == 'GET' and endpoint == '/open-apis/im/v1/messages':
            assert set(params) <= {'container_id_type','container_id','sort_type','page_size','card_msg_content_type','with_sender_name','only_thread_root_messages','start_time'}
            assert params.get('container_id_type') in ('thread', 'chat')
            assert params.get('container_id') == ('omt_'+self.root_mid if params['container_id_type'] == 'thread' else self.chat)
            assert params.get('page_size') == '50' and params.get('card_msg_content_type') == 'user_card_content'
            assert params.get('with_sender_name') == 'true' and params.get('sort_type') in ('ByCreateTimeDesc','ByCreateTimeAsc')
            with self.lock:
                self.list_reads.append(dict(params))
                rows = copy.deepcopy(list(self.messages.values()))
            if params['container_id_type'] == 'chat' and params.get('only_thread_root_messages') == 'true':
                rows = [r for r in rows if r['root_id'] == r['message_id']]
            rows.sort(key=lambda r:(int(r['create_time']),r['message_id']), reverse=params['sort_type']=='ByCreateTimeDesc')
            data = {'items':rows,'has_more':False,'page_token':''}
        elif method == 'GET' and endpoint in ('/open-apis/im/v1/messages/'+self.root_mid, '/open-apis/im/v1/messages/'+self.sent_mid):
            assert params == {'user_id_type':'open_id', 'card_msg_content_type':'user_card_content'}
            mid = endpoint.rsplit('/',1)[-1]
            with self.lock:
                self.exact_gets.append(mid)
                row = copy.deepcopy(self.messages.get(mid))
            if mid == self.sent_mid and not self.release_get.is_set():
                self.record('receipt_get_blocked', mid)
                self.blocked_get.set()
                return 503, {'code':500, 'msg':'synthetic exact receipt temporarily unavailable'}, False
            data = {'items':[row] if row else []}
            self.record('receipt_get_valid', mid)
        else:
            raise ValueError('native endpoint outside finite fixture')
        return 200, {'code':0,'data':data}, False

    def _install_handler(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self): self.handle_wire('GET')
            def do_POST(self): self.handle_wire('POST')
            def handle_wire(self, method):
                try:
                    self.connection.settimeout(5)
                    assert self.headers.get('Host') == '127.0.0.1:'+str(outer.case.relay.server.server_port)
                    if self.headers.get('Upgrade', '').lower() == 'websocket':
                        # Native Relay feed remains a real pending transport.
                        self.answer(426, {'code':426}, close=True)
                        return
                    length = self.headers.get('Content-Length', '0')
                    assert length.isdigit() and 0 <= int(length) <= 65536
                    body = self.rfile.read(int(length))
                    if self.path == '/query':
                        assert method == 'POST' and body
                        answer = outer._query(self.headers, body)
                        self.answer(200, answer)
                    else:
                        status, answer, drop = outer._native(method, self.path, self.headers, body)
                        if drop:
                            self.close_connection = True
                            try: self.connection.shutdown(socket.SHUT_RDWR)
                            except OSError: pass
                            self.connection.close()  # Physical row retained; no response bytes.
                        else:
                            self.answer(status, answer)
                except Exception:
                    with outer.lock: outer.failures.append('finite native/signed fixture request rejected')
                    try: self.answer(400, {'code':400,'msg':'fixture request rejected'}, close=True)
                    except OSError: pass
            def answer(self, status, payload, close=True):
                body = encoded(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                if close: self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                if close: self.close_connection = True
        assert self.case.relay.thread is None
        self.case.relay.server.RequestHandlerClass = Handler
