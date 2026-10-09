"""Real SQL/card authority and signed HTTP approval publication, offline only."""
import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import threading
import unittest

import test_hostd_claim_sync_app as claims
from hostd import remote_approval, store
from hostd.join_effects import Nip98Relay, JoinEffects, AgentSpec
from hostd.signed_reads import SignedReader
import recovery_authority as authority

publisher = importlib.import_module('hostd.approval_publisher') if importlib.util.find_spec('hostd.approval_publisher') else None
base,gs=claims.base,claims.gs
setUpModule=claims.setUpModule
tearDownModule=claims.tearDownModule
REQUEST,BINDING,CARD,DECISION='JOIN-aabbccdd','binding','om_reviewed_card','owner-click-event'


def text_hash(value):return hashlib.sha256(value.encode()).hexdigest()


class ApprovalPublisherTests(base.TmpCase,unittest.IsolatedAsyncioTestCase):
    assembly=claims.RoundPublisher.assembly
    seed=claims.RoundPublisher.seed

    def setup_publisher(self, *, kind='channel', planning_delay=0):
        self.kind=kind
        env,cfg,world,run=self.assembly()
        self.seed(world,run,sync={'version':1,'app_id':base.AGENT_APP})
        self.env,self.cfg,self.world,self.round=env,cfg,world,run
        self.now=int(world.clock.timestamp())
        self.path=self.tmp/'sql'/'hostd.db';self.db=store.Store(self.path)
        self.addCleanup(self.db.close)
        selected=cfg['agents'][cfg['desk_pubkey']]
        agentenv=base.write_owner_only(self.tmp/'approved-agent.env',
            f'BUZZ_PRIVATE_KEY={claims.AGENT_KEY}\nBUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\nBUZZ_ACP_CHANNELS={base.CHANNEL}\n')
        self.agentenv=agentenv
        self.db.register_agent(claims.AGENT,owner_pubkey=base.OWNER_PK,app_id=base.AGENT_APP,
            config_path=str(agentenv),now=self.now-200)
        if kind=='channel':
            self.db.reconcile_bindings([store.BindingRecord(BINDING,base.CHANNEL,base.CHAT,base.AGENT_APP,
                str(env.config),selected['lark_config_dir'],selected['lark_data_dir'],base.MIRROR_PK)],now=self.now-200)
        self.db.create_join(REQUEST,claims.AGENT,base.OWNER_PK,base.AGENT_APP,base.CHAT,
            kind=kind,binding_id=BINDING if kind=='channel' else None,now=self.now-200)
        self.db.rotate_card(REQUEST,CARD,now=self.now-150)
        self.decision_at=self.now-50 if kind=='channel' else self.now
        self.assertTrue(self.db.decide_join(REQUEST,DECISION,base.OWNER_PK,base.AGENT_APP,CARD,1,
            approved=True,now=self.decision_at))
        spec=AgentSpec(claims.AGENT,base.OWNER_PK,base.AGENT_APP,str(agentenv),str(self.tmp/'prompt'),
            str(self.tmp/'responsible'),'agent.service',str(self.tmp/'timer'),str(self.tmp),
            str(self.tmp/'bindings'),base.AGENT_APP,selected['lark_config_dir'],selected['lark_data_dir'],str(env.config))
        adapter=JoinEffects(self.db,{claims.AGENT:spec},None,None,clock=lambda:self.now+planning_delay)
        self.plan=adapter._plan(self.db.join_request(REQUEST),spec)
        if kind=='new_binding':
            Path(self.plan['secret_ref']).parent.mkdir(parents=True,mode=0o700)
            world.clock=world.clock+claims.timedelta(seconds=planning_delay)
            self.cfg['channel_id']=self.plan['channel_id'];self.cfg['mirror_env_file']=self.plan['secret_ref']
            base.write_owner_only(Path(self.plan['secret_ref']),env.mirror_env.read_text())
            base.write_owner_only(Path(self.plan['config_path']),json.dumps(self.cfg))
            self.db.set_effect_mirror(REQUEST,base.MIRROR_PK,now=self.now)
            self.db.reconcile_bindings([store.BindingRecord(REQUEST,self.plan['channel_id'],base.CHAT,base.AGENT_APP,
                self.plan['config_path'],selected['lark_config_dir'],selected['lark_data_dir'],base.MIRROR_PK)],now=self.now)
            base.write_owner_only(agentenv,agentenv.read_text().replace(base.CHANNEL,self.plan['channel_id']))
            self.seed(world,run,sync={'version':1,'app_id':base.AGENT_APP})
            policy=claims.latest_policy(world.relay_events,base.MIRROR_PK)
            doc=json.loads(policy['content']);doc['feishu']['bindings'][0].update(channel=self.plan['channel_id'],claimed_at=self.plan['created_at'],heartbeat=self.now+planning_delay)
            world.relay_events=[e for e in world.relay_events if e['id']!=policy['id']]+[gs.sign_event(base.OWNER_KEY,30177,
                [['d',base.MIRROR_PK]],json.dumps(doc),self.now)]
        self.posts=[];self.query_signers=[];self.readback_mode='';self.post_mode='';self.packet_mode=''
        self.post_entered=threading.Event();self.post_release=threading.Event();self.query_hook=None
        low=run.clients.http
        def http(url,headers,timeout,body=None):
            if url.endswith('/query'):
                filters=json.loads(body)
                auth=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
                self.assertTrue(gs._nip01_event_verified(auth));self.query_signers.append(auth['pubkey'])
                self.assertEqual(auth['pubkey'],base.OWNER_PK)
                authority.exact_tag(auth,'u',url);authority.exact_tag(auth,'method','POST')
                authority.exact_tag(auth,'payload',hashlib.sha256(body).hexdigest())
                if kind=='new_binding' and filters[0].get('kinds')==[39002]:
                    # Actual pinned roster for the newly planned channel.
                    roster=gs.sign_event(claims.PIN_KEY,39002,[['d',self.plan['channel_id']]]+
                        [['p',m['pubkey'],'',m['role']] for m in world.members],'',int(world.clock.timestamp()))
                    return 200,json.dumps([roster]).encode()
                if self.query_hook is not None:
                    hook,self.query_hook=self.query_hook,None;hook()
                status,raw=low(url,headers,timeout,body=body);filters=json.loads(body)
                rows=json.loads(raw)
                if any('ids' in row for row in filters):
                    rows=[event for event in rows if any('ids' not in row or event['id'] in row['ids'] for row in filters)]
                is_record=any(row.get('kinds')==[30078] or 'ids' in row for row in filters)
                if self.packet_mode and not is_record and rows:
                    if self.packet_mode=='mixed':
                        bad=copy.deepcopy(rows[-1]);bad['sig']='0'*128;rows.append(bad)
                    elif self.packet_mode=='saturated':rows=rows*257
                    elif self.packet_mode=='malformed':rows[-1]['tags'].append(['invalid',1])
                if is_record and self.readback_mode:
                    if self.readback_mode=='empty':rows=[]
                    elif self.readback_mode=='forged' and rows:rows[-1]['sig']='0'*128
                    elif self.readback_mode=='different_sig' and rows:
                        original=rows[-1]
                        rows[-1]=gs.sign_event(base.MIRROR_KEY,30078,original['tags'],original['content'],original['created_at'])
                        # Valid Schnorr alternate signature for the same event ID.
                        rows[-1]['sig']=gs.sync.nk.schnorr_sign(bytes.fromhex(original['id']),
                            bytes.fromhex(base.MIRROR_KEY),b'\1'*32).hex()
                    elif self.readback_mode=='wrong_time' and rows:
                        original=rows[-1];rows[-1]=gs.sign_event(base.MIRROR_KEY,30078,
                            original['tags'],original['content'],original['created_at']+1)
                return status,json.dumps(rows).encode()
            event=json.loads(body)
            if url.endswith('/events') and event['kind']==30078:
                signer=world._verify_relay_nip98(headers['Authorization'],url,body)
                self.assertEqual(signer,base.MIRROR_PK);self.assertTrue(gs._nip01_event_verified(event))
                self.posts.append(copy.deepcopy(event));self.post_entered.set()
                if self.post_mode=='blocked':self.assertTrue(self.post_release.wait(10))
                if self.post_mode=='network':raise OSError('offline transport failure')
                if not any(e['id']==event['id'] for e in world.relay_events):world.relay_events.append(copy.deepcopy(event))
                if self.post_mode=='lost':raise OSError('offline lost response')
                return 200,json.dumps({'accepted':True,'event_id':event['id'],'message':'duplicate:' if self.post_mode=='duplicate' else ''}).encode()
            return low(url,headers,timeout,body=body)
        self.relay=Nip98Relay('https://relay.test',env.signer_env,claims.PIN,http=http,
            trusted_relays=('https://relay.test',),clock=lambda:int(world.clock.timestamp()))
        return self

    def instance(self,db=None):
        self.assertIsNotNone(publisher,'the actual approval publisher module is missing')
        return publisher.ApprovalPublisher(db or self.db,self.plan['binding_id'],self.relay,clock=lambda:int(self.world.clock.timestamp()))

    async def pending(self,instance=None):
        result=await (instance or self.instance()).publish(REQUEST)
        self.assertEqual(result.status,'pending');self.assertIn('怎么解决',result.notice);self.assertIn('复制给 AI',result.notice)
        return result

    def mirror_policy(self,transform):
        previous=claims.latest_policy(self.world.relay_events,base.MIRROR_PK)
        body=json.loads(previous['content']);transform(body)
        event=gs.sign_event(base.OWNER_KEY,30177,[['d',base.MIRROR_PK]],json.dumps(body),self.now)
        self.world.relay_events.append(event)

    def verify_event(self,event):
        decoded=remote_approval.decode(event,now=int(self.world.clock.timestamp()))
        profiles=[e for e in self.world.relay_events if e['kind']==0]
        policies=[e for e in self.world.relay_events if e['kind']==30177]
        roster=gs.sign_event(claims.PIN_KEY,39002,[['d',base.CHANNEL]]+
            [['p',m['pubkey'],'',m['role']] for m in self.world.members],'',int(self.world.clock.timestamp()))
        context=remote_approval.ProofContext(decoded.scope,claims.PIN,
            authority.latest([e for e in profiles if e['pubkey']==claims.AGENT]),
            claims.latest_policy(policies,claims.AGENT),
            tuple(e for e in profiles if e['pubkey']==base.MIRROR_PK),tuple(policies),
            roster,int(self.world.clock.timestamp()),complete=True)
        return remote_approval.verify(event,context)

    async def test_actual_owner_card_sql_publishes_only_mirror_signed_canonical_record_and_exact_ack(self):
        self.setup_publisher();self.assertEqual(self.plan['secret_ref'],str(self.agentenv))
        self.assertEqual(self.plan['mirror_pubkey'],'')
        result=await self.instance().publish(REQUEST)
        self.assertEqual(result.status,'verified');self.assertEqual(len(self.posts),1)
        event=self.posts[0];proof=self.verify_event(event)
        self.assertEqual(event['pubkey'],base.MIRROR_PK);self.assertNotEqual(event['pubkey'],base.OWNER_PK)
        self.assertEqual((proof.card_generation,proof.card_message_sha256,proof.decision_event_sha256,proof.decision_at),
            (1,text_hash(CARD),text_hash(DECISION),self.now-50))
        self.assertEqual(result.event_id,event['id']);self.assertEqual(result.content_hash,proof.content_hash)
        self.assertTrue(self.query_signers);self.assertEqual(set(self.query_signers),{base.OWNER_PK})
        record=self.db.approval_publication(REQUEST)
        self.assertEqual(record.state,'acked');self.assertEqual(record.pin.signature,event['sig'])
        self.assertNotIn(CARD,event['content']);self.assertNotIn(DECISION,event['content'])

    async def test_actual_new_binding_plan_uses_request_mirror_path_and_first_same_second_approval(self):
        self.setup_publisher(kind='new_binding')
        self.assertEqual(self.plan['secret_ref'],str(self.tmp/'bindings'/REQUEST/'mirror.env'))
        self.assertNotEqual(self.plan['secret_ref'],str(self.agentenv))
        result=await self.instance().publish(REQUEST)
        self.assertEqual(result.status,'verified');self.assertEqual(len(self.posts),1)
        proof=remote_approval.decode(self.posts[0],now=self.now)
        self.assertEqual(proof.scope.channel_id,self.plan['channel_id'])
        self.assertEqual(proof.scope.claimed_at,self.decision_at)

    async def test_new_binding_cannot_borrow_copied_mirror_key_outside_exact_plan_path(self):
        self.setup_publisher(kind='new_binding')
        copied=base.write_owner_only(self.tmp/'foreign-mirror.env',Path(self.plan['secret_ref']).read_text())
        config=Path(self.plan['config_path']);body=json.loads(config.read_text())
        body['mirror_env_file']=str(copied);base.write_owner_only(config,json.dumps(body))
        await self.pending();self.assertEqual(self.posts,[])

    async def test_local_only_producer_requires_actual_catalog_agent_env_key_owner_and_channel(self):
        self.setup_publisher(kind='new_binding');original=self.agentenv.read_text()
        for mode in ('no_local_path','permissions','key','owner','channel'):
            with self.subTest(mode=mode):
                if mode=='no_local_path':self.db.conn.execute('UPDATE agent SET config_path=NULL')
                elif mode=='permissions':self.agentenv.chmod(0o644)
                elif mode=='key':base.write_owner_only(self.agentenv,original.replace(claims.AGENT_KEY,base.OWNER_KEY))
                elif mode=='owner':base.write_owner_only(self.agentenv,original.replace(base.OWNER_PK,'f'*64))
                else:base.write_owner_only(self.agentenv,original.replace(self.plan['channel_id'],claims.OTHER_CHANNEL))
                await self.pending()
                self.db.conn.execute('UPDATE agent SET config_path=?',(str(self.agentenv),))
                self.agentenv.chmod(0o600);base.write_owner_only(self.agentenv,original)
        self.assertEqual(self.posts,[])

    async def test_real_later_new_claim_after_original_owner_decision_stays_pending_under_v1_contract(self):
        self.setup_publisher(kind='new_binding',planning_delay=1)
        self.assertGreater(self.plan['created_at'],self.decision_at)
        await self.pending();self.assertEqual(self.posts,[])

    async def test_completed_binding_late_publication_preserves_first_decision_after_original_request_deadline(self):
        self.setup_publisher();old=self.now-8*86400
        self.db.conn.execute("UPDATE join_request SET status='done',created_at=?,deadline=?,updated_at=?",(old,old+7*86400,old+10))
        self.db.conn.execute('UPDATE join_decision SET created_at=?',(old+10,))
        self.mirror_policy(lambda body:body['feishu']['bindings'][0].update(claimed_at=old+5,heartbeat=self.now))
        result=await self.instance().publish(REQUEST)
        self.assertEqual(result.status,'verified');proof=self.verify_event(self.posts[0])
        self.assertEqual(proof.decision_at,old+10);self.assertEqual(proof.request_deadline,old+7*86400)

    async def test_requested_denied_expired_or_missing_first_callback_never_publishes(self):
        self.setup_publisher();instance=self.instance()
        for status in ('requested','denied','expired'):
            self.db.conn.execute('UPDATE join_request SET status=?',(status,));await self.pending(instance)
        self.db.conn.execute("UPDATE join_request SET status='approved'")
        self.db.conn.execute('DELETE FROM join_decision');await self.pending(instance)
        self.assertEqual(self.posts,[])

    async def test_actual_card_generation_app_owner_or_extra_sql_decision_cannot_manufacture_authority(self):
        self.setup_publisher();instance=self.instance()
        for column,value,original in (('card_generation',0,1),('callback_app_id','cli_wrong',base.AGENT_APP),
                                      ('owner_pubkey','f'*64,base.OWNER_PK)):
            with self.subTest(column=column):
                self.db.conn.execute(f'UPDATE join_request SET {column}=?',(value,));await self.pending(instance)
                self.db.conn.execute(f'UPDATE join_request SET {column}=?',(original,))
        self.db.conn.execute('INSERT INTO join_decision VALUES(?,?,?,?)',('cli_foreign','foreign-click',REQUEST,self.now))
        await self.pending(instance);self.assertEqual(self.posts,[])

    async def test_wrong_current_binding_plan_or_agent_app_rejected_without_post(self):
        self.setup_publisher();instance=self.instance()
        for table,column,value,original in (('effect_plan','channel_id',claims.OTHER_CHANNEL,base.CHANNEL),
            ('effect_plan','config_path','/foreign/config.json',str(self.env.config)),
            ('effect_plan','secret_ref','/foreign/mirror.env',str(self.agentenv)),
            ('binding','mirror_pubkey','f'*64,base.MIRROR_PK),('agent','app_id','cli_foreign',base.AGENT_APP)):
            with self.subTest(table=table,column=column):
                self.db.conn.execute(f'UPDATE {table} SET {column}=?',(value,));await self.pending(instance)
                self.db.conn.execute(f'UPDATE {table} SET {column}=?',(original,))
        self.assertEqual(self.posts,[])

    async def test_own_mirror_protected_file_permissions_key_and_owner_query_identity_are_independent(self):
        self.setup_publisher();instance=self.instance();original=self.env.mirror_env.read_bytes()
        self.env.mirror_env.chmod(0o644);await self.pending(instance);self.env.mirror_env.chmod(0o600)
        base.write_owner_only(self.env.mirror_env,'BUZZ_PRIVATE_KEY='+base.OWNER_KEY+'\n')
        await self.pending(instance);base.write_owner_only(self.env.mirror_env,original.decode())
        original_config=self.env.config.read_bytes();wrong=copy.deepcopy(self.cfg);wrong['channel_id']=claims.OTHER_CHANNEL
        base.write_owner_only(self.env.config,json.dumps(wrong));await self.pending(instance)
        base.write_owner_only(self.env.config,original_config.decode())
        original_events=copy.deepcopy(self.world.relay_events)
        for mode in ('mirror_owner','conditional_mirror','agent_app'):
            with self.subTest(mode=mode):
                self.world.relay_events=copy.deepcopy(original_events)
                if mode=='mirror_owner':self.world.relay_events.append(claims.profile(base.MIRROR_KEY,'8'.zfill(64),self.now))
                if mode=='conditional_mirror':self.world.relay_events.append(claims.profile(base.MIRROR_KEY,base.OWNER_KEY,self.now,'condition'))
                if mode=='agent_app':self.world.relay_events.append(gs.sign_event(base.OWNER_KEY,30177,
                    [['d',claims.AGENT]],json.dumps({'feishu':{'app_id':'cli_foreign'}}),self.now))
                await self.pending(instance)
        self.assertEqual(self.posts,[])

    async def test_complete_native_signed_reader_rejects_mixed_forged_malformed_and_saturated_packets(self):
        self.setup_publisher();instance=self.instance()
        for mode in ('mixed','malformed','saturated'):
            with self.subTest(mode=mode):self.packet_mode=mode;await self.pending(instance)
        self.assertEqual(self.posts,[])

    async def test_reclaimed_withdrawn_expired_claim_and_latest_roster_revocation_reject(self):
        self.setup_publisher();instance=self.instance();original=copy.deepcopy(self.world.relay_events)
        original_members=copy.deepcopy(self.world.members)
        for mode in ('reclaimed','withdrawn','expired','roster_revoked','earlier_competitor'):
            with self.subTest(mode=mode):
                self.world.relay_events=copy.deepcopy(original)
                self.world.members=copy.deepcopy(original_members)
                self.world.clock=base.NOW
                self.world.claim_roster_mode='revoked' if mode=='roster_revoked' else ''
                if mode=='reclaimed':self.mirror_policy(lambda b:b['feishu']['bindings'][0].update(claimed_at=self.now))
                if mode=='withdrawn':self.mirror_policy(lambda b:b['feishu'].update(bindings=[]))
                if mode=='expired':self.world.clock+=claims.timedelta(seconds=7200)
                if mode=='earlier_competitor':
                    other_key='9'.zfill(64);other=gs._signer_pubkey(other_key)
                    self.world.members=[m for m in self.world.members if m['pubkey']!=other]+[{'pubkey':other,'role':'bot'}]
                    self.world.relay_events.append(claims.profile(other_key,base.OWNER_KEY,self.now))
                    claim={'channel':base.CHANNEL,'chat_ref':gs.chat_ref(base.CHAT),'claimed_at':self.now-200,
                           'heartbeat':self.now,'sync_app':{'version':1,'app_id':base.AGENT_APP}}
                    self.world.relay_events.append(gs.sign_event(base.OWNER_KEY,30177,[['d',other]],
                        json.dumps({'feishu':{'mirror':True,'bindings':[claim]}}),self.now))
                await self.pending(instance)
        self.assertEqual(self.posts,[])

    async def test_lost_post_response_then_actual_close_reopen_only_gets_exact_original_event(self):
        self.setup_publisher();self.post_mode='lost';await self.pending()
        self.assertEqual(len(self.posts),1);event=copy.deepcopy(self.posts[0])
        record=self.db.approval_publication(REQUEST);self.assertEqual(record.state,'unknown')
        self.assertEqual((record.pin.event_id,record.pin.signature),(event['id'],event['sig']))
        self.db.close();self.post_mode=''
        with store.Store(self.path) as reopened:
            result=await self.instance(reopened).publish(REQUEST)
            self.assertEqual(result.status,'verified');self.assertEqual(reopened.approval_publication(REQUEST).state,'acked')
        self.assertEqual(len(self.posts),1)

    async def test_unknown_not_received_reserved_and_restart_never_retries_post_or_repins(self):
        self.setup_publisher();self.post_mode='network';await self.pending();self.assertEqual(len(self.posts),1)
        original=self.db.approval_publication(REQUEST)
        self.db.close();self.world.clock+=claims.timedelta(seconds=3600)
        # Keep the same stable claim live; a heartbeat event change cannot repin.
        self.now=int(self.world.clock.timestamp())
        self.mirror_policy(lambda b:b['feishu']['bindings'][0].update(heartbeat=self.now))
        with store.Store(self.path) as reopened:
            await self.pending(self.instance(reopened));self.assertEqual(reopened.approval_publication(REQUEST).pin,original.pin)
            # A separate actual SQL graph models crash after pin commit but
            # before UNKNOWN. A prior reservation is not dispatch authority.
            reserved_path=self.tmp/'reserved'/'state.db'
            with store.Store(reserved_path) as reserved:
                names=[r[0] for r in reopened.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='approval_publication'")]
                with reserved.transaction():
                    for name in names:
                        rows=[tuple(r) for r in reopened.conn.execute(f'SELECT * FROM "{name}"')]
                        if rows:reserved.conn.executemany(f'INSERT INTO "{name}" VALUES({",".join("?" for _ in rows[0])})',rows)
                self.assertTrue(reserved.reserve_approval_publication(original.pin,now=self.now).created)
            with store.Store(reserved_path) as reserved:
                before=len(self.query_signers);await self.pending(self.instance(reserved))
                self.assertGreater(len(self.query_signers),before)
                self.assertEqual(reserved.approval_publication(REQUEST).state,'reserved')
        self.assertEqual(len(self.posts),1)

    async def test_post_success_or_server_duplicate_without_exact_signed_get_proof_is_unknown(self):
        self.setup_publisher();self.post_mode='duplicate';self.readback_mode='empty'
        await self.pending();self.assertEqual(len(self.posts),1)
        self.assertEqual(self.db.approval_publication(REQUEST).state,'unknown')
        for mode in ('forged','different_sig','wrong_time'):
            with self.subTest(mode=mode):self.readback_mode=mode;await self.pending()
        self.assertEqual(len(self.posts),1)
        self.readback_mode='';self.assertEqual((await self.instance().publish(REQUEST)).status,'verified')

    async def test_sql_revocation_during_authority_read_and_after_post_prevents_dispatch_or_ack(self):
        self.setup_publisher();instance=self.instance();loop=asyncio.get_running_loop()
        def revoke():
            complete=threading.Event()
            def change():self.db.conn.execute("UPDATE agent SET status='retired'");complete.set()
            loop.call_soon_threadsafe(change);self.assertTrue(complete.wait(5))
        self.query_hook=revoke;await self.pending(instance);self.assertEqual(self.posts,[])
        self.db.conn.execute("UPDATE agent SET status='active'")
        self.post_mode='blocked';task=asyncio.create_task(instance.publish(REQUEST))
        self.assertTrue(await asyncio.to_thread(self.post_entered.wait,10))
        self.db.conn.execute("UPDATE agent SET status='retired'");self.post_release.set()
        result=await task;self.assertEqual(result.status,'pending')
        self.assertEqual(self.db.approval_publication(REQUEST).state,'unknown')

    async def test_cancelled_dispatched_io_is_reaped_and_durable_unknown_never_redispatched(self):
        self.setup_publisher();self.post_mode='blocked';task=asyncio.create_task(self.instance().publish(REQUEST))
        self.assertTrue(await asyncio.to_thread(self.post_entered.wait,10));task.cancel();task.cancel()
        self.post_release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        record=self.db.approval_publication(REQUEST);self.assertEqual(record.state,'unknown')
        self.post_mode='';self.assertEqual((await self.instance().publish(REQUEST)).status,'verified')
        self.assertEqual(len(self.posts),1)

    async def test_fixture_control_uses_actual_signed_reader_owner_auth_and_real_canonical_mirror_crypto(self):
        self.setup_publisher()
        rows=await SignedReader(self.relay).read('query',filters=[{'kinds':[30177],'limit':257}])
        self.assertTrue(rows);self.assertTrue(all(gs._nip01_event_verified(e) for e in rows))
        body=dict(version=1,decision='approve',agent_pubkey=claims.AGENT,agent_owner_pubkey=base.OWNER_PK,
            app_id=base.AGENT_APP,channel_id=base.CHANNEL,chat_ref=gs.chat_ref(base.CHAT),mirror_pubkey=base.MIRROR_PK,
            mirror_owner_pubkey=base.OWNER_PK,claimed_at=self.now-100,request_id=REQUEST,
            request_created_at=self.now-200,request_deadline=self.now-200+7*86400,card_generation=1,
            card_message_sha256=text_hash(CARD),decision_event_sha256=text_hash(DECISION),decision_at=self.now-50)
        content=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        event=gs.sign_event(base.MIRROR_KEY,30078,[['t',remote_approval.PREFIX],['h',base.CHANNEL],
            ['p',claims.AGENT],['d',remote_approval.PREFIX+':'+text_hash(content)]],content,self.now)
        self.assertEqual(self.verify_event(event).record_id,event['id']);self.assertEqual(self.posts,[])


if __name__=='__main__':unittest.main()
