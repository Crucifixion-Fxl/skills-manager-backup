"""Offline low-boundary MSG001/002 contracts, never physical L3 acceptance.

Actual signatures/card/BotLark parsers and actual runtime owner objects. Only
CLI/unit/kernel/Popen boundaries are fixtures. Native wake remains pending.
"""
import asyncio
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS));sys.path.insert(0,str(TESTS.parent/'scripts'))
import test_hostd_l3_agent_launcher as launcher_fixture
from hostd import delivery_mapping as mapping
from hostd import bot_clients
from hostd.cli_runtime import RuntimePaths,RuntimePathError
from hostd.store import Store,BindingRecord
import buzz_feishu_group_sync as gs

NOW=10000
RUN='1'*32
CHANNEL=launcher_fixture.CHANNEL
USER_APP='cli_a940faa4ec381bc4'
USER_ID='ou_755158d120e03b0c18dd4a9334bc3aad'
SYNC_APP='cli_aa48bbeeba38dbcf'
AGENT_APP='cli_aa48b41531785bfc'
MIRROR_KEY='3'.zfill(64)
HUMAN_KEY='4'.zfill(64)
MIRROR=gs._signer_pubkey(MIRROR_KEY)
HUMAN=gs._signer_pubkey(HUMAN_KEY)
MID='om_l3human001'
CARD='om_l3reply002'
LINK='https://127.0.0.1:9443'
TEXT1='SYNTHETIC_MSG001-'+RUN
TEXT2='SYNTHETIC_MSG002-'+RUN


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def signed(key,content,tags,stamp=NOW):return gs.sign_event(key,9,tags,content,stamp)
def wire_root(image_hash):
    return signed(MIRROR_KEY,TEXT1,[['h',CHANNEL],['feishu',MID],['feishu-root',MID],
        ['p',launcher_fixture.PUB],['imeta','url '+LINK+'/media/'+image_hash+'.png','m image/png','x '+image_hash]])
def wire_reply(root):return signed(HUMAN_KEY,TEXT2,[['h',CHANNEL],['e',root['id'],'','root'],['e',root['id'],'','reply']])
def wire_card(root,event):
    # Observed native Feishu GET flattening, through the real ADR27 parser.
    document=json.loads(mapping.message_card(event,'Synthetic human',TEXT2,LINK,CHANNEL))
    document['elements'][-1]={'tag':'note','elements':[{'tag':'a','text':gs.CARD_OPEN_TEXT,
        'href':gs.open_link(LINK,event['id'],CHANNEL,gs.buzz_thread_root(event))}]}
    return {'message_id':CARD,'chat_id':'oc_testx','root_id':MID,'deleted':False,'msg_type':'interactive',
        'sender':{'sender_type':'app','id_type':'app_id','id':SYNC_APP},
        'body':{'content':json.dumps(document)}}


class BoundaryWire:
    """Only subprocess response envelopes/files, never verified proof DTOs."""
    def __init__(self,test):
        self.test=test;self.calls=[];self.writes=[];self.identity=USER_ID;self.more=False
        self.events=[test.root_event];self.card=wire_card(test.root_event,test.reply_event)
        self.absent=False;self.timeout_write=False;self.hold_write=False;self.entered=None;self.release=None
        self.before_write=None;self.member_app=AGENT_APP
        self.chat={'chat_id':'oc_testx','owner_id':USER_ID,'name':'synthetic'}
        self.shortcut_unavailable=False
    def envelope(self,argv,data,identity='bot',meta=None):
        value={'ok':True,'identity':identity,'data':data}
        if meta is not None:value['meta']=meta
        return subprocess.CompletedProcess(argv,0,json.dumps(value),'')
    def __call__(self,argv,**kw):
        self.calls.append((tuple(argv),kw))
        if argv[0]==str(self.test.base.buzz):
            if argv[1:3]==['users','get']:return subprocess.CompletedProcess(argv,0,json.dumps({'pubkey':HUMAN}),'')
            if argv[1:3]==['messages','send']:
                self.mutation(argv,kw,'msg002');self.events.append(self.test.reply_event)
                self.test.ack_ledger()
                return subprocess.CompletedProcess(argv,0,json.dumps({'event_id':self.test.reply_event['id']}),'')
            if argv[1:3] in (['messages','get'],['messages','thread']):
                return subprocess.CompletedProcess(argv,0,json.dumps([] if self.absent else self.events),'')
            if argv[1:3]==['media','get']:
                dest=Path(argv[argv.index('-o')+1]);dest.write_bytes(self.test.image.read_bytes());dest.chmod(0o600)
                return subprocess.CompletedProcess(argv,0,'','')
            raise AssertionError('unexpected synthetic Buzz boundary method')
        self.test.assertEqual(argv[:2],[self.test.base.m.NODE,self.test.base.m.CLI_ENTRY])
        profile=argv[argv.index('--profile')+1]
        args=argv[2:]
        if args[:2]==['auth','status']:
            self.test.assertEqual(profile,'jchen-personal')
            self.test.assertNotIn('--as',argv)
            return subprocess.CompletedProcess(argv,0,json.dumps({'appId':USER_APP,'identities':{'user':{'openId':self.identity}}}),'')
        role=argv[argv.index('--as')+1]
        if role=='user':self.test.assertEqual(profile,'jchen-personal')
        if args[:2]==['im','+messages-send']:
            self.mutation(argv,kw,'msg001')
            return self.envelope(argv,{'message_id':MID},'user')
        if args[:2]==['im','+chat-members-list']:
            kinds=argv[argv.index('--member-types')+1]
            rows=({'users':[{'member_id':USER_ID,'name':'Synthetic'}],'user_total':1} if kinds=='user'
                else {'bots':[{'app_id':SYNC_APP,'member_id':'ou_syncbot'},{'app_id':self.member_app,'member_id':'ou_agentbot'},{'app_id':'cli_aa48b406abf8dbe7','member_id':'ou_agentbotb'}],'bot_total':3})
            rows.update(has_more=self.more,truncations=[])
            return self.envelope(argv,rows,role)
        if args[:2]==['im','+chat-messages-list']:
            if self.shortcut_unavailable:return subprocess.CompletedProcess(argv,2,'','unsupported shortcut page-limit')
            rows=[] if self.absent else [self.test.user_message,self.card]
            return self.envelope(argv,{'messages':rows,'has_more':self.more},role,{'pagination':{'complete':not self.more}})
        if args[:2]==['im','+threads-messages-list']:
            return self.envelope(argv,{'messages':[] if self.absent else [self.card],'has_more':self.more},role,{'pagination':{'complete':not self.more}})
        if args[:2]==['api','GET']:
            path=args[2]
            if path=='/open-apis/authen/v1/user_info':return self.envelope(argv,{'open_id':self.identity,'union_id':'on_synthetic'},role)
            if path=='/open-apis/application/v6/scopes':
                return self.envelope(argv,{'scopes':[{'scope_name':v,'scope_type':'tenant','grant_status':1} for v in ('im:chat:read','im:chat.members:read','im:message:readonly')]})
            if path=='/open-apis/bot/v3/info':return self.envelope(argv,{'bot':{'app_id':SYNC_APP,'open_id':'ou_syncbot'}})
            if path.startswith('/open-apis/im/v1/chats/'):
                return self.envelope(argv,self.chat,role)
            if path=='/open-apis/im/v1/messages':
                return self.envelope(argv,{'items':[] if self.absent else [self.test.user_message,self.card],'has_more':self.more,'page_token':''},role,{'pagination':{'complete':not self.more}})
            if path.startswith('/open-apis/im/v1/messages/'):
                mid=path.rsplit('/',1)[1];row=dict(self.test.user_message) if mid==MID else self.card
                if mid==MID and role=='bot':
                    kind=json.loads(argv[argv.index('--params')+1])['user_id_type']
                    row['sender']={'sender_type':'user','id_type':kind,'id':'on_synthetic' if kind=='union_id' else 'ou_desk_human'}
                return self.envelope(argv,{'items':[] if self.absent else [row]},role)
        raise AssertionError('unexpected synthetic Feishu boundary method')
    def http(self,url,headers,timeout,body=None):
        import base64
        auth=json.loads(base64.b64decode(headers['Authorization'].removeprefix('Nostr ')))
        self.test.assertTrue(gs._nip01_event_verified(auth))
        self.test.assertEqual(auth['pubkey'],launcher_fixture.OWNER)
        self.test.assertIn(['u',url],auth['tags'])
        if body is not None:
            self.test.assertIn(['method','POST'],auth['tags'])
            self.test.assertIn(['payload',hashlib.sha256(body).hexdigest()],auth['tags'])
            filters=json.loads(body);self.test.assertEqual(len(filters),1)
            rows=[self.test.roster] if filters[0]['kinds']==[39002] else [self.test.profile_event]
            return 200,json.dumps(rows).encode()
        self.test.assertIn(['method','GET'],auth['tags'])
        return 200,json.dumps({'channel':CHANNEL,'people':{},'union_ids':{HUMAN:'on_synthetic',launcher_fixture.OWNER:'on_owner'}}).encode()

    def mutation(self,argv,kw,phase):
        intent=json.loads((self.test.phase_dir/(phase+'.json')).read_text())
        self.test.assertEqual(intent['status'],'unknown')
        self.test.assertEqual(intent['run_id'],RUN)
        self.test.assertTrue(TEXT1 not in ' '.join(argv) and TEXT2 not in ' '.join(argv))
        self.test.assertIsInstance(kw.get('input'),str)
        self.writes.append(phase)
        if self.before_write:self.before_write(phase)
        if self.timeout_write:raise subprocess.TimeoutExpired(argv,1)
        if self.hold_write:
            self.entered.set();self.release.wait(3)


class ScenarioDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.m=importlib.import_module('integration.hostd_l3.scenario_driver')
        # Explicit PreparedRun CLI paths are validated before the wire runner.
        # Missing runtime metadata invalidates every positive/negative case.
        try:
            RuntimePaths(self.m.prepare.NODE,self.m.prepare.CLI_ENTRY).validate()
        except RuntimePathError:
            raise unittest.SkipTest('requires safely validated explicit Node/CLI runtime metadata; skipped offline Scenario contracts are not acceptance') from None
        self.f=launcher_fixture.AgentLauncher();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.base=self.f.base
        self.base.write(self.base.run/'runtime/owner.env','BUZZ_PRIVATE_KEY='+launcher_fixture.OWNER_KEY+'\nBUZZ_RELAY_URL=wss://127.0.0.1:9443\n')
        self.base.cfg['mirror_pubkey']=MIRROR
        self.base.write(self.base.run/'runtime/mirror.env','BUZZ_PRIVATE_KEY='+MIRROR_KEY+'\nBUZZ_RELAY_URL=wss://127.0.0.1:9443\n')
        self.base.write(self.base.config,self.base.cfg)
        self.user_cfg=self.base.run/'profiles/user/config';self.user_data=self.base.run/'profiles/user/data'
        self.user_cfg.mkdir(parents=True,mode=0o700);self.user_cfg.parent.chmod(0o700);self.user_data.mkdir(mode=0o700)
        self.base.write(self.user_cfg/'config.json',{'apps':[{'appId':USER_APP,'name':'jchen-personal'}]})
        self.human_env=self.base.run/'runtime/human.env'
        self.base.write(self.human_env,'BUZZ_PRIVATE_KEY='+HUMAN_KEY+'\nBUZZ_RELAY_URL=wss://127.0.0.1:9443\n')
        self.phase_dir=self.base.run/'runtime/scenario';self.phase_dir.mkdir(mode=0o700)
        self.image=self.phase_dir/'image.png'
        import base64
        self.image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='));self.image.chmod(0o600)
        self.body1=self.phase_dir/'msg001-post.json';self.body2=self.phase_dir/'msg002-text.txt'
        post={'zh_cn':{'title':'','content':[[{'tag':'text','text':TEXT1},{'tag':'at','user_id':'ou_agentbot'},
                                         {'tag':'img','image_key':'img_synthetic'}]]}}
        self.base.write(self.body1,post);self.base.write(self.body2,TEXT2)
        self.manifest=self.phase_dir/'scenario.json'
        self.doc={'version':1,'run_id':RUN,'revision':self.base.doc['revision'],'initial_binding':self.base.doc['initial_binding'],
            'channel_id':CHANNEL,'chat_id':'oc_testx','sync_app_id':SYNC_APP,'agent_app_id':AGENT_APP,'agent_pubkey':launcher_fixture.PUB,
            'user_config_dir':str(self.user_cfg),'user_data_dir':str(self.user_data),'user_profile':'jchen-personal',
            'human_env_file':str(self.human_env),'human_pubkey':HUMAN,'phase_dir':str(self.phase_dir),
            'msg001_content_file':str(self.body1),'msg002_content_file':str(self.body2),
            'image_file':str(self.image),'image_key':'img_synthetic','image_sha256':sha(self.image),'link_base':LINK}
        self.save();self.f.prepared=self.base.check();self.prepared=self.f.prepared
        self.root_event=wire_root(sha(self.image));self.reply_event=wire_reply(self.root_event)
        self.user_message={'message_id':MID,'chat_id':'oc_testx','root_id':MID,'msg_type':'post','deleted':False,
            'create_time':str(NOW*1000),'sender':{'sender_type':'user','id_type':'open_id','id':USER_ID},
            'body':{'content':self.body1.read_text()}}
        self.profile_event=gs.sign_event(HUMAN_KEY,0,[],json.dumps({'display_name':'Signed Synthetic'}),NOW-1)
        relay_key='8'.zfill(64);self.base.rt['relay_pubkey']=gs._signer_pubkey(relay_key)
        self.base.write(self.base.onboarding,self.base.rt)
        self.roster=gs.sign_event(relay_key,39002,[['d',CHANNEL],['p',launcher_fixture.OWNER,'','owner'],
            ['p',HUMAN,'','member'],['p',MIRROR,'','bot'],['p',launcher_fixture.PUB,'','bot']],'',NOW)
        native=bot_clients.BotLarkCli._normalize_message(self.user_message)
        projected=gs.route_feishu_message(native,open_id_to_pubkey={'on_synthetic':HUMAN},
            bot_member_to_pubkey={},channel_members={HUMAN,launcher_fixture.PUB},names={HUMAN:'Signed Synthetic'},
            now=__import__('datetime').datetime.fromtimestamp(NOW,__import__('datetime').timezone.utc),resolve_id=lambda ident:'on_synthetic' if ident==USER_ID else '')
        self.root_event=signed(MIRROR_KEY,projected.text,self.root_event['tags']);self.reply_event=wire_reply(self.root_event)
        self.f.prepared=self.base.check();self.prepared=self.f.prepared
        self.wire=BoundaryWire(self);self.hostd=None;self.agent=None;self.store=None
    async def asyncTearDown(self):
        if self.store:self.store.close()
        if self.hostd:self.hostd.stop()
        if self.agent:await self.agent.stop()
    def save(self):self.base.write(self.manifest,self.doc);self.pin=sha(self.manifest)
    def check(self):return self.m.ScenarioPlan.check(self.prepared,self.manifest,self.pin,human_identity_pin=HUMAN)
    async def driver(self,plan=None):
        patch=mock.patch.object(gs,'_http_get',self.wire.http);patch.start();self.addCleanup(patch.stop)
        plan=plan or self.check()
        if self.agent is None:
            self.agent=self.f.owned();await self.agent.start();self.f.backend.ready();await self.agent.refresh()
            self.hostd=self.base.m.OwnedHostd(self.prepared)
            native_popen=subprocess.Popen
            def harmless(argv,**kw):
                self.assertEqual(argv,self.prepared.argv())
                return native_popen([sys.executable,'-c','import time;time.sleep(30)'],**kw)
            with mock.patch.object(self.base.m.subprocess,'Popen',side_effect=harmless):self.hostd.start()
            self.store=Store(self.base.run/'runtime/hostd.sqlite3')
            self.store.reconcile_bindings([BindingRecord(self.doc['initial_binding'],CHANNEL,'oc_testx',SYNC_APP,
                str(self.f.env),str(self.base.config),str(self.base.run/'profiles/0/data'),mirror_pubkey=MIRROR)],now=NOW)
        return self.m.ScenarioDriver(plan,hostd=self.hostd,native_agent=self.agent,runner=self.wire,clock=lambda:NOW,deadline_seconds=.3)
    def ack_ledger(self,status='acked',target=CARD):
        d=self.store.reserve_delivery(self.doc['initial_binding'],self.reply_event['id'],'b2f',source_at=NOW,
            root_id=MID,content_hash=hashlib.sha256(TEXT2.encode()).hexdigest(),now=NOW)
        if status=='acked':self.store.ack_delivery(d.id,target,now=NOW)
        elif status=='unknown':self.store.fail_delivery(d.id,unknown=True,now=NOW)
    async def seed_root(self,driver):
        result=await driver.run_msg001();self.assertEqual(result['status'],'pending')
        self.assertEqual(result['reason'],'wake_witness_pending');return result
    def safe(self,value):
        text=json.dumps(value,ensure_ascii=False)
        self.assertTrue(TEXT1 not in text and TEXT2 not in text and HUMAN_KEY not in text)
        self.assertFalse(value['live_verified'])
    async def test_signed_profile_actual_route_projection_exact_body(self):
        native=dict(self.user_message)
        native['sender']={'sender_type':'user','id_type':'open_id','id':'ou_desk_human'}
        projected=dict(native);projected['sender']={'sender_type':'user','id_type':'union_id','id':'on_synthetic'}
        profile=gs.sign_event(HUMAN_KEY,0,[],json.dumps({'display_name':'Signed Synthetic'}),NOW-1)
        roles={HUMAN:'member',launcher_fixture.PUB:'bot'}
        actual=gs.route_feishu_message(bot_clients.BotLarkCli._normalize_message(native),
            open_id_to_pubkey={'on_synthetic':HUMAN},bot_member_to_pubkey={},channel_members=set(roles),
            names={HUMAN:'Signed Synthetic'},now=__import__('datetime').datetime.fromtimestamp(NOW,__import__('datetime').timezone.utc),
            resolve_id=lambda ident:'on_synthetic' if ident=='ou_desk_human' else '')
        result=self.m.projected_human_body(native,projected,human_pubkey=HUMAN,roles=roles,
            profiles=[profile],union_ids={HUMAN:'on_synthetic'},now=NOW)
        self.assertEqual(result,actual.text);self.assertNotEqual(result,TEXT1)
        bad=dict(profile);bad['sig']='0'*128
        for profiles,changed_roles,union_ids in (([bad],roles,{HUMAN:'on_synthetic'}),
                ([gs.sign_event(MIRROR_KEY,0,[],profile['content'],NOW-1)],roles,{HUMAN:'on_synthetic'}),
                ([profile],{HUMAN:'bot'},{HUMAN:'on_synthetic'}),([profile],roles,{HUMAN:'on_other'})):
            with self.assertRaises(self.m.ScenarioError):
                self.m.projected_human_body(native,projected,human_pubkey=HUMAN,roles=changed_roles,
                    profiles=profiles,union_ids=union_ids,now=NOW)

    async def test_readonly_protected_plan_actual_scope_and_frozen_pins(self):
        before={str(p):p.read_bytes() for p in self.base.run.rglob('*') if p.is_file()}
        plan=self.check();self.assertEqual(plan.readback()['status'],'prepared');self.assertFalse(plan.readback()['live_verified'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.base.run.rglob('*') if p.is_file()})
        with self.assertRaises(dataclasses.FrozenInstanceError):plan.run_id='foreign'
        self.assertTrue(HUMAN_KEY not in repr(plan))
    async def test_scope_app_chat_profile_path_and_pin_negatives(self):
        old=dict(self.doc)
        for key,value in [('chat_id','oc_foreign'),('channel_id','foreign'),('sync_app_id','cli_production'),
            ('user_profile','foreign'),('human_pubkey','f'*64),('msg001_content_file','/tmp/unapproved')]:
            with self.subTest(field=key):
                self.doc=dict(old);self.doc[key]=value;self.save()
                with self.assertRaises(self.m.ScenarioError) as e:self.check()
                self.assertIn('怎么解决',str(e.exception));self.assertIn('复制给 AI',str(e.exception))
        self.doc=old;self.save();self.manifest.chmod(0o644)
        with self.assertRaises(self.m.ScenarioError):self.check()
    async def test_msg001_unknown_before_post_exact_typed_user_and_wake_pending(self):
        d=await self.driver();r=await self.seed_root(d);self.safe(r)
        self.assertEqual(self.wire.writes,['msg001']);self.assertTrue(r['gates']['feishu_readback'])
        self.assertTrue(r['gates']['signed_mirror']);self.assertTrue(r['gates']['selected_mention']);self.assertTrue(r['gates']['image_readback'])
        self.assertFalse(r['gates']['native_awake'])
        writes=[c for c in self.wire.calls if '+messages-send' in c[0]];self.assertEqual(len(writes),1)
        argv,kw=writes[0];self.assertEqual(argv[argv.index('--chat-id')+1],'oc_testx')
        self.assertEqual(argv[argv.index('--profile')+1],'jchen-personal');self.assertEqual(argv[argv.index('--as')+1],'user')
        self.assertEqual(argv[argv.index('--content')+1],'-');self.assertEqual(argv[argv.index('--msg-type')+1],'post')
        self.assertLessEqual(len(argv[argv.index('--idempotency-key')+1]),50);self.assertEqual(json.loads(kw['input']),json.loads(self.body1.read_text()))
    async def test_phase_repeat_reopen_complete_absence_never_resends(self):
        d=await self.driver();await self.seed_root(d);self.wire.absent=True
        await d.run_msg001()
        reopened=self.m.ScenarioDriver(d.plan,hostd=self.hostd,native_agent=self.agent,runner=self.wire,clock=lambda:NOW,deadline_seconds=.1)
        await reopened.run_msg001();self.assertEqual(self.wire.writes,['msg001'])
    async def test_post_ack_without_fresh_same_user_chat_readback_stays_unknown(self):
        d=await self.driver();self.user_message['chat_id']='oc_foreign'
        r=await d.run_msg001();self.assertEqual(r['status'],'pending');self.assertFalse(r['gates']['feishu_readback'])
        self.assertEqual(json.loads((self.phase_dir/'msg001.json').read_text())['status'],'unknown')
        self.assertEqual(self.wire.writes,['msg001']);await d.run_msg001();self.assertEqual(self.wire.writes,['msg001'])
    async def test_signed_mirror_wrong_author_channel_signature_stale_duplicates_rejected(self):
        for case in ('author','channel','signature','stale','duplicate','plainbody','prefix','name'):
            with self.subTest(case=case):
                d=await self.driver();event=dict(self.root_event)
                if case=='author':event=signed(HUMAN_KEY,self.root_event['content'],event['tags'])
                if case=='channel':event=signed(MIRROR_KEY,self.root_event['content'],[[*t] if t[0]!='h' else ['h','foreign'] for t in event['tags']])
                if case=='plainbody':event=signed(MIRROR_KEY,TEXT1,event['tags'])
                if case=='prefix':event=signed(MIRROR_KEY,self.root_event['content'].replace('[飞书]','[wrong]'),event['tags'])
                if case=='name':event=signed(MIRROR_KEY,self.root_event['content'].replace('Signed Synthetic','Wrong Name'),event['tags'])
                if case=='signature':event['sig']='0'*128
                if case=='stale':event=signed(MIRROR_KEY,self.root_event['content'],event['tags'],NOW-1000)
                self.wire.events=[event,signed(MIRROR_KEY,self.root_event['content'],[*event['tags'],['d','distinct-duplicate']])] if case=='duplicate' else [event]
                r=await d.run_msg001();self.assertFalse(r['gates']['signed_mirror']);self.assertEqual(r['status'],'pending')
                self.assertEqual(self.wire.writes,['msg001'])
    async def test_actual_mention_and_media_hash_required_not_inferred_from_post_ack(self):
        d=await self.driver()
        tags=[tag for tag in self.root_event['tags'] if tag[0]!='p']
        self.wire.events=[signed(MIRROR_KEY,self.root_event['content'],tags)]
        result=await d.run_msg001();self.assertFalse(result['gates']['selected_mention'])
        tags=[tag if tag[0]!='imeta' else ['imeta','url '+LINK+'/media/'+('a'*64)+'.png','m image/png','x '+('a'*64)] for tag in self.root_event['tags']]
        self.wire.events=[signed(MIRROR_KEY,self.root_event['content'],tags)]
        result=await d.run_msg001();self.assertFalse(result['gates']['image_readback'])
        self.assertEqual(self.wire.writes,['msg001'])
    async def test_incomplete_roster_or_wrong_user_refuses_before_human_write(self):
        d=await self.driver();self.wire.identity='ou_foreign';r=await d.run_msg001()
        self.assertEqual(r['status'],'pending');self.assertEqual(self.wire.writes,[])
        self.wire.identity=USER_ID;self.wire.more=True;r=await d.run_msg001()
        self.assertEqual(r['status'],'pending');self.assertEqual(self.wire.writes,[])
    async def test_msg002_human_raw_send_public_card_actual_ledger_association(self):
        d=await self.driver();await self.seed_root(d);r=await d.run_msg002();self.safe(r)
        self.assertEqual(r['status'],'observed');self.assertTrue(r['gates']['signed_human'])
        self.assertTrue(r['gates']['card_readback']);self.assertTrue(r['gates']['ledger_acked'])
        self.assertEqual(self.wire.writes,['msg001','msg002'])
        argv,kw=next(c for c in self.wire.calls if c[0][0]==str(self.base.buzz) and c[0][1:3]==('messages','send'))
        self.assertEqual(argv[argv.index('--reply-to')+1],self.root_event['id']);self.assertEqual(kw['input'],TEXT2)
        await d.run_msg002();self.assertEqual(self.wire.writes,['msg001','msg002'])
    async def test_card_wrong_app_or_root_cannot_green_from_send_ack(self):
        d=await self.driver();await self.seed_root(d);self.wire.card['sender']['id']=AGENT_APP
        r=await d.run_msg002();self.assertEqual(r['status'],'pending');self.assertFalse(r['gates']['card_readback'])
        self.wire.card['sender']['id']=SYNC_APP;self.wire.card['root_id']='om_foreign'
        r=await d.run_msg002();self.assertFalse(r['gates']['card_readback']);self.assertEqual(self.wire.writes,['msg001','msg002'])
    async def test_unknown_or_missing_ledger_never_acked_and_no_driver_store_writes(self):
        d=await self.driver();await self.seed_root(d)
        original=self.ack_ledger
        self.ack_ledger=lambda:original('unknown')
        r=await d.run_msg002();self.assertEqual(r['status'],'pending');self.assertFalse(r['gates']['ledger_acked'])
        before=list(self.store.conn.iterdump());await d.run_msg002();self.assertEqual(before,list(self.store.conn.iterdump()))
        with self.store.transaction():self.store.conn.execute('DELETE FROM delivery WHERE source_id=?',(self.reply_event['id'],))
        before=list(self.store.conn.iterdump());r=await d.run_msg002();self.assertFalse(r['gates']['ledger_acked'])
        self.assertEqual(before,list(self.store.conn.iterdump()));self.assertEqual(self.wire.writes,['msg001','msg002'])
    async def test_timeout_unknown_intent_cas_changed_payload_never_retry(self):
        d=await self.driver();self.wire.timeout_write=True;r=await d.run_msg001();self.assertEqual(r['status'],'pending')
        self.wire.timeout_write=False;await d.run_msg001();self.assertEqual(self.wire.writes,['msg001'])
        self.body1.write_text('{}');self.body1.chmod(0o600)
        with self.assertRaises(self.m.ScenarioError):await d.run_msg001()
        self.assertEqual(self.wire.writes,['msg001'])
    async def test_concurrent_phase_and_cancel_join_dispatched_boundary_no_resend(self):
        import threading
        d=await self.driver();self.wire.hold_write=True;self.wire.entered=threading.Event();self.wire.release=threading.Event()
        task=asyncio.create_task(d.run_msg001())
        self.assertTrue(await asyncio.to_thread(self.wire.entered.wait,2))
        task.cancel();await asyncio.sleep(.02);self.assertFalse(task.done())
        self.wire.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.wire.hold_write=False;await d.run_msg001();self.assertEqual(self.wire.writes,['msg001'])


    def current_cli_root_shape(self):
        # Sanitized original API shape, IDs/text adapted to this isolated fixture.
        # Production post parser/route/signature logic still creates the wire.
        post=json.loads(self.body1.read_text())['zh_cn']
        native=json.loads(json.dumps(post));cells=native['content'][0]
        cells[0]['style']=[]
        cells[1].update(user_id='@_user_1',user_name='Fixture Agent',style=[])
        cells[2].update(width=384,height=384)
        native['content_v2']=json.loads(json.dumps(native['content']))
        self.user_message.update(updated=True,update_time=str(NOW*1000),message_position='24',
            mentions=[{'id':'ou_agentbot','id_type':'open_id','key':'@_user_1',
                       'name':'Fixture Agent','tenant_key':'tenant_fixture'}])
        self.user_message.pop('root_id',None)
        self.user_message['sender']['tenant_key']='tenant_fixture'
        self.user_message['body']={'content':json.dumps(native)}
        normalized=bot_clients.BotLarkCli._normalize_message(self.user_message)
        projected=gs.route_feishu_message(normalized,open_id_to_pubkey={'on_synthetic':HUMAN},
            bot_member_to_pubkey={},channel_members={HUMAN,launcher_fixture.PUB},
            names={HUMAN:'Signed Synthetic'},
            now=__import__('datetime').datetime.fromtimestamp(NOW,__import__('datetime').timezone.utc),
            resolve_id=lambda ident:'on_synthetic' if ident==USER_ID else '')
        self.root_event=signed(MIRROR_KEY,projected.text,self.root_event['tags'])
        self.reply_event=wire_reply(self.root_event);self.wire.events=[self.root_event]
        wire=self.wire
        def boundary(argv,**kw):
            result=wire(argv,**kw)
            if argv[2:5]==['api','GET','/open-apis/im/v1/messages']:
                value=json.loads(result.stdout);value.pop('meta',None);result.stdout=json.dumps(value)
            return result
        return boundary

    async def test_current_cli_terminal_api_envelope_without_meta_preserves_original_rows(self):
        d=await self.driver();d.runner=self.current_cli_root_shape()
        original=json.dumps(self.user_message,sort_keys=True)
        self.assertEqual(d._rows(),[self.user_message,self.wire.card])
        self.assertEqual(json.dumps(self.user_message,sort_keys=True),original)
        self.assertEqual(self.wire.writes,[])

    async def test_current_cli_selected_placeholder_and_image_metadata_semantic_root(self):
        d=await self.driver();d.runner=self.current_cli_root_shape()
        wire=d.runner
        # Separate semantic comparison RED from absent pagination metadata RED.
        def legacy_meta(argv,**kw):
            result=wire(argv,**kw)
            if argv[2:5]==['api','GET','/open-apis/im/v1/messages']:
                value=json.loads(result.stdout);value['meta']={'pagination':{'complete':True}};result.stdout=json.dumps(value)
            return result
        d.runner=legacy_meta;gates={k:False for k in ('feishu_readback','signed_mirror','selected_mention','image_readback')}
        root,_=d._root({'started':NOW},gates)
        self.assertIsNotNone(root);self.assertTrue(all(gates.values()))
        self.assertEqual(self.wire.writes,[])

    async def test_current_cli_sanitized_shape_full_msg001_still_requires_native_wake(self):
        d=await self.driver();d.runner=self.current_cli_root_shape()
        result=await d.run_msg001()
        self.assertTrue(all(result['gates'][k] for k in ('feishu_readback','signed_mirror','selected_mention','image_readback')))
        self.assertFalse(result['gates']['native_awake']);self.assertEqual(result['status'],'pending')
        self.assertFalse(result['live_verified']);self.assertEqual(self.wire.writes,['msg001'])
        intent=(self.phase_dir/'msg001.json').read_bytes()
        await d.run_msg001();self.assertEqual(self.wire.writes,['msg001'])
        self.assertEqual((self.phase_dir/'msg001.json').read_bytes(),intent)

    async def test_current_cli_terminal_pagination_ambiguity_unknown_metadata_and_saturation_refuse(self):
        d=await self.driver();wire=self.current_cli_root_shape()
        for case in ('missing-more','missing-token','token','null-token','more','numeric-more',
                     'meta-null','meta-empty','numeric-complete','incomplete','unknown-pagination',
                     'unknown-meta','unknown-top','unknown-data','invalid-item','missing-id','empty-id',
                     'numeric-id','duplicate','saturated'):
            with self.subTest(case=case):
                def boundary(argv,**kw):
                    result=wire(argv,**kw)
                    if argv[2:5]==['api','GET','/open-apis/im/v1/messages']:
                        value=json.loads(result.stdout);data=value['data']
                        if case=='missing-more':data.pop('has_more')
                        if case=='missing-token':data.pop('page_token')
                        if case=='token':data['page_token']='more-rows'
                        if case=='null-token':data['page_token']=None
                        if case=='more':data['has_more']=True
                        if case=='numeric-more':data['has_more']=0
                        if case=='meta-null':value['meta']=None
                        if case=='meta-empty':value['meta']={}
                        if case=='numeric-complete':value['meta']={'pagination':{'complete':1}}
                        if case=='incomplete':value['meta']={'pagination':{'complete':False}}
                        if case=='unknown-pagination':value['meta']={'pagination':{'complete':True,'truncated':True}}
                        if case=='unknown-meta':value['meta']={'pagination':{'complete':True},'unknown':'unreviewed'}
                        if case=='unknown-top':value['unknown']='unreviewed'
                        if case=='unknown-data':data['truncations']=['partial']
                        if case=='invalid-item':data['items']=[None]
                        if case=='missing-id':data['items']=[{}]
                        if case=='empty-id':data['items']=[{'message_id':''}]
                        if case=='numeric-id':data['items']=[{'message_id':1}]
                        if case=='duplicate':data['items'].append(data['items'][0])
                        if case=='saturated':data['items']=[{'message_id':'om_'+str(i)} for i in range(50000)]
                        result.stdout=json.dumps(value)
                    return result
                d.runner=boundary
                with self.assertRaises(ValueError):d._rows()
                self.assertEqual(self.wire.writes,[]);self.assertFalse((self.phase_dir/'msg001.json').exists())

    async def test_current_cli_post_selected_entities_metadata_and_original_scope_refuse_contradictions(self):
        d=await self.driver();wire=self.current_cli_root_shape()
        original=json.loads(json.dumps(self.user_message))
        for case in ('missing-entity','foreign-id','foreign-id-type','duplicate-entity','foreign-key',
                     'unknown-entity','entity-name','cell-name','mention','mention-style','invalid-dimensions',
                     'missing-height','dimension-type','text','image','title','unknown-cell','unknown-post',
                     'v2-text','v2-mention','v2-dimension','v2-name','sender','chat','root','parent',
                     'before-intent','future','malformed-time','deleted-type','duplicate-root'):
            with self.subTest(case=case):
                self.user_message=json.loads(json.dumps(original))
                post=json.loads(self.user_message['body']['content']);cells=post['content'][0]
                if case=='missing-entity':self.user_message.pop('mentions')
                if case=='foreign-id':self.user_message['mentions'][0]['id']='ou_foreign'
                if case=='foreign-id-type':self.user_message['mentions'][0]['id_type']='union_id'
                if case=='duplicate-entity':self.user_message['mentions'].append(self.user_message['mentions'][0])
                if case=='foreign-key':self.user_message['mentions'][0]['key']='@_user_2'
                if case=='unknown-entity':self.user_message['mentions'][0]['foreign']='unreviewed'
                if case=='entity-name':self.user_message['mentions'][0]['name']='foreign display'
                if case=='cell-name':cells[1]['user_name']='foreign display'
                if case=='mention':cells[1]['user_id']='ou_foreign'
                if case=='mention-style':cells[1]['style']=['bold']
                if case=='invalid-dimensions':cells[2]['height']=-1
                if case=='missing-height':cells[2].pop('height')
                if case=='dimension-type':cells[2]['width']=True
                if case=='text':cells[0]['text']+=' changed'
                if case=='image':cells[2]['image_key']='img_other'
                if case=='title':post['title']='foreign title'
                if case=='unknown-cell':cells[0]['unknown']='unreviewed'
                if case=='unknown-post':post['unknown']='unreviewed'
                if case=='v2-text':post['content_v2'][0][0]['text']+=' contradiction'
                if case=='v2-mention':post['content_v2'][0][1]['user_id']='ou_foreign'
                if case=='v2-dimension':post['content_v2'][0][2]['width']+=1
                if case=='v2-name':post['content_v2'][0][1]['user_name']='contradiction'
                if not case.startswith('v2-'):post['content_v2']=json.loads(json.dumps(post['content']))
                self.user_message['body']['content']=json.dumps(post)
                if case=='sender':self.user_message['sender']['id']='ou_foreign'
                if case=='chat':self.user_message['chat_id']='oc_other'
                if case=='root':self.user_message['root_id']='om_foreign'
                if case=='parent':self.user_message['parent_id']='om_foreign'
                if case=='before-intent':self.user_message['create_time']=str(NOW*1000-1)
                if case=='future':self.user_message['create_time']=str(NOW*1000+1)
                if case=='malformed-time':self.user_message['create_time']=str(NOW*1000)+'.5'
                if case=='deleted-type':self.user_message['deleted']=0
                def boundary(argv,**kw):
                    result=wire(argv,**kw)
                    if case=='duplicate-root' and argv[2:5]==['api','GET','/open-apis/im/v1/messages']:
                        value=json.loads(result.stdout);other=json.loads(json.dumps(self.user_message));other['message_id']='om_second';value['data']['items'].append(other);result.stdout=json.dumps(value)
                    return result
                d.runner=boundary;gates={k:False for k in ('feishu_readback','signed_mirror','selected_mention','image_readback')}
                root,_=d._root({'started':NOW},gates)
                self.assertIsNone(root);self.assertFalse(gates['feishu_readback']);self.assertEqual(self.wire.writes,[])
        self.user_message=original

    async def test_current_cli_readback_failure_diagnostic_is_safe_and_unknown_never_resends(self):
        d=await self.driver();wire=self.current_cli_root_shape()
        secret='PRIVATE-TRANSPORT-TEXT-MUST-NOT-APPEAR'
        def broken(argv,**kw):
            if argv[2:5]==['api','GET','/open-apis/im/v1/messages']:raise RuntimeError(secret)
            return wire(argv,**kw)
        d.runner=broken;result=await d.run_msg001();self.assertEqual(result['status'],'pending')
        error=result['diagnostics'][0]
        self.assertEqual(error['stage'],'root_readback');self.assertEqual(error['failure_type'],'RuntimeError')
        self.assertTrue(error['failure_location']);self.assertNotIn(secret,json.dumps(result))
        for frame in error['failure_location']:
            self.assertNotIn('/',frame['file']);self.assertEqual(set(frame),{'file','line','function'})
        intent=(self.phase_dir/'msg001.json').read_bytes();await d.run_msg001()
        self.assertEqual(self.wire.writes,['msg001']);self.assertEqual((self.phase_dir/'msg001.json').read_bytes(),intent)
        d.runner=wire;recovered=await d.run_msg001()
        self.assertTrue(recovered['gates']['feishu_readback']);self.assertFalse(recovered['gates']['native_awake'])
        self.assertEqual(self.wire.writes,['msg001']);self.assertEqual((self.phase_dir/'msg001.json').read_bytes(),intent)

    async def test_native_api_list_preserves_original_items_and_exact_user_query(self):
        d=await self.driver();self.wire.shortcut_unavailable=True
        rows=d._rows();self.assertEqual(rows,[self.user_message,self.wire.card])
        calls=[argv for argv,_ in self.wire.calls if argv[2:5]==('api','GET','/open-apis/im/v1/messages')]
        self.assertEqual(len(calls),1);argv=calls[0]
        self.assertEqual(json.loads(argv[argv.index('--params')+1]),{'container_id_type':'chat','container_id':self.doc['chat_id'],'page_size':50,'sort_type':'ByCreateTimeDesc','user_id_type':'open_id'})
        self.assertEqual(argv[argv.index('--page-limit')+1],'0');self.assertIn('--page-all',argv)
        self.assertEqual(argv[argv.index('--profile')+1],'jchen-personal');self.assertEqual(argv[argv.index('--as')+1],'user')
        self.assertFalse(any('+chat-messages-list' in argv for argv,_ in self.wire.calls));self.assertEqual(self.wire.writes,[])

    async def test_native_sender_tenant_metadata_keeps_original_api_row_authority(self):
        self.user_message['sender']['tenant_key']='synthetic-tenant'
        self.user_message.pop('root_id',None);self.user_message.pop('parent_id',None)
        d=await self.driver();result=await self.seed_root(d)
        self.assertTrue(result['gates']['feishu_readback']);self.assertTrue(result['gates']['signed_mirror'])
        self.assertEqual(self.wire.writes,['msg001']);self.assertFalse(result['live_verified'])

    async def test_native_post_content_and_redundant_v2_match_frozen_localized_post(self):
        expected=json.loads(self.body1.read_text())['zh_cn'];native=json.loads(json.dumps(expected))
        native['content'][0][0]['style']=[];native['content_v2']=json.loads(json.dumps(native['content']))
        self.user_message['body']={'content':json.dumps(native)}
        d=await self.driver();result=await self.seed_root(d)
        self.assertTrue(result['gates']['feishu_readback']);self.assertTrue(result['gates']['signed_mirror'])
        self.assertTrue(result['gates']['selected_mention']);self.assertTrue(result['gates']['image_readback'])
        self.assertEqual(self.wire.writes,['msg001']);self.assertFalse(result['live_verified'])

    async def test_native_api_paging_and_envelope_failures_never_become_readback(self):
        d=await self.driver();wire=self.wire
        for case in ('more','incomplete','duplicate','not-items','false-ok','bot-envelope','nonzero'):
            with self.subTest(case=case):
                def boundary(argv,**kw):
                    result=wire(argv,**kw)
                    if argv[2:5]==['api','GET','/open-apis/im/v1/messages']:
                        value=json.loads(result.stdout)
                        if case=='more':value['data']['has_more']=True
                        if case=='incomplete':value['meta']['pagination']['complete']=False
                        if case=='duplicate':value['data']['items'].append(value['data']['items'][0])
                        if case=='not-items':value['data']['items']={}
                        if case=='false-ok':value['ok']=False
                        if case=='bot-envelope':value['identity']='bot'
                        if case=='nonzero':result.returncode=1
                        result.stdout=json.dumps(value)
                    return result
                d.runner=boundary
                with self.assertRaises(ValueError):d._rows()
                self.assertEqual(self.wire.writes,[]);self.assertFalse((self.phase_dir/'msg001.json').exists())
        d.runner=wire;self.assertEqual(d._rows(),[self.user_message,self.wire.card])

    async def test_native_actor_target_and_literal_post_changes_never_become_root_authority(self):
        d=await self.driver();original=json.loads(json.dumps(self.user_message))
        for case in ('actor','actor-type','actor-id-type','unobserved-sender-name','chat','title','text','image','mention','style','v2-conflict','unsupported-mention-name','single-readback-different'):
            with self.subTest(case=case):
                self.user_message=json.loads(json.dumps(original));native=json.loads(self.body1.read_text())['zh_cn']
                native['content'][0][0]['style']=[];native['content_v2']=json.loads(json.dumps(native['content']))
                self.user_message['body']={'content':json.dumps(native)};self.user_message['sender']['tenant_key']='synthetic-tenant'
                if case=='actor':self.user_message['sender']['id']='ou_foreign'
                if case=='actor-type':self.user_message['sender']['sender_type']='app'
                if case=='actor-id-type':self.user_message['sender']['id_type']='union_id'
                if case=='unobserved-sender-name':self.user_message['sender']['name']='untrusted-display'
                if case=='chat':self.user_message['chat_id']='oc_foreign'
                if case=='title':native['title']='changed title'
                if case=='text':native['content'][0][0]['text']+=' changed'
                if case=='image':native['content'][0][2]['image_key']='img_foreign'
                if case=='mention':native['content'][0][1]['user_id']='ou_foreign'
                if case=='style':native['content'][0][0]['style']=['bold']
                if case=='unsupported-mention-name':native['content'][0][1]['user_name']='unverified-display'
                if case=='v2-conflict':native['content_v2'][0][0]['text']+=' conflicting view'
                elif case not in ('actor','actor-type','actor-id-type','unobserved-sender-name','chat','single-readback-different'):
                    native['content_v2']=json.loads(json.dumps(native['content']))
                self.user_message['body']={'content':json.dumps(native)}
                wire=self.wire
                def boundary(argv,**kw):
                    result=wire(argv,**kw)
                    if case=='single-readback-different' and argv[2:5]==['api','GET','/open-apis/im/v1/messages/'+MID]:
                        value=json.loads(result.stdout);value['data']['items'][0]['update_time']='changed-original';result.stdout=json.dumps(value)
                    return result
                d.runner=boundary;gates={'feishu_readback':False,'signed_mirror':False,'selected_mention':False,'image_readback':False}
                root,_=d._root({'started':NOW},gates)
                self.assertIsNone(root);self.assertFalse(gates['feishu_readback']);self.assertEqual(self.wire.writes,[])

    async def test_native_chat_get_shape_without_echoed_chat_id_accepts_exact_user_readonly_scope(self):
        d=await self.driver()
        # Public field shape only, sanitized independently of actual chat body.
        self.wire.chat={'chat_mode':'group','chat_status':'normal','chat_type':'private','user_count':'2','bot_count':'3','owner_id':USER_ID}
        d._identity()
        chat=[argv for argv,_ in self.wire.calls if argv[2:5]==('api','GET','/open-apis/im/v1/chats/'+self.doc['chat_id'])]
        self.assertEqual(len(chat),1)
        self.assertEqual(chat[0][chat[0].index('--profile')+1],'jchen-personal');self.assertEqual(chat[0][chat[0].index('--as')+1],'user')
        rosters=[argv for argv,_ in self.wire.calls if '+chat-members-list' in argv]
        self.assertEqual(len(rosters),2)
        for argv in rosters:
            self.assertEqual(argv[argv.index('--chat-id')+1],self.doc['chat_id'])
            self.assertEqual(argv[argv.index('--as')+1],'user');self.assertIn('--page-all',argv)
        self.assertEqual(self.wire.writes,[])
        self.assertFalse((self.phase_dir/'msg001.json').exists());self.assertFalse((self.phase_dir/'msg002.json').exists())

    async def test_chat_identity_optional_echo_wrong_status_envelope_and_target_refuse_before_write(self):
        d=await self.driver();original=self.wire
        for case in ('foreign-target','null-target','false-envelope','wrong-envelope-identity','bad-data','nonzero-exit','wrong-app-status','wrong-user-status','wrong-user-info','partial-users','partial-bots','truncated-roster','wrong-total','duplicate-bot-app','wrong-bot-set'):
            with self.subTest(case=case):
                self.wire.chat={'chat_mode':'group','chat_status':'normal','chat_type':'private'}
                if case in ('foreign-target','null-target'):self.wire.chat['chat_id']='oc_foreign' if case=='foreign-target' else None
                def boundary(argv,**kw):
                    result=original(argv,**kw);args=argv[2:]
                    if args[:2]==['auth','status'] and case in ('wrong-app-status','wrong-user-status'):
                        doc=json.loads(result.stdout)
                        if case=='wrong-app-status':doc['appId']='cli_foreign'
                        else:doc['identities']['user']['openId']='ou_foreign'
                        result.stdout=json.dumps(doc)
                    if args[:3]==['api','GET','/open-apis/authen/v1/user_info'] and case=='wrong-user-info':
                        doc=json.loads(result.stdout);doc['data']['open_id']='ou_foreign';result.stdout=json.dumps(doc)
                    if args[:3]==['api','GET','/open-apis/im/v1/chats/'+self.doc['chat_id']]:
                        doc=json.loads(result.stdout)
                        if case=='false-envelope':doc['ok']=False
                        if case=='wrong-envelope-identity':doc['identity']='bot'
                        if case=='bad-data':doc['data']=[]
                        if case=='nonzero-exit':result.returncode=1
                        result.stdout=json.dumps(doc)
                    if args[:2]==['im','+chat-members-list']:
                        doc=json.loads(result.stdout);kind=argv[argv.index('--member-types')+1]
                        if case=='partial-users' and kind=='user':doc['data']['has_more']=True
                        if case=='partial-bots' and kind=='bot':doc['data']['has_more']=True
                        if case=='truncated-roster':doc['data']['truncations']=['synthetic-incomplete']
                        if case=='wrong-total':doc['data'][kind+'_total']+=1
                        if kind=='bot' and case=='duplicate-bot-app':doc['data']['bots'][1]['app_id']=SYNC_APP
                        if kind=='bot' and case=='wrong-bot-set':doc['data']['bots'][1]['app_id']='cli_foreign'
                        result.stdout=json.dumps(doc)
                    return result
                d.runner=boundary
                with self.assertRaises(ValueError):d._identity()
                self.assertEqual(self.wire.writes,[]);self.assertFalse((self.phase_dir/'msg001.json').exists())

    async def test_native_auth_status_exact_verified_json_flags(self):
        d=await self.driver();await self.seed_root(d)
        auth=[argv for argv,_ in self.wire.calls if argv[2:4]==('auth','status')]
        self.assertTrue(auth)
        for argv in auth:
            self.assertEqual(argv[:2],(self.base.m.NODE,self.base.m.CLI_ENTRY))
            self.assertEqual(argv[2:],('auth','status','--verify','--json','--profile','jchen-personal'))
            self.assertNotIn('--as',argv);self.assertNotIn('--format',argv)
        business=[argv for argv,_ in self.wire.calls if argv[0]==self.base.m.NODE and argv[2:4]!=('auth','status')]
        self.assertTrue(business)
        for argv in business:
            role=argv[argv.index('--as')+1]
            self.assertEqual(argv[argv.index('--profile')+1],'jchen-personal' if role=='user' else SYNC_APP)
            self.assertIn(role,('user','bot'))

    async def test_repeated_cancel_joins_held_post_before_cleanup_and_never_resends(self):
        import threading
        d=await self.driver();self.wire.hold_write=True
        self.wire.entered=threading.Event();self.wire.release=threading.Event()
        completed=threading.Event();wire=self.wire
        def tracked_boundary(argv,**kw):
            is_post=argv[2:4]==['im','+messages-send']
            try:return wire(argv,**kw)
            finally:
                if is_post:completed.set()
        d.runner=tracked_boundary
        task=asyncio.create_task(d.run_msg001());premature=[]
        try:
            self.assertTrue(await asyncio.to_thread(self.wire.entered.wait,2))
            for _ in range(3):
                task.cancel();await asyncio.sleep(.02)
                premature.append(task.done())
                self.assertFalse(completed.is_set())
        finally:
            self.wire.release.set()
            self.assertTrue(await asyncio.to_thread(completed.wait,2))
            with self.assertRaises(asyncio.CancelledError):await task
        self.wire.hold_write=False
        result=await d.run_msg001();self.safe(result)
        self.assertEqual(self.wire.writes,['msg001'])
        self.assertEqual(json.loads((self.phase_dir/'msg001.json').read_text())['status'],'unknown')
        self.assertEqual(premature,[False,False,False])


