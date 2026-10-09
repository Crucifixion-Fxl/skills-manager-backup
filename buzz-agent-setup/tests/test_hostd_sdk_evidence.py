"""Actual SDK callback/connection wire and protected passive metadata evidence."""
import asyncio
import importlib
import inspect
import json
import hashlib
import os
import stat
import tempfile
import time
import contextlib
from pathlib import Path
import sys
import unittest
from unittest import mock

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS));sys.path.insert(0,str(SCRIPTS/'hostd'))
from hostd.sdk_evidence import FeedEvidenceWire,EvidenceConfig,EvidenceTap,EvidenceError,process_proof,TEST_APPS
APP='cli_aa48b41531785bfc'
try:
    import lark_oapi
    import feishu_feed as ff
except ImportError:
    HAS_SDK=False
else:HAS_SDK=True

@unittest.skipUnless(HAS_SDK,'actual installed lark-oapi SDK required; metadata gates remain separate')
class SDKWire(unittest.TestCase):
    def test_actual_sdk_connect_then_model_callback_carries_native_session(self):
        wire=FeedEvidenceWire(APP,'unit-sdk-run','a'*64)
        # Legacy function has no opt-in keyword: execute its actual callback as
        # baseline behavior, never replace a readiness/session/verdict method.
        kwargs={'evidence_wire':wire} if 'evidence_wire' in inspect.signature(ff.handler).parameters else {}
        dispatcher=ff.handler(APP,**kwargs)
        rows=[];connection=mock.Mock()
        payload={'schema':'2.0','header':{'event_type':'im.message.receive_v1','event_id':'evt-native','app_id':APP},
            'event':{'message':{'message_id':'om-native','chat_id':'oc_scope','root_id':'om-root','message_type':'text',
                'content':'private-body-secret'},'sender':{'sender_id':{'open_id':'private-operator'}}}}
        with mock.patch('lark_oapi.ws.client.ExpiringCache'),mock.patch.object(ff,'emit',rows.append), \
                mock.patch.object(ff.lark.ws.Client,'_get_conn_url',return_value='wss://transport.invalid?device_id=actual-device&service_id=1'):
            client=ff.EventClient(APP,'unit-fixture-secret',event_handler=dispatcher)
            client.evidence_wire=wire
            with mock.patch('lark_oapi.ws.client.websockets.connect',new=mock.AsyncMock(return_value=connection)), \
                    mock.patch('lark_oapi.ws.client.loop.create_task',side_effect=lambda coro:coro.close()):
                asyncio.run(client._connect())
            dispatcher._do_without_validation(json.dumps(payload).encode())
        self.assertIs(client._conn,connection)
        callback=next(row for row in rows if row['type']=='im.message.receive_v1')
        self.assertEqual(callback['message_id'],'om-native')
        self.assertIn('_sdk_evidence',callback)
        envelope=callback['_sdk_evidence']
        connected=next(row for row in rows if row['type']=='_connected')
        self.assertEqual(connected['_sdk_evidence'],envelope)
        self.assertEqual(envelope['run_id'],'unit-sdk-run')
        self.assertNotIn('private-body-secret',json.dumps(rows));self.assertNotIn('private-operator',json.dumps(rows))

