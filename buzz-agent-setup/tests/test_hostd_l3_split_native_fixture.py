"""Actual offline v2 fixture admission; synthetic opaque bytes do not prove native readiness."""
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import shlex
import sys
import unittest
from unittest import mock

import test_hostd_l3_fixture_builder as fixture
import test_hostd_l3_prepare as prepared_fixture

LOCAL = ['cli_aa48bbeeba38dbcf', 'cli_aa48b41531785bfc']
FOREIGN = 'cli_aa48b406abf8dbe7'


class SplitNativeFixtureTests(unittest.TestCase):
    def seed(self, *, split=True):
        f = fixture.FixtureBuilder(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        if split:
            for p in f.seed.glob('runtime/agent-2.*'):
                p.unlink()
            shutil.rmtree(f.seed/'profiles/2')
            f.manifest.unlink()
            f.cfg['agents'] = {pub: block for pub, block in f.cfg['agents'].items() if block['app_id'] in LOCAL}
            f.doc_catalog['agents'] = [row for row in f.doc_catalog['agents'] if row['feishu']['app_id'] in LOCAL]
            f.write(f.config, f.cfg)
            f.write(f.catalog, f.doc_catalog)
            f.bundle_doc.update(version=2, mode='TWO_LOCAL', local_app_ids=LOCAL.copy())
            f.save_bundle()
        return f

    def plan(self, f, *, pin=LOCAL):
        return f.builder().FixtureBuilder.check(f.bundle, run_parent=f.parent, run_id=fixture.RUN_ID,
            reviewed_source=f.source, reviewed_revision=prepared_fixture.REV, reviewed_branch=fixture.BRANCH,
            fixture_sha256=prepared_fixture.digest(f.fixture), python=f.doc['python'],
            expected_local_app_ids=pin, command_runner=f.runner)

    def admit(self, f, *, pin=LOCAL):
        return f.builder().prepare.PreparedRun.check(f.target/'manifest.json', reviewed_source=f.source,
            reviewed_revision=prepared_fixture.REV, fixture_sha256=prepared_fixture.digest(f.fixture),
            expected_local_app_ids=pin, command_runner=f.runner)

    def reject_builder(self, f):
        with self.assertRaises(f.builder().BuildError):
            self.plan(f)
        self.assertFalse(f.target.exists())

    def mutate_prepared(self, f, prepared, kind):
        rt=json.loads(prepared.onboarding_config.read_text())
        cfg_path=Path(rt['template_config']);cfg=json.loads(cfg_path.read_text())
        cat_path=Path(rt['catalog_path']);cat=json.loads(cat_path.read_text())
        row=cat['agents'][1];env=Path(row['env_file']);profile=Path(row['feishu']['lark_config_dir'])/'config.json'
        if kind=='owner':
            f.write(env,env.read_text().replace('BUZZ_ACP_AGENT_OWNER=', 'BUZZ_ACP_AGENT_OWNER=bad'))
        elif kind=='signer':
            f.write(env,env.read_text().replace('BUZZ_PRIVATE_KEY=', 'BUZZ_PRIVATE_KEY=bad'))
        elif kind=='attestation':
            body=env.read_text();f.write(env,body.replace('BUZZ_AUTH_TAG=', 'BUZZ_AUTH_TAG=bad'))
        elif kind=='channel':
            f.write(env,env.read_text().replace(f.cfg['channel_id'],'00000000-0000-0000-0000-000000000099'))
        elif kind=='profile':f.write(profile,{'apps':[{'appId':FOREIGN}]})
        elif kind=='missing_secret':(Path(row['feishu']['lark_data_dir'])/'lark-cli/master.key').unlink()
        elif kind=='secret_drift':f.write(Path(row['feishu']['lark_data_dir'])/'lark-cli/master.key',b'changed opaque')
        elif kind=='extra_secret':f.write(Path(row['feishu']['lark_data_dir'])/f'lark-cli/appsecret_{FOREIGN}.enc',b'opaque')
        elif kind=='hidden_profile_file':f.write(profile.parent/'foreign.env','FOREIGN_MATERIAL=synthetic')
        elif kind=='extra_row':cat['agents'].append(dict(row,name='extra'));f.write(cat_path,cat)
        elif kind=='missing_row':cat['agents'].pop();f.write(cat_path,cat)
        elif kind=='foreign_app':
            pub=next(pub for pub,b in cfg['agents'].items() if b['app_id']==LOCAL[1])
            cfg['agents'][pub]['app_id']=FOREIGN;f.write(cfg_path,cfg)
        elif kind=='pub_relocation':
            block=cfg['agents'].pop(next(pub for pub,b in cfg['agents'].items() if b['app_id']==LOCAL[1]))
            cfg['agents']['a'*64]=block;f.write(cfg_path,cfg)
        elif kind=='profile_relocation':
            row['feishu']['lark_config_dir']=cat['agents'][0]['feishu']['lark_config_dir'];f.write(cat_path,cat)
        elif kind=='nonprivate':profile.chmod(0o644)
        elif kind=='symlink':
            body=profile.read_bytes();profile.unlink();other=profile.parent/'real.json';f.write(other,body);profile.symlink_to(other)
        else:raise AssertionError(kind)

    def test_two_local_positive_build_and_revalidation(self):
        f=self.seed();plan=self.plan(f)
        self.assertEqual(plan.readback()['app_count'],2)
        prepared=plan.build();prepared.revalidate()
        self.assertEqual(prepared.local_app_ids,tuple(LOCAL))
        self.assertEqual(prepared.readback(),{'status':'prepared','live_verified':False,'app_count':2,'chat_count':2})
        manifest=json.loads((f.target/'manifest.json').read_text())
        self.assertEqual((manifest['version'],manifest['mode'],manifest['local_app_ids']),(2,'TWO_LOCAL',LOCAL))
        self.assertEqual(self.admit(f).local_app_ids,tuple(LOCAL))
        rt=json.loads(prepared.onboarding_config.read_text());cfg=json.loads(Path(rt['template_config']).read_text())
        cat=json.loads(Path(rt['catalog_path']).read_text())
        self.assertEqual({block['app_id'] for block in cfg['agents'].values()},set(LOCAL))
        self.assertEqual(len(cat['agents']),2)
        self.assertNotIn(FOREIGN,json.dumps(cfg));self.assertNotIn(FOREIGN,json.dumps(cat))
        for row in cat['agents']:
            self.assertTrue(Path(row['env_file']).is_relative_to(f.target))
            for name in ('lark_config_dir','lark_data_dir'):
                self.assertTrue(Path(row['feishu'][name]).is_relative_to(f.target))

    def test_v1_three_local_unchanged_manifest_and_readback(self):
        f=self.seed(split=False);plan=f.check();prepared=plan.build()
        self.assertEqual(plan.readback()['app_count'],3);self.assertEqual(prepared.readback()['app_count'],3)
        doc=json.loads((f.target/'manifest.json').read_text())
        self.assertEqual(doc['version'],1);self.assertNotIn('mode',doc);self.assertNotIn('local_app_ids',doc)
        prepared.revalidate()

    def test_actual_agentplan_selects_signed_local_a_and_rejects_foreign_b(self):
        f=self.seed()
        script=f.target/'provider/reviewed-provider.py'
        python=Path(sys.executable).resolve();native=Path('/usr/bin/true').resolve()
        row=f.doc_catalog['agents'][1];env=Path(row['env_file'])
        args='-I '+str(script)
        f.write(env,env.read_text()+'BUZZ_ACP_AGENT_COMMAND='+str(python)+'\nBUZZ_ACP_AGENT_ARGS='+','.join(shlex.split(args))+'\n')
        f.write(f.seed/'runtime/agent-1.responsible.json',{'channels':[f.cfg['channel_id']]})
        f.save_bundle();prepared=self.plan(f).build()
        script.parent.mkdir(mode=0o700);f.write(script,'# Synthetic reviewed provider, never executed.\n')
        # Existing public composition adds separately pinned scenario/provider
        # inputs after materialization, before either owned process starts.
        import base64
        user_cfg=f.target/'profiles/user/config';user_data=f.target/'profiles/user/data'
        user_cfg.mkdir(parents=True,mode=0o700);user_cfg.parent.chmod(0o700);user_data.mkdir(mode=0o700)
        f.write(user_cfg/'config.json',{'apps':[{'appId':'cli_a940faa4ec381bc4','name':'jchen-personal'}]})
        human_key='6'.zfill(64);human=f.gs._signer_pubkey(human_key)
        human_env=f.target/'runtime/human.env';f.write(human_env,'BUZZ_PRIVATE_KEY='+human_key+'\nBUZZ_RELAY_URL='+f.doc['relay_url']+'\n')
        phase=f.target/'runtime/scenario';phase.mkdir(mode=0o700)
        image=phase/'image.png';f.write(image,base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        body1=phase/'msg001.json';body2=phase/'msg002.txt'
        f.write(body1,{'zh_cn':{'title':'','content':[[{'tag':'text','text':'Synthetic message'},{'tag':'at','user_id':'ou_synthetic_a'},{'tag':'img','image_key':'img_synthetic'}]]}})
        f.write(body2,'Synthetic reply')
        pub=next(pub for pub,block in f.cfg['agents'].items() if block['app_id']==LOCAL[1])
        scenario=phase/'manifest.json'
        f.write(scenario,dict(version=1,run_id=prepared.run_id,revision=prepared.revision,initial_binding=prepared.initial_binding,
            channel_id=f.cfg['channel_id'],chat_id=f.cfg['chat_id'],sync_app_id=LOCAL[0],agent_app_id=LOCAL[1],agent_pubkey=pub,
            user_config_dir=str(user_cfg),user_data_dir=str(user_data),user_profile='jchen-personal',human_env_file=str(human_env),
            human_pubkey=human,phase_dir=str(phase),msg001_content_file=str(body1),msg002_content_file=str(body2),
            image_file=str(image),image_key='img_synthetic',image_sha256=prepared_fixture.digest(image),link_base=f.cfg['people_api']['base_url']))
        rt=json.loads(prepared.onboarding_config.read_text())
        launcher=importlib.import_module('integration.hostd_l3.agent_launcher')
        values=dict(catalog_path=rt['catalog_path'],legacy_join_path=rt['legacy_join_path'],agent_name='fixture-1',
            native_buzz_acp=native,native_sha256=prepared_fixture.digest(native),provider_python=python,
            python_sha256=prepared_fixture.digest(python),provider_script=script,
            script_sha256=prepared_fixture.digest(script),provider_args=args)
        plan=launcher.AgentPlan.check(prepared,**values)
        self.assertEqual(plan.app_id,LOCAL[1]);self.assertIn(plan.pubkey,f.cfg['agents']);plan.revalidate()
        self.assertEqual((f.target/'manifest.json').stat().st_nlink,1)
        scenario_module=importlib.import_module('integration.hostd_l3.scenario_driver')
        scenario_plan=scenario_module.ScenarioPlan.check(prepared,scenario,prepared_fixture.digest(scenario),human_identity_pin=human)
        self.assertEqual(json.loads(scenario_plan._doc)['agent_app_id'],LOCAL[1]);prepared.revalidate()
        with self.assertRaises(launcher.LauncherError):
            launcher.AgentPlan.check(prepared,**dict(values,agent_name='fixture-2'))

    def test_manifest_and_external_pin_independent_exact_contract(self):
        for kind in ('absent','wrong','reversed','malformed','unknown_mode','old_version','extra_app','missing_app','malformed_manifest'):
            with self.subTest(kind=kind):
                f=self.seed();self.plan(f)  # valid precondition, no prior mutation
                if kind in ('absent','wrong','reversed','malformed'):
                    pin={'absent':None,'wrong':[LOCAL[0],FOREIGN],'reversed':list(reversed(LOCAL)),'malformed':LOCAL[0]}[kind]
                    with self.assertRaises(f.builder().BuildError):self.plan(f,pin=pin)
                else:
                    if kind=='unknown_mode':f.bundle_doc['mode']='ARBITRARY'
                    if kind=='old_version':f.bundle_doc={'version':1,**{k:v for k,v in f.bundle_doc.items() if k not in ('version','mode','local_app_ids')}}
                    if kind=='extra_app':f.bundle_doc['local_app_ids']=LOCAL+[FOREIGN]
                    if kind=='missing_app':f.bundle_doc['local_app_ids']=LOCAL[:1]
                    if kind=='malformed_manifest':f.bundle_doc['local_app_ids']=LOCAL[0]
                    f.save_bundle();self.reject_builder(f)

    def test_prepared_pin_required_and_immutable_plan_fields(self):
        f=self.seed();plan=self.plan(f);prepared=plan.build()
        for pin in (None,[LOCAL[0],FOREIGN],list(reversed(LOCAL)),LOCAL[0]):
            with self.subTest(pin=pin):
                with self.assertRaises(f.builder().prepare.PreparationError):self.admit(f,pin=pin)
        for fields in ({'local_app_ids':(LOCAL[0],FOREIGN)}, {'_expected_local_app_ids':None}, {'mode':'ARBITRARY'},
                       {'mode':'THREE_LOCAL','_inventory':()}):
            with self.subTest(fields=fields):
                with self.assertRaises(f.builder().prepare.PreparationError):dataclasses.replace(prepared,**fields).revalidate()

    def test_builder_plan_external_pin_and_mode_cannot_be_replaced(self):
        for fields in ({'_expected_local_app_ids':None},{'local_app_ids':(LOCAL[0],FOREIGN)},{'mode':'ARBITRARY'}):
            with self.subTest(fields=fields):
                f=self.seed();plan=self.plan(f)
                with self.assertRaises(f.builder().BuildError):dataclasses.replace(plan,**fields).build()
                self.assertFalse(f.target.exists())

    def test_prepared_combined_legacy_reset_cannot_bypass_original_v2(self):
        for extra_file in (False, True):
            with self.subTest(extra_file=extra_file):
                f=self.seed();prepared=self.plan(f).build();prepared.revalidate()
                reset=dataclasses.replace(prepared,mode='THREE_LOCAL',
                    local_app_ids=tuple(sorted(f.builder().prepare.APPS)),
                    _expected_local_app_ids=None,_inventory=(),_manifest_path=None)
                if extra_file:
                    rt=json.loads(prepared.onboarding_config.read_text())
                    cfg=json.loads(Path(rt['template_config']).read_text())
                    profile=Path(next(iter(cfg['agents'].values()))['lark_config_dir'])
                    f.write(profile/'foreign.env','SYNTHETIC_REVIEW_ONLY=1\n')
                    with self.assertRaises(f.builder().prepare.PreparationError):prepared.revalidate()
                with self.assertRaises(f.builder().prepare.PreparationError):reset.revalidate()

    def test_builder_combined_legacy_reset_rejects_before_any_output(self):
        for extra_file in (False, True):
            with self.subTest(extra_file=extra_file):
                f=self.seed();plan=self.plan(f);plan._revalidate()
                reset=dataclasses.replace(plan,mode='THREE_LOCAL',
                    local_app_ids=tuple(sorted(f.builder().prepare.APPS)),
                    _expected_local_app_ids=None,_seed_inventory=())
                if extra_file:
                    f.write(f.seed/'foreign.env','SYNTHETIC_REVIEW_ONLY=1\n')
                    with self.assertRaises(ValueError):plan._revalidate()
                with self.assertRaises(f.builder().BuildError):reset.build()
                self.assertFalse(f.target.exists())

    def test_v1_arbitrary_manifest_basename_and_staging_paths_revalidate(self):
        f=self.seed(split=False);prepared=f.check().build();prepared.revalidate()
        custom=f.target/'arbitrary reviewed input.json'
        (f.target/'manifest.json').rename(custom)
        for manifest in (custom,f.target/'pending-manifest.json',f.target/'admitted-manifest.json'):
            with self.subTest(manifest=manifest.name):
                plan=f.builder().prepare.PreparedRun.check(manifest,reviewed_source=f.source,
                    reviewed_revision=prepared_fixture.REV,fixture_sha256=prepared_fixture.digest(f.fixture),command_runner=f.runner)
                plan.revalidate();self.assertEqual(plan.readback()['app_count'],3)

    def test_prepared_each_identity_material_and_path_control_reaches_its_gate(self):
        kinds=('owner','signer','attestation','channel','profile','missing_secret','extra_secret','hidden_profile_file',
               'extra_row','missing_row','foreign_app','pub_relocation','profile_relocation','nonprivate','symlink')
        for kind in kinds:
            with self.subTest(kind=kind):
                f=self.seed();prepared=self.plan(f).build();self.admit(f)  # independently accepted
                self.mutate_prepared(f,prepared,kind)
                with self.assertRaises(f.builder().prepare.PreparationError):self.admit(f)
                with self.assertRaises(f.builder().prepare.PreparationError):prepared.revalidate()

    def test_prepared_protected_opaque_bytes_drift_rejected_without_native_claim(self):
        f=self.seed();prepared=self.plan(f).build();self.admit(f)
        self.mutate_prepared(f,prepared,'secret_drift')
        # A new admission pins new opaque bytes, while the original admitted
        # plan must reject drift; neither operation decrypts or proves readiness.
        self.assertFalse(self.admit(f).readback()['live_verified'])
        with self.assertRaises(f.builder().prepare.PreparationError):prepared.revalidate()

    def test_builder_hidden_unlisted_foreign_file_and_opaque_drift(self):
        for kind in ('unlisted','listed','foreign_credential','drift','nonprivate','symlink','owner','app','profile'):
            with self.subTest(kind=kind):
                f=self.seed();plan=self.plan(f)
                row=f.doc_catalog['agents'][1];profile=Path(row['feishu']['lark_config_dir'])/'config.json'
                secret=Path(row['feishu']['lark_data_dir'])/'lark-cli/master.key'
                if kind in ('unlisted','listed'):f.write(f.seed/'foreign.env','SYNTHETIC B')
                elif kind=='foreign_credential':f.write(secret.parent/f'appsecret_{FOREIGN}.enc',b'SYNTHETIC')
                elif kind=='drift':f.write(secret,b'changed opaque')
                elif kind=='nonprivate':secret.chmod(0o644)
                elif kind=='symlink':secret.unlink();secret.symlink_to(profile)
                elif kind=='owner':
                    env=Path(row['env_file']);f.write(env,env.read_text().replace('BUZZ_ACP_AGENT_OWNER=','BUZZ_ACP_AGENT_OWNER=bad'))
                elif kind=='app':
                    row['feishu']['app_id']=FOREIGN;f.write(f.catalog,f.doc_catalog)
                elif kind=='profile':f.write(profile,{'apps':[{'appId':FOREIGN}]})
                with self.assertRaises(f.builder().BuildError):plan.build()
                self.assertFalse(f.target.exists())
                if kind not in ('unlisted','drift','nonprivate','symlink'):f.save_bundle()
                if kind=='drift':
                    f.save_bundle();self.plan(f)  # opaque bytes may be newly reviewed, never runtime-verified
                else:self.reject_builder(f)

    def test_duplicate_identity_and_missing_cfg_control(self):
        for kind in ('duplicate_app','duplicate_profile','missing_cfg','extra_cfg','swapped_apps'):
            with self.subTest(kind=kind):
                f=self.seed();self.plan(f)
                blocks=list(f.cfg['agents'].values())
                if kind=='duplicate_app':blocks[1]['app_id']=blocks[0]['app_id']
                elif kind=='duplicate_profile':blocks[1]['lark_config_dir']=blocks[0]['lark_config_dir']
                elif kind=='missing_cfg':f.cfg['agents'].pop(next(pub for pub,b in f.cfg['agents'].items() if b['app_id']==LOCAL[1]))
                elif kind=='extra_cfg':f.cfg['agents']['a'*64]=dict(blocks[1],app_id=FOREIGN)
                elif kind=='swapped_apps':blocks[0]['app_id'],blocks[1]['app_id']=blocks[1]['app_id'],blocks[0]['app_id']
                f.write(f.config,f.cfg);f.save_bundle();self.reject_builder(f)


if __name__=='__main__':unittest.main()
