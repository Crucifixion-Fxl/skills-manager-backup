"""Kernel seal/FD/process negatives with independently signed fixture events."""
import fcntl
import ctypes
import signal
import subprocess
import sys
import time
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import test_hostd_l3_acp_real_provider as component

class AlgorithmTests(unittest.TestCase):
    def setUp(self):
        path=component.SOURCE/'model_evidence.py';self.m=types.ModuleType('algorithm_observer');self.m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),self.m.__dict__)
    def test_process_scan_preserves_permission_io_and_malformed_failures(self):
        for error in (PermissionError(13,'denied'),OSError(5,'io'),ValueError('invalid')):
            with self.subTest(error=type(error).__name__),mock.patch.object(self.m.os,'listdir',return_value=['123']),mock.patch.object(Path,'read_text',side_effect=error):
                with self.assertRaises(type(error)):self.m.process_table()
    def test_process_scan_preserves_deadline_and_row_limit(self):
        with mock.patch.object(self.m.os,'listdir',return_value=[str(os.getpid())]):
            with self.assertRaises(self.m.EvidenceError):self.m.process_table(time.monotonic()-1)
            with mock.patch.object(self.m,'PROC_LIMIT',0):
                with self.assertRaises(self.m.EvidenceError):self.m.process_table()
    def test_official_bip340_valid_and_odd_R_vectors(self):
        # Primary vectors 0 and 6, public data only (no secret-key fields).
        # https://github.com/bitcoin/bips/blob/master/bip-0340/test-vectors.csv
        self.m.verify_schnorr('f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9',bytes(32),'e907831f80848d1069a5371b402410364bdf1c5f8307b0084c55f1ce2dca821525f66a4a85ea8b71e482a74f382d2ce5ebeee8fdb2172f477df4900d310536c0')
        with self.assertRaises(self.m.EvidenceError):
            self.m.verify_schnorr('dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659',bytes.fromhex('243f6a8885a308d313198a2e03707344a4093822299f31d0082efa98ec4e6c89'),'fff97bd5755eeea420453a14355235d382f6472f8568a18b2f057a14602975563cc27944640ac607cd107ae10923d9ef7a73c643e166be5ebea fa34b1ac553e2'.replace(' ',''))
    def test_png_crc_and_expanded_data_rejected_even_with_matching_image_hash(self):
        import base64
        import zlib
        good=component.png()
        b={'type':'image','mimeType':'image/png','data':base64.b64encode(good).decode()};self.assertEqual(self.m.decode_image([b],{'image_sha256':component.digest(good)})[1]['width'],1)
        corrupt=good[:29]+bytes([good[29]^1])+good[30:]
        def chunk(kind,data):return len(data).to_bytes(4,'big')+kind+data+zlib.crc32(kind+data).to_bytes(4,'big')
        bomb=good[:33]+chunk(b'IDAT',zlib.compress(b'X'*1_000_000))+chunk(b'IEND',b'')
        for raw in (corrupt,bomb):
            b['data']=base64.b64encode(raw).decode()
            with self.assertRaises(self.m.EvidenceError):self.m.decode_image([b],{'image_sha256':component.digest(raw)})

class ModelEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):component.RealProviderTests.setUpClass()
    @classmethod
    def tearDownClass(cls):component.RealProviderTests.tearDownClass()
    def setUp(self):
        self.t=component.RealProviderTests();self.t.setUp();self.addCleanup(self.t.doCleanups)
    def successful(self,mode="ok",native=False):
        t=self.t
        if native:self.native_server=component.native_transport_fixture(t)
        p=t.spawn(mode);sid=t.start(p);t.submit(p,sid);self.assertEqual(t.read(p)['params']['update']['content']['text'],component.ANSWER);self.assertEqual(t.read(p)['result']['stopReason'],'end_turn')
        path=t.root/'model_evidence.py';m=types.ModuleType('test_observer');m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);self.m=m
        native_pid=p.pid if native else os.getpid()
        start=int(Path('/proc/'+str(native_pid)+'/stat').read_text().rsplit(')',1)[1].split()[19])
        self.args={'root':t.root,'run_id':t.runid,'input_witness_path':t.root/'input-witness.json','expected_event':t.event,'initial_unit':'offline-test.unit','initial_pid':native_pid,'initial_start_ticks':start,'selected_pubkey':t.selected,'adapter_path':t.adapter,'adapter_sha256':component.digest(t.adapter.read_bytes()),'python_path':os.path.realpath(t.adapter_argv[0]),'python_sha256':component.digest(Path(t.adapter_argv[0]).read_bytes()),'adapter_argv':t.adapter_argv,'backend_config_sha256':component.digest((t.root/'backend.json').read_bytes())};return p
    def observed(self,**updates):return self.m.observe(**dict(self.args,**updates))
    def observed_success(self,timeout=3.,**updates):
        # Pending is a valid bounded kernel-snapshot result. Only positive
        # acceptance waits; mutation negatives retain one strict observation.
        deadline=time.monotonic()+timeout
        while True:
            result=self.observed(**updates)
            if result['status']=='component-observed':return result
            self.assertEqual(result['status'],'pending',result)
            remaining=deadline-time.monotonic()
            if remaining<=0:self.fail('Original model evidence stayed pending within observation budget')
            time.sleep(min(.05,remaining))
    def test_success_correlates_sealed_twins_signed_event_and_original_ancestor(self):
        self.successful();v=self.observed_success();self.assertEqual(v['status'],'component-observed');self.assertIs(v['live_verified'],False)
        child=json.loads((self.t.root/'model-evidence.json').read_text())['records'][0]['child'];self.assertFalse(Path('/proc/'+str(child['pid'])).exists(),'actual child must be reaped')
    def test_native_retrieval_correlates_original_signed_event_without_refetch_or_prompt_rewrite(self):
        self.successful(native=True);server=self.native_server
        self.assertEqual(self.observed_success()['status'],'component-observed');self.assertEqual(len(server.requests),1)
        row=json.loads((self.t.root/'model-evidence.json').read_text())['records'][0]
        self.assertEqual(row['prompt'],self.t.prompt);self.assertEqual(len(row['prompt']),1)
        self.assertEqual(row['image']['transport']['source'],'historical-adapter-observed-protected-tls-fetch')
        different=component.sign_event(dict(self.t.event,content='Different original signed event'))
        self.assertEqual(self.observed(expected_event=different)['status'],'pending')
        # Valid signed event with the same id isn't constructible after changing
        # tags/content; independent signed readback remains the authority.
        for key in ('sig','id'):
            altered=dict(self.t.event);altered[key]='0'*len(altered[key])
            self.assertEqual(self.observed(expected_event=altered)['status'],'pending')
        self.assertEqual(len(server.requests),1,'observer must validate sealed fetch evidence without using caller credentials')

    def test_positive_native_observation_can_wait_for_transient_kernel_snapshot(self):
        self.successful(native=True)
        evidence={name:(self.t.root/name).read_bytes() for name in ('model-evidence.json','input-witness.json')}
        original=self.m.os.listdir;delayed=[]
        def slow_once(path):
            if path=='/proc' and not delayed:
                delayed.append(True)
                # Exercise the real observer deadline; do not change its budget.
                time.sleep(.55)
            return original(path)
        with mock.patch.object(self.m.os,'listdir',side_effect=slow_once):
            self.assertEqual(self.observed_success()['status'],'component-observed')
        self.assertTrue(delayed)
        self.assertEqual(len(self.native_server.requests),1)
        self.assertEqual(evidence,{name:(self.t.root/name).read_bytes() for name in evidence})

    def test_positive_observation_budget_still_rejects_invalid_evidence(self):
        self.successful(native=True)
        evidence={name:(self.t.root/name).read_bytes() for name in ('model-evidence.json','input-witness.json')}
        with self.assertRaisesRegex(AssertionError,'stayed pending'):
            self.observed_success(timeout=.05,selected_pubkey='0'*64)
        self.assertEqual(len(self.native_server.requests),1)
        self.assertEqual(evidence,{name:(self.t.root/name).read_bytes() for name in evidence})

    def test_native_retrieval_evidence_and_trust_mutations_never_observed(self):
        self.successful(native=True);t=self.t;path=t.root/'model-evidence.json';original=path.read_bytes()
        for key,value in (('url','https://127.0.0.1:1/media/hostile.png'),('source','native-acp-image-block'),('certificate_sha256','0'*64),('data_base64','AA=='),('finished_ns',1),('content_length',24577)):
            doc=json.loads(original);doc['records'][0]['image']['transport'][key]=value;component.save(path,doc)
            self.assertEqual(self.observed()['status'],'pending')
        path.write_bytes(original);self.assertEqual(self.observed_success()['status'],'component-observed')
        (t.root/'ca.pem').write_text('mutated CA');self.assertEqual(self.observed()['status'],'pending')

    def test_genuine_fork_success_retains_historical_descendant_waits(self):
        self.successful('fork-tree');self.assertEqual(self.observed_success()['status'],'component-observed')
        row=json.loads((self.t.root/'model-evidence.json').read_text())['records'][0]
        life=row['group_lifecycle'];self.assertTrue(life['historical']);self.assertTrue(life['absence_observed']);self.assertEqual(len(life['descendant_waits']),2)
        self.assertTrue(all(w['wait_observed'] and not Path('/proc/'+str(w['pid'])).exists() for w in life['descendant_waits']))
    def test_runtime_trust_roots_wrong_start_event_selected_and_argv_rejected(self):
        self.successful()
        for change in ({'initial_start_ticks':self.args['initial_start_ticks']+1},{'initial_pid':1},{'selected_pubkey':'0'*64},{'adapter_sha256':'0'*64},{'backend_config_sha256':'0'*64},{'adapter_argv':self.args['adapter_argv']+['unexpected']},{'python_sha256':'0'*64},{'root':self.t.work},{'expected_event':dict(self.t.event,sig='0'*128)}):
            with self.subTest(keys=list(change)):self.assertEqual(self.observed(**change)['status'],'pending')
    def test_regular_twin_tampered_replaced_and_ordinary_file_rejected(self):
        self.successful();path=self.t.root/'model-evidence.json';raw=path.read_bytes()
        doc=json.loads(raw);doc['records'][0]['completion']='ordinary-file-forgery';path.write_text(json.dumps(doc));self.assertEqual(self.observed()['status'],'pending')
        path.write_bytes(raw);self.assertEqual(self.observed_success()['status'],'component-observed')
        other=self.t.root/'replacement';other.write_bytes(raw);other.chmod(0o600);os.replace(other,path);self.assertEqual(self.observed()['status'],'pending')
    def test_input_witness_tampered_and_source_closure_rejected(self):
        self.successful();path=self.t.root/'input-witness.json';raw=path.read_bytes();path.write_bytes(raw+b' ');self.assertEqual(self.observed()['status'],'pending');path.write_bytes(raw)
        dep=self.t.root/'acp_double.py';dep.write_bytes(dep.read_bytes()+b'\n');self.assertEqual(self.observed()['status'],'pending')
    def test_model_snapshot_requires_full_seals_and_owned_regular_fd(self):
        self.successful();root=self.t.root;path=root/'standalone.json';path.write_bytes(b'{}\n');path.chmod(0o600);regular=os.open(path,os.O_RDWR);self.addCleanup(os.close,regular);meta=os.fstat(regular)
        fd=os.memfd_create('hostd-acp-model-'+self.t.runid,os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);self.addCleanup(os.close,fd);os.fchmod(fd,0o600);os.write(fd,b'{}\n')
        with self.assertRaises(self.m.EvidenceError):self.m.model_snapshot(os.getpid(),self.t.runid,(meta.st_dev,meta.st_ino))
        mask=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL;fcntl.fcntl(fd,fcntl.F_ADD_SEALS,mask)
        self.assertEqual(self.m.model_snapshot(os.getpid(),self.t.runid,(meta.st_dev,meta.st_ino)),b'{}\n')
        with self.assertRaises(self.m.EvidenceError):self.m.model_snapshot(os.getpid(),'2'*32,(meta.st_dev,meta.st_ino))
    def test_plain_file_and_foreign_process_without_matching_fd_never_pass(self):
        self.successful();path=self.t.root/'model-evidence.json';doc=json.loads(path.read_text());doc['records'][0]['input']['provider_pid']=os.getpid();component.save(path,doc)
        self.assertEqual(self.observed()['status'],'pending')

