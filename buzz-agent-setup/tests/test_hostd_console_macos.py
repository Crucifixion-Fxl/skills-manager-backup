"""Mac client contracts + genuine isolated Linux SSH/UDS/Chrome transport.

The real transport test deliberately does not attest macOS/codesign/owner GUI.
No live SSH identity, hostd data, socket or token is used.
"""
import asyncio
import json
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd import console_macos as m
from hostd.console import ConsoleServer
from hostd.store import Store, BindingRecord


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.origin = 'http://127.0.0.1:56789'
        self.request = {'url': self.origin + '/api/graph', 'method': 'GET', 'headers': {}}

    def test_exact_scoped_auth_and_same_origin_rewrite(self):
        self.request.update(method='POST', url=self.origin + '/api/bindings/a/pause',
                            headers={'Origin': self.origin, 'Content-Type': 'application/json',
                                     'X-Hostd-Request': '1', 'Authorization': 'foreign', 'Sec-Fetch-Site': 'same-origin'})
        result = {v['name']: v['value'] for v in m.guarded_headers(self.origin, self.request, 'synthetic')}
        self.assertEqual(result['authorization'], 'Bearer synthetic')
        self.assertNotIn('host', result)
        self.assertEqual(result['origin'], self.origin)

    def test_cross_site_headers_routes_methods_and_origins_fail_closed(self):
        negatives = [dict(url=x) for x in ('https://evil.invalid/', self.origin + '.evil.invalid/',
                    self.origin + '/api/graph?token=synthetic', self.origin + '/elsewhere',
                    'http://user@127.0.0.1:56789/api/graph', self.origin + '/api/%67raph')]
        negatives += [dict(method='PUT'), dict(method='POST')]
        negatives += [dict(headers=h) for h in ({'Origin': 'https://evil.invalid'},
                      {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Dest': 'iframe'},
                      {'Authorization': 'a', 'authorization': 'b'}, {'X-Test': 'bad\r\nsecret'},
                      {'Proxy-Authorization': 'secret'})]
        for change in negatives:
            with self.subTest(keys=tuple(change)):
                with self.assertRaises(m.ClientError):
                    m.guarded_headers(self.origin, dict(self.request, **change), 'synthetic')

    def test_ssh_trust_and_one_connection_plan(self):
        argv = m.ssh_argv(56789, host='hostd.example.test')
        for option in ('StrictHostKeyChecking=yes', 'ExitOnForwardFailure=yes', 'ForwardAgent=no', 'ControlMaster=no'):
            self.assertIn(option, argv)
        self.assertIn('127.0.0.1:56789:/run/user/1009/buzz-hostd/console.sock', argv)
        self.assertEqual(argv[-2], 'hostd.example.test')
        self.assertNotIn('StrictHostKeyChecking=no', argv)
        self.assertNotIn('console.token', '\n'.join(argv[:-1]))

    def test_explicit_host_rejects_ssh_option_and_argument_injection(self):
        for host in ('-oProxyCommand=x', 'user@host', 'host x', 'host\nother', '', None):
            with self.subTest(host=host), self.assertRaises(m.ClientError):
                m.ssh_argv(56789, host=host)

    def test_non_mac_cli_cannot_claim_mac_acceptance(self):
        with mock.patch.object(m.sys, 'platform', 'linux'):
            with self.assertRaises(m.ClientError):m.checked_chrome()


class HelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'; self.runtime.mkdir(mode=0o700)
        self.token = 'S' * 43
        (self.runtime / 'console.token').write_text(self.token)
        (self.runtime / 'console.token').chmod(0o600)
        self.sock = socket.socket(socket.AF_UNIX); self.sock.bind(str(self.runtime / 'console.sock'))
        (self.runtime / 'console.sock').chmod(0o600)
        self.remote = None

    async def asyncTearDown(self):
        if self.remote:self.assertTrue(await self.remote.close())
        self.sock.close();self.tmp.cleanup()

    async def start(self, uid=None):
        self.remote = m.Remote([sys.executable, '-I', '-c', m.REMOTE_HELPER],
                               uid=os.geteuid() if uid is None else uid, runtime=str(self.runtime))
        await self.remote.start()
        return self.remote

    async def test_actual_helper_eof_and_secret_stays_memory_only(self):
        remote = await self.start()
        self.assertEqual(await remote.fresh(), self.token)
        self.assertNotIn(self.token, repr(remote))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['runtime'])
        self.assertTrue(await remote.close());self.remote = None

    async def test_actual_token_rotation_invalidates_without_retry(self):
        remote = await self.start()
        (self.runtime / 'console.token').write_text('T' * 43)
        await asyncio.wait_for(remote.invalid.wait(), 2)
        with self.assertRaises(m.ClientError):await remote.fresh()
        self.assertIsNone(remote.token)

    async def test_actual_socket_replacement_invalidates(self):
        remote = await self.start()
        (self.runtime / 'console.sock').unlink()
        replacement = socket.socket(socket.AF_UNIX)
        try:
            replacement.bind(str(self.runtime / 'console.sock'));(self.runtime / 'console.sock').chmod(0o600)
            await asyncio.wait_for(remote.invalid.wait(), 2)
            with self.assertRaises(m.ClientError):await remote.fresh()
        finally:replacement.close()

    async def test_bad_uid_permissions_and_symlinks_never_release_token(self):
        for mode in ('uid', 'runtime', 'token', 'symlink'):
            with self.subTest(mode=mode):
                if mode == 'runtime':self.runtime.chmod(0o755)
                if mode == 'token':(self.runtime / 'console.token').chmod(0o644)
                if mode == 'symlink':
                    (self.runtime / 'console.token').rename(self.runtime / 'private')
                    (self.runtime / 'console.token').symlink_to('private')
                with self.assertRaises(m.ClientError):await self.start(os.geteuid()+1 if mode=='uid' else None)
                self.assertIsNone(self.remote.token)
                self.assertTrue(await self.remote.close());self.remote=None
                self.runtime.chmod(0o700)
                if mode == 'symlink':
                    (self.runtime / 'console.token').unlink();(self.runtime / 'private').rename(self.runtime / 'console.token')
                (self.runtime / 'console.token').chmod(0o600)


