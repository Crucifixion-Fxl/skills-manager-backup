"""Scoped console restarts: actual SQL/files, fake lowlevel process observations."""
import asyncio
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd import join_effects as effects
from hostd.console import ActionResult
from hostd.store import Store,BindingRecord
try:
    from hostd import agent_operations as operations
except ImportError:
    operations=None

CHANNELS=('11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222')
KEY='3'*64
OWNER=effects.gs._signer_pubkey('2'*64)
AGENT=effects.gs._signer_pubkey(KEY)

class SQLGuard:
    def __init__(self,connection):self.connection=connection;self.thread=threading.get_ident();self.calls=[]
    def execute(self,*args,**kwargs):
        assert threading.get_ident()==self.thread,'SQLite must stay on main loop'
        self.calls.append(threading.get_ident());return self.connection.execute(*args,**kwargs)
    def __getattr__(self,key):return getattr(self.connection,key)

class LowlevelOps:
    def __init__(self,env):
        self.env=dict(env);self.pid=101;self.start=1001;self.invocation='a'*32
        self.status=('loaded','active');self.busy=[];self.calls=[];self.restarts=0
        self.ack_only=False;self.error_after_dispatch=False;self.logs=set(CHANNELS)
        self.loop_thread=threading.get_ident();self.before_idle=None;self.after_restart=None;self.before_journal=None
        self.entered=threading.Event();self.release=None
    def record(self,kind,unit,*extra):
        assert threading.get_ident()!=self.loop_thread,'Process IO must be offloaded'
        assert unit=='owned-agent.service','Caller cannot choose another unit'
        self.calls.append((kind,unit,*extra))
    def unit_status(self,unit):self.record('status',unit);return self.status
    def process(self,unit):self.record('process',unit);return self.pid,self.start,dict(self.env)
    def invocation_id(self,unit):self.record('invocation',unit);return self.invocation
    def journal_invocation(self,unit,pid,invocation):
        self.record('journal',unit,pid,invocation)
        assert (pid,invocation)==(self.pid,self.invocation),'Journal must pin current invocation'
        if self.before_journal:self.before_journal()
        return '\n'.join('subscribed to channel '+channel for channel in sorted(self.logs))
    def is_busy(self,unit):
        self.record('idle',unit)
        if self.before_idle:self.before_idle()
        return self.busy.pop(0) if self.busy else False
    def restart(self,unit):
        self.record('restart',unit);self.restarts+=1;self.entered.set()
        if self.release and not self.release.wait(5):raise RuntimeError('offline deadline')
        if not self.ack_only:self.pid,self.start,self.invocation=202,2002,'b'*32
        if self.after_restart:self.after_restart()
        if self.error_after_dispatch:raise RuntimeError('PRIVATE KEY BODY unknown outcome')

class RestartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        def file(name,value):
            p=self.root/name;p.write_text(value);p.chmod(0o600);return str(p)
        self.prompt=file('prompt',effects.legacy.PROMPT_BEGIN+'\n'+'\n'.join(CHANNELS)+'\n'+effects.legacy.PROMPT_END)
        self.responsible=file('responsible',json.dumps({'channels':list(CHANNELS)}))
        self.env={'BUZZ_PRIVATE_KEY':KEY,'BUZZ_ACP_AGENT_OWNER':OWNER,'BUZZ_ACP_CHANNELS':','.join(CHANNELS),
                  'BUZZ_ACP_SYSTEM_PROMPT_FILE':self.prompt,'BUZZ_RESPONSIBLE_CONFIG':self.responsible}
        self.env_file=file('agent.env','\n'.join(key+'='+value for key,value in self.env.items())+'\n')
        self.timer=file('timer.json',json.dumps({'version':1,'owner_pubkey':OWNER,'agents':[]}))
        self.spec=effects.AgentSpec(AGENT,OWNER,'cli_agent',self.env_file,self.prompt,self.responsible,
                                   'owned-agent.service',self.timer,str(self.root))
        self.db=Store(self.root/'sql'/'state.db');self.addCleanup(self.db.close)
        self.db.conn=SQLGuard(self.db.conn)
        self.db.register_agent(AGENT,owner_pubkey=OWNER,app_id='cli_agent',config_path=self.env_file,now=10)
        for i,channel in enumerate(CHANNELS):
            chat='oc_group'+str(i);config=file('config'+str(i),json.dumps({'channel_id':channel,'chat_id':chat,'sync_app_id':'cli_reader'}))
            self.db.reconcile_bindings([BindingRecord('binding'+str(i),channel,chat,'cli_reader',config,'/reader','/data')],now=10)
            self.db.record_agent_chat(AGENT,chat,effects.gs.chat_ref(chat),binding_id='binding'+str(i),status='active',now=10)
        self.ops=LowlevelOps(self.env)
    def driver(self):
        self.assertIsNotNone(operations,'missing scoped restart implementation is the genuine RED')
        return operations.AgentRestartDriver(self.db,lambda pub:self.spec if pub==AGENT else None,
                                             operations=self.ops,observations=2)
    def lock_available(self):
        fd=os.open(self.root/'join.lock',os.O_RDWR|os.O_CREAT,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return True
            except BlockingIOError:return False
        finally:os.close(fd)
    async def test_actual_sql_files_idle_twice_new_invocation_all_channels_before_success(self):
        driver=self.driver();before={p:Path(p).read_bytes() for p in (self.env_file,self.timer,self.prompt,self.responsible)}
        result=await driver(AGENT)
        self.assertEqual(result,ActionResult(True,'restarted'));self.assertEqual(self.ops.restarts,1)
        at=next(i for i,row in enumerate(self.ops.calls) if row[0]=='restart')
        self.assertEqual([row[0] for row in self.ops.calls[at-2:at]],['idle','idle'])
        journals=[row for row in self.ops.calls if row[0]=='journal']
        self.assertTrue(journals);self.assertTrue(all(row[-2:]==(202,'b'*32) for row in journals))
        self.assertEqual(before,{p:Path(p).read_bytes() for p in before});self.assertTrue(self.lock_available())
    async def test_ack_without_changed_identity_stays_unknown_and_never_restarts_again(self):
        driver=self.driver();self.ops.ack_only=True
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'))
        self.ops.ack_only=False;self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'))
        self.assertEqual(self.ops.restarts,1)
    async def test_waiting_verify_reuses_same_pinned_invocation_without_second_restart(self):
        driver=self.driver();self.ops.logs={CHANNELS[0]}
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'))
        self.ops.logs=set(CHANNELS)
        self.assertEqual(await driver(AGENT),ActionResult(True,'restarted'));self.assertEqual(self.ops.restarts,1)
    async def test_external_later_invocation_cannot_supply_old_operation_receipt(self):
        driver=self.driver();self.ops.logs={CHANNELS[0]};self.assertFalse((await driver(AGENT)).ok)
        self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32;self.ops.logs=set(CHANNELS)
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'));self.assertEqual(self.ops.restarts,1)
    async def test_unknown_restart_error_is_observed_not_blindly_replayed_or_logged(self):
        driver=self.driver();self.ops.error_after_dispatch=True;self.ops.logs={CHANNELS[0]}
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'))
        self.ops.logs=set(CHANNELS)
        self.assertEqual(await driver(AGENT),ActionResult(True,'restarted'));self.assertEqual(self.ops.restarts,1)
        self.assertNotIn('PRIVATE',driver.last_notice)
    async def test_busy_second_check_and_unknown_idle_never_restart(self):
        driver=self.driver()
        for busy in ([False,True],[False,None],[True]):
            with self.subTest(busy=busy):
                self.ops.busy=list(busy)
                self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_idle'))
                self.assertEqual(self.ops.restarts,0)
    async def test_unknown_retired_unapproved_wrong_sql_owner_app_and_config_fail_closed(self):
        driver=self.driver()
        for column,value in (('status','retired'),('owner_pubkey','f'*64),('app_id','cli_other'),('config_path','/unowned.env')):
            with self.subTest(column=column):
                original=self.db.conn.execute('SELECT '+column+' FROM agent WHERE pubkey=?',(AGENT,)).fetchone()[0]
                self.db.conn.execute('UPDATE agent SET '+column+'=? WHERE pubkey=?',(value,AGENT))
                self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
                self.db.conn.execute('UPDATE agent SET '+column+'=? WHERE pubkey=?',(original,AGENT))
        self.assertFalse((await driver('owned-agent.service')).ok);self.assertFalse((await driver('f'*64)).ok)
        self.db.conn.execute("UPDATE agent_chat SET status='retired'")
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
    async def test_canonical_grants_and_binding_config_identity_required(self):
        driver=self.driver()
        self.db.conn.execute("UPDATE agent_chat SET chat_ref=?",('f'*64,))
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
        for i in range(2):self.db.conn.execute('UPDATE agent_chat SET chat_ref=? WHERE chat_id=?',(effects.gs.chat_ref('oc_group'+str(i)),'oc_group'+str(i)))
        config=self.root/'config0';config.write_text(json.dumps({'channel_id':CHANNELS[1],'chat_id':'oc_group0','sync_app_id':'cli_reader'}))
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
    async def test_ungranted_env_channel_wrong_key_owner_and_unsafe_files_do_not_restart(self):
        driver=self.driver()
        for change in ({'BUZZ_ACP_CHANNELS':','.join(CHANNELS+('33333333-3333-4333-8333-333333333333',))},
                       {'BUZZ_PRIVATE_KEY':'4'*64},{'BUZZ_ACP_AGENT_OWNER':'f'*64}):
            with self.subTest(change=list(change)):
                env=dict(self.env,**change);Path(self.env_file).write_text('\n'.join(k+'='+v for k,v in env.items()))
                self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
        Path(self.env_file).write_text('\n'.join(k+'='+v for k,v in self.env.items()));Path(self.env_file).chmod(0o644)
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
    async def test_old_join_exclusion_and_mutual_lock_required(self):
        driver=self.driver();Path(self.timer).write_text(json.dumps({'version':1,'owner_pubkey':OWNER,'agents':[{'env_file':self.env_file}]}))
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
        Path(self.timer).write_text(json.dumps({'version':1,'owner_pubkey':OWNER,'agents':[]}))
        fd=os.open(self.root/'join.lock',os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
        finally:os.close(fd)
    async def test_old_process_unknown_or_wrong_identity_cannot_dispatch(self):
        driver=self.driver()
        for change in ('status','pid','invocation','key'):
            with self.subTest(change=change):
                old=(self.ops.status,self.ops.pid,self.ops.invocation,dict(self.ops.env))
                if change=='status':self.ops.status=('loaded','inactive')
                elif change=='pid':self.ops.pid=0
                elif change=='invocation':self.ops.invocation='not-invocation'
                else:self.ops.env['BUZZ_PRIVATE_KEY']='4'*64
                self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
                self.ops.status,self.ops.pid,self.ops.invocation,self.ops.env=old
    async def test_config_or_sql_change_during_io_preflight_blocks_dispatch(self):
        driver=self.driver();entered=threading.Event();release=threading.Event()
        def blocked():entered.set();release.wait(5)
        self.ops.before_idle=blocked
        task=asyncio.create_task(driver(AGENT))
        await asyncio.to_thread(entered.wait,5)
        self.db.conn.execute("UPDATE agent_chat SET status='retired'")
        release.set();self.assertFalse((await task).ok);self.assertEqual(self.ops.restarts,0)
    async def test_sql_revocation_during_final_dispatch_idle_never_restarts(self):
        driver=self.driver();entered=threading.Event();release=threading.Event()
        def final_idle():
            if sum(row[0]=='idle' for row in self.ops.calls)==3:
                entered.set()
                if not release.wait(5):raise RuntimeError('offline gate deadline')
        self.ops.before_idle=final_idle
        task=asyncio.create_task(driver(AGENT))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,5))
            # The real SQLite grant is revoked on its owning event loop while
            # the final worker-thread idle observation is blocked.
            self.db.conn.execute("UPDATE agent_chat SET status='retired'")
        finally:release.set()
        result=await task
        self.assertFalse(result.ok)
        self.assertEqual(self.ops.restarts,0,'revoked SQL scope cannot dispatch after the idle wait')
        self.assertNotIn(AGENT,driver._pending)
        self.assertTrue(self.lock_available())

    async def test_cancel_during_final_idle_denies_mainloop_handoff_without_restart(self):
        driver=self.driver();entered=threading.Event();release=threading.Event()
        def final_idle():
            if sum(row[0]=='idle' for row in self.ops.calls)==3:
                entered.set();release.wait(5)
        self.ops.before_idle=final_idle
        task=asyncio.create_task(driver(AGENT))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,5))
            task.cancel();await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertFalse(self.lock_available())
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.ops.restarts,0)
        self.assertNotIn(AGENT,driver._pending);self.assertTrue(self.lock_available())

    async def test_withheld_mainloop_authorization_times_out_without_pending_or_restart(self):
        from unittest import mock
        driver=self.driver();driver.authorization_timeout=.03
        loop=asyncio.get_running_loop();original=loop.call_soon_threadsafe;held=[]
        def handoff(callback,*args,**kwargs):
            if getattr(callback,'__name__','')=='_admit':
                held.append(callback)
                return None
            return original(callback,*args,**kwargs)
        with mock.patch.object(loop,'call_soon_threadsafe',handoff):
            result=await asyncio.wait_for(driver(AGENT),1)
        self.assertFalse(result.ok);self.assertEqual(self.ops.restarts,0)
        self.assertEqual(len(held),1);self.assertNotIn(AGENT,driver._pending)
        # A delayed callback after the bounded waiter failed cannot authorize.
        held[0]();self.assertEqual(self.ops.restarts,0);self.assertTrue(self.lock_available())

    async def test_file_change_after_first_idle_preflight_blocks_restart(self):
        driver=self.driver()
        def changed():
            Path(self.env_file).write_bytes(Path(self.env_file).read_bytes()+b'\n')
            self.ops.before_idle=None
        self.ops.before_idle=changed
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
    async def test_old_invocation_changed_after_first_idle_cannot_be_restarted_as_original(self):
        driver=self.driver()
        def changed():
            self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32
            self.ops.before_idle=None
        self.ops.before_idle=changed
        self.assertFalse((await driver(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
    async def test_cancel_repeatedly_reaps_dispatched_io_and_retains_shared_lock(self):
        driver=self.driver();self.ops.release=threading.Event()
        task=asyncio.create_task(driver(AGENT))
        self.assertTrue(await asyncio.to_thread(self.ops.entered.wait,5))
        task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
        self.assertFalse(task.done());self.assertFalse(self.lock_available())
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'));self.assertEqual(self.ops.restarts,1)
        self.ops.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.lock_available())
        self.assertEqual(await driver(AGENT),ActionResult(True,'restarted'));self.assertEqual(self.ops.restarts,1)
    async def test_cancel_during_lock_cleanup_preserves_verified_unreturned_restart(self):
        from unittest import mock
        driver=self.driver();entered=threading.Event();release=threading.Event()
        original=operations._OwnedLock.close
        def blocked(lock):
            entered.set();release.wait(5);original(lock)
        with mock.patch.object(operations._OwnedLock,'close',blocked):
            task=asyncio.create_task(driver(AGENT))
            self.assertTrue(await asyncio.to_thread(entered.wait,5))
            task.cancel();await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertFalse(self.lock_available())
            release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(await driver(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,1)
    async def test_busy_after_durable_reservation_handshake_never_dispatches(self):
        driver=self.driver();original=self.db.mark_restart_unknown
        def changed(*args,**kwargs):
            answer=original(*args,**kwargs)
            self.ops.busy=[True]
            return answer
        self.db.mark_restart_unknown=changed
        result=await driver(AGENT)
        self.assertFalse(result.ok);self.assertEqual(self.ops.restarts,0)
        record=self.db.active_restart(AGENT)
        self.assertIsNotNone(record);self.assertEqual(record.state,'unknown')
        self.assertIsNone(record.new_process)

    async def test_physical_change_during_reaped_cleanup_cannot_ack_old_proof(self):
        from unittest import mock
        entered=threading.Event();release=threading.Event();original=operations._OwnedLock.close
        def blocked(lock):
            entered.set();release.wait(5);original(lock)
        with mock.patch.object(operations._OwnedLock,'close',blocked):
            task=asyncio.create_task(self.driver()(AGENT))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,5))
                Path(self.prompt).write_bytes(Path(self.prompt).read_bytes()+b'\nchanged-after-readback\n')
            finally:release.set()
            result=await task
        self.assertFalse(result.ok);self.assertEqual(self.ops.restarts,1)
        record=self.db.active_restart(AGENT)
        self.assertIsNotNone(record);self.assertEqual(record.state,'unknown')
        self.assertIsNotNone(record.new_process)

    def final_io_gate(self):
        from unittest import mock
        entered=threading.Event();release=threading.Event();original=operations._OwnedLock.close
        def journal():
            entered.set()
            if not release.wait(5):raise RuntimeError('offline final IO deadline')
        def arm(lock):
            original(lock)
            self.ops.before_journal=journal
        return entered,release,mock.patch.object(operations._OwnedLock,'close',arm)

    async def test_process_changes_during_post_cleanup_final_io_cannot_ack(self):
        entered,release,patch=self.final_io_gate()
        with patch:
            task=asyncio.create_task(self.driver()(AGENT))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2),'missing post-cleanup physical proof')
                self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32
            finally:release.set()
            result=await task
        self.assertFalse(result.ok);self.assertEqual(self.ops.restarts,1)
        record=self.db.active_restart(AGENT);self.assertEqual(record.state,'unknown')
        self.assertEqual(record.new_process.invocation,'b'*32)

    async def test_sql_revocation_during_post_cleanup_final_io_cannot_ack(self):
        entered,release,patch=self.final_io_gate()
        with patch:
            task=asyncio.create_task(self.driver()(AGENT))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2),'missing post-cleanup physical proof')
                self.db.conn.execute("UPDATE agent_chat SET status='retired'")
            finally:release.set()
            result=await task
        self.assertFalse(result.ok);self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.db.active_restart(AGENT).state,'unknown')

    async def test_repeated_cancel_during_post_cleanup_final_io_reaps_and_preserves_pin(self):
        driver=self.driver();entered,release,patch=self.final_io_gate()
        with patch:
            task=asyncio.create_task(driver(AGENT))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2),'missing post-cleanup physical proof')
                task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done());self.assertTrue(self.lock_available())
                self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'))
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.db.active_restart(AGENT).state,'unknown')
        self.ops.before_journal=None;self.reopen_sql()
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,1)

    def reopen_sql(self):
        self.db.close()
        self.db=Store(self.root/'sql'/'state.db');self.addCleanup(self.db.close)
        self.db.conn=SQLGuard(self.db.conn)

    async def test_reopened_unpinned_unknown_never_restarts_or_adopts_later_invocation(self):
        self.ops.ack_only=True
        self.assertEqual(await self.driver()(AGENT),ActionResult(False,'waiting_verify'))
        self.reopen_sql();self.ops.ack_only=False
        self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32
        self.assertEqual(await self.driver()(AGENT),ActionResult(False,'waiting_verify'))
        self.assertEqual(self.ops.restarts,1)

    async def test_reopened_pinned_unknown_verifies_same_tuple_without_another_restart(self):
        self.ops.logs={CHANNELS[0]}
        self.assertEqual(await self.driver()(AGENT),ActionResult(False,'waiting_verify'))
        self.reopen_sql();self.ops.logs=set(CHANNELS)
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,1)

    async def test_reopened_cleanup_cancel_preserves_unreturned_pinned_receipt(self):
        from unittest import mock
        entered=threading.Event();release=threading.Event();original=operations._OwnedLock.close
        def blocked(lock):
            entered.set();release.wait(5);original(lock)
        with mock.patch.object(operations._OwnedLock,'close',blocked):
            task=asyncio.create_task(self.driver()(AGENT))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,5))
                task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done());self.assertFalse(self.lock_available())
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.reopen_sql()
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,1)

    async def test_reopened_dispatch_cancel_retains_first_changed_invocation_pin(self):
        self.ops.release=threading.Event();task=asyncio.create_task(self.driver()(AGENT))
        try:
            self.assertTrue(await asyncio.to_thread(self.ops.entered.wait,5))
            task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertFalse(self.lock_available())
        finally:self.ops.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.reopen_sql()
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,1)

    async def test_reopened_files_fingerprint_change_holds_pending_without_restart(self):
        self.ops.logs={CHANNELS[0]}
        self.assertEqual(await self.driver()(AGENT),ActionResult(False,'waiting_verify'))
        self.reopen_sql();self.ops.logs=set(CHANNELS)
        Path(self.prompt).write_bytes(Path(self.prompt).read_bytes()+b'\n')
        self.assertFalse((await self.driver()(AGENT)).ok);self.assertEqual(self.ops.restarts,1)

    async def test_reopened_exact_spec_fingerprint_change_holds_pending_without_restart(self):
        self.ops.logs={CHANNELS[0]}
        self.assertEqual(await self.driver()(AGENT),ActionResult(False,'waiting_verify'))
        self.reopen_sql();self.ops.logs=set(CHANNELS)
        self.spec=replace(self.spec,reader_app_id='cli_different_reader')
        self.assertFalse((await self.driver()(AGENT)).ok);self.assertEqual(self.ops.restarts,1)

    async def test_reopened_reserved_before_dispatch_crash_never_replays(self):
        from hostd import store as ledger
        self.db.reserve_restart('RESTART-RESERVED',AGENT,'a'*64,'b'*64,
                                ledger.RestartProcess(101,1001,'a'*32),now=100)
        self.reopen_sql()
        self.assertFalse((await self.driver()(AGENT)).ok);self.assertEqual(self.ops.restarts,0)
        self.assertEqual(self.db.active_restart(AGENT).state,'reserved')

    async def test_completed_receipt_allows_later_explicit_new_restart_intent(self):
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.reopen_sql()
        def changed():self.ops.pid,self.ops.start,self.ops.invocation=303,3003,'c'*32
        self.ops.after_restart=changed
        self.assertEqual(await self.driver()(AGENT),ActionResult(True,'restarted'))
        self.assertEqual(self.ops.restarts,2)

    async def test_postrestart_wrong_environment_cannot_claim_success(self):
        driver=self.driver();self.ops.after_restart=lambda:self.ops.env.update(BUZZ_ACP_CHANNELS=CHANNELS[0])
        self.assertEqual(await driver(AGENT),ActionResult(False,'waiting_verify'));self.assertEqual(self.ops.restarts,1)
        self.assertIn('怎么解决',driver.last_notice);self.assertIn('复制给 AI',driver.last_notice)
        self.assertNotIn(KEY,driver.last_notice)

