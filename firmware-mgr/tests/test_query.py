import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/query.py'


def load():
    spec = importlib.util.spec_from_file_location('firmware_query', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuthQueryTests(unittest.TestCase):
    def setUp(self):
        self.client = load()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def success(self, data=None):
        return {'result': 0, 'msg': 'success', 'data': data or {}}

    def test_existing_token_is_reused_without_password_login(self):
        os.environ['FIRMWARE_MGR_TOKEN'] = 'fixture-token'
        with patch.object(self.client, 'post', return_value=self.success()) as post:
            self.client.query('get_models', {}, 'prod')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args, ('prod', 'get_models', {}, 'fixture-token'))

    def test_credentials_login_and_token_are_used_in_same_process(self):
        os.environ.update(FIRMWARE_MGR_USERNAME='fixture-user', FIRMWARE_MGR_PASSWORD='fixture-password')
        replies = [self.success({'token': 'fresh-token'}), self.success(), self.success(['1.2'])]
        with patch.object(self.client, 'post', side_effect=replies) as post:
            result = self.client.query('get_versions', {}, 'prod')
        self.assertEqual(post.call_args_list[0].args, ('prod', 'login', {'userName': 'fixture-user', 'password': 'fixture-password'}, None))
        self.assertEqual(post.call_args_list[2].args[-1], 'fresh-token')
        self.assertEqual(result['data'], ['1.2'])
        self.assertNotIn('FIRMWARE_MGR_TOKEN', os.environ)

    def test_expired_token_logs_in_once(self):
        os.environ.update(FIRMWARE_MGR_TOKEN='old', FIRMWARE_MGR_USERNAME='user', FIRMWARE_MGR_PASSWORD='password')
        with patch.object(self.client, 'post', side_effect=[{'result': 10001}, self.success({'token': 'new'}), self.success()]) as post:
            self.client.query('get_models', {}, 'prod')
        self.assertEqual([c.args[1] for c in post.call_args_list], ['get_models', 'login', 'get_models'])

    def test_test_environment_never_falls_back_to_production_credentials(self):
        os.environ.update(FIRMWARE_MGR_USERNAME='prod', FIRMWARE_MGR_PASSWORD='private')
        with patch.object(self.client, 'post') as post, self.assertRaisesRegex(self.client.QueryError, 'FIRMWARE_MGR_TEST_USERNAME'):
            self.client.query('get_models', {}, 'test')
        post.assert_not_called()

    def test_test_environment_uses_test_account(self):
        os.environ.update(FIRMWARE_MGR_TEST_USERNAME='test-user', FIRMWARE_MGR_TEST_PASSWORD='test-password')
        with patch.object(self.client, 'post', side_effect=[self.success({'token': 'test-token'}), self.success()]) as post:
            self.client.query('get_models', {}, 'test')
        self.assertEqual(post.call_args_list[0].args[:3], ('test', 'login', {'userName': 'test-user', 'password': 'test-password'}))

    def test_login_failure_does_not_retry_or_echo_response(self):
        os.environ.update(FIRMWARE_MGR_USERNAME='user', FIRMWARE_MGR_PASSWORD='private')
        with patch.object(self.client, 'post', return_value={'result': -1, 'msg': 'private'}) as post:
            with self.assertRaises(self.client.QueryError) as error:
                self.client.query('get_models', {}, 'prod')
        self.assertEqual(post.call_count, 1)
        self.assertNotIn('private', str(error.exception))

    def test_endpoint_permission_denial_does_not_relogin(self):
        os.environ.update(FIRMWARE_MGR_TOKEN='valid', FIRMWARE_MGR_USERNAME='user', FIRMWARE_MGR_PASSWORD='private')
        with patch.object(self.client, 'post', side_effect=[self.success(), {'result': 10001}]) as post:
            with self.assertRaisesRegex(self.client.QueryError, 'permission'):
                self.client.query('get_publish_logs', {}, 'prod')
        self.assertEqual(post.call_count, 2)

    def test_writes_and_external_urls_are_rejected_before_login(self):
        for endpoint in ['publish_firmwares', 'set_ota_whitelist', 'https://example.com', '../login', 'get_user_info']:
            with self.subTest(endpoint=endpoint), patch.object(self.client, 'post') as post:
                with self.assertRaises(self.client.QueryError):
                    self.client.query(endpoint, {}, 'prod')
                post.assert_not_called()

    def test_invalid_login_token_stops_before_business_query(self):
        os.environ.update(FIRMWARE_MGR_USERNAME='user', FIRMWARE_MGR_PASSWORD='private')
        for token in ['', None, 'injected\nheader']:
            with self.subTest(token=token), patch.object(self.client, 'post', return_value=self.success({'token': token})) as post:
                with self.assertRaises(self.client.QueryError):
                    self.client.query('get_versions', {}, 'prod')
                self.assertEqual(post.call_count, 1)

    def test_curl_receives_secrets_only_over_stdin_not_argv_or_child_env(self):
        os.environ['FIRMWARE_MGR_PASSWORD'] = 'private'
        response = subprocess.CompletedProcess([], 0, json.dumps(self.success()) + '\n200', '')
        with patch.object(self.client.subprocess, 'run', return_value=response) as run:
            self.client.post('prod', 'login', {'userName': 'user', 'password': 'private'}, 'private-token')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['curl', '-q', '--config', '-'])
        self.assertIn('private', kwargs['input'])
        self.assertNotIn('FIRMWARE_MGR_PASSWORD', kwargs['env'])
        self.assertIn('https://firmware-mgr.addx.live/api/login', kwargs['input'])
        self.assertNotIn('location', kwargs['input'])
        self.assertLessEqual(kwargs['timeout'], 40)

    def test_slow_read_has_a_bounded_120_second_budget(self):
        response = subprocess.CompletedProcess([], 0, json.dumps(self.success()) + '\n200', '')
        with patch.object(self.client.subprocess, 'run', return_value=response) as run:
            self.client.post('prod', 'get_firmwares', {}, 'fixture-token')
        self.assertIn('max-time = 120', run.call_args.kwargs['input'])
        self.assertEqual(run.call_args.kwargs['timeout'], 125)

    def test_transport_and_non_json_failures_are_sanitized(self):
        outcomes = [subprocess.CompletedProcess([], 7, '', 'private'), subprocess.CompletedProcess([], 0, 'private\n502', '')]
        for outcome in outcomes:
            with patch.object(self.client.subprocess, 'run', return_value=outcome):
                with self.assertRaises(self.client.QueryError) as error:
                    self.client.post('prod', 'get_models', {}, 'private')
                self.assertNotIn('private', str(error.exception))


if __name__ == '__main__':
    unittest.main()
