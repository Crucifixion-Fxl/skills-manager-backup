"""Actual protected credentials and pooled HTTP; only HTTP transport is fake."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'scripts'
sys.path[:0] = [str(ROOT), str(ROOT / 'hostd')]
from http_pool import HttpPool, PoolError
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now


class Response:
    will_close = False
    def __init__(self, status, value, headers=None):
        self.status, self.headers = status, headers or {}
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()
    def getheader(self, name, default=None): return self.headers.get(name, default)
    def read1(self, count):
        chunk, self.body = self.body[:count], self.body[count:]
        return chunk


class Connection:
    def __init__(self, server): self.server, self.closed, self.next_response = server, False, None
    def request(self, method, path, body=None, headers=None):
        call = {'method': method, 'path': path, 'body': body, 'headers': dict(headers or {})}
        with self.server.lock:
            self.server.calls.append(call)
            self.token_request = path.endswith('/tenant_access_token/internal')
            if self.token_request:
                self.server.mints += 1
                app = json.loads(body)['app_id']
                self.app = app
                self.next_response = self.server.token_response or Response(200, {'code': 0, 'tenant_access_token': 'offline-' + app, 'expire': 100})
            else:
                self.next_response = self.server.responses.pop(0) if self.server.responses else Response(200, {'code': 0, 'data': {'message_id': 'om_fake'}})
    def getresponse(self):
        if self.token_request:
            self.server.mint_entered.set()
            if self.server.mint_gate is not None and self.app == self.server.mint_gate_app: self.server.mint_gate.wait(2)
        elif self.server.api_gate is not None:
            self.server.api_entered.set()
            self.server.api_gate.wait(2)
        if isinstance(self.next_response, Exception): raise self.next_response
        return self.next_response
    def close(self): self.closed = True


class Server:
    def __init__(self):
        self.lock, self.calls, self.connections = threading.Lock(), [], []
        self.mints, self.responses, self.token_response = 0, [], None
        self.mint_entered, self.api_entered = threading.Event(), threading.Event()
        self.mint_gate = self.api_gate = None
        self.mint_gate_app = 'cli_one'
    def connect(self, host, *, timeout, context):
        if host != 'open.feishu.cn' or timeout <= 0 or context.verify_mode == 0:
            raise AssertionError('wrong HTTPS transport boundary')
        connection = Connection(self)
        with self.lock: self.connections.append(connection)
        return connection


class PureBoundary(unittest.TestCase):
    def test_import_and_invalid_origin_never_read_credentials_or_open_transport(self):
        server = Server()
        pool = HttpPool(connection_factory=server.connect)
        for path in ('https://evil.test/open-apis/im/v1/messages', '//evil.test/a', '/open-apis/im/../auth/a',
                     '/open-apis/im/%2e%2e/auth/a', '/open-apis/im/%0d%0aHeader', '/open-apis/authen/v1/access_token'):
            with self.assertRaises(PoolError) as caught:
                pool.request('cli_fake', '/does/not/exist', '/does/not/exist', 'GET', path)
            self.assertTrue(caught.exception.definite)
            self.assertFalse(caught.exception.dispatched)
            self.assertIn('怎么解决', str(caught.exception))
            self.assertIn('复制给 AI', str(caught.exception))
            self.assertNotIn('evil', str(caught.exception))
        self.assertEqual(server.calls, [])

    def test_relative_dirs_bad_method_and_oversized_payload_are_rejected_before_transport(self):
        server = Server()
        pool = HttpPool(connection_factory=server.connect, max_request_bytes=64)
        for args in [('cli_fake', 'relative', '/tmp/data', 'GET', '/open-apis/im/v1/messages'),
                     ('cli_fake', '/tmp/cfg', '/tmp/data', 'CONNECT', '/open-apis/im/v1/messages')]:
            with self.assertRaises(PoolError): pool.request(*args)
        with self.assertRaises(PoolError):
            pool.request('cli_fake', '/tmp/cfg', '/tmp/data', 'POST', '/open-apis/im/v1/messages', data={'body': 'x' * 128})
        self.assertEqual(server.calls, [])

    def test_missing_or_unsafe_profile_is_fixed_error_without_transport(self):
        server = Server()
        pool = HttpPool(connection_factory=server.connect)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_text(json.dumps({'apps': [{'appId': 'cli_other'}]}))
            (root / 'config.json').chmod(0o600)
            with self.assertRaises(PoolError) as caught:
                pool.request('cli_fake', root, root, 'GET', '/open-apis/im/v1/messages')
            self.assertTrue(caught.exception.definite)
            self.assertNotIn('cli_other', repr(caught.exception))
            (root / 'config.json').chmod(0o644)
            with self.assertRaises(PoolError): pool.request('cli_other', root, root, 'GET', '/open-apis/im/v1/messages')
        self.assertEqual(server.calls, [])


@unittest.skipUnless(AESGCM, 'protected app secret requires cryptography; dependency venv exercises these cases')
class ProtectedPool(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock, self.server = Clock(), Server()
        self.pool = HttpPool(connection_factory=self.server.connect, clock=self.clock)
        self.addCleanup(self.pool.close)
        self.config, self.data = self.profile('cli_one')

    def profile(self, app):
        config, data = self.root / (app + '-config'), self.root / (app + '-data')
        config.mkdir(mode=0o700); data.mkdir(mode=0o700)
        (config / 'config.json').write_text(json.dumps({'apps': [{'appId': app, 'name': app}]}))
        (config / 'config.json').chmod(0o600)
        store = data / 'lark-cli'; store.mkdir(mode=0o700)
        key, nonce = bytes(range(32)), bytes(range(12))
        for name, content in [('master.key', key), ('appsecret_' + app + '.enc', nonce + AESGCM(key).encrypt(nonce, b'offline-secret', None))]:
            (store / name).write_bytes(content); (store / name).chmod(0o600)
        return config, data

    def request(self, **kwargs):
        return self.pool.request('cli_one', self.config, self.data, 'POST', '/open-apis/im/v1/messages', **kwargs)

    def launch(self, callback):
        values = []
        def run():
            try: values.append(callback())
            except BaseException as error: values.append(error)
        thread = threading.Thread(target=run); thread.start()
        self.addCleanup(lambda: thread.join(2))
        return thread, values

    def wait_stat(self, key, value):
        end = time.monotonic() + 2
        while self.pool.stats('cli_one').get(key) != value and time.monotonic() < end: time.sleep(.001)
        self.assertEqual(self.pool.stats('cli_one').get(key), value)

    def test_real_internal_tat_schema_cache_and_normalized_bot_result_reuse_connection(self):
        expected = {'ok': True, 'identity': 'bot', 'data': {'message_id': 'om_fake'}}
        self.assertEqual(self.request(params={'receive_id_type': 'chat_id'}, data={'receive_id': 'oc_fake'}), expected)
        self.assertEqual(self.request(), expected)
        self.assertEqual(self.server.mints, 1)
        self.assertEqual(len(self.server.connections), 1)
        token, api = self.server.calls[:2]
        self.assertEqual(token['method'], 'POST')
        self.assertEqual(token['path'], '/open-apis/auth/v3/tenant_access_token/internal')
        self.assertEqual(json.loads(token['body']), {'app_id': 'cli_one', 'app_secret': 'offline-secret'})
        self.assertNotIn('Authorization', token['headers'])
        self.assertEqual(api['headers']['Authorization'], 'Bearer offline-cli_one')
        self.assertEqual(api['path'], '/open-apis/im/v1/messages?receive_id_type=chat_id')

    def test_expiry_refreshes_before_server_expiry_without_cross_app_token_or_connection_reuse(self):
        self.request(); self.clock.now = 89; self.request()
        self.assertEqual(self.server.mints, 1)
        self.clock.now = 90; self.request()
        self.assertEqual(self.server.mints, 2)
        config, data = self.profile('cli_two')
        self.pool.request('cli_two', config, data, 'GET', '/open-apis/im/v1/messages')
        self.assertEqual(self.server.mints, 3)
        self.assertEqual(len(self.server.connections), 2)
        self.assertEqual(self.server.calls[-1]['headers']['Authorization'], 'Bearer offline-cli_two')

    def test_singleflight_refresh_success_for_competing_threads(self):
        self.server.mint_gate = threading.Event()
        self.addCleanup(self.server.mint_gate.set)
        workers = [self.launch(self.request) for _ in range(4)]
        self.assertTrue(self.server.mint_entered.wait(1))
        self.wait_stat('refresh_waiters', 3)
        self.assertEqual(self.server.mints, 1)
        self.server.mint_gate.set()
        for thread, values in workers:
            thread.join(1); self.assertEqual(values[0]['identity'], 'bot')
        self.assertEqual(self.server.mints, 1)

    def test_singleflight_failed_refresh_is_shared_without_raw_error_or_api_dispatch(self):
        self.server.token_response = Response(200, {'code': 10014, 'msg': 'private token/body'})
        self.server.mint_gate = threading.Event(); self.addCleanup(self.server.mint_gate.set)
        workers = [self.launch(self.request) for _ in range(4)]
        self.assertTrue(self.server.mint_entered.wait(1)); self.wait_stat('refresh_waiters', 3)
        self.server.mint_gate.set()
        for thread, values in workers:
            thread.join(1)
            self.assertIsInstance(values[0], PoolError)
            self.assertTrue(values[0].definite)
            self.assertFalse(values[0].dispatched)
            self.assertEqual(values[0].code, 10014)
            self.assertEqual(values[0].kind, 'configuration')
            self.assertNotIn('private', repr(values[0]))
        self.assertEqual(self.server.mints, 1)
        self.assertEqual(len(self.server.calls), 1)

    def test_profile_mismatch_duplicate_and_changed_directory_refuse_cached_token(self):
        self.request()
        config, data = self.profile('cli_alternate')
        (config / 'config.json').write_text(json.dumps({'apps': [{'appId': 'cli_one'}]}))
        with self.assertRaises(PoolError): self.pool.request('cli_one', config, data, 'GET', '/open-apis/im/v1/messages')
        (self.config / 'config.json').write_text(json.dumps({'apps': [{'appId': 'cli_one'}, {'appId': 'cli_one'}]}))
        with self.assertRaises(PoolError): self.request()
        self.assertEqual(len(self.server.calls), 2)

    def test_redirect_and_timeout_are_unknown_for_business_write_and_never_replayed(self):
        self.server.responses = [Response(302, {}, {'Location': 'https://evil.test/?token=private'}), TimeoutError('private response')]
        for expected in (302, -1):
            with self.assertRaises(PoolError) as caught: self.request()
            self.assertFalse(caught.exception.definite)
            self.assertEqual(caught.exception.http_status, expected)
            self.assertNotIn('private', str(caught.exception))
        self.assertEqual(len([c for c in self.server.calls if not c['path'].endswith('/internal')]), 2)
        self.assertTrue(self.server.connections[0].closed)

    def test_response_limit_and_malformed_success_hold_unknown_result_without_raw_body(self):
        self.server.responses = [Response(200, b'x' * (self.pool.max_response_bytes + 1)), Response(200, b'private not JSON')]
        for _ in range(2):
            with self.assertRaises(PoolError) as caught: self.request()
            self.assertFalse(caught.exception.definite)
            self.assertNotIn('private', repr(caught.exception))

    def test_unusable_success_schema_keeps_actual_status_and_code_but_unknown_outcome(self):
        self.server.responses = [Response(201, {'code': 0, 'data': []})]
        with self.assertRaises(PoolError) as caught: self.request()
        self.assertEqual(caught.exception.http_status, 201)
        self.assertEqual(caught.exception.code, 0)
        self.assertFalse(caught.exception.definite)

    def test_permission_rate_limit_and_auth_refusal_are_definite_numeric_memory_fields(self):
        self.server.responses = [Response(403, {'code': 99991672, 'msg': 'private'}), Response(429, {'code': 99991400, 'msg': 'private'}, {'Retry-After': '3'}), Response(401, {'code': 99991663, 'msg': 'private'})]
        for status, kind in ((403, 'permission'), (429, 'rate_limited'), (401, 'authentication')):
            with self.assertRaises(PoolError) as caught: self.request()
            self.assertTrue(caught.exception.definite)
            self.assertEqual(caught.exception.kind, kind)
            self.assertEqual(caught.exception.http_status, status)
            self.assertNotIn(str(caught.exception.code), repr(caught.exception))
            if status == 429: self.assertEqual(caught.exception.retry_after, 3)
        self.assertEqual(self.server.mints, 1)
        self.request()
        self.assertEqual(self.server.mints, 2)

    def test_invalid_token_response_cannot_enter_cache(self):
        for invalid in ({'code': 0, 'tenant_access_token': 'bad\r\nHeader', 'expire': 100},
                        {'code': 0, 'tenant_access_token': 'offline', 'expire': 0},
                        {'code': 0, 'tenant_access_token': 'offline', 'expire': True}):
            self.server.token_response = Response(200, invalid)
            with self.assertRaises(PoolError) as caught: self.request()
            self.assertTrue(caught.exception.definite)
            self.assertFalse(caught.exception.dispatched)
        self.assertEqual(self.server.mints, 3)
        self.assertEqual(len(self.server.calls), 3)

    def test_close_rejects_new_requests_and_closes_idle_connections(self):
        self.request(); self.pool.close()
        self.assertTrue(all(c.closed for c in self.server.connections))
        with self.assertRaises(PoolError) as caught: self.request()
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(len(self.server.calls), 2)

    def test_slow_refresh_for_one_app_does_not_block_another_app(self):
        self.server.mint_gate = threading.Event(); self.addCleanup(self.server.mint_gate.set)
        first, values = self.launch(self.request)
        self.assertTrue(self.server.mint_entered.wait(1))
        config, data = self.profile('cli_two')
        other = self.pool.request('cli_two', config, data, 'GET', '/open-apis/im/v1/messages')
        self.assertEqual(other['identity'], 'bot')
        self.assertTrue(first.is_alive())
        self.server.mint_gate.set(); first.join(1)
        self.assertEqual(values[0]['identity'], 'bot')

    def test_close_releases_refresh_waiters_without_waiting_for_mint_transport(self):
        self.server.mint_gate = threading.Event(); self.addCleanup(self.server.mint_gate.set)
        leader, _ = self.launch(self.request)
        self.assertTrue(self.server.mint_entered.wait(1))
        waiter, values = self.launch(self.request)
        self.wait_stat('refresh_waiters', 1)
        self.pool.close(); waiter.join(.2)
        self.assertFalse(waiter.is_alive())
        self.assertIsInstance(values[0], PoolError)
        self.assertFalse(values[0].dispatched)
        self.assertTrue(leader.is_alive())
        self.server.mint_gate.set(); leader.join(1)

    def test_connection_quota_wait_timeout_does_not_dispatch_business_write(self):
        self.pool.close()
        self.pool = HttpPool(connection_factory=self.server.connect, max_connections=1, timeout=.05)
        self.addCleanup(self.pool.close)
        self.request()
        self.server.api_gate = threading.Event(); self.addCleanup(self.server.api_gate.set)
        self.server.api_entered.clear()
        first, first_values = self.launch(self.request)
        self.assertTrue(self.server.api_entered.wait(1))
        with self.assertRaises(PoolError) as caught: self.request()
        self.assertTrue(caught.exception.definite)
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(len(self.server.connections), 1)
        self.assertEqual(len([c for c in self.server.calls if not c['path'].endswith('/internal')]), 2)
        self.server.api_gate.set(); first.join(1)
        self.assertIsInstance(first_values[0], PoolError)
        self.assertFalse(first_values[0].definite)

    def test_protected_secret_symlink_is_refused_without_any_http_request(self):
        original = self.data / 'lark-cli' / 'master.key'
        safe_copy = self.root / 'key-copy'
        original.rename(safe_copy); original.symlink_to(safe_copy)
        with self.assertRaises(PoolError) as caught: self.request()
        self.assertTrue(caught.exception.definite)
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(self.server.calls, [])


if __name__ == '__main__': unittest.main()
