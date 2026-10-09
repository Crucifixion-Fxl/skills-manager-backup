"""Offline SSH transport contract: real helper/Unix console, fake SSH boundary."""
from __future__ import annotations
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import signal
import time
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.console import ConsoleServer,ActionResult
from hostd.console_operations import ConsoleReceipt,current_intent
from hostd.store import Store,BindingRecord
from hostd.console_access import ConsoleAccess

class ForwardOps:
    """Actual loopback Unix byte forwarder; no SSH executable is ever invoked."""
    def __init__(self,test):
        self.test=test;self.loop=asyncio.get_running_loop();self.calls=[];self.pid=434343;self.start=19
        self.argv=();self.alive=True;self.foreign=False;self.fail=False;self.listener=None;self.workers=set();self.bad_packet=None
        self.mutate=None;self.ready=asyncio.Event();self.reaped=False;self.wrong_backend=False
        self.forward_gate=None;self.forward_entered=asyncio.Event();self.graph_response=None
    def helper(self,argv,*,stdin,env,timeout,limit):
        self.calls.append(('helper',tuple(argv),stdin))
        result=subprocess.run([sys.executable,'-I','-c',self.test.module().REMOTE_HELPER],input=stdin,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},timeout=timeout)
        if self.mutate:self.mutate()
        if self.bad_packet:result=SimpleNamespace(returncode=0,stdout=self.bad_packet(result.stdout),stderr=b'')
        return result
    def spawn(self,argv,*,env):
        self.calls.append(('spawn',tuple(argv),dict(env)));self.argv=tuple(argv)
        if self.fail:raise RuntimeError('synthetic creation result unknown')
        self.loop.call_soon_threadsafe(lambda:asyncio.create_task(self._listen()))
        return SimpleNamespace(pid=self.pid)
    async def _listen(self):
        self.listener=await asyncio.start_unix_server(self._forward,self.test.forward/'console.sock')
        (self.test.forward/'console.sock').chmod(0o600);self.ready.set()
    async def _forward(self,reader,writer):
        task=asyncio.current_task();self.workers.add(task);peer=None
        try:
            if self.forward_gate is not None:
                self.forward_entered.set();await self.forward_gate.wait()
            if self.graph_response is not None:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(self.graph_response);await writer.drain();return
            if self.wrong_backend:
                body=b'{"ok":false}';writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 12\r\nConnection: close\r\n\r\n'+body);await writer.drain();return
            upstream,peer=await asyncio.open_unix_connection(self.test.server.socket_path)
            async def copy(source,target):
                while data:=await source.read(65536):target.write(data);await target.drain()
            pair=[asyncio.create_task(copy(reader,peer)),asyncio.create_task(copy(upstream,writer))]
            try:await asyncio.wait(pair,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for job in pair:job.cancel()
                await asyncio.gather(*pair,return_exceptions=True)
        finally:
            if peer:peer.close();await peer.wait_closed()
            writer.close();await writer.wait_closed();self.workers.discard(task)
    def observe(self,process):
        return {'pid':self.pid,'start':self.start,'uid':os.geteuid(),'executable':'/foreign/ssh' if self.foreign else str(self.test.ssh),
            'sha256':self.test.digest(self.test.ssh),'argv':self.argv,'alive':self.alive}
    def terminate(self,process):self.calls.append(('terminate',process.pid));self.alive=False
    def wait(self,process,timeout):self.calls.append(('wait',process.pid));self.reaped=not self.alive;return 0 if self.reaped else None
    async def close(self):
        if self.listener:self.listener.close()
        jobs=tuple(self.workers)
        for job in jobs:job.cancel()
        if jobs:await asyncio.gather(*jobs,return_exceptions=True)
        if self.listener:await self.listener.wait_closed()

class ConsoleSSHTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'state'/'db')
        self.store.reconcile_bindings([BindingRecord('alpha','channel_alpha','oc_alpha','cli_alpha','/private/env','/private/config','/private/data',mirror_pubkey='d'*64)],now=100)
        self.server=ConsoleServer(self.store,self.root/'remote',heartbeat_interval=.03);await self.server.start()
        self.token=self.server.token_path.read_text().strip()
        self.forward=self.root/'forward';self.forward.mkdir(mode=0o700)
        self.client=self.root/'client';self.client.mkdir(mode=0o700)
        self.ssh=self.root/'ssh';shutil.copyfile('/usr/bin/true',self.ssh);self.ssh.chmod(0o700)
        self.known=self.root/'known-hosts';self.known.write_text('test.invalid ssh-ed25519 SYNTHETIC_PUBLIC_KEY\n');self.known.chmod(0o600)
        self.identity=self.root/'identity';self.identity.write_text('SYNTHETIC_PRIVATE_SSH_IDENTITY\n');self.identity.chmod(0o600)
        self.now=[100.0];self.ops=ForwardOps(self);self.tunnel=None;self.access=None;self.writers=[]
    async def asyncTearDown(self):
        for writer in self.writers:writer.close();await writer.wait_closed()
        if self.access:await self.access.close()
        if self.tunnel:
            try:await self.tunnel.close()
            except Exception:pass
        await self.ops.close();await self.server.close();self.store.close();self.tmp.cleanup()
    def module(self):return importlib.import_module('hostd.console_ssh')
    @staticmethod
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def plan(self,**changes):
        params=dict(ssh_binary=self.ssh,ssh_sha256=self.digest(self.ssh),host='test.invalid',user='testuser',remote_uid=os.geteuid(),
            remote_python='/usr/bin/python3.12',remote_runtime=self.server.runtime_dir,known_hosts=self.known,known_hosts_sha256=self.digest(self.known),
            identity_file=self.identity,identity_sha256=self.digest(self.identity),forward_dir=self.forward)
        params.update(changes);return self.module().SSHPlan.check(**params)
    def helper_request(self,**changes):
        request={'version':1,'nonce':'a'*64,'expected_uid':os.geteuid(),'runtime_dir':str(self.server.runtime_dir)};request.update(changes);return request
    def helper(self,request=None):
        return subprocess.run([sys.executable,'-I','-c',self.module().REMOTE_HELPER],input=json.dumps(request or self.helper_request()).encode(),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},timeout=3)
    async def started(self):
        self.tunnel=self.module().OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0],metadata_ttl=60)
        result=await self.tunnel.start();self.assertEqual(result['status'],'connected');self.assertFalse(result['live_verified']);return self.tunnel
    def safe(self,error):
        value=str(error);self.assertIn('怎么解决',value);self.assertIn('复制给 AI',value)
        self.assertNotIn(self.token,value);self.assertNotIn('SYNTHETIC_PRIVATE',value);self.assertNotIn('Traceback',value)
    async def request(self,path='/api/graph',method='GET',*,auth=True,fields=None,body=b''):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.access.port);self.writers.append(writer)
        headers={'Host':self.access.origin[7:],'Connection':'close'}
        if auth:headers['Authorization']='Bearer '+self.token
        if method=='POST':headers.update({'Origin':self.access.origin,'X-Hostd-Request':'1','Content-Type':'application/json','Content-Length':str(len(body)),'Idempotency-Key':'2'*64})
        headers.update(fields or {})
        writer.write((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode()+body);await writer.drain()
        data=await asyncio.wait_for(reader.read(),1);head,payload=data.split(b'\r\n\r\n',1);return int(head.split()[1]),payload

    async def test_native_child_exits_before_first_identity_observation_is_reaped(self):
        m=self.module();fixture=self.ops
        class ExitingOps(m.NativeSSHProcessOps):
            def __init__(self):super().__init__();self.waits=[]
            def helper(self,*args,**kwargs):return fixture.helper(*args,**kwargs)
            def observe(self,process):
                deadline=time.monotonic()+2
                while process.poll() is None:
                    if time.monotonic()>=deadline:raise AssertionError('actual child did not exit')
                    time.sleep(.01)
                return super().observe(process)
            def wait(self,process,timeout):
                result=super().wait(process,timeout);self.waits.append((process,result));return result
        native=ExitingOps()
        self.tunnel=m.OwnedSSHTunnel(self.plan(),process_ops=native,clock=lambda:self.now[0])
        with self.assertRaises(m.SSHError):await self.tunnel.start()
        process=self.tunnel.process
        self.assertIsNotNone(process)
        try:
            self.assertTrue(self.tunnel.readback()['reaped'])
            self.assertEqual(self.tunnel.readback()['status'],'closed')
            self.assertEqual(process.poll(),0)
            self.assertEqual(native.waits,[(process,0)])
            self.assertFalse(Path('/proc/'+str(process.pid)).exists())
            self.assertFalse((self.forward/'console.sock').exists())
            self.assertTrue((await self.tunnel.close())['reaped'])
        finally:
            # This exact child handle belongs to this test; no PID substitution.
            if process.poll() is None:os.kill(process.pid,signal.SIGKILL)
            native.wait(process,2)

    async def test_native_exit_proof_requires_exact_child_handle_pid_and_argv(self):
        m=self.module();native=m.NativeSSHProcessOps();argv=['/bin/sh','-c','exit 23']
        process=native.spawn(argv,env={'PATH':'/usr/bin:/bin'})
        deadline=time.monotonic()+2
        while process.poll() is None:
            self.assertLess(time.monotonic(),deadline);await asyncio.sleep(.01)
        self.assertEqual(process.poll(),23)
        forged=m._Process(process.pid);forged.returncode=23
        with self.assertRaises(m.SSHError):native.exited_owned(forged,argv)
        with self.assertRaises(m.SSHError):native.exited_owned(process,argv+['foreign'])
        original_pid=process.pid
        try:
            process.pid+=1
            with self.assertRaises(m.SSHError):native.exited_owned(process,argv)
        finally:process.pid=original_pid
        # Simulate the kernel reporting a reused PID, without altering verdicts.
        exists=m.os.path.lexists
        with mock.patch.object(m.os.path,'lexists',side_effect=lambda path:True if path=='/proc/'+str(process.pid) else exists(path)):
            with self.assertRaises(m.SSHError):native.exited_owned(process,argv)
        self.assertEqual(native.exited_owned(process,argv),23)

    async def test_actual_exited_child_cached_status_tamper_is_rejected(self):
        m=self.module();native=m.NativeSSHProcessOps();argv=['/bin/sh','-c','exit 23']
        process=native.spawn(argv,env={'PATH':'/usr/bin:/bin'})
        deadline=time.monotonic()+2
        while process.poll() is None:
            self.assertLess(time.monotonic(),deadline);await asyncio.sleep(.01)
        self.assertEqual(process.poll(),23)
        self.assertFalse(Path('/proc/'+str(process.pid)).exists())
        with self.assertRaises(ProcessLookupError):os.killpg(process.pid,0)
        try:
            process.returncode=0
            with self.assertRaises(m.SSHError):native.exited_owned(process,argv)
        finally:process.returncode=23
        self.assertEqual(native.exited_owned(process,argv),23)

    async def test_native_live_child_cannot_be_closed_by_untrusted_alive_or_exit_fields(self):
        # A live-child security test must own the lifetime, not race a ten-second
        # sleep against a loaded CI scheduler. Only this test terminates it.
        m=self.module();native=m.NativeSSHProcessOps();argv=['/usr/bin/sleep','infinity']
        process=native.spawn(argv,env={'PATH':'/usr/bin:/bin'})
        try:
            process._ownership=native.observe(process)
            self.assertTrue(process._ownership['alive'])
            self.assertEqual(os.getpgid(process.pid),process.pid)
            process.alive=False
            self.assertIsNone(native.exited_owned(process,argv))
            process.returncode=23
            with self.assertRaises(m.SSHError):native.exited_owned(process,argv)
        finally:
            process.returncode=None
            try:
                native.terminate(process)
                self.assertEqual(native.wait(process,2),-signal.SIGTERM)
            finally:
                # If an assertion/ownership observation fails, reap only this
                # exact direct child; do not leave an unbounded fixture behind.
                if process.poll() is None:os.kill(process.pid,signal.SIGKILL)
                native.wait(process,2)
        self.assertFalse(Path('/proc/'+str(process.pid)).exists())
        with self.assertRaises(ProcessLookupError):os.killpg(process.pid,0)

    async def test_replacement_native_handle_cannot_reap_original_tunnel(self):
        m=self.module();native=m.NativeSSHProcessOps();plan=self.plan()
        original=native.spawn(plan.argv(),env={'PATH':'/usr/bin:/bin'})
        replacement=native.spawn(plan.argv(),env={'PATH':'/usr/bin:/bin'})
        self.tunnel=m.OwnedSSHTunnel(plan,process_ops=native,clock=lambda:self.now[0])
        self.tunnel.process=replacement;self.tunnel._spawned_process=original
        try:
            with self.assertRaises(m.SSHError):await self.tunnel.close()
            self.assertFalse(self.tunnel.readback()['reaped'])
            self.assertNotEqual(self.tunnel.readback()['status'],'closed')
        finally:
            self.assertEqual(native.wait(original,2),0)
            self.assertEqual(native.wait(replacement,2),0)
            self.tunnel.process=original
        self.assertTrue((await self.tunnel.close())['reaped'])

    async def test_native_exit_with_actual_surviving_group_never_claims_reaped(self):
        # Isolate Linux subreaper state; reap the actual orphan after proving the
        # production wait refuses an exited leader with a surviving group.
        script=r"""
import ctypes,os,signal,sys,time
sys.path.insert(0,sys.argv[1])
from hostd.console_ssh import NativeSSHProcessOps,SSHError
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
ops=NativeSSHProcessOps()
argv=['/bin/sh','-c','sleep 30 & echo $! > "$1"; exit 23','fixture',sys.argv[2]]
p=ops.spawn(argv,env={'PATH':'/usr/bin:/bin'})
try:
 deadline=time.monotonic()+2
 while p.poll() is None:
  assert time.monotonic()<deadline
  time.sleep(.01)
 assert p.poll()==23
 child=int(open(sys.argv[2]).read())
 assert os.getpgid(child)==p.pid
 try:ops.exited_owned(p,argv)
 except SSHError:pass
 else:raise AssertionError('surviving group was accepted')
 assert os.getpgid(child)==p.pid
finally:
 try:os.killpg(p.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 while True:
  try:os.waitpid(-1,0)
  except ChildProcessError:break
assert ops.wait(p,1)==23
print('original child exit=23; live group rejected; isolated orphan reaped')
"""
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-I','-c',script,
            str(Path(__file__).resolve().parents[1]/'scripts'),str(self.root/'group-child')],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertIn(b'live group rejected',result.stdout)

    async def test_plan_rejects_socket_path_that_actual_linux_bind_rejects(self):
        directory=self.root/('l'*90);directory.mkdir(mode=0o700)
        path=directory/'console.sock'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
            with self.assertRaises(OSError):probe.bind(str(path))
        with self.assertRaises(self.module().SSHError):self.plan(forward_dir=directory)
        self.assertFalse(path.exists());self.assertEqual(self.ops.calls,[])

    async def test_plan_readonly_private_pin_no_live_claim(self):
        before=tuple(self.forward.iterdir());plan=self.plan();self.assertEqual(tuple(self.forward.iterdir()),before)
        self.assertFalse(plan.readback()['live_verified']);self.assertNotIn('SYNTHETIC_PRIVATE',repr(plan))
        self.assertEqual(self.ops.calls,[])

    async def test_unsafe_program_keys_paths_options_scope_rejected(self):
        m=self.module()
        for changes in ({'host':'test.invalid;touch /tmp/bad'},{'user':'-oProxyCommand=bad'},{'remote_python':'/usr/bin/python;id'},
                {'remote_runtime':'/tmp/../remote'},{'ssh_sha256':'f'*64},{'known_hosts_sha256':'f'*64},{'identity_sha256':'f'*64},{'remote_uid':-1}):
            with self.assertRaises(m.SSHError) as error:self.plan(**changes)
            self.safe(error.exception)
        for path,mode in ((self.ssh,0o775),(self.known,0o644),(self.identity,0o644),(self.forward,0o755)):
            old=path.stat().st_mode&0o777;path.chmod(mode)
            with self.assertRaises(m.SSHError):self.plan()
            path.chmod(old)
        alias=self.root/'alias-key';alias.symlink_to(self.identity)
        with self.assertRaises(m.SSHError):self.plan(identity_file=alias)

    async def test_fixed_helper_actual_private_runtime_packet(self):
        result=self.helper();self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,b'')
        packet=self.module().parse_helper_packet(result.stdout,self.helper_request())
        self.assertNotIn(self.token,repr(packet));self.assertFalse(packet.readback()['live_verified'])
        self.assertEqual(packet.readback()['remote_uid'],os.geteuid())
        self.assertNotIn(self.token,json.dumps(packet.readback()))

    async def test_helper_wrong_uid_nonce_shape_extra_fields_rejected(self):
        m=self.module()
        for changes in ({'expected_uid':os.geteuid()+1},{'nonce':'bad'},{'extra':'unsupported'},{'runtime_dir':'relative'},{'version':True}):
            result=self.helper(self.helper_request(**changes));self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,b'');self.assertNotIn(self.token.encode(),result.stderr)
        raw=self.helper().stdout
        for change in (lambda doc:dict(doc,nonce='b'*64),lambda doc:dict(doc,uid=os.geteuid()+1),lambda doc:dict(doc,extra='bad'),lambda doc:dict(doc,version=True)):
            with self.assertRaises(m.SSHError):m.parse_helper_packet(json.dumps(change(json.loads(raw))).encode(),self.helper_request())

    async def test_helper_nofollow_permissions_and_token_bounds(self):
        for path,mode in ((self.server.runtime_dir,0o755),(self.server.token_path,0o644),(self.server.socket_path,0o666)):
            old=path.stat().st_mode&0o777;path.chmod(mode);result=self.helper();self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,b'');path.chmod(old)
        alias=self.root/'remote-alias';alias.symlink_to(self.server.runtime_dir,target_is_directory=True)
        self.assertNotEqual(self.helper(self.helper_request(runtime_dir=str(alias))).returncode,0)
        old=self.server.token_path.read_bytes();self.server.token_path.write_bytes(b'x'*10000)
        self.assertNotEqual(self.helper().returncode,0);self.server.token_path.write_bytes(old)

    async def test_helper_duplicate_json_and_cas_reject(self):
        m=self.module();raw=self.helper().stdout;doc=json.loads(raw)
        duplicate=raw[:-1]+b',"nonce":"'+b'a'*64+b'"}'
        for packet in (duplicate,raw+b'\n{}',b'x'*10000):
            with self.assertRaises(m.SSHError):m.parse_helper_packet(packet,self.helper_request())
        self.assertIn('runtime_dev',doc);self.assertIn('runtime_ino',doc);self.assertIn('socket_dev',doc);self.assertIn('socket_ino',doc)
        self.assertEqual(doc['token_sha256'],hashlib.sha256((self.token+'\n').encode()).hexdigest())

    async def test_exact_ssh_flags_helper_stdin_no_secrets_argv(self):
        await self.started();commands=[call[1] for call in self.ops.calls if call[0] in ('helper','spawn')]
        for argv in commands:
            self.assertEqual(argv[0],str(self.ssh));self.assertIn('-T',argv);self.assertIn('-F',argv);self.assertIn('/dev/null',argv)
            for option in ('StrictHostKeyChecking=yes','IdentitiesOnly=yes','BatchMode=yes','IdentityAgent=none','ForwardAgent=no',
                    'ControlMaster=no','ControlPath=none','ProxyCommand=none','ProxyJump=none','PasswordAuthentication=no','GSSAPIAuthentication=no'):
                self.assertIn(option,argv)
            self.assertNotIn(self.token,str(argv));self.assertNotIn('SYNTHETIC_PRIVATE',str(argv))
        forward=next(call[1] for call in self.ops.calls if call[0]=='spawn')
        self.assertIn(str(self.forward/'console.sock')+':'+str(self.server.socket_path),forward)
        self.assertIn('StreamLocalBindMask=0177',forward);self.assertIn('StreamLocalBindUnlink=no',forward)
        self.assertIn('ExitOnForwardFailure=yes',forward);self.assertIn('-N',forward)
        self.assertFalse(any(path.name=='console.token' for path in self.forward.iterdir()))

    async def test_protected_key_cas_after_helper_blocks_forward_spawn(self):
        m=self.module();self.ops.mutate=lambda:self.known.write_text('changed public host key\n')
        self.tunnel=m.OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0])
        with self.assertRaises(m.SSHError):await self.tunnel.start()
        self.assertFalse(any(call[0]=='spawn' for call in self.ops.calls))

    async def test_preexisting_forward_socket_or_unknown_spawn_not_adopted(self):
        m=self.module();(self.forward/'console.sock').write_text('unrelated');(self.forward/'console.sock').chmod(0o600)
        with self.assertRaises(m.SSHError):self.plan()
        (self.forward/'console.sock').unlink();self.ops.fail=True
        self.tunnel=m.OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0])
        with self.assertRaises(m.SSHError):await self.tunnel.start()
        with self.assertRaises(m.SSHError):await self.tunnel.start()
        with self.assertRaises(m.SSHError):await self.tunnel.close()
        self.assertFalse(any(call[0]=='terminate' for call in self.ops.calls))

    async def test_remote_graph_403_cannot_claim_connected(self):
        m=self.module();self.ops.wrong_backend=True
        self.tunnel=m.OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0])
        with self.assertRaises(m.SSHError) as error:await self.tunnel.start()
        self.safe(error.exception);self.assertNotEqual(self.tunnel.readback()['status'],'connected')

    def closing_stream_adapter(self,error_type,*,after_body=None):
        """Real Unix reader/socket; inject only the owned writer close outcome."""
        original=asyncio.open_unix_connection
        async def connect(*args,**kwargs):
            reader,writer=await original(*args,**kwargs)
            if not str(args[0]).startswith("/proc/self/fd/"):return reader,writer
            class Writer:
                def __getattr__(self,name):return getattr(writer,name)
                async def wait_closed(self):
                    await writer.wait_closed()
                    raise error_type('synthetic close outcome')
            if after_body is not None:
                readexactly=reader.readexactly
                async def read_body(size):
                    body=await readexactly(size);after_body();return body
                reader.readexactly=read_body
            return reader,Writer()
        return mock.patch.object(asyncio,'open_unix_connection',side_effect=connect)

    async def test_complete_graph_readback_survives_expected_owned_close_errors(self):
        for error_type in (ConnectionResetError,BrokenPipeError):
            with self.subTest(error_type=error_type.__name__):
                try:
                    with self.closing_stream_adapter(error_type):await self.started()
                    self.assertEqual(self.tunnel.readback()['status'],'connected')
                    self.assertIsNotNone(self.tunnel.endpoint())
                finally:
                    await self.tunnel.close();await self.ops.close()
                    self.ops=ForwardOps(self)

    async def test_close_reset_cannot_accept_invalid_or_incomplete_graph(self):
        await self.started()
        valid=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 12\r\n\r\n{"nodes":[]}'
        responses=(valid.replace(b'200 OK',b'403 Forbidden'),
            valid.replace(b'Content-Type: application/json\r\n',b''),
            valid.replace(b'Content-Length: 12\r\n',b''),
            valid[:-1],valid.replace(b'"nodes"',b'"other"'))
        for response in responses:
            for error_type in (ConnectionResetError,BrokenPipeError):
                with self.subTest(response=response,error_type=error_type.__name__):
                    self.ops.graph_response=response
                    with self.closing_stream_adapter(error_type):
                        with self.assertRaises((self.module().SSHError,asyncio.IncompleteReadError)):
                            await self.tunnel._graph(self.tunnel._packet)

    async def test_close_reset_cannot_accept_identity_change_after_full_body(self):
        await self.started()
        for error_type in (ConnectionResetError,BrokenPipeError):
            with self.subTest(error_type=error_type.__name__):
                try:
                    with self.closing_stream_adapter(error_type,after_body=lambda:setattr(self.ops,'foreign',True)):
                        with self.assertRaises(self.module().SSHError):await self.tunnel._graph(self.tunnel._packet)
                finally:self.ops.foreign=False

    async def test_read_cancellation_with_close_reset_preserves_cancel_and_reaps(self):
        self.ops.forward_gate=asyncio.Event()
        self.tunnel=self.module().OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0])
        with self.closing_stream_adapter(ConnectionResetError):
            task=asyncio.create_task(self.tunnel.start())
            await asyncio.wait_for(self.ops.forward_entered.wait(),1);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        self.assertTrue(self.tunnel.readback()['reaped']);self.assertTrue(self.ops.reaped)
        self.assertFalse((self.forward/'console.sock').exists())

    async def test_owned_close_cancellation_and_unknown_io_error_propagate(self):
        await self.started()
        for error_type in (asyncio.CancelledError,OSError,RuntimeError):
            with self.subTest(error_type=error_type.__name__):
                with self.closing_stream_adapter(error_type):
                    with self.assertRaises(error_type):await self.tunnel._graph(self.tunnel._packet)

    async def test_forwarded_proxy_actual_auth_graph_origin_and_receipts(self):
        await self.started();self.access=ConsoleAccess.from_forwarder(self.tunnel,self.client);await self.access.start()
        status,payload=await self.request(auth=False);self.assertEqual(status,401);self.assertNotIn(self.token.encode(),payload)
        status,_=await self.request(fields={'Origin':'https://evil.invalid'});self.assertEqual(status,403)
        status,payload=await self.request();self.assertEqual(status,200);self.assertIn(b'binding:alpha',payload)
        receipts={}
        async def action(kind,target,operation):
            intent=current_intent()
            with self.store.transaction():self.store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
            receipts[intent.operation_id]=ConsoleReceipt(intent.operation_id,'paused',hashlib.sha256(intent.operation_id.encode()).hexdigest())
            return ActionResult(True,'paused')
        self.server.action=action;self.server.operation_readback=lambda intent:receipts.get(intent.operation_id)
        status,payload=await self.request('/api/bindings/alpha/pause','POST',body=b'{}');self.assertEqual(status,202)
        receipt=json.loads(payload);await self.server.wait_operation(receipt['id'])
        _,payload=await self.request('/api/operations/'+receipt['id']);self.assertEqual(json.loads(payload)['status'],'completed')
        self.assertFalse((self.forward/'console.token').exists())

    async def test_forwarded_sse_local_uid_and_disconnect_reap(self):
        await self.started();self.access=ConsoleAccess.from_forwarder(self.tunnel,self.client);await self.access.start()
        reader,writer=await asyncio.open_connection('127.0.0.1',self.access.port);self.writers.append(writer)
        writer.write(('GET /api/events HTTP/1.1\r\nHost: '+self.access.origin[7:]+'\r\nAuthorization: Bearer '+self.token+'\r\n\r\n').encode());await writer.drain()
        head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),1);self.assertIn(b'text/event-stream',head)
        self.assertIn(b'event: snapshot',await asyncio.wait_for(reader.readuntil(b'\n\n'),1))
        writer.close();await writer.wait_closed()
        for _ in range(40):
            if self.server.subscriber_count==0:break
            await asyncio.sleep(.01)
        self.assertEqual(self.server.subscriber_count,0)

    async def test_expired_remote_metadata_pending_refresh_exact_tuple(self):
        await self.started();self.access=ConsoleAccess.from_forwarder(self.tunnel,self.client);await self.access.start()
        self.now[0]+=61;status,_=await self.request();self.assertIn(status,(403,503))
        self.assertEqual(self.tunnel.readback()['status'],'pending')
        await self.tunnel.refresh();status,_=await self.request();self.assertEqual(status,200)
        old=self.server.token_path.read_bytes();self.server.token_path.write_text('x'*43+'\n')
        with self.assertRaises(self.module().SSHError):await self.tunnel.refresh()
        self.server.token_path.write_bytes(old)

    async def test_forged_endpoint_current_socket_process_mismatch_rejected(self):
        m=self.module();await self.started()
        with self.assertRaises(Exception):ConsoleAccess.from_forwarder(SimpleNamespace(token=self.token,socket_path=self.forward/'console.sock'),self.client)
        self.ops.foreign=True
        with self.assertRaises(Exception):ConsoleAccess.from_forwarder(self.tunnel,self.client)
        with self.assertRaises(m.SSHError):await self.tunnel.close()
        self.assertFalse(any(call[0]=='terminate' for call in self.ops.calls));self.ops.foreign=False
        socket=self.forward/'console.sock';socket.rename(self.forward/'held');socket.symlink_to(self.forward/'held')
        with self.assertRaises(Exception):ConsoleAccess.from_forwarder(self.tunnel,self.client)
        socket.unlink();(self.forward/'held').rename(socket)

    async def test_owned_cleanup_current_identity_reaped_no_broad_unlink(self):
        await self.started();unrelated=self.forward/'unrelated';unrelated.write_text('keep');unrelated.chmod(0o600)
        self.known.write_text('rotated host key\n');self.identity.write_text('rotated identity\n')
        with self.assertRaises(self.module().SSHError):self.tunnel.validate()
        result=await self.tunnel.close();self.assertTrue(result['reaped']);self.assertFalse(result['live_verified'])
        self.assertTrue(unrelated.exists());self.assertFalse((self.forward/'console.sock').exists())
        self.assertEqual([call[0] for call in self.ops.calls].count('terminate'),1)
        self.assertEqual([call[0] for call in self.ops.calls].count('wait'),1)
        await self.ops.close();self.forward=self.root/'forward-cancel';self.forward.mkdir(mode=0o700)
        self.known.write_text('test.invalid ssh-ed25519 SYNTHETIC_PUBLIC_KEY\n');self.identity.write_text('SYNTHETIC_PRIVATE_SSH_IDENTITY\n')
        self.ops=ForwardOps(self);self.ops.forward_gate=asyncio.Event()
        self.tunnel=self.module().OwnedSSHTunnel(self.plan(),process_ops=self.ops,clock=lambda:self.now[0])
        task=asyncio.create_task(self.tunnel.start());await asyncio.wait_for(self.ops.forward_entered.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        self.assertTrue(self.tunnel.readback()['reaped']);self.assertTrue(self.ops.reaped)
        self.assertFalse((self.forward/'console.sock').exists())

    async def test_native_process_parser_fixed_proc_and_fixed_failure_copy(self):
        m=self.module();proc=self.root/'proc';proc.mkdir(mode=0o700)
        tail=['S']+['0']*20;tail[19]='19'
        for name,raw in [('stat','434343 (synthetic) '+' '.join(tail)),('cmdline',str(self.ssh)+'\0-N\0')]:
            (proc/name).write_bytes(raw.encode());(proc/name).chmod(0o600)
        (proc/'exe').symlink_to(self.ssh);original=os.open
        def kernel_open(path,*args,**kwargs):
            if path=='/proc/434343':path=str(proc)
            return original(path,*args,**kwargs)
        with mock.patch.object(m.os,'open',side_effect=kernel_open):
            observed=m.NativeSSHProcessOps().observe(SimpleNamespace(pid=434343,poll=lambda:None))
        self.assertEqual(observed,{'pid':434343,'start':19,'uid':os.geteuid(),'executable':str(self.ssh),
            'sha256':self.digest(self.ssh),'argv':(str(self.ssh),'-N'),'alive':True})
        self.safe(m.SSHError())