def _run_surviving_group_observer(mode="fork-ok"):
    assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
    ModelEvidenceTests.setUpClass();test=ModelEvidenceTests();test.setUp();t=test.t;kid=None;identity=None
    try:
        # Deliberately defective pinned adapter fixture claims historical absence.
        # Root trust input pins this diagnostic fixture. Observer must independently
        # reject its real currently surviving group, despite valid sealed twins.
        def defective(body):
            needle='    def cleanup(self,terminate):\n'
            inject="""        if not terminate:
            started=time.monotonic_ns();rc=self.proc.wait(timeout=2)
            life={'historical':True,'scope':'bounded-owned-descendants-and-original-group','original':{k:v for k,v in self.original.items() if k!='state'},'started_ns':started,'finished_ns':time.monotonic_ns(),'absence_observed':True,'descendant_waits':[],'escaped_observed':False}
            self.proc._hostd_lifecycle=life;return rc,life
"""
            assert body.count(needle)==1;return body.replace(needle,needle+inject)
        t.adapter_transform=defective;p=test.successful(mode)
        kid=int((t.work/'capture.descendant').read_text());identity=component._proc(kid)
        assert identity['state']!='Z'
        if mode=='fork-ok':assert identity['pgid']==int((t.work/'capture.pid').read_text())
        else:assert identity['pgid']!=int((t.work/'capture.pid').read_text()) and identity['parent_pid']==p.pid
        # Verify immutable ledger and regular twins match before observer refusal.
        model_raw,ino=test.m.read_private(t.root/'model-evidence.json')
        assert test.m.model_snapshot(p.pid,t.runid,ino)==model_raw
        assert json.loads(model_raw)['records'][0]['group_lifecycle']['absence_observed'] is True
        verdict=test.observed()
        print(json.dumps({'phase':'independent-live-group','identity':identity,'actual_observer':verdict,'twins_match':True}),flush=True)
        assert verdict['status']=='pending','observer accepted actual surviving original group'
    finally:
        t.reap()
        if kid is not None:
            try:
                now=component._proc(kid)
                assert now['start_ticks']==identity['start_ticks'] and now['parent_pid']==os.getpid() and now['uid']==os.geteuid() and now['cgroup_sha256']==identity['cgroup_sha256']
                fd=os.pidfd_open(kid)
                try:
                    assert component._proc(kid)['start_ticks']==identity['start_ticks'];signal.pidfd_send_signal(fd,signal.SIGKILL);waited,status=os.waitpid(kid,0)
                    print(json.dumps({'phase':'independent-fixture-reap','pid':waited,'exit':os.waitstatus_to_exitcode(status)}),flush=True)
                finally:os.close(fd)
            except FileNotFoundError:pass
        test.doCleanups();ModelEvidenceTests.tearDownClass()

class IndependentGroupObserverTests(unittest.TestCase):
    def test_actual_escaped_adopted_descendant_with_lying_receipt_rejected(self):
        code="import sys;sys.path.insert(0,"+repr(str(component.HERE))+");import test_hostd_l3_model_evidence as e;e._run_surviving_group_observer('fork-escape')"
        done=subprocess.run([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        print(done.stdout.decode(),end='');self.assertEqual(done.returncode,0,done.stdout.decode())
    def test_actual_surviving_group_with_lying_historical_receipt_rejected(self):
        code="import sys;sys.path.insert(0,"+repr(str(component.HERE))+");import test_hostd_l3_model_evidence as e;e._run_surviving_group_observer()"
        done=subprocess.run([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        print(done.stdout.decode(),end='');self.assertEqual(done.returncode,0,done.stdout.decode())

if __name__=='__main__':unittest.main()
