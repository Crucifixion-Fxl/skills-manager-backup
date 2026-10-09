"""Finite own-bot remote reactions: real domain/HTTP adapters, offline boundaries.

Portable runs use the existing labelled AES primitive fixture; dependency-only
control executes actual AESGCM. No verified proof DTO, runtime, SQL method or
signature parser is mocked. Only relay HTTP, HTTPSConnection and AES are seams.
"""
import asyncio
import concurrent.futures
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

TESTS=Path(__file__).resolve().parent
sys.path[:0]=[str(TESTS),str(TESTS.parent/'scripts')]
import test_hostd_remote_manager as manager_fixture
import test_hostd_remote_proofs as proof_fixture
import test_hostd_remote_mapping as signed_fixture
from hostd import store
from hostd.agent_signed_reads import AgentReadFailure
from hostd.bot_clients import BotLarkCli,READ_SCOPE_GROUPS
from hostd.http_pool import HttpPool,PoolError,HOST,TOKEN_PATH
from hostd.delivery_mapping import message_card
from hostd.remote_manager import RemoteManager
from hostd.remote_mapping import RemoteMappingContext
from hostd.remote_proofs import RemoteProofs
from hostd.remote_runtime import RemoteRuntime
import buzz_feishu_group_sync as gs

CHANNEL,CHAT,APP,ORIGIN,NOW=proof_fixture.CHANNEL,proof_fixture.CHAT,proof_fixture.APP,proof_fixture.ORIGIN,proof_fixture.NOW
PUB,OWNER=proof_fixture.PUB,proof_fixture.OWNER
CAPS=('message','reaction_add','reaction_remove')
WRITE='im:message.reactions:write_only'
SEND='im:message:send_as_bot'
EMOJI='DONE'
PADDED='R'*85+'A=='
BODY_CANARY='SYNTHETIC_BODY_DO_NOT_CACHE'


class Response:
    def __init__(self,status,data):
        self.status=status;self.will_close=False;self.body=json.dumps(data).encode()
    def getheader(self,name):return str(len(self.body)) if name=='Content-Length' else None
    def read1(self,count):raw,self.body=self.body[:count],self.body[count:];return raw


class Connection:
    """Only HTTPSConnection's native request/response boundary is synthetic."""
    sock=None
    def __init__(self,world):self.world=world;self.response=None
    def request(self,method,path,body=None,headers=None):
        if path==TOKEN_PATH:
            data=json.loads(body);self.world.assertEqual(data['app_id'],APP)
            self.response=Response(200,{'code':0,'tenant_access_token':'offline-reaction-token','expire':3600})
        else:
            self.world.assertEqual(headers['Authorization'],'Bearer offline-reaction-token')
            self.response=self.world.native(method,path,body)
    def getresponse(self):
        if isinstance(self.response,BaseException):raise self.response
        return self.response
    def close(self):pass


