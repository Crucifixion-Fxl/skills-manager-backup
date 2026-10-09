"""Finite offline MSG003 contracts. Native delivery and SDK acceptance remain separate.

No high-level domain verdict, authority DTO or signed-event verifier is patched.
Only fixture setup inputs and source subprocess boundary are synthetic here.
"""
import asyncio
import dataclasses
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

MODULE=Path(os.environ.get('MSG003_DRIVER',str(Path(__file__).parent/'integration/hostd_l3/msg003_driver.py')))
CANONICAL=Path(os.environ.get('MSG003_CANONICAL',Path(__file__).resolve().parents[4]))
TESTS=CANONICAL/'skills/agent-harness/buzz-agent-setup/tests'
SCRIPTS=TESTS.parent/'scripts'
RUN='1'*32

class Msg003Contracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(MODULE.is_file(),'missing run-scoped MSG003 image driver')
        for name in ('cryptography','lark-oapi','websockets'):
            try:importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:raise unittest.SkipTest('requires actual '+name+'; skipped offline contract is not acceptance')
        sys.path[:0]=[str(TESTS),str(SCRIPTS)]
        package=importlib.import_module('integration.hostd_l3')
        spec=importlib.util.spec_from_file_location('integration.hostd_l3.msg003_driver',MODULE)
        self.m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.m;spec.loader.exec_module(self.m)
        import zero_local_fixture as fixture
        import test_hostd_l3_scenario_driver as source_fixture
        self.fixture=fixture
        self.source=source_fixture.ScenarioDriverTests();await self.source.asyncSetUp()
        self.addAsyncCleanup(self.source.asyncTearDown);self.addCleanup(self.source.doCleanups)
        revision=subprocess.run(['git','-C',str(CANONICAL),'rev-parse','HEAD'],capture_output=True,text=True,check=True,timeout=5).stdout.strip()
        # The old source metadata fixture's git subprocess is synthetic; pin its
        # revision to this candidate. It never supplies host B's descriptor.
        self.source.base.doc['revision']=revision;self.source.base.save()
        self.source.base.head_result.stdout=revision+'\n'
        self.source.doc['revision']=revision;self.source.save()
        self.source.prepared=self.source.base.m.PreparedRun.check(self.source.base.manifest,reviewed_source=self.source.base.source,reviewed_revision=revision,fixture_sha256=fixture.digest(self.source.base.fixture),command_runner=self.source.base.runner)
        self.scenario=self.source.check()
        fixture_home=Path.home()/'.codex';fixture_home.mkdir(mode=0o700,exist_ok=True)
        fixture_parent=fixture_home/'hostd-msg003-private-fixtures';fixture_parent.mkdir(mode=0o700,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='msg003-private-',dir=fixture_parent);self.addCleanup(self.temp.cleanup)
        parent=Path(self.temp.name);parent.chmod(0o700)
        hashes={str(p.relative_to(CANONICAL)):fixture.digest(p) for p in SCRIPTS.rglob('*.py')}
        with mock.patch.object(fixture.uuid,'uuid4',return_value=uuid.UUID(hex=RUN)):
            self.b=fixture.Fixture(parent,CANONICAL,revision,hashes)
        self.addCleanup(self.b.close)
        rt=json.loads(self.b.onboarding.read_text());rt['remote_link_base']=self.b.relay.origin
        self.fixture.write(self.b.onboarding,json.dumps(rt));self.b.repin()
        self.bp=self.m.zero.ZeroLocalRunPlan.check(self.b.manifest,**self.b.check_args)
        self.source_path=self.source.phase_dir/'msg003-source.json'
        self.doc={'version':1,'run_id':RUN,'host_b_manifest_sha256':hashlib.sha256(self.bp._manifest_bytes).hexdigest(),'agent_name':'local','buzz_cli':str(self.b.node),'buzz_cli_sha256':fixture.digest(self.b.node),'phase_dir':str(self.source.phase_dir),'scenario_manifest_sha256':self.source.pin,'sdk':None}
        self.save_source()
        self.sr=self.admit_source()
        self.plan=self.m.Msg003RunPlan.check(self.bp,self.sr,self.scenario,source_agent_name='local')
        self.owned=self.m.zero.OwnedZeroLocalRun(self.bp)
        self.driver=self.m.Msg003Driver(self.plan,hostd=self.owned,clock=lambda:10000)
        self.gs=importlib.import_module('buzz_feishu_group_sync')

    def save_source(self):self.fixture.write(self.source_path,json.dumps(self.doc))
    def admit_source(self):return self.m.Msg003SourceRun.check(self.bp,self.source_path,self.fixture.digest(self.source_path))
    def event(self,**changes):
        doc=json.loads(self.plan._scenario_doc);imeta=['imeta','url '+self.b.relay.origin+'/media/'+doc['image_sha256']+'.png','x '+doc['image_sha256'],'m image/png','size '+str(self.plan._image_size)]
        args={'key':self.b.agent_key,'kind':9,'tags':[['h',self.b.doc['channel_id']],imeta],'content':'MSG003-'+RUN,'created_at':10000};args.update(changes)
        return self.gs.sign_event(args['key'],args['kind'],args['tags'],args['content'],args['created_at'])
    def safe(self,receipt):
        value=receipt.readback();text=json.dumps(value)
        for canary in (self.b.agent_key,self.b.owner_key,self.fixture.SECRET,'BUZZ_PRIVATE_KEY',str(self.b.root),'MSG003-'+RUN,'img_secret','https://'):
            self.assertNotIn(canary,text)
        self.assertFalse(value['live_verified'])

    async def test_12_v2_root_descriptor_is_explicit_pinned_and_not_authority(self):
        # These fields constrain later original signed/native reads. Static
        # admission must not create a Store, target, grant or native write.
        before=self.b.snapshot()
        self.doc={**self.doc,'version':2,'root':{'event_id':'a'*64,
            'event_sha256':'b'*64,'native_message_id':'om_original_msg001'}}
        self.save_source()
        source=self.admit_source()
        plan=self.m.Msg003RunPlan.check(self.bp,source,self.scenario,source_agent_name='local')
        driver=self.m.Msg003Driver(plan,hostd=self.owned,clock=lambda:10000)
        self.assertEqual((await driver.publish_once()).readback()['status'],'pending')
        self.assertEqual(before,self.b.snapshot())
        self.assertFalse(any(p.exists() for p in self.b.outputs))
        self.assertFalse((self.source.phase_dir/'msg003-intent.json').exists())
        for root in ({}, {'event_id':'a'*64,'event_sha256':'b'*64},
                {'event_id':'a'*64,'event_sha256':'b'*64,'native_message_id':'om_original_msg001','verified':True},
                {'event_id':'unsigned','event_sha256':'b'*64,'native_message_id':'om_original_msg001'}):
            with self.subTest(root_keys=sorted(root)):
                self.doc['root']=root;self.save_source()
                with self.assertRaises(self.m.Msg003Error):self.admit_source()

    async def test_13_v2_signed_source_thread_and_unknown_root_cas(self):
        self.doc.update(version=2,root={'event_id':'a'*64,'event_sha256':'b'*64,'native_message_id':'om_original_msg001'})
        self.save_source();source=self.admit_source()
        plan=self.m.Msg003RunPlan.check(self.bp,source,self.scenario,source_agent_name='local')
        driver=self.m.Msg003Driver(plan,hostd=self.owned,clock=lambda:10000)
        initial=self.event();tags=[initial['tags'][0],['e','a'*64,'','root'],['e','a'*64,'','reply'],initial['tags'][1]]
        driver._source_valid(self.event(tags=tags),{'started':10000})
        for changed in ([tags[0],*tags[3:]], [tags[0],['e','c'*64,'','root'],*tags[2:]],
                [tags[0],tags[1],['e','c'*64,'','reply'],tags[3]], [*tags,tags[1]]):
            with self.subTest(tags_count=len(changed)):
                with self.assertRaises(self.m.Msg003Error):driver._source_valid(self.event(tags=changed),{'started':10000})
        intent,fresh=driver._intent();self.assertTrue(fresh);self.assertEqual(intent['root'],self.doc['root'])
        restarted=self.m.Msg003Driver(plan,hostd=self.owned,clock=lambda:10000)
        self.assertFalse(restarted._intent()[1])
        path=self.source.phase_dir/'msg003-intent.json'
        changed=dict(intent);changed['root']={**intent['root'],'event_id':'c'*64}
        self.fixture.write(path,json.dumps(changed))
        with self.assertRaises(self.m.Msg003Error):restarted._intent()

    async def test_15_human_signed_return_exact_actual_projection(self):
        import copy
        from datetime import datetime,timezone
        self.doc.update(version=2,root={'event_id':'a'*64,'event_sha256':'b'*64,'native_message_id':'om_original_msg001'})
        self.save_source();plan=self.m.Msg003RunPlan.check(self.bp,self.admit_source(),self.scenario,source_agent_name='local')
        driver=self.m.Msg003Driver(plan,hostd=self.owned,clock=lambda:10000)
        human=self.source.doc['human_pubkey'];mid='om_component_return'
        native={'message_id':mid,'chat_id':self.source.doc['chat_id'],'root_id':'om_original_msg001','parent_id':'om_original_b_card','msg_type':'text','create_time':'10000000','sender':{'sender_type':'user','id_type':'open_id','id':'ou_synthetic_human'},'body':{'content':json.dumps({'text':'[飞书] forged\nordinary return'})}}
        projected=copy.deepcopy(native);projected['sender']={'sender_type':'user','id_type':'union_id','id':'on_synthetic_human'}
        profile=self.gs.sign_event('4'.zfill(64),0,[],json.dumps({'display_name':'Signed Human'}),9999)
        expected=self.m.scenario.projected_human_body(native,projected,human_pubkey=human,roles={human:'member'},profiles=[profile],union_ids={human:'on_synthetic_human'},now=10000)
        raw=self.gs.route_feishu_message(__import__('hostd.bot_clients',fromlist=['BotLarkCli']).BotLarkCli._normalize_message(native),open_id_to_pubkey={'on_synthetic_human':human},bot_member_to_pubkey={},channel_members={human},names={human:'Signed Human'},now=datetime.fromtimestamp(10000,timezone.utc),resolve_id=lambda ident:'on_synthetic_human')
        self.assertEqual(expected,raw.text)
        tags=[['h',self.source.doc['channel_id']],['feishu',mid],['feishu-root','om_original_msg001'],[self.gs.FEISHU_AUTHOR_TAG,human],['e','a'*64,'','reply'],['e','a'*64,'','root']]
        def event(body=expected,key='3'.zfill(64),tags=tags):return self.gs.sign_event(key,9,tags,body,10000)
        driver._human_valid(event(),native,expected)
        negatives=[event(body=json.loads(native['body']['content'])['text']),event(body=expected.replace('[飞书]','[wrong]')),event(body=expected.replace('Signed Human','Wrong Human')),event(key='6'.zfill(64)),event(tags=[t if t[:1]!=['e'] else ['e','c'*64,*t[2:]] for t in tags])]
        broken=event();broken['sig']='0'*128;negatives.append(broken)
        for bad in negatives:
            with self.subTest(event_id=bad['id']):
                with self.assertRaises(self.m.Msg003Error):driver._human_valid(bad,native,expected)

    async def test_14_human_return_requires_started_original_v2_and_clears_cache(self):
        calls=[];self.driver.runner=lambda *a,**kw:calls.append(a)
        self.driver._last.update(status='observed',human_return=True,human_event_sha256='a'*64,human_native_sha256='b'*64)
        result=await self.driver.observe_human_return('om_unverified_human')
        self.assertEqual(result.readback()['status'],'pending');self.assertNotIn('human_event_sha256',result.readback())
        self.assertEqual(calls,[]);self.assertFalse((self.source.phase_dir/'msg003-intent.json').exists())
        self.safe(result)

    async def test_01_distinct_zero_local_topology_and_source_pins(self):
        snapshot=self.b.snapshot()
        self.assertEqual(self.bp.readback()['registry_bindings'],0)
        self.assertNotEqual(self.bp._owner,self.bp._source_owner)
        with self.assertRaises(self.m.Msg003Error):self.m.Msg003RunPlan.check(self.source.prepared,self.sr,self.scenario,source_agent_name='local')
        with self.assertRaises(self.m.Msg003Error):self.m.Msg003RunPlan.check(self.bp,self.sr,self.scenario,source_agent_name='foreign')
        copied_binary=self.source.phase_dir/'copied-source-cli';self.fixture.write(copied_binary,self.b.node.read_bytes(),0o700)
        old=dict(self.doc)
        for field,bad in [('buzz_cli',str(copied_binary)),('run_id','2'*32),('agent_name','foreign'),('buzz_cli_sha256','a'*64),('scenario_manifest_sha256','b'*64),('phase_dir',str(self.b.root)),('host_b_manifest_sha256','c'*64)]:
            with self.subTest(field=field):
                self.doc={**old,field:bad};self.save_source()
                with self.assertRaises(self.m.Msg003Error):
                    source=self.admit_source();self.m.Msg003RunPlan.check(self.bp,source,self.scenario,source_agent_name='local')
        self.doc=old;self.save_source()
        self.assertEqual(snapshot,self.b.snapshot())

    async def test_02_no_authority_or_native_side_effect_without_original_run(self):
        calls=[];d=self.m.Msg003Driver(self.plan,hostd=self.owned,runner=lambda *a,**kw:calls.append((a,kw)),clock=lambda:10000)
        before=self.b.snapshot()
        self.assertEqual((await d.publish_once()).readback()['status'],'pending')
        self.assertEqual((await d.observe()).readback()['status'],'pending')
        self.assertEqual(calls,[]);self.assertEqual(before,self.b.snapshot())
        self.assertFalse(any(p.exists() for p in self.b.outputs))
        self.assertFalse((self.source.phase_dir/'msg003-intent.json').exists())

    async def test_03_authentic_signed_original_source_image_and_channel(self):
        intent={'started':10000};event=self.event()
        self.assertTrue(self.gs._nip01_event_verified(event));self.driver._source_valid(event,intent)
        bad_events=[self.event(key='5'.zfill(64)),self.event(kind=1),self.event(content='foreign'),self.event(created_at=9999),self.event(tags=[['h','00000000-0000-0000-0000-000000000002']])]
        unsigned=dict(event);unsigned['sig']='0'*128;bad_events.append(unsigned)
        for bad in bad_events:
            with self.assertRaises(self.m.Msg003Error):self.driver._source_valid(bad,intent)
        imeta=list(event['tags'][1]);imeta[-1]='size 1'
        with self.assertRaises(self.m.Msg003Error):self.driver._source_valid(self.event(tags=[event['tags'][0],imeta]),intent)

    async def test_04_driver_does_not_replace_normal_dispatch_or_authority(self):
        # Architectural guard supplements real assembly's separate suite.
        source=MODULE.read_text()
        for forbidden in ('activate_remote_grant(','.reconcile(','.deliver(','.upload_image(','.send_card(','.post(', 'Worker('):
            self.assertNotIn(forbidden,source)
        self.assertEqual(self.bp.argv()[1:5],['-m','hostd','run','--only'])
        self.assertEqual(self.bp.argv()[5],self.b.doc['selector'])
        self.assertIn('--onboarding-config',self.bp.argv())

    async def test_05_durable_unknown_exclusive_intent_and_cancellation_join(self):
        intent,fresh=self.driver._intent();self.assertTrue(fresh);self.assertEqual(intent['state'],'unknown')
        original=(self.source.phase_dir/'msg003-intent.json').read_bytes()
        repeated,fresh=self.driver._intent();self.assertFalse(fresh);self.assertEqual(intent,repeated)
        self.assertEqual(original,(self.source.phase_dir/'msg003-intent.json').read_bytes())
        self.assertEqual((self.source.phase_dir/'msg003-intent.json').stat().st_mode&0o777,0o600)
        import threading
        entered,release,ended=threading.Event(),threading.Event(),threading.Event()
        def boundary():entered.set();release.wait(3);ended.set()
        task=asyncio.create_task(self.driver._io(boundary));await asyncio.to_thread(entered.wait,1)
        task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
        self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(ended.is_set())

    async def test_06_no_native_success_from_unsigned_or_absent_evidence(self):
        # Store ACK/native GET/image-byte positives remain the separate required
        # full assembly gate. Empty-run and forged source controls must fail.
        result=await self.driver.observe('f'*64);self.assertEqual(result.readback()['status'],'pending')
        self.assertFalse(result.readback()['native_readback']);self.safe(result)
        self.assertFalse(any(p.exists() for p in self.b.outputs))
        for path in self.source.phase_dir.glob('msg003-receipt-*.json'):
            if path.name.startswith('msg003-receipt-manifest'):continue
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),path.stem.removeprefix('msg003-receipt-'))

    async def test_07_callback_cannot_substitute_original_native_readback(self):
        path=self.source.phase_dir/'synthetic-callback.json'
        self.fixture.write(path,json.dumps({'verified':True,'run_id':RUN,'body':'SDK_CANARY'}))
        receipt=await self.driver.join_sdk_evidence((path,self.fixture.digest(path)))
        self.assertFalse(receipt.readback()['sdk_callback']);self.assertFalse(receipt.readback()['native_readback']);self.safe(receipt)
        self.assertNotIn('SDK_CANARY',json.dumps(receipt.readback()))

    async def test_09_failed_sdk_revalidation_clears_cached_verdict(self):
        # This is a cache-state regression only. The original unstarted owner
        # naturally fails its process gate; cached booleans confer no authority.
        self.driver._last.update(status='observed',native_readback=True,source_readback=True,sdk_callback=True,native_readback_sha256='a'*64,native_message_sha256='b'*64,source_event_sha256='c'*64,callback_event_sha256='d'*64,callback_session_sha256='e'*64,callback_artifact_sha256='f'*64,target_sha256='1'*64,scope_sha256='2'*64,grant_revision=1,images=[{'sha256':'3'*64}])
        result=await self.driver.join_sdk_evidence(('missing',))
        row=result.readback();self.assertEqual(row['status'],'pending');self.assertFalse(row['native_readback']);self.assertFalse(row['source_readback']);self.assertFalse(row['sdk_callback'])
        for field in ('native_readback_sha256','native_message_sha256','source_event_sha256','callback_event_sha256','callback_session_sha256','callback_artifact_sha256','target_sha256','scope_sha256','grant_revision','images'):
            self.assertNotIn(field,row)
        self.safe(result)

    async def test_10_existing_unknown_recovers_by_original_signed_query(self):
        import time
        self.driver.clock=time.time;self.driver._intent()
        event=self.event(created_at=int(time.time()));self.b.relay.events.append(event)
        # This finite low TLS peer requires the selected own-agent signer; the
        # generic zero-local fixture defaults to the independent owner's reads.
        self.b.relay.owner=self.b.agent_pub;self.b.relay.start()
        def forbidden(*args,**kwargs):self.fail('existing UNKNOWN republished source')
        self.driver.runner=forbidden
        # Isolate driver recovery from the independent daemon lifecycle. These
        # test-support gates return no authority; real signed TLS query and
        # source verifier remain original, and no Store/native action exists.
        with mock.patch.dict(os.environ,self.bp.environment(),clear=True),mock.patch.object(self.driver,'_owners'),mock.patch.object(self.driver,'_snapshot',return_value=None):
            result=await self.driver.publish_once()
        self.assertTrue(result.readback()['source_readback']);self.assertEqual(self.driver._event,event)
        self.assertEqual(len(self.b.relay.received),1);self.assertEqual(self.b.relay.failures,[])
        restarted=self.m.Msg003Driver(self.plan,hostd=self.owned,runner=forbidden,clock=time.time)
        with mock.patch.dict(os.environ,self.bp.environment(),clear=True),mock.patch.object(restarted,'_owners'),mock.patch.object(restarted,'_snapshot',return_value=None):
            recovered=await restarted.publish_once()
        self.assertTrue(recovered.readback()['source_readback']);self.assertEqual(len(self.b.relay.received),2)
        self.assertFalse(any(p.exists() for p in self.b.outputs));self.safe(recovered)

    async def test_11_source_cli_stubborn_root_and_admission_cleanup(self):
        original=subprocess.Popen;captured=[];original_proc=self.m.zero._proc
        def spawn(*args,**kwargs):
            proc=original(*args,**kwargs);captured.append(proc);return proc
        options={'input':'marker','capture_output':True,'text':True,'check':False,'env':{'PATH':os.environ['PATH']}}
        stubborn=self.source.phase_dir/'stubborn-source.py'
        self.fixture.write(stubborn,"import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(10)\n")
        fast=self.source.phase_dir/'fast-source.py';self.fixture.write(fast,'import time\ntime.sleep(.05)\n')
        def emergency():
            for process in captured:
                if process.poll() is None:
                    self.m.zero._signal_original(original_proc(process.pid),__import__('signal').SIGKILL)
                    process.wait(timeout=2)
        self.addCleanup(emergency)
        with mock.patch.object(self.m.subprocess,'Popen',side_effect=spawn):
            with self.subTest(boundary='stubborn_root'):
                with self.assertRaises(subprocess.TimeoutExpired):
                    await self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(stubborn)],timeout=.1,**options))
                self.assertIsNotNone(captured[-1].returncode);self.assertFalse(self.m.zero._session(captured[-1].pid))
            emergency()
            with self.subTest(boundary='post_spawn_admission'):
                calls=0
                def absent_once(pid):
                    nonlocal calls
                    calls+=1;return None if calls==1 else original_proc(pid)
                with mock.patch.object(self.m.zero,'_proc',side_effect=absent_once):
                    with self.assertRaises(self.m.Msg003Error):
                        await self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(fast)],timeout=2,**options))
                self.assertIsNotNone(captured[-1].returncode);self.assertFalse(self.m.zero._session(captured[-1].pid))
            for failure in ('always_none','always_raises'):
                with self.subTest(boundary=failure):
                    def unavailable(pid):
                        if failure=='always_raises':raise OSError('identity unavailable')
                        return None
                    with mock.patch.object(self.m.zero,'_proc',side_effect=unavailable):
                        with self.assertRaises((self.m.Msg003Error,OSError)):
                            await self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(fast)],timeout=2,**options))
                    self.assertIsNotNone(captured[-1].returncode);self.assertFalse(self.m.zero._session(captured[-1].pid))

    async def test_08_owned_source_cli_tree_join(self):
        self.assertTrue(callable(getattr(self.m,'_run_source_cli',None)),'missing owned source CLI process tree runner')
        script=self.source.phase_dir/'source-tree.py'
        self.fixture.write(script,"import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(float(__import__(\"sys\").argv[1]))',sys.argv[1]])\np.wait()\n")
        original=subprocess.Popen;captured=[]
        def owned_spawn(*args,**kwargs):
            proc=original(*args,**kwargs);captured.append(proc);return proc
        options={'input':'marker','capture_output':True,'text':True,'check':False,'env':{'PATH':os.environ['PATH']}}
        with mock.patch.object(self.m.subprocess,'Popen',side_effect=owned_spawn):
            result=await self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(script),'.02'],timeout=2,**options))
            self.assertEqual(result.returncode,0);self.assertFalse(self.m.zero._session(captured[-1].pid))
            with self.assertRaises(subprocess.TimeoutExpired):
                await self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(script),'10'],timeout=.1,**options))
            self.assertFalse(self.m.zero._session(captured[-1].pid));self.assertIsNotNone(captured[-1].poll())
            task=asyncio.create_task(self.driver._io(lambda:self.m._run_source_cli([sys.executable,str(script),'10'],timeout=.2,**options)))
            await asyncio.sleep(.05);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertFalse(self.m.zero._session(captured[-1].pid));self.assertIsNotNone(captured[-1].poll())

