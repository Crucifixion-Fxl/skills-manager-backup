#!/usr/bin/env python3
"""Standalone, standard-library Mac client. No hostd installation on the Mac.

The only remote command is REMOTE_HELPER. SSH trust/authentication stays with
OpenSSH; its captured stdout and the private Chrome CDP pipe never go to logs.
Linux transport tests are not evidence of macOS acceptance.
"""
import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

USER = 'jchen'
REMOTE_UID = 1009
RUNTIME = '/run/user/1009/buzz-hostd'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
LIMIT = 1024 * 1024
ROUTE = re.compile(r'/(?:api/(?:graph|events|(?:bindings|agents|operations)/[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}(?:/(?:pause|resume|backfill|restart))?))?\Z')
NOTICE = 'Console connection not verified or closed. Check SSH trust/authentication and Chrome; no operation was retried.'


class ClientError(Exception):
    def __init__(self):
        super().__init__(NOTICE)


class DiagnosticError(ClientError):
    """Allowlisted metadata only; never retain stderr, URLs or response bodies."""
    STAGES = {'chrome_validation': 10, 'ssh_start': 20, 'remote_helper': 21,
              'loopback_start': 30, 'chrome_launch': 40, 'chrome_cdp': 41,
              'page_readback': 50, 'cleanup': 60, 'client_runtime': 70}
    CODES = frozenset(('failed', 'timeout', 'unsupported_platform', 'python_version',
        'path_missing', 'path_not_canonical', 'untrusted_permissions', 'not_executable',
        'not_macho', 'signature_rejected', 'codesign_unavailable', 'spawn_failed',
        'host_trust_rejected', 'authentication_rejected', 'forward_rejected',
        'connection_failed', 'identity_file_rejected', 'invalid_handshake',
        'not_reaped', 'temporary_directory_changed'))

    def __init__(self, stage, code, *, process_exit=None):
        if stage not in self.STAGES or code not in self.CODES:
            raise ValueError('unknown diagnostic category')
        if process_exit is not None and (type(process_exit) is not int or not -255 <= process_exit <= 255):
            raise ValueError('invalid diagnostic exit status')
        super().__init__()
        self.stage, self.code, self.process_exit = stage, code, process_exit
        self.exit_status = self.STAGES[stage]

    def receipt(self):
        value = {'status': 'failed', 'stage': self.stage, 'code': self.code}
        if self.process_exit is not None:value['process_exit'] = self.process_exit
        return value


async def checked_step(stage, awaitable):
    try:
        return await awaitable
    except DiagnosticError:
        raise
    except asyncio.TimeoutError:
        raise DiagnosticError(stage, 'timeout') from None
    except Exception:
        raise DiagnosticError(stage, 'failed') from None


def ssh_failure_code(data):
    """Best-effort OpenSSH classification; unmatched text stays private."""
    lower = data.lower()
    for category, markers in (
        ('host_trust_rejected', (b'host key verification failed', b'remote host identification has changed')),
        ('authentication_rejected', (b'permission denied', b'no supported authentication methods', b'unprotected private key file')),
        ('forward_rejected', (b'address already in use', b'cannot listen to port', b'could not request local forwarding')),
        ('connection_failed', (b'connection refused', b'no route to host', b'connection timed out', b'could not resolve hostname'))):
        if any(marker in lower for marker in markers):return category
    return 'failed'


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ClientError()
        out[key] = value
    return out


