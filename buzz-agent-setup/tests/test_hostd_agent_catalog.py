"""Actual protected join/profile inputs; no relay or service changes."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd import agent_catalog as catalog
import gitlab_buzz_sync as sync

@unittest.skipUnless(AESGCM, 'protected app secret requires cryptography; dependency environment executes every catalog case')
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.key='1'.zfill(64);self.owner_key='2'.zfill(64)
        self.pub=sync.publisher_pubkey_from_private_key(self.key);self.owner=sync.publisher_pubkey_from_private_key(self.owner_key)
        sig=sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{self.pub}:'.encode()).digest(),bytes.fromhex(self.owner_key),bytes(32)).hex()
        self.env=self.root/'agent.env';self.prompt=self.root/'prompt.md';self.responsible=self.root/'responsible.json'
        self.write(self.prompt,'private prompt');self.write(self.responsible,'{"people_file":"DO_NOT_READ_PEOPLE"}')
        self.env_text=f'BUZZ_PRIVATE_KEY={self.key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_AUTH_TAG=\'{json.dumps(["auth",self.owner,"",sig])}\'\nBUZZ_ACP_CHANNELS=00000000-0000-0000-0000-000000000001\nBUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n'
        self.write(self.env,self.env_text)
        self.cfg=self.root/'config';self.data=self.root/'data';self.cfg.mkdir();(self.data/'lark-cli').mkdir(parents=True)
        self.write(self.cfg/'config.json',json.dumps({'apps':[{'appId':'cli_agent','name':'local','appSecret':'unused'}]}))
        key=b'k'*32;nonce=b'n'*12
        self.write(self.data/'lark-cli'/'master.key',key)
        self.write(self.data/'lark-cli'/'appsecret_cli_agent.enc',nonce+AESGCM(key).encrypt(nonce,b'NEVER_RETURN_SECRET',None))
        self.agent={'name':'local','env_file':str(self.env),'unit':'buzz-local-local.service','capabilities':{'summary':'test','repos':[]},'feishu':{'app_id':'cli_agent','lark_config_dir':str(self.cfg),'lark_data_dir':str(self.data)}}
        self.doc={'version':1,'owner_pubkey':self.owner,'buzz':{'cli_path':'/tmp/buzz','cli_sha256':'a'*64},'state_dir':str(self.root/'state'),'lark_cli':'/tmp/lark-cli','agents':[self.agent]}
        self.path=self.root/'catalog.json';self.legacy=self.root/'legacy.json';self.save()
    def write(self,p,v):
        p.write_bytes(v if isinstance(v,bytes) else v.encode());p.chmod(0o600)
    def save(self,legacy=None):
        self.write(self.path,json.dumps(self.doc));self.write(self.legacy,json.dumps(dict(self.doc,agents=[] if legacy is None else legacy)))
    def load(self):return catalog.load(self.path,legacy_join_path=self.legacy)
    def test_ownbot_metadata_and_protected_hashes_only(self):
        result=self.load();r=result.records[0]
        self.assertEqual(r.status,'own_bot_verified');self.assertEqual(r.pubkey,self.pub);self.assertEqual(r.owner_pubkey,self.owner)
        self.assertEqual(r.prompt_file,self.prompt);self.assertEqual(r.responsible_config,self.responsible)
        self.assertEqual(r.env_file,self.env);self.assertEqual(r.unit,self.agent['unit']);self.assertEqual(r.app_id,'cli_agent')
        self.assertEqual(r.lark_config_dir,self.cfg);self.assertEqual(r.lark_data_dir,self.data)
        self.assertEqual(result.catalog_sha256,hashlib.sha256(self.path.read_bytes()).hexdigest())
        for text in [repr(r),repr(dataclasses.asdict(r)),repr(result)]:
            self.assertNotIn(self.key,text);self.assertNotIn('NEVER_RETURN_SECRET',text);self.assertNotIn('private prompt',text)
        self.assertTrue(r.requires_fresh_relay)
    def test_each_legacy_identity_collision_blocks(self):
        for field in ['name','env_file','unit','app_id']:
            a=json.loads(json.dumps(self.agent));a.update(name='other',env_file='/tmp/other.env',unit='other.service');a['feishu']['app_id']='cli_other'
            if field=='app_id':a['feishu'][field]=self.agent['feishu'][field]
            else:a[field]=self.agent[field]
            self.save([a]);self.assertEqual(self.load().records[0].status,'blocked',field)
            self.assertIn('legacy_join_overlap',self.load().records[0].reasons)
    def test_missing_bot_explicit_future_not_borrowed(self):
        self.agent.pop('feishu');self.save([self.agent]);r=self.load().records[0]
        self.assertEqual(r.status,'missing_bot');self.assertIsNone(r.app_id);self.assertIn('future_l6',r.reasons)
    def test_absent_legacy_evidence_fails_closed(self):
        self.legacy.unlink()
        with self.assertRaises(catalog.CatalogError) as e:self.load()
        self.assertIn('怎么解决',str(e.exception))
    def test_owner_and_signed_auth_must_match(self):
        for text in [self.env_text.replace(self.owner,'a'*64),self.env_text.replace('"auth"','"wrong"'),self.env_text.replace('"",','"conditional",')]:
            self.write(self.env,text);self.assertEqual(self.load().records[0].status,'blocked')
    def test_invalid_signature_never_becomes_verified(self):
        import re
        self.write(self.env,re.sub(r'[0-9a-f]{128}', '0'*128,self.env_text))
        r=self.load().records[0];self.assertEqual(r.status,'blocked');self.assertIn('agent_identity_invalid',r.reasons)
    def test_profile_duplicate_or_name_collision_or_wrong_app(self):
        for apps in [[{'appId':'cli_other'}],[{'appId':'cli_agent'},{'appId':'cli_agent'}],[{'appId':'cli_agent','name':'local'},{'appId':'cli_other','name':'local'}]]:
            self.write(self.cfg/'config.json',json.dumps({'apps':apps}));r=self.load().records[0]
            self.assertEqual(r.status,'blocked');self.assertIn('bot_profile_invalid',r.reasons)
    def test_broken_secret_is_blocked_without_exception_text(self):
        self.write(self.data/'lark-cli'/'master.key',b'NEVER_RETURN_SECRET');r=self.load().records[0]
        self.assertEqual(r.status,'blocked');self.assertNotIn('NEVER_RETURN_SECRET',repr(r))
    def test_symlink_and_permissions_fail_closed(self):
        self.env.chmod(0o644);self.assertEqual(self.load().records[0].status,'blocked');self.env.chmod(0o600)
        target=self.root/'real';self.cfg.rename(target);self.cfg.symlink_to(target,target_is_directory=True)
        self.assertEqual(self.load().records[0].status,'blocked')
    def test_duplicate_catalog_identity_blocks_both(self):
        a=json.loads(json.dumps(self.agent));a['name']='another';a['feishu']={'app_id':'cli_another','lark_config_dir':str(self.root/'another-config'),'lark_data_dir':str(self.root/'another-data')};self.doc['agents'].append(a);self.save()
        self.assertEqual([r.status for r in self.load().records],['blocked','blocked'])
    def test_no_guessed_prompt_or_responsible(self):
        self.write(self.env,'\n'.join(line for line in self.env_text.splitlines() if not line.startswith(('BUZZ_ACP_SYSTEM_PROMPT_FILE=','BUZZ_RESPONSIBLE_CONFIG='))))
        r=self.load().records[0];self.assertIsNone(r.prompt_file);self.assertIsNone(r.responsible_config)
        self.assertEqual(r.status,'blocked');self.assertIn('runtime_paths_missing',r.reasons)
    def test_unknown_fields_or_path_injection_fail_with_fixed_notice(self):
        self.agent['unit']='local.service;NEVER_RETURN_SECRET';self.save()
        with self.assertRaises(catalog.CatalogError) as e:self.load()
        self.assertNotIn('NEVER_RETURN_SECRET',str(e.exception))
        self.agent['unit']='local.service';self.doc['invented_manifest']='NEVER_RETURN_SECRET';self.save()
        with self.assertRaises(catalog.CatalogError):self.load()

if __name__=='__main__':unittest.main()
