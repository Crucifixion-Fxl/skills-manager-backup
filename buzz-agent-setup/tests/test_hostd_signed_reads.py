"""Native child protocol with real subprocesses; all transport remains offline."""
import asyncio
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
from unittest import mock
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class Protocol(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = '2'.zfill(64)
        self.payload = {'version': 1, 'origin': 'https://relay.test', 'key': self.key,
                        'pin': 'a' * 64, 'now': 1000, 'operation': 'query',
                        'filters': [{'kinds': [0], 'limit': 2}]}

    def child(self, body):
        p = self.root / 'child.py'
        p.write_text('import json,sys,time,os\n' + body)
        return [sys.executable, str(p)]

    async def test_private_stdin_and_minimal_child_environment(self):
        from hostd import signed_reads as reads
        command = self.child("p=json.load(sys.stdin)\nassert p['key'] not in str(sys.argv)\nassert 'PARENT_SECRET' not in os.environ\nprint(json.dumps({'ok':True,'value':[]}))\n")
        os.environ['PARENT_SECRET'] = 'CANARY_PRIVATE'; self.addCleanup(os.environ.pop, 'PARENT_SECRET', None)
        self.assertEqual(await reads.child_read(self.payload, command=command), [])
        self.assertNotIn(self.key, repr(reads.ReadRequest.from_data(self.payload)))

    async def test_bounded_output_kills_and_reaps_child(self):
        from hostd import signed_reads as reads
        command = self.child("json.load(sys.stdin)\nsys.stdout.write('x'*(1024*1024+1));sys.stdout.flush()\ntime.sleep(30)\n")
        started = time.monotonic()
        with self.assertRaises(reads.ReadFailure) as exc:
            await reads.child_read(self.payload, command=command)
        self.assertLess(time.monotonic() - started, 3)
        self.assertNotIn(self.key, str(exc.exception))
        self.assertIn('怎么解决', str(exc.exception)); self.assertIn('复制给 AI', str(exc.exception))

    async def test_outer_timeout_reaps_unresponsive_child(self):
        from hostd import signed_reads as reads
        command = self.child("json.load(sys.stdin)\ntime.sleep(30)\n")
        started = time.monotonic()
        with self.assertRaises(reads.ReadFailure):
            await reads.child_read(self.payload, command=command, outer_seconds=0.2)
        self.assertLess(time.monotonic() - started, 3)

    async def test_repeated_cancel_waits_for_actual_child_completion(self):
        from hostd import signed_reads as reads
        receipt = self.root / 'completed'
        command = self.child("json.load(sys.stdin)\ntime.sleep(.35)\nopen(" + repr(str(receipt)) + ",'w').write('reaped')\nprint(json.dumps({'ok':True,'value':[]}))\n")
        task = asyncio.create_task(reads.child_read(self.payload, command=command))
        await asyncio.sleep(.1); task.cancel(); await asyncio.sleep(.05); task.cancel()
        self.assertFalse(task.done()); self.assertFalse(receipt.exists())
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(receipt.read_text(), 'reaped')

    async def test_native_child_preserves_actual_ten_second_signal_deadline(self):
        from hostd import signed_reads as reads
        # Actual child main invokes the unmodified read_budget, with fake only
        # the lowest HTTP transport. This cannot contact any network endpoint.
        command = self.child("sys.path.insert(0," + repr(str(SCRIPTS)) + ")\nfrom hostd.signed_reads import child_main\ndef http(*a,**kw):\n time.sleep(12)\n return 200,b'[]'\nchild_main(http=http)\n")
        started = time.monotonic()
        with self.assertRaises(reads.ReadFailure): await reads.child_read(self.payload, command=command)
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, 9); self.assertLess(elapsed, 12)

    async def test_rejects_mutation_and_arbitrary_protocol_fields(self):
        from hostd import signed_reads as reads
        for update in ({'operation': 'publish'}, {'sql': 'DROP TABLE binding'},
                       {'filters': [{'url': 'https://attacker.test'}]}, {'now': True}):
            with self.subTest(update=update):
                with self.assertRaises(reads.ReadFailure): reads.ReadRequest.from_data(dict(self.payload, **update))

    async def test_malformed_child_receipt_fails_closed_without_raw_output(self):
        from hostd import signed_reads as reads
        for data in ('RAW_PRIVATE_EXCEPTION', '{"ok":true}', '{"ok":1,"value":[]}', '{"ok":true,"value":[],"key":"CANARY"}'):
            command = self.child('json.load(sys.stdin)\nprint(' + repr(data) + ')\n')
            with self.subTest(data=data):
                with self.assertRaises(reads.ReadFailure) as exc: await reads.child_read(self.payload, command=command)
                self.assertNotIn('RAW_PRIVATE_EXCEPTION', str(exc.exception)); self.assertNotIn('CANARY', str(exc.exception))


