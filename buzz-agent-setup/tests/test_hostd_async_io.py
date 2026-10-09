"""Actual bot send/identity cancellation; only transport and time are offline."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.bot_clients import BotLarkCli
from hostd.join_cards import BotCards
from hostd.onboarding import Coordinator,IdentityResolver,Operator
from hostd.store import Store

class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.config=self.root/'config';self.data=self.root/'data';self.config.mkdir();self.data.mkdir()
        path=self.config/'config.json';path.write_text(json.dumps({'apps':[{'appId':'cli_agent','name':'agent'}]}));path.chmod(0o600)
        self.entered=threading.Event();self.release=threading.Event();self.finished=threading.Event();self.calls=[]
        self.db=Store(self.root/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.db.register_agent('a'*64,owner_pubkey='b'*64,app_id='cli_agent',now=100)
        self.row=self.db.create_join('JOIN-12345678','a'*64,'b'*64,'cli_agent','oc_group',kind='new_binding',now=100)
        self.client=BotLarkCli('cli_agent',self.config,self.data,base_env={},runner=self.runner)

    def runner(self,argv,**kwargs):
        self.calls.append(argv)
        self.assertEqual(argv[argv.index('--as')+1],'bot')
        self.assertEqual(argv[argv.index('--profile')+1],'agent')
        self.entered.set()
        try:
            if not self.release.wait(5):raise AssertionError('offline transport was not released')
            if '/open-apis/contact/v3/users/ou_owner' in argv:
                data={'user':{'open_id':'ou_owner','union_id':'on_owner'}}
            else:data={'message_id':'om_sent'}
            return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data}),'')
        finally:self.finished.set()

    async def cancel_while_dispatched(self,task):
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait,2))
            task.cancel();await asyncio.sleep(0);await asyncio.sleep(0)
            self.assertFalse(task.done(),'cancel propagated while actual bot IO was still running')
            self.assertIsNotNone(self.db.conn)
            task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done(),'a second cancel must still wait for dispatched IO')
        finally:
            self.release.set()
            await asyncio.gather(task,return_exceptions=True)
            await asyncio.to_thread(self.finished.wait,2)
        self.assertTrue(task.cancelled());self.assertTrue(self.finished.is_set())
        self.assertEqual(len(self.calls),1)

    async def test_card_cancel_reaps_actual_send_and_preserves_reservation_without_ack(self):
        coordinator=Coordinator(self.db,None,BotCards({'cli_agent':self.client}),clock=lambda:100)
        await self.cancel_while_dispatched(asyncio.create_task(coordinator._send(self.row,100)))
        self.assertEqual(self.db.join_request(self.row['request_id'])['status'],'requested')
        self.assertIsNone(self.db.join_request(self.row['request_id'])['card_message_id'])
        self.assertEqual(self.db.join_transport(self.row['request_id'])['send_status'],'reserved')

    async def test_identity_cancel_reaps_actual_app_contact_read(self):
        resolver=IdentityResolver({'cli_agent':self.client},None,None)
        await self.cancel_while_dispatched(asyncio.create_task(resolver.inviter('cli_agent',Operator('ou_owner'),now=100)))

    async def test_helper_reaps_io_error_and_propagates_cancel_without_new_attempt(self):
        from hostd.async_io import thread_call
        def io():
            self.entered.set()
            try:
                if not self.release.wait(5):raise AssertionError('offline transport was not released')
                raise OSError('offline IO failed')
            finally:self.finished.set()
        task=asyncio.create_task(thread_call(io))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait,2))
            task.cancel();await asyncio.sleep(0);await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            self.release.set();await asyncio.gather(task,return_exceptions=True)
        self.assertTrue(task.cancelled());self.assertTrue(self.finished.is_set())

    async def test_helper_normal_result_and_failure_keep_existing_contract(self):
        from hostd.async_io import thread_call
        self.assertEqual(await thread_call(lambda x,scale=1:x*scale,3,scale=2),6)
        with self.assertRaises(OSError):await thread_call(lambda:(_ for _ in ()).throw(OSError('offline error')))

if __name__=='__main__':unittest.main()
