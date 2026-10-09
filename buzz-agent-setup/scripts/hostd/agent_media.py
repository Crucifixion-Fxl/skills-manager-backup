"""Own-key bounded binary media reads, separate from signed query budgets.

Production uses a fresh, non-redirecting HTTPS child. The constructor's explicit
cooperative HTTP seam is offline only and does not claim a hard signal deadline.
This module proves media integrity, never source authorization or upload access.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import signal
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from recovery_origin import canonical_origin
from hostd import signed_reads as shared
from hostd.async_io import thread_call
from hostd.safety import read_owned

MAX_INPUT = 65536
MAX_BODY = 10_000_000
MAX_BASE64 = 4 * ((MAX_BODY + 2) // 3)
MAX_OUTPUT = MAX_BASE64 + 4096
MEDIA_SECONDS = 10
HEX = re.compile('[0-9a-f]{64}')
MIME_SUFFIX = {'image/png': '.png', 'image/jpeg': '.jpg'}
NOTICE = ('图片暂未完整核验，保留待处理。怎么解决：检查自己的受保护身份、空条件 OA、'
          '明确 Relay、私有 CA、完整图片和读取时限。'
          '\n复制给 AI：帮我核查 hostd own-agent 图片读取；不要输出密钥、认证、图片或响应正文。')


class AgentMediaFailure(ValueError):
    status = 'pending'

    def __init__(self):
        super().__init__(NOTICE)


def _need(value):
    if not value:
        raise AgentMediaFailure() from None


def _json(raw):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            _need(name not in result)
            result[name] = value
        return result

    def constant(_):
        raise AgentMediaFailure()

    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


@dataclass(frozen=True)
class AgentMediaRequest:
    origin: str
    key: str = field(repr=False)
    pin: str
    now: int
    agent: str
    owner: str
    auth_tag: tuple = field(repr=False)
    url: str = field(repr=False)
    sha256: str
    mime: str
    size: int

    @classmethod
    def from_data(cls, value):
        try:
            _need(type(value) is dict and set(value) == {'version', 'operation', 'origin', 'key',
                'pin', 'now', 'agent', 'owner', 'auth_tag', 'url', 'sha256', 'mime', 'size'})
            _need(len(json.dumps(value, separators=(',', ':')).encode()) <= MAX_INPUT)
            _need(type(value['version']) is int and value['version'] == 1
                  and value['operation'] == 'media_get')
            origin = canonical_origin(value['origin'])
            _need(origin.startswith('https://') and value['origin'] == origin)
            key = gs.secret_hex(value['key'], 'own agent')
            _need(all(type(value[name]) is str and HEX.fullmatch(value[name])
                      for name in ('pin', 'agent', 'owner', 'sha256')))
            _need(gs._signer_pubkey(key) == value['agent'])
            auth = value['auth_tag']
            _need(type(auth) is list and len(auth) == 4 and all(type(part) is str for part in auth)
                  and auth[2] == '' and authority.attested_owner({'tags': [auth]}, value['agent']) == value['owner'])
            _need(type(value['now']) is int and 0 <= value['now'] <= 2**63 - 1
                  and type(value['size']) is int and 1 <= value['size'] <= MAX_BODY)
            _need(type(value['mime']) is str and value['mime'] in MIME_SUFFIX)
            expected = origin + '/media/' + value['sha256'] + MIME_SUFFIX[value['mime']]
            _need(type(value['url']) is str and value['url'] == expected)
            return cls(origin, key, value['pin'], value['now'], value['agent'], value['owner'],
                       tuple(auth), expected, value['sha256'], value['mime'], value['size'])
        except Exception:
            raise AgentMediaFailure() from None

    def packet(self):
        return dict(version=1, operation='media_get', origin=self.origin, key=self.key,
                    pin=self.pin, now=self.now, agent=self.agent, owner=self.owner,
                    auth_tag=list(self.auth_tag), url=self.url, sha256=self.sha256,
                    mime=self.mime, size=self.size)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    _need(remaining > 0)
    return min(MEDIA_SECONDS, remaining)


@contextmanager
def _hard_budget():
    """Own a fresh child timer; never enlarge or borrow signed query budgets."""
    _need(threading.current_thread() is threading.main_thread()
          and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
          and signal.SIGALRM not in signal.pthread_sigmask(signal.SIG_BLOCK, set()))
    previous = signal.getsignal(signal.SIGALRM)
    deadline = time.monotonic() + MEDIA_SECONDS

    def expired(_signum, _frame):
        raise AgentMediaFailure()

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, MEDIA_SECONDS)
        yield deadline
        _remaining(deadline)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _body(request, raw):
    _need(type(raw) is bytes and len(raw) == request.size and len(raw) <= MAX_BODY)
    _need(raw.startswith(b'\x89PNG\r\n\x1a\n') if request.mime == 'image/png'
          else raw.startswith(b'\xff\xd8\xff'))
    _need(hashlib.sha256(raw).hexdigest() == request.sha256)
    return raw


def _headers(request):
    return {'Authorization': gs.nip98_header(request.key, 'GET', request.url,
                datetime.fromtimestamp(request.now, timezone.utc)),
            'x-auth-tag': json.dumps(list(request.auth_tag), separators=(',', ':')),
            'Accept': request.mime, 'Accept-Encoding': 'identity', 'Connection': 'close'}


def _native(request, deadline, connection_factory):
    connection = None
    response = None
    try:
        parsed = urlsplit(request.url)
        context = ssl.create_default_context()
        _need(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED)
        connection = connection_factory(parsed.hostname, port=parsed.port,
            timeout=_remaining(deadline), context=context)
        connection.request('GET', parsed.path, body=None, headers=_headers(request))
        _remaining(deadline)
        response = connection.getresponse()
        _need(type(response.status) is int and response.status == 200)
        rows = response.getheaders()
        _need(type(rows) is list and len(rows) <= 256)
        headers, count = {}, 0
        for row in rows:
            _need(isinstance(row, (tuple, list)) and len(row) == 2
                  and all(type(part) is str for part in row))
            name, value = row
            count += len(name) + len(value)
            _need(count <= 65536 and name and not any(ord(c) < 32 or ord(c) == 127 for c in name + value))
            name = name.lower()
            _need(name not in headers)
            headers[name] = value
        _need(headers.get('content-type') == request.mime
              and headers.get('content-length') == str(request.size)
              and headers.get('content-encoding', 'identity') == 'identity'
              and 'transfer-encoding' not in headers and 'location' not in headers)
        # HTTPResponse.read() clamps reads to Content-Length and closes its
        # buffered file at that boundary, hiding any following octets. Read
        # bounded bytes from its underlying buffered socket stream instead.
        # The injectable lower fixture has no fp and retains its own raw read.
        raw_stream = getattr(response, 'fp', None)
        reader = raw_stream.read if raw_stream is not None else response.read
        raw = bytearray()
        while True:
            remaining = _remaining(deadline)
            sock = getattr(connection, 'sock', None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = reader(min(65536, request.size + 1 - len(raw)))
            _need(type(chunk) is bytes)
            _remaining(deadline)
            if not chunk:
                break
            raw.extend(chunk)
            _need(len(raw) <= request.size and len(raw) <= MAX_BODY)
        return _body(request, bytes(raw))
    finally:
        close_error = False
        if response is not None:
            close = getattr(response, 'close', None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    close_error = True
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                close_error = True
        if close_error:
            raise AgentMediaFailure() from None


def _envelope(request, raw):
    return dict(version=1, ok=True, mime=request.mime, size=request.size,
                sha256=request.sha256, data_b64=base64.b64encode(raw).decode('ascii'))


def _decode_output(request, output):
    try:
        _need(len(output) <= MAX_OUTPUT)
        value = _json(output)
        _need(type(value) is dict and set(value) == {'version', 'ok', 'mime', 'size', 'sha256', 'data_b64'}
              and type(value['version']) is int and value['version'] == 1 and value['ok'] is True)
        _need(type(value['size']) is int and value['size'] == request.size
              and value['mime'] == request.mime and value['sha256'] == request.sha256)
        encoded = value['data_b64']
        _need(type(encoded) is str and len(encoded) <= MAX_BASE64)
        raw = base64.b64decode(encoded, validate=True)
        _need(base64.b64encode(raw).decode('ascii') == encoded)
        return _body(request, raw)
    except Exception:
        raise AgentMediaFailure() from None


def child_main(*, connection_factory=http.client.HTTPSConnection):
    """Fixed entry; a test script may inject only the lower HTTPS boundary."""
    try:
        with _hard_budget() as deadline:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            _need(len(raw) <= MAX_INPUT)
            request = AgentMediaRequest.from_data(_json(raw))
            result = _native(request, deadline, connection_factory)
            output = json.dumps(_envelope(request, result), separators=(',', ':')).encode()
            _need(len(output) <= MAX_OUTPUT)
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
    except BaseException:
        sys.stdout.buffer.write(b'{"version":1,"ok":false}')
        sys.stdout.buffer.flush()


def _child_read(payload, command, outer_seconds):
    process = None
    trust_fd = None
    try:
        _need(type(outer_seconds) in (int, float) and 0 < outer_seconds <= 12)
        deadline = time.monotonic() + outer_seconds
        request = AgentMediaRequest.from_data(payload)
        raw = json.dumps(request.packet(), separators=(',', ':')).encode()
        _need(len(raw) <= MAX_INPUT)
        argv = [sys.executable, str(Path(__file__).resolve())] if command is None else list(command)
        trust_fd = shared._trust_bundle()
        environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8'}
        if trust_fd is not None:
            environment['SSL_CERT_FILE'] = '/proc/self/fd/' + str(trust_fd)
        _remaining(deadline)
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, close_fds=True,
            pass_fds=() if trust_fd is None else (trust_fd,))
        output, offset = bytearray(), 0
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = _remaining(deadline)
                for key, _ in selector.select(min(remaining, .2)):
                    if key.fileobj is process.stdin:
                        offset += os.write(process.stdin.fileno(), raw[offset:offset + 4096])
                        if offset == len(raw):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            output.extend(chunk)
                            _need(len(output) <= MAX_OUTPUT)
            _need(process.wait(timeout=_remaining(deadline)) == 0)
        value = _decode_output(request, output)
        _remaining(deadline)
        return value
    except BaseException:
        raise AgentMediaFailure() from None
    finally:
        cleanup_failed = False
        try:
            if process is not None:
                try:
                    try:
                        running = process.poll()
                    except BaseException:
                        running = None
                        cleanup_failed = True
                    if running is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            # The child can exit between poll and kill. Still
                            # wait below so that the original child is reaped.
                            pass
                        except BaseException:
                            cleanup_failed = True
                    try:
                        process.wait()
                    except BaseException:
                        cleanup_failed = True
                finally:
                    for stream in (process.stdin, process.stdout):
                        if stream is not None and not stream.closed:
                            try:
                                stream.close()
                            except BaseException:
                                cleanup_failed = True
        finally:
            if trust_fd is not None:
                try:
                    os.close(trust_fd)
                except BaseException:
                    cleanup_failed = True
        if cleanup_failed:
            raise AgentMediaFailure() from None


async def child_read(payload, *, command=None, outer_seconds=12):
    """command/short deadline are explicit offline subprocess seams only."""
    return await thread_call(_child_read, payload, command, outer_seconds)


def _cooperative(request, http):
    """Offline constructor seam: same integrity checks, soft elapsed deadline."""
    try:
        deadline = time.monotonic() + MEDIA_SECONDS
        value = http(request.url, _headers(request), _remaining(deadline), body=None)
        names = ('status', 'content_type', 'content_length', 'body', 'redirected', 'complete')
        if type(value) is dict:
            _need(set(value) == set(names))
            values = tuple(value[name] for name in names)
        else:
            values = tuple(getattr(value, name) for name in names)
        status, mime, size, raw, redirected, complete = values
        _need(type(status) is int and status == 200 and type(mime) is str and mime == request.mime
              and type(size) is int and size == request.size and redirected is False and complete is True)
        raw = _body(request, raw)
        _remaining(deadline)
        return raw
    except Exception:
        raise AgentMediaFailure() from None


class OwnAgentMediaReader:
    def __init__(self, record, *, origin, relay_pubkey, trusted_relays,
                 clock=lambda: int(time.time()), media_http=None):
        try:
            self.agent, self.owner, self.env_file = record.pubkey, record.owner_pubkey, str(record.env_file)
            _need(all(type(value) is str and HEX.fullmatch(value)
                      for value in (self.agent, self.owner, relay_pubkey))
                  and Path(self.env_file).is_absolute())
            _need(isinstance(trusted_relays, (tuple, list, set, frozenset)))
            self.origin = canonical_origin(origin)
            _need(self.origin.startswith('https://')
                  and self.origin in {canonical_origin(value) for value in trusted_relays})
            _need(callable(clock) and (media_http is None or callable(media_http)))
            self.pin, self.clock, self.media_http = relay_pubkey, clock, media_http
            self.transport_mode = 'native_child' if media_http is None else 'injected_cooperative'
        except Exception:
            raise AgentMediaFailure() from None

    def _request(self, url, sha256, mime, size):
        try:
            env = {}
            for line in read_owned(self.env_file, max_bytes=MAX_INPUT).decode().splitlines():
                name, separator, value = line.partition('=')
                if separator and name in ('BUZZ_PRIVATE_KEY', 'BUZZ_AUTH_TAG', 'BUZZ_RELAY_URL'):
                    _need(name not in env)
                    env[name] = value.strip().strip('"').strip("'")
            _need(canonical_origin(env['BUZZ_RELAY_URL']) == self.origin)
            return AgentMediaRequest.from_data(dict(version=1, operation='media_get',
                origin=self.origin, key=env['BUZZ_PRIVATE_KEY'], pin=self.pin,
                now=self.clock(), agent=self.agent, owner=self.owner,
                auth_tag=_json(env['BUZZ_AUTH_TAG']), url=url, sha256=sha256, mime=mime, size=size))
        except Exception:
            raise AgentMediaFailure() from None

    async def read_media(self, url, *, sha256, mime, size):
        request = await thread_call(self._request, url, sha256, mime, size)
        if self.media_http is None:
            raw = await child_read(request.packet())
        else:
            raw = await thread_call(_cooperative, request, self.media_http)
        current = await thread_call(self._request, url, sha256, mime, size)
        _need((current.key, current.auth_tag, current.origin, current.agent, current.owner) ==
              (request.key, request.auth_tag, request.origin, request.agent, request.owner))
        return raw


if __name__ == '__main__':
    child_main()
