"""Read-only remote mapping via actual own-agent wire and own-bot CLI adapters."""
import base64
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import buzz_feishu_group_sync as gs
import recovery_authority as authority
from hostd.agent_signed_reads import OwnAgentReader
from hostd.bot_clients import BotLarkCli,NODE,CLI_ENTRY
from hostd.delivery_mapping import message_card,DeliveryMapping
from hostd.remote_target import RemoteTarget

KEY='1'.zfill(64);OWNER_KEY='2'.zfill(64);RELAY_KEY='3'.zfill(64)
FOREIGN_OWNER_KEY='4'.zfill(64);MIRROR_KEY='5'.zfill(64);HUMAN_KEY='6'.zfill(64)
PUB,OWNER,PIN,FOREIGN_OWNER,MIRROR,HUMAN=map(gs._signer_pubkey,(KEY,OWNER_KEY,RELAY_KEY,FOREIGN_OWNER_KEY,MIRROR_KEY,HUMAN_KEY))
CHANNEL='00000000-0000-0000-0000-000000000001';ORIGIN='https://127.0.0.1:9443';CHAT='oc_foreign';APP='cli_own_b';NOW=10000


def auth(key,owner_key,conditions=''):
    pub=gs._signer_pubkey(key);owner=gs._signer_pubkey(owner_key)
    digest=hashlib.sha256(f'nostr:agent-auth:{pub}:{conditions}'.encode()).digest()
    return ['auth',owner,conditions,gs.sync.nk.schnorr_sign(digest,bytes.fromhex(owner_key),bytes(32)).hex()]