class World(manager_fixture.World):
    def __init__(self,case):
        super().__init__(case)
        self.scopes.update((SEND,WRITE));self.scope_rows=None
        self.reactions=[];self.mutations=[];self.reaction_reads=[]
        self.read_override=None;self.permission_error=False;self.lose=False;self.timeout_write=False
        self.apply_mutation=True;self.reaction_id='reaction-original'
        self.block=False;self.entered=threading.Event();self.release=threading.Event();self.completed=threading.Event()
        self.on_native=None;self.after_mutation=None;self.current_event=None
        self.pool=HttpPool(connection_factory=self.connect,timeout=3)
        case.addCleanup(self.pool.close);case.addCleanup(self.release.set)
        self.bot=BotLarkCli(APP,self.cfg,self.data,base_env={'HOME':str(self.root)},
            http_pool=self.pool,chat_id=CHAT,runner=self.forbid_cli)
    def forbid_cli(self,*args,**kwargs):raise AssertionError('reaction slice cannot borrow a CLI or personal identity')
    def connect(self,host,*,timeout,context):
        self.assertEqual(host,HOST);self.assertGreater(timeout,0);self.assertNotEqual(context.verify_mode,0)
        return Connection(self)
    def on_loop(self,action):
        result=concurrent.futures.Future()
        def apply():
            try:result.set_result(action())
            except BaseException as exc:result.set_exception(exc)
        self.loop.call_soon_threadsafe(apply)
        return result.result(timeout=3)
    def native(self,method,raw_path,body):
        parsed=urlsplit(raw_path);path=parsed.path
        params={k:v[-1] for k,v in parse_qs(parsed.query,keep_blank_values=True).items()}
        data=json.loads(body) if body else None
        self.api_calls.append((method,path,params,data))
        if self.on_native:self.on_native(method,path)
        if path=='/open-apis/application/v6/scopes':
            rows=self.scope_rows if self.scope_rows is not None else [{'scope_name':s,'scope_type':'tenant','grant_status':self.scope_grant_status} for s in sorted(self.scopes)]
            return Response(200,{'code':0,'data':{'scopes':rows}})
        if path.endswith('/members/list'):
            if self.on_member:
                action,self.on_member=self.on_member,None;action()
            return Response(200,{'code':0,'data':{'users':[],
                'bots':[] if self.bot_absent else [{'member_id':'ou_own','app_id':APP}],
                'user_total':0,'bot_total':0 if self.bot_absent else 1,'truncations':[],
                'has_more':self.members_incomplete,'page_token':'same'}})
        if path=='/open-apis/im/v1/chats':
            return Response(200,{'code':0,'data':{'items':[{'chat_id':CHAT}],'has_more':False}})
        if path.endswith('/reactions') or '/reactions/' in path:
            mid=unquote(path.split('/')[5]);self.assertEqual(mid,'om_root')
            if method=='GET':
                self.reaction_reads.append((mid,dict(params)))
                rows=copy.deepcopy(self.reactions)
                result=self.read_override(params,rows) if self.read_override else {'items':rows,'has_more':False}
                if isinstance(result,BaseException):return result
                return Response(200,{'code':0,'data':result})
            self.assertIn(method,('POST','DELETE'))
            action='reaction_add' if method=='POST' else 'reaction_remove'
            self.assertIsNotNone(self.current_event)
            observed=self.on_loop(lambda:(self.db.remote_delivery_by_source(self.target_id,self.current_event['id'],action),self.db.conn.in_transaction))
            row,in_transaction=observed
            self.assertIsNotNone(row);self.assertEqual(row.status,'unknown');self.assertFalse(in_transaction)
            self.mutations.append((method,path,copy.deepcopy(data)))
            self.entered.set()
            try:
                if self.block:self.assertTrue(self.release.wait(3))
                if self.permission_error:return Response(403,{'code':99991672,'msg':'SYNTHETIC_PRIVATE_ERROR'})
                if method=='POST':
                    self.assertEqual(data,{'reaction_type':{'emoji_type':EMOJI}})
                    if self.apply_mutation:self.reactions=[self.reaction_row(self.reaction_id)]
                    result=self.reaction_row(self.reaction_id)
                else:
                    rid=unquote(path.rsplit('/',1)[1]);self.assertEqual(rid,self.reaction_id)
                    self.assertIsNone(data)
                    if self.apply_mutation:self.reactions=[]
                    result=self.reaction_row(rid)
                if self.after_mutation:self.after_mutation()
                if self.timeout_write:return TimeoutError('SYNTHETIC_PRIVATE_ERROR')
                return OSError('SYNTHETIC_PRIVATE_ERROR') if self.lose else Response(200,{'code':0,'data':result})
            finally:self.completed.set()
        if path=='/open-apis/im/v1/messages':
            rows=list(self.messages.values())
            if params.get('container_id_type')=='thread':rows=[r for r in rows if r.get('thread_id')==params.get('container_id')]
            return Response(200,{'code':0,'data':{'items':rows,'has_more':self.incomplete,'page_token':'same'}})
        if path.startswith('/open-apis/im/v1/messages/'):
            mid=path.rsplit('/',1)[1]
            return Response(200,{'code':0,'data':{'items':[self.messages[mid]] if mid in self.messages else []}})
        raise AssertionError('unexpected finite native HTTP method')
    @staticmethod
    def reaction_row(rid,app=APP):
        return {'reaction_id':rid,'reaction_type':{'emoji_type':EMOJI},
                'operator':{'operator_type':'app','operator_id':app},'action_time':str(NOW*1000)}
    def own_root(self):
        event=self.event();self.card(event,app=APP)
        self.messages['om_root']['body']['content']=message_card(event,self.actual_record.name,event['content'],ORIGIN,CHANNEL)
        self.messages['om_root'].update(root_id='om_root',thread_id='omt_om_root')
        return event
    def reaction(self,root,*,kind=7,key=signed_fixture.KEY,refs=None,text='✅'):
        return self.event(kind=kind,key=key,tags=refs if refs is not None else [['e',root['id']]],text='' if kind==5 else text)


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w=World(self);self.w.loop=asyncio.get_running_loop()
        self.path=self.w.root/'metadata'/'reactions.db';self.db=store.Store(self.path)
        self.addCleanup(lambda:self.db.close());self.w.db=self.db
        self.db.register_agent(PUB,owner_pubkey=OWNER,app_id=APP,config_path=str(self.w.env),now=NOW)
        self.w.target_id=store.Store._remote_digest([PUB,CHANNEL]);self.consumer=self.w.consumer()
        self.root=self.w.own_root()
    def pending(self,result):
        self.assertEqual(result.status,'pending');self.assertIn('怎么解决',result.notice);self.assertIn('复制给 AI',result.notice)
        value=json.dumps(result.readback(),ensure_ascii=False);self.assertFalse(result.readback()['live_verified'])
        for canary in (BODY_CANARY,signed_fixture.KEY,'SYNTHETIC_APP_SECRET','SYNTHETIC_PRIVATE_ERROR','Traceback','offline-reaction-token'):
            self.assertNotIn(canary,value)
    def context(self):return RemoteMappingContext(self.w.target,reader=self.w.reader,bot_client=self.w.bot,clock=lambda:self.w.now,link_base=ORIGIN)
    async def request(self,caps=CAPS):
        self.assertIn('capabilities',inspect.signature(self.consumer.verify).parameters,
                      'genuine missing feature: actual proof producer cannot request reaction mutation capabilities')
        return await self.consumer.verify(self.w.target,capabilities=caps)
    async def grant(self,caps=CAPS):
        result=await self.request(caps);self.assertEqual(result.status,'verified')
        self.assertEqual(result.authorization.evidence.capabilities,caps)
        grant=self.db.activate_remote_grant(result.authorization.evidence,expected_revision=0,now=self.w.now)
        self.assertTrue(self.db.refresh_remote_proof(grant.target_id,result.authorization.evidence,
            revision=grant.revision,scope_hash=grant.scope_hash,now=self.w.now))
        return grant
    def runtime(self):return RemoteRuntime(self.db,record=self.w.actual_record,reader=self.w.reader,bot=self.w.bot,
        proofs=self.consumer,link_base=ORIGIN,clock=lambda:self.w.now)
    async def deliver(self,event,grant,runtime=None,**pins):
        self.w.current_event=event
        return await (runtime or self.runtime()).deliver(self.w.target,event,target_id=pins.get('target_id',grant.target_id),
            revision=pins.get('revision',grant.revision),scope_hash=pins.get('scope_hash',grant.scope_hash))
    def row(self,event,action):return self.db.remote_delivery_by_source(self.w.target_id,event['id'],action)
    def reopen(self):
        self.db.close();self.db=store.Store(self.path);self.w.db=self.db
    async def added(self):
        grant=await self.grant();event=self.w.reaction(self.root)
        self.assertEqual((await self.deliver(event,grant)).status,'acked')
        row=self.row(event,'reaction_add');self.assertEqual((row.sender_app_id,row.message_id,row.reaction_id,row.emoji),(APP,'om_root',self.w.reaction_id,EMOJI))
        return grant,event,row