@unittest.skipUnless(sys.platform == 'linux' and Path('/usr/sbin/sshd').exists()
                     and Path('/opt/google/chrome/chrome').exists(), 'Linux transport tools unavailable')
class GenuineSSHChromeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='.hostd-macos-ssh-test-',dir=Path.home());self.root=Path(self.tmp.name)
        self.store=Store(self.root/'state'/'db')
        self.store.reconcile_bindings([BindingRecord('alpha','channel_alpha','oc_alpha','cli_alpha','/private/env','/private/config','/private/data',mirror_pubkey='d'*64)],now=100)
        self.server=ConsoleServer(self.store,self.root/'runtime',heartbeat_interval=.2)
        await self.server.start()
        self.remote=None;self.browser=None;self.proxy=None;self.sshd=None
        for name in ('host','client'):
            result=subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.root/name)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode,0)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.sshport=s.getsockname()[1]
        self.known=self.root/'known_hosts'
        # This public key is generated by this exact isolated test server,
        # not taken from unauthenticated network discovery.
        self.known.write_text('[127.0.0.1]:'+str(self.sshport)+' '+(self.root/'host.pub').read_text())
        self.known.chmod(0o600)
        config=self.root/'sshd_config'
        config.write_text('\n'.join(['Port '+str(self.sshport),'ListenAddress 127.0.0.1',
            'HostKey '+str(self.root/'host'),'PidFile '+str(self.root/'sshd.pid'),
            'AuthorizedKeysFile '+str(self.root/'client.pub'),'StrictModes yes','UsePAM no',
            'PasswordAuthentication no','KbdInteractiveAuthentication no','AllowStreamLocalForwarding yes',
            'AllowTcpForwarding yes','LogLevel ERROR'])+'\n')
        self.sshd=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if self.sshd.poll() is not None:self.fail('isolated sshd exited, code '+str(self.sshd.returncode))
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',self.sshport);writer.close();await writer.wait_closed();break
            except OSError:await asyncio.sleep(.01)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.localport=s.getsockname()[1]
        extra=('-F','/dev/null','-p',str(self.sshport),'-i',str(self.root/'client'),
               '-o','UserKnownHostsFile='+str(self.known),'-o','GlobalKnownHostsFile=/dev/null',
               '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes')
        self.argv=m.ssh_argv(self.localport,host='127.0.0.1',user=pwd.getpwuid(os.geteuid()).pw_name,
                  remote_python=sys.executable,runtime=str(self.server.runtime_dir),extra=extra)

    async def asyncTearDown(self):
        if self.browser:self.assertTrue(await self.browser.close())
        if self.proxy:await self.proxy.close()
        if self.remote:self.assertTrue(await self.remote.close())
        if self.sshd:
            self.sshd.terminate();self.sshd.wait(timeout=5);self.sshd.stderr.close()
        await self.server.close();self.store.close();self.tmp.cleanup()

    async def test_real_ssh_unix_forward_and_owned_chrome_counts(self):
        self.remote=m.Remote(self.argv,uid=os.geteuid(),runtime=str(self.server.runtime_dir))
        await self.remote.start()
        self.proxy=m.Loopback(self.remote,self.localport);await self.proxy.start()
        origin=self.proxy.origin
        self.browser=m.Browser('/opt/google/chrome/chrome',self.root/'profile',origin,self.remote,
                               extra=('--headless=new','--disable-gpu'))
        await self.browser.start()
        evidence=await self.browser.verify()
        self.assertTrue(evidence['page']);self.assertEqual(evidence['graph_status'],200)
        self.assertGreater(evidence['nodes'],0)
        # An unrelated native HTTP client has no bearer and cannot use the URL.
        reader,writer=await asyncio.open_connection('127.0.0.1',self.localport)
        writer.write(b'GET /api/graph HTTP/1.1\r\nHost: hostd.local\r\nConnection: close\r\n\r\n');await writer.drain()
        self.assertIn(b' 401 ',await reader.readline());writer.close();await writer.wait_closed()
        # A new target is guarded before navigation; no external server sees
        # the request, even though it is reachable from this process.
        hits=[];outside_tasks=set()
        async def outside(reader,writer):
            task=asyncio.current_task();outside_tasks.add(task)
            try:
                # Chrome may preconnect TCP before Fetch interception. The
                # invariant is zero HTTP bytes/credentials, not zero SYNs.
                try:data=await asyncio.wait_for(reader.read(1),.5)
                except asyncio.TimeoutError:data=b''
                if data:hits.append(True)
            finally:
                writer.close();await writer.wait_closed();outside_tasks.discard(task)
        foreign=await asyncio.start_server(outside,'127.0.0.1',0)
        try:
            cdp=self.browser.cdp
            target=await cdp.command('Target.createTarget',{'url':'about:blank'})
            state=self.browser.targets.setdefault(target['targetId'],{'ready':asyncio.Event()})
            await asyncio.wait_for(state['ready'].wait(),3)
            denied=await cdp.command('Page.navigate',{'url':'http://127.0.0.1:'+str(foreign.sockets[0].getsockname()[1])+'/'},state['session'])
            self.assertIn('errorText',denied)
            foreign.close();await foreign.wait_closed()
            await asyncio.gather(*outside_tasks);self.assertEqual(hits,[])
            await cdp.command('Target.closeTarget',{'targetId':target['targetId']})
        finally:foreign.close();await foreign.wait_closed()
        # Closing the exact Console target is observed even with browser process alive.
        await self.browser.cdp.command('Target.closeTarget',{'targetId':self.browser.page})
        await asyncio.wait_for(self.browser.closed.wait(),3)
        # Rotation is detected over the same authenticated SSH process.
        (self.server.runtime_dir/'console.token').write_text('R'*43)
        await asyncio.wait_for(self.remote.invalid.wait(),2)
        with self.assertRaises(m.ClientError):await self.remote.fresh()
        print(json.dumps({'transport':'real_Linux_SSH_UDS_Chrome','mac_acceptance':False,**evidence}))

    async def test_console_window_close_drains_actual_browser_and_ssh_driver(self):
        real_browser,real_remote=m.Browser,m.Remote
        holders={};closing=[]
        def browser(binary,profile,origin,remote):
            value=real_browser(binary,profile,origin,remote,extra=('--headless=new','--disable-gpu'))
            verify=value.verify
            async def observed():
                evidence=await verify()
                # Close only the owned actual page after successful readback.
                closing.append(asyncio.create_task(value.cdp.command('Target.closeTarget',{'targetId':value.page})))
                return evidence
            value.verify=observed;holders['browser']=value;return value
        def remote(argv):
            value=real_remote(argv,uid=os.geteuid(),runtime=str(self.server.runtime_dir))
            holders['remote']=value;return value
        def argv(port,**kw):
            result=list(self.argv)
            result[result.index('-L')+1]='127.0.0.1:'+str(port)+':'+str(self.server.runtime_dir/'console.sock')
            return result
        output=io.StringIO()
        with mock.patch.object(m,'checked_chrome',return_value='/opt/google/chrome/chrome'),mock.patch.object(m,'ssh_argv',side_effect=argv),mock.patch.object(m,'Browser',side_effect=browser),mock.patch.object(m,'Remote',side_effect=remote),redirect_stdout(output):
            await asyncio.wait_for(m.run(SimpleNamespace(chrome='fixture',host='hostd.example.test',identity_file=None,verify_only=False)),20)
        await asyncio.gather(*closing)
        packets=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([v['status'] for v in packets],['verified','closed'])
        self.assertTrue(packets[-1]['browser_reaped']);self.assertTrue(packets[-1]['ssh_reaped'])
        self.assertTrue(holders['browser'].reaped);self.assertIsNotNone(holders['remote'].process.returncode)
        self.assertFalse(holders['browser'].profile.parent.exists())
        self.assertNotIn(self.server.token_path.read_text().strip(),output.getvalue())

    async def test_unknown_host_key_is_rejected_without_helper_packet(self):
        self.known.write_text('')
        self.remote=m.Remote(self.argv,uid=os.geteuid(),runtime=str(self.server.runtime_dir))
        with self.assertRaises(m.DiagnosticError) as raised:await self.remote.start()
        self.assertIsNone(self.remote.token)
        self.assertEqual(raised.exception.receipt(), {'status':'failed','stage':'ssh_start',
            'code':'host_trust_rejected','process_exit':255})

    async def test_rejected_actual_ssh_auth_is_classified_without_stderr(self):
        (self.root/'client.pub').write_text('')
        self.remote=m.Remote(self.argv,uid=os.geteuid(),runtime=str(self.server.runtime_dir))
        with self.assertRaises(m.DiagnosticError) as raised:await self.remote.start()
        self.assertIsNone(self.remote.token)
        self.assertEqual(raised.exception.receipt(), {'status':'failed','stage':'ssh_start',
            'code':'authentication_rejected','process_exit':255})

class LoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class SyntheticRemote:
            token='Z'*43
            invalid=asyncio.Event()
            calls=0
            async def fresh(self):
                self.calls+=1
                if self.invalid.is_set():raise m.ClientError()
                return self.token
        self.remote=SyntheticRemote();self.requests=[];self.response=b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'
        self.upstream=await asyncio.start_server(self.receive,'127.0.0.1',0)
        self.proxy=m.Loopback(self.remote,self.upstream.sockets[0].getsockname()[1]);await self.proxy.start()

    async def receive(self,reader,writer):
        try:
            head=await reader.readuntil(b'\r\n\r\n');self.requests.append(head)
            if self.response:writer.write(self.response);await writer.drain()
        finally:writer.close();await writer.wait_closed()

    async def asyncTearDown(self):
        await self.proxy.close();self.upstream.close();await self.upstream.wait_closed()

    async def request(self,*,method='GET',path='/api/graph',headers=(),authorized=True,body=b''):
        fields=[('Host',self.proxy.origin[7:])]
        if authorized:fields.append(('Authorization','Bearer '+self.remote.token))
        fields+=headers
        reader,writer=await asyncio.open_connection('127.0.0.1',int(self.proxy.origin.rsplit(':',1)[1]))
        writer.write((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields)+'\r\n').encode()+body)
        await writer.drain();reply=await reader.read();writer.close();await writer.wait_closed();return reply

    async def test_anonymous_never_forwarded_and_valid_auth_rewrites(self):
        self.assertIn(b' 403 ',await self.request(authorized=False));self.assertEqual(self.requests,[])
        self.assertIn(b' 200 ',await self.request());self.assertEqual(len(self.requests),1)
        self.assertIn(b'host: hostd.local\r\n',self.requests[0]);self.assertEqual(self.remote.calls,2)

    async def test_duplicates_smuggling_cross_site_body_and_frame_rejected_before_forward(self):
        cases=[{'headers': [('Host','evil.invalid')]},{'headers':[('Content-Length','0'),('content-length','0')]},
               {'headers':[('Transfer-Encoding','chunked')]},{'headers':[('Content-Length','1025')]},
               {'headers':[('Content-Length','1')],'body':b'x'}, {'headers':[('Origin','https://evil.invalid')]},
               {'headers':[('Sec-Fetch-Site','cross-site')]},{'headers':[('Sec-Fetch-Dest','iframe')]},
               {'headers':[('Connection','upgrade')]},{'headers':[('Expect','100-continue')]},
               {'method':'POST'},{'method':'OPTIONS'},{'path':'/api/graph?x=1'},
               {'headers':[('Bad','x'*2049)]}]
        for case in cases:
            with self.subTest(keys=tuple(case)):
                self.assertIn(b' 403 ',await self.request(**case))
        self.assertEqual(self.requests,[]);self.assertEqual(self.remote.calls,0)

    async def test_post_unknown_is_sent_once_and_never_retried(self):
        self.response=b''
        reply=await self.request(method='POST',path='/api/bindings/a/pause',headers=[
            ('Origin',self.proxy.origin),('Content-Type','application/json'),('X-Hostd-Request','1'),('Content-Length','2')],body=b'{}')
        self.assertIn(b' 403 ',reply);self.assertEqual(len(self.requests),1)
        self.assertIn(b'origin: http://hostd.local\r\n',self.requests[0])
        await asyncio.sleep(.02);self.assertEqual(len(self.requests),1)

    async def test_redirect_cookie_cors_and_token_response_never_forwarded(self):
        for headers,body in ((b'Location: https://evil.invalid\r\n',b''),
                             (b'Set-Cookie: session=bad\r\n',b''),
                             (b'Access-Control-Allow-Origin: *\r\n',b''),
                             (b'',self.remote.token.encode())):
            self.response=b'HTTP/1.1 200 OK\r\n'+headers+b'Content-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
            reply=await self.request();self.assertIn(b' 403 ',reply);self.assertNotIn(self.remote.token.encode(),reply)


class CDPFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_pipe_write_does_not_leak_pending_future(self):
        child_read,parent_write=os.pipe();parent_read,child_write=os.pipe()
        async def event(value):pass
        cdp=m.CDP(parent_read,parent_write,event)
        try:
            with mock.patch.object(cdp,'write',side_effect=OSError('synthetic-secret')):
                with self.assertRaises(OSError):await cdp.command('Browser.getVersion',{})
            self.assertEqual(cdp.pending,{})
        finally:
            await cdp.close();os.close(child_read);os.close(child_write)


class DiagnosticTests(unittest.TestCase):
    def test_fixed_diagnostic_is_metadata_only_and_has_stage_exit(self):
        error = m.DiagnosticError('chrome_validation', 'signature_rejected', process_exit=1)
        self.assertEqual(error.receipt(), {'status': 'failed', 'stage': 'chrome_validation',
                                          'code': 'signature_rejected', 'process_exit': 1})
        self.assertEqual(error.exit_status, 10)
        self.assertEqual(str(error), m.NOTICE)
        with self.assertRaises(ValueError):m.DiagnosticError('raw-secret', 'failed')
        with self.assertRaises(ValueError):m.DiagnosticError('ssh_start', 'credential-body')

    def test_codesign_failure_reports_fixed_reason_not_raw_output(self):
        with tempfile.TemporaryDirectory(prefix='.hostd-mac-validation-', dir=Path.home()) as folder:
            binary = Path(folder) / 'Chrome.app' / 'Contents' / 'MacOS' / 'Chrome'
            binary.parent.mkdir(parents=True)
            for parent in (binary.parent, binary.parent.parent, binary.parent.parent.parent):
                parent.chmod(0o700)
            binary.write_bytes(b'\xcf\xfa\xed\xfe' + b'synthetic executable')
            binary.chmod(0o700)
            output = io.StringIO()
            with mock.patch.object(m.sys, 'platform', 'darwin'), mock.patch.object(
                    m.subprocess, 'run', return_value=SimpleNamespace(returncode=1,
                    stdout=b'private token fixture', stderr=b'private token fixture')) as codesign, \
                    mock.patch('sys.stderr', output):
                result = m.main(['--host', 'hostd.example.test', '--chrome', str(binary)])
            self.assertEqual(result, 10)
            command = codesign.call_args.args[0]
            self.assertEqual(command[:5], ['/usr/bin/codesign', '--verify', '--strict', '--deep', '-R'])
            self.assertEqual(command[5], '=identifier \"com.google.Chrome\" and anchor apple generic and certificate leaf[subject.OU] = \"EQHXZ8M8AV\"')
            self.assertEqual(command[6:], [str(binary.parents[2])])
            self.assertEqual(codesign.call_args.kwargs['stderr'], subprocess.DEVNULL)
            packets = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
            self.assertEqual(packets[0]['stage'], 'chrome_validation')
            self.assertEqual(packets[0]['code'], 'signature_rejected')
            self.assertEqual(packets[0]['process_exit'], 1)
            self.assertNotIn('private token fixture', output.getvalue())
            self.assertNotIn(str(binary), output.getvalue())

    @unittest.skipUnless(sys.platform == 'darwin', 'requires actual Apple requirement parser')
    def test_native_apple_parser_accepts_exact_inline_chrome_requirement(self):
        # Compile/print only: no signing, bundle modification or SSH connection.
        requirement = '=identifier "com.google.Chrome" and anchor apple generic and certificate leaf[subject.OU] = "EQHXZ8M8AV"'
        result = subprocess.run(['/usr/bin/csreq', '-r', requirement, '-t'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=20)
        self.assertEqual(result.returncode, 0)

    def test_group_writable_binary_still_rejected_before_codesign(self):
        with tempfile.TemporaryDirectory(prefix='.hostd-mac-mode-', dir=Path.home()) as folder:
            binary = Path(folder) / 'Chrome'
            binary.write_bytes(b'\xcf\xfa\xed\xfe')
            binary.chmod(0o770)
            with mock.patch.object(m.sys, 'platform', 'darwin'), mock.patch.object(m.subprocess, 'run') as codesign:
                with self.assertRaises(m.DiagnosticError) as raised:m.checked_chrome(binary)
            self.assertEqual(raised.exception.code, 'untrusted_permissions')
            codesign.assert_not_called()

    def test_ssh_stderr_classification_never_returns_original_text(self):
        for text, category in [(b'Host key verification failed. SECRET', 'host_trust_rejected'),
                               (b'Permission denied (publickey). SECRET', 'authentication_rejected'),
                               (b'bind: Address already in use SECRET', 'forward_rejected'),
                               (b'Connection refused SECRET', 'connection_failed'),
                               (b'unknown credential-bearing failure SECRET', 'failed')]:
            self.assertEqual(m.ssh_failure_code(text), category)


class DiagnosticStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_wrapper_discards_private_exception_text(self):
        async def bad():raise RuntimeError('synthetic-private-CDP-response')
        with self.assertRaises(m.DiagnosticError) as raised:
            await m.checked_step('chrome_cdp', bad())
        self.assertEqual(raised.exception.receipt(), {'status': 'failed', 'stage': 'chrome_cdp', 'code': 'failed'})
        self.assertNotIn('synthetic-private', str(raised.exception))

    async def test_stage_wrapper_preserves_precise_failure(self):
        async def bad():raise m.DiagnosticError('ssh_start','host_trust_rejected',process_exit=255)
        with self.assertRaises(m.DiagnosticError) as raised:
            await m.checked_step('chrome_cdp', bad())
        self.assertEqual(raised.exception.receipt()['stage'], 'ssh_start')
        self.assertEqual(raised.exception.receipt()['process_exit'], 255)

    @unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/false').exists(), 'Linux process fixture')
    async def test_actual_early_child_exit_reports_cdp_failure_and_reaps(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = m.Browser('/usr/bin/false', Path(folder)/'profile', 'http://127.0.0.1:51234', None)
            try:
                with self.assertRaises(m.DiagnosticError) as raised:await browser.start()
                self.assertEqual(raised.exception.stage, 'chrome_cdp')
            finally:self.assertTrue(await browser.close())
            self.assertTrue(browser.reaped)


if __name__=='__main__':unittest.main()