class ConfigFixture:
    def setup_config(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.now=time.time();self.path=self.root/'config.json'
        self.data={'schema_version':1,'run_id':'unit-sdk-run','window':{'start':self.now-1,'end':self.now+60},
            'target':{'chat_id':'oc_scope','apps':{'hostd-test-a':APP}},'events_path':str(self.root/'events.jsonl'),
            'receipt_path':str(self.root/'receipt.json'),'max_rows':1000,'max_bytes':1048576,
            'source_sha256':{'hostd/'+n:hashlib.sha256((SCRIPTS/'hostd'/n).read_bytes()).hexdigest()
                for n in ('sdk_evidence.py','feishu_feed.py','__main__.py')}}
    def save(self):
        raw=(json.dumps(self.data)+'\n').encode();self.path.write_bytes(raw);self.path.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()
    def config(self):return EvidenceConfig.check(self.path,self.save())

class ProtectedConfig(ConfigFixture,unittest.TestCase):
    def setUp(self):self.setup_config()
    def tearDown(self):self.temp.cleanup()
    def test_explicit_hash_private_fresh_exact_schema(self):
        cfg=self.config();cfg.revalidate(fresh=True)
        self.assertFalse(Path(self.data['events_path']).exists());self.assertFalse(Path(self.data['receipt_path']).exists())
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,'0'*64)
        self.data['unknown_private_token']='never-write'
        with self.assertRaises(EvidenceError) as error:self.config()
        self.assertNotIn('never-write',str(error.exception))
    def test_wrong_app_chat_window_bounds_and_duplicate_keys_rejected(self):
        original=json.loads(json.dumps(self.data))
        cases=[{'target':{'chat_id':'oc_scope','apps':{'hostd-test-a':'cli_other'}}},
            {'target':{'chat_id':'other-chat','apps':{'hostd-test-a':APP}}},
            {'window':{'start':self.now-100,'end':self.now+1}},
            {'window':{'start':self.now-2,'end':self.now-1}},
            {'window':{'start':self.now+20,'end':self.now+30}},
            {'max_rows':1001},{'max_rows':True},{'max_bytes':1048577},
            {'max_bytes':0},{'source_sha256':{'private':'never-write'}},
            {'receipt_path':str(self.root/'events.jsonl')}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.data={**original,**changes}
                with self.assertRaises(EvidenceError):self.config()
        raw=b'{"schema_version":1,"schema_version":1}'
        self.path.write_bytes(raw);self.path.chmod(0o600)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,hashlib.sha256(raw).hexdigest())
    def test_mode_symlink_hardlink_fifo_and_existing_output_reject(self):
        pin=self.save();self.path.chmod(0o644)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,pin)
        self.path.chmod(0o600);link=self.root/'link';link.symlink_to(self.path)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(link,pin)
        link.unlink();os.link(self.path,link)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,pin)
        link.unlink();cfg=self.config();Path(self.data['events_path']).write_text('old')
        with self.assertRaises(EvidenceError):cfg.revalidate(fresh=True)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,pin)
        Path(self.data['events_path']).unlink();self.path.unlink();os.mkfifo(self.path,0o600)
        with self.assertRaises(EvidenceError):EvidenceConfig.check(self.path,pin)
    def test_config_inode_content_parent_and_source_drift_reject(self):
        cfg=self.config();original=self.path.read_bytes();self.path.rename(self.root/'held')
        self.path.write_bytes(original);self.path.chmod(0o600)
        with self.assertRaises(EvidenceError):cfg.revalidate()
        cfg=self.config();self.path.write_bytes(self.path.read_bytes()+b' ')
        with self.assertRaises(EvidenceError):cfg.revalidate()
        cfg=self.config();self.root.chmod(0o755)
        with self.assertRaises(EvidenceError):cfg.revalidate()
        self.root.chmod(0o700);cfg=self.config()
        import hostd.sdk_evidence as module
        original_read=module.read_file
        def drift(path,**kwargs):
            raw,pin,base=original_read(path,**kwargs)
            return (raw+b'# drift',pin,base) if Path(path).name=='feishu_feed.py' else (raw,pin,base)
        with mock.patch.object(module,'read_file',side_effect=drift):
            with self.assertRaises(EvidenceError):cfg.revalidate()

