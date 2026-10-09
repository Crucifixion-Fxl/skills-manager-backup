"""Own-agent reads: synthetic credentials, raw signed wire and real local child/TLS."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import buzz_feishu_group_sync as gs
import recovery_authority as authority
KEY = '1'.zfill(64)
OWNER_KEY = '2'.zfill(64)
RELAY_KEY = '3'.zfill(64)
PUB = gs._signer_pubkey(KEY)
OWNER = gs._signer_pubkey(OWNER_KEY)
PIN = gs._signer_pubkey(RELAY_KEY)
CHANNEL = '00000000-0000-0000-0000-000000000001'
NOW = 10000
ORIGIN = 'https://127.0.0.1:9443'


def auth_tag(conditions=''):
    digest = hashlib.sha256(f'nostr:agent-auth:{PUB}:{conditions}'.encode()).digest()
    signature = gs.sync.nk.schnorr_sign(digest, bytes.fromhex(OWNER_KEY), bytes(32)).hex()
    return ['auth', OWNER, conditions, signature]


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.env = self.root / 'agent.env'; self.write_env()
        self.record = SimpleNamespace(pubkey=PUB, owner_pubkey=OWNER, env_file=str(self.env))
        self.events = [gs.sign_event(KEY, 9, [['h', CHANNEL], ['feishu','om_fixture'], ['d',PUB]], 'synthetic', NOW)]
        self.calls = []; self.status = 200

    def module(self): return importlib.import_module('hostd.agent_signed_reads')

    def write_env(self, *, key=KEY, auth=None, relay=ORIGIN):
        self.env.write_text('BUZZ_PRIVATE_KEY='+key+'\nBUZZ_AUTH_TAG='+json.dumps(auth if auth is not None else auth_tag(), separators=(',', ':'))+'\nBUZZ_RELAY_URL='+relay+'\n')
        self.env.chmod(0o600)

    def http(self, url, headers, timeout, *, body=None):
        self.calls.append((url, headers, body))
        self.check_wire(url, headers, body)
        return self.status, json.dumps(self.events).encode()

    def check_wire(self, url, headers, body):
        self.assertEqual(url, self.origin+'/query')
        headers = {k.lower():v for k,v in headers.items()}
        signed = json.loads(base64.b64decode(headers['authorization'].split(' ', 1)[1]))
        self.assertTrue(gs._nip01_event_verified(signed)); self.assertEqual(signed['pubkey'], PUB)
        self.assertEqual(signed['kind'], 27235); self.assertEqual(signed['created_at'], NOW)
        authority.exact_tag(signed, 'u', url); authority.exact_tag(signed, 'method', 'POST')
        authority.exact_tag(signed, 'payload', hashlib.sha256(body).hexdigest())
        self.assertEqual(headers['x-auth-tag'], json.dumps(auth_tag(), separators=(',', ':')))
        self.assertEqual(authority.attested_owner({'tags': [json.loads(headers['x-auth-tag'])]}, PUB), OWNER)

    def reader(self, *, origin=ORIGIN, http=None, **kwargs):
        self.origin = origin
        return self.module().OwnAgentReader(self.record, origin=origin, relay_pubkey=PIN,
            trusted_relays=(origin,), clock=lambda: NOW, http=http or self.http, **kwargs)

    async def fails(self, call):
        with self.assertRaises(self.module().AgentReadFailure) as exc: await call
        self.assertIn('怎么解决', str(exc.exception)); self.assertIn('复制给 AI', str(exc.exception))
        self.assertNotIn(KEY, str(exc.exception)); self.assertNotIn('synthetic', str(exc.exception))


class AgentReads(Base):
    async def test_actual_agent_nip98_and_raw_oa_header(self):
        filters = [{'kinds':[9], 'authors':[PUB], 'ids':[self.events[0]['id']], '#h':[CHANNEL], '#feishu':['om_fixture'], '#d':[PUB], 'limit':2}]
        self.assertEqual(await self.reader().query(filters), self.events)
        self.assertEqual(json.loads(self.calls[0][2]), filters)

    async def test_env_reloaded_before_every_io(self):
        reader = self.reader(); await reader.query([{'kinds':[9]}])
        self.write_env(key=OWNER_KEY)
        await self.fails(reader.query([{'kinds':[9]}])); self.assertEqual(len(self.calls), 1)

    async def test_wrong_owner_bad_oa_and_nonempty_conditions_fail_before_io(self):
        for auth in (['auth', OWNER, '', '0'*128], auth_tag('kind=9'), ['auth', PUB, '', auth_tag()[3]], ['auth', OWNER, '']):
            with self.subTest(shape=len(auth)):
                self.write_env(auth=auth); await self.fails(self.reader().query([{'kinds':[9]}]))
        self.assertFalse(bool(self.calls))

    async def test_unsafe_env_permissions_and_symlink_ancestors_fail(self):
        self.env.chmod(0o644); await self.fails(self.reader().query([{'kinds':[9]}]))
        self.env.chmod(0o600)
        link = self.root/'link.env'; link.symlink_to(self.env); self.record.env_file=str(link)
        await self.fails(self.reader().query([{'kinds':[9]}]))
        parent = self.root/'parent'; parent.symlink_to(self.root, target_is_directory=True)
        self.record.env_file=str(parent/self.env.name); await self.fails(self.reader().query([{'kinds':[9]}]))
        self.assertFalse(bool(self.calls))

    async def test_pinned_origin_cannot_follow_env_switch(self):
        self.write_env(relay='https://127.0.0.1:9555')
        await self.fails(self.reader().query([{'kinds':[9]}])); self.assertFalse(bool(self.calls))

    async def test_403_pending_fixed_notice_without_owner_fallback(self):
        self.status=403; await self.fails(self.reader().query([{'kinds':[9]}])); self.assertEqual(len(self.calls),1)
        self.assertEqual(self.module().AgentReadFailure().status, 'pending')

    async def test_malformed_signature_and_full_cap_are_not_empty(self):
        self.events[0]['sig']='0'*128; await self.fails(self.reader().query([{'kinds':[9]}]))
        self.events=[gs.sign_event(KEY,9,[['h',CHANNEL]],'synthetic',NOW)]*256
        await self.fails(self.reader().query([{'kinds':[9], 'limit':256}]))

    async def test_member_readback_requires_relay_signer_channel_and_sane_time(self):
        tags=[['d',CHANNEL],['p',PUB,'','bot']]
        self.events=[gs.sign_event(RELAY_KEY,39002,tags,'',NOW)]
        self.assertEqual(await self.reader().members(CHANNEL), {PUB:'bot'})
        for key, tags2, stamp in ((KEY,tags,NOW),(RELAY_KEY,[['d',PUB]],NOW),(RELAY_KEY,tags,NOW+999)):
            self.events=[gs.sign_event(key,39002,tags2,'',stamp)]
            await self.fails(self.reader().members(CHANNEL))

    async def test_query_protocol_rejects_mutations_extra_fields_and_nested_malformed(self):
        for filters in ([{'publish':True}],[{'#h':[[]]}],[{'authors':['bad']}],[{'kinds':[True]}],[{'limit':0}],[],[{'#feishu':['bad\n']}], [{'url':'https://other.test'}]):
            with self.subTest(filters=filters): await self.fails(self.reader().query(filters))
        self.assertFalse(bool(self.calls))

    async def test_oversized_query_packet_fails_before_any_io(self):
        filters=[{'#d':['x'*256]*256}]*2
        await self.fails(self.reader().query(filters))
        self.assertFalse(bool(self.calls))

    async def test_sealed_packet_rejects_identity_and_auth_mismatch(self):
        mod=self.module()
        packet=dict(version=1,origin=ORIGIN,key=KEY,pin=PIN,now=NOW,operation='query',filters=[{'kinds':[9]}],agent=PUB,owner=OWNER,auth_tag=auth_tag())
        request=mod.AgentReadRequest.from_data(packet)
        self.assertNotIn(KEY,repr(request)); self.assertNotIn(auth_tag()[3],repr(request))
        for update in ({'agent':OWNER},{'owner':PUB},{'auth_tag':auth_tag('kind=9')},{'operation':'publish'},{'unexpected':1}):
            with self.subTest(field=next(iter(update))):
                with self.assertRaises(mod.AgentReadFailure): mod.AgentReadRequest.from_data(dict(packet,**update))
        from hostd.signed_reads import ReadRequest, ReadFailure
        with self.assertRaises(ReadFailure): ReadRequest.from_data(packet)


class NativeChildren(Base):
    def packet(self):
        return dict(version=1,origin=ORIGIN,key=KEY,pin=PIN,now=NOW,operation='query',filters=[{'kinds':[9]}],agent=PUB,owner=OWNER,auth_tag=auth_tag())

    def child(self, body):
        path=self.root/'child.py'; path.write_text('import os,sys,json,time\n'+body)
        return [sys.executable,str(path)]

    async def test_private_packet_minimal_environment_actual_child(self):
        command=self.child("p=json.load(sys.stdin)\nassert p['key'] not in str(sys.argv)\nassert 'SECRET_CANARY' not in os.environ\nprint(json.dumps({'ok':True,'value':[]}))\n")
        with mock.patch.dict(os.environ,{'SECRET_CANARY':'private'}):
            self.assertEqual(await self.module().child_read(self.packet(),command=command),[])

    async def test_outer_timeout_kills_and_reaps_exact_child(self):
        pid=self.root/'pid'
        command=self.child("json.load(sys.stdin)\nopen("+repr(str(pid))+",'w').write(str(os.getpid()))\ntime.sleep(30)\n")
        await self.fails(self.module().child_read(self.packet(),command=command,outer_seconds=.3))
        with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()),0)

    async def test_repeated_cancellation_reaps_actual_child_before_return(self):
        pid=self.root/'pid'
        command=self.child("json.load(sys.stdin)\nopen("+repr(str(pid))+",'w').write(str(os.getpid()))\ntime.sleep(.35)\nprint(json.dumps({'ok':True,'value':[]}))\n")
        task=asyncio.create_task(self.module().child_read(self.packet(),command=command))
        await asyncio.sleep(.1); task.cancel(); await asyncio.sleep(.05); task.cancel()
        self.assertFalse(task.done())
        with self.assertRaises(asyncio.CancelledError): await task
        with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()),0)

    async def test_native_hard_budget_uses_real_child_signal(self):
        command=self.child("sys.path.insert(0,"+repr(str(SCRIPTS))+")\nfrom hostd.agent_signed_reads import child_main\ndef http(*a,**kw):\n time.sleep(12)\n return 200,b'[]'\nchild_main(http=http)\n")
        started=time.monotonic(); await self.fails(self.module().child_read(self.packet(),command=command))
        self.assertGreater(time.monotonic()-started,9); self.assertLess(time.monotonic()-started,12)


@unittest.skipUnless(Path('/usr/bin/openssl').is_file(),'actual private TLS requires openssl')
class NativeTLS(Base):
    def setUp(self):
        super().setUp()
        cert=self.root/'cert.pem'; key=self.root/'key.pem'
        subprocess.run(['/usr/bin/openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1','-addext','basicConstraints=critical,CA:FALSE','-addext','extendedKeyUsage=serverAuth'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cert.chmod(0o600); key.chmod(0o600); self.cert=cert; self.received=[]
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']))
                outer.received.append((self.path,dict(self.headers),body))
                answer=json.dumps(outer.events).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(answer)));self.end_headers();self.wfile.write(answer)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        self.server.socket=ctx.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin='https://127.0.0.1:'+str(self.server.server_port);self.write_env(relay=self.origin)

    def native_reader(self):
        # Full discovery can replace the gs module after this fixture captured
        # its _http_get. Let the actual adapter choose its own native default.
        reader=self.module().OwnAgentReader(self.record,origin=self.origin,relay_pubkey=PIN,
            trusted_relays=(self.origin,),clock=lambda:NOW)
        self.assertEqual(reader.transport_mode,'native_child')
        return reader

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    async def test_native_https_sealed_ca_actual_nip98_and_oa(self):
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.cert)}):
            reader=self.native_reader()
            self.assertEqual(await reader.query([{'kinds':[9],'#h':[CHANNEL]}]),self.events)
        self.assertEqual(len(self.received),1)
        path,headers,body=self.received[0]; self.assertEqual(path,'/query')
        self.check_wire(self.origin+path,headers,body)

    async def test_native_https_untrusted_certificate_fails_before_http(self):
        with mock.patch.dict(os.environ,{},clear=True):
            await self.fails(self.native_reader().query([{'kinds':[9]}]))
        self.assertFalse(self.received)

    async def test_unsafe_trust_bundle_fails_before_child_network(self):
        self.cert.chmod(0o644)
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':str(self.cert)}):
            await self.fails(self.native_reader().query([{'kinds':[9]}]))
        self.assertFalse(self.received)

class AgentPolicySnapshot(Base):
    @classmethod
    def setUpClass(cls):
        cls.policies = [gs.sign_event(OWNER_KEY,30177,[['d',f'{n:064x}']], '{}',NOW)
                        for n in range(475)]

    async def test_complete_snapshot_uses_own_key_and_oa_with_fixed_filter(self):
        self.events = self.policies
        self.assertEqual(await self.reader().policy_snapshot(), self.events)
        self.assertEqual(json.loads(self.calls[0][2]), [{'kinds':[30177],'limit':1000}])
        await self.fails(self.reader().query([{'kinds':[30177],'limit':257}]))

    async def test_rejects_raw_boundary_duplicate_wrong_kind_future_and_invalid_signature(self):
        import copy
        bad=copy.deepcopy(self.policies[0]);bad['sig']='0'*128
        future=gs.sign_event(OWNER_KEY,30177,[['d',PUB]],'{}',NOW+10000)
        wrong=self.events[0]
        for rows in ([self.policies[0]]*1000,[self.policies[0]]*2,[wrong],[future],[bad]):
            self.events=rows
            with self.subTest(count=len(rows)):
                await self.fails(self.reader().policy_snapshot())

    async def test_fixed_packet_rejects_filters_and_capacity_and_403_never_falls_back(self):
        reader=self.reader();packet=reader._request(None,'policy_snapshot').packet()
        for extra in ({'filters':[]},{'limit':1001},{'channel':CHANNEL}):
            with self.subTest(extra=extra),self.assertRaises(self.module().AgentReadFailure):
                self.module().AgentReadRequest.from_data(dict(packet,**extra))
        self.status=403
        await self.fails(reader.policy_snapshot())
        self.assertEqual(len(self.calls),1)  # check_wire has verified own key + OA

    async def test_actual_child_accepts_fixed_snapshot_and_preserves_auth_identity(self):
        events=self.root/'policies.json';events.write_text(json.dumps(self.policies))
        script=self.root/'child.py';wire=self.root/'wire.json'
        script.write_text('import sys,pathlib,json\nsys.path.insert(0,'+repr(str(SCRIPTS))+')\n'
            'from hostd.agent_signed_reads import child_main\n'
            'def http(url,headers,timeout,*,body=None):\n'
            ' pathlib.Path('+repr(str(wire))+').write_text(json.dumps([url,headers,body.decode()]))\n'
            ' return 200,pathlib.Path('+repr(str(events))+').read_bytes()\nchild_main(http=http)\n')
        reader=self.reader();packet=reader._request(None,'policy_snapshot').packet()
        result=await self.module().child_read(packet,command=[sys.executable,str(script)])
        self.assertEqual(result,self.policies)
        url,headers,body=json.loads(wire.read_text());self.check_wire(url,headers,body.encode())
        self.assertEqual(json.loads(body),[{'kinds':[30177],'limit':1000}])
