"""Pure remote-target discovery: real protected catalog, signed wire and bot parser.

All keys are synthetic, every HTTP/CLI transport is an explicit offline fixture.
No high-level verified-record/claim/member response is mocked.
"""
import asyncio
import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_hostd_agent_catalog as fixture
from hostd import agent_catalog
from hostd.bot_clients import BotLarkCli, NODE, CLI_ENTRY
from hostd.join_effects import Nip98Relay
import buzz_feishu_group_sync as gs
import recovery_authority as authority

CHANNEL='00000000-0000-0000-0000-000000000001'
NOW=10000
ORIGIN='https://127.0.0.1:9443'
CHAT='oc_remote'

class Wire:
    def __init__(self,f):
        self.f=f;self.remote_key='3'.zfill(64);self.mirror_key='4'.zfill(64);self.relay_key='5'.zfill(64)
        self.remote_owner=gs._signer_pubkey(self.remote_key);self.mirror=gs._signer_pubkey(self.mirror_key);self.pin=gs._signer_pubkey(self.relay_key)
        self.roles={f.pub:'bot',f.owner:'member',self.remote_owner:'owner',self.mirror:'bot'}
        self.claims=[{'channel':CHANNEL,'chat_ref':gs.chat_ref(CHAT),'claimed_at':900,'heartbeat':NOW}]
        self.app='cli_agent';self.corrupt=False;self.malformed_roster=False;self.extra_events=[];self.http_status=200
        self.calls=[];self.cli_calls=[];self.complete=True;self.bot_present=True
        self.pages={'':{'items':[{'chat_id':CHAT}],'has_more':False}}
        self.revoke=None;self.trace=[];self.roster_time=NOW
    def auth(self,key,owner_key):
        pub=gs._signer_pubkey(key);owner=gs._signer_pubkey(owner_key)
        sig=gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{pub}:'.encode()).digest(),bytes.fromhex(owner_key),bytes(32)).hex()
        return ['auth',owner,'',sig]
    def events(self):
        stamp=json.dumps([self.roles,self.claims,self.app,self.malformed_roster,self.extra_events,self.roster_time],sort_keys=True)
        if getattr(self,'_event_stamp',None)==stamp:return json.loads(json.dumps(self._event_cache))
        out=[gs.sign_event(self.f.key,0,[self.auth(self.f.key,self.f.owner_key)],'{}',NOW),
             gs.sign_event(self.f.owner_key,30177,[['d',self.f.pub]],json.dumps({'feishu':{'app_id':self.app}}),NOW),
             gs.sign_event(self.mirror_key,0,[self.auth(self.mirror_key,self.remote_key)],'{}',NOW),
             gs.sign_event(self.remote_key,30177,[['d',self.mirror]],json.dumps({'feishu':{'mirror':True,'bindings':self.claims}}),NOW)]
        tags=[['d',CHANNEL]]+[['p',pk,'',role] for pk,role in self.roles.items()]
        if self.malformed_roster:tags.append(['p',self.f.pub,'','bot'])
        out.append(gs.sign_event(self.relay_key,39002,tags,'',self.roster_time))
        self._event_stamp=stamp;self._event_cache=out+self.extra_events
        return json.loads(json.dumps(self._event_cache))
    def http(self,url,headers,timeout,*,body=None):
        self.trace.append(('io',threading.get_ident()));self.calls.append((url,headers,body))
        assert url==ORIGIN+'/query'
        signed=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
        assert gs._nip01_event_verified(signed);assert signed['pubkey']==self.f.owner
        authority.exact_tag(signed,'u',url);authority.exact_tag(signed,'method','POST')
        authority.exact_tag(signed,'payload',hashlib.sha256(body).hexdigest())
        rows=[]
        for fil in json.loads(body):
            for ev in self.events():
                if 'kinds' in fil and ev['kind'] not in fil['kinds']:continue
                if 'authors' in fil and ev['pubkey'] not in fil['authors']:continue
                if '#d' in fil and not any(t[:1]==['d'] and len(t)>1 and t[1] in fil['#d'] for t in ev['tags']):continue
                if ev not in rows:rows.append(ev)
        if self.corrupt and rows:rows[0]['sig']='0'*128
        if self.revoke:self.revoke()
        return self.http_status,json.dumps(rows).encode()
    def runner(self,argv,**kwargs):
        self.trace.append(('io',threading.get_ident()));self.cli_calls.append((argv,kwargs))
        assert argv[:2]==[NODE,CLI_ENTRY]
        assert argv[argv.index('--as')+1]=='bot';assert argv[argv.index('--profile')+1]=='local'
        assert kwargs['env']['LARKSUITE_CLI_CONFIG_DIR']==str(self.f.cfg)
        assert 'BUZZ_PRIVATE_KEY' not in kwargs['env']
        if '+chat-members-list' in argv:
            assert argv[argv.index('--chat-id')+1]==CHAT
            kind=argv[argv.index('--member-types')+1]
            data={'has_more':not self.complete,'truncations':[]}
            if kind=='user':data.update(users=[],user_total=0)
            else:data.update(bots=[{'app_id':'cli_agent','member_id':'ou_bot'}] if self.bot_present else [],bot_total=int(self.bot_present))
        elif '/open-apis/im/v1/chats' in argv:
            params=json.loads(argv[argv.index('--params')+1]);data=self.pages[params.get('page_token','')]
        else:raise AssertionError('unexpected offline operation')
        return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data,'meta':{'pagination':{'complete':self.complete}}}),'')

