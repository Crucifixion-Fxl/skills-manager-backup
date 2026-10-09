"""Actual owned child stdio contracts for an explicitly fake ACP backend.

No native buzz-acp, publication, relay, provider, credentials or L3 acceptance.
The launcher pins this Python script and exact argument string separately.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT=Path(__file__).resolve().parent/'integration/hostd_l3/acp_double.py'
CANARY='SYNTHETIC_PRIVATE_PROMPT_NOT_A_RECEIPT'

class ACPDoubleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.root.chmod(0o700)
        self.work=self.root/'work';self.work.mkdir(mode=0o700)
        self.receipt=self.root/'receipt.json';self.manifest=self.root/'double.json'
        self.doc={'version':1,'run_id':'1'*32,'work_dir':str(self.work),'receipt_file':str(self.receipt),
                  'reply_text':'L3 synthetic deterministic reply','hold_marker':'L3-HOLD-'+ '1'*32}
        self.save();self.children=[];self.buffers={};self.addCleanup(self.reap)
    def save(self):
        self.manifest.write_text(json.dumps(self.doc));self.manifest.chmod(0o600)
        self.pin=hashlib.sha256(self.manifest.read_bytes()).hexdigest()
    def spawn(self,path=None,pin=None):
        # No shell, HOME discovery or inherited authentication. Same script/path
        # and exact args are suitable for AgentPlan provider_* hash admission.
        proc=subprocess.Popen([sys.executable,str(SCRIPT),'--manifest',str(path or self.manifest),
            '--sha256',pin or self.pin],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'HOME':str(self.root),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'},
            cwd=self.work,start_new_session=True)
        self.children.append(proc);self.buffers[proc.pid]=b''
        self.assertEqual(os.getpgid(proc.pid),proc.pid)
        return proc
    def reap(self):
        for proc in self.children:
            if proc.poll() is None:
                # Only Popen objects created here, own group confirmed at spawn.
                os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
            for stream in (proc.stdin,proc.stdout,proc.stderr):stream.close()
            self.assertFalse(Path('/proc/'+str(proc.pid)).exists())
    def send(self,p,method,params=None,rid=1):
        v={'jsonrpc':'2.0','method':method,'params':params or {}}
        if rid is not None:v['id']=rid
        p.stdin.write((json.dumps(v)+'\n').encode());p.stdin.flush()
    def read(self,p,timeout=3):
        end=time.monotonic()+timeout;buf=self.buffers[p.pid]
        while b'\n' not in buf:
            remaining=end-time.monotonic();self.assertGreater(remaining,0,'bounded ACP frame absent')
            self.assertTrue(select.select([p.stdout],[],[],remaining)[0],'bounded ACP frame absent')
            piece=os.read(p.stdout.fileno(),65536);self.assertTrue(bool(piece),'ACP child exited before protocol response')
            buf+=piece;self.assertLessEqual(len(buf),131072)
        line,buf=buf.split(b'\n',1);self.buffers[p.pid]=buf
        value=json.loads(line);self.assertEqual(value.get('jsonrpc'),'2.0');return value
    def initialize(self,p):
        self.send(p,'initialize',{'protocolVersion':1,'clientCapabilities':{}},0)
        v=self.read(p);self.assertEqual(v['id'],0);self.assertEqual(v['result']['protocolVersion'],1)
        self.assertFalse(v['result']['agentCapabilities']['loadSession']);self.assertEqual(v['result']['authMethods'],[])
    def session(self,p,rid=1):
        self.send(p,'session/new',{'cwd':str(self.work),'mcpServers':[]},rid)
        v=self.read(p);return v['result']['sessionId']
    def prompt(self,p,sid,rid=2,text=CANARY):
        self.send(p,'session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':text}]},rid)
    def finish(self,p):
        p.stdin.close();self.assertEqual(p.wait(timeout=3),0)
        data=json.loads(self.receipt.read_text());self.assertEqual(data['backend'],'deterministic_acp_double')
        self.assertFalse(data['live_verified']);self.assertTrue(CANARY not in self.receipt.read_text())
        self.assertEqual(self.receipt.stat().st_mode&0o777,0o600);return data
    def test_initialize_new_and_reused_session_real_stdio(self):
        p=self.spawn();self.initialize(p);sid=self.session(p)
        self.assertEqual(sid,'l3-'+self.doc['run_id']+'-1')
        for rid in (2,3):
            self.prompt(p,sid,rid);self.read(p);self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
        data=self.finish(p);self.assertEqual(data['sessions'],1);self.assertEqual(data['prompts'],2)
    def test_text_chunk_notification_before_completion_and_metadata_only(self):
        p=self.spawn();self.initialize(p);sid=self.session(p);self.prompt(p,sid)
        v=self.read(p);self.assertNotIn('id',v);self.assertEqual(v['method'],'session/update')
        self.assertEqual(v['params']['sessionId'],sid);u=v['params']['update']
        self.assertEqual(u['sessionUpdate'],'agent_message_chunk');self.assertEqual(u['content']['type'],'text')
        self.assertTrue(u['content']['text']==self.doc['reply_text'])
        self.assertEqual(self.read(p)['id'],2);self.finish(p)
    def test_images_and_resource_links_are_metadata_no_file_or_url_access(self):
        p=self.spawn();self.initialize(p);sid=self.session(p)
        self.send(p,'session/prompt',{'sessionId':sid,'prompt':[{'type':'image','mimeType':'image/png',
            'data':base64.b64encode(b'synthetic image').decode()},{'type':'resource_link','uri':'file:///not-permitted',
            'name':'synthetic metadata'}]},2)
        self.read(p);self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
        self.send(p,'session/prompt',{'sessionId':sid,'prompt':[{'type':'image','mimeType':'image/png','data':'!invalid'}]},3)
        self.assertEqual(self.read(p)['error']['code'],-32602);self.finish(p)
    def test_cancel_completes_original_prompt_then_session_reuse(self):
        p=self.spawn();self.initialize(p);sid=self.session(p)
        self.prompt(p,sid,text=self.doc['hold_marker']);self.send(p,'session/cancel',{'sessionId':sid},None)
        v=self.read(p);self.assertEqual(v['id'],2);self.assertEqual(v['result']['stopReason'],'cancelled')
        self.prompt(p,sid,3);self.read(p);self.assertEqual(self.read(p)['id'],3)
        self.assertEqual(self.finish(p)['cancelled'],1)
    def test_concurrent_prompt_same_session_rejected_without_cancelling_other(self):
        p=self.spawn();self.initialize(p);sid=self.session(p);self.prompt(p,sid,text=self.doc['hold_marker'])
        self.prompt(p,sid,3);self.assertEqual(self.read(p)['error']['code'],-32000)
        self.send(p,'session/cancel',{'sessionId':sid},None);self.assertEqual(self.read(p)['id'],2);self.finish(p)
    def test_initialize_required_wrong_cwd_mcp_and_unknown_session_rejected(self):
        p=self.spawn();self.send(p,'session/new',{'cwd':str(self.work),'mcpServers':[]})
        self.assertEqual(self.read(p)['error']['code'],-32000);self.initialize(p)
        for rid,params in [(2,{'cwd':str(self.root),'mcpServers':[]}),(3,{'cwd':str(self.work),'mcpServers':[{'name':'unapproved'}]})]:
            self.send(p,'session/new',params,rid);self.assertEqual(self.read(p)['error']['code'],-32602)
        self.prompt(p,'foreign-session',4);self.assertEqual(self.read(p)['error']['code'],-32602);self.finish(p)
    def test_bad_json_duplicate_keys_and_unknown_method_safe_protocol_errors(self):
        p=self.spawn()
        for raw in (b'{invalid}\n',b'{"jsonrpc":"2.0","id":1,"id":2,"method":"initialize"}\n'):
            p.stdin.write(raw);p.stdin.flush();v=self.read(p);self.assertIsNone(v['id']);self.assertEqual(v['error']['code'],-32700)
            self.assertIn('怎么解决',v['error']['message']);self.assertIn('复制给 AI',v['error']['message'])
        self.initialize(p);self.send(p,'arbitrary/shell',{'command':CANARY},2)
        v=self.read(p);self.assertEqual(v['error']['code'],-32601);self.assertTrue(CANARY not in json.dumps(v));self.finish(p)
    def test_oversized_and_partial_frame_fail_bounded_and_reap(self):
        for data in (b'x'*65537+b'\n',b'{"partial":true}'):
            with self.subTest(partial=len(data)<100):
                p=self.spawn();p.stdin.write(data);p.stdin.close()
                self.assertNotEqual(p.wait(timeout=3),0)
                err=p.stderr.read();self.assertTrue('怎么解决'.encode() in err);self.assertTrue(CANARY.encode() not in err)
                self.assertFalse(Path('/proc/'+str(p.pid)).exists())
    def test_eof_joins_held_prompt_without_orphan_worker(self):
        p=self.spawn();self.initialize(p);sid=self.session(p);self.prompt(p,sid,text=self.doc['hold_marker'])
        data=self.finish(p);self.assertEqual(data['cancelled'],1)
        self.assertFalse(Path('/proc/'+str(p.pid)).exists())
    def test_nonreading_stdout_has_hard_write_budget(self):
        self.doc['reply_text']='s'*4096;self.save();p=self.spawn()
        rows=[{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':1}},
              {'jsonrpc':'2.0','id':1,'method':'session/new','params':{'cwd':str(self.work),'mcpServers':[]}}]
        rows += [{'jsonrpc':'2.0','id':i+2,'method':'session/prompt','params':{'sessionId':'l3-'+self.doc['run_id']+'-1',
                  'prompt':[{'type':'text','text':'synthetic'}]}} for i in range(48)]
        payload=b''.join((json.dumps(v)+'\n').encode() for v in rows)
        # Actual OS pipe left unread, rather than a high-level writer fake.
        os.set_blocking(p.stdin.fileno(),False);sent=0;deadline=time.monotonic()+4
        while sent<len(payload) and time.monotonic()<deadline and p.poll() is None:
            try:sent+=os.write(p.stdin.fileno(),payload[sent:])
            except BlockingIOError:time.sleep(.01)
            except BrokenPipeError:break
        self.assertNotEqual(p.wait(timeout=4),0);self.assertFalse(Path('/proc/'+str(p.pid)).exists())
        self.assertTrue('怎么解决'.encode() in p.stderr.read())
    def test_protected_manifest_hash_symlink_hardlink_mode_and_cas(self):
        for case in ('hash','symlink','hardlink','mode','changed'):
            with self.subTest(case=case):
                self.save();path=self.manifest;pin=self.pin
                if case=='hash':pin='0'*64
                if case=='symlink':path=self.root/'alias';path.symlink_to(self.manifest)
                if case=='hardlink':os.link(self.manifest,self.root/'hard')
                if case=='mode':self.manifest.chmod(0o644)
                if case=='changed':self.manifest.write_text('{}')
                p=self.spawn(path,pin);self.assertNotEqual(p.wait(timeout=3),0)
                self.assertTrue('怎么解决'.encode() in p.stderr.read());self.assertFalse(self.receipt.exists())
                if case=='hardlink':(self.root/'hard').unlink()
    def test_manifest_escape_existing_receipt_and_unsupported_shapes_rejected(self):
        baseline=dict(self.doc)
        for case in ('escape','receipt','extra','boolversion'):
            with self.subTest(case=case):
                self.doc=dict(baseline)
                if case=='escape':self.doc['work_dir']=str(self.root.parent)
                if case=='receipt':self.receipt.write_text('synthetic existing');self.receipt.chmod(0o600)
                if case=='extra':self.doc['command']='forbidden'
                if case=='boolversion':self.doc['version']=True
                self.save();p=self.spawn();self.assertNotEqual(p.wait(timeout=3),0)
                self.assertTrue('怎么解决'.encode() in p.stderr.read())
                if case=='receipt':self.assertEqual(self.receipt.read_text(),'synthetic existing');self.receipt.unlink()
    def test_malformed_rpc_ids_are_null_and_never_echo_input_canary(self):
        p=self.spawn()
        for kind,rid in (('object',{'secret':CANARY}),('list',[CANARY]),('bool',True),('long',CANARY*8)):
            with self.subTest(kind=kind):
                self.send(p,'initialize',{'protocolVersion':1},rid)
                value=self.read(p)
                # Boolean assertions keep malformed bodies out of test failures.
                self.assertTrue(value.get('id') is None,'malformed RPC id must be null')
                self.assertTrue(CANARY not in json.dumps(value),'malformed input must not be echoed')
                self.assertEqual(value['error']['code'],-32602)
                self.assertIn('怎么解决',value['error']['message']);self.assertIn('复制给 AI',value['error']['message'])
        self.initialize(p);sid=self.session(p);self.prompt(p,sid)
        self.read(p);self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
        self.finish(p)
