"""PRIVATE tests-first S7 root/binding wiring; AST only, never executed.

Immutable snapshot must compose reviewed daemon6 main/Store + schema12 Store.
No foreign AgentRecord/env/catalog/lane, Coordinator mock or proof DTO.
"""
import asyncio
import base64
import concurrent.futures
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS), str(SCRIPTS / 'hostd')]
import test_buzz_feishu_group_sync as base
import test_hostd_delivery_mapping as mapping_fixture
import test_hostd_remote_mapping as keys
import test_hostd_remote_proofs as crypto_fixture
from hostd import binding_worker, bot_clients, delivery_mapping, registry, store
from hostd.async_io import thread_call
from hostd.state_store import StateAdapter
from hostd.http_pool import HttpPool
from hostd.scheduler import Scheduler
from hostd.onboarding_runtime import OnboardingRuntime
from hostd.join_effects import ProcessOps
import hostd_real_onboarding_fixture as real_fixture
from hostd_real_onboarding_fixture import RealOnboardingInput
import buzz_feishu_group_sync as gs

setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
SYNC_KEY = '6'.zfill(64)
SYNC_PUB = gs._signer_pubkey(SYNC_KEY)
OTHER_CHANNEL = '77777777-7777-4777-8777-777777777777'
OTHER_CHAT = 'oc_s7other000000000000000000000001'


class Response:
    status = 200
    will_close = False
    def __init__(self, payload):
        self.body = json.dumps(payload, separators=(',', ':')).encode()
    def getheader(self, name, default=None): return default
    def getheaders(self): return []
    def read1(self, size):
        out, self.body = self.body[:size], self.body[size:]
        return out
    read = read1


class Connection:
    """Only HTTPS wire; the real root pool owns token cache/deadline/scheduler."""
    def __init__(self, fixture, host, *, timeout, context):
        fixture.case.assertEqual(host, 'open.feishu.cn')
        self.fixture, self.closed, self.pending = fixture, False, None
        fixture.connections.append(self)
    def request(self, method, path, body=None, headers=None):
        f = self.fixture; f.case.assertFalse(self.closed)
        data = json.loads(body) if body else None
        if path == '/open-apis/auth/v3/tenant_access_token/internal':
            f.case.assertEqual(data['app_id'], base.AGENT_APP)
            # Same genuine synthetic protected ciphertext/parser/AES seam as
            # RealOnboardingInput; never replace app_secret or the Pool.
            f.case.assertEqual(data['app_secret'], 'SYNTHETIC_APP_SECRET')
            self.pending = {'code': 0, 'tenant_access_token': 'SYNTHETIC_S7_TOKEN', 'expire': 7200}
            return
        f.case.assertEqual(headers['Authorization'], 'Bearer SYNTHETIC_S7_TOKEN')
        parsed = urlsplit(path)
        params = {k:v[0] for k,v in parse_qs(parsed.query).items()}
        self.pending = {'code': 0, 'data': f.native(method, parsed.path, params, data)}
    def getresponse(self): return Response(self.pending)
    def close(self): self.closed = True


