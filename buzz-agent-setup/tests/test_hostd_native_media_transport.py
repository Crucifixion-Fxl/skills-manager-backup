"""PRIVATE AST-only proposal; no imports/collection authorized yet.

Actual child/SSL/native HTTPS and synthetic own crypto. Only low connection or
explicit offline child command seams; no transport-mode/verifier success fake.
"""
import asyncio
import base64
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]
import test_hostd_agent_signed_reads as own
import buzz_feishu_group_sync as gs
import recovery_authority as authority

PNG = b'\x89PNG\r\n\x1a\n' + b'SYNTHETIC_MEDIA_ONLY'
MAX_BODY = 10_000_000
MAX_OUTPUT = 4 * ((MAX_BODY + 2) // 3) + 4096


def sha(raw): return hashlib.sha256(raw).hexdigest()


class MediaBase(own.Base):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hostd.agent_media'),
            'missing real own-key native binary child; query 1MiB budget must not expand')
        return importlib.import_module('hostd.agent_media')

    def packet(self, raw=PNG, *, origin=own.ORIGIN):
        digest = sha(raw)
        return dict(version=1, operation='media_get', origin=origin, key=own.KEY,
            pin=own.PIN, now=own.NOW, agent=own.PUB, owner=own.OWNER,
            auth_tag=own.auth_tag(), url=origin+'/media/'+digest+'.png',
            sha256=digest, mime='image/png', size=len(raw))

    def reader(self, *, origin=own.ORIGIN, **kwargs):
        return self.module().OwnAgentMediaReader(self.record, origin=origin,
            relay_pubkey=own.PIN, trusted_relays=(origin,), clock=lambda: own.NOW, **kwargs)

    def child(self, code):
        path = self.root / ('child-'+str(len(list(self.root.glob('child-*.py'))))+'.py')
        path.write_text('import os,sys,json,time,base64,hashlib,ssl\n'+code)
        path.chmod(0o600)
        return [sys.executable, str(path)]

    def success(self, raw=PNG):
        return dict(version=1,ok=True,mime='image/png',size=len(raw),sha256=sha(raw),
            data_b64=base64.b64encode(raw).decode('ascii'))

    def output_child(self, value):
        return self.child('json.load(sys.stdin)\nprint('+repr(json.dumps(value,separators=(',',':')))+')\n')

    def connection_child(self, *, size=len(PNG), status=200, headers=None, body=None,
                         read_sleep=0, extra_body=b''):
        """Script calls actual child_main; only HTTPSConnection/response is fake."""
        data = PNG if body is None and size==len(PNG) else body
        body_expr = repr(data) if data is not None else "b'\\x89PNG\\r\\n\\x1a\\n'+b'x'*("+str(size)+"-8)"
        hdrs = [('Content-Type','image/png'),('Content-Length',str(size))] if headers is None else headers
        return self.child("sys.path.insert(0,"+repr(str(SCRIPTS))+ ")\n"
            "from hostd.agent_media import child_main\n"
            "import buzz_feishu_group_sync as gs\nimport recovery_authority as a\n"
            "raw="+body_expr+"+"+repr(extra_body)+"\n"
            "class Response:\n status="+str(status)+"\n offset=0\n"
            " def getheaders(self): return "+repr(hdrs)+"\n"
            " def read(self,n):\n  assert type(n) is int and 0<n<=65536\n"
            "  time.sleep("+str(read_sleep)+")\n  answer=raw[self.offset:self.offset+n];self.offset+=len(answer);return answer\n"
            "class Connection:\n"
            " def __init__(self,host,port=None,*,timeout,context):\n"
            "  assert host=='127.0.0.1' and port==9443\n"
            "  assert 0<timeout<=10 and context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED\n"
            " def request(self,method,path,body=None,headers=None):\n"
            "  assert method=='GET' and body is None and path.startswith('/media/')\n"
            "  event=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))\n"
            "  assert gs._nip01_event_verified(event) and event['pubkey']=="+repr(own.PUB)+"\n"
            "  a.exact_tag(event,'u',"+repr(own.ORIGIN)+"+path);a.exact_tag(event,'method','GET')\n"
            "  assert not a.tags(event,'payload')\n"
            "  auth=json.loads(headers['x-auth-tag']);assert auth[2]==''\n"
            "  assert a.attested_owner({'tags':[auth]},event['pubkey'])=="+repr(own.OWNER)+"\n"
            " def getresponse(self): return Response()\n def close(self): pass\n"
            "child_main(connection_factory=Connection)\n")

    async def failed(self, awaitable):
        with self.assertRaises(self.module().AgentMediaFailure) as caught: await awaitable
        value=str(caught.exception)
        self.assertIn('怎么解决',value);self.assertIn('复制给 AI',value)
        for secret in (own.KEY, own.auth_tag()[3], 'SYNTHETIC_MEDIA_ONLY','Traceback'):
            self.assertNotIn(secret,value)

    def popen_observer(self, captures, entered=None):
        original=subprocess.Popen
        def spawn(argv, **kwargs):
            self.assertNotIn(own.KEY,repr(argv));self.assertNotIn(own.auth_tag()[3],repr(argv))
            self.assertTrue(kwargs['close_fds']);self.assertIs(kwargs['stderr'],subprocess.DEVNULL)
            self.assertIs(kwargs['stdin'],subprocess.PIPE);self.assertIs(kwargs['stdout'],subprocess.PIPE)
            env=kwargs['env'];self.assertEqual(set(env)-{'SSL_CERT_FILE'},{'PATH','LANG'})
            self.assertNotIn('HOME',env);self.assertNotIn('HTTPS_PROXY',env);self.assertNotIn('CANARY_SECRET',env)
            process=original(argv,**kwargs);captures.append(process)
            if entered: entered.set()
            return process
        return spawn

    def reaped(self, processes):
        self.assertTrue(processes)
        for process in processes:
            self.assertIsNotNone(process.poll())
            with self.assertRaises(ProcessLookupError): os.kill(process.pid,0)
            self.assertTrue(process.stdin.closed and process.stdout.closed)