class Metadata(ConfigFixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.setup_config();self.tap=None;self.processes=[]
    async def asyncTearDown(self):
        if self.tap:await self.tap.close()
        for process in self.processes:
            if process.returncode is None:process.terminate()
            await process.wait()
        self.temp.cleanup()
    async def test_original_child_kernel_proof_rejects_argv_and_foreign_pid(self):
        argv=[sys.executable,'-I','-c','import time;time.sleep(30)']
        p=await asyncio.create_subprocess_exec(*argv);self.processes.append(p)
        for _ in range(50):
            try:proof=process_proof(p,argv);break
            except (FileNotFoundError,EvidenceError):await asyncio.sleep(.01)
        else:self.fail('actual original child did not settle')
        self.assertEqual(proof[0],p.pid)
        with self.assertRaises(EvidenceError):process_proof(p,argv+['foreign'])
        with self.assertRaises(EvidenceError):process_proof(type('Unknown',(),{'pid':p.pid})(),argv)
    async def test_wrong_native_argv_unknown_child_and_fake_connected_disable_only_evidence(self):
        self.tap=EvidenceTap(self.config());await self.tap.start()
        argv=[sys.executable,'-I','-c','import time;time.sleep(30)']
        p=await asyncio.create_subprocess_exec(*argv);self.processes.append(p)
        self.assertFalse(self.tap.register_child(APP,p,argv,'a'*64))
        self.assertEqual(self.tap.readback()['state'],'disabled')
        self.assertEqual(self.tap.readback()['verdict'],'pending')
        self.assertNotIn('private',json.dumps(self.tap._receipt()))
    async def test_fresh_outputs_protected_and_same_inode_content_drift_detected(self):
        self.tap=EvidenceTap(self.config());await self.tap.start();self.assertEqual(self.tap.state,'recording')
        for name in ('events_path','receipt_path'):
            path=Path(self.data[name]);self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        Path(self.data['events_path']).write_text('private-body-token')
        with self.assertRaises(EvidenceError):self.tap._check_outputs()
        await self.tap.close();self.assertEqual(self.tap.state,'disabled')
    async def test_replaced_output_inode_detected(self):
        self.tap=EvidenceTap(self.config());await self.tap.start()
        path=Path(self.data['events_path']);path.rename(self.root/'held-events');path.write_text('');path.chmod(0o600)
        with self.assertRaises(EvidenceError):self.tap._check_outputs()
        await self.tap.close();self.assertEqual(self.tap.state,'disabled')
    async def test_empty_tap_close_has_pending_verdict_and_no_native_success_claim(self):
        self.tap=EvidenceTap(self.config());await self.tap.start();await self.tap.close()
        receipt=json.loads(Path(self.data['receipt_path']).read_text())
        self.assertEqual(receipt['state'],'closed');self.assertEqual(receipt['verdict'],'pending')
        self.assertFalse(receipt['live_verified']);self.assertEqual(receipt['rows'],0)

@unittest.skipUnless(HAS_SDK,'actual installed SDK needed for native original producer fixture')
class NativeProducer(ConfigFixture,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=Metadata.asyncSetUp
    asyncTearDown=Metadata.asyncTearDown
    async def start_producer(self,*,register=True,reaction_payloads=()):
        # The exact original feed script executes in its own real Python child.
        # Only credential lookup and SDK transport/lifecycle driver are isolated;
        # actual _connect, dispatcher/model callback, feed handler/wire remain.
        payload={'schema':'2.0','header':{'event_type':'im.message.receive_v1','event_id':'evt-native','app_id':APP},
            'event':{'message':{'message_id':'om-native','chat_id':'oc_scope','root_id':'om-private-root',
                'content':'private-message-body','message_type':'text'}}}
        card={'schema':'2.0','header':{'event_type':'card.action.trigger','event_id':'evt-card','app_id':APP},
            'event':{'operator':{'open_id':'private-operator-id','union_id':'private-union-id'},
                'token':'private-card-token','action':{'value':{'private-card-value':'never-store'}},
                'context':{'open_chat_id':'oc_scope','open_message_id':'om-card'},'current_message':{'message_id':'om-card'}}}
        fixture='''import asyncio,json,os,time
import secrets_store
secrets_store.app_secret=lambda *args: 'synthetic-unit-fixture-secret'
import lark_oapi.ws.client as sdk
class Transport:
 async def close(self):pass
async def connect(*args,**kwargs):return Transport()
sdk.websockets.connect=connect
sdk.Client._get_conn_url=lambda self:'wss://transport.invalid?device_id=actual-device&service_id=1'
original_task=sdk.loop.create_task
sdk.loop.create_task=lambda coro: coro.close() if coro.cr_code.co_name=='_receive_message_loop' else original_task(coro)
def start(self):
 async def driver():
  await self._connect()
  self._event_handler._do_without_validation(PAYLOAD)
  before=time.monotonic()
  response=self._event_handler._do_without_validation(CARD)
  assert time.monotonic()-before<3 and '排队' in response.toast.content
  for reaction in REACTIONS:self._event_handler._do_without_validation(json.dumps(reaction).encode())
  await asyncio.sleep(2)
  await self._disconnect()
  await asyncio.sleep(2)
 sdk.loop.run_until_complete(driver())
sdk.Client.start=start
'''.replace('PAYLOAD',repr(json.dumps(payload).encode())).replace('CARD',repr(json.dumps(card).encode())).replace('REACTIONS',repr(list(reaction_payloads)))
        (self.root/'sitecustomize.py').write_text(fixture)
        self.tap=EvidenceTap(self.config());await self.tap.start()
        nonce='a'*64
        argv=[sys.executable,str(SCRIPTS/'hostd/feishu_feed.py'),APP,str(self.root),str(self.root),
            '--sdk-evidence-run-id',self.data['run_id'],'--sdk-evidence-nonce',nonce]
        environment={'PATH':'/usr/bin:/bin','HOME':str(self.root),'PYTHONPATH':str(self.root)+':'+str(SCRIPTS)+':'+str(SCRIPTS/'hostd'),
            'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
        p=await asyncio.create_subprocess_exec(*argv,env=environment,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        self.processes.append(p)
        if register:self.assertTrue(self.tap.register_child(APP,p,argv,nonce))
        return p
    async def receive(self,p):
        line=await asyncio.wait_for(p.stdout.readline(),8)
        self.assertTrue(line,'actual SDK child produced no callback')
        return json.loads(line)
    async def connected(self,p):
        while True:
            row=await self.receive(p)
            envelope=row.pop('_sdk_evidence',None);self.tap.enqueue(APP,p,envelope,row)
            if row['type']=='_connected':return row,envelope
    async def test_original_sdk_producer_metadata_is_checker_compatible_and_excludes_all_private_fields(self):
        p=await self.start_producer();await self.connected(p)
        message=await self.receive(p);card=await self.receive(p)
        self.assertEqual(card['token'],'private-card-token')  # original business envelope unchanged
        for row in (message,card):
            envelope=row.pop('_sdk_evidence');self.tap.enqueue(APP,p,envelope,row)
        queued=repr(list(self.tap.queue._queue))
        for private in ('private-card-token','private-operator-id','private-card-value','private-message-body','om-private-root'):
            self.assertNotIn(private,queued)
        await asyncio.wait_for(self.tap.queue.join(),5)
        closed=await self.receive(p);self.assertEqual(closed['type'],'_disconnected')
        self.tap.enqueue(APP,p,closed.pop('_sdk_evidence'),closed)
        await asyncio.wait_for(self.tap.queue.join(),5)
        await self.tap.close()
        rows=[json.loads(line) for line in Path(self.data['events_path']).read_text().splitlines()]
        self.assertEqual([r['type'] for r in rows],['_connection_started','im.message.receive_v1','card.action.trigger','_connection_closed'])
        probes=Path(__file__).parent/'hostd_probes';sys.path.insert(0,str(probes))
        import check_feishu_events as checker
        allowed={'schema_version','run_id','t_recv','bot','app_id','session_id','connection_id','type','event_id','chat_id','message_id'}
        for row in rows:checker.validate_row(row);self.assertLessEqual(set(row),allowed)
        retained=Path(self.data['events_path']).read_text()+Path(self.data['receipt_path']).read_text()
        for private in ('private-card-token','private-operator-id','private-union-id','private-card-value','private-message-body','om-private-root'):
            self.assertNotIn(private,retained)
        receipt=json.loads(Path(self.data['receipt_path']).read_text());self.assertEqual(receipt['state'],'closed')
        self.assertEqual(receipt['children'][0]['pid'],p.pid);self.assertEqual(receipt['verdict'],'pending')
    async def test_original_parent_consumes_single_child_and_business_before_passive_tap(self):
        import test_hostd_wiring_lifecycle as wiring
        import registry
        p=await self.start_producer(register=False)
        h=wiring.hd.Hostd(registry.Registry(),self.root/'status.json')
        h.app_profiles={APP:(self.root,self.root)};h.app_lock_dir=self.root/'locks';h.sdk_evidence=self.tap
        original=h.on_feishu;seen=[];received=asyncio.Event()
        async def observe(app,event,by_chat):
            self.assertNotIn('_sdk_evidence',event)
            await original(app,event,by_chat)
            seen.append(dict(event))
            if event['type']=='card.action.trigger':received.set()
        async def spawn(*argv,**kwargs):
            self.assertEqual(tuple(argv),(sys.executable,str(SCRIPTS/'hostd/feishu_feed.py'),APP,str(self.root),str(self.root),'--sdk-evidence-run-id',self.data['run_id'],'--sdk-evidence-nonce','a'*64))
            return p
        with mock.patch.object(wiring.hd.asyncio,'create_subprocess_exec',spawn),mock.patch('secrets.token_hex',return_value='a'*64),mock.patch.object(h,'on_feishu',observe):
            task=asyncio.create_task(h.feishu_child(APP,[]))
            try:
                await asyncio.wait_for(received.wait(),8);await asyncio.wait_for(self.tap.queue.join(),5)
                self.assertEqual(sum(e['type']=='_connected' for e in seen),1)
                self.assertEqual(next(e for e in seen if e['type']=='card.action.trigger')['token'],'private-card-token')
                self.assertEqual(self.tap.rows,3);self.assertEqual(self.tap.children[id(p)].proof[0],p.pid)
                self.assertNotIn('private-card-token',repr(list(self.tap.queue._queue)))
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task
        self.assertIsNotNone(p.returncode)
    async def test_crossrun_envelope_disables_evidence_without_changing_business(self):
        # One real producer supplies actual native session; corrupt only wire
        # association fields after callback to test the parent's negative gates.
        p=await self.start_producer();row,envelope=await self.connected(p)
        message=await self.receive(p);callback_envelope=message.pop('_sdk_evidence')
        self.assertEqual(message['message_id'],'om-native')
        callback_envelope['run_id']='wrong-run'
        self.tap.enqueue(APP,p,callback_envelope,message)
        self.assertEqual(self.tap.state,'disabled')
        card=await self.receive(p);self.assertEqual(card['type'],'card.action.trigger');self.assertEqual(card['token'],'private-card-token')
    async def test_old_session_fake_connected_wrong_app_and_queue_pressure_fail_closed(self):
        p=await self.start_producer();row,envelope=await self.connected(p)
        message=await self.receive(p);actual=message.pop('_sdk_evidence')
        await self.tap.queue.join()
        for failure in ('old-session','fake-connected','wrong-app','queue-full','bytes'):
            with self.subTest(failure=failure):
                self.tap.state='recording';self.tap.children[id(p)].session=dict(envelope)
                damaged=dict(actual);event=dict(message)
                if failure=='old-session':damaged['session_id']='b'*64
                if failure=='fake-connected':event['type']='_connected'
                if failure=='wrong-app':event['app']='cli_other'
                if failure=='queue-full':
                    while not self.tap.queue.full():self.tap.queue.put_nowait(('ignored',None,None))
                if failure=='bytes':self.tap.queued_bytes=self.tap.config.max_bytes
                self.tap.enqueue(APP,p,damaged,event)
                self.assertEqual(self.tap.state,'disabled');self.assertEqual(self.tap.readback()['verdict'],'pending')
                while not self.tap.queue.empty():self.tap.queue.get_nowait();self.tap.queue.task_done()
                self.tap.queued_bytes=0
        card=await self.receive(p);self.assertEqual(card['token'],'private-card-token')
    async def test_process_drift_blocks_recording_actual_callback(self):
        p=await self.start_producer();await self.connected(p);await self.tap.queue.join()
        row=await self.receive(p);envelope=row.pop('_sdk_evidence')
        import hostd.sdk_evidence as module
        original=module.process_proof
        def drift(process,argv):
            proof=original(process,argv)
            return (proof[0],proof[1]+1,*proof[2:])
        with mock.patch.object(module,'process_proof',side_effect=drift):
            self.tap.enqueue(APP,p,envelope,row);await self.tap.queue.join()
        self.assertEqual(self.tap.state,'disabled')
        self.assertEqual(self.tap.readback()['verdict'],'pending')
    async def test_native_handler_still_emits_card_toast_after_row_limit_exhaustion(self):
        self.data['max_rows']=1;p=await self.start_producer();await self.connected(p)
        message=await self.receive(p);self.tap.enqueue(APP,p,message.pop('_sdk_evidence'),message)
        self.assertEqual(self.tap.state,'disabled')
        card=await self.receive(p);self.assertEqual(card['type'],'card.action.trigger');self.assertEqual(card['token'],'private-card-token')

class HarnessOptIn(ConfigFixture,unittest.TestCase):
    def setUp(self):self.setup_config()
    def tearDown(self):self.temp.cleanup()
    def test_prepared_admission_explicit_aux_pin_default_manifest_and_argv_unchanged(self):
        import test_hostd_l3_prepare as fixture
        case=fixture.PrepareTests('test_check_is_readonly_and_never_ready');case.setUp()
        try:
            old=case.check();old_argv=old.argv()
            for name in ('sdk_evidence.py','feishu_feed.py','__main__.py'):
                target=case.source/'skills/agent-harness/buzz-agent-setup/scripts/hostd'/name
                if target.exists():target.chmod(0o644)
                target.write_bytes((SCRIPTS/'hostd'/name).read_bytes());target.chmod(0o444)
            case.doc['source_hashes']={str(p.relative_to(case.source)):fixture.digest(p) for p in case.m._runtime_files(case.source)}
            case.save();manifest=case.manifest.read_bytes()
            self.data.update(run_id=case.doc['run_id'],events_path=str(case.run/'runtime/events.jsonl'),receipt_path=str(case.run/'runtime/receipt.json'))
            self.path=case.run/'sdk-evidence.json';pin=self.save()
            kwargs={'reviewed_source':case.source,'reviewed_revision':fixture.REV,'fixture_sha256':fixture.digest(case.fixture),'command_runner':case.runner}
            plan=case.m.PreparedRun.check(case.manifest,**kwargs,sdk_evidence_config=self.path,sdk_evidence_config_sha256=pin)
            self.assertEqual(plan.argv()[:-4],old_argv)
            self.assertEqual(plan.argv()[-4:],['--sdk-evidence-config',str(self.path),'--sdk-evidence-config-sha256',pin])
            self.assertEqual(case.manifest.read_bytes(),manifest);self.assertFalse(Path(self.data['events_path']).exists())
            with self.assertRaises(case.m.PreparationError):case.m.PreparedRun.check(case.manifest,**kwargs,sdk_evidence_config=self.path)
            self.path.chmod(0o644)
            with self.assertRaises(case.m.PreparationError):plan.revalidate()
        finally:case.doCleanups()
    @unittest.skipUnless(HAS_SDK,'actual zero-local runtime dependencies required')
    def test_zero_local_explicit_aux_pin_preserves_v1_authority_and_no_outputs(self):
        import test_hostd_l3_zero_local_run as fixture
        case=fixture.ZeroLocalRunTests('test_host_b_manifest_requires_independent_verified_zero_local_topology')
        module=case.load_candidate();case.setup_fixture();run=case.case()
        try:
            for path in (run.profile,run.catalog,run.legacy):
                path.write_text(path.read_text().replace('cli_agent',APP));path.chmod(0o600)
            encrypted=run.data/'lark-cli/appsecret_cli_agent.enc'
            encrypted.rename(encrypted.with_name('appsecret_'+APP+'.enc'))
            profile=run.doc['app_profiles'].pop('cli_agent');run.doc['app_profiles'][APP]=profile
            run.check_args['expected_app_profiles']=json.loads(json.dumps(run.doc['app_profiles']))
            run.repin();original=run.manifest.read_bytes()
            default=module.ZeroLocalRunPlan.check(run.manifest,**run.check_args)
            self.data.update(run_id=run.run_id,events_path=str(run.runtime/'events.jsonl'),receipt_path=str(run.runtime/'receipt.json'))
            self.path=run.root/'sdk-evidence.json';pin=self.save()
            plan=module.ZeroLocalRunPlan.check(run.manifest,**run.check_args,sdk_evidence_config=self.path,sdk_evidence_config_sha256=pin)
            self.assertEqual(plan.argv()[:-4],default.argv());self.assertEqual(run.manifest.read_bytes(),original)
            self.assertFalse(Path(self.data['events_path']).exists());self.assertFalse(Path(self.data['receipt_path']).exists())
            self.path.chmod(0o644)
            with self.assertRaises(module.ZeroLocalRunError):plan.revalidate(phase='prepared')
        finally:case.doCleanups()
    def test_zero_local_signature_default_gate_does_not_accept_half_optin(self):
        from integration.hostd_l3 import zero_local_run
        parameters=inspect.signature(zero_local_run.ZeroLocalRunPlan.check).parameters
        self.assertIn('sdk_evidence_config',parameters);self.assertIn('sdk_evidence_config_sha256',parameters)
        # Existing zero-local full admission tests cover default closure; a
        # missing protected manifest cannot acquire authority by optional flags.
        with self.assertRaises(zero_local_run.ZeroLocalRunError):
            zero_local_run.ZeroLocalRunPlan.check(self.root/'missing',reviewed_source=self.root,reviewed_revision='a'*40,
                reviewed_source_hashes={},expected_host_b_owner='b'*64,expected_relay_pubkey='c'*64,
                expected_channel_id='scope',expected_source_owner_pubkey='d'*64,expected_agent_pubkeys=[],
                expected_app_profiles={},sdk_evidence_config=self.path)

if __name__=='__main__':unittest.main()