class Lane:
    """Actual protected binding and legacy low transport with scoped signed rows."""
    def __init__(self, fixture, name, channel, chat):
        self.fixture, self.name, self.channel, self.chat = fixture, name, channel, chat
        self.claimed_at=fixture.now-100
        self.root = fixture.run_dir / name; self.root.mkdir(mode=0o700)
        self.env = base.Env(self.root, message_format='card', remove_extras=False)
        self.cfg = gs.load_config(self.env.config)
        self.cfg['channel_id'], self.cfg['chat_id'] = channel, chat
        self.cfg['agents'] = {SYNC_PUB: self.cfg['agents'][base.AGENT_PK]}
        self.cfg['desk_pubkey'] = SYNC_PUB
        self.cfg['binding_claim'] = True
        self.write(self.env.config, json.dumps(self.cfg))
        self.world = mapping_fixture.MappingWorld(self.root)
        self.world.needs_auth_tag.clear()
        self.world.members = [m for m in self.world.members if m['pubkey'] not in (base.CAROL_PK,base.AGENT2_PK)]
        self.world.users = {base.OWNER_OPEN,base.ALICE_OPEN,base.BOB_OPEN}
        for row in self.world.members:
            if row['pubkey'] == base.AGENT_PK: row['pubkey'] = SYNC_PUB
        self.world.members.append({'pubkey':keys.PUB,'role':'bot'})
        self.world.bots[keys.APP] = 'ou_s7foreign000000000000000000001'
        self.world.bot_members[keys.APP] = self.world.bots[keys.APP]
        self.world.clock = fixture.utc_datetime()
        self.events = self.world.events
        self.messages = {}
        self.root_mid = 'om_s7root' + name.replace('-','_')
        self.root_event = gs.sign_event(base.MIRROR_KEY,9,[['h',channel],['feishu',self.root_mid],
            ['feishu-root',self.root_mid],['feishu-author',base.ALICE_PK]],'mapped topic root',fixture.now-30)
        self.events.append(self.root_event)
        self.messages[self.root_mid] = self.raw_message(self.root_mid, base.ALICE_OPEN,
            'user','text',json.dumps({'text':'mapped topic root'}),self.root_mid)
        self.public = [
            gs.sign_event(SYNC_KEY,0,[keys.auth(SYNC_KEY,base.OWNER_KEY)],'{}',fixture.now-20),
            gs.sign_event(base.MIRROR_KEY,0,[keys.auth(base.MIRROR_KEY,base.OWNER_KEY)],'{}',fixture.now-20),
            gs.sign_event(keys.KEY,0,[keys.auth(keys.KEY,keys.OWNER_KEY)],
                          json.dumps({'name':'远端助手'}),fixture.now-20),
            gs.sign_event(base.OWNER_KEY,30177,[['d',SYNC_PUB]],
                          json.dumps({'feishu':{'app_id':base.AGENT_APP}}),fixture.now-10),
            gs.sign_event(keys.OWNER_KEY,30177,[['d',keys.PUB]],
                          json.dumps({'feishu':{'app_id':keys.APP}}),fixture.now-10),
            gs.sign_event(base.OWNER_KEY,30177,[['d',base.MIRROR_PK]],json.dumps({'feishu':{
                'mirror':True,'bindings':[{'channel':channel,'chat_ref':gs.chat_ref(chat),
                'claimed_at':fixture.now-100,'heartbeat':fixture.now,
                'sync_app':{'version':1,'app_id':base.AGENT_APP}}]}}),fixture.now),
            gs.sign_event(keys.RELAY_KEY,39002,[['d',channel]]+
                [['p',m['pubkey'],'',m['role']] for m in self.world.members], '',fixture.now)]
        self.public.append(self.root_event)
        self.query_extra = None
        self.query_calls = []

    @staticmethod
    def write(path, value):
        path = Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'wb') as output: output.write(value.encode() if isinstance(value,str) else value)
        path.chmod(0o600)

    def raw_message(self, mid, sender, sender_type, kind, content, root):
        return {'message_id':mid,'chat_id':self.chat,'root_id':root,
            'thread_id':'omt_'+self.root_mid,'create_time':str(self.fixture.now*1000),
            'msg_type':kind,'sender':{'sender_type':sender_type,'id_type':'app_id' if sender_type=='app' else 'open_id','id':sender},
            'body':{'content':content}}

    def source(self, label):
        row=gs.sign_event(keys.KEY,9,[['h',self.channel],['e',self.root_event['id'],'','root'],
            ['e',self.root_event['id'],'','reply']],label,self.fixture.now)
        self.events.append(row); self.public.append(row)
        return row

    def delivered(self, event):
        mid='om_s7delivered'+event['id'][:10]
        content=delivery_mapping.message_card(event,'远端助手',event['content'],self.cfg['people_api']['base_url'],self.channel)
        self.messages[mid]=self.raw_message(mid,keys.APP,'app','interactive',content,self.root_mid)
        return mid

    def http(self,url,headers,timeout,body=None):
        self.world.clock=self.fixture.utc_datetime()
        if url=='https://relay.test/query':
            self.fixture.case.assertEqual(self.world._verify_relay_nip98(headers.get('Authorization',''),url,body),base.OWNER_PK)
            filters=json.loads(body); self.query_calls.append(copy.deepcopy(filters))
            def matches(event,fil):
                if 'kinds' in fil and event['kind'] not in fil['kinds']:return False
                if 'authors' in fil and event['pubkey'] not in fil['authors']:return False
                if 'ids' in fil and event['id'] not in fil['ids']:return False
                if 'since' in fil and event['created_at']<fil['since']:return False
                if 'until' in fil and event['created_at']>fil['until']:return False
                return all(any(tag and tag[0]==key[1:] and len(tag)>1 and tag[1] in values for tag in event['tags'])
                           for key,values in fil.items() if key.startswith('#'))
            packet=[e for lane in self.fixture.lanes.values() for e in lane.public+lane.events]
            rows={}
            for fil in filters:
                matched=sorted((e for e in packet if matches(e,fil)),
                    key=lambda e:(e['created_at'],e['id']),reverse=True)
                if type(fil.get('limit')) is int:matched=matched[:fil['limit']]
                rows.update((e['id'],e) for e in matched)
            selected=sorted(rows.values(),key=lambda e:(e['created_at'],e['id']),reverse=True)
            if self.query_extra is not None:selected.append(self.query_extra)
            return 200,json.dumps(selected).encode()
        expected=base.API_ORIGIN+'/bind/api/channels/'+self.channel+'/people'
        if url==expected:
            self.fixture.case.assertEqual(self.world._verify_nip98(headers.get('Authorization',''),'GET',url),base.OWNER_PK)
            roles={m['pubkey']:m['role'] for m in self.world.members}
            people={pk:base.bridge_open_of(op) for pk,op in self.world.bindings.items() if pk in roles}
            unions={pk:base.union_of(op) for pk,op in self.world.bindings.items() if pk in roles}
            return 200,json.dumps({'channel':self.channel,'as_of':self.fixture.utc_datetime().isoformat(),
                'people':people,'union_ids':unions,'emails':{pk:self.world.emails_of(pk) for pk in people}}).encode()
        return self.world.http_get(url,headers,timeout,body=body)

    def runner(self, argv, **kwargs):
        # The base low CLI fixture serves only its original channel. A second
        # genuine protected binding needs its own low channel response.
        if argv[1:3] == ['channels','get']:
            self.fixture.case.assertEqual(argv[argv.index('--channel')+1],self.channel)
            return subprocess.CompletedProcess(argv,0,json.dumps({'id':self.channel,'name':'本群'}),'')
        return self.world(argv,**kwargs)

    def clients(self, cfg, environment, *, http_pool=None, scheduler=None):
        return bot_clients.build_clients(cfg,environment,runner=self.runner,http=self.http,
            trusted_relays={'https://relay.test'},http_pool=http_pool or self.fixture.host.http_pool,
            scheduler=scheduler or self.fixture.host.scheduler)