class NativeMediaTests(MediaBase):
    async def test_control_signed_query_budget_and_parser_remain_separate(self):
        from hostd import signed_reads
        import recovery_read_budget
        self.assertEqual((signed_reads.MAX_OUTPUT,recovery_read_budget.MAX_BYTES),(1048576,1048576))
        from hostd.agent_signed_reads import AgentReadRequest, AgentReadFailure
        with self.assertRaises(AgentReadFailure): AgentReadRequest.from_data(self.packet())

    async def test_actual_own_signed_get_and_binary_body_above_query_budget(self):
        raw=b'\x89PNG\r\n\x1a\n'+b'x'*(1_048_577-8)
        self.assertEqual(await self.module().child_read(self.packet(raw),
            command=self.connection_child(size=len(raw))),raw)

    async def test_exact_ten_million_bytes_allowed_by_separate_protocol(self):
        raw=b'\x89PNG\r\n\x1a\n'+b'x'*(MAX_BODY-8)
        self.assertEqual(await self.module().child_read(self.packet(raw),
            command=self.connection_child(size=MAX_BODY)),raw)

    async def test_packet_identity_empty_oa_types_and_path_are_not_caller_authority(self):
        mod=self.module(); packet=self.packet();request=mod.AgentMediaRequest.from_data(packet)
        self.assertNotIn(own.KEY,repr(request));self.assertNotIn(own.auth_tag()[3],repr(request))
        updates=({'key':own.OWNER_KEY},{'agent':own.OWNER},{'owner':own.PUB},
            {'auth_tag':own.auth_tag('kind=9')},{'auth_tag':['auth',own.OWNER,'','0'*128]},
            {'size':True},{'size':0},{'size':MAX_BODY+1},{'now':False},{'now':2**63},
            {'url':packet['url']+'?token=x'},{'url':packet['url']+'#f'},
            {'url':packet['url'].replace('127.0.0.1','foreign.invalid')},
            {'url':packet['url'].replace('/media/','/media/../')},
            {'url':packet['url'].replace('.png','.jpg')},{'sha256':'A'*64},
            {'mime':'image/svg+xml'},{'operation':'query'},{'unknown':1})
        for update in updates:
            with self.subTest(field=next(iter(update))):
                with self.assertRaises(mod.AgentMediaFailure):mod.AgentMediaRequest.from_data(dict(packet,**update))

    async def test_private_stdin_minimal_environment_real_spawn_and_reap(self):
        captures=[]
        command=self.child("p=json.load(sys.stdin)\nassert 'CANARY_SECRET' not in os.environ\n"
            "assert p['key'] not in repr(sys.argv)\nprint("+repr(json.dumps(self.success()))+")\n")
        with mock.patch.dict(os.environ,{'CANARY_SECRET':'SYNTHETIC_NEVER_INHERIT','HTTPS_PROXY':'http://foreign.invalid'}):
            with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
                self.assertEqual(await self.module().child_read(self.packet(),command=command),PNG)
        self.reaped(captures)

    async def test_output_exact_typed_canonical_metadata_and_recomputed_body(self):
        good=self.success();mod=self.module()
        variants=({'ok':1},{'size':True},{'size':len(PNG)+1},{'sha256':'0'*64},
            {'mime':'image/jpeg'},{'data_b64':good['data_b64']+'\n'},
            {'data_b64':base64.b64encode(b'not-png').decode()},{'extra':'forbidden'})
        for update in variants:
            with self.subTest(field=next(iter(update))):
                await self.failed(mod.child_read(self.packet(),command=self.output_child(dict(good,**update))))
        command=self.child("json.load(sys.stdin)\nprint('{\"version\":1,\"ok\":false,\"ok\":true}')\n")
        await self.failed(mod.child_read(self.packet(),command=command))

    async def test_child_stdout_maximum_plus_one_is_killed_before_parse(self):
        captures=[];command=self.child("json.load(sys.stdin)\nsys.stdout.buffer.write(b'x'*"+str(MAX_OUTPUT+1)+")\nsys.stdout.flush()\ntime.sleep(30)\n")
        original_read=os.read;stdout_count=0
        def counted(fd,n):
            nonlocal stdout_count
            value=original_read(fd,n)
            if captures and not captures[0].stdout.closed and fd==captures[0].stdout.fileno():
                stdout_count+=len(value)
            return value
        started=time.monotonic()
        with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
            with mock.patch('os.read',side_effect=counted):
                await self.failed(self.module().child_read(self.packet(),command=command,outer_seconds=12))
        self.assertLess(time.monotonic()-started,10,'overflow must fail before outer timeout')
        self.assertLessEqual(stdout_count,MAX_OUTPUT+65536);self.reaped(captures)

    async def test_header_redirect_status_encoding_duplicate_and_length_never_follow(self):
        variants=((302,[('Location','https://foreign.invalid/'),('Content-Length',str(len(PNG)))]),
            (403,[('Content-Type','image/png'),('Content-Length',str(len(PNG)))]),
            (200,[('Content-Type','image/png'),('Content-Length',str(len(PNG))),('Content-Length','1')]),
            (200,[('Content-Type','image/png'),('Content-Length',str(len(PNG))),('Content-Encoding','gzip')]),
            (200,[('Content-Type','image/png'),('Transfer-Encoding','chunked')]),
            (200,[('Content-Type','image/jpeg'),('Content-Length',str(len(PNG)))]),
            (200,[('Content-Type','image/png'),('Content-Length',str(len(PNG)+1))]))
        for status,headers in variants:
            with self.subTest(status=status,headers=headers):
                await self.failed(self.module().child_read(self.packet(),
                    command=self.connection_child(status=status,headers=headers)))

    async def test_truncated_overflow_hash_and_magic_bodies_are_pending(self):
        for raw in (PNG[:-1],PNG+b'x',b'not-png'+PNG[7:],PNG[:8]+b'z'*(len(PNG)-8)):
            with self.subTest(size=len(raw)):
                await self.failed(self.module().child_read(self.packet(),command=self.connection_child(body=raw)))
        raw=b'\x89PNG\r\n\x1a\n'+b'x'*(MAX_BODY-8)
        await self.failed(self.module().child_read(self.packet(raw),
            command=self.connection_child(size=MAX_BODY,extra_body=b'x')))

    async def test_actual_child_hard_ten_second_deadline_restores_query_budget(self):
        captures=[];started=time.monotonic()
        with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
            await self.failed(self.module().child_read(self.packet(),command=self.connection_child(read_sleep=20)))
        self.assertGreaterEqual(time.monotonic()-started,9)
        self.assertLess(time.monotonic()-started,12.5);self.reaped(captures)
        self.assertEqual(captures[0].returncode,0,'hard child timer emitted fixed failure before parent kill')
        from hostd import signed_reads
        self.assertEqual(signed_reads.MAX_OUTPUT,1048576)

    async def test_outer_deadline_kills_and_reaps_noncooperative_child(self):
        captures=[];command=self.child('json.load(sys.stdin)\ntime.sleep(30)\n')
        started=time.monotonic()
        with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
            await self.failed(self.module().child_read(self.packet(),command=command,outer_seconds=.4))
        self.assertLess(time.monotonic()-started,2);self.reaped(captures)

    async def test_repeated_cancel_joins_original_dispatched_child(self):
        captures=[];entered=threading.Event()
        command=self.child('json.load(sys.stdin)\ntime.sleep(.4)\nprint('+repr(json.dumps(self.success()))+')\n')
        with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures,entered)):
            task=asyncio.create_task(self.module().child_read(self.packet(),command=command,outer_seconds=1))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,240))
                task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done())
                with self.assertRaises(asyncio.CancelledError): await task
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(len(captures),1);self.reaped(captures)

    async def test_nonzero_or_fixed_failure_child_never_returns_partial_bytes(self):
        for code in ('json.load(sys.stdin)\nsys.exit(7)\n',
            'json.load(sys.stdin)\nprint(\'{"version":1,"ok":false}\')\n'):
            await self.failed(self.module().child_read(self.packet(),command=self.child(code)))

    async def test_env_changed_or_unsafe_before_dispatch_never_spawns(self):
        self.module();reader=self.reader();packet=self.packet()
        with mock.patch('subprocess.Popen',side_effect=AssertionError('must fail before native spawn')):
            for alter in ('key','origin','permissions'):
                self.write_env()
                if alter=='key':self.write_env(key=own.OWNER_KEY)
                elif alter=='origin':self.write_env(relay='https://127.0.0.1:9555')
                else:self.env.chmod(0o644)
                await self.failed(reader.read_media(packet['url'],sha256=packet['sha256'],mime=packet['mime'],size=packet['size']))


