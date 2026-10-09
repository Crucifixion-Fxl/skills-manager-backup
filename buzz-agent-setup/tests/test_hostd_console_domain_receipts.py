"""Actual Hostd handlers attach operation-specific durable receipts."""
import asyncio
import hashlib
import json
import secrets
import threading
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from hostd.store import Store
import test_hostd_console_wiring as fixture

class DomainReceipts(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ConsoleWiring.asyncSetUp
    asyncTearDown=fixture.ConsoleWiring.asyncTearDown
    persisted=fixture.ConsoleWiring.persisted

    async def request(self,operation,key):
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        head='POST /api/bindings/alpha/'+operation+' HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer '+self.token+'\r\nOrigin: http://hostd.local\r\nX-Hostd-Request: 1\r\nIdempotency-Key: '+key+'\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n'
        writer.write(head.encode()+b'{}');await writer.drain();raw=await reader.read()
        writer.close();await writer.wait_closed()
        return int(raw.split(b' ',2)[1]),json.loads(raw.split(b'\r\n\r\n',1)[1])

    async def test_actual_pause_status_and_operation_receipt_are_committed_together(self):
        key=secrets.token_hex(32);status,accepted=await self.request('pause',key)
        self.assertEqual(status,202);result=await self.h.console.wait_operation(accepted['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(self.persisted(),'paused')
        with Store(self.h.store_path) as db:
            row=db.console_request(self.h.console._principal(),hashlib.sha256(key.encode()).hexdigest())
            self.assertEqual(row.status,'completed');self.assertRegex(row.receipt_hash,r'^[0-9a-f]{64}$')
        status,repeated=await self.request('pause',key)
        self.assertEqual(status,202);self.assertEqual(repeated['id'],accepted['id'])

    async def test_actual_successful_backfill_has_its_own_receipt_after_round(self):
        calls=[]
        def run(dirty,**kwargs):
            calls.append(set(dirty));return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        key=secrets.token_hex(32);status,accepted=await self.request('backfill',key)
        self.assertEqual(status,202);result=await self.h.console.wait_operation(accepted['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(len(calls),1)
        self.assertEqual(calls[0],{'members','buzz','feishu'})
        status,repeated=await self.request('backfill',key)
        self.assertEqual(status,202);self.assertEqual(repeated['id'],accepted['id']);self.assertEqual(len(calls),1)

    async def test_failed_round_cannot_mint_receipt_from_active_binding_status(self):
        calls=[]
        def run(*args,**kwargs):calls.append(True);return {'errors':1,'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        status,accepted=await self.request('backfill',secrets.token_hex(32));self.assertEqual(status,202)
        result=await self.h.console.wait_operation(accepted['id']);self.assertEqual(result['status'],'unknown')
        with Store(self.h.store_path) as db:
            row=db.console_operation(accepted['id'],self.h.console._principal());self.assertIsNone(row.receipt_hash)
        self.assertEqual(calls,[True])

    async def test_retired_while_waiting_for_binding_lock_cannot_be_resurrected(self):
        lock=self.h.binding_locks['alpha'];await lock.acquire()
        try:
            _,accepted=await self.request('pause',secrets.token_hex(32))
            for _ in range(100):
                with Store(self.h.store_path) as db:
                    row=db.console_operation(accepted['id'],self.h.console._principal())
                if row.status=='dispatched':break
                await asyncio.sleep(.005)
            self.assertEqual(row.status,'dispatched')
            with Store(self.h.store_path) as db:
                with db.transaction():db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id='alpha'")
        finally:lock.release()
        result=await self.h.console.wait_operation(accepted['id'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(self.persisted(),'retired')

    async def test_retired_during_round_cannot_be_resurrected_or_acknowledged(self):
        entered=threading.Event();release=threading.Event()
        def run(*args,**kwargs):entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        _,accepted=await self.request('backfill',secrets.token_hex(32))
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            with Store(self.h.store_path) as db:
                with db.transaction():db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id='alpha'")
        finally:release.set()
        result=await self.h.console.wait_operation(accepted['id'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(self.persisted(),'retired')
        with Store(self.h.store_path) as db:
            self.assertIsNone(db.console_operation(accepted['id'],self.h.console._principal()).receipt_hash)

    async def test_historical_pause_receipt_keeps_current_readback_status(self):
        _,accepted=await self.request('pause',secrets.token_hex(32))
        result=await self.h.console.wait_operation(accepted['id']);self.assertEqual(result['status'],'completed')
        self.assertTrue(self.h._set_binding_state('alpha','active'))
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        writer.write(('GET /api/operations/'+accepted['id']+' HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer '+self.token+'\r\n\r\n').encode())
        await writer.drain();raw=await reader.read();writer.close();await writer.wait_closed()
        self.assertEqual(int(raw.split(b' ',2)[1]),200)
        result=json.loads(raw.split(b'\r\n\r\n',1)[1])
        self.assertEqual(result['status'],'completed');self.assertEqual(result['readback']['status'],'active')
        self.assertEqual(result['observed'],'paused')

    async def test_terminal_task_cache_is_bounded_while_request_keys_remain_durable(self):
        self.h.console.max_operations=2;first_key=secrets.token_hex(32);first_id=None
        for index in range(4):
            key=first_key if index==0 else secrets.token_hex(32)
            code,accepted=await self.request('pause',key);self.assertEqual(code,202)
            if index==0:first_id=accepted['id']
            result=await self.h.console.wait_operation(accepted['id']);self.assertEqual(result['status'],'completed')
            await asyncio.sleep(.01)
        self.assertLessEqual(len(self.h.console._operation_tasks),2)
        with Store(self.h.store_path) as db:
            self.assertEqual(db.conn.execute('SELECT count(*) FROM console_operation').fetchone()[0],4)
        code,retried=await self.request('pause',first_key)
        self.assertEqual(code,202);self.assertEqual(retried['id'],first_id)

if __name__=='__main__':unittest.main()