if __name__=='__main__':unittest.main()

# This CLI program is an offline native subprocess boundary only. The actual
# BotLarkCli prepares files, pins its profile, verifies TLS and checks envelopes.
IMAGE_CLI = r'''import base64,http.client,json,os,ssl,sys
from urllib.parse import urlsplit
from pathlib import Path
wire=json.loads(Path(WIRE).read_text())
args=sys.argv[2:]
assert args[-2:]==['--profile','local'] and args[args.index('--as')+1]=='bot'
context=ssl.create_default_context(cafile=CA)
def call(path,value=None):
    data=json.dumps(value).encode() if value is not None else None
    origin=urlsplit(ORIGIN);connection=http.client.HTTPSConnection(origin.hostname,origin.port,context=context,timeout=5)
    try:
        connection.request('POST' if value is not None else 'GET',path,body=data,headers={'Authorization':'Bearer '+wire['token']})
        response=connection.getresponse();assert response.status==200
        return json.loads(response.read())
    finally:connection.close()
if args[:3]==['im','images','create']:
    name=args[args.index('--file')+1].removeprefix('image=')
    blob=Path(name).read_bytes();answer=call('/fixture-image-upload',{'body':base64.b64encode(blob).decode()})
    print(json.dumps({'ok':True,'identity':'bot','data':answer}))
elif args[:2]==['api','GET'] and args[2].startswith('/open-apis/im/v1/images/'):
    key=args[2].rsplit('/',1)[-1];answer=call('/fixture-own-image/'+key)
    raw=base64.b64decode(answer['body']);name=args[args.index('--output')+1]
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:out.write(raw)
    print(json.dumps({'saved_path':str(Path(name).absolute()),'size_bytes':answer.get('reported_size',len(raw)),'content_type':answer['mime']}))
else:sys.exit(97)
'''