# stdin: one bounded setup packet, then bounded unpredictable challenge strings.
# stdout: token once, then challenge acknowledgements; never a shell/user log.
# EOF closes this one SSH connection. Rotation terminates it without reauth.
REMOTE_HELPER = r'''
import hashlib,json,os,re,select,stat,sys

def unique(pairs):
 out={}
 for k,v in pairs:
  if k in out:raise ValueError()
  out[k]=v
 return out

def ident(s):return (s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def snapshot(path):
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  for part in path.split('/')[1:]:
   child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
   os.close(fd);fd=child
  base=os.fstat(fd)
  if base.st_uid!=os.geteuid() or stat.S_IMODE(base.st_mode)!=0o700:raise ValueError()
  sock=os.stat('console.sock',dir_fd=fd,follow_symlinks=False)
  if not stat.S_ISSOCK(sock.st_mode) or sock.st_uid!=os.geteuid() or stat.S_IMODE(sock.st_mode)!=0o600:raise ValueError()
  tokenfd=os.open('console.token',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=fd)
  try:
   meta=os.fstat(tokenfd)
   if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_nlink!=1:raise ValueError()
   raw=os.read(tokenfd,129)
   if not re.fullmatch(rb'[A-Za-z0-9_-]{43}\n?',raw):raise ValueError()
   if ident(meta)!=ident(os.fstat(tokenfd)) or ident(meta)!=ident(os.stat('console.token',dir_fd=fd,follow_symlinks=False)):raise ValueError()
  finally:os.close(tokenfd)
  if ident(base)!=ident(os.fstat(fd)) or ident(sock)!=ident(os.stat('console.sock',dir_fd=fd,follow_symlinks=False)):raise ValueError()
  return (ident(base),ident(sock),ident(meta),hashlib.sha256(raw).hexdigest()),raw.strip().decode('ascii')
 finally:os.close(fd)

def emit(value):
 sys.stdout.write(json.dumps(value,separators=(',',':'))+'\n');sys.stdout.flush()

try:
 raw=sys.stdin.buffer.readline(2049)
 if len(raw)>2048 or not raw.endswith(b'\n'):raise ValueError()
 req=json.loads(raw,object_pairs_hook=unique)
 if set(req)!={'uid','runtime','nonce'} or type(req['uid']) is not int or req['uid']!=os.geteuid():raise ValueError()
 path=req['runtime'];nonce=req['nonce']
 if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_+./-]+',path) or any(p in ('','..','.') for p in path.split('/')[1:]):raise ValueError()
 if not isinstance(nonce,str) or not re.fullmatch('[0-9a-f]{64}',nonce):raise ValueError()
 expected,token=snapshot(path)
 if snapshot(path)[0]!=expected:raise ValueError()
 emit({'nonce':nonce,'uid':os.geteuid(),'token':token})
 while True:
  if snapshot(path)[0]!=expected:raise ValueError()
  ready,_,_=select.select([sys.stdin.buffer],[],[],.25)
  if not ready:continue
  line=sys.stdin.buffer.readline(130)
  if not line:break
  if not re.fullmatch(rb'[0-9a-f]{64}\n',line):raise ValueError()
  if snapshot(path)[0]!=expected:raise ValueError()
  emit({'checked':line.decode().strip()})
except BaseException:sys.exit(1)
'''


def ssh_argv(port, *, host, user=USER, remote_python='/usr/bin/python3.12',
             runtime=RUNTIME, ssh='/usr/bin/ssh', identity=None, extra=()):
    # The operator selects the SSH host; trust/authentication remain strict.
    # Extra transport options remain test-only.
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
        raise ClientError()
    options = ('StrictHostKeyChecking=yes', 'ExitOnForwardFailure=yes',
               'ForwardAgent=no', 'ControlMaster=no', 'ControlPath=none',
               'ControlPersist=no', 'PermitLocalCommand=no', 'RemoteCommand=none',
               'ProxyCommand=none', 'ProxyJump=none', 'RequestTTY=no',
               'ServerAliveInterval=5', 'ServerAliveCountMax=1',
               'ConnectionAttempts=1', 'ConnectTimeout=10', 'NumberOfPasswordPrompts=1')
    identity_args = ['-i', str(identity)] if identity is not None else []
    return [ssh, '-F', '/dev/null', '-T', *identity_args, *extra, *sum((['-o', v] for v in options), []),
            '-o', 'Hostname=' + host, '-l', user,
            '-L', '127.0.0.1:' + str(port) + ':' + runtime + '/console.sock', host,
            'exec ' + shlex.quote(remote_python) + ' -I -c ' + shlex.quote(REMOTE_HELPER)]


