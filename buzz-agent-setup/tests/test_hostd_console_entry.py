"""CLI orchestration over actual accepted transports, synthetic process boundaries."""
import asyncio
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_hostd_console_ssh as ssh_fixture
import test_hostd_console_access as browser_fixture
from hostd import console_access as access,console_browser as browser,console_ssh as ssh
from hostd.console import ConsoleServer

class EntryTests(unittest.IsolatedAsyncioTestCase):
    module=ssh_fixture.ConsoleSSHTests.module
    digest=staticmethod(ssh_fixture.ConsoleSSHTests.digest)
    plan=ssh_fixture.ConsoleSSHTests.plan
    async def asyncSetUp(self):
        await ssh_fixture.ConsoleSSHTests.asyncSetUp(self)
        ui_module=importlib.import_module(ConsoleServer.__module__)
        ui=self.root/'ui.html';ui.write_bytes(ui_module.UI_PATH.read_bytes());ui.chmod(0o600)
        ui_patch=mock.patch.object(ui_module,'UI_PATH',ui);ui_patch.start();self.addCleanup(ui_patch.stop)
        self.chrome=self.root/'chrome';shutil.copyfile('/usr/bin/true',self.chrome);self.chrome.chmod(0o700)
        self.bops=browser_fixture.BrowserOps();self.bops.sha=self.digest(self.chrome)
        self.document=dict(version=1,ssh_binary=str(self.ssh),ssh_sha256=self.digest(self.ssh),host='test.invalid',user='testuser',
            remote_uid=os.geteuid(),remote_python='/usr/bin/python3.12',remote_runtime=str(self.server.runtime_dir),
            known_hosts=str(self.known),known_hosts_sha256=self.digest(self.known),identity_file=str(self.identity),
            identity_sha256=self.digest(self.identity),forward_dir=str(self.forward))
        self.plan_path=self.root/'ssh-plan.json';self.write_plan()
        self.output=io.StringIO();self.order=[];self.statuses=[];self.handlers={};self.registered=asyncio.Event();self.auto_stop=True
        self.target_access=None;self.target_tunnel=None;self.target_browser=None
        self.add_signal=asyncio.get_running_loop().add_signal_handler
    async def asyncTearDown(self):
        await ssh_fixture.ConsoleSSHTests.asyncTearDown(self)
    def write_plan(self,document=None):
        self.plan_path.write_text(json.dumps(self.document if document is None else document));self.plan_path.chmod(0o600)
    def args(self,*,local=False):
        return SimpleNamespace(runtime_dir=str(self.server.runtime_dir) if local else None,ssh_plan=None if local else str(self.plan_path),
            client_dir=str(self.client),chrome_binary=str(self.chrome),chrome_sha256=self.digest(self.chrome),display=None,xauthority=None)
    def safe(self,error):
        text=str(error);self.assertIn('怎么解决',text);self.assertIn('复制给 AI',text)
        self.assertNotIn(self.token,text);self.assertNotIn('Traceback',text)
    def signals(self,sig,callback,*args):
        self.handlers[sig]=(callback,args)
        if len(self.handlers)==2:
            self.registered.set()
            if self.auto_stop:asyncio.get_running_loop().call_soon(callback,*args)
    async def run_entry(self,*,local=False):
        original_command=self.bops.command
        async def command(method,params,**kw):
            if method=='Page.navigate':
                url=params['url'];self.assertRegex(url,r'^http://127\.0\.0\.1:[1-9][0-9]*')
                host=url.split('/')[2];reader,writer=await asyncio.open_connection('127.0.0.1',int(host.split(':')[1]))
                try:
                    writer.write(('GET / HTTP/1.1\r\nHost: '+host+'\r\nAuthorization: Bearer '+self.token+'\r\nConnection: close\r\n\r\n').encode());await writer.drain()
                    raw=await asyncio.wait_for(reader.read(),2);head,body=raw.split(b'\r\n\r\n',1)
                    self.statuses.append(int(head.split()[1]));self.assertNotIn(self.token.encode(),head+body)
                finally:writer.close();await writer.wait_closed()
            return await original_command(method,params,**kw)
        self.bops.command=command
        originals={cls:cls.close for cls in (browser.OwnedBrowser,access.ConsoleAccess,ssh.OwnedSSHTunnel)}
        def closer(cls,label):
            async def close(obj):
                self.order.append(label)
                if label=='browser':self.target_browser=obj
                if label=='access':self.target_access=obj
                if label=='tunnel':self.target_tunnel=obj
                return await originals[cls](obj)
            return close
        bops=self.bops
        class BrowserBoundary(browser.NativeBrowserOps):
            def __new__(cls):return bops
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ssh,'NativeSSHProcessOps',return_value=self.ops))
            stack.enter_context(mock.patch.object(browser,'NativeBrowserOps',BrowserBoundary))
            stack.enter_context(mock.patch.object(asyncio.get_running_loop(),'add_signal_handler',side_effect=self.signals))
            for cls,label in ((browser.OwnedBrowser,'browser'),(access.ConsoleAccess,'access'),(ssh.OwnedSSHTunnel,'tunnel')):
                stack.enter_context(mock.patch.object(cls,'close',closer(cls,label)))
            stack.enter_context(contextlib.redirect_stdout(self.output))
            await access._entry(self.args(local=local))
    async def test_remote_actual_components_page_and_reverse_cleanup(self):
        await self.run_entry()
        self.assertEqual(self.statuses,[200]);self.assertEqual(self.order,['browser','access','tunnel'])
        self.assertTrue(self.target_browser.readback()['reaped']);self.assertTrue(self.target_tunnel.readback()['reaped'])
        self.assertIsNone(self.target_access._server);self.assertFalse((self.forward/'console.sock').exists())
        receipt=json.loads(self.output.getvalue());self.assertEqual(receipt['status'],'connected');self.assertFalse(receipt['live_verified'])
        self.assertEqual(receipt['remote_ssh'],'connected');self.assertNotIn(self.token,self.output.getvalue())
        self.assertFalse((self.forward/'console.token').exists())
    async def test_local_actual_components_continue_without_ssh(self):
        await self.run_entry(local=True)
        self.assertEqual(self.statuses,[200]);self.assertEqual(self.order,['browser','access']);self.assertEqual(self.ops.calls,[])
        self.assertFalse(json.loads(self.output.getvalue())['live_verified'])
    async def test_remote_graph_failure_never_starts_browser(self):
        self.ops.wrong_backend=True
        with self.assertRaises(ssh.SSHError):await self.run_entry()
        self.assertEqual(self.bops.calls,[]);self.assertEqual(self.output.getvalue(),'');self.assertTrue(self.ops.reaped)
        self.assertEqual(self.order,['tunnel'])
    async def test_browser_failure_reaps_browser_proxy_and_tunnel(self):
        self.bops.fail_worker=True
        with self.assertRaises(browser.BrowserError):await self.run_entry()
        self.assertEqual(self.order,['browser','access','tunnel']);self.assertTrue(self.ops.reaped)
        self.assertEqual(self.output.getvalue(),'');self.assertFalse((self.forward/'console.sock').exists())
    async def test_cancel_after_start_reverse_cleanup_joins_owned_io(self):
        self.auto_stop=False;task=asyncio.create_task(self.run_entry())
        try:
            ready=asyncio.create_task(self.registered.wait())
            try:
                await asyncio.wait_for(asyncio.wait((task,ready),return_when=asyncio.FIRST_COMPLETED),3)
                if task.done():await task
            finally:
                ready.cancel();await asyncio.gather(ready,return_exceptions=True)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(self.order,['browser','access','tunnel']);self.assertTrue(self.ops.reaped)
            self.assertFalse((self.forward/'console.sock').exists())
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    async def test_signal_callbacks_removed_after_owned_entry(self):
        removed=[];loop=asyncio.get_running_loop()
        with mock.patch.object(loop,'remove_signal_handler',side_effect=lambda sig:removed.append(sig) or True):await self.run_entry(local=True)
        self.assertEqual(set(self.handlers),{signal.SIGINT,signal.SIGTERM});self.assertEqual(set(removed),set(self.handlers))
    async def wait_until(self,predicate,timeout=.4):
        deadline=asyncio.get_running_loop().time()+timeout
        while not predicate() and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.005)
        return predicate()
    async def end_entry(self,task):
        if not task.done() and signal.SIGTERM in self.handlers:
            callback,args=self.handlers[signal.SIGTERM];callback(*args)
        if not task.done():
            try:await asyncio.wait_for(task,2)
            except (asyncio.CancelledError,Exception):pass
        else:await asyncio.gather(task,return_exceptions=True)
    async def test_remote_periodic_actual_same_tuple_refresh_no_new_process(self):
        self.auto_stop=False
        with mock.patch.object(access,'SSH_REFRESH_INTERVAL',.01,create=True):
            task=asyncio.create_task(self.run_entry())
            try:
                await asyncio.wait_for(self.registered.wait(),2)
                refreshed=await self.wait_until(lambda:sum(call[0]=='helper' for call in self.ops.calls)>=2)
                self.assertTrue(refreshed)
            finally:await self.end_entry(task)
        self.assertEqual(sum(call[0]=='spawn' for call in self.ops.calls),1)
        self.assertEqual(self.order,['browser','access','tunnel']);self.assertTrue(self.ops.reaped)
        self.assertEqual(self.statuses,[200]);self.assertNotIn(self.token,self.output.getvalue())
    async def test_remote_periodic_real_token_rotation_stops_old_owned_session(self):
        self.auto_stop=False
        with mock.patch.object(access,'SSH_REFRESH_INTERVAL',.01,create=True):
            task=asyncio.create_task(self.run_entry())
            try:
                await asyncio.wait_for(self.registered.wait(),2)
                self.server.token_path.write_text('x'*43+'\n')
                stopped=await self.wait_until(task.done)
                self.assertTrue(stopped)
                with self.assertRaises(ssh.SSHError):await task
            finally:await self.end_entry(task)
        self.assertEqual(sum(call[0]=='spawn' for call in self.ops.calls),1)
        self.assertEqual(self.order,['browser','access','tunnel']);self.assertTrue(self.ops.reaped)
        self.assertFalse((self.forward/'console.sock').exists());self.assertNotIn('x'*43,self.output.getvalue())
    async def test_cancel_periodic_helper_joins_before_reverse_cleanup(self):
        self.auto_stop=False;entered=asyncio.Event();release=threading.Event();original=self.ops.helper;calls=0
        loop=asyncio.get_running_loop()
        def helper(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(2):raise AssertionError('synthetic helper fence expired')
            return original(*args,**kwargs)
        self.ops.helper=helper
        with mock.patch.object(access,'SSH_REFRESH_INTERVAL',.01,create=True):
            task=asyncio.create_task(self.run_entry())
            try:
                await asyncio.wait_for(self.registered.wait(),2)
                self.assertTrue(await self.wait_until(entered.is_set))
                task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done());self.assertFalse(self.ops.reaped)
                release.set()
                with self.assertRaises(asyncio.CancelledError):await task
            finally:
                release.set();await self.end_entry(task)
        self.assertEqual(self.order,['browser','access','tunnel']);self.assertTrue(self.ops.reaped)
        self.assertFalse((self.forward/'console.sock').exists())

    async def test_load_plan_uses_actual_private_pins_no_creation(self):
        before=tuple(self.forward.iterdir());plan=access.load_ssh_plan(self.plan_path)
        self.assertIsInstance(plan,ssh.SSHPlan);self.assertEqual(plan.remote_uid,os.geteuid());self.assertEqual(tuple(self.forward.iterdir()),before)
        self.assertNotIn(self.token,repr(plan));self.assertEqual(self.ops.calls,[])
    async def test_plan_symlink_permissions_hardlink_and_bounds_rejected(self):
        self.plan_path.chmod(0o644)
        with self.assertRaises(access.AccessError) as error:access.load_ssh_plan(self.plan_path)
        self.safe(error.exception);self.plan_path.chmod(0o600)
        alias=self.root/'alias';alias.symlink_to(self.plan_path)
        with self.assertRaises(access.AccessError):access.load_ssh_plan(alias)
        hard=self.root/'hard';os.link(self.plan_path,hard)
        with self.assertRaises(access.AccessError):access.load_ssh_plan(self.plan_path)
        hard.unlink();self.plan_path.write_bytes(b' '*16385)
        with self.assertRaises(access.AccessError):access.load_ssh_plan(self.plan_path)
    async def test_plan_exact_shape_duplicate_json_no_credentials(self):
        for changes in ({'token':'synthetic-secret'},{'version':True},{'remote_uid':True},{'ssh_options':['unsafe']}):
            self.write_plan(dict(self.document,**changes))
            with self.assertRaises(access.AccessError) as error:access.load_ssh_plan(self.plan_path)
            self.safe(error.exception);self.assertNotIn('synthetic-secret',str(error.exception))
        missing=dict(self.document);missing.pop('identity_file');self.write_plan(missing)
        with self.assertRaises(access.AccessError):access.load_ssh_plan(self.plan_path)
        self.write_plan();raw=self.plan_path.read_text();self.plan_path.write_text(raw[:-1]+',"version":1}')
        with self.assertRaises(access.AccessError):access.load_ssh_plan(self.plan_path)
    async def test_plan_changed_program_pin_fails_before_any_process(self):
        self.write_plan(dict(self.document,ssh_sha256='0'*64))
        with self.assertRaises(access.AccessError):access.load_ssh_plan(self.plan_path)
        self.assertEqual(self.ops.calls,[]);self.assertEqual(self.bops.calls,[])

class CLIShapeTests(unittest.TestCase):
    def args(self):return ['--client-dir','/private/client','--chrome-binary','/private/chrome','--chrome-sha256','a'*64]
    def test_mutually_exclusive_modes_and_fixed_errors_no_raw_values(self):
        for extra in ([],['--runtime-dir','/private/local','--ssh-plan','/private/remote'],['--unknown','sensitive-marker']):
            output=io.StringIO()
            with contextlib.redirect_stderr(output):result=access.main(self.args()+extra)
            self.assertEqual(result,1);self.assertIn('怎么解决',output.getvalue());self.assertIn('复制给 AI',output.getvalue())
            self.assertNotIn('sensitive-marker',output.getvalue())
    def test_cli_accepts_remote_parser_without_starting_transport(self):
        seen=[]
        def parser_only(coro):seen.append(coro);coro.close()
        with mock.patch.object(access.asyncio,'run',side_effect=parser_only),contextlib.redirect_stderr(io.StringIO()):
            result=access.main(self.args()+['--ssh-plan','/private/ssh-plan.json'])
        self.assertEqual(result,0);self.assertEqual(len(seen),1)
