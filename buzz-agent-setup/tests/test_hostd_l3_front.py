"""Actual local TLS/HTTP/WSS; generated identities only, no external requests."""
import asyncio
import base64
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent / 'localstack'),
               str(Path(__file__).resolve().parents[1] / 'scripts')]
import buzz_feishu_group_sync as gs
try:
    import websockets
except ImportError:
    websockets = None

KEY = '0' * 63 + '1'
OTHER = '0' * 63 + '2'
OWNER = gs._signer_pubkey(KEY)
CHANNEL = '00000000-0000-0000-0000-000000000001'
NOW = 1800000000


@unittest.skipUnless(websockets is not None and Path('/usr/bin/openssl').is_file(),
                     'real loopback WSS/TLS tests require websockets and openssl')
class FrontTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cert, self.key = self.root / 'cert.pem', self.root / 'key.pem'
        await asyncio.to_thread(subprocess.run, ['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(self.key), '-out', str(self.cert), '-days', '1', '-subj', '/CN=127.0.0.1',
            '-addext', 'subjectAltName=IP:127.0.0.1'], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for p in (self.cert, self.key): p.chmod(0o600)
        self.received = []
        self.ws_requests = []
        async def backend(reader, writer):
            try:
                header = await reader.readuntil(b'\r\n\r\n')
                length = next((int(line.split(b':',1)[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
                body = await reader.readexactly(length)
                self.received.append((header,body))
                response = json.dumps({'body':body.decode(), 'headers':header.decode()}).encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(response)).encode()+b'\r\nConnection: close\r\n\r\n'+response)
                await writer.drain()
            finally:
                writer.close();await writer.wait_closed()
        self.backend = await asyncio.start_server(backend,'127.0.0.1',0)
        self.addAsyncCleanup(self.close_backend)
        async def echo(socket):
            self.ws_requests.append((socket.request.path,dict(socket.request.headers)))
            await socket.send('backend-first')
            async for data in socket: await socket.send(data)
        self.ws = await websockets.serve(echo,'127.0.0.1',0)
        self.addAsyncCleanup(self.close_ws)
        self.module = importlib.import_module('hostd_front')
        self.document = {CHANNEL:{'people':{OWNER:'ou_fixtureowner'},'union_ids':{OWNER:'on_fixtureowner'}}}
        self.front = self.make_front()
        await self.front.start()
        self.addAsyncCleanup(self.front.stop)
        self.trust = ssl.create_default_context(cafile=str(self.cert))

    async def close_backend(self):
        self.backend.close();await self.backend.wait_closed()

    async def close_ws(self):
        self.ws.close();await self.ws.wait_closed()

    def make_front(self,**kwargs):
        options = dict(cert_file=self.cert,key_file=self.key,
            backend_http=f'http://127.0.0.1:{self.backend.sockets[0].getsockname()[1]}',
            backend_ws=f'ws://127.0.0.1:{self.ws.sockets[0].getsockname()[1]}',
            people=self.document,allowed_signers=frozenset({OWNER}),clock=lambda:NOW,
            request_timeout=.3)
        options.update(kwargs)
        return self.module.Front(**options)

    def authorization(self, *, method='GET', path=None, key=KEY, stamp=NOW):
        return gs.nip98_header(key,method,self.front.https_origin+(path or self.people_path),
                              datetime.fromtimestamp(stamp,timezone.utc))

    @property
    def people_path(self):return f'/bind/api/channels/{CHANNEL}/people'

    async def request(self,path,*,method='GET',auth=None,body=b'',headers=b''):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
        try:
            raw=(f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.front.port}\r\nContent-Length: {len(body)}\r\n').encode()
            if auth:raw+=f'Authorization: {auth}\r\n'.encode()
            writer.write(raw+headers+b'\r\n'+body);await writer.drain()
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
            length=next((int(line.split(b':',1)[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
            return int(header.split()[1]),await reader.readexactly(length)
        finally:writer.close();await writer.wait_closed()

    async def test_signed_people_success_actual_tls_and_immutable_snapshot(self):
        self.document[CHANNEL]['union_ids'][OWNER]='on_changed'
        status,body=await self.request(self.people_path,auth=self.authorization())
        self.assertEqual(status,200);self.assertEqual(json.loads(body),{'channel':CHANNEL,**{k:v for k,v in {'people':{OWNER:'ou_fixtureowner'},'union_ids':{OWNER:'on_fixtureowner'}}.items()}})
        self.assertFalse(self.received);self.assertEqual(self.front.host,'127.0.0.1')

    async def test_untrusted_tls_fails_before_http(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await asyncio.open_connection('127.0.0.1',self.front.port,ssl=ssl.create_default_context(),server_hostname='127.0.0.1')

    async def test_wrong_method_path_timestamp_channel_signer_and_signature(self):
        for method,path,auth,expected in [
            ('POST',self.people_path,self.authorization(method='POST'),405),
            ('GET',self.people_path,self.authorization(method='POST'),401),
            ('GET',self.people_path,self.authorization(path=self.people_path+'?x=1'),401),
            ('GET',self.people_path,self.authorization(stamp=NOW-61),401),
            ('GET',self.people_path,self.authorization(stamp=NOW+61),401),
            ('GET',self.people_path,self.authorization(key=OTHER),401),
            ('GET','/bind/api/channels/00000000-0000-0000-0000-000000000099/people',self.authorization(),404),
            ('GET',self.people_path,'Nostr invalid',401),
            ('GET',self.people_path,None,401)]:
            with self.subTest(method=method,path=path,expected=expected):
                status,body=await self.request(path,method=method,auth=auth)
                self.assertEqual(status,expected);self.assertNotIn(OWNER.encode(),body)
        event=json.loads(base64.b64decode(self.authorization()[6:]));event['sig']='0'*128
        status,_=await self.request(self.people_path,auth='Nostr '+base64.b64encode(json.dumps(event).encode()).decode())
        self.assertEqual(status,401);self.assertFalse(self.received)

    async def test_people_namespace_never_falls_back_to_backend(self):
        for path in [self.people_path+'?x=1','/bind/api/channels/invalid/people','/bind/api/channels/'+CHANNEL+'/people/extra']:
            status,_=await self.request(path,auth=self.authorization());self.assertEqual(status,404)
        self.assertFalse(self.received)

    async def test_http_post_preserves_signed_full_url_header_target_and_body(self):
        body=b'{"filters":[{"kinds":[0]}]}'
        auth=gs.nip98_header(KEY,'POST',self.front.https_origin+'/query',datetime.fromtimestamp(NOW,timezone.utc),body=body)
        status,_=await self.request('/query',method='POST',auth=auth,body=body)
        self.assertEqual(status,200);header,actual=self.received[-1]
        self.assertEqual(actual,body);self.assertIn(('Authorization: '+auth).encode(),header)
        self.assertIn(f'Host: 127.0.0.1:{self.front.port}'.encode(),header)
        self.assertTrue(header.startswith(b'POST /query HTTP/1.1\r\n'))

    async def test_actual_wss_proxy_bidirectional_text_and_binary(self):
        auth=self.authorization()
        async with websockets.connect(self.front.wss_origin+'/relay?subscription=test',ssl=self.trust,
                                     additional_headers={'Authorization':auth}) as socket:
            self.assertEqual(await socket.recv(),'backend-first')
            await socket.send('client-frame');self.assertEqual(await socket.recv(),'client-frame')
            await socket.send(b'\x00\xffbinary');self.assertEqual(await socket.recv(),b'\x00\xffbinary')
        path,headers=self.ws_requests[-1];self.assertEqual(path,'/relay?subscription=test')
        self.assertEqual(headers['authorization'],auth);self.assertEqual(headers['host'],f'127.0.0.1:{self.front.port}')

    async def test_stop_reaps_open_websocket_and_slow_client(self):
        socket=await websockets.connect(self.front.wss_origin,ssl=self.trust)
        reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
        writer.write(b'GET /');await writer.drain()
        await asyncio.wait_for(self.front.stop(),2)
        with self.assertRaises(websockets.ConnectionClosed):
            await asyncio.wait_for(socket.recv(),2);await asyncio.wait_for(socket.recv(),2)
        self.assertEqual(await asyncio.wait_for(reader.read(),2),b'');writer.close();await writer.wait_closed()
        self.assertEqual(self.front.active_connections,0)

    async def test_backend_nonloopback_or_url_injection_rejected(self):
        for backend in ['http://example.com:80','http://127.0.0.1:80/path','http://user@127.0.0.1:80','http://127.0.0.1:80?x=1']:
            with self.subTest(backend=backend),self.assertRaises(ValueError):self.make_front(backend_http=backend)

    async def test_private_tls_paths_reject_symlink_and_insecure_permissions(self):
        linked=self.root/'linked.pem';linked.symlink_to(self.key)
        with self.assertRaises(ValueError):await self.make_front(key_file=linked).start()
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):await self.make_front().start()
        self.key.chmod(0o600)
        ancestor=self.root/'alias';ancestor.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):await self.make_front(key_file=ancestor/'key.pem').start()

    async def test_bounded_http_rejects_smuggling_and_header_overflow(self):
        status,_=await self.request('/query',method='POST',headers=b'Transfer-Encoding: chunked\r\n')
        self.assertEqual(status,400)
        status,_=await self.request('/query',headers=b'Content-Length: 0\r\n')
        self.assertEqual(status,400)
        reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
        writer.write(b'GET / HTTP/1.1\r\nX: '+b'x'*17000+b'\r\n\r\n');await writer.drain()
        self.assertIn(b' 431 ',await asyncio.wait_for(reader.read(),2));writer.close();await writer.wait_closed()

    async def test_slow_header_gets_bounded_timeout(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
        writer.write(b'GET /');await writer.drain()
        self.assertIn(b' 408 ',await asyncio.wait_for(reader.read(),2));writer.close();await writer.wait_closed()

    async def test_http_content_length_completes_without_backend_eof_or_extra_response(self):
        async def keepalive(reader,writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx');await writer.drain()
                await reader.read()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(keepalive,'127.0.0.1',0)
        try:
            self.front.http_port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
            writer.write(f'GET /query HTTP/1.1\r\nHost: 127.0.0.1:{self.front.port}\r\n\r\n'.encode());await writer.drain()
            response=await asyncio.wait_for(reader.read(),2)
            self.assertEqual(response.count(b'HTTP/1.1'),1);self.assertTrue(response.endswith(b'\r\n\r\nx'))
            writer.close();await writer.wait_closed()
        finally:server.close();await server.wait_closed()

    async def test_backend_websocket_handshake_has_http_deadline(self):
        async def silent(reader,writer):
            try:await reader.read()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(silent,'127.0.0.1',0)
        try:
            self.front.ws_port=server.sockets[0].getsockname()[1]
            status,_=await self.request('/relay',headers=b'Upgrade: websocket\r\nConnection: Upgrade\r\n')
            self.assertEqual(status,408)
        finally:server.close();await server.wait_closed()

    async def test_actual_chunked_backend_response_is_forwarded_without_rewriting(self):
        response=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n'
        async def chunked(reader,writer):
            try:
                await reader.readuntil(b'\r\n\r\n');writer.write(response);await writer.drain()
                await reader.read()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(chunked,'127.0.0.1',0)
        try:
            self.front.http_port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',self.front.port,ssl=self.trust,server_hostname='127.0.0.1')
            writer.write(f'GET /query HTTP/1.1\r\nHost: 127.0.0.1:{self.front.port}\r\n\r\n'.encode());await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(),2),response)
            writer.close();await writer.wait_closed()
        finally:server.close();await server.wait_closed()

    async def test_invalid_people_mapping_fails_closed(self):
        for doc in [{CHANNEL:{'people':{},'union_ids':{OWNER:'ou_wrong_namespace'}}}, {'invalid':self.document[CHANNEL]}]:
            with self.subTest(doc=doc),self.assertRaises(ValueError):self.make_front(people=doc)


if __name__=='__main__':unittest.main()
