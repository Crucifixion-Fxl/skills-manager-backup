"""Durable HTTP retries over actual private Unix sockets and SQLite."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_hostd_console as fixture
from hostd import console

KEY='1'*64

class DurableHTTP(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ConsoleTests.asyncSetUp
    asyncTearDown=fixture.ConsoleTests.asyncTearDown
    request=fixture.ConsoleTests.request
    assert_notice=fixture.ConsoleTests.assert_notice

    def install_action(self):
        self.calls=[]
        async def action(kind,target,operation):
            self.calls.append((kind,target,operation))
            if operation=='pause':self.store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id=?",(target,))
            return console.ActionResult(True,'paused')
        self.server.action=action
        return action

    async def post(self,*,key=KEY,operation='pause',headers=None):
        fields={} if key is None else {'Idempotency-Key':key}
        fields.update(headers or {})
        return await self.request('POST','/api/bindings/alpha/'+operation,headers=fields,body=b'{}',idempotency=False)

    async def test_missing_or_malformed_request_key_never_accepts_action(self):
        self.install_action()
        for key in (None,'', 'x', 'A'*64, '1'*63,'1'*65):
            with self.subTest(key_present=key is not None):
                status,payload,_=await self.post(key=key)
                self.assertEqual(status,400);self.assert_notice(payload)
        self.assertEqual(self.calls,[])

    async def test_lost_response_retry_returns_same_operation_without_reexecution(self):
        self.install_action()
        status,first,_=await self.post();self.assertEqual(status,202)
        await asyncio.sleep(.03)
        status,repeated,_=await self.post();self.assertEqual(status,202)
        self.assertEqual(repeated['id'],first['id'])
        await asyncio.sleep(.03);self.assertEqual(len(self.calls),1)
        self.assertNotIn(KEY,json.dumps(repeated));self.assertNotIn(self.token,json.dumps(repeated))

    async def test_request_key_cannot_be_reassigned_to_another_action(self):
        self.install_action();status,first,_=await self.post();self.assertEqual(status,202)
        await asyncio.sleep(.03)
        status,payload,_=await self.post(operation='resume')
        self.assertEqual(status,409);self.assert_notice(payload)
        self.assertEqual(len(self.calls),1)

    async def test_reopen_returns_durable_operation_and_never_dispatches_retry(self):
        action=self.install_action();status,first,_=await self.post();self.assertEqual(status,202)
        await asyncio.sleep(.03);await self.server.close()
        self.server=console.ConsoleServer(self.store,self.root/'runtime',action=action,
                request_timeout=.15,heartbeat_interval=.05,allowed_hosts=('hostd.local',))
        await self.server.start();self.token=self.server.token_path.read_text().strip()
        status,readback,_=await self.request(target='/api/operations/'+first['id'])
        self.assertEqual(status,200);self.assertEqual(readback['id'],first['id'])
        status,repeated,_=await self.post();self.assertEqual(status,202)
        self.assertEqual(repeated['id'],first['id']);await asyncio.sleep(.03)
        self.assertEqual(len(self.calls),1)

    async def test_wrong_peer_or_bearer_cannot_retrieve_or_retry_original_operation(self):
        self.install_action();status,first,_=await self.post();self.assertEqual(status,202)
        with patch.object(self.server,'_peer_uid',return_value=os.geteuid()+1):
            status,payload,_=await self.request(target='/api/operations/'+first['id'])
            self.assertEqual(status,403);self.assert_notice(payload)
        status,payload,_=await self.post(headers={'Authorization':'Bearer wrong'})
        self.assertEqual(status,401);self.assert_notice(payload)
        await asyncio.sleep(.03);self.assertEqual(len(self.calls),1)

if __name__=='__main__':unittest.main()