class Remote:
    def __init__(self, argv, *, uid=REMOTE_UID, runtime=RUNTIME):
        self.argv, self.uid, self.runtime = argv, uid, runtime
        self.process = None
        self.invalid = asyncio.Event()
        self.lock = asyncio.Lock()
        self.token = None
        self.pending = {}
        self.reader_task = None
        self.stderr_task = None
        self.stderr_code = 'failed'

    def __repr__(self):
        return '<Remote private SSH channel>'

    async def start(self):
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=4096)
        except OSError:
            raise DiagnosticError('ssh_start', 'spawn_failed') from None
        self.stderr_task = asyncio.create_task(self._stderr())
        try:
            await self._handshake()
        except Exception as error:
            # Collect a prompt child exit and classified stderr without
            # printing either raw stream or retrying this SSH connection.
            try:await asyncio.wait_for(self.process.wait(), .5)
            except asyncio.TimeoutError:pass
            if self.process.returncode is not None:
                await asyncio.gather(self.stderr_task, return_exceptions=True)
            stage = 'ssh_start' if (self.process.returncode == 255 or self.stderr_code != 'failed'
                                   or isinstance(error, asyncio.TimeoutError)) else 'remote_helper'
            code = self.stderr_code if self.stderr_code != 'failed' else ('timeout' if isinstance(error, asyncio.TimeoutError) else 'invalid_handshake')
            raise DiagnosticError(stage, code, process_exit=self.process.returncode) from None

    async def _stderr(self):
        tail = b''
        while True:
            chunk = await self.process.stderr.read(4096)
            if not chunk:return
            tail = (tail + chunk)[-8192:]
            category = ssh_failure_code(tail)
            if category != 'failed':self.stderr_code = category

    async def _handshake(self):
        nonce = secrets.token_hex(32)
        self.process.stdin.write((json.dumps({'nonce': nonce, 'uid': self.uid,
                                             'runtime': self.runtime}) + '\n').encode())
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(self.process.stdout.readline(), 60)
        if len(raw) > 2048 or not raw.endswith(b'\n'):
            raise ClientError()
        value = json.loads(raw, object_pairs_hook=unique)
        if (set(value) != {'nonce', 'uid', 'token'} or value['nonce'] != nonce
                or type(value['uid']) is not int or value['uid'] != self.uid
                or not isinstance(value['token'], str)
                or not re.fullmatch('[A-Za-z0-9_-]{43}', value['token'])):
            raise ClientError()
        self.token = value['token']
        self.reader_task = asyncio.create_task(self._read())
        await self.fresh()

    async def _read(self):
        try:
            while True:
                raw = await self.process.stdout.readline()
                if len(raw) > 256 or not raw.endswith(b'\n'):
                    raise ClientError()
                value = json.loads(raw, object_pairs_hook=unique)
                if set(value) != {'checked'} or not isinstance(value['checked'], str):
                    raise ClientError()
                future = self.pending.get(value['checked'])
                if future is None or future.done():
                    raise ClientError()
                future.set_result(None)
        except Exception:
            self.invalidate()

    def invalidate(self):
        self.token = None
        self.invalid.set()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ClientError())

    async def fresh(self):
        async with self.lock:
            if self.invalid.is_set() or not self.token or self.process.returncode is not None:
                raise ClientError()
            nonce = secrets.token_hex(32)
            future = asyncio.get_running_loop().create_future()
            self.pending[nonce] = future
            try:
                self.process.stdin.write((nonce + '\n').encode())
                await self.process.stdin.drain()
                await asyncio.wait_for(future, 3)
                if self.invalid.is_set() or self.process.returncode is not None:
                    raise ClientError()
                return self.token
            except BaseException:
                self.invalidate()
                raise
            finally:
                self.pending.pop(nonce, None)

    async def close(self):
        self.invalidate()
        if self.process is not None:
            self.process.stdin.close()  # EOF to fixed helper; never signal a PID.
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                return False
        if self.reader_task:
            await asyncio.gather(self.reader_task, return_exceptions=True)
        if self.stderr_task:
            await asyncio.gather(self.stderr_task, return_exceptions=True)
        return True


def guarded_headers(origin, request, token):
    """Exact origin and route only; rejection happens before any credential use."""
    url = request.get('url')
    if not isinstance(url, str) or len(url) > 8192:
        raise ClientError()
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.netloc != origin[7:] or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or not ROUTE.fullmatch(parsed.path)):
        raise ClientError()
    method = request.get('method')
    if method not in ('GET', 'POST'):
        raise ClientError()
    headers = request.get('headers')
    if not isinstance(headers, dict) or len(headers) > 64:
        raise ClientError()
    fields = {}
    for name, value in headers.items():
        if (not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9-]+', name)
                or not isinstance(value, str) or len(value) > 2048
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or name.lower() in fields):
            raise ClientError()
        fields[name.lower()] = value
    if (fields.get('origin') not in (None, origin)
            or fields.get('sec-fetch-site') not in (None, 'none', 'same-origin')
            or fields.get('sec-fetch-dest') in ('iframe', 'frame')
            or any(k in fields for k in ('proxy-authorization', 'proxy-connection', 'upgrade', 'transfer-encoding'))):
        raise ClientError()
    if method == 'POST' and (fields.get('origin') != origin
                            or fields.get('x-hostd-request') != '1'
                            or fields.get('content-type') != 'application/json'):
        raise ClientError()
    fields.pop('authorization', None)
    fields.pop('host', None)  # Chrome rejects Host overrides; proxy rewrites it.
    fields['authorization'] = 'Bearer ' + token
    return [{'name': name, 'value': value} for name, value in fields.items()]


