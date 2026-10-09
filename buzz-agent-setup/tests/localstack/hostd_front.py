"""Run-scoped L3 HTTPS/WSS front; no Feishu, Docker or unit management.

Only the people answer is a fixture. HTTP and WebSocket bytes go to explicit
loopback backends with their request target, Host and signed headers intact.
Caller owns the run manifest, backend advertised public URL and generated TLS.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
from types import MappingProxyType
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import buzz_feishu_group_sync as gs
from hostd.safety import read_owned

HEADER_LIMIT = 16384
BODY_LIMIT = 4 * 1024 * 1024
CHANNEL_RE = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
NOTICE = ('本机 L3 前端配置或请求无法验证。怎么解决：检查固定本机目标、私有 TLS 文件和签名。'
          '复制给 AI：帮我核对 hostd 隔离验收前端的本机目标、文件权限和签名，不要输出凭据或个人信息。')


def _backend(value, scheme):
    try:
        parts = urlsplit(value)
        if (parts.scheme != scheme or parts.hostname != '127.0.0.1'
                or not parts.port or parts.username or parts.password
                or parts.path or parts.query or parts.fragment
                or value != f'{scheme}://127.0.0.1:{parts.port}'):
            raise ValueError
        return parts.port
    except (TypeError, ValueError):
        raise ValueError(NOTICE) from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _people_snapshot(people):
    try:
        if not isinstance(people, dict) or not people or len(people) > 64:
            raise ValueError
        result = {}
        for channel, mapping in people.items():
            if not isinstance(channel, str) or not CHANNEL_RE.fullmatch(channel):
                raise ValueError
            if not isinstance(mapping, dict) or set(mapping) != {'people', 'union_ids'}:
                raise ValueError
            body = json.dumps(dict(channel=channel, **mapping), separators=(',', ':')).encode()
            if len(body) > BODY_LIMIT:
                raise ValueError
            checked = gs.parse_people_response(body, channel)
            if checked.union_ids is None or len(set(checked.union_ids.values())) != len(checked.union_ids):
                raise ValueError
            result[channel] = body
        return MappingProxyType(result)
    except (Exception, SystemExit):
        raise ValueError(NOTICE) from None


class Front:
    """One fresh listener. Await start()/stop(); stop reaps all connection tasks.

    cert_file/key_file must be current-UID 0600 regular files with no symlink
    ancestor. Backends are exact http/ws://127.0.0.1:port origins. The immutable
    people snapshot and signer set are explicit approval input, never inferred.
    """
    host = '127.0.0.1'

    def __init__(self, *, cert_file, key_file, backend_http, backend_ws,
                 people, allowed_signers, clock=time.time, request_timeout=5):
        self.http_port = _backend(backend_http, 'http')
        self.ws_port = _backend(backend_ws, 'ws')
        self._people = _people_snapshot(people)
        try:
            self.allowed_signers = frozenset(allowed_signers)
            if not self.allowed_signers or any(not isinstance(p, str) or not gs.HEX64_RE.fullmatch(p)
                                               for p in self.allowed_signers):
                raise ValueError
            if (isinstance(request_timeout, bool) or not isinstance(request_timeout, (int, float))
                    or not math.isfinite(request_timeout) or not 0 < request_timeout <= 30
                    or not callable(clock)):
                raise ValueError
            self.cert_file, self.key_file = Path(cert_file), Path(key_file)
        except (TypeError, ValueError):
            raise ValueError(NOTICE) from None
        self.clock, self.request_timeout = clock, request_timeout
        self.server = None
        self.port = None
        self._tasks, self._writers = set(), set()
        self._stopped = False

    @property
    def https_origin(self):
        if self.port is None:
            raise ValueError(NOTICE)
        return f'https://127.0.0.1:{self.port}'

    @property
    def wss_origin(self):
        return self.https_origin.replace('https:', 'wss:', 1)

    @property
    def active_connections(self):
        return len(self._tasks)

    async def start(self):
        if self.server is not None or self._stopped:
            raise ValueError(NOTICE)
        try:
            # read_owned validates pinned nofollow FDs. Load only those bytes;
            # reopening the original path could race a symlink replacement.
            cert = read_owned(self.cert_file, max_bytes=65536)
            key = read_owned(self.key_file, max_bytes=65536)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            with tempfile.TemporaryDirectory(prefix='hostd-l3-tls-') as directory:
                for name, data in (('cert.pem', cert), ('key.pem', key)):
                    path = Path(directory) / name
                    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
                        stream.write(data)
                context.load_cert_chain(str(Path(directory) / 'cert.pem'), str(Path(directory) / 'key.pem'))
            self.server = await asyncio.start_server(self._accept, self.host, 0, ssl=context,
                ssl_handshake_timeout=self.request_timeout, limit=HEADER_LIMIT)
            self.port = self.server.sockets[0].getsockname()[1]
            return self
        except (Exception, SystemExit):
            raise ValueError(NOTICE) from None

    async def stop(self):
        self._stopped = True
        if self.server is not None:
            self.server.close()
        for writer in tuple(self._writers):
            writer.close()
            writer.transport.abort()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            # Python 3.12 waits for accepted connections as well as listener.
            # Close/reap them before waiting for server shutdown.
            await asyncio.wait_for(self.server.wait_closed(), max(1, self.request_timeout * 2))

    def _accept(self, reader, writer):
        # Register synchronously, before the coroutine can be scheduled behind
        # stop(). Late handshake callbacks also see the terminal flag.
        if self._stopped:
            writer.close()
            writer.transport.abort()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        self._writers.add(writer)

    async def _response(self, writer, status, body=b''):
        reasons = {200:'OK',400:'Bad Request',401:'Unauthorized',404:'Not Found',405:'Method Not Allowed',
                   408:'Request Timeout',413:'Payload Too Large',431:'Request Header Fields Too Large',502:'Bad Gateway'}
        writer.write((f'HTTP/1.1 {status} {reasons[status]}\r\nContent-Type: application/json\r\n'
                      f'Content-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n').encode()+body)
        await writer.drain()

    async def _headers(self, reader):
        raw = await reader.readuntil(b'\r\n\r\n')
        if len(raw) > HEADER_LIMIT:
            raise asyncio.LimitOverrunError('', len(raw))
        lines = raw[:-4].split(b'\r\n')
        request = lines.pop(0).decode('ascii').split(' ')
        if len(request) != 3 or request[0] not in ('GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS') or request[2] != 'HTTP/1.1':
            raise ValueError
        method, target, _ = request
        if not target.startswith('/') or target.startswith('//') or any(ord(c) <= 32 or ord(c) == 127 for c in target) or '#' in target:
            raise ValueError
        headers = {}
        for line in lines:
            key, sep, value = line.partition(b':')
            if not sep or not re.fullmatch(rb'[A-Za-z0-9!#$%&\'*+.^_`|~-]+', key):
                raise ValueError
            name = key.decode('ascii').lower()
            if name in headers or any(byte < 32 and byte != 9 or byte == 127 for byte in value):
                raise ValueError
            headers[name] = value.decode('latin1').strip()
        if headers.get('host') != f'127.0.0.1:{self.port}' or 'transfer-encoding' in headers or 'expect' in headers:
            raise ValueError
        length = headers.get('content-length', '0')
        if not re.fullmatch(r'[0-9]{1,8}', length):
            raise ValueError
        return raw, method, target, headers, int(length)

    def _authorized(self, authorization, target):
        try:
            if not authorization.startswith('Nostr ') or len(authorization) > 4096:
                return False
            event = json.loads(base64.b64decode(authorization[6:], validate=True), object_pairs_hook=_unique)
            return (gs._nip01_event_verified(event) and event['kind'] == 27235
                    and type(event['created_at']) is int and abs(self.clock()-event['created_at']) <= 60
                    and event['pubkey'] in self.allowed_signers and event['content'] == ''
                    and event['tags'] == [['u', self.https_origin+target], ['method', 'GET']])
        except (Exception, SystemExit):
            return False

    async def _relay_response(self, reader, writer, method, upgrade):
        raw = await reader.readuntil(b'\r\n\r\n')
        if len(raw) > HEADER_LIMIT:
            raise ValueError
        lines = raw[:-4].split(b'\r\n')
        first = lines.pop(0).split(b' ', 2)
        if len(first) != 3 or first[0] not in (b'HTTP/1.0', b'HTTP/1.1') or not re.fullmatch(rb'[0-9]{3}', first[1]):
            raise ValueError
        status = int(first[1])
        headers = {}
        for line in lines:
            key, separator, value = line.partition(b':')
            if not separator:
                raise ValueError
            name = key.decode('ascii').lower()
            if name in headers and name in ('content-length', 'transfer-encoding'):
                raise ValueError
            headers[name] = value.decode('latin1').strip()
        if upgrade and status == 101:
            if headers.get('upgrade', '').lower() != 'websocket':
                raise ValueError
            writer.write(raw)
            await writer.drain()
            return True
        if status < 200 or 300 <= status < 400:
            # No backend redirect can escape the fixed local origin.
            await self._response(writer, 502)
            return False
        body = bytearray()
        if method == 'HEAD' or status in (204, 304):
            pass
        elif 'transfer-encoding' in headers:
            if headers['transfer-encoding'].lower() != 'chunked' or 'content-length' in headers:
                raise ValueError
            while True:
                line = await reader.readuntil(b'\r\n')
                if len(line) > 128 or not re.fullmatch(rb'[0-9a-fA-F]+\r\n', line):
                    raise ValueError
                size = int(line[:-2], 16)
                if len(body) + size + len(line) + 2 > BODY_LIMIT:
                    raise ValueError
                body.extend(line)
                if size == 0:
                    # Trailers aren't required by relay JSON APIs; reject them
                    # rather than forwarding unchecked secondary headers.
                    if await reader.readexactly(2) != b'\r\n':
                        raise ValueError
                    body.extend(b'\r\n')
                    break
                chunk = await reader.readexactly(size+2)
                if not chunk.endswith(b'\r\n'):
                    raise ValueError
                body.extend(chunk)
        elif 'content-length' in headers:
            length = headers['content-length']
            if not re.fullmatch(r'[0-9]{1,8}', length) or int(length) > BODY_LIMIT:
                raise ValueError
            body.extend(await reader.readexactly(int(length)))
        else:
            while chunk := await reader.read(65536):
                if len(body) + len(chunk) > BODY_LIMIT:
                    raise ValueError
                body.extend(chunk)
        writer.write(raw+body)
        await writer.drain()
        return False

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        self._writers.add(writer)
        backend = None
        try:
            async with asyncio.timeout(self.request_timeout):
                raw, method, target, headers, length = await self._headers(reader)
                if length > BODY_LIMIT:
                    await self._response(writer, 413)
                    return
                body = await reader.readexactly(length)
                if target.startswith('/bind/'):
                    match = re.fullmatch(r'/bind/api/channels/('+CHANNEL_RE.pattern+r')/people', target)
                    if not match or match[1] not in self._people:
                        await self._response(writer, 404)
                    elif method != 'GET' or length:
                        await self._response(writer, 405)
                    elif not self._authorized(headers.get('authorization', ''), target):
                        await self._response(writer, 401)
                    else:
                        await self._response(writer, 200, self._people[match[1]])
                    return
                upgrade = headers.get('upgrade', '').lower() == 'websocket'
                if upgrade and (method != 'GET' or length or 'upgrade' not in headers.get('connection', '').lower().split(', ')):
                    raise ValueError
                upstream_reader, backend = await asyncio.open_connection(self.host, self.ws_port if upgrade else self.http_port)
                self._writers.add(backend)
                backend.write(raw+body)
                await backend.drain()
                if not await self._relay_response(upstream_reader, writer, method, upgrade):
                    return
            # Tunnel unchanged WebSocket frames; long connection has no HTTP
            # request deadline. Closing either side cancels/reaps both pumps.
            async def pipe(source, destination):
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
            pumps = [asyncio.create_task(pipe(reader, backend)), asyncio.create_task(pipe(upstream_reader, writer))]
            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except asyncio.LimitOverrunError:
            await self._response(writer, 431)
        except TimeoutError:
            await self._response(writer, 408)
        except (ValueError, UnicodeError, asyncio.IncompleteReadError):
            await self._response(writer, 400)
        except (ConnectionError, OSError):
            # No request, auth header, body or backend exception is logged.
            pass
        finally:
            for closing in (writer, backend):
                if closing is not None:
                    closing.close()
                    try:
                        await asyncio.wait_for(closing.wait_closed(), self.request_timeout)
                    except (TimeoutError, ConnectionError, OSError):
                        pass
                    self._writers.discard(closing)
            self._tasks.discard(task)
