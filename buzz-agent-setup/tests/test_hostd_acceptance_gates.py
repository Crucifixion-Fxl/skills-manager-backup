"""Acceptance gates use temporary protected metadata and fake CLI only."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS/'hostd_probes'))
sys.path.insert(0,str(TESTS.parent/'scripts'))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m
    if path.name=='bot_api_probe.py':
        import feishu_creds
        with mock.patch.object(feishu_creds,'lark',return_value={'ok':False}),mock.patch.object(feishu_creds,'app_credentials',return_value=('cli_offline','offline')):
            spec.loader.exec_module(m)
    else:spec.loader.exec_module(m)
    return m
class Gates(unittest.TestCase):
    def setUp(self):
        self.l4=module('safe_l4',TESTS/'integration/hostd_l4/l4_smoke.py')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.fixture=self.root/'groups.json';self.cfg=self.root/'config.json';self.manifest=self.root/'run.json'
        self.write(self.fixture,{'hostd-test-群X':'oc_test','hostd-test-群W-未绑定':'oc_w'})
        self.config={'chat_id':'oc_test','channel_id':'00000000-0000-0000-0000-000000000001','desk_pubkey':'a'*64,'agents':{'a'*64:{'app_id':'cli_aa48bbeeba38dbcf'}},'buzz_cli':'/bin/true','people_api':{'signer_env_file':str(self.root/'owner.env')},'reaction_sync':'two_way'}
        self.run={'version':1,'scope':'hostd-local-l3','binding':'hostd-local-l3','config_path':str(self.cfg),'fixture_path':str(self.fixture),'test_group':'hostd-test-群X','chat_id':'oc_test','channel_id':self.config['channel_id'],'sync_app_id':'cli_aa48bbeeba38dbcf','relay_url':'ws://127.0.0.1:7777','user_profile':'jchen-personal','user_app_id':'cli_a940faa4ec381bc4','owner_open_id':'ou_755158d120e03b0c18dd4a9334bc3aad'}
        # /bin is a symlink on this machine; use the real ELF path.
        self.config['buzz_cli']=str(Path('/bin/true').resolve());self.save()
    def write(self,p,obj):p.write_text(json.dumps(obj) if isinstance(obj,dict) else obj);p.chmod(0o600)
    def save(self):self.write(self.cfg,self.config);self.write(self.manifest,self.run)
    def gate(self,**kw):return self.l4.authorize_run('hostd-local-l3',opt_in=True,manifest_path=self.manifest,**kw)
    def test_default_refusal_before_credential_or_cli(self):
        with mock.patch.object(self.l4,'Side') as side,mock.patch.object(self.l4.subprocess,'run') as cli:
            self.assertEqual(self.l4.main(['hostd-local-l3']),1);side.assert_not_called();cli.assert_not_called()
    def test_protected_fixed_manifest_accepts_local_only(self):
        g=self.gate();self.assertEqual(g.cfg['chat_id'],'oc_test');self.assertEqual(g.relay_url,self.run['relay_url'])
    def test_production_relay_and_arbitrary_binding_reject(self):
        for url in ['wss://relay.buzz.example','ws://127.0.0.1.attacker.test','ws://localhost@evil.test','ws://0.0.0.0:7777']:
            self.run['relay_url']=url;self.save()
            with self.assertRaises(self.l4.GateError):self.gate()
        with self.assertRaises(self.l4.GateError):self.l4.authorize_run('golf',opt_in=True,manifest_path=self.manifest)
    def test_app_chat_and_wrapper_not_self_authorized_by_manifest(self):
        self.run['sync_app_id']='cli_production';self.config['agents']['a'*64]['app_id']='cli_production';self.save()
        with self.assertRaises(self.l4.GateError):self.gate()
        self.run['sync_app_id']='cli_aa48bbeeba38dbcf';self.config['agents']['a'*64]['app_id']=self.run['sync_app_id'];self.run['chat_id']=self.config['chat_id']='oc_prod';self.save()
        with self.assertRaises(self.l4.GateError):self.gate()
        self.run['chat_id']=self.config['chat_id']='oc_test';wrapper=self.root/'buzz';self.write(wrapper,'#!/bin/sh\nexit 0\n');wrapper.chmod(0o700);self.config['buzz_cli']=str(wrapper);self.save()
        with self.assertRaises(self.l4.GateError):self.gate()
    def test_manifest_symlink_permissions_and_credential_escape_reject(self):
        self.manifest.chmod(0o644)
        with self.assertRaises(self.l4.GateError):self.gate()
        self.manifest.chmod(0o600);target=self.root/'real.json';self.manifest.rename(target);self.manifest.symlink_to(target)
        with self.assertRaises(self.l4.GateError):self.gate()
        self.manifest.unlink();self.config['people_api']['signer_env_file']='/home/jchen/.config/buzz/env';self.save()
        with self.assertRaises(self.l4.GateError):self.gate()
    def test_owner_readback_mismatch_never_reads_signer(self):
        g=self.gate()
        with mock.patch.object(self.l4,'lark',return_value={'ok':True,'appId':'cli_wrong'}),mock.patch.object(self.l4,'read_owned') as read:
            with self.assertRaises(self.l4.GateError):self.l4.Side(g)
            read.assert_not_called()
    def test_owner_verification_explicit_node_and_profile(self):
        with mock.patch.object(self.l4.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='{"ok":true}')) as call:
            self.l4.lark('auth','status',profile='jchen-personal')
        argv=call.call_args.args[0];self.assertEqual(argv[:2],[self.l4.NODE,self.l4.CLI_ENTRY]);self.assertIn('--profile',argv);self.assertIn('jchen-personal',argv)
        self.assertNotIn('--as',argv)
        self.assertIn('--verify',argv)
        self.assertIn('--json',argv)
    def test_owner_readback_and_local_signer_positive(self):
        g=self.gate();self.write(self.root/'owner.env',f'BUZZ_PRIVATE_KEY=offline\nBUZZ_RELAY_URL={g.relay_url}\n')
        replies=[{'appId':g.user_app_id},{'data':{'open_id':g.owner_open_id,'name':'陈敬敏'}}]
        with mock.patch.object(self.l4,'lark',side_effect=replies):side=self.l4.Side(g)
        self.assertEqual(side.buzz_env['BUZZ_RELAY_URL'],g.relay_url)
        self.write(self.root/'owner.env','BUZZ_RELAY_URL=wss://production.example\n')
        with mock.patch.object(self.l4,'lark',side_effect=replies),self.assertRaises(self.l4.GateError):self.l4.Side(g)
    def test_off_or_missing_message_id_fail_full_smoke(self):
        for mode,fmid in [('off','om_1'),('two_way',None)]:
            side=mock.Mock();side.cfg=dict(self.config,reaction_sync=mode);side.lark.return_value={'data':{'message_id':fmid}}
            hit={'sender':{'id':self.run['sync_app_id']}};ev={'id':'e1','pubkey':'b'*64,'content':'[飞书] test'};side.cfg['mirror_pubkey']='b'*64
            with mock.patch.object(self.l4,'authorize_run',return_value=object()),mock.patch.object(self.l4,'Side',return_value=side),mock.patch.object(self.l4,'wait',side_effect=[(1,hit),(1,ev)]),mock.patch.object(self.l4,'OUT',self.root/'out'):
                self.assertEqual(self.l4.main(['hostd-local-l3','--allow-local-l3','--run-manifest',str(self.manifest)]),1)
            receipt=json.loads((self.root/'out'/'hostd-local-l3.json').read_text());self.assertFalse(receipt['checks']['reaction_f2b_arrived']);self.assertIn('reaction_f2b_arrived',receipt['failed_checks']);self.assertEqual(receipt['reaction_status'],'unverified')

class BotProbe(unittest.TestCase):
    def test_import_never_reads_home_or_calls_cli(self):
        import feishu_creds
        spec=importlib.util.spec_from_file_location('safe_bot_import',TESTS/'hostd_probes/bot_api_probe.py');m=importlib.util.module_from_spec(spec)
        with mock.patch.object(Path,'home',side_effect=AssertionError('HOME read')),mock.patch.object(feishu_creds,'lark',side_effect=AssertionError('network')) as cli,mock.patch.object(feishu_creds,'app_credentials',side_effect=AssertionError('credentials')) as creds,mock.patch('subprocess.run',side_effect=AssertionError('subprocess')):
            spec.loader.exec_module(m)
        cli.assert_not_called();creds.assert_not_called()
        self.assertTrue(callable(m.main))
    def fake(self,scopes):
        fc=mock.Mock();fc.app_credentials.side_effect=lambda name:({'hostd-test-desk':'cli_aa48bbeeba38dbcf','hostd-test-a':'cli_aa48b41531785bfc','hostd-test-b':'cli_aa48b406abf8dbe7'}[name],'NEVER_PRINT_SECRET')
        def call(name,*args):
            if '/open-apis/calendar/v4/calendars' in args:return {'ok':False,'error':{'code':99991672,'console_url':'https://open.feishu.cn/page/scope-apply?clientID=cli_b','missing_scopes':scopes}}
            if '+chat-members-list' in args:return {'ok':True,'data':{'bots':[{'app_id':'cli_aa48b41531785bfc','member_id':'ou_a'},{'app_id':'cli_aa48b406abf8dbe7','member_id':'ou_b'}],'users':[],'user_total':0,'bot_total':2,'has_more':False,'truncations':[]}}
            if '/open-apis/im/v1/messages' in args:return {'ok':True,'data':{'items':[{'sender':{'sender_type':'app'}}]}}
            if '/open-apis/im/v1/chats' in args:return {'ok':True,'data':{'items':[{'chat_id':'oc_test'}]}}
            return {'ok':True,'data':{}}
        fc.lark.side_effect=call;return fc
    def test_reconfigured_test_profile_cannot_borrow_production_app(self):
        m=module('safe_bot_identity',TESTS/'hostd_probes/bot_api_probe.py');client=self.fake([])
        client.app_credentials.side_effect=lambda name:('cli_production','NEVER_PRINT_SECRET')
        with self.assertRaises(ValueError):m.run_probe('oc_test',client)
        client.lark.assert_not_called()
    def test_missing_scope_names_fail_loud(self):
        m=module('safe_bot_run',TESTS/'hostd_probes/bot_api_probe.py')
        for scopes in [None,[],{},'', [''],['bad scope']]:
            r=m.run_probe('oc_test',self.fake(scopes));self.assertFalse(r['ok']);self.assertIn('P0-14_missing_scope_error_has_missing_scopes',r['failed_checks']);self.assertIn('怎么解决',r['notice'])
        self.assertTrue(m.run_probe('oc_test',self.fake(['calendar:calendar:readonly']))['ok'])
    def test_receipt_symlink_and_unsafe_permissions_refused(self):
        m=module('safe_bot_writer',TESTS/'hostd_probes/bot_api_probe.py')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);real=root/'real';real.mkdir();link=root/'link';link.symlink_to(real,target_is_directory=True)
            with self.assertRaises(OSError):m.write_receipt(link/'receipt.json',{'ok':False})
            unsafe=root/'unsafe.json';unsafe.write_text('unchanged');unsafe.chmod(0o644)
            with self.assertRaises(ValueError):m.write_receipt(unsafe,{'ok':False})
            self.assertEqual(unsafe.read_text(),'unchanged')
    def test_main_fakeable_protected_fixture_no_implicit_home(self):
        m=module('safe_bot_main',TESTS/'hostd_probes/bot_api_probe.py')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'groups.json';p.write_text('{"hostd-test-群X":"oc_test"}');p.chmod(0o600)
            with mock.patch.object(m,'fc',self.fake(['calendar:calendar:readonly'])):
                self.assertEqual(m.main(['--groups',str(p),'--out',str(Path(d)/'receipt.json')]),0)


class BotMemberEvidence(unittest.TestCase):
    fake=BotProbe.fake
    # Only this explicit fake can service probe operations. No profile or subprocess access.
    def setUp(self):
        self.probe=module('safe_bot_member_evidence',TESTS/'hostd_probes/bot_api_probe.py')
        self.calls=[]
        self.initial=self.roster()
        self.after=self.roster()
        self.posted=False
        self.identity_changed=False
        self.client=self.fake(['calendar:calendar:readonly'])
        fallback=self.client.lark.side_effect
        def call(name,*args):
            self.calls.append((name,args))
            if '+chat-members-list' in args:
                return json.loads(json.dumps(self.after if self.posted else self.initial))
            if 'POST' in args:
                self.posted=True
            return fallback(name,*args)
        self.client.lark.side_effect=call
        creds=self.client.app_credentials.side_effect
        self.client.app_credentials.side_effect=lambda name: ('cli_changed','offline') if self.identity_changed and self.posted and name=='hostd-test-desk' else creds(name)
    def roster(self):
        return {'ok':True,'data':{'users':[],'user_total':0,'bots':[
            {'app_id':'cli_aa48b41531785bfc','member_id':'ou_a'},
            {'app_id':'cli_aa48b406abf8dbe7','member_id':'ou_b'}],
            'bot_total':2,'has_more':False,'truncations':[]},'meta':{'pagination':{'complete':True}}}
    def rejected(self):
        result=self.probe.run_probe('oc_test',self.client)
        self.assertFalse(result['ok'])
        self.assertTrue(result['failed_checks'])
        self.assertIn('怎么解决',result['notice']);self.assertIn('复制给 AI',result['notice'])
    def test_requires_both_a_and_b(self):
        for missing in [0,1]:
            with self.subTest(missing=missing):
                self.initial=self.roster();self.initial['data']['bots'].pop(missing);self.initial['data']['bot_total']=1
                self.posted=False;self.rejected()
    def test_initial_incomplete_roster_cannot_pass(self):
        for field,value in [('has_more',True),('truncations',['page_limit']),('bot_total',3),('user_total',1)]:
            with self.subTest(field=field):
                self.initial=self.roster();self.initial['data'][field]=value;self.posted=False;self.rejected()
    def test_missing_completeness_fields_cannot_pass(self):
        self.initial['data'].pop('has_more');self.rejected()
    def test_mutation_ack_with_stale_roster_cannot_pass(self):
        self.after['data']['bots'].pop();self.after['data']['bot_total']=1;self.rejected()
    def test_mutation_ack_without_fresh_read_cannot_pass(self):
        self.after={'ok':False};self.rejected()
    def test_postwrite_incomplete_roster_cannot_pass(self):
        self.after['meta']['pagination']['complete']=False;self.rejected()
    def test_postwrite_malformed_roster_cannot_pass(self):
        self.after['data']['bots']=[None];self.rejected()
    def test_postwrite_member_id_missing_cannot_pass(self):
        self.after['data']['bots'][0].pop('member_id');self.rejected()
    def test_profile_changed_after_ack_cannot_pass(self):
        self.identity_changed=True
        with self.assertRaises(ValueError) as caught:self.probe.run_probe('oc_test',self.client)
        self.assertIn('怎么解决',str(caught.exception));self.assertIn('复制给 AI',str(caught.exception))
    def test_success_requires_actual_fresh_same_app_complete_read(self):
        result=self.probe.run_probe('oc_test',self.client);self.assertTrue(result['ok'])
        post=next(i for i,(_,args) in enumerate(self.calls) if 'POST' in args)
        reads=[(i,name,args) for i,(name,args) in enumerate(self.calls) if '+chat-members-list' in args]
        self.assertEqual(len(reads),2);self.assertLess(reads[0][0],post);self.assertGreater(reads[1][0],post)
        for _,name,args in reads:
            self.assertEqual(name,'hostd-test-desk');self.assertIn('--page-limit',args);self.assertEqual(args[args.index('--page-limit')+1],'0')
            self.assertEqual(args[args.index('--chat-id')+1],'oc_test');self.assertEqual(args[args.index('--as')+1],'bot')
        self.assertTrue(result['P0-9_postwrite_members']['complete'])

    def test_postwrite_malformed_pagination_evidence_cannot_pass(self):
        for meta in ['invalid', {'pagination':'invalid'}, {'pagination':{'complete':1}}, {'pagination':{'complete':None}}]:
            with self.subTest(meta=meta):
                self.after=self.roster();self.after['meta']=meta;self.posted=False;self.rejected()