class TrustBundle(Protocol):
    # Do not inherit/repeat the seven native protocol tests.
    test_private_stdin_and_minimal_child_environment = None
    test_bounded_output_kills_and_reaps_child = None
    test_outer_timeout_reaps_unresponsive_child = None
    test_repeated_cancel_waits_for_actual_child_completion = None
    test_native_child_preserves_actual_ten_second_signal_deadline = None
    test_rejects_mutation_and_arbitrary_protocol_fields = None
    test_malformed_child_receipt_fails_closed_without_raw_output = None

    def setUp(self):
        super().setUp()
        import ssl
        source=ssl.get_default_verify_paths().openssl_cafile
        if not source or not Path(source).is_file():self.skipTest('stdlib TLS system roots unavailable')
        self.bundle=self.root/'private-roots.pem'
        self.bundle.write_bytes(Path(source).read_bytes());self.bundle.chmod(0o600)

    async def test_explicit_private_bundle_reaches_actual_native_ssl_context(self):
        from hostd import signed_reads as reads
        command=self.child("json.load(sys.stdin)\nimport ssl\nctx=ssl.create_default_context()\nassert ctx.verify_mode==ssl.CERT_REQUIRED and ctx.check_hostname\nprint(json.dumps({'ok':True,'value':[bool(os.environ.get('SSL_CERT_FILE'))]}))\n")
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.bundle),'HTTPS_PROXY':'PRIVATE_PROXY_CANARY'}):
            self.assertEqual(await reads.child_read(self.payload,command=command),[True])

    async def test_absent_bundle_keeps_standard_roots_without_extra_environment(self):
        from hostd import signed_reads as reads
        command=self.child("json.load(sys.stdin)\nimport ssl\nctx=ssl.create_default_context()\nassert ctx.verify_mode==ssl.CERT_REQUIRED and ctx.check_hostname\nprint(json.dumps({'ok':True,'value':[os.environ.get('SSL_CERT_FILE') is None,'HTTPS_PROXY' not in os.environ]}))\n")
        with mock.patch.dict(os.environ,{},clear=True):
            self.assertEqual(await reads.child_read(self.payload,command=command),[True,True])

    async def test_unsafe_explicit_bundle_fails_before_child_dispatch(self):
        from hostd import signed_reads as reads
        marker=self.root/'dispatched'
        command=self.child("json.load(sys.stdin)\nopen("+repr(str(marker))+",'w').write('unsafe')\nprint(json.dumps({'ok':True,'value':[]}))\n")
        link=self.root/'link.pem';link.symlink_to(self.bundle)
        parent_link=self.root/'parent-link';parent_link.symlink_to(self.root,target_is_directory=True)
        for path in (link,parent_link/self.bundle.name):
            with self.subTest(path=path),mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(path)}):
                with self.assertRaises(reads.ReadFailure):await reads.child_read(self.payload,command=command)
                self.assertFalse(marker.exists())
        self.bundle.chmod(0o644)
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.bundle)}):
            with self.assertRaises(reads.ReadFailure):await reads.child_read(self.payload,command=command)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(Path('/usr/bin/openssl').is_file(),'actual native loopback TLS fixture needs openssl')
    async def test_actual_child_signed_query_over_loopback_tls_private_leaf_bundle(self):
        from hostd import signed_reads as reads
        import ssl,base64
        import buzz_feishu_group_sync as gs
        cert=self.root/'leaf.pem';key=self.root/'leaf.key'
        await asyncio.to_thread(subprocess.run,['/usr/bin/openssl','req','-x509','-newkey','rsa:2048','-nodes',
            '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=127.0.0.1',
            '-addext','subjectAltName=IP:127.0.0.1','-addext','basicConstraints=critical,CA:FALSE'],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        key.chmod(0o600);cert.chmod(0o600)
        self.bundle.write_bytes(self.bundle.read_bytes()+cert.read_bytes());self.bundle.chmod(0o600)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        received=[]
        async def handler(reader,writer):
            try:
                head=await reader.readuntil(b'\r\n\r\n')
                headers=dict(line.decode().split(': ',1) for line in head.split(b'\r\n')[1:] if b': ' in line)
                body=await reader.readexactly(int(headers['Content-Length']))
                credential=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
                assert gs._nip01_event_verified(credential) and credential['pubkey']==gs._signer_pubkey(self.key)
                assert json.loads(body)==self.payload['filters']
                received.append(True)
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]');await writer.drain()
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0,ssl=context)
        try:
            payload=dict(self.payload,origin='https://127.0.0.1:'+str(server.sockets[0].getsockname()[1]))
            with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.bundle)}):
                self.assertEqual(await reads.child_read(payload),[])
            self.assertEqual(received,[True])
        finally:
            server.close();await server.wait_closed()

    async def test_foreign_owned_or_invalid_pem_bundle_is_not_dispatched(self):
        from hostd import signed_reads as reads
        import stat
        from types import SimpleNamespace
        marker=self.root/'dispatched'
        command=self.child("json.load(sys.stdin)\nopen("+repr(str(marker))+",'w').write('unsafe')\nprint(json.dumps({'ok':True,'value':[]}))\n")
        actual=os.fstat
        def foreign(fd):
            info=actual(fd)
            if stat.S_ISREG(info.st_mode):return SimpleNamespace(st_uid=os.geteuid()+1,st_mode=info.st_mode,st_size=info.st_size)
            return info
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.bundle)}),mock.patch('hostd.safety.os.fstat',side_effect=foreign):
            with self.assertRaises(reads.ReadFailure):await reads.child_read(self.payload,command=command)
            self.assertFalse(marker.exists())
        self.bundle.write_bytes(b'NOT_A_CERTIFICATE');self.bundle.chmod(0o600)
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.bundle)}):
            with self.assertRaises(reads.ReadFailure):await reads.child_read(self.payload,command=command)
            self.assertFalse(marker.exists())


if __name__ == '__main__': unittest.main()