class NativeMediaTLS(MediaBase):
    def setUp(self):
        super().setUp()
        binary=Path('/usr/bin/openssl')
        self.assertTrue(binary.is_file(),'fixture prerequisite: offline synthetic TLS certificate generator')
        cert=self.root/'ca.pem';key=self.root/'tls.key'
        subprocess.run([str(binary),'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
            '-out',str(cert),'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1',
            '-addext','basicConstraints=critical,CA:FALSE','-addext','extendedKeyUsage=serverAuth'],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,
            env={'PATH':os.defpath,'LANG':'C.UTF-8'})
        cert.chmod(0o600);key.chmod(0o600);self.cert=cert;self.received=[];outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                outer.received.append((self.path,dict(self.headers)))
                self.send_response(200);self.send_header('Content-Type','image/png')
                self.send_header('Content-Length',str(len(PNG)));self.end_headers()
                self.wfile.write(PNG+outer.extra_body)
        self.extra_body=b''
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start();self.addCleanup(self.stop)
        self.origin='https://127.0.0.1:'+str(self.server.server_port);self.write_env(relay=self.origin)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    async def test_native_default_actual_https_ca_and_own_get_authorization(self):
        mod=self.module();captures=[];packet=self.packet(origin=self.origin)
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.cert),'HTTPS_PROXY':'http://foreign.invalid'}):
            with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
                raw=await self.reader(origin=self.origin).read_media(packet['url'],
                    sha256=packet['sha256'],mime=packet['mime'],size=packet['size'])
        self.assertEqual(raw,PNG);self.reaped(captures)
        self.assertEqual(len(self.received),1);path,headers=self.received[0]
        headers={k.lower():v for k,v in headers.items()}
        event=json.loads(base64.b64decode(headers['authorization'].split(' ',1)[1]))
        self.assertTrue(gs._nip01_event_verified(event));self.assertEqual(event['pubkey'],own.PUB)
        authority.exact_tag(event,'u',self.origin+path);authority.exact_tag(event,'method','GET')
        self.assertFalse(authority.tags(event,'payload'));self.assertEqual(json.loads(headers['x-auth-tag']),own.auth_tag())
        self.assertEqual(Path(captures[0].args[1]).name,'agent_media.py')

    async def test_actual_tls_rejects_octet_after_declared_content_length(self):
        packet=self.packet(origin=self.origin);captures=[]
        self.extra_body=b'X'
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.cert)}):
            with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
                await self.failed(self.reader(origin=self.origin).read_media(packet['url'],
                    sha256=packet['sha256'],mime=packet['mime'],size=packet['size']))
        self.reaped(captures)
        self.assertEqual(len(self.received),1)
        self.assertEqual(self.received[0][0],'/media/'+sha(PNG)+'.png')

    async def test_ca_sealed_fd_rejects_mutation_before_actual_spawn(self):
        mod=self.module();packet=self.packet(origin=self.origin);original=subprocess.Popen;captures=[]
        def spawn(argv,**kwargs):
            fd=kwargs['pass_fds'][0];seals=fcntl.fcntl(fd,fcntl.F_GET_SEALS)
            required=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
            self.assertEqual(seals&required,required);self.assertEqual(os.fstat(fd).st_mode&0o777,0o600)
            self.assertEqual(kwargs['env']['SSL_CERT_FILE'],'/proc/self/fd/'+str(fd))
            with self.assertRaises(OSError):os.pwrite(fd,b'changed',0)
            self.cert.write_bytes(b'changed-after-seal')
            process=original(argv,**kwargs);captures.append(process);return process
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.cert)}):
            with mock.patch('subprocess.Popen',side_effect=spawn):
                self.assertEqual(await self.reader(origin=self.origin).read_media(packet['url'],
                    sha256=packet['sha256'],mime=packet['mime'],size=packet['size']),PNG)
        self.reaped(captures)

    async def test_unsafe_or_symlink_ca_fails_before_native_child(self):
        mod=self.module();packet=self.packet(origin=self.origin)
        link=self.root/'linked.pem';link.symlink_to(self.cert)
        for path in (link,self.cert):
            if path==self.cert:self.cert.chmod(0o644)
            with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(path)}):
                with mock.patch('subprocess.Popen',side_effect=AssertionError('unsafe CA must not spawn')):
                    await self.failed(self.reader(origin=self.origin).read_media(packet['url'],
                        sha256=packet['sha256'],mime=packet['mime'],size=packet['size']))
        self.assertFalse(self.received)

    async def test_cleanup_kill_race_is_sanitized_and_actual_child_is_reaped(self):
        mod=self.module();captures=[];proxies=[];original_popen=subprocess.Popen
        original_read=os.read;read_failed=False
        command=self.child('json.load(sys.stdin)\nprint('+repr(json.dumps(self.success()))+')\n')

        class ExitedChildRace:
            def __init__(self,process):
                self.process=process;self.did_race=False;self.kill_calls=0;self.wait_calls=0
            def __getattr__(self,name):return getattr(self.process,name)
            def poll(self):
                if not self.did_race:
                    self.did_race=True
                    self.process.wait(timeout=3)
                    return None  # stale pre-exit poll, then an actual exited-child kill race
                return self.process.poll()
            def kill(self):
                self.kill_calls+=1
                raise ProcessLookupError('SYNTHETIC_CLEANUP_CANARY')
            def wait(self,timeout=None):
                self.wait_calls+=1
                return self.process.wait(timeout=timeout)

        def spawn(argv,**kwargs):
            process=original_popen(argv,**kwargs);captures.append(process)
            proxy=ExitedChildRace(process);proxies.append(proxy);return proxy

        def fail_read(fd,n):
            nonlocal read_failed
            if captures and fd==captures[0].stdout.fileno() and not read_failed:
                read_failed=True
                raise OSError('SYNTHETIC_CLEANUP_CANARY')
            return original_read(fd,n)

        with mock.patch('subprocess.Popen',side_effect=spawn):
            with mock.patch('os.read',side_effect=fail_read):
                try:
                    await mod.child_read(self.packet(),command=command)
                except Exception as failure:
                    self.assertIsInstance(failure,mod.AgentMediaFailure)
                    self.assertIn('怎么解决',str(failure))
                    self.assertNotIn('SYNTHETIC_CLEANUP_CANARY',str(failure))
                else:
                    self.fail('cleanup race must return the fixed media failure')
        self.assertTrue(read_failed)
        self.assertEqual(proxies[0].kill_calls,1)
        self.assertGreaterEqual(proxies[0].wait_calls,1,
                                'cleanup must attempt actual wait after the exited-child kill race')
        self.reaped(captures)

    async def test_untrusted_tls_never_reaches_http_or_owner_fallback(self):
        mod=self.module();packet=self.packet(origin=self.origin);captures=[]
        with mock.patch.dict(os.environ,{},clear=True):
            with mock.patch('subprocess.Popen',side_effect=self.popen_observer(captures)):
                await self.failed(self.reader(origin=self.origin).read_media(packet['url'],
                    sha256=packet['sha256'],mime=packet['mime'],size=packet['size']))
        self.reaped(captures);self.assertFalse(self.received)
