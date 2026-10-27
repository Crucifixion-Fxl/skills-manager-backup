#!/usr/bin/env python3
"""Read firmware metadata using an injected token or native account credentials."""
import argparse
import json
import os
import subprocess
import sys

HOSTS = {'prod': 'https://firmware-mgr.addx.live', 'test': 'https://firmware-mgr-test.addx.live'}
READ_ENDPOINTS = (
    'get_models', 'get_versions', 'get_firmwares', 'get_publishs',
    'get_publish_logs', 'get_ota_whitelist', 'get_ota_blacklist',
    'get_ota_debug_whitelist', 'get_ota_device_whitelist',
)


class QueryError(Exception):
    """Safe diagnostic that contains no credential or raw server response."""


def valid_token(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096 and not any(c in value for c in '\r\n\0')


def post(environment, endpoint, payload, token=None):
    if environment not in HOSTS or endpoint not in (*READ_ENDPOINTS, 'login'):
        raise QueryError('Unsupported environment or endpoint')
    if not isinstance(payload, dict) or (token is not None and not valid_token(token)):
        raise QueryError('Invalid payload or token')
    timeout = 120 if endpoint in ('get_firmwares', 'get_publishs') else 30
    # Curl config on stdin keeps both headers and JSON out of process argv.
    config = [
        'url = ' + json.dumps(HOSTS[environment] + '/api/' + endpoint),
        'request = "POST"', 'header = "Content-Type: application/json"',
        'data = ' + json.dumps(json.dumps(payload)),
        'silent', 'show-error', 'connect-timeout = 10', f'max-time = {timeout}',
        'proto = "=https"', 'write-out = "\\n%{http_code}"',
    ]
    if token is not None:
        config.append('header = ' + json.dumps('token: ' + token))
    child_env = {k: v for k, v in os.environ.items() if not k.startswith('FIRMWARE_MGR_')}
    try:
        result = subprocess.run(['curl', '-q', '--config', '-'], input='\n'.join(config),
                                text=True, capture_output=True, timeout=timeout + 5, env=child_env)
    except (OSError, subprocess.TimeoutExpired):
        raise QueryError('Firmware API transport failed; check network and curl') from None
    if result.returncode:
        raise QueryError('Firmware API transport failed; check network and TLS')
    try:
        body, status = result.stdout.rsplit('\n', 1)
        data = json.loads(body)
        code = int(status)
        if not isinstance(data, dict) or type(data.get('result')) is not int:
            raise ValueError()
        # The native API may return its auth envelope with a non-2xx status.
        if not 200 <= code < 300 and data['result'] != 10001:
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise QueryError('Firmware API returned an unexpected response; verify the deployment') from None


def query(endpoint, payload, environment='prod'):
    if environment not in HOSTS or endpoint not in READ_ENDPOINTS or not isinstance(payload, dict):
        raise QueryError('Only supported read-only endpoints and JSON objects are allowed')
    prefix = 'FIRMWARE_MGR_' if environment == 'prod' else 'FIRMWARE_MGR_TEST_'
    token = os.environ.get(prefix + 'TOKEN')
    probe = None
    if token:
        if not valid_token(token):
            raise QueryError('Invalid token in environment')
        probe = post(environment, 'get_models', {}, token)
        if probe['result'] not in (0, 10001):
            raise QueryError('Token probe failed; verify deployment before retrying')
    if not token or probe['result'] == 10001:
        username, password = os.environ.get(prefix + 'USERNAME'), os.environ.get(prefix + 'PASSWORD')
        if not username or not password:
            raise QueryError('Configure ' + prefix + 'USERNAME and ' + prefix + 'PASSWORD, or a valid ' + prefix + 'TOKEN')
        login = post(environment, 'login', {'userName': username, 'password': password}, None)
        data = login.get('data')
        token = data.get('token') if isinstance(data, dict) else None
        if login.get('result') != 0 or not valid_token(token):
            raise QueryError('Native login failed; check account, password and account permissions; no automatic retry')
        probe = post(environment, 'get_models', {}, token)
        if probe['result'] != 0:
            raise QueryError('Fresh token probe failed; check account permission and deployment')
    # A later denial may be permission-related or a token invalidated after the probe.
    result = probe if endpoint == 'get_models' and not payload else post(environment, endpoint, payload, token)
    if result['result'] == 10001:
        raise QueryError('Target permission denied or token expired after probe; no automatic relogin')
    if result['result'] != 0:
        raise QueryError('Read query failed; check request parameters and deployment')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('endpoint', choices=READ_ENDPOINTS)
    parser.add_argument('--environment', choices=HOSTS, default='prod')
    parser.add_argument('--data', default='{}', help='JSON object, or - to read JSON from stdin')
    args = parser.parse_args()
    try:
        payload = json.loads(sys.stdin.read() if args.data == '-' else args.data)
        print(json.dumps(query(args.endpoint, payload, args.environment), ensure_ascii=False))
    except (QueryError, ValueError) as error:
        print(str(error) if isinstance(error, QueryError) else 'Invalid JSON payload', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