class Loopback:
    """Validate caller-supplied auth before forwarding; never authenticate guests.

Chrome 151 rejects Host in Fetch.continueRequest. This byte proxy performs the
same backend Host/Origin adaptation as the existing Linux ConsoleAccess.
"""
    def __init__(self, remote, tunnel_port):
        self.remote, self.tunnel_port = remote, tunnel_port
        self.server = None
        self.tasks, self.writers = set(), set()
        self.origin = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=16384)
        self.origin = 'http://127.0.0.1:' + str(self.server.sockets[0].getsockname()[1])

    async def close(self):
        if self.server:
            self.server.close();await self.server.wait_closed()
        for writer in tuple(self.writers):writer.close()
        for task in tuple(self.tasks):task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task);self.writers.add(writer)
        upstream = None
        sent = False
        try:
            if len(self.tasks) > 32:raise ClientError()
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(header) > 16384:raise ClientError()
            lines = header[:-4].decode('ascii').split('\r\n')
            if len(lines) > 33 or any(len(x) > 2048 for x in lines):raise ClientError()
            method, target, version = lines[0].split(' ')
            if version != 'HTTP/1.1' or not ROUTE.fullmatch(target):raise ClientError()
            fields = {}
            for line in lines[1:]:
                name, colon, value = line.partition(':')
                if not colon or not re.fullmatch('[A-Za-z0-9-]+', name) or name.lower() in fields or value.startswith('\t'):raise ClientError()
                fields[name.lower()] = value.strip()
            token = self.remote.token
            if (not token or self.remote.invalid.is_set() or fields.get('host') != self.origin[7:]
                    or not hmac.compare_digest(fields.get('authorization', ''), 'Bearer ' + token)):
                raise ClientError()
            guarded_headers(self.origin, {'url':self.origin+target, 'method':method, 'headers':fields}, '')
            if any(k in fields for k in ('expect', 'upgrade', 'transfer-encoding')) or fields.get('connection','').lower() not in ('','close','keep-alive'):raise ClientError()
            length = fields.get('content-length', '0')
            if not re.fullmatch('[0-9]{1,4}', length) or int(length) > 1024 or method == 'GET' and int(length):raise ClientError()
            body = await asyncio.wait_for(reader.readexactly(int(length)), 5)
            if not hmac.compare_digest(await self.remote.fresh(), token):raise ClientError()
            incoming, upstream = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.tunnel_port, limit=LIMIT), 5)
            self.writers.add(upstream)
            if not hmac.compare_digest(await self.remote.fresh(), token):raise ClientError()
            fields['host'] = 'hostd.local';fields['connection'] = 'close'
            if 'origin' in fields:fields['origin'] = 'http://hostd.local'
            wire = (method+' '+target+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields.items())+'\r\n').encode()+body
            upstream.write(wire);await asyncio.wait_for(upstream.drain(), 5)
            head = await asyncio.wait_for(incoming.readuntil(b'\r\n\r\n'), 35)
            if len(head) > 16384 or token.encode() in head:raise ClientError()
            lines = head[:-4].decode('ascii').split('\r\n')
            status = int(lines[0].split(' ')[1]);response = {}
            for line in lines[1:]:
                name, colon, value = line.partition(':')
                if not colon or not re.fullmatch('[A-Za-z0-9-]+',name) or name.lower() in response:raise ClientError()
                response[name.lower()] = value.strip()
            if status in (301,302,303,307,308) or any(k in response for k in ('location','set-cookie','access-control-allow-origin','transfer-encoding')):raise ClientError()
            if method == 'GET' and target == '/api/events' and status == 200 and response.get('content-type') == 'text/event-stream':
                writer.write(head);await writer.drain();sent = True
                disconnect = asyncio.create_task(reader.read(1))
                packet = None
                try:
                    while not self.remote.invalid.is_set():
                        packet = asyncio.create_task(incoming.readuntil(b'\n\n'))
                        done, _ = await asyncio.wait((packet, disconnect), return_when=asyncio.FIRST_COMPLETED)
                        if disconnect in done:break
                        data = packet.result()
                        if len(data) > LIMIT or token.encode() in data:raise ClientError()
                        writer.write(data);await asyncio.wait_for(writer.drain(),5)
                finally:
                    disconnect.cancel()
                    if packet is not None:packet.cancel()
                    await asyncio.gather(disconnect, *([packet] if packet else []), return_exceptions=True)
            else:
                length = response.get('content-length','')
                if not re.fullmatch('[0-9]{1,8}',length) or int(length) > LIMIT:raise ClientError()
                data = await asyncio.wait_for(incoming.readexactly(int(length)),5)
                if token.encode() in data:raise ClientError()
                writer.write(head+data);await writer.drain();sent = True
        except Exception:
            if not sent:
                try:
                    writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain()
                except Exception:pass
        finally:
            if upstream:
                self.writers.discard(upstream);upstream.close()
            self.writers.discard(writer);writer.close();self.tasks.discard(task)