class RemoteMapping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.cfg=self.root/'config';self.data=self.root/'data'
        self.cfg.mkdir(mode=0o700);self.data.mkdir(mode=0o700)
        profile=self.cfg/'config.json';profile.write_text(json.dumps({'apps':[{'appId':APP,'name':'local'}]}));profile.chmod(0o600)
        self.env=self.root/'agent.env';self.env.write_text('BUZZ_PRIVATE_KEY='+KEY+'\nBUZZ_AUTH_TAG='+json.dumps(auth(KEY,OWNER_KEY),separators=(',',':'))+'\nBUZZ_RELAY_URL='+ORIGIN+'\n');self.env.chmod(0o600)
        self.roles={PUB:'bot',MIRROR:'bot',OWNER:'member',FOREIGN_OWNER:'owner',HUMAN:'member'}
        self.claims=[{'channel':CHANNEL,'chat_ref':gs.chat_ref(CHAT),'claimed_at':NOW-100,'heartbeat':NOW}]
        self.policy_app=APP;self.mirror_conditions='';self.mirror_policy=True;self.bad_sig=False;self.status=200
        self.events=[];self.messages={};self.wire_calls=[];self.cli_calls=[];self.incomplete=False;self.history_extra=[]
        self.directory=self.metadata()
        claim=next(e for e in self.directory if e['kind']==30177 and e['pubkey']==FOREIGN_OWNER)
        self.target=RemoteTarget(PUB,OWNER,APP,CHANNEL,CHAT,gs.chat_ref(CHAT),ORIGIN,MIRROR,FOREIGN_OWNER,claim['id'],NOW-100,NOW,NOW)
        self.reader=OwnAgentReader(SimpleNamespace(pubkey=PUB,owner_pubkey=OWNER,env_file=str(self.env)),origin=ORIGIN,relay_pubkey=PIN,trusted_relays=(ORIGIN,),clock=lambda:NOW,http=self.http)
        self.bot=BotLarkCli(APP,self.cfg,self.data,base_env={'HOME':str(self.root),'PATH':'/usr/bin'},runner=self.runner)

    def metadata(self):
        body={'feishu':{'mirror':self.mirror_policy,'bindings':self.claims}}
        return [gs.sign_event(KEY,0,[auth(KEY,OWNER_KEY)],'{}',NOW),
            gs.sign_event(OWNER_KEY,30177,[['d',PUB]],json.dumps({'feishu':{'app_id':self.policy_app}}),NOW),
            gs.sign_event(MIRROR_KEY,0,[auth(MIRROR_KEY,FOREIGN_OWNER_KEY,self.mirror_conditions)],'{}',NOW),
            gs.sign_event(FOREIGN_OWNER_KEY,30177,[['d',MIRROR]],json.dumps(body),NOW),
            gs.sign_event(RELAY_KEY,39002,[['d',CHANNEL]]+[['p',pk,'',role] for pk,role in self.roles.items()],'',NOW)]

    def http(self,url,headers,timeout,*,body=None):
        self.wire_calls.append((url,headers,body))
        self.assertEqual(url,ORIGIN+'/query')
        signed=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
        self.assertTrue(gs._nip01_event_verified(signed));self.assertEqual(signed['pubkey'],PUB)
        authority.exact_tag(signed,'u',url);authority.exact_tag(signed,'method','POST');authority.exact_tag(signed,'payload',hashlib.sha256(body).hexdigest())
        self.assertEqual(json.loads(headers['x-auth-tag']),auth(KEY,OWNER_KEY))
        rows=[]
        for f in json.loads(body):
            for e in self.directory+self.events:
                if 'kinds' in f and e['kind'] not in f['kinds']:continue
                if 'authors' in f and e['pubkey'] not in f['authors']:continue
                if 'ids' in f and e['id'] not in f['ids']:continue
                if any(k.startswith('#') and len(k)==2 and not any(len(t)>1 and t[0]==k[1:] and t[1] in v for t in e['tags']) for k,v in f.items()):continue
                rows.append(e)
        rows=json.loads(json.dumps(rows))
        if self.bad_sig and rows:rows[0]['sig']='0'*128
        return self.status,json.dumps(rows).encode()

    def runner(self,argv,**kwargs):
        self.cli_calls.append((argv,kwargs))
        self.assertEqual(argv[:2],[NODE,CLI_ENTRY]);self.assertEqual(argv[argv.index('--as')+1],'bot')
        self.assertEqual(argv[argv.index('--profile')+1],'local')
        self.assertEqual(kwargs['env']['LARKSUITE_CLI_CONFIG_DIR'],str(self.cfg))
        self.assertNotIn('BUZZ_PRIVATE_KEY',kwargs['env'])
        if '+chat-messages-list' in argv:
            self.assertEqual(argv[argv.index('--chat-id')+1],CHAT)
            self.assertIn('--page-limit',argv);self.assertLessEqual(int(argv[argv.index('--page-limit')+1]),150)
            data={'messages':list(self.messages.values())+self.history_extra,'has_more':self.incomplete}
        else:
            self.assertIn('GET',argv)
            endpoint=next(x for x in argv if x.startswith('/open-apis/im/v1/messages/'))
            mid=endpoint.rsplit('/',1)[-1]
            data={'items':[self.messages[mid]] if mid in self.messages else []}
        payload={'ok':True,'identity':'bot','data':data,'meta':{'pagination':{'complete':not self.incomplete}}}
        return subprocess.CompletedProcess(argv,0,json.dumps(payload),'')

    def module(self):return importlib.import_module('hostd.remote_mapping')
    def context(self,**kwargs):return self.module().RemoteMappingContext(self.target,reader=self.reader,bot_client=self.bot,clock=lambda:NOW,**kwargs)
    def event(self,*,key=KEY,tags=None):
        e=gs.sign_event(key,9,[['h',CHANNEL]]+(tags or []),'SYNTHETIC_BODY_DO_NOT_CACHE',NOW);self.events.append(e);return e
    def card(self,event,mid='om_root',*,root=None,app=APP,link_base=ORIGIN):
        row={'message_id':mid,'chat_id':CHAT,'msg_type':'interactive','sender':{'sender_type':'app','id_type':'app_id','id':app},'body':{'content':message_card(event,'Synthetic','SYNTHETIC_BODY_DO_NOT_CACHE',link_base,CHANNEL)}}
        if root:row['root_id']=root
        self.messages[mid]=row;return row
    def mirrored(self,mid='om_human',*,root=None,tags=None):
        root=root or mid
        e=self.event(key=MIRROR_KEY,tags=[['feishu',mid],['feishu-root',root],['feishu-author',HUMAN]]+(tags or []))
        self.messages[mid]={'message_id':mid,'chat_id':CHAT,'root_id':root,'msg_type':'text','sender':{'sender_type':'user','id_type':'union_id','id':'on_synthetic'},'body':{'content':json.dumps({'text':'SYNTHETIC_BODY_DO_NOT_CACHE'})}}
        return e
    def pending(self,result):
        self.assertEqual(result.status,'pending');self.assertIsNone(result.mapping)
        self.assertIn('怎么解决',result.notice);self.assertIn('复制给 AI',result.notice)
        self.assertNotIn('SYNTHETIC_BODY',str(result));self.assertNotIn(KEY,str(result))

    async def test_own_agent_card_resolves_by_event_and_message_actual_adapters(self):
        e=self.event();self.card(e);ctx=self.context()
        a=await ctx.resolve_buzz(e['id']);b=await ctx.resolve_feishu('om_root')
        self.assertEqual(a.status,'verified');self.assertEqual(a.mapping,DeliveryMapping(e['id'],'om_root','om_root',None,'b2f',APP));self.assertEqual(a.mapping,b.mapping)
        self.assertFalse(a.readback()['live_verified'])
        self.assertNotIn('SYNTHETIC_BODY',repr(ctx))

    async def test_signed_winner_mirror_f2b_metadata_requires_actual_message_readback(self):
        e=self.mirrored();ctx=self.context()
        result=await ctx.resolve_buzz(e['id']);self.assertEqual(result.status,'verified')
        self.assertEqual(result.mapping,DeliveryMapping(e['id'],'om_human','om_human',None,'f2b'))
        self.assertFalse(result.readback()['live_verified']);self.assertNotIn('human_pubkey',result.readback())
        self.assertFalse(any('+chat-messages-list' in a for a,_ in self.cli_calls))

    async def test_mirror_mapping_ignores_unindexed_multiletter_filter(self):
        original=self.mirrored()
        for i in range(4):self.mirrored('om_new'+str(i))
        ctx=self.context()
        for result in (await ctx.resolve_buzz(original['id']),await ctx.resolve_feishu('om_human')):
            self.assertEqual(result.status,'verified')
            self.assertEqual(result.mapping.event_id,original['id'])
        filters=[f for _,_,body in self.wire_calls for f in json.loads(body)]
        self.assertFalse(any('#feishu' in f for f in filters))
        self.assertTrue(any(f.get('ids')==[original['id']] for f in filters))

    async def test_agent_reply_root_recursion_matches_actual_root_and_public_footer(self):
        root=self.event();self.card(root)
        reply=self.event(tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']]);self.card(reply,'om_reply',root='om_root')
        result=await self.context().resolve_feishu('om_reply')
        self.assertEqual(result.status,'verified');self.assertEqual(result.mapping.feishu_root,'om_root');self.assertEqual(result.mapping.buzz_root,root['id'])

    async def test_mirror_reply_root_recursion_without_human_authorization(self):
        root=self.mirrored();reply=self.mirrored('om_reply',root='om_human',tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']])
        result=await self.context().resolve_buzz(reply['id']);self.assertEqual(result.status,'verified')
        self.assertEqual(result.mapping.buzz_root,root['id'])

    async def test_human_b2f_without_trusted_sync_app_is_pending(self):
        e=self.event(key=HUMAN_KEY);self.card(e,app='cli_foreign_sync')
        self.pending(await self.context().resolve_buzz(e['id']))

    async def test_wrong_app_chat_deleted_and_missing_message_fail_closed(self):
        e=self.event();row=self.card(e)
        for field,value in (('chat_id','oc_wrong'),('deleted',True),('sender',{'sender_type':'app','id_type':'app_id','id':'cli_wrong'})):
            original=row.get(field);row[field]=value;self.pending(await self.context().resolve_feishu('om_root'))
            if original is None:row.pop(field)
            else:row[field]=original
        self.messages.clear();self.pending(await self.context().resolve_buzz(e['id']))

    async def test_bad_signature_wrong_channel_missing_or_duplicate_event_pending(self):
        e=self.event();self.card(e);self.bad_sig=True;self.pending(await self.context().resolve_buzz(e['id']));self.bad_sig=False
        self.events.append(e);self.pending(await self.context().resolve_buzz(e['id']));self.events=[e]
        other=gs.sign_event(KEY,9,[['h','00000000-0000-0000-0000-000000000002']],'synthetic',NOW);self.events=[other]
        self.pending(await self.context().resolve_buzz(other['id']));self.pending(await self.context().resolve_buzz('a'*64))

    async def test_raw_flattened_note_a_footer_is_valid_but_duplicate_or_wrong_link_is_not(self):
        e=self.event();row=self.card(e);doc=json.loads(row['body']['content']);link=gs.open_link(ORIGIN,e['id'],CHANNEL,None)
        doc['elements'][-1]={'tag':'note','elements':[{'tag':'a','text':gs.CARD_OPEN_TEXT,'href':link}]};row['body']['content']=json.dumps(doc)
        self.assertEqual((await self.context().resolve_feishu('om_root')).status,'verified')
        doc['elements'][-1]['elements'].append(dict(doc['elements'][-1]['elements'][0]));row['body']['content']=json.dumps(doc)
        self.pending(await self.context().resolve_feishu('om_root'))
        doc['elements'][-1]['elements']= [{'tag':'a','text':gs.CARD_OPEN_TEXT,'href':link.replace(ORIGIN,'https://evil.test')}];row['body']['content']=json.dumps(doc)
        self.pending(await self.context().resolve_feishu('om_root'))

    async def test_history_incomplete_or_conflicting_copies_never_choose_one(self):
        e=self.event();self.card(e);self.incomplete=True;self.pending(await self.context().resolve_buzz(e['id']));self.incomplete=False
        self.card(e,'om_duplicate');self.pending(await self.context().resolve_buzz(e['id']))

    async def test_root_cycle_missing_and_mismatched_thread_are_pending(self):
        e=self.event();row=self.card(e);row['root_id']='om_root';self.assertEqual((await self.context().resolve_feishu('om_root')).status,'verified')
        row['root_id']='om_missing';self.pending(await self.context().resolve_feishu('om_root'))
        other=self.event(tags=[['p',PUB]]);self.card(other,'om_other');row['root_id']='om_other';self.messages['om_other']['root_id']='om_root'
        self.pending(await self.context().resolve_feishu('om_root'))

    async def test_depth_more_than_eight_is_bounded_pending(self):
        root=self.mirrored('om_0');previous=root
        for i in range(1,10):
            previous=self.mirrored(f'om_{i}',root=f'om_{i-1}',tags=[['e',root['id'],'','root'],['e',previous['id'],'','reply']])
        self.pending(await self.context().resolve_feishu('om_9'))
        self.assertLessEqual(sum('/open-apis/im/v1/messages/' in ' '.join(a) for a,_ in self.cli_calls),9)

    async def test_changed_owner_policy_roster_or_claim_invalidates_fake_checked_at(self):
        e=self.event();self.card(e);ctx=self.context();self.assertEqual((await ctx.resolve_buzz(e['id'])).status,'verified')
        self.policy_app='cli_wrong';self.directory=self.metadata();self.pending(await ctx.resolve_buzz(e['id']))
        self.policy_app=APP;self.roles[PUB]='member';self.directory=self.metadata();self.pending(await ctx.resolve_buzz(e['id']))
        self.roles[PUB]='bot';self.claims[0]['heartbeat']=1;self.directory=self.metadata();self.pending(await ctx.resolve_buzz(e['id']))
        self.claims[0]['heartbeat']=NOW;self.claims[0]['claimed_at']=NOW-99;self.directory=self.metadata();self.pending(await ctx.resolve_buzz(e['id']))
        self.claims[0]['heartbeat']=NOW;self.claims[0]['claimed_at']=NOW-100;self.directory=self.metadata()
        original_runner=self.runner
        def changing_runner(argv,**kwargs):
            result=original_runner(argv,**kwargs)
            self.claims[0]['claimed_at']=NOW-98;self.directory=self.metadata()
            return result
        self.bot.runner=changing_runner
        self.pending(await ctx.resolve_feishu('om_root'))

    async def test_same_authority_heartbeat_refresh_preserves_mapping_but_cannot_regress(self):
        e=self.event();self.card(e);ctx=self.context()
        self.claims[0]['heartbeat']=NOW+1;self.directory=self.metadata()
        refreshed=next(row for row in self.directory if row['kind']==30177 and row['pubkey']==FOREIGN_OWNER)
        self.assertNotEqual(refreshed['id'],self.target.claim_event_id)
        self.assertEqual((await ctx.resolve_buzz(e['id'])).status,'verified')
        self.assertEqual((await ctx.resolve_feishu('om_root')).status,'verified')
        self.claims[0]['heartbeat']=NOW;self.directory=self.metadata()
        self.pending(await ctx.resolve_feishu('om_root'))

    async def test_same_authority_heartbeat_renewed_during_actual_message_read_is_valid(self):
        e=self.event();self.card(e);ctx=self.context();original_runner=self.runner;changed=[]
        def renewing_runner(argv,**kwargs):
            result=original_runner(argv,**kwargs)
            if not changed and any(x.startswith('/open-apis/im/v1/messages/') for x in argv):
                self.claims[0]['heartbeat']=NOW+1;self.directory=self.metadata();changed.append(True)
            return result
        self.bot.runner=renewing_runner
        result=await ctx.resolve_feishu('om_root')
        self.assertEqual(changed,[True]);self.assertEqual(result.status,'verified')
        self.assertEqual(result.mapping.message_id,'om_root')

    async def test_explicit_public_link_origin_distinct_from_private_relay_resolves_reply(self):
        public='https://buzz-ui.example.test';root=self.event();self.card(root,link_base=public)
        reply=self.event(tags=[['e',root['id'],'','root'],['e',root['id'],'','reply']])
        self.card(reply,'om_reply',root='om_root',link_base=public);ctx=self.context(link_base=public)
        result=await ctx.resolve_feishu('om_reply');self.assertEqual(result.status,'verified')
        self.assertEqual(result.mapping.buzz_root,root['id'])
        self.assertEqual((await ctx.resolve_buzz(reply['id'])).mapping,result.mapping)
        self.assertTrue(self.wire_calls);self.assertTrue(all(url==ORIGIN+'/query' for url,_,_ in self.wire_calls))

    async def test_only_explicit_public_origin_and_canonical_footer_query_allowed(self):
        public='https://buzz-ui.example.test';e=self.event();row=self.card(e,link_base=public);ctx=self.context(link_base=public)
        self.assertEqual((await ctx.resolve_feishu('om_root')).status,'verified')
        doc=json.loads(row['body']['content']);valid=gs.open_link(public,e['id'],CHANNEL,None)
        for link in (valid.replace(public,ORIGIN),valid.replace(public,'https://evil.example.test'),
                     valid+'&extra=1',valid+'&e='+e['id'],valid+'#fragment',valid.replace(public,'https://user@buzz-ui.example.test')):
            with self.subTest(link=link):
                doc['elements'][-1]={'tag':'note','elements':[{'tag':'a','text':gs.CARD_OPEN_TEXT,'href':link}]}
                row['body']['content']=json.dumps(doc);self.pending(await ctx.resolve_feishu('om_root'))

    async def test_invalid_public_link_base_pending_before_transport(self):
        e=self.event();self.card(e)
        for base in ('http://buzz-ui.example.test','https://user@buzz-ui.example.test','https://buzz-ui.example.test?q=1',
                     'https://buzz-ui.example.test#fragment','https://buzz-ui.example.test/path','https://buzz-ui.example.test:99999'):
            with self.subTest(base=base):self.pending(await self.context(link_base=base).resolve_feishu('om_root'))
        self.assertFalse(self.wire_calls);self.assertFalse(self.cli_calls)

    async def test_nonempty_mirror_oa_or_not_declared_mirror_pending(self):
        e=self.mirrored();self.mirror_conditions='kind=9';self.directory=self.metadata();self.pending(await self.context().resolve_buzz(e['id']))
        self.mirror_conditions='';self.mirror_policy=False;self.directory=self.metadata();self.pending(await self.context().resolve_buzz(e['id']))

    async def test_discovered_target_cannot_substitute_wrong_own_reader_or_bot(self):
        e=self.event();self.card(e);self.target=dataclasses.replace(self.target,agent_pubkey=HUMAN)
        self.pending(await self.context().resolve_buzz(e['id']))
        self.target=dataclasses.replace(self.target,agent_pubkey=PUB,app_id='cli_wrong')
        self.pending(await self.context().resolve_buzz(e['id']))

    async def test_unknown_roster_bot_is_not_accepted_as_registered_mirror(self):
        rogue_key='7'.zfill(64);rogue=gs._signer_pubkey(rogue_key);self.roles[rogue]='bot';self.directory=self.metadata()
        e=self.event(key=rogue_key,tags=[['feishu','om_rogue'],['feishu-root','om_rogue']]);self.messages['om_rogue']={'message_id':'om_rogue','chat_id':CHAT,'msg_type':'text','sender':{'sender_type':'user','id_type':'union_id','id':'on_synthetic'},'body':{'content':'{}'}}
        self.pending(await self.context().resolve_buzz(e['id']))

    async def test_403_or_bot_read_exception_pending_without_fallback_or_leaked_exception(self):
        e=self.event();self.card(e);self.status=403;self.pending(await self.context().resolve_buzz(e['id']));self.status=200
        self.messages['om_root']['message_id']='om_wrong';self.pending(await self.context().resolve_feishu('om_root'))

    async def test_bad_ids_rejected_before_actual_io(self):
        ctx=self.context()
        for eid in ('bad','a'*63,'../secrets'):self.pending(await ctx.resolve_buzz(eid))
        for mid in ('bad','../secrets'):self.pending(await ctx.resolve_feishu(mid))
        self.assertFalse(bool(self.wire_calls));self.assertFalse(bool(self.cli_calls))

    async def test_context_does_not_create_local_database_or_foreign_binding(self):
        e=self.event();self.card(e)
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=await self.context().resolve_buzz(e['id']);self.assertEqual(result.status,'verified')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse(hasattr(result,'grant'));self.assertFalse(hasattr(result,'human_pubkey'))