class RealWireControls(unittest.TestCase):
    def test_post_semantics_preserve_title_order_literal_fields_and_nonempty_styles(self):
        m=importlib.import_module('integration.hostd_l3.scenario_driver')
        body={'title':'literal title','content':[[{'tag':'text','text':'a literal whitespace '},{'tag':'a','text':'link','href':'https://example.invalid/exact'},{'tag':'at','user_id':'ou_exact'},{'tag':'img','image_key':'img_exact'}]]}
        native=json.loads(json.dumps(body));native['content'][0][0]['style']=[];native['content'][0][1]['style']=[];native['content_v2']=json.loads(json.dumps(native['content']))
        canonical=m._canonical(json.dumps({'zh_cn':body}));self.assertEqual(m._canonical(json.dumps(native)),canonical)
        for case in ('title','text','href','image','mention','cell-order','paragraph-order','style','mention-name'):
            changed=json.loads(json.dumps(body))
            if case=='title':changed['title']+=' '
            if case=='text':changed['content'][0][0]['text']=changed['content'][0][0]['text'].strip()
            if case=='href':changed['content'][0][1]['href']+='?changed'
            if case=='image':changed['content'][0][3]['image_key']='img_other'
            if case=='mention':changed['content'][0][2]['user_id']='ou_other'
            if case=='cell-order':changed['content'][0].reverse()
            if case=='paragraph-order':changed['content'].insert(0,[])
            if case=='style':changed['content'][0][0]['style']=['bold']
            if case=='mention-name':changed['content'][0][2]['user_name']='unverified-display'
            self.assertNotEqual(m._canonical(json.dumps(changed)),canonical,case)
        styled=json.loads(json.dumps(body));styled['content'][0][0]['style']=['bold','italic']
        native=json.loads(json.dumps(styled));native['content_v2']=json.loads(json.dumps(native['content']))
        self.assertEqual(m._canonical(json.dumps({'zh_cn':styled})),m._canonical(json.dumps(native)))
        native['content'][0][0]['style'].reverse();native['content_v2']=json.loads(json.dumps(native['content']))
        self.assertNotEqual(m._canonical(json.dumps({'zh_cn':styled})),m._canonical(json.dumps(native)))
        native['content_v2'][0][0]['text']='conflicting view'
        with self.assertRaises(ValueError):m._canonical(json.dumps(native))
        for invalid in ({**body,'files':[]},{'zh_cn':body,'en_us':body},{**body,'content_v2':None},{**body,'title':None}):
            with self.assertRaises(ValueError):m._canonical(json.dumps(invalid))

    def test_real_signed_mapping_rejects_tampered_signature(self):
        root=wire_root('a'*64)
        self.assertIsNotNone(mapping.buzz_mapping(root,CHANNEL,set(),{MIRROR}))
        root['content']='tampered';self.assertIsNone(mapping.buzz_mapping(root,CHANNEL,set(),{MIRROR}))
    def test_real_native_card_parser_root_and_app_proof(self):
        root=wire_root('a'*64);reply=wire_reply(root);card=wire_card(root,reply)
        root_map=mapping.buzz_mapping(root,CHANNEL,set(),{MIRROR})
        self.assertIsNotNone(mapping.feishu_mapping(card,reply,CHANNEL,'oc_testx',LINK,{HUMAN},{},SYNC_APP,root_mapping=root_map))
        card['root_id']='om_foreign'
        self.assertIsNone(mapping.feishu_mapping(card,reply,CHANNEL,'oc_testx',LINK,{HUMAN},{},SYNC_APP,root_mapping=root_map))
    def test_actual_bot_adapter_cli_envelope_message_read_not_verified_dto(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cfg=root/'config';data=root/'data';cfg.mkdir(mode=0o700);data.mkdir(mode=0o700)
            p=cfg/'config.json';p.write_text(json.dumps({'apps':[{'appId':SYNC_APP}]}));p.chmod(0o600)
            event=wire_root('a'*64);card=wire_card(event,wire_reply(event));calls=[]
            def runner(argv,**kw):
                calls.append(argv)
                return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':{'items':[card]}}),'')
            client=bot_clients.BotLarkCli(SYNC_APP,cfg,data,base_env={'HOME':str(root)},runner=runner)
            self.assertEqual(client.message_view(CARD,'open_id')['message_id'],CARD)
            self.assertEqual(calls[0][:2],[bot_clients.NODE,bot_clients.CLI_ENTRY]);self.assertEqual(calls[0][calls[0].index('--as')+1],'bot')
    def test_real_sqlite_readonly_exact_acked_source_target_root_tuple(self):
        import sqlite3,tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'state/db'
            event=wire_reply(wire_root('a'*64))
            with Store(path) as store:
                store.reconcile_bindings([BindingRecord('synthetic',CHANNEL,'oc_testx',SYNC_APP,
                    str(root/'env'),str(root/'cfg'),str(root/'data'),mirror_pubkey=MIRROR)],now=NOW)
                delivery=store.reserve_delivery('synthetic',event['id'],'b2f',root_id=MID,source_at=NOW,now=NOW)
                store.ack_delivery(delivery.id,CARD,now=NOW)
            db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
            try:
                row=db.execute('SELECT source_id,target_id,root_id,status FROM delivery WHERE id=?',(delivery.id,)).fetchone()
                self.assertEqual(row,(event['id'],CARD,MID,'acked'))
                with self.assertRaises(sqlite3.OperationalError):db.execute("UPDATE delivery SET status='unknown'")
            finally:db.close()
