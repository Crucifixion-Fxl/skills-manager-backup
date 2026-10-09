"""Ordinary browser Basic-authenticated loopback gateway for the private UDS Console.

No browser driver, credential URLs, Store, operation replay or LAN listener.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import binascii
from collections import deque
import hmac
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct

from .console_access import ConsoleAccess, AccessError, _open, _token, HEADER_LIMIT, BODY_LIMIT, FRAME_LIMIT, ROUTE
from .console import ConsoleServer
from .console_metadata import DisplayMetadata


class BasicConsoleGateway(ConsoleAccess):
    _csp = ConsoleServer._csp

    @classmethod
    def check(cls, runtime_dir, password_file, *, port=18481, request_timeout=2, metadata_file=None):
        if type(port) is not int or not 1 <= port <= 65535:raise AccessError()
        password_file = Path(password_file)
        own = super().check(runtime_dir, password_file.parent, request_timeout=request_timeout)
        own._password, own._password_inode, own._password_hash = _token(password_file)
        if hmac.compare_digest(own._password, own._token):raise AccessError()
        own._password_file = password_file
        own._bind_port = port
        own._failures = deque(maxlen=20)
        own._monitor = None
        own._metadata = DisplayMetadata.load(metadata_file)
        own.invalidated = asyncio.Event()
        return own

    def _validate(self):
        super()._validate()
        value, inode, digest = _token(self._password_file)
        if (inode != self._password_inode or digest != self._password_hash
                or not hmac.compare_digest(value, self._password)):raise AccessError()

    def _contains_secret(self, data):
        return any(value in data for value in (self._token.encode(), self._password.encode(),
                   base64.b64encode(b'owner:' + self._password.encode())))

    async def start(self):
        if self._server is not None or self._closed:raise AccessError()
        self._validate()
        self._server = await asyncio.start_server(self._handle, '127.0.0.1', self._bind_port, limit=HEADER_LIMIT)
        self.listener_address = self._server.sockets[0].getsockname()
        self.port = self.listener_address[1]
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self._monitor = asyncio.create_task(self._watch_credentials())
        return self.readback()

    async def _watch_credentials(self):
        try:
            while not self._closed:
                await asyncio.sleep(.5)
                try:self._validate()
                except Exception:
                    self.invalidated.set()
                    await self.close()
                    return
        except asyncio.CancelledError:pass

    async def close(self):
        task = self._monitor
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Python 3.12 Server.wait_closed waits for active transports too.
        # Revoke streams before awaiting the listener's final close.
        self._closed = True
        for writer in tuple(self._writers):writer.close()
        for request in tuple(self._tasks):
            if request is not asyncio.current_task():request.cancel()
        await super().close()

    async def _error(self, writer, status):
        body = b'{"ok":false,"error":"Console access unavailable"}'
        challenge = 'WWW-Authenticate: Basic realm="hostd Console", charset="UTF-8"\r\n' if status == 401 else ''
        writer.write((f'HTTP/1.1 {status} Error\r\n' + challenge +
                      'Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n'
                      'Referrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n'
                      f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body)
        await asyncio.wait_for(writer.drain(), self.request_timeout)

    async def _request(self,reader):
        header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),self.request_timeout)
        if len(header)>HEADER_LIMIT:raise ValueError(431)
        lines=header[:-4].decode('ascii').split('\r\n')
        if len(lines)>33 or any(len(line)>2048 for line in lines):raise ValueError(431)
        method,target,version=lines[0].split(' ')
        if version!='HTTP/1.1' or method not in ('GET','POST') or not (ROUTE.fullmatch(target) or method == 'GET' and target == '/api/metadata'):raise ValueError(400)
        fields={}
        for line in lines[1:]:
            name,colon,value=line.partition(':');key=name.lower()
            if not colon or not re.fullmatch(r'[A-Za-z0-9-]+',name) or key in fields or value.startswith('\t'):raise ValueError(400)
            value=value.strip()
            if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError(400)
            fields[key]=value
        if any(key in fields for key in ('transfer-encoding','upgrade','expect','proxy-authorization','proxy-connection')):raise ValueError(400)
        if fields.get('connection','').lower() not in ('','close','keep-alive'):raise ValueError(400)
        length=fields.get('content-length','0')
        if not re.fullmatch(r'[0-9]{1,4}',length) or int(length)>BODY_LIMIT:raise ValueError(413)
        if method=='GET' and int(length):raise ValueError(400)
        if (fields.get('host')!=self.origin[7:] or fields.get('origin') not in (None,self.origin)
                or fields.get('sec-fetch-site') not in (None,'none','same-origin') or fields.get('sec-fetch-dest') in ('iframe','frame')):raise ValueError(403)
        self._validate()
        now = asyncio.get_running_loop().time()
        while self._failures and self._failures[0] < now - 30:self._failures.popleft()
        if len(self._failures) >= 20:raise ValueError(429)
        auth = fields.get('authorization', '')
        try:
            if not auth.startswith('Basic '):raise ValueError()
            decoded = base64.b64decode(auth[6:], validate=True)
        except (ValueError, binascii.Error):decoded = b''
        if not hmac.compare_digest(decoded, b'owner:' + self._password.encode()):
            self._failures.append(now)
            raise ValueError(401)
        if method=='POST' and (fields.get('origin')!=self.origin or fields.get('x-hostd-request')!='1'):raise ValueError(403)
        body=await asyncio.wait_for(reader.readexactly(int(length)),self.request_timeout)
        if method == 'POST':
            if (fields.get('content-type') != 'application/json' or body != b'{}'
                    or not re.fullmatch(r'/api/(?:bindings/[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}/(?:pause|resume|backfill)|agents/[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}/restart)', target)
                    or not re.fullmatch(r'[0-9a-f]{64}', fields.get('idempotency-key', ''))):raise ValueError(400)
        # Only authenticated, structurally valid calls acquire backend identity.
        fields = {key: value for key, value in fields.items() if key in
                  ('host', 'origin', 'content-type', 'content-length', 'x-hostd-request', 'idempotency-key')}
        fields['authorization'] = 'Bearer ' + self._token
        return method,target,fields,body
    async def _handle(self,reader,writer):
        task=asyncio.current_task();self._tasks.add(task);self._writers.add(writer);upstream=None;sent=False
        try:
            if len(self._tasks)>32:raise ValueError(503)
            method,target,fields,body=await self._request(reader)
            self._validate()
            if target == '/api/metadata' and method == 'GET':
                metadata = self._metadata.public()
                if self._contains_secret(json.dumps(metadata, ensure_ascii=False).encode()):
                    metadata = DisplayMetadata().public()
                await ConsoleServer._send(self, writer, 200, metadata)
                return
            if target == '/' and method == 'GET':
                # Serve the immutable UI from this gateway package, never old backend HTML.
                html = ConsoleServer._read_ui(self)
                if self._password in html or self._token in html:raise AccessError()
                await ConsoleServer._send(self, writer, 200, html, html=True)
                return
            # Connect through the pinned parent descriptor, then recheck CAS.
            parent=_open(self.runtime_dir,directory=True)
            try:incoming,upstream=await asyncio.wait_for(asyncio.open_unix_connection(f'/proc/self/fd/{parent}/console.sock',limit=FRAME_LIMIT),self.request_timeout)
            finally:os.close(parent)
            self._validate();self._writers.add(upstream)
            peer=upstream.get_extra_info('socket')
            if peer is None or struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.geteuid():raise AccessError()
            fields['host']='hostd.local';fields['connection']='close'
            if 'origin' in fields:fields['origin']='http://hostd.local'
            upstream.write((method+' '+target+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields.items())+'\r\n').encode()+body)
            await asyncio.wait_for(upstream.drain(),self.request_timeout)
            head=await asyncio.wait_for(incoming.readuntil(b'\r\n\r\n'),self.request_timeout)
            if len(head)>HEADER_LIMIT or self._contains_secret(head):raise AccessError()
            lines=head[:-4].decode('ascii').split('\r\n');status=int(lines[0].split(' ')[1]);response={}
            for line in lines[1:]:
                k,colon,v=line.partition(':');key=k.lower()
                if not colon or key in response:raise AccessError()
                response[key]=v.strip()
            if 'location' in response or status in (301,302,303,307,308) or any(k in response for k in ('set-cookie','access-control-allow-origin','transfer-encoding')):raise AccessError()
            if response.get('content-type')=='text/event-stream' and target=='/api/events' and method=='GET' and status==200:
                writer.write(head);await writer.drain();sent=True
                disconnect=asyncio.create_task(reader.read(1))
                try:
                    while True:
                        packet=asyncio.create_task(incoming.readuntil(b'\n\n'))
                        done,_=await asyncio.wait((packet,disconnect),return_when=asyncio.FIRST_COMPLETED)
                        if disconnect in done:
                            packet.cancel();await asyncio.gather(packet,return_exceptions=True);break
                        data=packet.result()
                        if len(data)>FRAME_LIMIT or self._contains_secret(data):raise AccessError()
                        writer.write(data);await asyncio.wait_for(writer.drain(),self.request_timeout)
                finally:
                    disconnect.cancel()
                    if 'packet' in locals() and not packet.done():packet.cancel()
                    await asyncio.gather(disconnect,*( [packet] if 'packet' in locals() else []),return_exceptions=True)
            else:
                length=response.get('content-length','')
                if not re.fullmatch(r'[0-9]{1,8}',length) or int(length)>FRAME_LIMIT:raise AccessError()
                data=await asyncio.wait_for(incoming.readexactly(int(length)),self.request_timeout)
                if self._contains_secret(data):raise AccessError()
                writer.write(head+data);sent=True;await asyncio.wait_for(writer.drain(),self.request_timeout)
        except asyncio.CancelledError:raise
        except Exception as error:
            if not sent:
                status=408 if isinstance(error,asyncio.TimeoutError) else error.args[0] if isinstance(error,ValueError) and error.args and type(error.args[0]) is int else 503
                try:await self._error(writer,status)
                except Exception:pass
        finally:
            for current in (upstream,writer):
                if current:
                    current.close();self._writers.discard(current)
                    try:await current.wait_closed()
                    except Exception:pass
            self._tasks.discard(task)


async def run(args):
    gateway = BasicConsoleGateway.check(args.runtime_dir, args.password_file, port=args.port, metadata_file=args.metadata_file)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):loop.add_signal_handler(sig, stop.set)
    stopped = invalidated = None
    try:
        await gateway.start()
        print(json.dumps({'status': 'listening', 'origin': gateway.origin}), flush=True)
        stopped = asyncio.create_task(stop.wait())
        invalidated = asyncio.create_task(gateway.invalidated.wait())
        await asyncio.wait((stopped, invalidated), return_when=asyncio.FIRST_COMPLETED)
        return 1 if gateway.invalidated.is_set() else 0
    finally:
        for task in (stopped, invalidated):
            if task is not None:task.cancel()
        await asyncio.gather(*(task for task in (stopped, invalidated) if task is not None), return_exceptions=True)
        await gateway.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Ordinary browser Console over SSH loopback transport')
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--password-file', required=True, help='Private 0600 file with independent random 43-character password')
    parser.add_argument('--port', type=int, default=18481)
    parser.add_argument('--metadata-file', help='Optional private 0600 local display snapshot; never grants operation authority')
    args = parser.parse_args(argv)
    try:return asyncio.run(run(args))
    except (Exception, KeyboardInterrupt):
        print('{"status":"failed","error":"Console access unavailable"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
