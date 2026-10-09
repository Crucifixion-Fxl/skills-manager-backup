"""Real root factory/Unix HTTP/SQL/driver; only process transport is synthetic."""
import asyncio
import hashlib
import json
import threading
import unittest
from unittest import mock

import test_hostd_agent_operations as domain
import test_hostd_wiring_lifecycle as root_fixture
from hostd import agent_operations
from hostd.store import Store
from hostd_real_onboarding_fixture import RealOnboardingInput


class RootConsoleRestart(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        domain.RestartTests.setUp(self)
        root_fixture.Wiring.setUp(self)
        self.h.store_path=self.db.path
        self.h.console_dir=self.root/'console'
        self.real_input=RealOnboardingInput(self,private_key=domain.KEY,
            owner_key='2'*64,app_id='cli_agent',env_file=self.env_file,
            prompt_file=self.prompt,responsible_file=self.responsible,
            unit=self.spec.unit,state_dir=self.root)
        self.h.onboarding_config=self.real_input.config
        await self.start_root_services()

    async def start_root_services(self):
        with self.real_input.factory_patch():
            await self.h.start_onboarding()
        self.assertIs(root_fixture.hd.Store,Store,'root factory must use canonical Store identity')
        self.assertIs(type(self.h.runtime_store),Store)
        self.assertIs(type(self.h.restart_agent),agent_operations.AgentRestartDriver)
        self.assertIs(self.h.restart_agent.store,self.h.runtime_store)
        self.h.runtime_store.conn=domain.SQLGuard(self.h.runtime_store.conn)
        self.h.restart_agent.ops=self.ops
        self.h.restart_agent.observations=2
        await self.h.start_console()
        self.token=self.h.console.token_path.read_text().strip()
        self.assertIs(self.h.console.action.__self__,self.h)

    async def asyncTearDown(self):
        if self.ops.release:self.ops.release.set()
        await self.h.close_console()
        await self.h.close_onboarding()

    async def open_post(self,key):
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        packet=('POST /api/agents/'+domain.AGENT+'/restart HTTP/1.1\r\nHost: hostd.local\r\n'
                'Authorization: Bearer '+self.token+'\r\nOrigin: http://hostd.local\r\n'
                'X-Hostd-Request: 1\r\nIdempotency-Key: '+key+'\r\nContent-Type: application/json\r\n'
                'Content-Length: 2\r\nConnection: close\r\n\r\n{}').encode()
        writer.write(packet);await writer.drain()
        return reader,writer

    async def request(self,key):
        reader,writer=await self.open_post(key)
        try:
            raw=await asyncio.wait_for(reader.read(),2)
            head,body=raw.split(b'\r\n\r\n',1)
            return int(head.split(b' ',2)[1]),json.loads(body)
        finally:writer.close();await writer.wait_closed()

    async def get_operation(self,identity):
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        writer.write(('GET /api/operations/'+identity+' HTTP/1.1\r\nHost: hostd.local\r\n'
                      'Authorization: Bearer '+self.token+'\r\nConnection: close\r\n\r\n').encode())
        await writer.drain()
        try:
            raw=await asyncio.wait_for(reader.read(),2);head,body=raw.split(b'\r\n\r\n',1)
            return int(head.split(b' ',2)[1]),json.loads(body)
        finally:writer.close();await writer.wait_closed()

    async def wait_until(self,predicate):
        for _ in range(400):
            if predicate():return
            await asyncio.sleep(.005)
        self.fail('offline root execution did not reach the expected bounded stage')

    async def test_actual_root_native_driver_http_links_ack_and_duplicate_never_restarts(self):
        status,queued=await self.request('1'*64)
        self.assertEqual(status,202)
        completed=await self.h.console.wait_operation(queued['id'])
        self.assertEqual(completed['status'],'completed');self.assertEqual(completed['observed'],'restarted')
        row=self.db.console_operation(queued['id'],self.h.console._principal())
        self.assertIsNotNone(row.restart_operation_id);self.assertIsNotNone(row.receipt_hash)
        ack=self.db.restart_record(row.restart_operation_id)
        self.assertEqual(ack.state,'acked');self.assertEqual(ack.new_process.invocation,'b'*32)
        self.assertEqual(self.ops.restarts,1)
        status,duplicate=await self.request('1'*64)
        self.assertEqual(status,202);self.assertEqual(duplicate['id'],queued['id'])
        status,view=await self.get_operation(queued['id'])
        self.assertEqual(status,200);self.assertEqual(view['status'],'completed');self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],1)

    async def test_lost_http_response_retries_same_durable_id_without_second_dispatch(self):
        key='2'*64;self.ops.release=threading.Event()
        reader,writer=await self.open_post(key)
        try:
            await self.wait_until(self.ops.entered.is_set)
            writer.close();await writer.wait_closed()
        finally:self.ops.release.set()
        row=self.db.console_request(self.h.console._principal(),hashlib.sha256(key.encode()).hexdigest())
        self.assertIsNotNone(row)
        self.assertEqual((await self.h.console.wait_operation(row.id))['status'],'completed')
        status,retry=await self.request(key)
        self.assertEqual(status,202);self.assertEqual(retry['id'],row.id)
        self.assertEqual(retry['status'],'completed');self.assertEqual(self.ops.restarts,1)

    async def test_unknown_root_close_reopen_restores_without_fresh_restart_or_fake_ack(self):
        self.ops.logs={domain.CHANNELS[0]};key='3'*64
        status,queued=await self.request(key);self.assertEqual(status,202)
        result=await self.h.console.wait_operation(queued['id'])
        self.assertEqual(result['status'],'unknown')
        principal=self.h.console._principal();row=self.db.console_operation(queued['id'],principal)
        self.assertIsNotNone(row.restart_operation_id)
        self.assertEqual(self.db.restart_record(row.restart_operation_id).state,'unknown')
        original_driver=self.h.restart_agent
        await self.h.close_console();await self.h.close_onboarding()
        domain.RestartTests.reopen_sql(self)
        self.ops.logs=set(domain.CHANNELS)
        await self.start_root_services()
        self.assertIsNot(self.h.restart_agent,original_driver)
        self.assertEqual(self.h.console._principal(),principal)
        status,recovered=await self.get_operation(row.id)
        self.assertEqual(status,200);self.assertEqual(recovered['status'],'unknown')
        status,retry=await self.request(key)
        self.assertEqual(status,202);self.assertEqual(retry['id'],row.id)
        self.assertEqual(retry['status'],'unknown');self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.db.restart_record(row.restart_operation_id).state,'unknown')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],1)
