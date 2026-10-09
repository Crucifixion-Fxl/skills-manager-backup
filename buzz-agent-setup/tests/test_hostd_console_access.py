"""Offline local access contract: real Unix console, synthetic Chrome/CDP boundary."""
from __future__ import annotations
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd.console import ConsoleServer, ActionResult
from hostd.console_operations import ConsoleReceipt, current_intent
from hostd.store import Store, BindingRecord


class BrowserOps:
    """Only the process/kernel boundary is synthetic; never claims Chrome acceptance."""
    def __init__(self):
        self.calls=[]; self.pid=42424; self.start=17; self.alive=True; self.foreign=False
        self.argv=(); self.sha=''; self.fds=(); self.cdp=[]; self.event_sink=None; self.fail_worker=False; self.late_attach=False; self.fetch_gate=None
    def spawn(self, argv, *, env, read_fd, write_fd):
        self.calls.append(('spawn', tuple(argv), dict(env)))
        self.argv=tuple(argv); self.fds=(read_fd,write_fd)
        return SimpleNamespace(pid=self.pid)
    def observe(self, process):
        return {'pid': self.pid, 'start': self.start, 'uid': os.geteuid(),
                'executable': '/foreign/chrome' if self.foreign else self.argv[0],
                'sha256': self.sha, 'argv': self.argv, 'alive': self.alive}
    def bind_events(self,callback):self.event_sink=callback
    async def command(self, method, params, *, session=None):
        self.cdp.append((session,method,params))
        if method=='Target.createTarget':
            if self.event_sink:
                event={'method':'Target.attachedToTarget','params':{'sessionId':'owned-page','targetInfo':{'targetId':'owned-target','type':'page'}}}
                if self.late_attach:asyncio.get_running_loop().call_soon(lambda:asyncio.create_task(self.event_sink(event)))
                else:await self.event_sink(event)
            return {'targetId':'owned-target'}
        if method=='Fetch.enable' and session=='owned-page' and self.fetch_gate is not None:await self.fetch_gate.wait()
        if method=='Fetch.enable' and session=='failed-worker':raise RuntimeError('synthetic Fetch unavailable')
        if method=='Page.navigate' and self.fail_worker and self.event_sink:
            await self.event_sink({'method':'Target.attachedToTarget','params':{'sessionId':'failed-worker',
                'targetInfo':{'targetId':'new-worker','type':'service_worker'}}})
        if method=='Target.attachToTarget':return {'sessionId':'owned-page'}
        return {}
    def terminate(self, process): self.calls.append(('terminate',process.pid)); self.alive=False
    def wait(self, process, timeout): self.calls.append(('wait',process.pid)); return 0 if not self.alive else None


class ConsoleAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        console=importlib.import_module(ConsoleServer.__module__)
        ui=self.root/'console-ui.html';ui.write_bytes(console.UI_PATH.read_bytes());ui.chmod(0o600)
        ui_patch=mock.patch.object(console,'UI_PATH',ui);ui_patch.start();self.addCleanup(ui_patch.stop)
        self.store=Store(self.root/'state'/'db')
        self.store.reconcile_bindings([BindingRecord('alpha','channel_alpha','oc_alpha','cli_alpha',
            '/private/profile','/private/config','/private/data',mirror_pubkey='d'*64)],now=100)
        self.server=ConsoleServer(self.store,self.root/'daemon',request_timeout=.1,heartbeat_interval=.03)
        await self.server.start();self.token=self.server.token_path.read_text().strip()
        self.client_dir=self.root/'client';self.client_dir.mkdir(mode=0o700)
        self.access=None;self.browser=None;self.writers=[]
    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close();await writer.wait_closed()
        if self.browser is not None:
            try:await self.browser.close()
            except Exception:pass
        if self.access is not None:await self.access.close()
        await self.server.close();self.store.close();self.tmp.cleanup()
    def module(self): return importlib.import_module('hostd.console_access')
    def browser_module(self): return importlib.import_module('hostd.console_browser')
    def admit(self):
        m=self.module();self.access=m.ConsoleAccess.check(self.server.runtime_dir,self.client_dir,request_timeout=.1)
        return self.access
    async def start(self):
        access=self.admit();await access.start();return access
    async def raw(self, raw):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.access.port)
        self.writers.append(writer);writer.write(raw);await writer.drain()
        data=await asyncio.wait_for(reader.read(),1)
        head,body=data.split(b'\r\n\r\n',1);return int(head.split()[1]),head,body
    async def request(self,path='/api/graph',method='GET',*,auth=True,fields=None,body=b''):
        headers={'Host':self.access.origin.removeprefix('http://'),'Connection':'close'}
        if auth:headers['Authorization']='Bearer '+self.token
        if method=='POST':headers.update({'Origin':self.access.origin,'X-Hostd-Request':'1',
            'Content-Type':'application/json','Content-Length':str(len(body)),'Idempotency-Key':'1'*64})
        headers.update(fields or {})
        return await self.raw((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode()+body)
    def safe(self,value):
        text=str(value);self.assertNotIn(self.token,text);self.assertNotIn('Traceback',text)
        self.assertIn('怎么解决',text);self.assertIn('复制给 AI',text)
    def browser_plan(self):
        b=self.browser_module();chrome=self.root/'chrome'
        if not chrome.exists():shutil.copyfile('/usr/bin/true',chrome);chrome.chmod(0o700)
        sha=hashlib.sha256(chrome.read_bytes()).hexdigest()
        return b.BrowserPlan.check(self.access,chrome_binary=chrome,chrome_sha256=sha,profile_dir=self.client_dir/'chrome-profile')

    async def test_protected_runtime_token_socket_admission(self):
        m=self.module();self.admit();self.assertNotIn(self.token,repr(self.access))
        self.assertFalse(self.access.readback()['live_verified'])
        for path,mode in ((self.server.token_path,0o644),(self.server.socket_path,0o666),(self.server.runtime_dir,0o755)):
            old=path.stat().st_mode&0o777;path.chmod(mode)
            with self.assertRaises(m.AccessError) as error:m.ConsoleAccess.check(self.server.runtime_dir,self.client_dir)
            self.safe(error.exception);path.chmod(old)
        alias=self.root/'alias';alias.symlink_to(self.server.runtime_dir,target_is_directory=True)
        with self.assertRaises(m.AccessError):m.ConsoleAccess.check(alias,self.client_dir)
        with self.assertRaises(m.AccessError):m.ConsoleAccess.check(self.server.runtime_dir,self.root/'absent')

    async def test_loopback_ephemeral_page_preserves_csp_no_token(self):
        await self.start();self.assertRegex(self.access.origin,r'^http://127\.0\.0\.1:[1-9][0-9]*$')
        self.assertEqual(self.access.listener_address[0],'127.0.0.1')
        status,head,body=await self.request('/')
        self.assertEqual(status,200);self.assertIn(b"connect-src 'self'",head)
        self.assertNotIn(self.token.encode(),head+body);self.assertNotIn(b'Access-Control-Allow-Origin',head)

    async def test_missing_wrong_auth_never_injected_into_unix(self):
        await self.start()
        for fields,auth in (({},False),({'Authorization':'Bearer wrong'},True)):
            status,_,body=await self.request(fields=fields,auth=auth)
            self.assertEqual(status,401);self.safe(body.decode())
        self.assertEqual(self.server.subscriber_count,0)

    async def test_graph_details_exact_stored_status_actual_unix(self):
        await self.start();status,_,body=await self.request()
        self.assertEqual(status,200);graph=json.loads(body)
        binding=next(n for n in graph['nodes'] if n['id']=='binding:alpha')
        self.assertEqual(binding['status'],'active')
        status,_,body=await self.request('/api/bindings/alpha');self.assertEqual(status,200)
        self.assertNotIn(b'/private/profile',body)

    async def test_post_real_durable_pause_receipt_and_idempotency(self):
        receipts={};calls=[]
        async def action(kind,target,operation):
            calls.append((kind,target,operation));intent=current_intent()
            with self.store.transaction():self.store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
            receipts[intent.operation_id]=ConsoleReceipt(intent.operation_id,'paused',hashlib.sha256(intent.operation_id.encode()).hexdigest())
            return ActionResult(True,'paused')
        self.server.action=action;self.server.operation_readback=lambda intent:receipts.get(intent.operation_id)
        await self.start();status,_,body=await self.request('/api/bindings/alpha/pause','POST',body=b'{}')
        self.assertEqual(status,202);receipt=json.loads(body);await self.server.wait_operation(receipt['id'])
        status,_,body=await self.request('/api/operations/'+receipt['id']);self.assertEqual(json.loads(body)['status'],'completed')
        status,_,body=await self.request('/api/bindings/alpha/pause','POST',body=b'{}')
        self.assertEqual(json.loads(body)['id'],receipt['id']);self.assertEqual(len(calls),1)

    async def test_sse_roundtrip_disconnect_reaps_unix_subscriber(self):
        await self.start();reader,writer=await asyncio.open_connection('127.0.0.1',self.access.port)
        self.writers.append(writer)
        writer.write(('GET /api/events HTTP/1.1\r\nHost: '+self.access.origin.removeprefix('http://')+'\r\nAuthorization: Bearer '+self.token+'\r\n\r\n').encode());await writer.drain()
        head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),1);self.assertIn(b'text/event-stream',head)
        packet=await asyncio.wait_for(reader.readuntil(b'\n\n'),1);self.assertIn(b'event: snapshot',packet)
        self.assertNotIn(self.token.encode(),head+packet);writer.close();await writer.wait_closed()
        for _ in range(30):
            if self.server.subscriber_count==0:break
            await asyncio.sleep(.01)
        self.assertEqual(self.server.subscriber_count,0)

    async def test_ambiguous_framing_connect_upgrade_queries_reject(self):
        await self.start();base='Host: '+self.access.origin.removeprefix('http://')+'\r\nAuthorization: Bearer '+self.token+'\r\n'
        for first,extra in [('CONNECT hostd.local:80 HTTP/1.1',''),('GET /api/graph?token=x HTTP/1.1',''),
                ('GET http://evil.invalid/ HTTP/1.1',''),('GET / HTTP/1.1','Upgrade: websocket\r\n'),
                ('GET / HTTP/1.1','Content-Length: 0\r\nContent-Length: 0\r\n'),
                ('GET / HTTP/1.1','Transfer-Encoding: chunked\r\n'),('GET / HTTP/1.1','Authorization: Bearer duplicate\r\n')]:
            status,_,body=await self.raw((first+'\r\n'+base+extra+'\r\n').encode())
            self.assertIn(status,(400,403,413));self.safe(body.decode())

    async def test_cross_site_host_origin_iframe_and_csrf_reject(self):
        await self.start()
        for fields in ({'Host':'localhost:80'},{'Origin':'https://evil.invalid'},{'Origin':'null'},
                {'Sec-Fetch-Site':'cross-site'},{'Sec-Fetch-Dest':'iframe','Sec-Fetch-Site':'cross-site'}):
            status,_,body=await self.request(fields=fields);self.assertEqual(status,403);self.safe(body.decode())
        for fields,body in (({'Origin':'http://evil.invalid'},b'{}'),({'X-Hostd-Request':'0'},b'{}'),({},b'{"path":"/etc/passwd"}')):
            status,_,_=await self.request('/api/bindings/alpha/pause','POST',fields=fields,body=body)
            self.assertIn(status,(400,403));self.assertFalse(self.server._operations)

    async def test_token_socket_replacement_cas_rejects(self):
        m=self.module();await self.start();old=self.server.token_path.read_bytes()
        self.server.token_path.write_text('x'*43+'\n');self.server.token_path.chmod(0o600)
        status,_,body=await self.request();self.assertIn(status,(403,503));self.safe(body.decode())
        self.server.token_path.write_bytes(old)
        self.server.socket_path.rename(self.root/'held-socket')
        self.server.socket_path.symlink_to(self.root/'held-socket')
        status,_,body=await self.request();self.assertIn(status,(403,503));self.safe(body.decode())
        self.server.socket_path.unlink();(self.root/'held-socket').rename(self.server.socket_path)
        runtime=self.server.runtime_dir;held=self.root/'held-runtime';runtime.rename(held);runtime.mkdir(mode=0o700)
        for name in ('console.sock','console.token'):(held/name).rename(runtime/name)
        try:
            status,_,_=await self.request();self.assertIn(status,(403,503))
        finally:
            for name in ('console.sock','console.token'):(runtime/name).rename(held/name)
            runtime.rmdir();held.rename(runtime)

    async def test_slow_request_limits_close_reap(self):
        await self.start();reader,writer=await asyncio.open_connection('127.0.0.1',self.access.port);self.writers.append(writer)
        writer.write(b'GET / HTTP/1.1\r\n');await writer.drain()
        data=await asyncio.wait_for(reader.read(),1);self.assertIn(b'408',data.split(b'\r\n',1)[0])
        self.safe(data.decode());port=self.access.port;await self.access.close()
        with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',port)
        self.assertFalse(self.access.readback()['live_verified'])

    async def test_fetch_exact_origin_redirect_strips_foreign_authorization(self):
        await self.start();b=self.browser_module();sent=[]
        async def send(session,method,params):sent.append((session,method,params));return {}
        guard=b.FetchGuard(self.access,send);await guard.attach('owned-page',target_type='page')
        for url,expected in ((self.access.origin+'/',True),('https://evil.invalid/',False),
                (self.access.origin+'.evil.invalid/',False),('http://localhost:'+str(self.access.port)+'/',False),
                ('http://127.0.0.1:'+str(self.access.port+1)+'/api/graph',False)):
            await guard.handle('owned-page',{'method':'Fetch.requestPaused','params':{'requestId':'r1',
                'request':{'url':url,'method':'GET','headers':{'Authorization':'Bearer stale','X-Test':'1'}}}})
            method,params=sent[-1][1:];self.assertEqual(method,'Fetch.continueRequest')
            auth=[row['value'] for row in params['headers'] if row['name'].lower()=='authorization']
            self.assertEqual(auth,['Bearer '+self.token] if expected else [])
        await guard.handle('owned-page',{'method':'Fetch.requestPaused','params':{'requestId':'redirected',
            'redirectedRequestId':'r1','request':{'url':'https://evil.invalid/redirect','method':'GET','headers':{'authorization':'Bearer '+self.token}}}})
        self.assertFalse(any(h['name'].lower()=='authorization' for h in sent[-1][2]['headers']))
        self.assertFalse(any(method in ('Network.setExtraHTTPHeaders','Runtime.evaluate') for _,method,_ in sent))

    async def test_fetch_new_targets_fail_closed_no_page_token(self):
        await self.start();b=self.browser_module();sent=[]
        async def send(session,method,params):sent.append((session,method,params));return {}
        guard=b.FetchGuard(self.access,send)
        with self.assertRaises(b.BrowserError):await guard.handle('foreign-session',{'method':'Fetch.requestPaused','params':{}})
        for kind in ('page','iframe','worker','service_worker'):
            await guard.attach('owned-'+kind,target_type=kind)
            methods=[method for session,method,_ in sent if session=='owned-'+kind]
            self.assertEqual(methods,['Fetch.enable','Runtime.runIfWaitingForDebugger'])
            self.assertNotIn(self.token,json.dumps([params for session,_,params in sent if session=='owned-'+kind]))

        # Genuine pipe transport must reap its outstanding event handlers.
        peer_read,client_write=os.pipe2(os.O_CLOEXEC);client_read,peer_write=os.pipe2(os.O_CLOEXEC)
        entered=asyncio.Event();finished=asyncio.Event();never=asyncio.Event()
        async def event(packet):
            entered.set()
            try:await never.wait()
            finally:finished.set()
        pipe=b._Pipe(client_read,client_write,event)
        try:
            await pipe.start()
            os.write(peer_write,json.dumps({'method':'Fetch.requestPaused','params':{}}).encode()+b'\0')
            await asyncio.wait_for(entered.wait(),1)
            await pipe.close()
            self.assertTrue(finished.is_set(),'owned CDP event task survived pipe close')
        finally:
            never.set();await pipe.close()
            for fd in (peer_read,client_write,client_read,peer_write):os.close(fd)

        # A real CDP pipe peer pauses navigation and withholds its response
        # until Fetch.continueRequest arrives. Command response waits must not
        # retain the lock needed by event-driven commands.
        peer_read,client_write=os.pipe2(os.O_CLOEXEC);client_read,peer_write=os.pipe2(os.O_CLOEXEC)
        os.set_blocking(peer_read,False);buffer=b'';wire=[]
        async def receive():
            nonlocal buffer
            for _ in range(150):
                if b'\0' in buffer:
                    packet,buffer=buffer.split(b'\0',1);return json.loads(packet)
                try:buffer+=os.read(peer_read,65536)
                except BlockingIOError:pass
                await asyncio.sleep(.005)
            raise AssertionError('CDP peer did not receive required event command')
        pipe=None
        async def event(packet):
            await pipe.command('Fetch.continueRequest',{'requestId':packet['params']['requestId'],'headers':[]},session='owned-page')
        pipe=b._Pipe(client_read,client_write,event)
        async def peer():
            navigation=await receive();wire.append(navigation['method'])
            self.assertEqual(navigation['method'],'Page.navigate')
            os.write(peer_write,json.dumps({'method':'Fetch.requestPaused','sessionId':'owned-page',
                'params':{'requestId':'paused-navigation','request':{'url':self.access.origin+'/','method':'GET','headers':{}}}}).encode()+b'\0')
            continued=await receive();wire.append(continued['method'])
            self.assertEqual(continued['method'],'Fetch.continueRequest')
            for command in (continued,navigation):
                os.write(peer_write,json.dumps({'id':command['id'],'result':{}}).encode()+b'\0')
        tasks=[]
        try:
            await pipe.start()
            tasks=[asyncio.create_task(peer()),asyncio.create_task(pipe.command('Page.navigate',{'url':self.access.origin+'/'},session='owned-page'))]
            try:await asyncio.wait_for(asyncio.gather(*tasks),.65)
            except asyncio.TimeoutError:self.fail('navigation response wait blocked paused-request command')
            self.assertEqual(wire,['Page.navigate','Fetch.continueRequest'])
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await pipe.close();await asyncio.gather(*tasks,return_exceptions=True)
            for fd in (peer_read,client_write,client_read,peer_write):os.close(fd)

        # A writable pipe does not promise capacity for a full bounded frame.
        # A nonreading native peer must not hold an executor write indefinitely.
        peer_read,client_write=os.pipe2(os.O_CLOEXEC);client_read,peer_write=os.pipe2(os.O_CLOEXEC)
        pipe=b._Pipe(client_read,client_write,event);write_task=None
        try:
            write_task=asyncio.create_task(asyncio.to_thread(pipe._write,b'x'*(1024*1024)))
            try:
                with self.assertRaises(b.BrowserError):await asyncio.wait_for(asyncio.shield(write_task),2.4)
            except asyncio.TimeoutError:self.fail('native CDP write exceeded its owned IO budget')
        finally:
            # The initial implementation may have a blocked write. Drain only
            # this owned synthetic pipe and join the worker before closing fds.
            os.set_blocking(peer_read,False)
            while write_task is not None and not write_task.done():
                try:os.read(peer_read,65536)
                except BlockingIOError:pass
                await asyncio.sleep(.001)
            if write_task is not None:await asyncio.gather(write_task,return_exceptions=True)
            await pipe.close()
            for fd in (peer_read,client_write,client_read,peer_write):os.close(fd)

    async def test_browser_program_pin_profile_fresh_private(self):
        await self.start();b=self.browser_module();plan=self.browser_plan()
        self.assertNotIn(self.token,repr(plan));self.assertFalse(plan.readback()['live_verified'])
        # Only fixed /proc directory opens are redirected. The production parser
        # reads genuine bytes, ELF inode/hash and argv, not expected-plan echoes.
        proc=self.root/'proc';proc.mkdir(mode=0o700)
        tail=['S']+['0']*20;tail[19]='17'
        (proc/'stat').write_text('42424 (synthetic) '+' '.join(tail));(proc/'stat').chmod(0o600)
        argv=(str(plan.chrome_binary),'--remote-debugging-pipe','about:blank')
        (proc/'cmdline').write_bytes(('\0'.join(argv)+'\0').encode());(proc/'cmdline').chmod(0o600)
        (proc/'exe').symlink_to(plan.chrome_binary)
        original_open=os.open
        def kernel_open(path,*args,**kwargs):
            if path=='/proc/42424':path=str(proc)
            return original_open(path,*args,**kwargs)
        with mock.patch.object(b.os,'open',side_effect=kernel_open):
            observed=b.NativeBrowserOps().observe(SimpleNamespace(pid=42424,poll=lambda:None))
        self.assertEqual(observed,{'pid':42424,'start':17,'uid':os.geteuid(),'executable':str(plan.chrome_binary),
            'sha256':plan.chrome_sha256,'argv':argv,'alive':True})
        plan.chrome_binary.chmod(0o775)
        with self.assertRaises(b.BrowserError):self.browser_plan()
        plan.chrome_binary.chmod(0o700);plan.profile_dir.mkdir(mode=0o700)
        with self.assertRaises(b.BrowserError):self.browser_plan()
        plan.profile_dir.rmdir();plan.chrome_binary.write_bytes(b'not ELF');plan.chrome_binary.chmod(0o700)
        with self.assertRaises(b.BrowserError):b.BrowserPlan.check(self.access,chrome_binary=plan.chrome_binary,chrome_sha256=plan.chrome_sha256,profile_dir=plan.profile_dir)

    async def test_browser_owned_pipe_argv_environment_metadata_only(self):
        await self.start();b=self.browser_module();plan=self.browser_plan();ops=BrowserOps();ops.sha=plan.chrome_sha256
        self.browser=b.OwnedBrowser(plan,process_ops=ops);result=await self.browser.start()
        argv=ops.calls[0][1];env=ops.calls[0][2]
        self.assertIn('--remote-debugging-pipe',argv);self.assertIn('--user-data-dir='+str(plan.profile_dir),argv)
        self.assertEqual(argv[-1],'about:blank');self.assertNotIn(self.token,str(ops.calls))
        methods=[method for _,method,_ in ops.cdp]
        self.assertIn('Target.setAutoAttach',methods);self.assertIn('Fetch.enable',methods);self.assertIn('Page.navigate',methods)
        with self.subTest(autoattach='must consume owned attached event, not attach twice'):
            self.assertNotIn('Target.attachToTarget',methods)
            self.assertEqual([method for session,method,_ in ops.cdp if session=='owned-page' and method=='Fetch.enable'],['Fetch.enable'])
        self.assertLess(methods.index('Target.setAutoAttach'),methods.index('Fetch.enable'))
        self.assertLess(methods.index('Fetch.enable'),methods.index('Page.navigate'))
        attach=next(params for _,method,params in ops.cdp if method=='Target.setAutoAttach')
        self.assertEqual(attach,{'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':True})
        navigate=next(params for _,method,params in ops.cdp if method=='Page.navigate')
        self.assertEqual(navigate,{'url':self.access.origin+'/'})
        fetch=next(params for _,method,params in ops.cdp if method=='Fetch.enable')
        self.assertEqual(fetch,{'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
        self.assertNotIn(self.token,str(ops.cdp))
        for forbidden in ('--no-sandbox','--ignore-certificate-errors','--disable-web-security'):
            self.assertNotIn(forbidden,argv)
        self.assertFalse(result['live_verified']);self.assertNotIn(self.token,str(result))
        self.assertNotIn('LD_PRELOAD',env);self.assertEqual(env['HOME'],str(self.client_dir))
        await self.browser.close();self.assertTrue(self.browser.readback()['reaped'])
        for fd in ops.fds:
            with self.assertRaises(OSError):os.fstat(fd)
        # Attached wire may arrive after the create response. Navigation must
        # await the exact target's Fetch-ready event, not merely its session ID.
        late_plan=b.BrowserPlan.check(self.access,chrome_binary=plan.chrome_binary,chrome_sha256=plan.chrome_sha256,profile_dir=self.client_dir/'late-autoattach-profile')
        late=BrowserOps();late.sha=plan.chrome_sha256;late.late_attach=True;late.fetch_gate=asyncio.Event()
        self.browser=b.OwnedBrowser(late_plan,process_ops=late);starting=asyncio.create_task(self.browser.start())
        for _ in range(50):
            if any(method=='Fetch.enable' for _,method,_ in late.cdp):break
            await asyncio.sleep(.005)
        self.assertFalse(any(method=='Page.navigate' for _,method,_ in late.cdp))
        late.fetch_gate.set();self.assertEqual((await starting)['status'],'connected')
        self.assertFalse(any(method=='Target.attachToTarget' for _,method,_ in late.cdp))
        await self.browser.close()
        # The lower CDP peer emits a newly autoattached service worker whose
        # Fetch domain fails. Startup must not overwrite pending with connected.
        failed=b.BrowserPlan.check(self.access,chrome_binary=plan.chrome_binary,chrome_sha256=plan.chrome_sha256,profile_dir=self.client_dir/'failed-fetch-profile')
        rejected=BrowserOps();rejected.sha=plan.chrome_sha256;rejected.fail_worker=True
        self.browser=b.OwnedBrowser(failed,process_ops=rejected)
        rejected.bind_events(self.browser._event)
        with self.assertRaises(b.BrowserError) as error:await self.browser.start()
        self.safe(error.exception);self.assertNotEqual(self.browser.readback()['status'],'connected')
        self.assertTrue(self.browser.readback()['reaped'])

    async def test_browser_foreign_identity_never_killed_or_adopted(self):
        await self.start();b=self.browser_module();plan=self.browser_plan();ops=BrowserOps();ops.sha=plan.chrome_sha256
        self.browser=b.OwnedBrowser(plan,process_ops=ops);await self.browser.start();ops.foreign=True
        with self.assertRaises(b.BrowserError) as error:await self.browser.close()
        self.safe(error.exception);self.assertFalse(any(call[0]=='terminate' for call in ops.calls))
        self.assertFalse(self.browser.readback()['reaped']);ops.foreign=False
        held=self.client_dir/'held-profile';plan.profile_dir.rename(held);plan.profile_dir.mkdir(mode=0o700)
        with self.assertRaises(b.BrowserError):await self.browser.close()
        self.assertFalse(any(call[0]=='terminate' for call in ops.calls))
        plan.profile_dir.rmdir();held.rename(plan.profile_dir)

    async def test_browser_failed_spawn_cancel_unknown_no_secret_or_retry(self):
        await self.start();b=self.browser_module();plan=self.browser_plan();ops=BrowserOps();ops.sha=plan.chrome_sha256
        def fail(*args,**kwargs):raise RuntimeError('SYNTHETIC_PRIVATE_SECRET '+self.token)
        ops.spawn=fail;self.browser=b.OwnedBrowser(plan,process_ops=ops)
        with self.assertRaises(b.BrowserError) as error:await self.browser.start()
        self.safe(error.exception);self.assertNotIn('SYNTHETIC_PRIVATE_SECRET',str(error.exception))
        with self.assertRaises(b.BrowserError):await self.browser.start()
        self.assertFalse(self.browser.readback()['reaped'])
        # An actual blocked worker boundary is cancelled; no Chrome is run.
        self.browser=None
        entered=threading.Event();release=threading.Event();other=BrowserOps();other.sha=plan.chrome_sha256
        spawn=other.spawn
        def blocked(*args,**kwargs):
            entered.set();release.wait(2);return spawn(*args,**kwargs)
        other.spawn=blocked
        # Failed launch owns its new profile; a separate fresh private plan is required.
        alternate=b.BrowserPlan.check(self.access,chrome_binary=plan.chrome_binary,chrome_sha256=plan.chrome_sha256,
            profile_dir=self.client_dir/'cancel-profile')
        self.browser=b.OwnedBrowser(alternate,process_ops=other)
        task=asyncio.create_task(self.browser.start())
        self.assertTrue(await asyncio.to_thread(entered.wait,1))
        task.cancel();release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.browser.readback()['reaped'])
        self.assertEqual([call[0] for call in other.calls].count('terminate'),1)
        self.assertEqual([call[0] for call in other.calls].count('wait'),1)
        with self.assertRaises(b.BrowserError):await self.browser.start()
        # Cancellation during owned shutdown still joins the dispatched wait
        # and records observed reaping before propagating cancellation.
        profile=self.client_dir/'shutdown-profile'
        shutdown=b.BrowserPlan.check(self.access,chrome_binary=plan.chrome_binary,chrome_sha256=plan.chrome_sha256,profile_dir=profile)
        last=BrowserOps();last.sha=plan.chrome_sha256
        waiting=threading.Event();finished=threading.Event();wait=last.wait
        def blocked_wait(*args,**kwargs):
            waiting.set();finished.wait(2);return wait(*args,**kwargs)
        last.wait=blocked_wait;self.browser=b.OwnedBrowser(shutdown,process_ops=last)
        await self.browser.start();task=asyncio.create_task(self.browser.close())
        self.assertTrue(await asyncio.to_thread(waiting.wait,1));task.cancel();finished.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.browser.readback()['reaped'])