class CDP:
    """The same null-delimited, concurrent command/event protocol as Linux UI."""
    def __init__(self, read_fd, write_fd, event):
        self.read_fd, self.write_fd, self.event = read_fd, write_fd, event
        self.pending, self.events = {}, set()
        self.serial = 0
        self.failed = asyncio.Event()
        self.lock = asyncio.Lock()
        self.buffer = b''
        self.closed = False
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        self.task = asyncio.create_task(self.receive())

    def read(self):
        while b'\0' not in self.buffer:
            if self.closed:
                raise ClientError()
            ready, _, _ = select.select([self.read_fd], [], [], .1)
            if not ready:
                continue
            data = os.read(self.read_fd, 65536)
            if not data or len(self.buffer) + len(data) > LIMIT:
                raise ClientError()
            self.buffer += data
        raw, self.buffer = self.buffer.split(b'\0', 1)
        return json.loads(raw, object_pairs_hook=unique)

    def write(self, data):
        end = time.monotonic() + 3
        while data:
            if self.closed or time.monotonic() >= end:
                raise ClientError()
            _, ready, _ = select.select([], [self.write_fd], [], .1)
            if ready:
                count = os.write(self.write_fd, data)
                if count <= 0:
                    raise ClientError()
                data = data[count:]

    async def command(self, method, params, session=None):
        if self.failed.is_set() or self.closed:
            raise ClientError()
        ident = None
        future = None
        try:
            async with self.lock:
                self.serial += 1
                ident = self.serial
                future = asyncio.get_running_loop().create_future()
                self.pending[ident] = future
                value = {'id': ident, 'method': method, 'params': params}
                if session:
                    value['sessionId'] = session
                wire = json.dumps(value, separators=(',', ':')).encode() + b'\0'
                if len(wire) > LIMIT:
                    raise ClientError()
                await asyncio.to_thread(self.write, wire)
            return await asyncio.wait_for(future, 10)
        finally:
            if ident is not None:self.pending.pop(ident, None)
            if future is not None:
                if not future.done():future.cancel()
                elif not future.cancelled():future.exception()

    def fail(self):
        self.failed.set()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ClientError())

    def event_done(self, task):
        self.events.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.fail()

    async def receive(self):
        try:
            while not self.closed:
                packet = await asyncio.to_thread(self.read)
                if not isinstance(packet, dict):
                    raise ClientError()
                if 'id' in packet:
                    future = self.pending.get(packet['id'])
                    if future is not None and not future.done():
                        if 'error' in packet or not isinstance(packet.get('result'), dict):
                            future.set_exception(ClientError())
                        else:
                            future.set_result(packet['result'])
                elif 'method' in packet:
                    if len(self.events) >= 64:
                        raise ClientError()
                    task = asyncio.create_task(self.event(packet))
                    self.events.add(task)
                    task.add_done_callback(self.event_done)
                else:
                    raise ClientError()
        except Exception:
            self.fail()

    async def close(self):
        self.closed = True
        self.fail()
        await asyncio.gather(self.task, return_exceptions=True)
        for task in tuple(self.events):
            task.cancel()
        await asyncio.gather(*self.events, return_exceptions=True)
        os.close(self.write_fd)
        os.close(self.read_fd)


