"""Static run packaging with real schemas and synthetic own identities only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_hostd_l3_prepare as fixture

MODULE=Path(__file__).parent/'integration/hostd_l3/fixture_builder.py'
BRANCH='hostd-l3-reviewed'
RUN_ID='1'*32


class FixtureBuilder(unittest.TestCase):
    def setUp(self):
        # Reuse the actual PreparedRun fixture shape, not a fake admission API.
        fixture.PrepareTests.setUp(self)
        self.seed=self.run
        self.parent=self.root/'candidates';self.parent.mkdir(mode=0o700)
        self.target=self.parent/self.seed.name
        import buzz_feishu_group_sync as gs
        self.gs=gs;owner_key='4'.zfill(64);owner=gs._signer_pubkey(owner_key)
        old_agents=list(self.cfg['agents'].values());agents={};rows=[]
        for i,block in enumerate(old_agents):
            key=str(i+1).zfill(64);pub=gs._signer_pubkey(key)
            signature=gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{pub}:'.encode()).digest(),bytes.fromhex(owner_key),bytes(32)).hex()
            prompt=self.seed/f'runtime/agent-{i}.prompt';responsible=self.seed/f'runtime/agent-{i}.responsible.json'
            self.write(prompt,'Synthetic run fixture; no personal content.');self.write(responsible,{'version':1,'channels':{}})
            env=self.seed/f'runtime/agent-{i}.env'
            self.write(env,'BUZZ_PRIVATE_KEY='+key+'\nBUZZ_AUTH_TAG='+json.dumps(['auth',owner,'',signature],separators=(',',':'))+'\nBUZZ_RELAY_URL='+self.doc['relay_url']+'\nBUZZ_ACP_AGENT_OWNER='+owner+'\nBUZZ_ACP_CHANNELS='+self.cfg['channel_id']+'\nBUZZ_ACP_SYSTEM_PROMPT_FILE='+str(prompt)+'\nBUZZ_RESPONSIBLE_CONFIG='+str(responsible)+'\n')
            agents[pub]=block
            rows.append({'name':f'fixture-{i}','env_file':str(env),'unit':f'buzz-l3-{RUN_ID}-{i}.service','capabilities':{'summary':'synthetic','repos':[]},'feishu':block})
            data=Path(block['lark_data_dir'])/'lark-cli';data.mkdir(mode=0o700)
            # Opaque synthetic credential bytes are pinned/copied, never decrypted
            # or described as runtime-verified by this static generator.
            self.write(data/'master.key',b'\x11'*32)
            self.write(data/f"appsecret_{block['app_id']}.enc",b'\x22'*12+b'SYNTHETIC_CIPHERTEXT_AND_TAG')
        self.cfg['agents']=agents;self.cfg['desk_pubkey']=next(iter(agents))
        self.cfg['mirror_pubkey']=gs._signer_pubkey('5'.zfill(64))
        self.write(self.seed/'runtime/owner.env','BUZZ_PRIVATE_KEY='+owner_key+'\nBUZZ_RELAY_URL='+self.doc['relay_url']+'\n')
        mirror_key='5'.zfill(64);mirror=self.cfg['mirror_pubkey']
        mirror_sig=gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{mirror}:'.encode()).digest(),bytes.fromhex(owner_key),bytes(32)).hex()
        self.write(self.seed/'runtime/mirror.env','BUZZ_PRIVATE_KEY='+mirror_key+'\nBUZZ_AUTH_TAG='+json.dumps(['auth',owner,'',mirror_sig],separators=(',',':'))+'\nBUZZ_RELAY_URL='+self.doc['relay_url']+'\n')
        self.write(self.config,self.cfg)
        self.doc_catalog={'version':1,'owner_pubkey':owner,'buzz':{'cli_path':str(self.buzz),'cli_sha256':fixture.digest(self.buzz)},'state_dir':str(self.seed/'runtime/join-state'),'lark_cli':str(self.seed/'lark-cli'),'agents':rows}
        self.write(self.catalog,self.doc_catalog);self.write(self.legacy,dict(self.doc_catalog,agents=[]))
        self.write(self.seed/'lark-cli','Synthetic root-supplied CLI placeholder; never executed.')
        self.trust=self.bundle
        self.bundle=self.seed/'bundle.json'
        self.bundle_doc={'version':1,'binding_config':str(self.config),'catalog_path':str(self.catalog),'legacy_join_path':str(self.legacy),'onboarding_config':str(self.onboarding),'fixture_path':str(self.fixture),'fixture_sha256':fixture.digest(self.fixture),'trust_bundle':str(self.trust),'buzz_cli':str(self.buzz),'files':{}}
        self.save_bundle()
        def git(argv,**kwargs):
            if 'symbolic-ref' in argv:return subprocess.CompletedProcess(argv,0,BRANCH+'\n','')
            return self.head_result if 'rev-parse' in argv else self.status_result
        self.runner=mock.Mock(side_effect=git)

    save=fixture.PrepareTests.save

    def write(self,p,value):
        if isinstance(value,bytes):p.write_bytes(value);p.chmod(0o600)
        else:fixture.PrepareTests.write(self,p,value)

    def save_bundle(self):
        # Include exactly the input dependency closure, excluding the old fixture
        # admission manifest and this packaging descriptor itself.
        self.bundle_doc['files']={str(p.relative_to(self.seed)):fixture.digest(p) for p in self.seed.rglob('*') if p.is_file() and p not in (self.manifest,self.bundle)}
        self.write(self.bundle,self.bundle_doc)

    def builder(self):
        if hasattr(self,'_builder'):return self._builder
        spec=importlib.util.spec_from_file_location('l3_fixture_builder',MODULE)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        self._builder=module
        return module

    def check(self):
        return self.builder().FixtureBuilder.check(self.bundle,run_parent=self.parent,run_id=RUN_ID,
            reviewed_source=self.source,reviewed_revision=fixture.REV,reviewed_branch=BRANCH,
            fixture_sha256=fixture.digest(self.fixture),python=str(Path(sys.executable).resolve()),command_runner=self.runner)

    def reject(self):
        module=self.builder()
        with self.assertRaises(module.BuildError) as exc:self.check()
        self.assertIn('怎么解决',str(exc.exception));self.assertIn('复制给 AI',str(exc.exception))
        self.assertNotIn('SYNTHETIC',str(exc.exception));self.assertFalse(self.target.exists())

    def test_check_is_readonly_and_cannot_claim_ready(self):
        before={str(p):p.read_bytes() for p in self.seed.rglob('*') if p.is_file()}
        plan=self.check();self.assertFalse(self.target.exists())
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.seed.rglob('*') if p.is_file()})
        self.assertFalse(plan.readback()['live_verified']);self.assertEqual(plan.readback()['runtime_verification'],'pending')

    def test_build_actual_preparedrun_admission_with_three_exact_apps_two_chats(self):
        plan=self.check();prepared=plan.build()
        self.assertEqual(Path(sys.modules[type(prepared).__module__].__file__).resolve(),fixture.MODULE.resolve())
        self.assertEqual(prepared.readback(),{'status':'prepared','live_verified':False,'app_count':3,'chat_count':2})
        manifest=json.loads((self.target/'manifest.json').read_text())
        admitted=self.m.PreparedRun.check(self.target/'manifest.json',reviewed_source=self.source,reviewed_revision=fixture.REV,fixture_sha256=fixture.digest(self.fixture),command_runner=self.runner)
        self.assertEqual(admitted.readback()['status'],'prepared')
        self.assertEqual(manifest['source_root'],str(self.source));self.assertEqual(manifest['revision'],fixture.REV)
        receipt=json.loads((self.target/'builder-receipt.json').read_text())
        self.assertEqual(receipt['runtime_verification'],'pending');self.assertTrue(receipt['credentials_pinned'])
        self.assertNotIn('own_bot_verified',json.dumps(receipt));self.assertNotIn('SYNTHETIC_CIPHERTEXT',json.dumps(plan.readback()))

    def test_paths_relocated_only_in_schema_fields_not_text_bodies(self):
        p=self.seed/'runtime/agent-0.prompt';self.write(p,'Keep literal '+str(self.seed)+' in this synthetic prompt.')
        self.save_bundle();self.check().build()
        output=self.target/'runtime/agent-0.prompt';self.assertEqual(output.read_bytes(),p.read_bytes())
        cfg=json.loads((self.target/'home/.config/buzz-feishu-sync/hostd-local-l3/config.json').read_text())
        self.assertTrue(Path(cfg['mirror_env_file']).is_relative_to(self.target))
        self.assertEqual(set(cfg['agents']),set(self.cfg['agents']))

    def test_private_output_files_directories_and_existing_join_state(self):
        self.check().build()
        for p in self.target.rglob('*'):
            self.assertFalse(p.is_symlink());self.assertEqual(p.stat().st_uid,os.geteuid())
            self.assertEqual(p.stat().st_mode&0o777,0o700 if p.is_dir() or p.name=='buzz' else 0o600)
        self.assertEqual((self.target/'runtime/join-state').stat().st_mode&0o777,0o700)

    def test_app_allowlist_exact_identity_not_just_count(self):
        blocks=list(self.cfg['agents'].values());blocks[2]['app_id']='cli_unapproved'
        self.write(self.config,self.cfg);self.save_bundle();self.reject()

    def test_wrong_or_extra_chat_fixture_rejected(self):
        self.write(self.fixture,{'hostd-test-群X':'oc_wrong','hostd-test-群W-未绑定':'oc_testw','extra':'oc_other'})
        self.save_bundle();self.reject()

    def test_external_fixture_hash_not_self_authorized_by_bundle(self):
        self.bundle_doc['fixture_sha256']='0'*64;self.write(self.bundle,self.bundle_doc);self.reject()

    def test_wrong_source_head_or_branch_rejected_before_write(self):
        self.head_result.stdout='b'*40;self.reject();self.head_result.stdout=fixture.REV+'\n'
        self.runner.side_effect=lambda argv,**kw:subprocess.CompletedProcess(argv,0,'wrong\n','') if 'symbolic-ref' in argv else (self.head_result if 'rev-parse' in argv else self.status_result)
        self.reject()

    def test_dirty_source_or_missing_own_git_rejected_before_write(self):
        self.status_result.stdout=' M owned.py\n';self.reject();self.status_result.stdout=''
        (self.source/'.git').rmdir();self.reject()

    def test_extra_file_and_path_escape_rejected(self):
        self.write(self.seed/'extra.txt','SYNTHETIC');self.save_bundle();self.reject()
        self.bundle_doc['files'].pop('extra.txt');self.bundle_doc['files']['../escape']='a'*64
        self.write(self.bundle,self.bundle_doc);self.reject()

    def test_unsafe_permissions_symlink_file_and_ancestor_rejected(self):
        self.config.chmod(0o644);self.reject();self.config.chmod(0o600)
        content=self.config.read_bytes();self.config.unlink();real=self.seed/'real-config';self.write(real,content)
        self.config.symlink_to(real);self.reject();self.config.unlink();self.write(self.config,content)
        link=self.root/'linked-parent';link.symlink_to(self.parent,target_is_directory=True)
        module=self.builder()
        with self.assertRaises(module.BuildError):module.FixtureBuilder.check(self.bundle,run_parent=link,run_id=RUN_ID,reviewed_source=self.source,reviewed_revision=fixture.REV,reviewed_branch=BRANCH,fixture_sha256=fixture.digest(self.fixture),python=str(Path(sys.executable).resolve()),command_runner=self.runner)

    def test_catalog_legacy_overlap_and_owner_mismatch_rejected(self):
        self.write(self.legacy,self.doc_catalog);self.save_bundle();self.reject()
        self.write(self.legacy,dict(self.doc_catalog,agents=[],owner_pubkey='a'*64));self.save_bundle();self.reject()

    def test_agent_identity_oa_and_profile_mismatch_rejected(self):
        env=Path(self.doc_catalog['agents'][0]['env_file']);original=env.read_text()
        self.write(env,original.replace('BUZZ_PRIVATE_KEY=','BUZZ_PRIVATE_KEY=bad'));self.save_bundle();self.reject()
        self.write(env,original)
        profile=Path(self.doc_catalog['agents'][0]['feishu']['lark_config_dir'])/'config.json'
        self.write(profile,{'apps':[{'appId':'cli_wrong'}]});self.save_bundle();self.reject()

    def test_existing_run_never_overwritten(self):
        self.target.mkdir(mode=0o700);sentinel=self.target/'sentinel';self.write(sentinel,'SYNTHETIC existing')
        with self.assertRaises(self.builder().BuildError):self.check()
        self.assertEqual(sentinel.read_text(),'SYNTHETIC existing')

    def test_build_revalidates_material_bytes_and_source_before_any_write(self):
        plan=self.check();self.write(self.seed/'runtime/agent-0.prompt','changed SYNTHETIC')
        with self.assertRaises(self.builder().BuildError):plan.build()
        self.assertFalse(self.target.exists())
        self.save_bundle();plan=self.check();self.head_result.stdout='b'*40
        with self.assertRaises(self.builder().BuildError):plan.build()
        self.assertFalse(self.target.exists())

    def test_failed_write_leaves_no_success_manifest_and_no_existing_file_damage(self):
        plan=self.check();module=self.builder()
        with mock.patch.object(module.os,'write',side_effect=OSError('SYNTHETIC private failure')):
            with self.assertRaises(module.BuildError) as exc:plan.build()
        self.assertNotIn('SYNTHETIC',str(exc.exception));self.assertFalse((self.target/'manifest.json').exists())
        self.assertTrue(self.config.is_file())
        # A failure during the final manifest write must not leave a file that
        # looks successful. Use a second fresh parent; preserve the first pending
        # directory rather than cleaning it as part of the product flow.
        self.parent=self.root/'second-candidates';self.parent.mkdir(mode=0o700)
        self.target=self.parent/self.seed.name;second=self.check()
        original=type(second)._write
        manifest_writes=0
        def partial(fd,rel,raw,mode):
            nonlocal manifest_writes
            if raw==second._manifest:
                manifest_writes+=1
                if manifest_writes==2:
                    original(fd,rel,raw[:16],mode)
                    raise OSError('SYNTHETIC partial manifest')
            return original(fd,rel,raw,mode)
        with mock.patch.object(type(second),'_write',side_effect=partial):
            with self.assertRaises(module.BuildError):second.build()
        self.assertFalse((self.target/'manifest.json').exists())

    def test_no_process_start_clone_systemd_or_saas_calls(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('process start forbidden')):
            self.check().build()
        self.assertTrue(all('clone' not in call.args[0] and 'systemctl' not in call.args[0] for call in self.runner.call_args_list))