class PortableAES(crypto_fixture.DecryptFixture):
    """Synthetic lower AES primitive; real profile/file parsing is untouched."""
    def encrypt(self, nonce, plaintext, aad):
        if nonce != b'n'*12 or aad is not None:
            raise ValueError('invalid synthetic AES input')
        return self.ciphertext


class Socket:
    """Only the relay WebSocket wire; actual follow verifies AUTH and events."""
    def __init__(self, fixture, url):
        fixture.case.assertEqual(url,'wss://relay.test')
        self.fixture,self.queue,self.closed=fixture,asyncio.Queue(),False
        self.queue.put_nowait(json.dumps(['AUTH','SYNTHETIC_S7_CHALLENGE']))
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    def __aiter__(self):return self
    async def __anext__(self):return await self.queue.get()
    async def send(self, raw):
        frame=json.loads(raw)
        if frame[0]=='AUTH':
            event=frame[1]
            self.fixture.case.assertTrue(gs._nip01_event_verified(event))
            self.fixture.case.assertEqual((event['kind'],event['pubkey']),(22242,base.MIRROR_PK))
            self.queue.put_nowait(json.dumps(['OK',event['id'],True,'']))
        elif frame[0]=='REQ':
            self.fixture.case.assertEqual(len(frame),3)
            self.fixture.case.assertEqual(len(frame[2]['#h']),1)
            self.fixture.case.assertIn(frame[2]['#h'][0],{l.channel for l in self.fixture.lanes.values()})
            self.queue.put_nowait(json.dumps(['EOSE',frame[1]]))
        else:self.fixture.case.assertEqual(frame[0],'CLOSE')