class ActualControls(Base):
    async def test_actual_read_proof_catalog_signed_reader_and_http_pool_default_message(self):
        result=await self.consumer.verify(self.w.target);self.assertEqual(result.status,'verified')
        self.assertEqual(result.authorization.evidence.capabilities,('message',))
        grant=self.db.activate_remote_grant(result.authorization.evidence,expected_revision=0,now=NOW)
        self.assertEqual(grant.evidence.app_id,APP);self.assertEqual(self.db.bindings(),[])
        self.assertFalse(self.w.cli_calls);self.assertFalse(self.w.mutations)
    async def test_actual_original_mapping_and_signed_reaction_tamper_rejection(self):
        self.assertEqual((await self.context().resolve_buzz(self.root['id'])).status,'verified')
        event=self.w.reaction(self.root);event['sig']='0'*128
        with self.assertRaises(AgentReadFailure):await self.w.reader.query([{'kinds':[7],'ids':[event['id']],'#h':[CHANNEL],'limit':2}])
        self.assertFalse(self.w.mutations)
    async def test_actual_native_own_reactions_complete_wrong_operator_and_padded_id(self):
        self.w.reactions=[self.w.reaction_row(PADDED)]
        result=await asyncio.to_thread(self.w.bot.own_reactions,'om_root',EMOJI,expected_reaction_id=PADDED)
        self.assertTrue(result.complete);self.assertEqual(result.reaction_ids,(PADDED,))
        self.w.reactions=[self.w.reaction_row(PADDED,'cli_other')]
        result=await asyncio.to_thread(self.w.bot.own_reactions,'om_root',EMOJI,expected_reaction_id=PADDED)
        self.assertFalse(result.complete)
    async def test_actual_http_permission_error_fixed_notice_and_no_retry(self):
        self.w.scope_rows=[]
        def deny(method,path):
            if path=='/open-apis/application/v6/scopes':return Response(403,{'code':99991672})
        native=self.w.native
        self.w.native=lambda method,path,body:deny(method,path) or native(method,path,body)
        with self.assertRaises(PoolError) as caught:await asyncio.to_thread(self.w.bot.bot_scopes)
        self.assertTrue(caught.exception.definite);self.assertIn('怎么解决',str(caught.exception));self.assertFalse(self.w.mutations)
    async def test_actual_crypto_control_or_explicit_portable_aes_boundary(self):
        if proof_fixture.AESGCM is None:
            self.assertIs(proof_fixture.secret_module().AESGCM,proof_fixture.DecryptFixture)
            self.assertEqual((await self.consumer.verify(self.w.target)).status,'verified')
        else:
            world=proof_fixture.World(self,actual_crypto=True)
            world.scopes.add(SEND)
            self.assertEqual((await world.consumer().verify(world.target)).status,'verified')


