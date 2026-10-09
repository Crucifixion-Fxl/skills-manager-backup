"""HTTP observations are bounded, secret-free and never authorize a replay."""
import http.client
import json
import ssl
import unittest
from unittest import mock

import test_hostd_http_pool as base
import test_hostd_relay_malformed as relay_base
import http_pool as pool_module
from hostd.http_pool import PoolError as CanonicalPoolError


class HttpDiagnostics(unittest.TestCase):
    def setUp(self):
        self.server = base.Server()
        self.pool = base.HttpPool(connection_factory=self.server.connect)
        self.addCleanup(self.pool.close)
        self.app = pool_module._App('cli_test', None, None)
        self.pool.apps['cli_test'] = self.app

    def exchange(self, phase='api'):
        return self.pool._exchange_transport(self.app, 'POST', '/open-apis/im/v1/messages',
            {'Authorization': 'SECRET token'}, b'SECRET body', self.pool.clock() + 30, phase)

    def failure(self, phase='api'):
        with self.assertRaises(base.PoolError) as caught:
            self.exchange(phase)
        error = caught.exception
        self.assertEqual(error.definite, phase == 'token')
        self.assertEqual(error.dispatched, phase == 'api')
        self.assertLessEqual(len(self.server.calls), 1)  # Unknown POST stays single dispatch.
        self.assertTrue(pool_module.valid_http_diagnostic(error.http_diagnostic))
        self.assertNotIn('SECRET', json.dumps(error.http_diagnostic) + str(error) + repr(error))
        return error

    def test_request_headers_read_decode_shape_are_distinct_and_post_is_not_retried(self):
        cases = [('request', 'reset'), ('headers', 'disconnected'), ('read', 'incomplete_read'),
                 ('decode', 'value'), ('shape', 'value')]
        for stage, label in cases:
            with self.subTest(stage=stage):
                self.server.calls.clear()
                response = base.Response(200, {'code': 0, 'data': {}})
                self.server.responses = [response]
                if stage == 'request':
                    patch = mock.patch.object(base.Connection, 'request', side_effect=ConnectionResetError('SECRET URL'))
                elif stage == 'headers':
                    patch = mock.patch.object(base.Connection, 'getresponse', side_effect=http.client.RemoteDisconnected('SECRET headers'))
                elif stage == 'read':
                    patch = mock.patch.object(response, 'read1', side_effect=http.client.IncompleteRead(b'SECRET partial', 100))
                else:
                    response.body = b'SECRET invalid JSON' if stage == 'decode' else b'{"code":0,"data":"SECRET"}'
                    patch = mock.patch.object(self.pool, 'max_response_bytes', 1024)
                with patch:
                    error = self.failure()
                self.assertEqual((error.http_diagnostic['stage'], error.http_diagnostic['exception']), (stage, label))
                self.assertEqual(self.pool.stats('cli_test')['connections'], 0)

    def test_acquire_failure_has_no_dispatch_and_safe_fixed_exception_class(self):
        SecretError = type('SECRET_CLASS', (Exception,), {})
        for cause, label in [(ssl.SSLError('SECRET certificate'), 'tls'),
                             (TimeoutError('SECRET host'), 'timeout'),
                             (OSError('SECRET path'), 'os'), (SecretError('SECRET body'), 'other')]:
            with self.subTest(label=label), mock.patch.object(self.pool, 'factory', side_effect=cause):
                with self.assertRaises(base.PoolError) as caught:
                    self.exchange()
                error = caught.exception
                self.assertTrue(error.definite)
                self.assertFalse(error.dispatched)
                self.assertEqual(error.http_diagnostic['stage'], 'acquire')
                self.assertEqual(error.http_diagnostic['exception'], label)
                self.assertNotIn('SECRET', json.dumps(error.http_diagnostic))
                self.assertEqual(self.server.calls, [])
                self.assertEqual(self.pool.stats('cli_test')['connections'], 0)

    def test_acquire_deadline_remains_definite(self):
        with self.assertRaises(base.PoolError) as caught:
            self.pool._acquire(self.app, self.pool.clock() - 1, 'api')
        error = caught.exception
        self.assertTrue(error.definite)
        self.assertFalse(error.dispatched)
        self.assertEqual(error.http_diagnostic['exception'], 'timeout')

    def test_token_and_api_failures_keep_outcome_and_bounded_numeric_evidence(self):
        for phase in ('api', 'token'):
            self.server.calls.clear()
            self.server.responses = [TimeoutError('SECRET socket')]
            error = self.failure(phase)
            self.assertEqual(error.http_diagnostic['phase'], phase)
        self.server.calls.clear()
        self.server.responses = [base.Response(403, {'code': 230027, 'msg': 'SECRET server text'})]
        with self.assertRaises(base.PoolError) as caught:
            self.exchange()
        error = caught.exception
        self.assertTrue(error.definite)
        self.assertTrue(error.dispatched)
        self.assertEqual((error.http_diagnostic['http_status'], error.http_diagnostic['api_code']), (403, 230027))
        self.assertEqual(error.http_diagnostic['exception'], 'none')
        self.assertEqual(len(self.server.calls), 1)

    def test_numeric_fields_never_export_unbounded_or_peer_typed_values(self):
        for status, code in [('SECRET status', 0), (True, 0), (200, 2**100), (200, True)]:
            self.server.calls.clear()
            self.server.responses = [base.Response(status, {'code': code})]
            error = self.failure()
            self.assertIn(error.http_diagnostic['http_status'], (-1, 200))
            self.assertEqual(error.http_diagnostic['api_code'], -1)

    def test_elapsed_is_bounded_and_diagnostic_failure_preserves_error(self):
        self.server.responses = [TimeoutError('SECRET')]
        with mock.patch.object(pool_module, '_diagnostic_clock', return_value=10), \
             mock.patch.object(pool_module.time, 'monotonic', return_value=12.5):
            error = self.failure()
        self.assertEqual(error.http_diagnostic['elapsed_ms'], 2500)
        for now, expected in ((9, 0), (100000, pool_module.MAX_DIAGNOSTIC_MS)):
            self.server.calls.clear()
            self.server.responses = [TimeoutError('SECRET')]
            with mock.patch.object(pool_module, '_diagnostic_clock', return_value=10), \
                 mock.patch.object(pool_module.time, 'monotonic', return_value=now):
                self.assertEqual(self.failure().http_diagnostic['elapsed_ms'], expected)
        self.server.calls.clear()
        self.server.responses = [TimeoutError('SECRET')]
        with mock.patch.object(pool_module, 'valid_http_diagnostic', side_effect=OSError('SECRET writer')):
            with self.assertRaises(base.PoolError) as caught:
                self.exchange()
        self.assertFalse(caught.exception.definite)
        self.assertTrue(caught.exception.dispatched)
        self.assertFalse(hasattr(caught.exception, 'http_diagnostic'))
        self.assertEqual(len(self.server.calls), 1)