def checked_chrome(path=CHROME):
    if sys.platform != 'darwin' or not hasattr(os, 'posix_spawn'):
        raise DiagnosticError('chrome_validation', 'unsupported_platform')
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise DiagnosticError('chrome_validation', 'path_not_canonical')
    for current in (path, *path.parents):
        try:meta = current.stat()
        except OSError:raise DiagnosticError('chrome_validation', 'path_missing') from None
        # /Applications is commonly root:admin 0775 on macOS. Trust only
        # that privileged administrative group on ancestor directories.
        group_write = meta.st_mode & 0o020
        trusted_admin_parent = current != path and meta.st_uid == 0 and meta.st_gid in (0, 80)
        if meta.st_uid not in (0, os.geteuid()) or meta.st_mode & 0o002 or group_write and not trusted_admin_parent:
            raise DiagnosticError('chrome_validation', 'untrusted_permissions')
    if not stat.S_ISREG(path.stat().st_mode) or not os.access(path, os.X_OK):
        raise DiagnosticError('chrome_validation', 'not_executable')
    with path.open('rb') as source:
        if source.read(4) not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
            raise DiagnosticError('chrome_validation', 'not_macho')
    try:
        result = subprocess.run(['/usr/bin/codesign', '--verify', '--strict',
                             '--deep', '-R', '=identifier "com.google.Chrome" and anchor apple generic and certificate leaf[subject.OU] = "EQHXZ8M8AV"',
                             str(path.parents[2])], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        raise DiagnosticError('chrome_validation', 'timeout') from None
    except OSError:
        raise DiagnosticError('chrome_validation', 'codesign_unavailable') from None
    if result.returncode:
        raise DiagnosticError('chrome_validation', 'signature_rejected', process_exit=result.returncode)
    return str(path)


class Browser:
    def __init__(self, binary, profile, origin, remote, *, extra=()):
        self.binary, self.profile, self.origin, self.remote = binary, Path(profile), origin, remote
        self.extra = extra  # Linux test-only headless flags; not a CLI option.
        self.pid = None
        self.reaped = False
        self.cdp = None
        self.sessions, self.targets = set(), {}
        self.page = None
        self.page_status = None
        self.closed = asyncio.Event()

    async def event(self, event):
        method = event['method']
        if method == 'Target.attachedToTarget':
            params = event['params']
            session, info = params['sessionId'], params['targetInfo']
            state = self.targets.setdefault(info['targetId'], {'ready': asyncio.Event()})
            if state.get('session') is not None:
                if state['session'] != session:
                    raise ClientError()
                return
            state['session'] = session
            await self.cdp.command('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True}, session)
            await self.cdp.command('Network.enable', {}, session)
            await self.cdp.command('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session)
            self.sessions.add(session)
            await self.cdp.command('Runtime.runIfWaitingForDebugger', {}, session)
            state['ready'].set()
        elif method == 'Fetch.requestPaused':
            session = event.get('sessionId')
            params = event['params']
            if session not in self.sessions:
                raise ClientError()
            try:
                # Validate before fetching the token, then recheck remote identity
                # immediately before continuing this individual request.
                guarded_headers(self.origin, params['request'], '')
                token = await self.remote.fresh()
                headers = guarded_headers(self.origin, params['request'], token)
            except Exception:
                await self.cdp.command('Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'}, session)
                return
            await self.cdp.command('Fetch.continueRequest', {'requestId': params['requestId'], 'headers': headers}, session)
        elif method == 'Network.responseReceived':
            response = event['params']['response']
            if event['params'].get('type') == 'Document' and response.get('url') == self.origin + '/':
                self.page_status = response.get('status')
        elif method == 'Target.detachedFromTarget':
            self.sessions.discard(event['params']['sessionId'])
            if any(v.get('session') == event['params']['sessionId'] for k, v in self.targets.items() if k == self.page):
                self.closed.set()
        elif method == 'Target.targetDestroyed' and event['params']['targetId'] == self.page:
            self.closed.set()

    async def start(self):
        if sys.platform == 'darwin':checked_chrome(self.binary)
        self.profile.mkdir(mode=0o700)
        child_read, write_fd = os.pipe()
        read_fd, child_write = os.pipe()
        read_copy = fcntl.fcntl(child_read, fcntl.F_DUPFD_CLOEXEC, 5)
        write_copy = fcntl.fcntl(child_write, fcntl.F_DUPFD_CLOEXEC, 5)
        null = os.open('/dev/null', os.O_RDWR)
        try:
            actions = [(os.POSIX_SPAWN_DUP2, null, fd) for fd in (0, 1, 2)] + [
                (os.POSIX_SPAWN_DUP2, read_copy, 3), (os.POSIX_SPAWN_DUP2, write_copy, 4)]
            argv = [self.binary, '--user-data-dir=' + str(self.profile), '--remote-debugging-pipe',
                    '--no-first-run', '--no-default-browser-check',
                    '--disable-background-networking', '--disable-sync', '--disable-extensions',
                    '--disable-breakpad', '--disable-crash-reporter', '--no-proxy-server', *self.extra, 'about:blank']
            environment = {'HOME': str(self.profile.parent), 'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}
            for name in ('DISPLAY', 'XAUTHORITY', 'TMPDIR'):
                if name in os.environ:environment[name] = os.environ[name]
            try:self.pid = os.posix_spawn(self.binary, argv, environment, file_actions=actions)
            except OSError:raise DiagnosticError('chrome_launch', 'spawn_failed') from None
        finally:
            for fd in (child_read, child_write, read_copy, write_copy, null):
                os.close(fd)
        self.cdp = CDP(read_fd, write_fd, self.event)
        await checked_step('chrome_cdp', self.cdp.command('Browser.setDownloadBehavior', {'behavior': 'deny'}))
        await checked_step('chrome_cdp', self.cdp.command('Target.setDiscoverTargets', {'discover': True}))
        await checked_step('chrome_cdp', self.cdp.command('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True}))
        target = await checked_step('chrome_cdp', self.cdp.command('Target.createTarget', {'url': 'about:blank'}))
        self.page = target['targetId']
        state = self.targets.setdefault(self.page, {'ready': asyncio.Event()})
        await checked_step('chrome_cdp', asyncio.wait_for(state['ready'].wait(), 10))
        await checked_step('chrome_cdp', self.cdp.command('Page.navigate', {'url': self.origin + '/'}, state['session']))
        # Remove only this private browser's bootstrap blank tabs. Closing the
        # Console target later ends the launcher, even if Chrome stays running.
        targets = await checked_step('chrome_cdp', self.cdp.command('Target.getTargets', {}))
        for info in targets.get('targetInfos', []):
            if info['targetId'] != self.page and info.get('type') == 'page' and info.get('url') == 'about:blank':
                prior = self.targets.get(info['targetId'])
                if prior is not None:await checked_step('chrome_cdp', asyncio.wait_for(prior['ready'].wait(),10))
                await checked_step('chrome_cdp', self.cdp.command('Target.closeTarget', {'targetId':info['targetId']}))

    async def verify(self):
        expression = """(async()=>{if(location.href!==ORIGIN+'/')return null;
if(document.readyState==='loading'||document.title!=='本机同步控制台'||!document.querySelector('#graph'))return null;
if(document.querySelector('#access')?.textContent!=='已读取本机状态。')return null;
const r=await fetch('/api/graph',{cache:'no-store'});if(r.status!==200)return null;
const g=await r.json();if(!Array.isArray(g.nodes)||!Array.isArray(g.edges)||g.notice)return null;
return {page:!!document.documentElement&&document.body!==null,graph_status:r.status,nodes:g.nodes.length,edges:g.edges.length};})()""".replace('ORIGIN', json.dumps(self.origin))
        end = time.monotonic() + 15
        while time.monotonic() < end:
            await self.remote.fresh()
            reply = await self.cdp.command('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True}, self.targets[self.page]['session'])
            value = reply.get('result', {}).get('value')
            if (self.page_status == 200 and isinstance(value, dict) and value.get('page') is True and value.get('graph_status') == 200
                    and type(value.get('nodes')) is int and 0 <= value['nodes'] <= 100000
                    and type(value.get('edges')) is int and 0 <= value['edges'] <= 100000):
                return {key: value[key] for key in ('page', 'graph_status', 'nodes', 'edges')}
            await asyncio.sleep(.1)
        raise ClientError()

    async def close(self):
        if self.cdp:
            try:
                await self.cdp.command('Browser.close', {})
            except Exception:
                pass
            await self.cdp.close()  # CDP pipe EOF, not PID-based termination.
        if self.pid is None:
            return True
        end = time.monotonic() + 5
        while time.monotonic() < end:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
            if pid == self.pid:
                self.reaped = True
                return True
            await asyncio.sleep(.05)
        return False


async def run(args):
    binary = checked_chrome(args.chrome)
    # Reserve-then-close race is fail-closed: SSH ExitOnForwardFailure is checked
    # by successful helper startup; collision is not silently retried.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    identity = args.identity_file
    if identity is not None:
        path = Path(identity).expanduser()
        try:meta = path.stat()
        except OSError:raise DiagnosticError('ssh_start', 'identity_file_rejected') from None
        if not path.is_absolute() or path.resolve() != path or not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
            raise DiagnosticError('ssh_start', 'identity_file_rejected')
        identity = str(path)
    directory = Path(tempfile.mkdtemp(prefix='hostd-console-'))
    directory.chmod(0o700)
    directory_identity = (directory.stat().st_dev, directory.stat().st_ino)
    remote = Remote(ssh_argv(port, host=args.host, identity=identity))
    proxy = Loopback(remote, port)
    browser = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        async def startup():
            nonlocal browser
            await checked_step('ssh_start', remote.start())
            await checked_step('loopback_start', proxy.start())
            browser = Browser(binary, directory / 'profile', proxy.origin, remote)
            await checked_step('chrome_launch', browser.start())
            return await checked_step('page_readback', browser.verify())
        starting = asyncio.create_task(startup())
        stopping = asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait((starting, stopping),return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                starting.cancel()
                await asyncio.gather(starting,return_exceptions=True)
                return
            evidence = starting.result()
            origin = proxy.origin
        finally:
            stopping.cancel()
            await asyncio.gather(stopping,return_exceptions=True)
        print(json.dumps({'status': 'verified', 'platform': sys.platform,
                          'temporary_local_url': origin + '/', 'access': 'this_Mac_Chrome_window_only',
                          **evidence}), flush=True)
        if not args.verify_only:
            waiters = [asyncio.create_task(event.wait()) for event in
                       (stopped, remote.invalid, browser.closed, browser.cdp.failed)]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                if not stopped.is_set() and not browser.closed.is_set():
                    if remote.invalid.is_set():raise DiagnosticError('remote_helper', 'failed')
                    if browser.cdp.failed.is_set():raise DiagnosticError('chrome_cdp', 'failed')
            finally:
                for task in waiters:
                    task.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
    finally:
        startup_error = sys.exc_info()[1]
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        remote.invalidate()
        await proxy.close()
        try:browser_done = await browser.close() if browser else True
        except Exception:browser_done = False
        try:ssh_done = await remote.close()
        except Exception:ssh_done = False
        if browser_done and ssh_done:
            meta = directory.lstat()
            if not stat.S_ISDIR(meta.st_mode) or (meta.st_dev,meta.st_ino) != directory_identity:
                raise DiagnosticError('cleanup', 'temporary_directory_changed')
            shutil.rmtree(directory)
        print(json.dumps({'status': 'closed' if browser_done and ssh_done else 'cleanup_pending',
                          'browser_reaped': browser_done, 'ssh_reaped': ssh_done}), flush=True)
        if not browser_done or not ssh_done:
            cleanup_error = DiagnosticError('cleanup', 'not_reaped')
            if isinstance(startup_error, DiagnosticError):
                # Keep the original failing stage while explicitly reporting
                # unconfirmed cleanup. Neither exception's raw context escapes.
                print(json.dumps(cleanup_error.receipt()), file=sys.stderr)
            else:
                raise cleanup_error


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mac SSH Console; existing trusted SSH login and Google Chrome required.')
    parser.add_argument('--host', required=True, help='Explicit SSH hostname or IPv4 address; no user or options.')
    parser.add_argument('--chrome', default=CHROME)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--identity-file', help='Optional existing private SSH key path; file content is never read by this client.')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 9):
            raise DiagnosticError('client_runtime', 'python_version')
        if sys.platform != 'darwin':
            raise DiagnosticError('chrome_validation', 'unsupported_platform')
        if not Path(args.chrome).is_file():
            raise DiagnosticError('chrome_validation', 'path_missing')
        asyncio.run(run(args))
        return 0
    except DiagnosticError as error:
        print(json.dumps(error.receipt()), file=sys.stderr)
        print(NOTICE, file=sys.stderr)
        return error.exit_status
    except (Exception, KeyboardInterrupt):
        print(json.dumps(DiagnosticError('client_runtime', 'failed').receipt()), file=sys.stderr)
        print(NOTICE, file=sys.stderr)
        return 70


if __name__ == '__main__':
    raise SystemExit(main())