class ScopedAdapter(unittest.TestCase):
    def test_failed_systemctl_show_cannot_prove_loaded_active_from_partial_stdout(self):
        self.assertIsNotNone(operations)
        def runner(argv,**kwargs):return subprocess.CompletedProcess(argv,1,'LoadState=loaded\nActiveState=active\n','PRIVATE')
        ops=operations.ScopedProcessOps(runner=runner,base_env={})
        self.assertEqual(ops.unit_status('owned-agent.service'),('unknown','unknown'))
    def test_actual_processops_invocation_and_journal_filters_with_bounded_fake_runner(self):
        self.assertIsNotNone(operations,'missing real ProcessOps adapter is the genuine RED')
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs));self.assertLessEqual(kwargs['timeout'],10)
            return subprocess.CompletedProcess(argv,0,'d'*32+'\n' if argv[0].endswith('systemctl') else 'subscribed to channel '+CHANNELS[0],'')
        ops=operations.ScopedProcessOps(runner=runner,base_env={})
        self.assertEqual(ops.invocation_id('owned-agent.service'),'d'*32)
        self.assertIn(CHANNELS[0],ops.journal_invocation('owned-agent.service',202,'d'*32))
        self.assertIn('InvocationID',calls[0][0]);self.assertIn('_PID=202',calls[1][0]);self.assertIn('_SYSTEMD_INVOCATION_ID='+'d'*32,calls[1][0])
        self.assertFalse(any('restart' in call[0] for call in calls))

if __name__=='__main__':unittest.main()