class DiagnosticSchema(unittest.TestCase):
    def metadata(self):
        return dict(stage='read', exception='timeout', phase='api', http_status=200, api_code=-1, elapsed_ms=90000)

    def test_exact_pool_classes_only_and_existing_errors_keep_original_schema(self):
        relay = relay_base.relay
        for kind in (base.PoolError, CanonicalPoolError):
            error = kind()
            error.http_diagnostic = self.metadata()
            doc = relay.failure_diagnostic('phase_feishu', error)
            self.assertTrue(relay.valid_diagnostic(doc))
            self.assertEqual(doc['http'], self.metadata())
            error.http_diagnostic['phase'] = 'SECRET'
            self.assertEqual(doc['http']['phase'], 'api')
        for error in (RuntimeError('SECRET'), type('SECRET_CLASS', (base.PoolError,), {})()):
            error.http_diagnostic = self.metadata()
            self.assertNotIn('http', relay.failure_diagnostic('phase_feishu', error))

    def test_invalid_metadata_is_omitted_and_status_schema_rejects_it(self):
        relay = relay_base.relay
        bad = [('stage', 'SECRET'), ('exception', 'SECRET_CLASS'), ('phase', 'profile'),
               ('http_status', True), ('http_status', 600), ('api_code', 2**31),
               ('api_code', -2), ('elapsed_ms', -1), ('elapsed_ms', 86400001),
               ('elapsed_ms', 1.5), ('elapsed_ms', True), ('url', 'SECRET'), ('phase', [])]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                metadata = {**self.metadata(), key: value}
                error = base.PoolError()
                error.http_diagnostic = metadata
                self.assertNotIn('http', relay.failure_diagnostic('phase_buzz', error))
                doc = dict(stage='phase_buzz', error_type='CliError', location=[], http=metadata)
                self.assertFalse(relay.valid_diagnostic(doc))
                with self.assertRaises(ValueError):
                    relay.copy_diagnostic(doc)
        self.assertFalse(pool_module.valid_http_diagnostic(None))
        self.assertFalse(pool_module.valid_http_diagnostic({}))

    def test_copy_is_detached_and_preserves_both_schema_versions(self):
        relay = relay_base.relay
        doc = dict(stage='phase_buzz', error_type='CliError', location=[
            dict(file='http_pool.py', function='_exchange_transport', line=1)], http=self.metadata())
        copy = relay.copy_diagnostic(doc)
        doc['http']['phase'] = 'SECRET'
        doc['location'][0]['file'] = 'SECRET'
        self.assertEqual(copy['http']['phase'], 'api')
        self.assertEqual(copy['location'][0]['file'], 'http_pool.py')
        old = dict(stage='replay', error_type='OtherError', location=[])
        self.assertEqual(relay.copy_diagnostic(old), old)


if __name__ == '__main__':
    unittest.main()