@unittest.skipUnless(fixture.AESGCM is not None,'cryptography needed for genuine protected own-bot catalog; dependency environment must execute all cases')
class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from hostd import remote_target
        self.m=remote_target
        f=fixture.CatalogTests('test_ownbot_metadata_and_protected_hashes_only');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        f.env_text+=f'BUZZ_RELAY_URL={ORIGIN}\n';f.write(f.env,f.env_text)
        self.record=agent_catalog.load(f.path,legacy_join_path=f.legacy).records[0]
        self.w=Wire(f);f.write(f.root/'owner.env',f'BUZZ_PRIVATE_KEY={f.owner_key}\n')
        self.relay=Nip98Relay(ORIGIN,f.root/'owner.env',self.w.pin,http=self.w.http,clock=lambda:NOW)
        self.environment=mock.patch.dict(os.environ,{},clear=True);self.environment.start();self.addCleanup(self.environment.stop)
        self.client=BotLarkCli('cli_agent',f.cfg,f.data,base_env={'HOME':str(f.root),'PATH':'/usr/bin'},runner=self.w.runner)
        self.allowed=True;self.guard_calls=[];self.loop_thread=threading.get_ident()
    def guard(self,record,channel):
        self.w.trace.append(('guard',threading.get_ident()));self.guard_calls.append((record.pubkey,channel,threading.get_ident()))
        return self.allowed
    def resolver(self,**kwargs):
        args=dict(relay=self.relay,clients={'cli_agent':self.client},authorize=self.guard,clock=lambda:NOW);args.update(kwargs)
        return self.m.RemoteTargetResolver(self.f.path,self.f.legacy,**args)
    async def resolve(self,**kwargs):return await self.resolver(**kwargs).resolve(self.record,CHANNEL)
    def pending(self,result):
        self.assertEqual(result.status,'pending');self.assertIsNone(result.target)
        self.assertIn('怎么解决',result.notice);self.assertIn('复制给 AI',result.notice)
        self.assertNotIn(self.f.key,repr(result));self.assertNotIn('NEVER_RETURN_SECRET',repr(result))
    async def test_foreign_owner_target_verified_without_local_binding_or_sync_profile(self):
        before={str(p):p.read_bytes() for p in self.f.root.rglob('*') if p.is_file()}
        result=await self.resolve();self.assertEqual(result.status,'verified');target=result.target
        self.assertEqual((target.agent_pubkey,target.owner_pubkey,target.app_id,target.channel_id,target.chat_id),
                         (self.f.pub,self.f.owner,'cli_agent',CHANNEL,CHAT))
        self.assertEqual(target.chat_ref,gs.chat_ref(CHAT));self.assertEqual(target.mirror_owner_pubkey,self.w.remote_owner)
        self.assertNotEqual(target.mirror_owner_pubkey,target.owner_pubkey);self.assertEqual(target.checked_at,NOW)
        self.assertNotIn(self.f.key,repr(target));self.assertNotIn(self.f.owner_key,repr(target))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.f.root.rglob('*') if p.is_file()})
        with self.assertRaises(dataclasses.FrozenInstanceError):target.chat_id='oc_other'
        self.assertTrue(self.w.cli_calls);self.assertTrue(all('POST' not in argv and 'PATCH' not in argv and 'DELETE' not in argv for argv,_ in self.w.cli_calls))
    async def test_absent_or_false_guard_never_reads_or_authorizes(self):
        for guard in [None,lambda record,channel:False,lambda record,channel:1]:
            self.pending(await self.resolve(authorize=guard))
        self.assertEqual(self.w.calls,[]);self.assertEqual(self.w.cli_calls,[])
    async def test_guard_revoked_during_actual_wire_read_stops_further_io(self):
        self.w.revoke=lambda:setattr(self,'allowed',False)
        self.pending(await self.resolve());self.assertEqual(len(self.w.calls),1);self.assertEqual(self.w.cli_calls,[])
    async def test_guard_runs_on_mainloop_before_and_after_each_awaited_io(self):
        original=self.m.agent_catalog.load
        def reload(*args,**kwargs):self.w.trace.append(('io',threading.get_ident()));return original(*args,**kwargs)
        with mock.patch.object(self.m.agent_catalog,'load',side_effect=reload):result=await self.resolve()
        self.assertEqual(result.status,'verified');self.assertTrue(self.guard_calls)
        self.assertTrue(all(thread==self.loop_thread for _,_,thread in self.guard_calls))
        # Member listing includes two CLI reads in one awaited complete snapshot.
        for i,(kind,thread) in enumerate(self.w.trace):
            if kind=='io':
                left=next((j for j in range(i-1,-1,-1) if self.w.trace[j][0]=='guard'),None)
                right=next((j for j in range(i+1,len(self.w.trace)) if self.w.trace[j][0]=='guard'),None)
                self.assertIsNotNone(left);self.assertIsNotNone(right);self.assertNotEqual(thread,self.loop_thread)
    async def test_current_allowlist_not_stale_catalog_snapshot(self):
        self.f.write(self.f.env,self.f.env_text.replace('BUZZ_ACP_CHANNELS='+CHANNEL,'BUZZ_ACP_CHANNELS='))
        self.pending(await self.resolve());self.assertEqual(self.w.calls,[])
    async def test_catalog_identity_mutation_or_legacy_overlap_pending(self):
        self.f.save([self.f.agent]);self.pending(await self.resolve());self.assertEqual(self.w.calls,[])
    async def test_own_client_identity_and_pinned_paths_required(self):
        wrong=BotLarkCli('cli_other',self.f.cfg,self.f.data,base_env={'HOME':str(self.f.root)},runner=self.w.runner)
        self.pending(await self.resolve(clients={'cli_agent':wrong}));self.assertEqual(self.w.cli_calls,[])
    async def test_reader_owner_mismatch_never_borrows_foreign_owner_key(self):
        self.f.write(self.f.root/'foreign.env',f'BUZZ_PRIVATE_KEY={self.w.remote_key}\n')
        relay=Nip98Relay(ORIGIN,self.f.root/'foreign.env',self.w.pin,http=self.w.http,clock=lambda:NOW)
        self.pending(await self.resolve(relay=relay));self.assertEqual(self.w.calls,[])
    async def test_signed_owner_policy_wrong_app_or_bad_signature_pending(self):
        self.w.app='cli_other';self.pending(await self.resolve());self.w.app='cli_agent';self.w.corrupt=True;self.pending(await self.resolve())
    async def test_pinned_roster_bot_and_foreign_mirror_owner_roles_required(self):
        for pk,role in [(self.f.pub,'member'),(self.w.mirror,'member'),(self.w.remote_owner,'member')]:
            old=self.w.roles[pk];self.w.roles[pk]=role
            with self.subTest(pk=pk):self.pending(await self.resolve())
            self.w.roles[pk]=old
        self.w.malformed_roster=True;self.pending(await self.resolve())
    async def test_signed_roster_from_future_not_current_membership_proof(self):
        self.w.roster_time=NOW+gs.RELAY_CLOCK_SKEW_SECONDS+1
        self.pending(await self.resolve())
    async def test_roster_unavailable_stays_pending_no_fallback(self):
        self.w.http_status=403;self.pending(await self.resolve())
    async def test_lease_expired_future_or_malformed_relevant_claim_pending(self):
        for change in [{'heartbeat':1},{'heartbeat':NOW+10000},{'channel':CHANNEL,'chat_ref':'malformed'}]:
            old=list(self.w.claims);self.w.claims=[dict(old[0],**change)]
            with self.subTest(change=change):self.pending(await self.resolve())
            self.w.claims=old
    async def test_nonmirror_policy_ignored_only_if_valid_signature_and_shape(self):
        self.w.extra_events=[gs.sign_event(self.w.remote_key,30177,[['d','a'*64]],json.dumps({'respond_to':'anyone'}),NOW)]
        self.assertEqual((await self.resolve()).status,'verified')
        self.w.extra_events[0]['sig']='0'*128;self.pending(await self.resolve())
    async def test_winning_claim_selected_instead_of_rejecting_all_matching_refs(self):
        key='6'.zfill(64);mirror=gs._signer_pubkey(key);self.w.roles[mirror]='bot'
        profile=gs.sign_event(key,0,[self.w.auth(key,self.w.remote_key)],'{}',NOW)
        claim=dict(self.w.claims[0],claimed_at=950)
        self.w.extra_events=[profile,gs.sign_event(self.w.remote_key,30177,[['d',mirror]],json.dumps({'feishu':{'mirror':True,'bindings':[claim]}}),NOW)]
        result=await self.resolve();self.assertEqual(result.status,'verified');self.assertEqual(result.target.mirror_pubkey,self.w.mirror)
        claim['claimed_at']=850
        self.w.extra_events[1]=gs.sign_event(self.w.remote_key,30177,[['d',mirror]],json.dumps({'feishu':{'mirror':True,'bindings':[claim]}}),NOW)
        result=await self.resolve();self.assertEqual(result.status,'verified');self.assertEqual(result.target.mirror_pubkey,mirror)
    async def test_complete_chat_pages_and_full_member_readback_required(self):
        self.w.pages={'':{'items':[],'has_more':True,'page_token':'p2'},'p2':{'items':[{'chat_id':CHAT}],'has_more':False}}
        self.assertEqual((await self.resolve()).status,'verified')
        self.w.complete=False;self.pending(await self.resolve())
    async def test_repeated_page_token_or_no_canonical_chat_match_pending(self):
        self.w.pages={'':{'items':[],'has_more':True,'page_token':'p2'},'p2':{'items':[],'has_more':True,'page_token':'p2'}}
        self.pending(await self.resolve());self.w.pages={'':{'items':[{'chat_id':'oc_unrelated'}],'has_more':False}};self.pending(await self.resolve())
    async def test_own_bot_removed_and_guard_revoked_before_final_target_pending(self):
        self.w.bot_present=False;self.pending(await self.resolve())
        self.w.bot_present=True
        original=self.w.runner
        def revoke_after_read(argv,**kwargs):
            result=original(argv,**kwargs)
            if '+chat-members-list' in argv:self.allowed=False
            return result
        self.client.runner=revoke_after_read;self.pending(await self.resolve())

if __name__=='__main__':unittest.main()