class ReactionFeature(Base):
    async def test_default_message_requires_actual_send_permission_not_read_only(self):
        self.w.scopes=set(READ_SCOPE_GROUPS)
        result=await self.consumer.verify(self.w.target);self.pending(result)
        self.assertFalse(self.w.mutations)
    async def test_request_scope_alternatives_overlap_and_invalid_capability_tuples(self):
        for alternative in (WRITE,'im:message'):
            self.w.scopes=set(READ_SCOPE_GROUPS)|{SEND,alternative}
            self.assertEqual((await self.request()).status,'verified')
        self.w.scopes.update((WRITE,'im:message'))
        self.assertEqual((await self.request()).status,'verified')
        for caps in (('message',),('edit',),('message','edit'),('reaction_add',),('reaction_remove',),('message','reaction_add'),('reaction_add','reaction_remove')):
            with self.subTest(valid_caps=caps):
                result=await self.request(caps);self.assertEqual(result.status,'verified')
                self.assertEqual(result.authorization.evidence.capabilities,caps)
        for caps in ((),('unknown',),('reaction_add','message'),('message','reaction_add','reaction_add'),['message'],('edit','message')):
            with self.subTest(caps=caps):self.pending(await self.request(caps))
    async def test_default_message_send_alternatives_from_official_primary_docs(self):
        for alternative in ('im:message',SEND,'im:message:send'):
            self.w.scopes=set(READ_SCOPE_GROUPS)|{alternative}
            self.assertEqual((await self.consumer.verify(self.w.target)).status,'verified')
        self.w.scopes=set(READ_SCOPE_GROUPS)|{WRITE}
        self.pending(await self.consumer.verify(self.w.target))
    async def test_scope_missing_denied_duplicate_malformed_rows_refuse_grant(self):
        self.w.scopes=set(READ_SCOPE_GROUPS)|{SEND}
        self.pending(await self.request())
        self.w.scopes.add(WRITE)
        for rows in ([{'scope_name':WRITE,'scope_type':'tenant','grant_status':0}],
                     [{'scope_name':s,'scope_type':'tenant','grant_status':1} for s in self.w.scopes]+[{'scope_name':WRITE,'scope_type':'tenant','grant_status':1}],
                     [{'scope_name':s,'scope_type':'tenant','grant_status':True} for s in self.w.scopes],None):
            with self.subTest(mode=type(rows).__name__):
                self.w.scope_rows=rows if rows is not None else [{'scope_name':WRITE,'scope_type':'tenant','grant_status':'1'}]
                self.pending(await self.request())
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM remote_grant').fetchone()[0],0)
    async def test_actual_manager_optional_capabilities_and_runtime_uses_original_grant(self):
        self.assertIn('capabilities',inspect.signature(RemoteManager).parameters,'genuine missing feature: manager cannot request native reaction capability proof')
        manager=RemoteManager(self.db,self.w.catalog_path,self.w.legacy_path,record=self.w.actual_record,reader=self.w.reader,
            bot=self.w.bot,discovery_relay=self.w.discovery,link_base=ORIGIN,clock=lambda:self.w.now,capabilities=CAPS)
        self.addAsyncCleanup(manager.close)
        result=await manager.reconcile(CHANNEL,expected_revision=0);self.assertEqual(result.status,'active')
        grant=self.db.remote_grant(result.target_id);self.assertEqual(grant.evidence.capabilities,CAPS)
        event=self.w.reaction(self.root);self.assertEqual((await self.deliver(event,grant)).status,'acked')
        self.assertEqual(len(self.w.mutations),1)
    async def test_add_unknown_committed_before_actual_post_and_exact_native_get_ack_once(self):
        grant,event,row=await self.added()
        self.assertEqual(len(self.w.mutations),1);self.assertEqual(self.w.mutations[0][0],'POST')
        self.assertTrue(self.w.reaction_reads)
        self.assertEqual((await self.deliver(event,grant)).status,'acked');self.assertEqual(len(self.w.mutations),1)
        self.assertEqual(self.row(event,'reaction_add'),row);self.assertFalse(self.w.cli_calls)
    async def test_source_signature_author_channel_one_e_and_emoji_required(self):
        grant=await self.grant();bad=self.w.reaction(self.root);bad['sig']='0'*128
        wrong=gs.sign_event(signed_fixture.KEY,7,[['h','00000000-0000-0000-0000-000000000002'],['e',self.root['id']]],'✅',NOW)
        self.w.events.append(wrong)
        events=[bad,wrong,self.w.reaction(self.root,key=signed_fixture.HUMAN_KEY),
            self.w.reaction(self.root,refs=[]),self.w.reaction(self.root,refs=[['e',self.root['id']],['e',self.root['id']]]),
            self.w.reaction(self.root,text='unsupported')]
        for event in events:self.pending(await self.deliver(event,grant))
        self.assertFalse(self.w.mutations)
    async def test_original_mapping_footer_own_app_chat_and_root_cannot_be_borrowed(self):
        grant=await self.grant();event=self.w.reaction(self.root);original=copy.deepcopy(self.w.messages['om_root'])
        for field,value in (('chat_id','oc_other'),('root_id','om_other'),('sender',{'sender_type':'app','id_type':'app_id','id':'cli_other'}),('body',{'content':'{}'})):
            with self.subTest(field=field):
                self.w.messages['om_root']=copy.deepcopy(original);self.w.messages['om_root'][field]=value
                self.pending(await self.deliver(event,grant))
        self.assertFalse(self.w.mutations)
    async def test_unknown_lost_response_reopen_get_only_and_complete_absence_no_post(self):
        grant=await self.grant();event=self.w.reaction(self.root);self.w.lose=True
        self.pending(await self.deliver(event,grant));old=self.row(event,'reaction_add');self.assertEqual(old.status,'unknown')
        self.w.lose=False;self.w.reactions=[];self.reopen()
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add'),old)
        self.w.reactions=[self.w.reaction_row(self.w.reaction_id)]
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add'),old)
        self.assertEqual(len(self.w.mutations),1)
    async def test_wrong_operator_duplicate_incomplete_malformed_and_alternate_id_cannot_ack(self):
        grant=await self.grant();event=self.w.reaction(self.root);self.w.lose=True
        self.pending(await self.deliver(event,grant));self.w.lose=False
        original=copy.deepcopy(self.w.reactions[0])
        for mode in ('foreign','user','duplicate','incomplete','malformed','bad_padding','alternate'):
            def response(params,rows,mode=mode):
                row=copy.deepcopy(original)
                if mode=='foreign':row['operator']['operator_id']='cli_other'
                if mode=='user':row['operator']={'operator_type':'user','operator_id':'ou_other'}
                if mode=='duplicate':return {'items':[row,copy.deepcopy(row)],'has_more':False}
                if mode=='incomplete':return {'items':[row],'has_more':True,'page_token':'same'}
                if mode=='malformed':return {'items':[row],'has_more':1}
                if mode=='bad_padding':row['reaction_id']='R'*85+'B=='
                if mode=='alternate':row['reaction_id']='alternate-own-id'
                return {'items':[row],'has_more':False}
            self.w.read_override=response
            with self.subTest(mode=mode):self.pending(await self.deliver(event,grant))
            self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.assertEqual(len(self.w.mutations),1)
    async def test_permission_rejection_pending_fixed_notice_no_identity_fallback_or_retry(self):
        grant=await self.grant();event=self.w.reaction(self.root);self.w.permission_error=True
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.w.permission_error=False
        self.pending(await self.deliver(event,grant));self.assertEqual(len(self.w.mutations),1);self.assertFalse(self.w.cli_calls)
    async def test_remove_original_acked_padded_id_and_exact_own_absence_get_only(self):
        self.w.reaction_id=PADDED;grant,add,receipt=await self.added()
        event=self.w.reaction(add,kind=5);self.w.lose=True
        self.pending(await self.deliver(event,grant));old=self.row(event,'reaction_remove');self.assertEqual(old.status,'unknown')
        self.assertEqual(self.w.mutations[-1][1].rsplit('/',1)[1],PADDED.replace('=','%3D'))
        self.w.lose=False;self.reopen()
        self.assertEqual((await self.deliver(event,grant)).status,'acked')
        removed=self.row(event,'reaction_remove')
        self.assertEqual((removed.reaction_id,removed.emoji,removed.message_id),(receipt.reaction_id,receipt.emoji,receipt.message_id))
        self.assertEqual(len(self.w.mutations),2)
    async def test_remove_missing_unacked_source_other_author_and_alternate_live_id_refuse(self):
        grant=await self.grant();add=self.w.reaction(self.root);event=self.w.reaction(add,kind=5)
        self.pending(await self.deliver(event,grant));self.assertFalse(self.w.mutations)
        self.assertEqual((await self.deliver(add,grant)).status,'acked')
        wrong=self.w.reaction(add,kind=5,key=signed_fixture.HUMAN_KEY);self.pending(await self.deliver(wrong,grant))
        self.w.reactions=[self.w.reaction_row('alternate-own-id')]
        self.pending(await self.deliver(event,grant));self.assertEqual(len(self.w.mutations),1)
    async def test_remove_unknown_incomplete_or_different_own_id_cannot_infer_absence(self):
        grant,add,_=await self.added();event=self.w.reaction(add,kind=5);self.w.lose=True
        self.pending(await self.deliver(event,grant));self.w.lose=False
        for mode in ('incomplete','alternate','foreign_same_id'):
            self.w.read_override=lambda params,rows,mode=mode:({'items':[],'has_more':True,'page_token':'same'} if mode=='incomplete'
                else {'items':[self.w.reaction_row('alternate-own-id' if mode=='alternate' else self.w.reaction_id,
                                                 APP if mode=='alternate' else 'cli_other')],'has_more':False})
            with self.subTest(mode=mode):self.pending(await self.deliver(event,grant))
            self.assertEqual(self.row(event,'reaction_remove').status,'unknown')
        self.assertEqual(len(self.w.mutations),2)
    async def test_current_scope_grant_catalog_and_sql_pins_rechecked_across_real_awaits(self):
        grant=await self.grant();event=self.w.reaction(self.root)
        for pins in ({'revision':grant.revision+1},{'scope_hash':'a'*64},{'target_id':'a'*64}):self.pending(await self.deliver(event,grant,**pins))
        self.w.scopes.discard(WRITE);self.pending(await self.deliver(event,grant));self.w.scopes.add(WRITE)
        catalog=self.w.catalog_path.read_bytes()
        self.w.on_member=lambda:self.w.owned(self.w.catalog_path,catalog+b'\n')
        self.pending(await self.deliver(event,grant));self.w.owned(self.w.catalog_path,catalog)
        self.w.on_member=lambda:self.w.on_loop(lambda:self.db.suspend_remote_grant(grant.target_id,expected_revision=grant.revision,now=NOW))
        self.pending(await self.deliver(event,grant));self.assertFalse(self.w.mutations)
    async def test_revocation_after_physical_post_keeps_unknown_no_ack_or_resend(self):
        grant=await self.grant();event=self.w.reaction(self.root)
        self.w.after_mutation=lambda:self.w.on_loop(lambda:self.db.suspend_remote_grant(grant.target_id,expected_revision=grant.revision,now=NOW))
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.w.after_mutation=None;self.pending(await self.deliver(event,grant));self.assertEqual(len(self.w.mutations),1)
    async def test_repeated_cancel_joins_exact_original_post_then_reopen_get_only(self):
        grant=await self.grant();event=self.w.reaction(self.root);self.w.block=True
        task=asyncio.create_task(self.deliver(event,grant))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.entered.wait,60))
            for _ in range(3):
                task.cancel();await asyncio.sleep(.02);self.assertFalse(task.done());self.assertFalse(self.w.completed.is_set())
        finally:self.w.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.w.completed.is_set());self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.w.block=False;self.reopen();self.pending(await self.deliver(event,grant))
        self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.assertEqual(len(self.w.mutations),1)

    async def test_timeout_create_stays_original_unknown_with_get_only_after_reopen(self):
        grant=await self.grant();event=self.w.reaction(self.root);self.w.timeout_write=True
        self.pending(await self.deliver(event,grant));old=self.row(event,'reaction_add')
        self.assertEqual(old.status,'unknown');self.w.timeout_write=False;self.reopen()
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add'),old)
        self.assertEqual(len(self.w.mutations),1)
    async def test_preexisting_own_reaction_is_not_a_receipt_for_this_signed_source(self):
        grant=await self.grant();event=self.w.reaction(self.root)
        self.w.reactions=[self.w.reaction_row('preexisting-own-id')]
        self.pending(await self.deliver(event,grant));self.assertFalse(self.w.mutations)
        row=self.row(event,'reaction_add');self.assertTrue(row is None or row.status!='acked')
    async def test_native_read_await_revocation_before_post_and_before_receipt_ack(self):
        grant=await self.grant();event=self.w.reaction(self.root)
        def revoke_scope(method,path):
            if method=='GET' and path.endswith('/reactions'):
                self.w.scopes.discard(WRITE);self.w.on_native=None
        self.w.on_native=revoke_scope
        self.pending(await self.deliver(event,grant));self.assertFalse(self.w.mutations)
        self.w.scopes.add(WRITE)
        def revoke_receipt(method,path):
            if method=='GET' and path.endswith('/reactions') and self.w.mutations:
                self.w.on_native=None
                self.w.on_loop(lambda:self.db.suspend_remote_grant(grant.target_id,expected_revision=grant.revision,now=NOW))
        self.w.on_native=revoke_receipt
        self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add').status,'unknown')
        self.assertEqual(len(self.w.mutations),1)

    async def test_successful_create_exact_returned_receipt_guards_with_fresh_world_per_mode(self):
        modes=('foreign','user','duplicate','incomplete','malformed','bad_padding','alternate')
        for index,mode in enumerate(modes):
            with self.subTest(mode=mode):
                if index:
                    self.db.close()
                    await self.asyncSetUp()  # new protected catalog, native token pool, SQL grant and signed source
                grant=await self.grant();event=self.w.reaction(self.root)
                def response(params,rows,mode=mode):
                    row=copy.deepcopy(rows[0])
                    if mode=='foreign':row['operator']['operator_id']='cli_other'
                    if mode=='user':row['operator']={'operator_type':'user','operator_id':'ou_other'}
                    if mode=='duplicate':return {'items':[row,copy.deepcopy(row)],'has_more':False}
                    if mode=='incomplete':return {'items':[row],'has_more':True,'page_token':'same'}
                    if mode=='malformed':return {'items':[row],'has_more':1}
                    if mode=='bad_padding':row['reaction_id']='R'*85+'B=='
                    if mode=='alternate':row['reaction_id']='alternate-own-id'
                    return {'items':[row],'has_more':False}
                self.w.after_mutation=lambda response=response:setattr(self.w,'read_override',response)
                self.pending(await self.deliver(event,grant))
                self.assertEqual(len(self.w.mutations),1);self.assertEqual(self.w.mutations[0][0],'POST')
                self.assertEqual(self.row(event,'reaction_add').status,'unknown')
                self.assertTrue(self.w.reaction_reads);self.assertFalse(self.w.cli_calls)
    async def test_repeated_cancel_joins_original_delete_then_reopen_exact_absence_can_ack(self):
        grant,add,receipt=await self.added();event=self.w.reaction(add,kind=5)
        self.w.block=True;self.w.entered.clear();self.w.completed.clear()
        task=asyncio.create_task(self.deliver(event,grant))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.entered.wait,60))
            for _ in range(3):
                task.cancel();await asyncio.sleep(.02);self.assertFalse(task.done());self.assertFalse(self.w.completed.is_set())
        finally:self.w.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.w.completed.is_set());self.assertEqual(self.row(event,'reaction_remove').status,'unknown')
        self.w.block=False;self.reopen();self.assertEqual((await self.deliver(event,grant)).status,'acked')
        removed=self.row(event,'reaction_remove')
        self.assertEqual((removed.reaction_id,removed.emoji,removed.message_id),(receipt.reaction_id,receipt.emoji,receipt.message_id))
        self.assertEqual([call[0] for call in self.w.mutations],['POST','DELETE'])
    async def test_native_body_or_root_changed_during_post_keeps_original_unknown(self):
        for index,mode in enumerate(('body','root')):
            with self.subTest(mode=mode):
                if index:
                    self.db.close();await self.asyncSetUp()
                grant=await self.grant();event=self.w.reaction(self.root)
                original=copy.deepcopy(self.w.messages['om_root'])
                def mutate(mode=mode):
                    if mode=='body':
                        doc=json.loads(self.w.messages['om_root']['body']['content'])
                        doc['elements'][0]['text']['content']='SYNTHETIC_CHANGED_NATIVE_BODY'
                        self.w.messages['om_root']['body']['content']=json.dumps(doc)
                    else:self.w.messages['om_root']['root_id']='om_other'
                self.w.after_mutation=mutate
                result=await self.deliver(event,grant)
                self.assertEqual([call[0] for call in self.w.mutations],['POST'])
                self.pending(result)
                self.assertNotIn('SYNTHETIC_CHANGED_NATIVE_BODY',json.dumps(result.readback()))
                old=self.row(event,'reaction_add');self.assertIsNotNone(old);self.assertEqual(old.status,'unknown')
                self.w.after_mutation=None;self.reopen()
                self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add'),old)
                self.w.messages['om_root']=original
                self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_add'),old)
                self.assertEqual([call[0] for call in self.w.mutations],['POST'])
    async def test_native_body_or_root_changed_during_delete_holds_then_original_absence_recovers(self):
        for index,mode in enumerate(('body','root')):
            with self.subTest(mode=mode):
                if index:
                    self.db.close();await self.asyncSetUp()
                grant,add,receipt=await self.added();event=self.w.reaction(add,kind=5)
                original=copy.deepcopy(self.w.messages['om_root'])
                def mutate(mode=mode):
                    if mode=='body':
                        doc=json.loads(self.w.messages['om_root']['body']['content'])
                        doc['elements'][0]['text']['content']='SYNTHETIC_CHANGED_NATIVE_BODY'
                        self.w.messages['om_root']['body']['content']=json.dumps(doc)
                    else:self.w.messages['om_root']['root_id']='om_other'
                self.w.after_mutation=mutate
                result=await self.deliver(event,grant)
                self.assertEqual([call[0] for call in self.w.mutations],['POST','DELETE'])
                self.pending(result)
                self.assertNotIn('SYNTHETIC_CHANGED_NATIVE_BODY',json.dumps(result.readback()))
                old=self.row(event,'reaction_remove');self.assertIsNotNone(old);self.assertEqual(old.status,'unknown')
                self.w.after_mutation=None;self.reopen()
                self.pending(await self.deliver(event,grant));self.assertEqual(self.row(event,'reaction_remove'),old)
                self.w.messages['om_root']=original
                self.assertEqual((await self.deliver(event,grant)).status,'acked')
                removed=self.row(event,'reaction_remove')
                self.assertEqual((removed.reaction_id,removed.emoji,removed.message_id),(receipt.reaction_id,receipt.emoji,receipt.message_id))
                self.assertEqual([call[0] for call in self.w.mutations],['POST','DELETE'])