class Msg003NormalImageContracts(Msg003Contracts):
    threaded=False
    # Only one normal assembly invocation, all boundaries and negative GETs
    # share this immutable run. Inherited guard contracts are not collected.
    test_01_distinct_zero_local_topology_and_source_pins=None
    test_02_no_authority_or_native_side_effect_without_original_run=None
    test_03_authentic_signed_original_source_image_and_channel=None
    test_04_driver_does_not_replace_normal_dispatch_or_authority=None
    test_05_durable_unknown_exclusive_intent_and_cancellation_join=None
    test_06_no_native_success_from_unsigned_or_absent_evidence=None
    test_07_callback_cannot_substitute_original_native_readback=None
    test_08_owned_source_cli_tree_join=None
    test_09_failed_sdk_revalidation_clears_cached_verdict=None
    test_10_existing_unknown_recovers_by_original_signed_query=None
    test_11_source_cli_stubborn_root_and_admission_cleanup=None
    test_12_v2_root_descriptor_is_explicit_pinned_and_not_authority=None
    test_13_v2_signed_source_thread_and_unknown_root_cas=None
    test_14_human_return_requires_started_original_v2_and_clears_cache=None
    test_15_human_signed_return_exact_actual_projection=None

    async def test_normal_cli_image_and_original_readback(self):
        import base64,copy,fcntl,http.client,re,ssl,time
        import remote_text_fixture as text
        import normal_remote_assembly_fixture as native
        from urllib.parse import urlsplit
        from hostd.agent_signed_reads import _matches
        test=self
        clock_path=self.source.phase_dir/'fixture-matrix-clock.json'
        clock_lock=self.source.phase_dir/'fixture-matrix-clock.lock'
        test.fixture.write(clock_path,json.dumps({'time':None}))
        test.fixture.write(clock_lock,'')
        # Only this owned synthetic child's clock dependency is controlled.
        # Transport/source guards remain the original pinned implementation.
        clock_wire="""
import fcntl as _fixture_fcntl,json as _fixture_json,stat as _fixture_stat,time as _fixture_time
_fixture_real_time=_fixture_time.time
_fixture_clock_path="""+repr(str(clock_path))+"""
_fixture_clock_lock="""+repr(str(clock_lock))+"""
def _fixture_matrix_time():
    fd=os.open(_fixture_clock_path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not (_fixture_stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and _fixture_stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=128):
            raise OSError('invalid fixture clock')
        value=_fixture_json.loads(os.read(fd,129))
        if set(value)!={'time'} or (value['time'] is not None and type(value['time']) is not int):
            raise OSError('invalid fixture clock')
        return value['time']
    finally:os.close(fd)
import functools as _fixture_functools
from hostd.remote_dispatch import RemoteDispatch as _fixture_dispatch
_fixture_dispatch_original=_fixture_dispatch.__init__
_fixture_clock_unsupplied=object()
@_fixture_functools.wraps(_fixture_dispatch_original)
def _fixture_dispatch_init(self,onboarding,*,scheduler,http_pool,base_env=None,clock=_fixture_clock_unsupplied):
    if clock is _fixture_clock_unsupplied:clock=_fixture_dispatch_original.__kwdefaults__['clock']
    if callable(clock):
        original_clock=clock
        def controlled_clock():
            fd=os.open(_fixture_clock_lock,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                info=os.fstat(fd)
                if not (_fixture_stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and _fixture_stat.S_IMODE(info.st_mode)==0o600 and info.st_size==0):
                    raise OSError('invalid fixture clock lock')
                _fixture_fcntl.flock(fd,_fixture_fcntl.LOCK_SH)
                epoch=_fixture_matrix_time()
                return original_clock() if epoch is None else epoch
            finally:os.close(fd)
        clock=controlled_clock
    return _fixture_dispatch_original(self,onboarding,scheduler=scheduler,http_pool=http_pool,base_env=base_env,clock=clock)
_fixture_dispatch.__init__=_fixture_dispatch_init
"""
        class ImageWire(text.RemoteTextFixture):
            def _build_packets(self):
                super()._build_packets();self.events=[r for r in self.events if r!=self.reply]
            def __init__(self):
                self.human_message=None;self.source_event=None;self.image_posts=[];self.image_gets=[];self.bad_image=False;self.bad_native=None;self.image_mode=None
                super().__init__(test.b,test.fixture)
                self.image_body=test.source.image.read_bytes()
                self.key='img_msg003_'+test.b.run_id
                self.image_hash=hashlib.sha256(self.image_body).hexdigest()
                redirect=text.redirect_wire(self.chat,self.root_mid,self.sent_mid)
                redirect=redirect.replace("if method == 'POST' and parts.path not in (", "if method == 'POST' and parts.path not in ('/open-apis/im/v1/messages', ")
                redirect=redirect.replace("elif method != 'POST' or url != '/query':", "elif not ((method == 'POST' and url == '/query') or (method == 'GET' and url == "+repr('/media/'+self.image_hash+'.png')+") or (method == 'POST' and url == '/fixture-image-upload') or (method == 'GET' and url == "+repr('/fixture-own-image/'+self.key)+")):")
                if test.threaded:
                    redirect=redirect.replace('class _ZeroNativeHTTPS', '_zero_native_paths = _zero_native_paths | {'+repr('/open-apis/im/v1/messages/om_human'+test.b.run_id)+'}\nclass _ZeroNativeHTTPS')
                    redirect=redirect.replace("        context = kwargs.get('context')", "        if host == '127.0.0.1:'+str(_origin_port) and port is None:\n            host,port='127.0.0.1',_origin_port\n            if kwargs.get('context') is None:kwargs['context']=ssl.create_default_context()\n        context = kwargs.get('context')")
                    redirect=redirect.replace("(method == 'GET' and url == "+repr('/fixture-own-image/'+self.key)+")", "(method == 'GET' and url == "+repr('/fixture-own-image/'+self.key)+") or (method == 'GET' and url == "+repr('/bind/api/channels/'+test.b.doc['channel_id']+'/people')+")")
                self.redirect=redirect
                test.fixture.write(test.b.wire_file,test.fixture.STARTUPWIRE+redirect+clock_wire)
                cli='#!'+sys.executable+'\nimport os\nos.environ[\"ZERO_LOCAL_RELAY_PORT\"]='+repr(str(test.b.relay.server.server_port))+'\nCA='+repr(str(test.b.relay.cert))+'\nWIRE='+repr(str(self.wire_path))+'\nORIGIN='+repr(test.b.relay.origin)+'\n'+test.fixture.STARTUPWIRE+redirect+IMAGE_CLI
                test.fixture.write(test.b.node,cli,0o700)
                for path in (test.b.catalog,test.b.legacy):
                    doc=json.loads(path.read_text());doc['buzz']['cli_sha256']=test.fixture.digest(test.b.node)
                    test.fixture.write(path,json.dumps(doc))
                base=test.b.relay.server.RequestHandlerClass;outer=self
                class Handler(base):
                    def do_GET(self):
                        if test.threaded and self.path=='/bind/api/channels/'+test.b.doc['channel_id']+'/people':
                            auth=self.headers['Authorization'];assert auth.startswith('Nostr ')
                            event=json.loads(base64.b64decode(auth[6:]));assert outer.gs._nip01_event_verified(event)
                            assert event['pubkey']==test.source.doc['human_pubkey']
                            assert ['u',outer.case.relay.origin+self.path] in event['tags'] and ['method','GET'] in event['tags']
                            payload=json.dumps({'channel':test.b.doc['channel_id'],'people':{},'union_ids':{test.source.doc['human_pubkey']:'on_originalhuman'}}).encode()
                            self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                        elif self.path=='/media/'+outer.image_hash+'.png':
                            outer.record('media_get',{})
                            self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(outer.image_body)));self.end_headers();self.wfile.write(outer.image_body)
                        else:super().do_GET()
                test.b.relay.server.RequestHandlerClass=Handler
                if test.threaded:
                    self.messages[self.root_mid].update(msg_type='post',create_time=str(self.now*1000),body={'content':test.source.body1.read_text()})
                    human=test.source.doc['human_pubkey']
                    profile=self.gs.sign_event('4'.zfill(64),0,[],json.dumps({'display_name':'Signed Synthetic'}),self.now-1)
                    self.events.append(profile)
                    native=copy.deepcopy(self.messages[self.root_mid]);native['sender']={'sender_type':'user','id_type':'open_id','id':'ou_originalhuman'}
                    normalized=__import__('hostd.bot_clients',fromlist=['BotLarkCli']).BotLarkCli._normalize_message(native)
                    projection=self.gs.route_feishu_message(normalized,open_id_to_pubkey={'on_originalhuman':human},bot_member_to_pubkey={},channel_members={human,test.source.doc['agent_pubkey']},names={human:'Signed Synthetic'},now=__import__('datetime').datetime.fromtimestamp(self.now,__import__('datetime').timezone.utc),resolve_id=lambda ident:'on_originalhuman' if ident=='ou_originalhuman' else '')
                    self.root_event=self.gs.sign_event(self.mirror_key,9,[['h',test.b.doc['channel_id']],
                        ['feishu',self.root_mid],['feishu-root',self.root_mid],['p',test.source.doc['agent_pubkey']],
                        ['imeta','url '+test.source.doc['link_base']+'/media/'+self.image_hash+'.png','x '+self.image_hash,'m image/png']],
                        projection.text,self.now)
                    self.events=[e for e in self.events if not (e['kind']==9 and ['feishu',self.root_mid] in e['tags'])]+[self.root_event]
                    self.messages[self.root_mid].update(msg_type='post',create_time=str(self.now*1000),body={'content':test.source.body1.read_text()})
                    self.wire.update(events=self.events,native_root=self.messages[self.root_mid])
                    test.fixture.write(self.wire_path,text.encoded(self.wire));self.wire_hash=test.fixture.digest(self.wire_path)
                test.b.repin()
            def _query_clock(self,principal):
                if principal==self.case.agent_pub:
                    # Publication is atomic; the server must not block a
                    # previously signed request behind the clock handoff.
                    epoch=json.loads(clock_path.read_text())['time']
                    if epoch is not None:return epoch
                return super()._query_clock(principal)
            def _query(self,headers,body):
                rows=super()._query(headers,body)
                if self.source_event is not None and any(_matches(self.source_event,f) for f in json.loads(body)):
                    rows.append(json.loads(json.dumps(self.source_event)))
                return rows
            def _native(self,method,path,headers,body):
                endpoint=urlsplit(path).path
                if test.threaded and endpoint==text.TOKEN_PATH and json.loads(body)['app_id']==test.source.doc['sync_app_id']:
                    assert json.loads(body)['app_secret']==test.fixture.SECRET
                    return 200,{'code':0,'tenant_access_token':self.token,'expire':7200},False
                if test.threaded and self.human_message is not None and method=='GET' and endpoint=='/open-apis/im/v1/messages/'+self.human_message['message_id']:
                    from urllib.parse import parse_qs
                    params=parse_qs(urlsplit(path).query);assert headers['Authorization']=='Bearer '+self.token
                    assert params.pop('card_msg_content_type', None)==['user_card_content']
                    row=copy.deepcopy(self.human_message)
                    if params=={'user_id_type':['union_id']} and row['sender']['sender_type']=='user':row['sender']={'sender_type':'user','id_type':'union_id','id':'on_originalhuman'}
                    return 200,{'code':0,'data':{'items':[row]}},False
                if test.threaded and method=='GET' and endpoint=='/open-apis/im/v1/messages/'+self.root_mid:
                    from urllib.parse import parse_qs
                    params=parse_qs(urlsplit(path).query);assert headers['Authorization']=='Bearer '+self.token
                    assert params.pop('card_msg_content_type', None)==['user_card_content']
                    row=copy.deepcopy(self.messages[self.root_mid])
                    if params=={'user_id_type':['union_id']}:row['sender']={'sender_type':'user','id_type':'union_id','id':'on_originalhuman'}
                    else:assert params=={'user_id_type':['open_id']}
                    return 200,{'code':0,'data':{'items':[row]}},False
                if endpoint in ('/fixture-image-upload','/fixture-own-image/'+self.key):
                    assert headers.get('Authorization')=='Bearer '+self.token
                    state=text.readonly(test.b.outputs[0]);assert state and state['binding']==[]
                    with self.lock:
                        if method=='POST':
                            raw=base64.b64decode(json.loads(body)['body']);assert raw==self.image_body
                            assert not self.image_posts
                            with text.closing(text.sqlite3.connect(test.b.outputs[0].as_uri()+'?mode=ro',uri=True)) as db:
                                row=db.execute('SELECT ordinal,state,image_key,content_hash FROM remote_image_upload WHERE source_id=?',(self.source_event['id'],)).fetchall()
                            assert row==[(0,'unknown','',self.image_hash)]
                            self.image_posts.append(copy.deepcopy(row));data={'image_key':self.key}
                        else:
                            self.image_gets.append(self.key);raw=b'bad' if self.bad_image else self.image_body
                            if self.image_mode=='changed':raw=self.image_body+b'changed'
                            if self.image_mode=='empty':raw=b''
                            data={'body':base64.b64encode(raw).decode(),'mime':'image/jpeg' if self.image_mode=='mime' else 'image/png'}
                            if self.image_mode=='size':data['reported_size']=len(raw)+1
                    return 200,data,False
                if method=='GET' and endpoint=='/open-apis/application/v6/scopes':
                    status,payload,drop=super()._native(method,path,headers,body)
                    payload['data']['scopes'].append({'scope_name':'im:resource:upload','scope_type':'tenant','grant_status':1})
                    return status,payload,drop
                if method=='POST' and endpoint==('/open-apis/im/v1/messages/'+self.root_mid+'/reply' if test.threaded else '/open-apis/im/v1/messages'):
                    assert headers.get('Authorization')=='Bearer '+self.token
                    data=json.loads(body);assert data['msg_type']=='interactive'
                    if test.threaded:assert set(data)=={'msg_type','content','uuid','reply_in_thread'} and data['reply_in_thread'] is True
                    else:assert data['receive_id']==self.chat
                    card=json.loads(data['content']);assert [e['img_key'] for e in card['elements'] if e.get('tag')=='img']==[self.key]
                    state=text.readonly(test.b.outputs[0]);assert state['binding']==[]
                    rows=[r for r in state['remote_delivery'] if r['source_id']==self.source_event['id']]
                    assert len(rows)==1 and rows[0]['status']=='unknown' and rows[0]['root_id']==(self.root_event['id'] if test.threaded else '')
                    assert rows[0]['content_hash']==hashlib.sha256(data['content'].encode()).hexdigest()
                    with text.closing(text.sqlite3.connect(test.b.outputs[0].as_uri()+'?mode=ro',uri=True)) as db:
                        images=db.execute('SELECT ordinal,state,image_key FROM remote_image_upload WHERE source_id=?',(self.source_event['id'],)).fetchall()
                    assert images==[(0,'acked',self.key)]
                    assert not self.posts
                    self.posts.append(copy.deepcopy(data));self.unknown_before_post.append(copy.deepcopy(state))
                    self.messages[self.sent_mid]={'message_id':self.sent_mid,'chat_id':self.chat,'root_id':self.root_mid if test.threaded else self.sent_mid,'create_time':str(int(time.time())*1000),'msg_type':'interactive','sender':{'sender_type':'app','id_type':'app_id','id':text.APP},'body':{'content':data['content']}}
                    self.posted.set();return 200,{'code':0,'data':{'message_id':self.sent_mid}},False
                response=super()._native(method,path,headers,body)
                if method=='GET' and endpoint=='/open-apis/im/v1/messages/'+self.sent_mid and self.bad_native:
                    status,payload,drop=response;payload=copy.deepcopy(payload)
                    if payload['data']['items']:payload['data']['items'][0].update(self.bad_native)
                    return status,payload,drop
                return response
        world=ImageWire()
        # ImageWire pins every static packet/script before the root manifest is
        # admitted. Dynamic source publication is an authenticated source action.
        revision=self.bp._revision
        if self.threaded:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            self.source.base.cfg['mirror_pubkey']=world.mirror
            self.source.base.cfg['people_api']={'base_url':self.b.relay.origin,'signer_env_file':str(self.source.human_env)}
            self.source.base.write(Path(self.source.base.cfg['mirror_env_file']),'BUZZ_PRIVATE_KEY='+world.mirror_key+'\nBUZZ_RELAY_URL='+self.source.base.rt['relay_url']+'\n')
            self.source.doc['link_base']=self.b.relay.origin
            old_relay=self.source.base.doc['relay_url'];new_relay=self.b.relay.origin.replace('https://','wss://')
            self.source.base.doc.update(relay_url=new_relay,people_url=self.b.relay.origin)
            self.source.base.rt.update(relay_url=new_relay,trusted_relays=[new_relay])
            self.source.base.write(self.source.base.onboarding,self.source.base.rt)
            for env_path in (self.source.base.run/'runtime').rglob('*.env'):
                self.fixture.write(env_path,env_path.read_text().replace(old_relay,new_relay))
            self.fixture.write(self.source.base.bundle,self.source.base.bundle.read_bytes()+b'\n'+self.b.relay.cert.read_bytes())
            # Root event was prepared with the original scenario link_base;
            # republish signed fixture bytes before admitting any runtime.
            tags=copy.deepcopy(world.root_event['tags'])
            next(t for t in tags if t[0]=='imeta')[1]='url '+self.b.relay.origin+'/media/'+world.image_hash+'.png'
            world.root_event=self.gs.sign_event(world.mirror_key,9,tags,world.root_event['content'],world.now)
            world.events=[e for e in world.events if not (e['kind']==9 and ['feishu',world.root_mid] in e['tags'])]+[world.root_event]
            world.wire['events']=world.events;self.fixture.write(world.wire_path,text.encoded(world.wire));world.wire_hash=self.fixture.digest(world.wire_path)
            block=self.source.base.cfg['agents'][self.source.base.cfg['desk_pubkey']]
            key=b'k'*32;nonce=b'n'*12;folder=Path(block['lark_data_dir'])/'lark-cli';folder.mkdir(mode=0o700)
            self.fixture.write(folder/'master.key',key)
            self.fixture.write(folder/('appsecret_'+block['app_id']+'.enc'),nonce+AESGCM(key).encrypt(nonce,self.fixture.SECRET.encode(),None))
        self.source.base.cfg['chat_id']=world.chat;self.source.base.write(self.source.base.config,self.source.base.cfg)
        groups=json.loads(self.source.base.fixture.read_text());groups['hostd-test-群X']=world.chat;self.source.base.write(self.source.base.fixture,groups)
        self.source.base.doc['fixture_sha256']=test.fixture.digest(self.source.base.fixture);self.source.base.save()
        self.source.doc['chat_id']=world.chat;self.source.save()
        self.source.prepared=self.source.base.m.PreparedRun.check(self.source.base.manifest,reviewed_source=self.source.base.source,reviewed_revision=revision,fixture_sha256=test.fixture.digest(self.source.base.fixture),command_runner=self.source.base.runner)
        self.scenario=self.source.check()
        self.b.repin()
        self.bp=self.m.zero.ZeroLocalRunPlan.check(self.b.manifest,**self.b.check_args)
        if self.threaded:
            phase=self.source.phase_dir;doc=self.source.doc
            intent={'version':1,'status':'unknown','phase':'msg001','run_id':RUN,'revision':revision,'binding':doc['initial_binding'],'chat':world.chat,'channel':doc['channel_id'],'bodyhash':self.fixture.digest(self.source.body1),'idempotency':'msg001-'+RUN,'root':None,'started':world.now-1}
            self.fixture.write(phase/'msg001.json',json.dumps(intent))
            self.doc.update(version=2,root={'event_id':world.root_event['id'],'event_sha256':self.m._digest(world.root_event),'native_message_id':world.root_mid})
        self.doc.update(host_b_manifest_sha256=hashlib.sha256(self.bp._manifest_bytes).hexdigest(),buzz_cli_sha256=test.fixture.digest(self.b.node),scenario_manifest_sha256=self.source.pin)
        probe=TESTS/'hostd_probes';sys.path.insert(0,str(probe))
        import event_recorder as recorder
        import check_feishu_events as checker
        sdk_started=int(time.time());sdk_session='msg003-session-'+RUN
        recorder_manifest=self.source.phase_dir/'msg003-recorder-manifest.json'
        test.fixture.write(recorder_manifest,json.dumps({'schema_version':2,'run_id':RUN,'window':{'start':sdk_started,'end':sdk_started+90},'target':{'chat_id':world.chat,'apps':{'own':'cli_agent','foreign':'cli_foreign'}}}))
        self.doc['sdk']={'manifest_path':str(recorder_manifest),'manifest_sha256':test.fixture.digest(recorder_manifest),'checker_sha256':test.fixture.digest(probe/'check_feishu_events.py'),'session_sha256':hashlib.sha256(sdk_session.encode()).hexdigest(),'event_type':'im.message.receive_v1','started':sdk_started,'deadline':sdk_started+90}
        self.save_source();self.sr=self.admit_source();self.plan=self.m.Msg003RunPlan.check(self.bp,self.sr,self.scenario,source_agent_name='local')
        diagnostic_path=self.source.phase_dir/'msg003-child-diagnostic.log'
        diagnostic_fd=os.open(diagnostic_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        diagnostic_file=os.fdopen(diagnostic_fd,'wb');self.addCleanup(diagnostic_file.close)
        class TraceCapture(test.fixture.CapturePopen):
            def __call__(self,argv,**kwargs):
                kwargs['stderr']=diagnostic_file
                return super().__call__(argv,**kwargs)
        capture=TraceCapture(self.b,self.bp);self.addCleanup(capture.emergency_reap)
        self.owned=self.m.zero.OwnedZeroLocalRun(self.bp,popen_factory=capture)
        self.addCleanup(self.owned.stop)
        self.b.relay.start();self.owned.start()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                if self.owned.readback()['runtime_started']:break
            except self.m.zero.ZeroLocalRunError:pass
            await asyncio.sleep(.1)
        else:self.fail('normal zero-local root did not become ready')
        calls=[]
        def publish(argv,**kwargs):
            calls.append(argv);assert argv[1:3]==['messages','send']
            intent=json.loads((self.source.phase_dir/'msg003-intent.json').read_text());assert intent['state']=='unknown'
            assert kwargs['env']['BUZZ_PRIVATE_KEY']==self.b.agent_key
            stamp=int(time.time());doc=json.loads(self.plan._scenario_doc)
            extra=[['e',world.root_event['id'],'','root'],['e',world.root_event['id'],'','reply']] if self.threaded else []
            if self.threaded:assert argv[-2:]==['--reply-to',world.root_event['id']] and intent['root']==self.doc['root']
            world.source_event=self.gs.sign_event(self.b.agent_key,9,[['h',self.b.doc['channel_id']],*extra,['imeta','url '+self.b.relay.origin+'/media/'+world.image_hash+'.png','x '+world.image_hash,'m image/png','size '+str(len(world.image_body))]],kwargs['input'],stamp)
            # Real signed event has been published; deliberately lose CLI response.
            raise subprocess.TimeoutExpired(argv,1)
        self.driver=self.m.Msg003Driver(self.plan,hostd=self.owned,runner=publish)
        namespace={'_origin_port':self.b.relay.server.server_port}
        # Same strictly verified native HTTPS transport in this parent; no
        # target/proof/dispatcher/adapter callback is substituted.
        original_init=http.client.HTTPSConnection.__init__;original_request=http.client.HTTPSConnection.request
        exec(world.redirect,namespace)
        http.client.HTTPSConnection.__init__=original_init;http.client.HTTPSConnection.request=original_request
        with mock.patch.dict(os.environ,self.bp.environment(),clear=True),mock.patch.object(http.client.HTTPSConnection,'__init__',namespace['_ZeroNativeHTTPS'].__init__),mock.patch.object(http.client.HTTPSConnection,'request',namespace['_ZeroNativeHTTPS'].request):
            if self.threaded:
                try:await self.driver._root_readback()
                except Exception as error:
                    import traceback
                    self.fail('original root readback frames: '+str([(Path(f.filename).name,f.lineno,f.name,type(error).__name__) for f in traceback.extract_tb(error.__traceback__)]))
            published=await self.driver.publish_once();self.assertTrue(published.readback()['source_readback']);self.assertEqual(len(calls),1)
            self.assertEqual(len(world.posts),0)
            blob=await self.driver._reader().read_media(self.b.relay.origin+'/media/'+world.image_hash+'.png',sha256=world.image_hash,mime='image/png',size=len(world.image_body))
            self.assertEqual(blob,world.image_body)
            deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                result=await self.driver.observe()
                if result.readback()['native_readback']:break
                await asyncio.sleep(.5)
            else:
                state=text.readonly(self.b.outputs[0])
                typed=[]
                try:await self.driver._native(self.driver._event,self.driver._snapshot(self.driver._event['id']))
                except Exception as error:
                    import traceback
                    typed=[(Path(frame.filename).name,frame.lineno,frame.name,type(error).__name__) for frame in traceback.extract_tb(error.__traceback__)]
                diagnostic={'errors':world.failures,'posts':len(world.posts),'uploads':len(world.image_posts),'image_gets':len(world.image_gets),'native_methods':[list(x[:2]) for x in world.native_calls],'grant_revisions':[g['revision'] for g in state['remote_grant']],'delivery_states':[r['status'] for r in state['remote_delivery']], 'images':[row['state'] for row in state.get('remote_image_upload',[])], 'source_matches':[row['source_id']==self.driver._event['id'] for row in state['remote_delivery']], 'snapshot_counts':[(len(snapshot[2]),len(snapshot[3])) for snapshot in [self.driver._snapshot(self.driver._event['id'])] if snapshot],'media_gets':sum(kind=='media_get' for seq,kind,value in world.trace),'signed_kind_counts':[q['filters'][0].get('kinds') for q in world.signed_queries[-12:]]}
                self.fail('normal image readback pending: '+str(diagnostic)+'; readback trace:'+str(typed)+'; typed trace: '+diagnostic_path.read_text()[-2000:])
            self.assertEqual(result.readback()['status'],'observed');self.assertEqual(len(world.posts),1);self.assertEqual(len(world.image_posts),1);self.assertTrue(world.image_gets)
            if self.threaded:
                def no_resend(*args,**kwargs):self.fail('threaded UNKNOWN republished')
                restarted=self.m.Msg003Driver(self.plan,hostd=self.owned,runner=no_resend)
                recovered=await restarted.publish_once();self.assertTrue(recovered.readback()['source_readback'])
                self.assertEqual(len(calls),1);self.assertEqual(len(world.posts),1);self.assertEqual(len(world.image_posts),1)
                native_root=copy.deepcopy(world.messages[world.root_mid])
                for change in ({'deleted':True},{'body':{'content':'{"zh_cn":{"content":[]}}'}},{'root_id':'om_crossroot'}):
                    world.messages[world.root_mid]={**native_root,**change}
                    rejected=await restarted.publish_once();self.assertFalse(rejected.readback()['source_readback'])
                    self.safe(rejected)
                world.messages[world.root_mid]=native_root
                human={'message_id':'om_human'+RUN,'chat_id':world.chat,'root_id':world.root_mid,'parent_id':world.sent_mid,
                    'msg_type':'text','create_time':str(int(time.time())*1000),'sender':{'sender_type':'user','id_type':'open_id','id':'ou_originalhuman'},
                    'body':{'content':json.dumps({'text':'SYNTHETIC_HUMAN_RETURN'})}}
                # No ordinary A daemon has published a signed mirror return.
                # A valid native projection alone cannot satisfy this gate.
                world.human_message=human
                rejected=await restarted.observe_human_return(human['message_id'])
                self.assertFalse(rejected.readback()['human_return']);self.assertEqual(rejected.readback()['status'],'pending')
                self.assertTrue(any(any(f.get('#feishu')==[human['message_id']] for f in q['filters']) for q in world.signed_queries))
                for change in ({'parent_id':''},{'parent_id':world.root_mid},{'root_id':'om_crossroot'},
                        {'sender':{'sender_type':'app','id_type':'app_id','id':text.APP}},{'msg_type':'image'}):
                    world.human_message={**human,**change}
                    rejected=await restarted.observe_human_return(human['message_id'])
                    self.assertFalse(rejected.readback()['human_return']);self.safe(rejected)
                self.assertEqual(len(calls),1);self.assertEqual(len(world.posts),1)
                self.assertFalse(text.readonly(self.b.outputs[0])['binding'])
                return  # v1 below already owns the finite SDK/negative-image suite.
            # An actual installed SDK parser materializes its P2 model and calls
            # the original recorder. Only the input socket frame and lifecycle
            # marker are synthetic; no callback verdict or SDK object is faked.
            from lark_oapi.core.model.raw_request import RawRequest
            sdk_path=self.source.phase_dir/'msg003-sdk.jsonl'
            recorder._write(sdk_path,{'schema_version':2,'run_id':RUN,'t_recv':time.time(),'bot':'own','app_id':'cli_agent','session_id':sdk_session,'connection_id':sdk_session,'type':'_connection_started'})
            dispatcher=recorder.handler_for(sdk_path,RUN,'own','cli_agent',{'session_id':sdk_session})
            sdk_event='msg003-callback-'+RUN
            packet={'schema':'2.0','header':{'event_id':sdk_event,'event_type':'im.message.receive_v1','create_time':str(int(time.time()*1000000)),'token':'','app_id':'cli_agent'},'event':{'sender':{'sender_id':{'open_id':'ou_synthetic'},'sender_type':'app','tenant_key':'synthetic'},'message':{'message_id':world.sent_mid,'chat_id':world.chat,'chat_type':'group','message_type':'interactive','create_time':str(int(time.time()*1000)),'content':'SDK_CANARY','mentions':[]}}}
            request=RawRequest();request.uri='/offline-msg003-sdk';request.headers={};request.body=json.dumps(packet).encode()
            sdk_response=dispatcher.do(request);self.assertEqual(sdk_response.status_code,200)
            sdk_raw=sdk_path.read_bytes();self.assertNotIn(b'SDK_CANARY',sdk_raw)
            sdk_rows=[json.loads(line) for line in sdk_raw.splitlines()];self.assertEqual([r['type'] for r in sdk_rows],['_connection_started','im.message.receive_v1'])
            checker.validate_recorder_manifest(json.loads(recorder_manifest.read_bytes()))
            for row in sdk_rows:checker.validate_row(row)
            pins=result.readback();event_sha=hashlib.sha256(sdk_event.encode()).hexdigest()
            join=(sdk_path,test.fixture.digest(sdk_path),pins['source_event_sha256'],pins['native_readback_sha256'],event_sha)
            # SDK joining rechecks physical native evidence and requires the
            # SQL snapshot to remain identical across that IO. A genuine proof
            # renewal may therefore return pending even for this original
            # callback. Restore current native evidence, then join the same
            # immutable artifact; never replay publication or synthesize PASS.
            # The two positive joins share one bound; all negatives stay single.
            sdk_recovery_deadline=time.monotonic()+50
            async def original_sdk_join():
                while True:
                    receipt=await self.driver.observe()
                    if receipt.readback()['native_readback']:
                        receipt=await self.driver.join_sdk_evidence(join)
                        if receipt.readback()['sdk_callback']:return receipt
                    if time.monotonic()>=sdk_recovery_deadline:
                        state=receipt.readback()
                        self.fail('original SDK join did not settle: '+json.dumps({
                            key:state[key] for key in ('status','source_readback','native_readback','sdk_callback')}))
                    await asyncio.sleep(.1)
            joined=await original_sdk_join();self.assertTrue(joined.readback()['sdk_callback']);self.safe(joined)
            # The original ACK/proof and first SDK join above use real time.
            # This offline denial matrix does not renew a 30-second proof;
            # control its observation clock so scheduling cannot expire that
            # fixture halfway through identity/image negatives or proof renewals.
            # Atomic replace alone cannot prevent a concurrent producer from
            # sampling a newer real clock before publication. Serialize the
            # epoch sample + handoff with every child clock read, so an in-flight
            # proof cannot become newer than the frozen observer's clock.
            with clock_lock.open('rb') as clock_guard:
                fcntl.flock(clock_guard.fileno(),fcntl.LOCK_EX)
                proof=self.driver._snapshot(self.driver._event['id'])[0]
                observation_time=int(self.driver.clock())
                self.assertLessEqual(proof['proof_checked_at'],observation_time)
                self.assertLess(observation_time,proof['proof_valid_until'])
                clock_next=clock_path.with_suffix('.next')
                test.fixture.write(clock_next,json.dumps({'time':observation_time}))
                with clock_next.open('rb') as clock_file:os.fsync(clock_file.fileno())
                os.replace(clock_next,clock_path)
                self.driver.clock=lambda:observation_time
            # A concurrent genuine renewal can make the original driver's
            # before/after snapshots differ. Recovery positives wait for its
            # actual readback, without requiring a new proof to be written.
            # All recovery waits share one bound; negative checks stay single.
            recovery_deadline=time.monotonic()+50
            async def restored_native():
                while True:
                    receipt=await self.driver.observe()
                    state=receipt.readback()
                    if state['native_readback']:return receipt
                    if time.monotonic()>=recovery_deadline:
                        self.fail('native recovery did not settle: '+json.dumps({
                            key:state[key] for key in ('status','native_readback','source_readback')}))
                    await asyncio.sleep(.1)
            # Each changed sanitized row retains a correct file digest, so a
            # rejection is about the original join identity, not a stale hash.
            for field,bad in (('run_id','other-run'),('app_id','cli_foreign'),('chat_id','oc_foreign'),('session_id','foreign-session'),('message_id','om_foreign'),('event_id','foreign-event')):
                changed=copy.deepcopy(sdk_rows);changed[-1][field]=bad
                path=self.source.phase_dir/('sdk-negative-'+field+'.jsonl');test.fixture.write(path,'\n'.join(json.dumps(row) for row in changed)+'\n')
                restored=await restored_native();self.assertTrue(restored.readback()['native_readback'])
                rejected=await self.driver.join_sdk_evidence((path,test.fixture.digest(path),join[2],join[3],join[4]));self.assertFalse(rejected.readback()['sdk_callback']);self.safe(rejected)
            restored=await restored_native();self.assertTrue(restored.readback()['native_readback'])
            self.assertFalse((await self.driver.join_sdk_evidence((sdk_path,'0'*64,*join[2:]))).readback()['sdk_callback'])
            restored=await restored_native();self.assertTrue(restored.readback()['native_readback'])
            world.bad_image=True;self.assertFalse((await self.driver.join_sdk_evidence(join)).readback()['sdk_callback']);world.bad_image=False
            r=await restored_native();self.assertTrue(r.readback()['native_readback'])
            joined=await original_sdk_join();self.assertTrue(joined.readback()['sdk_callback'])
            original_grants=copy.deepcopy(text.readonly(self.b.outputs[0])['remote_grant'])
            immutable=test.fixture.digest(self.b.outputs[0])
            for wrong in ({'chat_id':'oc_foreign'},{'sender':{'sender_type':'app','id_type':'app_id','id':'cli_foreign'}},{'root_id':'om_foreign'}):
                world.bad_native=wrong;r=await self.driver.observe();self.assertFalse(r.readback()['native_readback']);self.assertEqual(r.readback()['status'],'pending')
            original_card=json.loads(world.posts[0]['content'])
            for mode in ('wrong_key','missing_image','extra_image','partial_body'):
                card=copy.deepcopy(original_card)
                if mode=='wrong_key':next(e for e in card['elements'] if e.get('tag')=='img')['img_key']='img_foreign'
                if mode=='missing_image':card['elements']=[e for e in card['elements'] if e.get('tag')!='img']
                if mode=='extra_image':card['elements'].insert(-1,{'tag':'img','img_key':'img_extra','alt':{'tag':'plain_text','content':''}})
                world.bad_native={'body':{} if mode=='partial_body' else {'content':json.dumps(card)}}
                r=await self.driver.observe();self.assertFalse(r.readback()['native_readback']);self.safe(r)
            world.bad_native=None
            for mode in ('changed','empty','mime','size'):
                world.image_mode=mode;r=await self.driver.observe();self.assertFalse(r.readback()['native_readback']);self.safe(r)
            world.image_mode=None
            world.bad_native=None;world.bad_image=True
            r=await self.driver.observe();self.assertFalse(r.readback()['native_readback'])
            world.bad_image=False;r=await restored_native();self.assertTrue(r.readback()['native_readback'])
            await self.driver.publish_once();self.assertEqual(len(calls),1)
            self.assertEqual(len(world.posts),1);self.assertEqual(len(world.image_posts),1)
            self.assertEqual(world.failures,[]);self.safe(r)
            self.assertEqual(original_grants,text.readonly(self.b.outputs[0])['remote_grant'])
            expiry=self.driver._snapshot(self.driver._event['id'])[0]['proof_valid_until']
            current_clock=self.driver.clock;self.driver.clock=lambda:expiry
            with self.assertRaises(self.m.Msg003Error):self.driver._snapshot(self.driver._event['id'])
            self.driver.clock=current_clock
        stopped=self.owned.stop();self.assertEqual(stopped['status'],'stopped');self.assertEqual(test.fixture.session_members(capture.process.pid),set())

class Msg003ThreadedNormalImageContracts(Msg003NormalImageContracts):
    threaded=True
    async def test_normal_cli_image_and_original_readback(self):
        await super().test_normal_cli_image_and_original_readback()