class WiringWorld:
    def __init__(self,case):
        self.case=case
        self.tmp=tempfile.TemporaryDirectory(prefix='hostd-s7-wiring-')
        case.addCleanup(self.tmp.cleanup)
        self.run_dir=Path(self.tmp.name)
        self.now=int(base.NOW.timestamp())+10
        self.loop=asyncio.get_running_loop()
        self.lanes={};self.connections=[];self.effects=[];self.children=[];self.main_tasks=[]
        self.native_entered=threading.Event();self.native_release=threading.Event()
        self.block_binding=None;self.inflight=0;self.mutex=threading.Lock()
        self.original_spawn=asyncio.create_subprocess_exec
        self.transport_patches=[]
        self.host=None;self.db_path=self.run_dir/'metadata'/'hostd.db'
        self.native_mid=0
        self.setup_lane('initial',base.CHANNEL,base.CHAT)
        first=self.lanes['initial']
        app=first.cfg['agents'][SYNC_PUB]
        # Install lower portable AES BEFORE the real input seeds encrypted
        # bytes; a dependency-only encrypt paired with another fake decrypt
        # would be an invalid fixture, not a wiring regression.
        for module in (real_fixture.catalog_fixture,crypto_fixture.secret_module()):
            patch=mock.patch.object(module,'AESGCM',PortableAES)
            patch.start();case.addCleanup(patch.stop)
        self.real_input=RealOnboardingInput(case,private_key=SYNC_KEY,owner_key=base.OWNER_KEY,
            app_id=base.AGENT_APP,profile_config_dir=app['lark_config_dir'],
            profile_data_dir=app['lark_data_dir'],relay_key=keys.RELAY_KEY)
        # Own record env remains zero-channel; foreign public B has NO row/path.
        case.assertEqual(self.real_input.pub,SYNC_PUB)
        secrets=crypto_fixture.secret_module()
        absent=object();previous=sys.modules.get('secrets_store',absent)
        sys.modules['secrets_store']=secrets
        def restore():
            if previous is absent:sys.modules.pop('secrets_store',None)
            else:sys.modules['secrets_store']=previous
        case.addCleanup(restore)
        self.first_profile=(app['lark_config_dir'],app['lark_data_dir'])

    def utc_datetime(self):return datetime.fromtimestamp(self.now,timezone.utc)

    def setup_lane(self,name,channel,chat):
        lane=Lane(self,name,channel,chat);self.lanes[name]=lane
        # One relay has one latest owner-signed mirror policy containing both
        # binding entries. A per-lane fake divergent policy would falsely lend
        # authority to the second protected binding.
        entries=[{'channel':x.channel,'chat_ref':gs.chat_ref(x.chat),
            'claimed_at':x.claimed_at,'heartbeat':self.now,
            'sync_app':{'version':1,'app_id':base.AGENT_APP}} for x in self.lanes.values()]
        policy=gs.sign_event(base.OWNER_KEY,30177,[['d',base.MIRROR_PK]],
            json.dumps({'feishu':{'mirror':True,'bindings':entries}}),self.now)
        for current in self.lanes.values():
            current.public=[e for e in current.public if not(e['kind']==30177 and ['d',base.MIRROR_PK] in e['tags'])]
            current.public.append(policy)
        return lane

    def on_loop(self,call):
        future=concurrent.futures.Future()
        def perform():
            try:future.set_result(call())
            except BaseException as exc:future.set_exception(exc)
        self.loop.call_soon_threadsafe(perform)
        return future.result(timeout=10)

    def native(self,method,path,params,data):
        with self.mutex:self.inflight+=1
        try:
            if method=='GET' and path=='/open-apis/application/v6/scopes':
                names=set(bot_clients.READ_SCOPE_GROUPS)|{'im:message:send_as_bot','im:message:update'}
                return {'scopes':[{'scope_name':n,'scope_type':'tenant','grant_status':1} for n in sorted(names)]}
            if method=='GET' and path=='/open-apis/im/v1/chats':
                return {'items':[dict(x.world.chat,chat_id=x.chat,name='本群') for x in self.lanes.values()],
                        'has_more':False,'page_token':''}
            lane=next((x for x in self.lanes.values() if '/chats/'+x.chat in path),None)
            if method=='GET' and lane is not None:
                if path.endswith('/members/list'):
                    typ=params.get('member_id_type','open_id');kind=params.get('member_types','user,bot')
                    users=[{'member_id':base.union_of(op) if typ=='union_id' else op,'name':'Human'} for op in lane.world.users]
                    bots=[{'app_id':app,'member_id':mid} for app,mid in lane.world.bots.items()]
                    result={'has_more':False,'page_token':'','truncations':[]}
                    if 'user' in kind:result.update(users=users,user_total=len(users))
                    if 'bot' in kind:result.update(bots=bots,bot_total=len(bots))
                    return result
                return dict(lane.world.chat,name='本群',owner_id=base.union_of(base.OWNER_OPEN),owner_id_type='union_id')
            if method=='GET' and path=='/open-apis/im/v1/messages':
                container=params['container_id']
                lane=next(x for x in self.lanes.values() if container in (x.chat,'omt_'+x.root_mid))
                rows=list(lane.messages.values())
                if params['container_id_type']=='thread':rows=[m for m in rows if m['root_id']==lane.root_mid]
                else:rows=[m for m in rows if m['message_id']==m['root_id']]
                return {'items':copy.deepcopy(rows),'has_more':False,'page_token':''}
            if method=='GET' and path.startswith('/open-apis/im/v1/messages/'):
                mid=path.rsplit('/',1)[-1]
                row=next((x.messages[mid] for x in self.lanes.values() if mid in x.messages),None)
                return {'items':[] if row is None else [copy.deepcopy(row)]}
            if method=='POST' and path.endswith('/reactions/batch_query'):
                return {'success_msg_reaction_details':[{'message_id':q['message_id'],'message_reaction_items':[],
                    'has_more':False} for q in data['queries']],'fail_msg_reaction_details':[]}
            if method=='POST' and (path.endswith('/reply') or path=='/open-apis/im/v1/messages'):
                if path.endswith('/reply'):
                    root=path.split('/')[-2];lane=next(x for x in self.lanes.values() if x.root_mid==root)
                else:
                    lane=next(x for x in self.lanes.values() if x.chat==data['receive_id']);root=''
                card=json.loads(data['content'])
                self.native_mid+=1;mid='om_s7notice'+str(self.native_mid)
                self.effects.append((lane.name,method,path,mid,copy.deepcopy(data)))
                if self.block_binding==lane.name:
                    state=self.on_loop(lambda:self.notice_rows(lane.name))
                    self.case.assertEqual(len(state),1)
                    self.case.assertIn(state[0].state,('reserved','unknown'),
                        'original native POST requires the committed immutable reservation')
                    self.native_entered.set()
                    self.case.assertTrue(self.native_release.wait(600),'configured synthetic long-native barrier must be released and joined')
                # While held, the native server has accepted the original POST
                # but has not exposed its effect. Competitor GET cannot invent
                # or adopt a card; original UUID/reservation remain authoritative.
                lane.messages[mid]=lane.raw_message(mid,base.AGENT_APP,'app',data['msg_type'],data['content'],root or mid)
                return {'message_id':mid}
            if method=='PATCH' and path.startswith('/open-apis/im/v1/messages/'):
                mid=path.rsplit('/',1)[-1];lane=next(x for x in self.lanes.values() if mid in x.messages)
                lane.messages[mid]['body']['content']=data['content']
                self.effects.append((lane.name,method,path,mid,copy.deepcopy(data)))
                return {'message_id':mid}
            raise AssertionError('unapproved low native HTTP method/path')
        finally:
            with self.mutex:self.inflight-=1

    def notice_rows(self,binding):
        with store.Store(self.db_path) as db:
            # SQL12 global list API stays immutable. This fixture reads only
            # whitelisted metadata under the real Store lock, scoped before its
            # bound; production coordinator owns its rotated scoped read.
            with db._lock:
                rows=db.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? ORDER BY source_id LIMIT 256',
                    (binding,)).fetchall()
                return tuple(db._delivery_notice_record(row) for row in rows)

    def row(self,binding,source):
        with store.Store(self.db_path) as db:return db.get_delivery_notice(binding,source['id'])

    def load_root(self):
        # A missing optional websockets package is a low external transport seam,
        # never a replacement Hostd/Worker/notice/Round/Store.
        absent=object();prior=sys.modules.get('websockets',absent)
        if importlib.util.find_spec('websockets') is None:
            module=types.ModuleType('websockets')
            def unconfigured(*args,**kwargs):raise AssertionError('unconfigured synthetic WebSocket transport')
            module.connect=unconfigured;sys.modules['websockets']=module
        try:return importlib.import_module('hostd.__main__')
        finally:
            if prior is absent:sys.modules.pop('websockets',None)
            else:sys.modules['websockets']=prior

    async def root(self):
        hd=self.load_root();self.hd=hd;reg=registry.Registry()
        if not self.transport_patches:
            for target,name,value in ((asyncio,'create_subprocess_exec',self.sdk),
                                      (hd.relay_feed,'_connect',lambda url:Socket(self,url))):
                patch=mock.patch.object(target,name,value);patch.start();self.case.addCleanup(patch.stop)
                self.transport_patches.append(patch)
        lane=self.lanes['initial'];app=lane.cfg['agents'][SYNC_PUB]
        reg.bindings[lane.name]=registry.Binding(lane.name,lane.env.config,lane.env.state_dir,
            lane.channel,lane.chat,base.AGENT_APP,*self.first_profile,'https://relay.test')
        self.host=hd.Hostd(reg,self.run_dir/'status.json',state_db=self.db_path,
            migrate_bot_readers=True,onboarding_config=self.real_input.config)
        self.case.addAsyncCleanup(self.close)
        self.host.http_pool.factory=lambda host,**kw:Connection(self,host,**kw)
        self.host.app_lock_dir=self.run_dir/'app-locks'
        self.install_worker(lane)
        self.host._running=True
        self.real_input.world.now=self.now
        # Preserve the actual factory and all its protected catalog/SQL checks.
        # A nonexistent synthetic user unit is observed through the real
        # ProcessOps parser with only systemctl/journal wire substituted.
        with self.real_input.factory_patch():
            delegate=OnboardingRuntime.create.__func__
            async def lower_create(cls,config,db,**kwargs):
                kwargs['runtime_operations']=ProcessOps(runner=self.system)
                return await delegate(cls,config,db,**kwargs)
            with mock.patch.object(OnboardingRuntime,'create',classmethod(lower_create)):
                await self.host.start_onboarding()
        self.case.assertIs(type(self.host.onboarding),OnboardingRuntime)
        self.case.assertNotIn(keys.APP,self.host.onboarding.records)
        self.case.assertNotIn(keys.PUB,self.host.onboarding.effects.specs)
        self.case.assertFalse((self.run_dir/'foreign.env').exists())
        return self.host

    def system(self,argv,**kwargs):
        self.case.assertIn(Path(argv[0]).name,('systemctl','journalctl'))
        # Low missing-unit response, never a successful runtime verdict.
        return subprocess.CompletedProcess(argv,1,'','')

    def install_worker(self,lane):
        worker=self.host.workers[lane.name]
        worker.client_factory=lane.clients  # real build_clients; only runner/http below are fake
        worker.clock=self.utc_datetime

    def ready(self):
        h=self.host
        self.case.assertTrue(callable(getattr(h,'notice_wake_once',None)),
            'missing actual daemon persisted notice wake/Worker wiring is intended tests-first RED')
        self.case.assertIs(type(h.workers['initial']),binding_worker.Worker,
            'initial Worker factory must share canonical clients/Round/Store classes')
        h.notice_clock=lambda:self.now
        self.case.assertIs(h.workers['initial'].http_pool,h.http_pool)
        self.case.assertIs(h.workers['initial'].scheduler,h.scheduler)
        self.case.assertEqual(h.workers['initial'].claim_relay_pubkey,keys.PIN)

    async def start_worker(self,lane):
        h=self.host;h._running=True
        tasks=h.binding_tasks.setdefault(lane.name,{})
        if tasks.get('worker') is None or tasks['worker'].done():
            tasks['worker']=h._spawn('worker',lane.name,lambda:h.worker(lane.name))

    async def drive(self,lane,source=None,phase='buzz'):
        h=self.host
        if source is not None:
            self.case.assertEqual(self.hd.relay_feed._event(source,lane.channel),source)
            await h.on_relay(lane.name,source)
        else:h.mark(lane.name,phase,catch_up=phase=='feishu')
        async with h.binding_locks[lane.name]:
            dirty=h.dirty[lane.name];h.dirty[lane.name]=set()
            targets=h.threads[lane.name];h.threads[lane.name]=set()
            result = await h._round(lane.name,dirty - {'notice'} if dirty - {'notice'} else dirty,targets)
            if source is not None:
                self.case.assertIsNone(self.row(lane.name,source), 'ordinary sync only queues untrusted IDs')
                await h._round(lane.name,{'notice'},set())
            return result

    async def wait(self,condition,label,seconds=240):
        deadline=asyncio.get_running_loop().time()+seconds
        while not condition():
            if asyncio.get_running_loop().time()>=deadline:self.case.fail('actual rendezvous missing: '+label)
            await asyncio.sleep(.02)

    async def sdk(self,*argv,**kwargs):
        self.case.assertEqual(Path(argv[1]).name,'feishu_feed.py')
        self.case.assertEqual(argv[2],base.AGENT_APP)
        self.case.assertEqual(tuple(argv[3:]),tuple(map(str,self.first_profile)))
        child=await self.original_spawn(sys.executable,'-c',
            'import json,sys;print(json.dumps({"type":"_connected","chat_id":""}),flush=True);sys.stdin.buffer.read()',
            stdin=asyncio.subprocess.PIPE,stdout=kwargs['stdout'],stderr=kwargs['stderr'],
            env={'PATH':'/usr/bin:/bin'})
        self.children.append(child);return child

    async def add_dynamic(self):
        h=self.host;lane=self.setup_lane('JOIN-1234abcd',OTHER_CHANNEL,OTHER_CHAT)
        # Same already-owned sync app/pair; the new approved mirror config is
        # exact request path, never a callback/public path or foreign credentials.
        path=Path(h.onboarding_config.binding_dir)/lane.name/'config.json'
        agent=lane.cfg['agents'][SYNC_PUB]
        agent['lark_config_dir'],agent['lark_data_dir']=self.first_profile
        Lane.write(path,json.dumps(lane.cfg));lane.env.config=path
        db=h.runtime_store
        db.create_join(lane.name,SYNC_PUB,base.OWNER_PK,base.AGENT_APP,lane.chat,kind='new_binding',now=self.now)
        generation=db.reserve_join_card(lane.name,now=self.now)
        db.finish_join_card(lane.name,generation,'om_s7approved',now=self.now)
        self.case.assertTrue(db.decide_join(lane.name,'ev_s7approval',base.OWNER_PK,base.AGENT_APP,
            'om_s7approved',generation,approved=True,now=self.now))
        db.ensure_effect_plan(lane.name,lane.channel,lane.name,lane.cfg['mirror_env_file'],path,now=self.now)
        db.set_effect_mirror(lane.name,base.MIRROR_PK,now=self.now)
        plan=db.effect_plan(lane.name);row=db.join_request(lane.name)
        # Genuine root registration guard; only real relay's wire connect may
        # be replaced in snapshot setup. It starts actual worker/app tasks.
        await h.register_runtime_binding(row,plan)
        self.install_worker(lane)
        self.case.assertIs(type(h.workers[lane.name]),binding_worker.Worker)
        self.case.assertIs(h.workers[lane.name].http_pool,h.http_pool)
        self.case.assertIs(h.workers[lane.name].scheduler,h.scheduler)
        self.case.assertEqual(h.workers[lane.name].claim_relay_pubkey,keys.PIN)
        await self.wait(lambda:self.binding_state(lane.name)=='active','dynamic actual local binding readiness')
        return lane

    def binding_state(self,name):
        with store.Store(self.db_path) as db:
            return next(row['status'] for row in db.bindings() if row['binding_id']==name)

    async def close(self):
        self.native_release.set()
        for task in self.main_tasks:
            if not task.done():task.cancel()
        if self.main_tasks:await asyncio.gather(*self.main_tasks,return_exceptions=True)
        if self.host is not None:await self.host._shutdown()
        self.case.assertEqual(self.inflight,0)
        self.case.assertTrue(all(child.returncode is not None for child in self.children))


class DeliveryNoticeWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_commit_failure_does_not_advance_ordinary_cursor(self):
        w=WiringWorld(self);h=await w.root();w.ready();lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        with store.Store(w.db_path) as db:before=StateAdapter(db,lane.name,lane.env.state_dir).load().buzz_since
        source=lane.source('queue-write-unavailable');await h.on_relay(lane.name,source)
        async with h.binding_locks[lane.name]:
            with mock.patch.object(store.Store,'enqueue_notice_hints',side_effect=OSError('synthetic disk write failure')):
                with self.assertRaises(OSError):await h._round(lane.name,{'buzz'},set())
        with store.Store(w.db_path) as db:
            self.assertEqual(StateAdapter(db,lane.name,lane.env.state_dir).load().buzz_since,before)
            self.assertEqual(db.pending_notice_hints(lane.name),[])
        await h._shutdown();h=await w.root();w.ready()
        async with h.binding_locks[lane.name]:await h._round(lane.name,{'buzz'},set())
        with store.Store(w.db_path) as db:
            self.assertIn(source['id'],[r['source_id'] for r in db.pending_notice_hints(lane.name,limit=256)])
        self.assertFalse(w.effects)

    async def test_missing_candidate_does_not_starve_due_durable_notice(self):
        w=WiringWorld(self);h=await w.root();w.ready();lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        source=lane.source('valid-waiting-notice');await w.drive(lane,source)
        with store.Store(w.db_path) as db:db.enqueue_notice_hints(lane.name,['0'*64],now=w.now)
        w.now+=60
        async with h.binding_locks[lane.name]:await h._round(lane.name,{'notice'},set())
        self.assertEqual(w.row(lane.name,source).state,'noticed')
        with store.Store(w.db_path) as db:self.assertEqual(db.pending_notice_hints(lane.name)[0]['source_id'],'0'*64)

    async def test_full_candidate_queue_holds_cursor_but_still_drains_verified_noncandidate(self):
        w=WiringWorld(self);h=await w.root();w.ready();lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        with store.Store(w.db_path) as db:
            db.enqueue_notice_hints(lane.name,[lane.root_event['id']],now=w.now-10)
            db.enqueue_notice_hints(lane.name,[f'{i:064x}' for i in range(4095)],now=w.now-9)
            before=StateAdapter(db,lane.name,lane.env.state_dir).load().buzz_since
        source=lane.source('after-full-queue')
        await h.on_relay(lane.name,source)
        async with h.binding_locks[lane.name]:
            rep=await h._round(lane.name,{'buzz'},set())
            self.assertEqual(rep['errors'],0)
            self.assertTrue(rep['hostd']['notice']['backlog'])
            self.assertEqual(w.binding_state(lane.name),'active')
            with store.Store(w.db_path) as db:
                self.assertEqual(StateAdapter(db,lane.name,lane.env.state_dir).load().buzz_since,before)
            await h._round(lane.name,{'notice'},set())
            with store.Store(w.db_path) as db:
                self.assertIsNone(db.conn.execute('SELECT 1 FROM notice_hint WHERE source_id=?',(lane.root_event['id'],)).fetchone())
            await h._round(lane.name,{'buzz'},set())
            with store.Store(w.db_path) as db:
                self.assertIsNotNone(db.conn.execute('SELECT 1 FROM notice_hint WHERE source_id=?',(source['id'],)).fetchone())
        self.assertFalse(w.effects)

    async def test_unobserved_durable_candidate_survives_restart_and_actual_notice_worker(self):
        w=WiringWorld(self);h=await w.root();w.ready();lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        source=lane.source('queued-before-process-exit')
        await h.on_relay(lane.name,source)
        async with h.binding_locks[lane.name]:
            await h._round(lane.name,{'buzz'},set())
        self.assertIsNone(w.row(lane.name,source))
        with store.Store(w.db_path) as db:
            self.assertIn(source['id'],[r['source_id'] for r in db.pending_notice_hints(lane.name,limit=256)])
        self.assertFalse(w.effects)
        await h._shutdown()
        h=await w.root();w.ready()
        await w.start_worker(lane)
        await h.notice_wake_once()
        await w.wait(lambda:w.row(lane.name,source) is not None,'persisted candidate actual notice worker')
        self.assertEqual(w.row(lane.name,source).state,'waiting')
        self.assertFalse(w.effects)

    async def test_unreadable_current_claim_does_not_observe_despite_old_active_row(self):
        w=WiringWorld(self);h=await w.root();w.ready();lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        source=lane.source('claim-read-incomplete')
        await h.on_relay(lane.name,source)
        async with h.binding_locks[lane.name]:
            await h._round(lane.name,{'buzz'},set())
            with mock.patch.object(delivery_mapping.MappedHostdRound,'check_claims',return_value='unreadable'):
                await h._round(lane.name,{'notice'},set())
        self.assertIsNone(w.row(lane.name,source))
        self.assertFalse(w.effects)
        with store.Store(w.db_path) as db:
            self.assertTrue(db.pending_notice_hints(lane.name))

    async def test_actual_initial_dynamic_binding_foreign_source_deadline_reopen_and_native_recovery(self):
        w=WiringWorld(self);h=await w.root();w.ready()
        lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        self.assertEqual(w.binding_state(lane.name),'active')
        source=lane.source('foreign-one')
        self.assertNotIn(keys.PUB,lane.cfg['agents'])
        self.assertNotIn(keys.APP,h.app_profiles)
        self.assertEqual(h.onboarding.records.keys(),{base.AGENT_APP})
        # Actual root refresh installs an empty own-outlet set: human-only
        # routing is real; foreign source observation must happen BEFORE it.
        await w.drive(lane,source)
        self.assertEqual(h.workers[lane.name].outlet_specs,{})
        self.assertNotIn(keys.PUB,h.onboarding.effects.specs)
        with store.Store(w.db_path) as db:
            self.assertEqual(next(b for b in db.bindings() if b['binding_id']==lane.name)['status'],'active',
                'foreign public bot without local responsibility must not degrade the A sync binding')
        row=w.row(lane.name,source)
        self.assertIsNotNone(row,'pre-filter foreign kind9 hint must reach real observe')
        self.assertEqual((row.first_observed_at,row.deadline_at),(w.now,w.now+60))
        self.assertEqual(row.state,'waiting');self.assertFalse(w.effects)
        self.assertEqual(h.workers[lane.name].notice_cursor,source['id'],
            'Worker must retain the coordinator metadata cursor across fresh per-run lifetimes')
        self.assertTrue(any(any(f.get('ids')==[source['id']] for f in batch) for batch in lane.query_calls))
        original=(row.notice_uuid,row.first_observed_at,row.notice_content_sha256)
        # Restart a real root/Worker from the same SQL/files. No repeated feed
        # event may be necessary to reach the persisted due wake.
        await h._shutdown()
        old=h;h=await w.root()
        w.ready();w.now+=59
        self.assertEqual(h.store_path,old.store_path)
        await h.notice_wake_once()
        self.assertFalse(w.effects)
        before_forward=(len(lane.world.buzz_calls),len(lane.events))
        w.now+=1
        await w.start_worker(lane)
        await h.notice_wake_once()
        await w.wait(lambda:w.row(lane.name,source).state=='noticed','persisted60s notice native effect')
        current=w.row(lane.name,source)
        self.assertEqual((current.notice_uuid,current.first_observed_at,current.notice_content_sha256),original)
        self.assertEqual(len([e for e in w.effects if e[1]=='POST']),1)
        self.assertEqual(current.root_message_id,lane.root_mid)
        async with h.binding_locks[lane.name]:
            pass
        timer_phase=h.status['bindings'][lane.name]['last_dirty']
        self.assertEqual(timer_phase,['notice'],'timer-only wake must not repeat forwarding phases')
        self.assertEqual(len(lane.events),before_forward[1],'notice must not publish any Buzz mapping/event')
        foreign_mid=lane.delivered(source)
        await h.on_feishu(base.AGENT_APP,{'app':base.AGENT_APP,'type':'im.message.receive_v1',
            'chat_id':lane.chat,'message_id':foreign_mid,'root_id':lane.root_mid},{lane.chat:lane.name})
        await w.wait(lambda:w.row(lane.name,source).state=='recovered','native arrival rechecks original signed proof')
        self.assertEqual([e[3] for e in w.effects if e[1]=='PATCH'],[current.notice_message_id])
        self.assertFalse(any(e[1]=='POST' and e[3]!=current.notice_message_id for e in w.effects))
        # Exact current approved dynamic registration inherits same pool/pin.
        dynamic=await w.add_dynamic()
        extra=dynamic.source('foreign-dynamic')
        await h.on_relay(dynamic.name,extra)
        await w.wait(lambda:w.row(dynamic.name,extra) is not None,'dynamic real observer assembly')
        self.assertEqual(w.row(dynamic.name,extra).state,'waiting')
        async with h.binding_locks[dynamic.name]:
            pass
        self.assertEqual(h.workers[dynamic.name].notice_cursor,extra['id'])
        self.assertNotIn((dynamic.name,keys.PUB),h.outlet_tasks)
        self.assertFalse(any(Path(p).name=='foreign.env' for p in h.app_profiles.values() if isinstance(p,str)))

    async def test_committed_native_competing_stores_other_binding_progress_and_repeated_root_cancel_join(self):
        w=WiringWorld(self);h=await w.root();w.ready()
        lane=w.lanes['initial']
        await w.drive(lane,phase='members')
        self.assertEqual(w.binding_state(lane.name),'active')
        source=lane.source('blocked-first')
        await w.drive(lane,source)
        self.assertEqual(w.row(lane.name,source).state,'waiting')
        dynamic=await w.add_dynamic();other=dynamic.source('independent-second')
        await h.on_relay(dynamic.name,other)
        await w.wait(lambda:w.row(dynamic.name,other) is not None,'actual second binding observation')
        w.now+=60;w.block_binding=lane.name
        # Only this synthetic long-barrier method changes the configurable
        # pool budget. Product default90 and signed Source freshness stay intact.
        h.http_pool.timeout=600
        await w.start_worker(lane);await h.notice_wake_once()
        reached=await asyncio.wait_for(asyncio.to_thread(w.native_entered.wait,240),241)
        self.assertTrue(reached,'must enter original native POST after committed immutable reservation')
        saved=w.row(lane.name,source);self.assertIn(saved.state,('reserved','unknown'))
        # Independent connection and actual fresh Round/Coordinator, no fake
        # reserve boolean or callback. It must not borrow the original POST.
        spec=importlib.util.find_spec('hostd.delivery_notice');self.assertIsNotNone(spec)
        module=importlib.import_module('hostd.delivery_notice')
        db=store.Store(w.db_path)
        self.addCleanup(db.close)
        independent_scheduler=Scheduler()
        independent_pool=HttpPool(scheduler=independent_scheduler,timeout=600,
            connection_factory=lambda host,**kw:Connection(w,host,**kw))
        self.addCleanup(independent_scheduler.close)
        self.addCleanup(independent_pool.close)
        # Another coordinator/connection does not share the original POST's
        # app/chat lane lock. Its own real pool/parser still authenticates from
        # the same protected A profile; no foreign profile or second SDK feed.
        adapter=StateAdapter(db,lane.name,lane.env.state_dir)
        state=adapter.load();clients=lane.clients(lane.cfg,lane.env.base_env,
            http_pool=independent_pool,scheduler=independent_scheduler)
        holder={}
        def persist():adapter.save(holder['round'].state,now=w.now)
        run=delivery_mapping.MappedHostdRound(lane.cfg,clients,state,gs._new_report(),w.utc_datetime(),persist,
            reader_namespace=base.AGENT_APP,store=db,binding_id=lane.name,
            claim_relay_pubkey=keys.PIN,auth_clock=w.utc_datetime)
        holder['round']=run
        for call in (run.verify_identities,run.load_people,run.load_directory,run.verify_desk):await thread_call(call)
        competitor=module.DeliveryNoticeCoordinator(db,lane.name,run,
            utc_clock=lambda:w.now,monotonic_clock=asyncio.get_running_loop().time)
        self.addAsyncCleanup(competitor.close)
        try:
            await competitor.scan_once()
            self.assertEqual(len([e for e in w.effects if e[0]==lane.name and e[1]=='POST']),1)
            self.assertEqual(w.row(lane.name,source).notice_uuid,saved.notice_uuid)
            await h.notice_wake_once()
            before = h.status['bindings'][dynamic.name]['runs']
            effects_before=len(w.effects)
            h.mark(dynamic.name,'feishu',catch_up=True)
            await w.wait(lambda:h.status['bindings'][dynamic.name]['runs']>before, 'ordinary binding progresses while notice lane is occupied')
            self.assertEqual(len(w.effects),effects_before)
            self.assertIn(w.row(lane.name,source).state,('reserved','unknown'))
            # Actual root lifecycle owns repeated-cancel cleanup, with genuine
            # supervised relay/SDK wire seams installed before any tasks start.
            root_task=asyncio.create_task(h.main());w.main_tasks.append(root_task)
            await w.wait(lambda:root_task.get_coro().cr_frame is not None
                and root_task.get_coro().cr_frame.f_locals.get('sources') is not None,
                'actual main assembled supervised jobs before cancellation')
            await w.wait(lambda:bool(w.children) and h.status['apps'][base.AGENT_APP]['feishu']=='connected',
                'actual SDK child connected',seconds=30)
            borrowed=h.runtime_store
            with self.assertRaises(ValueError):
                with w.hd.app_lock(base.AGENT_APP,h.app_lock_dir):pass
            root_task.cancel()
            await asyncio.sleep(.05);root_task.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(root_task.done(),'actual main must join original notice native IO before feeds/resources')
            self.assertFalse(h.http_pool.closed)
            self.assertIsNotNone(borrowed.conn)
            self.assertTrue(all(child.returncode is None for child in w.children))
            w.native_release.set()
            await asyncio.gather(root_task,return_exceptions=True)
            self.assertEqual(w.inflight,0)
            self.assertTrue(h.http_pool.closed)
            self.assertIsNone(borrowed.conn)
            self.assertTrue(all(child.returncode is not None for child in w.children))
            with w.hd.app_lock(base.AGENT_APP,h.app_lock_dir):pass
            self.assertEqual(len([e for e in w.effects if e[0]==lane.name and e[1]=='POST']),1)
            self.assertIn(w.row(lane.name,source).state,('reserved','unknown','noticed'))
        finally:
            w.native_release.set()
            await competitor.close();db.close();independent_pool.close();independent_scheduler.close()
