"""Real private UDS/SQL gateway tests; browser automation is test-only."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd import console as console_module
from hostd.console import ConsoleServer, ActionResult
from hostd.console_operations import ConsoleReceipt, current_intent
from hostd.console_web import BasicConsoleGateway
from hostd.console_access import AccessError
from hostd.store import Store, BindingRecord


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'state'/'db')
        self.store.reconcile_bindings([BindingRecord('alpha','channel_alpha','oc_alpha','cli_alpha',
            '/private/profile','/private/config','/private/data',mirror_pubkey='d'*64)], now=100)
        self.server = ConsoleServer(self.store, self.root/'runtime', request_timeout=.5, heartbeat_interval=.03)
        await self.server.start()
        self.authdir = self.root/'auth'; self.authdir.mkdir(mode=0o700)
        self.password = secrets.token_urlsafe(32)
        self.password_file = self.authdir/'password'
        self.password_file.write_text(self.password+'\n'); self.password_file.chmod(0o600)
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1',0)); port=reserve.getsockname()[1]
        self.gateway = BasicConsoleGateway.check(self.server.runtime_dir, self.password_file, port=port, request_timeout=.5)
        await self.gateway.start()
        self.writers=[]

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close(); await writer.wait_closed()
        await self.gateway.close(); await self.server.close(); self.store.close(); self.tmp.cleanup()

    def headers(self, *, auth=True):
        rows=[('Host',self.gateway.origin[7:])]
        if auth:rows.append(('Authorization','Basic '+base64.b64encode(('owner:'+self.password).encode()).decode()))
        return rows

    async def request(self, path='/api/graph', method='GET', *, headers=None, body=b'', auth=True, stream=False):
        rows=self.headers(auth=auth)+list(headers or [])
        reader,writer=await asyncio.open_connection('127.0.0.1',self.gateway.port)
        self.writers.append(writer)
        writer.write((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in rows)+'\r\n').encode()+body)
        await writer.drain()
        if stream:return reader,writer
        raw=await asyncio.wait_for(reader.read(),2)
        head,payload=raw.split(b'\r\n\r\n',1)
        return int(head.split()[1]),head,payload

    async def test_anonymous_and_wrong_auth_never_connect_upstream(self):
        with mock.patch('hostd.console_web.asyncio.open_unix_connection', side_effect=AssertionError('must not connect')) as opened:
            for path in ('/','/api/graph','/api/events'):
                status,head,body=await self.request(path,auth=False)
                self.assertEqual(status,401);self.assertIn(b'WWW-Authenticate: Basic realm="hostd Console"',head)
            for value in ('Basic !!!!','Bearer '+self.server._token,'Basic '+base64.b64encode(b'owner:wrong').decode()):
                self.assertEqual((await self.request(auth=False,headers=[('Authorization',value)]))[0],401)
            opened.assert_not_called()

    async def test_fixed_ui_and_graph_no_secret(self):
        for path in ('/','/api/graph','/api/bindings/alpha'):
            status,head,body=await self.request(path)
            self.assertEqual(status,200)
            for secret in (self.password,self.server._token):self.assertNotIn(secret.encode(),head+body)
            if path=='/':
                self.assertIn(b"credentials:'same-origin'",body)
                self.assertIn(b'Content-Security-Policy:',head)

    async def test_cross_site_framing_and_post_boundaries_before_upstream(self):
        cases=[{'headers':[('Host','evil.invalid')]},{'headers':[('Origin','null')]},
            {'headers':[('Origin','https://evil.invalid')]},{'headers':[('Sec-Fetch-Site','cross-site')]},
            {'headers':[('Sec-Fetch-Dest','iframe')]},{'headers':[('Transfer-Encoding','chunked')]},
            {'headers':[('Content-Length','0'),('content-length','0')]},{'headers':[('Expect','100-continue')]},
            {'headers':[('Connection','upgrade')]},{'headers':[('Content-Length','1025')]},
            {'path':'/api/graph?secret=x'},{'method':'OPTIONS'},{'method':'POST'},
            {'headers':[('Authorization','Basic duplicate')]},{'headers':[('X-Test','x\x01y')]}]
        with mock.patch('hostd.console_web.asyncio.open_unix_connection', side_effect=AssertionError('must not connect')) as opened:
            for case in cases:
                with self.subTest(case=case):self.assertGreaterEqual((await self.request(**case))[0],400)
            opened.assert_not_called()

    async def test_valid_basic_cannot_bypass_csrf_or_unknown_post(self):
        common=[('Origin',self.gateway.origin),('Content-Type','application/json'),('Content-Length','2'),('Idempotency-Key','a'*64)]
        with mock.patch('hostd.console_web.asyncio.open_unix_connection', side_effect=AssertionError('must not connect')) as opened:
            self.assertEqual((await self.request('/api/bindings/alpha/pause','POST',headers=common,body=b'{}'))[0],403)
            self.assertEqual((await self.request('/api/graph','POST',headers=common+[('X-Hostd-Request','1')],body=b'{}'))[0],400)
            opened.assert_not_called()

    async def test_actual_sql_idempotency_and_same_owner(self):
        receipts={};calls=[]
        async def action(kind,target,operation):
            calls.append((kind,target,operation));intent=current_intent()
            with self.store.transaction():self.store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
            receipts[intent.operation_id]=ConsoleReceipt(intent.operation_id,'paused',hashlib.sha256(intent.operation_id.encode()).hexdigest())
            return ActionResult(True,'paused')
        self.server.action=action;self.server.operation_readback=lambda intent:receipts.get(intent.operation_id)
        fields=[('Origin',self.gateway.origin),('Content-Type','application/json'),('X-Hostd-Request','1'),('Content-Length','2'),('Idempotency-Key','a'*64)]
        status,_,body=await self.request('/api/bindings/alpha/pause','POST',headers=fields,body=b'{}')
        self.assertEqual(status,202);receipt=json.loads(body)
        await self.server.wait_operation(receipt['id'])
        status,_,body=await self.request('/api/bindings/alpha/pause','POST',headers=fields,body=b'{}')
        self.assertEqual(json.loads(body)['id'],receipt['id']);self.assertEqual(len(calls),1)

    async def test_password_rotation_closes_idle_sse_and_listener(self):
        reader,writer=await self.request('/api/events',stream=True)
        self.assertIn(b' 200 ',await reader.readuntil(b'\r\n\r\n'))
        self.assertIn(b'event: snapshot',await reader.readuntil(b'\n\n'))
        self.password_file.write_text(secrets.token_urlsafe(32)+'\n')
        await asyncio.wait_for(self.gateway.invalidated.wait(),2)
        await asyncio.wait_for(reader.read(),2)
        await asyncio.sleep(.05)
        self.assertEqual(self.server.subscriber_count,0)
        self.assertTrue(self.gateway._closed)

    async def test_backend_token_rotation_rejects_immediately(self):
        self.server.token_path.write_text(secrets.token_urlsafe(32)+'\n')
        with mock.patch('hostd.console_web.asyncio.open_unix_connection') as opened:
            self.assertEqual((await self.request())[0],503);opened.assert_not_called()

    async def test_password_permissions_hardlink_and_backend_reuse_rejected(self):
        self.password_file.chmod(0o644)
        with self.assertRaises(AccessError):BasicConsoleGateway.check(self.server.runtime_dir,self.password_file)
        self.password_file.chmod(0o600)
        os.link(self.password_file,self.authdir/'alias')
        with self.assertRaises(AccessError):BasicConsoleGateway.check(self.server.runtime_dir,self.password_file)
        (self.authdir/'alias').unlink()
        self.password_file.write_text(self.server._token+'\n')
        with self.assertRaises(AccessError):BasicConsoleGateway.check(self.server.runtime_dir,self.password_file)

    async def test_auth_failures_are_bounded(self):
        for _ in range(20):self.assertEqual((await self.request(auth=False))[0],401)
        self.assertEqual((await self.request(auth=False))[0],429)

    async def test_unknown_upstream_response_never_retries_post(self):
        calls=[]
        async def drop(reader,writer):
            head=await reader.readuntil(b'\r\n\r\n');body=await reader.readexactly(2)
            calls.append(head+body);writer.close();await writer.wait_closed()
        replacement=self.root/'replacement.sock'
        srv=await asyncio.start_unix_server(drop,path=str(replacement));replacement.chmod(0o600)
        # Test-only transport seam preserves real parent validation/peer identity.
        real_open=asyncio.open_unix_connection
        async def connect(*args,**kwargs):return await real_open(str(replacement),limit=1024*1024)
        fields=[('Origin',self.gateway.origin),('Content-Type','application/json'),('X-Hostd-Request','1'),('Content-Length','2'),('Idempotency-Key','b'*64)]
        try:
            with mock.patch('hostd.console_web.asyncio.open_unix_connection',side_effect=connect):
                self.assertEqual((await self.request('/api/bindings/alpha/pause','POST',headers=fields,body=b'{}'))[0],503)
            self.assertEqual(len(calls),1)
            self.assertNotIn(self.password.encode(),calls[0]);self.assertIn(self.server._token.encode(),calls[0])
        finally:srv.close();await srv.wait_closed()



if __name__=='__main__':unittest.main()
