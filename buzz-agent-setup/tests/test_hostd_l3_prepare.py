"""Offline admission and actual harmless child ownership, never an L3 acceptance suite."""
import dataclasses
from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
MODULE=TESTS/'integration/hostd_l3/prepare.py'
APPS=('cli_aa48bbeeba38dbcf','cli_aa48b41531785bfc','cli_aa48b406abf8dbe7')
REV='a'*40

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class PrepareTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('l3_prepare',MODULE)
        self.m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.m;spec.loader.exec_module(self.m)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'release';self.source.mkdir(mode=0o700)
        (self.source/'.git').mkdir(mode=0o700)
        script=self.source/'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py';script.parent.mkdir(parents=True)
        script.write_text('# reviewed source\n');script.chmod(0o444)
        self.run=self.root/('run-'+'1'*32);self.run.mkdir(mode=0o700)
        for p in ['home/.config/buzz-feishu-sync/hostd-local-l3','runtime','runtime/join-state','profiles','people','opt/buzz-0.5.23/usr/bin']:
            (self.run/p).mkdir(parents=True,mode=0o700)
        for p in self.run.rglob('*'):
            if p.is_dir():p.chmod(0o700)
        self.buzz=self.run/'opt/buzz-0.5.23/usr/bin/buzz';shutil.copyfile(Path('/usr/bin/true').resolve(),self.buzz);self.buzz.chmod(0o700)
        self.fixture=self.run/'groups.json';self.write(self.fixture,{'hostd-test-群X':'oc_testx','hostd-test-群W-未绑定':'oc_testw'})
        self.config=self.run/'home/.config/buzz-feishu-sync/hostd-local-l3/config.json'
        agents={}
        for i,app in enumerate(APPS):
            cfg=self.run/f'profiles/{i}/config';data=self.run/f'profiles/{i}/data'
            cfg.mkdir(parents=True,mode=0o700);data.mkdir(mode=0o700);cfg.parent.chmod(0o700)
            self.write(cfg/'config.json',{'apps':[{'appId':app}]})
            agents[str(i+1)*64]={'app_id':app,'lark_config_dir':str(cfg),'lark_data_dir':str(data)}
        for name in ['owner.env','mirror.env']:self.write(self.run/'runtime'/name,'BUZZ_RELAY_URL=wss://127.0.0.1:9443\n')
        self.cfg={'channel_id':'00000000-0000-0000-0000-000000000001','chat_id':'oc_testx','owner_open_id':'ou_testowner','owner_app_id':'cli_a940faa4ec381bc4','mirror_pubkey':'f'*64,'mirror_env_file':str(self.run/'runtime/mirror.env'),'people_api':{'base_url':'https://127.0.0.1:9443','signer_env_file':str(self.run/'runtime/owner.env')},'agents':agents,'desk_pubkey':'1'*64,'buzz_cli':str(self.buzz),'buzz_cli_sha256':digest(self.buzz),'lark_cli':str(self.run/'lark-cli'),'remove_extras':True,'identity':'union_id','reaction_sync':'two_way'}
        self.write(self.config,self.cfg)
        self.catalog=self.run/'runtime/catalog.json';self.legacy=self.run/'runtime/legacy.json'
        doc={'version':1,'owner_pubkey':'a'*64,'buzz':{'cli_path':str(self.buzz),'cli_sha256':digest(self.buzz)},'state_dir':str(self.run/'runtime/join-state'),'lark_cli':str(self.run/'lark-cli'),'agents':[{'name':'local','env_file':str(self.run/'runtime/owner.env'),'unit':'buzz-l3-'+ '1'*32+'.service','capabilities':{'summary':'local','repos':[]},'feishu':agents['2'*64]}]}
        self.write(self.catalog,doc);self.write(self.legacy,dict(doc,agents=[]))
        self.onboarding=self.run/'runtime/onboarding.json'
        self.rt={'version':1,'owner_env_file':str(self.run/'runtime/owner.env'),'relay_url':'wss://127.0.0.1:9443','relay_pubkey':'a'*64,'template_config':str(self.config),'binding_dir':str(self.run/'home/.config/buzz-feishu-sync'),'legacy_join_path':str(self.legacy),'catalog_path':str(self.catalog),'trusted_relays':['wss://127.0.0.1:9443']}
        self.write(self.onboarding,self.rt)
        import ssl
        self.bundle=self.run/'people/trust.pem';shutil.copyfile(ssl.get_default_verify_paths().cafile,self.bundle);self.bundle.chmod(0o600)
        self.manifest=self.run/'manifest.json'
        self.doc={'version':1,'run_id':'1'*32,'run_dir':str(self.run),'source_root':str(self.source),'revision':REV,'source_hashes':{'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py':digest(script)},'fixture_path':str(self.fixture),'fixture_sha256':digest(self.fixture),'initial_binding':'hostd-local-l3','onboarding_config':str(self.onboarding),'buzz_cli':str(self.buzz),'buzz_sha256':digest(self.buzz),'python':str(Path(sys.executable).resolve()),'relay_url':'wss://127.0.0.1:9443','people_url':'https://127.0.0.1:9443','trust_bundle':str(self.bundle)}
        self.save();self.head_result=subprocess.CompletedProcess([],0,REV+'\n','');self.status_result=subprocess.CompletedProcess([],0,'','')
        self.runner=mock.Mock(side_effect=lambda argv,**kw:self.head_result if 'rev-parse' in argv else self.status_result)
    def write(self,p,value):p.write_text(json.dumps(value) if isinstance(value,dict) else value);p.chmod(0o600)
    def save(self):self.write(self.manifest,self.doc)
    def check(self):return self.m.PreparedRun.check(self.manifest,reviewed_source=self.source,reviewed_revision=REV,fixture_sha256=digest(self.fixture),command_runner=self.runner)
    def reject(self):
        with self.assertRaises(self.m.PreparationError) as caught:self.check()
        self.assertIn('怎么解决',str(caught.exception));self.assertIn('复制给 AI',str(caught.exception))
    def test_check_is_readonly_and_never_ready(self):
        before={str(p):p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
        plan=self.check();self.assertEqual(plan.readback()['status'],'prepared');self.assertFalse(plan.readback()['live_verified'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.run.rglob('*') if p.is_file()})
        with self.assertRaises(dataclasses.FrozenInstanceError):plan.run_id='changed'
    def test_revision_source_and_hash_mismatch_reject(self):
        for key,value in [('revision','b'*40),('source_root',str(self.run)),('source_hashes',{'../secret':'a'*64})]:
            with self.subTest(key=key):
                original=self.doc[key];self.doc[key]=value;self.save();self.reject();self.doc[key]=original
    def test_actual_git_head_mismatch_reject(self):
        self.head_result.stdout='b'*40;self.reject()
    def test_git_failure_never_exposes_stderr(self):
        self.head_result=subprocess.CompletedProcess([],1,'','SECRET_VALUE');self.reject()
    def test_missing_own_git_directory_rejects_matching_parent_git(self):
        (self.source/'.git').rmdir()
        # The fake metadata runner still returns matching HEAD and clean status.
        self.reject()
    def test_own_git_symlink_unsafe_or_foreign_metadata_rejected(self):
        git=self.source/'.git';git.rmdir()
        target=self.root/'foreign-git';target.mkdir(mode=0o700)
        git.symlink_to(target,target_is_directory=True)
        with self.subTest(kind='symlink'):self.reject()
        git.unlink()
        git.mkdir(mode=0o777);git.chmod(0o777)
        with self.subTest(kind='unsafe permissions'):self.reject()
        git.chmod(0o700)
        original=self.m.os.fstat
        def foreign(fd):
            value=original(fd)
            if os.readlink(f'/proc/self/fd/{fd}')==str(git):
                fields=list(value);fields[4]=os.geteuid()+1;return os.stat_result(fields)
            return value
        with self.subTest(kind='foreign owner'),mock.patch.object(self.m.os,'fstat',side_effect=foreign):self.reject()
    def test_dirty_candidate_rejected_before_start(self):
        self.status_result.stdout=' M skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py\n';self.reject()
    def test_source_hash_coverage_requires_whole_runtime_tree(self):
        p=self.source/'skills/agent-harness/buzz-agent-setup/scripts/hostd/extra.py';p.write_text('unreviewed module\n');self.reject()
    def test_fixture_pin_not_self_authorized(self):
        with self.assertRaises(self.m.PreparationError):self.m.PreparedRun.check(self.manifest,reviewed_source=self.source,reviewed_revision=REV,fixture_sha256='b'*64,command_runner=self.runner)
    def test_only_exact_two_groups_and_three_apps(self):
        self.write(self.fixture,{'hostd-test-群X':'oc_testx','hostd-test-群W-未绑定':'oc_testw','invented':'oc_other'});self.doc['fixture_sha256']=digest(self.fixture);self.save();self.reject()
    def test_production_app_and_profile_escape_reject(self):
        for field,value in [('app_id','cli_production'),('lark_data_dir','/home/jchen/.config/lark')]:
            with self.subTest(field=field):
                original=self.cfg['agents']['2'*64][field];self.cfg['agents']['2'*64][field]=value;self.write(self.config,self.cfg);self.reject();self.cfg['agents']['2'*64][field]=original
    def test_native_config_unknown_field_reject(self):
        self.cfg['unknown_secret']='NEVER_PRINT';self.write(self.config,self.cfg);self.reject()
    def test_initial_binding_arbitrary_or_wrong_chat_reject(self):
        self.doc['initial_binding']='golf';self.save();self.reject();self.doc['initial_binding']='hostd-local-l3';self.save()
        self.cfg['chat_id']='oc_prod';self.write(self.config,self.cfg);self.reject()
    def test_local_same_origin_tls_and_bundle_required(self):
        for field,value in [('relay_url','wss://production.example'),('people_url','https://127.0.0.1:9444'),('relay_url','ws://127.0.0.1:9443')]:
            with self.subTest(field=field):
                original=self.doc[field];self.doc[field]=value;self.save();self.reject();self.doc[field]=original
        self.save();self.write(self.bundle,'not a trust bundle');self.reject()
    def test_missing_or_outside_runtime_config_reject(self):
        self.rt['owner_env_file']='/home/jchen/secret.env';self.write(self.onboarding,self.rt);self.reject()
    def test_optional_cache_and_catalog_paths_cannot_escape_run(self):
        self.cfg['people_cache_file']='/home/jchen/.config/people.json';self.write(self.config,self.cfg);self.reject()
    def test_agent_env_relay_and_prompt_paths_cannot_escape_run(self):
        for extra in ['BUZZ_RELAY_URL=wss://production.example\n','BUZZ_RELAY_URL=wss://127.0.0.1:9443\nBUZZ_ACP_SYSTEM_PROMPT_FILE=/home/jchen/private.md\n']:
            with self.subTest(extra=extra):
                self.write(self.run/'runtime/owner.env',extra);self.reject()
    def test_existing_private_join_state_directory_admitted_and_revalidated(self):
        state=self.run/'runtime/join-state';state.mkdir(mode=0o700,exist_ok=True)
        plan=self.check();self.assertEqual(plan.readback()['status'],'prepared')
        state.chmod(0o777)
        with mock.patch.object(self.m.subprocess,'Popen') as child:
            with self.assertRaises(self.m.PreparationError):self.m.OwnedHostd(plan).start()
            child.assert_not_called()
    def test_missing_join_state_directory_is_rejected(self):
        state=self.run/'runtime/join-state'
        if state.exists():state.rmdir()
        self.reject()
    def test_join_state_directory_unsafe_symlink_escape_rejected(self):
        state=self.run/'runtime/join-state';state.mkdir(mode=0o700,exist_ok=True);state.chmod(0o777)
        with self.subTest(kind='unsafe'):self.reject()
        state.chmod(0o700);target=self.run/'runtime/join-state-real';state.rename(target);state.symlink_to(target,target_is_directory=True)
        with self.subTest(kind='symlink'):self.reject()
        state.unlink();target.rename(state)
        for path in [self.catalog,self.legacy]:
            doc=json.loads(path.read_text());doc['state_dir']='/home/jchen/.config/join-state';self.write(path,doc)
        with self.subTest(kind='escape'):self.reject()
    def test_raw_elf_and_sha_required(self):
        self.buzz.write_text('#!/bin/sh\nexit 0\n');self.doc['buzz_sha256']=digest(self.buzz);self.save();self.reject()
    def test_private_manifest_and_ancestor_symlink_reject(self):
        self.manifest.chmod(0o644);self.reject();self.manifest.chmod(0o600)
        real=self.run/'profiles-real';(self.run/'profiles').rename(real);(self.run/'profiles').symlink_to(real,target_is_directory=True);self.reject()
    def test_stale_runtime_outputs_reject(self):
        for name in ['hostd.sqlite3','status.json','console','hostd-process.json']:
            with self.subTest(name=name):
                p=self.run/'runtime'/name;self.write(p,'stale');self.reject();p.unlink()
    def test_argv_and_clean_env_actual_hostd_contract(self):
        plan=self.check();child=mock.Mock();child.pid=12345;child.poll.return_value=None
        with mock.patch.object(self.m.subprocess,'Popen',return_value=child) as popen:
            owned=self.m.OwnedHostd(plan);owned.start()
        args=popen.call_args.args[0];kw=popen.call_args.kwargs
        self.assertEqual(args[:4],[self.doc['python'],'-m','hostd','run'])
        self.assertEqual(args[args.index('--only')+1],'hostd-local-l3')
        self.assertEqual(args[args.index('--onboarding-config')+1],str(self.onboarding));self.assertIn('--console-dir',args)
        self.assertTrue(kw['start_new_session']);self.assertEqual(kw['env']['HOME'],str(self.run/'home'))
        self.assertEqual(kw['env']['SSL_CERT_FILE'],str(self.bundle));self.assertIn('HOSTD_NODE_BINARY',kw['env']);self.assertIn('HOSTD_LARK_CLI_ENTRY',kw['env'])
        self.assertNotIn('BUZZ_PRIVATE_KEY',kw['env']);self.assertNotIn('GITLAB_TOKEN',kw['env']);self.assertNotIn('TOKEN',json.dumps(owned.readback()))
    def test_actual_harmless_child_reaped_exact_owned_process(self):
        plan=self.check();real_popen=subprocess.Popen
        def lowlevel(argv,**kwargs):return real_popen([sys.executable,'-c','import time;time.sleep(60)'],**kwargs)
        with mock.patch.object(self.m.subprocess,'Popen',side_effect=lowlevel):
            owned=self.m.OwnedHostd(plan);owned.start()
        try:
            self.assertEqual(owned.readback()['status'],'running');self.assertFalse(owned.readback()['live_verified'])
            result=owned.stop(timeout=1);self.assertTrue(result['reaped']);self.assertEqual(result['status'],'stopped')
            self.assertEqual(owned.stop()['status'],'stopped')
        finally:
            if owned.process.poll() is None:owned.process.kill();owned.process.wait(timeout=3)
    def test_plan_changed_before_start_refused(self):
        plan=self.check();self.write(self.config,dict(self.cfg,chat_id='oc_prod'))
        with mock.patch.object(self.m.subprocess,'Popen') as popen:
            with self.assertRaises(self.m.PreparationError):self.m.OwnedHostd(plan).start()
            popen.assert_not_called()
    def test_cli_bad_input_fixed_rule16_no_raw_argument(self):
        error=io.StringIO()
        with redirect_stderr(error),mock.patch.object(self.m.subprocess,'Popen') as child:
            self.assertEqual(self.m.main(['--unknown=NEVER_PRINT_SECRET']),1)
        child.assert_not_called();self.assertIn('怎么解决',error.getvalue());self.assertIn('复制给 AI',error.getvalue())
        self.assertNotIn('NEVER_PRINT_SECRET',error.getvalue())
    def test_unstarted_stop_cannot_kill_arbitrary_pid(self):
        owned=self.m.OwnedHostd(self.check())
        with mock.patch.object(self.m.os,'killpg') as kill:
            self.assertEqual(owned.stop()['status'],'not_started');kill.assert_not_called()


class ProcessGroupTests(unittest.TestCase):
    setUp=PrepareTests.setUp
    write=PrepareTests.write
    save=PrepareTests.save
    check=PrepareTests.check
    def test_actual_child_and_grandchild_gracefully_reaped(self):
        plan=self.check();real_popen=subprocess.Popen
        marker=self.run/'runtime/child-created'
        script="""import subprocess,sys,time,signal,pathlib
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
def stop(*args):
 child.terminate();child.wait(timeout=3);sys.exit(0)
signal.signal(signal.SIGTERM,stop)
while True:time.sleep(.1)
"""
        def lowlevel(argv,**kwargs):return real_popen([sys.executable,'-c',script,str(marker)],**kwargs)
        with mock.patch.object(self.m.subprocess,'Popen',side_effect=lowlevel):
            owned=self.m.OwnedHostd(plan);owned.start()
        try:
            deadline=time.monotonic()+3
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(marker.exists());child_pid=int(marker.read_text())
            result=owned.stop(timeout=3);self.assertTrue(result['reaped']);self.assertTrue(result['children_reaped'])
            with self.assertRaises(ProcessLookupError):os.kill(child_pid,0)
        finally:
            if owned.process.poll() is None:
                os.killpg(owned.process.pid,15);owned.process.wait(timeout=4)

if __name__=='__main__':unittest.main()
