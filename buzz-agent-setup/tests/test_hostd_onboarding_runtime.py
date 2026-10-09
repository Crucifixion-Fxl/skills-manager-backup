"""Offline factory/discovery uses real protected loaders, bot CLI and signed wire."""
import asyncio
import base64
import hashlib
import json
import subprocess
import threading
import sys
from pathlib import Path
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS));sys.path.insert(0,str(TESTS.parent/'scripts'))
import test_hostd_agent_catalog as fixture
import buzz_feishu_group_sync as gs
from hostd.store import Store,BindingRecord
from hostd.scheduler import Scheduler
from hostd import onboarding_runtime as runtime
from hostd.onboarding import IdentityResolver,Coordinator
from hostd.join_cards import BotCards
from hostd.join_effects import JoinEffects,Nip98Relay,AgentRuntime
from hostd.bot_clients import NODE,CLI_ENTRY

class World:
    def __init__(self,f):
        self.f=f;self.now=1000;self.calls=[];self.http_calls=[];self.cards={};self.pages={"":{'items':[{'chat_id':'oc_group'}],'has_more':False}}
        self.bot=True;self.complete=True;self.people=True;self.claims=[];self.bad_policy=False;self.policy_app='cli_agent'
        import buzz_agent_join_requests as legacy
        auth=json.loads(legacy.parse_env(f.env_text)['BUZZ_AUTH_TAG'])
        self.profile=gs.sign_event(f.key,0,[auth],'{}',10)
        self.relay_key='3'.zfill(64);self.pin=gs._signer_pubkey(self.relay_key)
        self.mirror_key='4'.zfill(64);self.mirror=gs._signer_pubkey(self.mirror_key)
    def http(self,url,headers,timeout,body=None):
        self.http_calls.append((url,body))
        credential=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
        assert gs._nip01_event_verified(credential) and credential['pubkey']==self.f.owner
        assert ['u',url] in credential['tags']
        if '/people' in url:
            return 200,json.dumps({'channel':CHANNEL,'people':{},'union_ids':{self.f.owner:'on_owner'} if self.people else {}}).encode()
        assert url=='https://relay.test/query'
        filters=json.loads(body);out=[]
        policy=gs.sign_event(self.f.owner_key,30177,[['d',self.f.pub]],json.dumps({'feishu':{'app_id':self.policy_app}}),20)
        if self.bad_policy:policy['sig']='0'*128
        mauth=['auth',self.f.owner,'',gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{self.mirror}:'.encode()).digest(),bytes.fromhex(self.f.owner_key),bytes(32)).hex()]
        mprofile=gs.sign_event(self.mirror_key,0,[mauth],'{}',10)
        mp=gs.sign_event(self.f.owner_key,30177,[['d',self.mirror]],json.dumps({'feishu':{'mirror':True,'bindings':self.claims}}),30)
        for fil in filters:
            for row in [self.profile,mprofile,policy,mp,gs.sign_event(self.relay_key,39002,[['d',CHANNEL],['p',self.f.owner,'','owner']], '',30)]:
                if 'kinds' in fil and row['kind'] not in fil['kinds']:continue
                if 'authors' in fil and row['pubkey'] not in fil['authors']:continue
                if '#d' in fil and not any(t[:1]==['d'] and t[1] in fil['#d'] for t in row['tags']):continue
                out.append(row)
        if getattr(self,'wrong_kind',False) and filters==[{'kinds':[30177],'limit':1000}]:out.append(gs.sign_event(self.relay_key,39002,[['d',CHANNEL]],'',40))
        return 200,json.dumps(out).encode()
    def runner(self,argv,**kw):
        self.calls.append((argv,kw))
        assert argv[:2]==[NODE,CLI_ENTRY]
        assert argv[argv.index('--as')+1]=='bot';assert argv[argv.index('--profile')+1]=='local'
        assert 'BUZZ_PRIVATE_KEY' not in kw['env']
        data={}
        if '+chat-members-list' in argv:
            kind=argv[argv.index('--member-types')+1]
            data={'has_more':not self.complete,'truncations':[]}
            if kind=='user':data.update(users=[{'member_id':'on_owner'}]+([{'member_id':'on_unknown'}] if getattr(self,'extra_user',False) else []),user_total=2 if getattr(self,'extra_user',False) else 1)
            else:data.update(bots=[{'app_id':'cli_agent','member_id':'ou_bot'}] if self.bot else [],bot_total=int(self.bot))
        elif '+messages-send' in argv:
            gate=getattr(self,'send_gate',None)
            if gate is not None:
                self.send_entered.set()
                if not gate.wait(10):raise AssertionError('offline send gate timeout')
            card=json.loads(argv[argv.index('--content')+1]);mid='om_card'+str(len(self.cards)+1)
            self.cards[mid]={'message_id':mid,'chat_id':argv[argv.index('--chat-id')+1],'msg_type':'interactive','sender':{'id':'cli_agent','id_type':'app_id','sender_type':'app'},'body':{'content':json.dumps(card)}};data={'message_id':mid}
        elif '+messages-get' in argv or (len(argv)>4 and '/messages/om_' in argv[4]):
            mid=next((v.rsplit('/',1)[-1] for v in argv if '/messages/om_' in v),'')
            if '--message-id' in argv:mid=argv[argv.index('--message-id')+1]
            data={'items':[self.cards[mid]]}
        elif len(argv)>4 and argv[3]=='GET' and argv[4]=='/open-apis/im/v1/chats':
            params=json.loads(argv[argv.index('--params')+1]);data=self.pages[params.get('page_token','')]
        elif len(argv)>4 and '/contact/v3/users/' in argv[4]:
            op=argv[4].rsplit('/',1)[-1];data={'user':{'open_id':op,'union_id':'on_owner' if op=='ou_owner' else 'on_other'}}
        elif len(argv)>4 and argv[3]=='PATCH':data={}
        else:raise AssertionError('unexpected offline command')
        return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data,'meta':{'pagination':{'complete':self.complete}}}),'')

CHANNEL='00000000-0000-0000-0000-000000000001'
@unittest.skipUnless(fixture.AESGCM is not None, 'actual protected startup/catalog fixture requires cryptography; dependency CI executes every case')
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        f=fixture.CatalogTests('test_ownbot_metadata_and_protected_hashes_only');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.w=World(f);f.write(f.root/'owner.env',f'BUZZ_PRIVATE_KEY={f.owner_key}\n')
        f.write(f.root/'template.json','{}');(f.root/'bindings').mkdir(mode=0o700)
        self.db=Store(f.root/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.scheduler=Scheduler();self.addCleanup(self.scheduler.close)
        self.kw=dict(version=1,owner_env_file=str(f.root/'owner.env'),relay_url='https://relay.test',relay_pubkey=self.w.pin,
          template_config=str(f.root/'template.json'),binding_dir=str(f.root/'bindings'),legacy_join_path=str(f.legacy),catalog_path=str(f.path),trusted_relays=('https://relay.test',))
    async def test_deferred_issuers_do_not_block_own_feed_proofs_and_recover_later(self):
        with mock.patch.object(runtime.OnboardingRuntime, '_refresh_issuers', new_callable=mock.AsyncMock) as refresh:
            service=await runtime.OnboardingRuntime.create(runtime.RuntimeConfig(**self.kw),self.db,registrar=None,scheduler=self.scheduler,http=self.w.http,runner=self.w.runner,clock=lambda:self.w.now,defer_issuers=True)
            try:
                self.assertIn('cli_agent',service.eligible_apps)
                self.assertEqual(service._issuer_bindings,{})
                refresh.assert_not_awaited()
                await service._recover_fallback()
                refresh.assert_awaited_once()
            finally:service.close()

    async def create(self):
        service=await runtime.OnboardingRuntime.create(runtime.RuntimeConfig(**self.kw),self.db,registrar=None,scheduler=self.scheduler,http=self.w.http,runner=self.w.runner,base_env={'BUZZ_PRIVATE_KEY':'NEVER_CHILD'},clock=lambda:self.w.now)
        self.addCleanup(service.close);return service

    async def test_failed_startup_own_proof_recovers_and_handles_new_invite(self):
        self.w.bad_policy=True
        service=await self.create()
        self.assertNotIn('cli_agent',service.eligible_apps)
        self.assertNotIn(self.f.pub,service.effects.specs)
        self.w.bad_policy=False
        await service.drain()
        self.assertIn('cli_agent',service.eligible_apps)
        self.assertIn(self.f.pub,service.effects.specs)
        self.assertIs(service.effects.clients['cli_agent'],service.clients['cli_agent'])
        answer=service.enqueue_feed('cli_agent',{'type':'im.chat.member.bot.added_v1','app':'cli_agent',
            'chat_id':'oc_group','event_id':'evt_recovered','operator_id':{'open_id':'ou_owner','union_id':'on_owner'}})
        self.assertNotEqual(answer['toast']['type'],'error')
        await service.drain()
        rows=self.db.join_requests()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'requested')
        self.assertTrue(rows[0]['card_message_id']);self.assertEqual(len(self.w.cards),1)

    async def test_own_recovery_failed_proof_is_throttled_and_current_identity_required(self):
        self.w.bad_policy=True;service=await self.create()
        tick=[100.];service._retry_clock=lambda:tick[0]
        with mock.patch.object(service,'_fresh',wraps=service._fresh) as fresh:
            await service.drain();await service.drain()
            self.assertEqual(fresh.await_count,1)
            self.assertNotIn('cli_agent',service.eligible_apps)
            self.w.bad_policy=False
            # The originally protected own record now overlaps the old writer.
            self.f.save([self.f.agent]);tick[0]+=60
            await service.drain()
            self.assertEqual(fresh.await_count,2)
            self.assertNotIn('cli_agent',service.records)
            self.assertNotIn(self.f.pub,service.effects.specs)
            self.assertEqual(self.db.conn.execute('SELECT count(*) FROM agent').fetchone()[0],0)

    async def test_own_recovery_close_or_cancel_during_proof_never_admits(self):
        self.w.bad_policy=True;service=await self.create();self.w.bad_policy=False
        entered=asyncio.Event();release=asyncio.Event()
        async def waiting(record):entered.set();await release.wait()
        with mock.patch.object(service,'_fresh',side_effect=waiting):
            task=asyncio.create_task(service.drain())
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertNotIn('cli_agent',service.records)
            service._own_retry_at.clear();entered.clear()
            task=asyncio.create_task(service.drain())
            await entered.wait();service.close();release.set();await task
        self.assertNotIn('cli_agent',service.records)
        self.assertNotIn(self.f.pub,service.effects.specs)

    async def test_own_recovery_one_candidate_per_pass_and_bad_first_does_not_starve(self):
        from dataclasses import replace
        self.w.bad_policy=True;service=await self.create();self.w.bad_policy=False
        record=next(iter(service._pending_own.values()))
        blocked=replace(record,name='aaa-blocked')
        service._pending_own[blocked.name]=blocked
        original=service._fresh;seen=[]
        async def proof(candidate):
            seen.append(candidate.name)
            if candidate.name==blocked.name:raise ValueError('not verified')
            return await original(candidate)
        with mock.patch.object(service,'_fresh',side_effect=proof):
            await service.drain()
            self.assertEqual(seen,[blocked.name]);self.assertNotIn('cli_agent',service.records)
            await service.drain()
            self.assertEqual(seen,[blocked.name,record.name]);self.assertIn('cli_agent',service.records)

    async def test_own_recovery_does_not_adopt_changed_policy_app(self):
        self.w.bad_policy=True;service=await self.create()
        self.w.bad_policy=False;self.w.policy_app='cli_unknown'
        await service.drain()
        self.assertEqual(service.eligible_apps,())
        self.assertEqual(service.effects.specs,{})

    async def test_own_recovery_dispatch_queue_is_bounded_and_does_not_apply_effects(self):
        from types import SimpleNamespace
        self.w.bad_policy=True;service=await self.create();self.w.bad_policy=False
        self.db.register_agent(self.f.pub,owner_pubkey=self.f.owner,app_id='cli_agent',now=1000)
        for suffix in ('11111111','22222222'):
            request='JOIN-'+suffix
            self.db.create_join(request,self.f.pub,self.f.owner,'cli_agent','oc_'+suffix,kind='new_binding',now=1000)
            self.db.conn.execute("UPDATE join_request SET status='done' WHERE request_id=?",(request,))
        registrar=SimpleNamespace(check_own_profile=mock.Mock(),own_admitted=mock.AsyncMock(),
            restore=mock.AsyncMock(side_effect=[{'registered':0},{'registered':1}]))
        service.effects.registrar=registrar
        with mock.patch.object(service.effects,'apply',new_callable=mock.AsyncMock) as apply:
            await service.drain()
            registrar.restore.assert_awaited_once_with(request_id='JOIN-11111111')
            await service.drain()
            self.assertEqual(registrar.restore.await_args_list,[mock.call(request_id='JOIN-11111111'),mock.call(request_id='JOIN-22222222')])
            registrar.own_admitted.assert_awaited_once()
            apply.assert_not_awaited()
        self.assertEqual(self.w.cards,{})
        self.assertEqual({r['status'] for r in self.db.join_requests()},{'done'})

    async def test_own_recovery_current_owner_change_and_retired_sql_agent_stay_closed(self):
        self.w.bad_policy=True;service=await self.create();self.w.bad_policy=False
        original=self.f.env.read_text()
        self.f.write(self.f.env,original.replace(self.f.owner,'a'*64))
        await service.drain()
        self.assertNotIn('cli_agent',service.records)
        self.f.write(self.f.env,original);service._own_retry_at.clear()
        self.db.register_agent(self.f.pub,owner_pubkey=self.f.owner,app_id='cli_agent',now=1000)
        self.db.conn.execute("UPDATE agent SET status='retired'")
        await service.drain()
        self.assertNotIn('cli_agent',service.eligible_apps)
        self.assertEqual(self.db.conn.execute('SELECT status FROM agent').fetchone()[0],'retired')

    async def test_queue_diagnostics_exclude_person_chat_event_and_callback_data(self):
        service=await self.create()
        service.enqueue_feed('cli_agent',{'type':'im.chat.member.bot.added_v1','app':'cli_agent',
            'chat_id':'oc_private','event_id':'evt_private','operator_id':{'open_id':'ou_private','union_id':'on_private'}})
        value=service.diagnostics()
        self.assertEqual(value['queued'],1)
        self.assertEqual(value['agents'],1)
        self.assertEqual(value['active'],{})
        for secret in ('oc_private','evt_private','ou_private','on_private'):
            self.assertNotIn(secret,json.dumps(value))
    def bound(self):
        cfg={'channel_id':CHANNEL,'chat_id':'oc_group','people_api':{'base_url':'https://relay.test','signer_env_file':self.kw['owner_env_file']}}
        self.f.write(self.f.root/'bound.json',json.dumps(cfg))
        self.db.reconcile_bindings([BindingRecord('alpha',CHANNEL,'oc_group','cli_agent',str(self.f.root/'bound.json'),str(self.f.cfg),str(self.f.data),self.w.mirror,gs.chat_ref('oc_group'))],now=self.w.now)
        self.w.claims=[{'channel':CHANNEL,'chat_ref':gs.chat_ref('oc_group'),'claimed_at':900,'heartbeat':1000}]
    def test_config_requires_explicit_safe_fields_and_trust(self):
        for field in ['owner_env_file','relay_pubkey','template_config','binding_dir','legacy_join_path','catalog_path']:
            bad=dict(self.kw);bad[field]=''
            with self.assertRaises(runtime.RuntimeErrorNotice) as exc:runtime.RuntimeConfig(**bad)
            self.assertIn('怎么解决',str(exc.exception));self.assertIn('复制给 AI',str(exc.exception))
        with self.assertRaises(runtime.RuntimeErrorNotice):runtime.RuntimeConfig(**dict(self.kw,relay_url='https://attacker.test'))
        with self.assertRaises(runtime.RuntimeErrorNotice):runtime.RuntimeConfig(**dict(self.kw,version=True))
    async def test_factory_actual_adapters_explicit_paths_no_global_profiles(self):
        service=await self.create()
        for actual,cls in [(service.identity,IdentityResolver),(service.cards,BotCards),(service.coordinator,Coordinator),(service.effects,JoinEffects),(service.relay,Nip98Relay),(service.effects.runtime,AgentRuntime)]:self.assertIsInstance(actual,cls)
        self.assertEqual(service.effects.specs[self.f.pub].timer_config,str(self.f.legacy))
        self.assertEqual(service.effects.specs[self.f.pub].reader_app_id,'cli_agent')
        self.assertEqual(service.effects.specs[self.f.pub].template_config,self.kw['template_config'])
        self.assertEqual(service.eligible_apps,('cli_agent',));self.assertEqual(self.w.cards,{})
    async def test_reconnect_complete_requested_only_real_card_and_restart_dedupe(self):
        service=await self.create();service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'});await service.drain()
        rows=self.db.join_requests();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'requested');self.assertTrue(rows[0]['card_message_id'])
        self.assertEqual(rows[0]['card_generation'],1);self.assertEqual(len(self.w.cards),1)
        restored=await self.create();restored.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'});await restored.drain()
        self.assertEqual(len(self.db.join_requests()),1);self.assertEqual(len(self.w.cards),1)
        self.assertFalse(any('/contact/' in str(call[0]) for call in self.w.calls))
    async def test_partial_second_page_never_creates_from_first_page(self):
        service=await self.create();self.w.pages={'':{'items':[{'chat_id':'oc_group'}],'has_more':True,'page_token':'next'},'next':{'items':[]}}
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'});await service.drain()
        self.assertEqual(self.db.join_requests(),[]);self.assertIn('怎么解决',service.last_notice)
    async def test_incomplete_roster_or_bot_absence_no_request(self):
        service=await self.create()
        for attr in ['complete','bot']:
            setattr(self.w,attr,False);service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'});await service.drain();self.assertEqual(self.db.join_requests(),[]);setattr(self.w,attr,True)
    async def test_expired_or_foreign_claim_uncertain_not_new_binding(self):
        service=await self.create()
        for heartbeat in [1,1000]:
            self.w.claims=[{'channel':CHANNEL,'chat_ref':gs.chat_ref('oc_group'),'claimed_at':1,'heartbeat':heartbeat}]
            answer=await service.discover('cli_agent','oc_group');self.assertEqual(answer.status,'uncertain');self.assertEqual(self.db.join_requests(),[])
    async def test_bound_claim_and_fresh_people_owner_click_blocks_without_readback(self):
        self.bound();service=await self.create();answer=await service.discover('cli_agent','oc_group');self.assertEqual((answer.status,answer.binding_id),('bound','alpha'))
        service.enqueue_feed('cli_agent',{'type':'im.chat.member.bot.added_v1','app':'cli_agent','chat_id':'oc_group','event_id':'evt_invite','operator_id':{'open_id':'ou_other','union_id':'on_other'}});await service.drain()
        row=self.db.join_requests()[0];self.assertEqual(row['status'],'requested');self.assertEqual(row['kind'],'channel')
        self.w.people=False
        # The fresh actual app projection is insufficient without directory mapping.
        with self.assertRaises(ValueError):await service.identity.resolve('cli_agent',runtime.Operator('ou_owner','on_owner'),now=1000)
        self.w.people=True;person=await service.identity.resolve('cli_agent',runtime.Operator('ou_owner','on_owner'),now=1000);self.assertEqual(person.pubkey,self.f.owner)
        self.assertTrue(any('/people' in url for url,_ in self.w.http_calls))
    async def test_binding_heartbeat_during_network_read_does_not_invalidate_discovery(self):
        self.bound();service=await self.create();original=service.relay.read
        async def read(kind,*args,**kwargs):
            result=await original(kind,*args,**kwargs)
            if kind=='members':
                self.db.conn.execute("UPDATE binding SET heartbeat_at=heartbeat_at+1 WHERE binding_id='alpha'")
            return result
        service.relay.read=read
        self.assertEqual((await service.discover('cli_agent','oc_group')).status,'bound')
        people=await service._people_loader('cli_agent',1000)
        self.assertEqual(people.union_ids[self.f.owner],'on_owner')

    async def test_binding_authority_change_during_network_read_still_rejected(self):
        self.bound();service=await self.create();original=service.relay.read
        for field,value in [('status','paused'),('claimed_at',2000),('sync_app_id','cli_changed')]:
            with self.subTest(field=field):
                before=dict(self.db.bindings()[0])
                async def read(kind,*args,**kwargs):
                    result=await original(kind,*args,**kwargs)
                    if kind=='members':
                        self.db.conn.execute(f"UPDATE binding SET {field}=? WHERE binding_id='alpha'",(value,))
                    return result
                service.relay.read=read
                self.assertEqual((await service.discover('cli_agent','oc_group')).status,'uncertain')
                self.db.conn.execute(f"UPDATE binding SET {field}=? WHERE binding_id='alpha'",(before[field],))
                with self.assertRaises(runtime.RuntimeErrorNotice):await service._people_loader('cli_agent',1000)
                self.db.conn.execute(f"UPDATE binding SET {field}=? WHERE binding_id='alpha'",(before[field],))

    async def test_signed_policy_mismatch_or_new_legacy_overlap_blocks_discovery(self):
        service=await self.create();self.w.policy_app='cli_other'
        self.assertEqual((await service.discover('cli_agent','oc_group')).status,'uncertain');self.assertEqual(self.db.join_requests(),[])
        self.w.policy_app='cli_agent';self.f.save([self.f.agent]);self.assertEqual((await service.discover('cli_agent','oc_group')).status,'uncertain')
    async def test_no_binding_people_cannot_guess_owner_and_failures_redact(self):
        service=await self.create()
        with self.assertRaises(ValueError) as exc:await service.identity.owner_union(self.f.owner,'cli_agent',now=1000)
        self.assertIn('怎么解决',str(exc.exception));self.assertNotIn(self.f.owner_key,str(exc.exception));self.assertFalse(any('/people' in url for url,_ in self.w.http_calls))
        result=service.enqueue_feed('cli_attacker',{'type':'card.action.trigger','app':'cli_agent'});self.assertEqual(result['toast']['type'],'error')
    async def test_prepare_members_fresh_owner_and_unknown_users_are_explicit(self):
        self.bound();service=await self.create()
        self.db.create_join('JOIN-12345678',self.f.pub,self.f.owner,'cli_agent','oc_group',kind='channel',binding_id='alpha',now=1000)
        gen=self.db.rotate_card('JOIN-12345678','om_approved',now=1000)
        self.db.decide_join('JOIN-12345678','evt_approve',self.f.owner,'cli_agent','om_approved',gen,approved=True,now=1000)
        plan=self.db.ensure_effect_plan('JOIN-12345678',CHANNEL,'alpha',str(self.f.root/'mirror.env'),str(self.f.root/'bound.json'),now=1000)
        row=self.db.join_request('JOIN-12345678')
        self.w.extra_user=True
        answer=await service.prepare_members(row,plan)
        self.assertTrue(answer.complete);self.assertEqual(answer.people,((self.f.owner,'on_owner'),));self.assertEqual(answer.pending_union_ids,('on_unknown',))
        self.assertEqual(answer.checked_at,1000)
        self.w.people=False
        with self.assertRaises(runtime.RuntimeErrorNotice):await service.prepare_members(row,plan)
        self.w.people=True;self.w.complete=False
        with self.assertRaises(runtime.RuntimeErrorNotice):await service.prepare_members(row,plan)
        self.w.complete=True
        with self.assertRaises(runtime.RuntimeErrorNotice):await service.prepare_members(dict(row,owner_pubkey='a'*64),plan)
    async def test_protected_binding_directory_and_startup_document(self):
        link=self.f.root/'binding-link';link.symlink_to(self.f.root/'bindings',target_is_directory=True)
        config=runtime.RuntimeConfig(**dict(self.kw,binding_dir=str(link)))
        with self.assertRaises(runtime.RuntimeErrorNotice):await runtime.OnboardingRuntime.create(config,self.db,registrar=None,scheduler=self.scheduler,http=self.w.http,runner=self.w.runner)
        self.assertEqual(self.w.http_calls,[])
        path=self.f.root/'startup.json';self.f.write(path,json.dumps(dict(self.kw,trusted_relays=list(self.kw['trusted_relays']))))
        self.assertEqual(runtime.RuntimeConfig.load(path),runtime.RuntimeConfig(**self.kw))
        path.chmod(0o644)
        with self.assertRaises(runtime.RuntimeErrorNotice):runtime.RuntimeConfig.load(path)
    async def test_new_feed_during_card_io_keeps_service_awake(self):
        service=await self.create();self.w.send_gate=threading.Event();self.w.send_entered=threading.Event()
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        task=asyncio.create_task(service.drain())
        try:
            entered=await asyncio.to_thread(self.w.send_entered.wait,15)
            self.assertTrue(entered)
            service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        finally:self.w.send_gate.set()
        await asyncio.wait_for(task,20)
        self.assertTrue(service._wake.is_set())

    async def test_wrong_signed_query_kind_cannot_be_silently_dropped_as_unbound(self):
        service=await self.create();self.w.wrong_kind=True
        answer=await service.discover('cli_agent','oc_group')
        self.assertEqual(answer.status,'uncertain');self.assertEqual(self.db.join_requests(),[])

    async def test_live_invite_card_is_sent_between_reconnect_chat_checks(self):
        from unittest import mock
        service=await self.create()
        original=service._discovered
        scanned=[];observed=[]
        async def discovered(app,chat,event=None,operator=None):
            if event is None:
                scanned.append(chat)
                if chat=='oc_first':
                    service.enqueue_feed(app,{'type':'im.chat.member.bot.added_v1',
                        'app':app,'chat_id':'oc_group','event_id':'evt_live',
                        'operator_id':{'open_id':'ou_owner','union_id':'on_owner'}})
                elif chat=='oc_second':
                    # A real request and native card must have progressed,
                    # not only a queue toast, before scanning the next chat.
                    observed.append((len(self.w.cards),any(r['card_message_id'] for r in self.db.join_requests())))
                return
            await original(app,chat,event,operator)
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        with mock.patch.object(service,'chats',return_value=('oc_first','oc_second')), \
                mock.patch.object(service,'_discovered',side_effect=discovered):
            await service.drain()
            self.assertEqual(scanned, ['oc_first'])
            self.assertTrue(service._queue)
            await service.drain()
        self.assertEqual(scanned,['oc_first','oc_second'])
        self.assertEqual(observed,[(1,True)])
        self.assertEqual(len(self.w.cards),1)

    async def test_approved_retry_progresses_between_background_units_and_is_bounded_fair(self):
        from unittest import mock
        service=await self.create();now=[100.];service._retry_clock=lambda:now[0]
        rows=[{'request_id':'JOIN-a','status':'approved','callback_app_id':'cli_agent'},
              {'request_id':'JOIN-b','status':'applied','callback_app_id':'cli_agent'},
              {'request_id':'JOIN-fallback','status':'approved','callback_app_id':'cli_agent'}]
        order=[]
        async def tick(*,now,request_id):
            order.append(request_id)
            if request_id=='JOIN-a' and order.count('JOIN-a')==1:raise ValueError('temporary proof failure')
        async def chats(app):return tuple('oc_scan'+str(n) for n in range(38))
        async def scan(*args):
            order.append('scan')
            if order.count('scan')==1:now[0]+=61
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        with mock.patch.object(self.db,'join_requests',return_value=rows), \
             mock.patch.object(self.db,'join_request',side_effect=lambda r:next(v for v in rows if v['request_id']==r)), \
             mock.patch.object(self.db,'fallback_provenance',side_effect=lambda r:{} if r=='JOIN-fallback' else None), \
             mock.patch.object(service.coordinator,'tick',side_effect=tick), \
             mock.patch.object(service,'chats',side_effect=chats), \
             mock.patch.object(service,'_discovered',side_effect=scan):
            await service._foreground()  # First failed request still consumes its slot.
            await service._foreground()
            self.assertEqual(order,['JOIN-a'])
            # Recovery can run while 38 reconnect checks are still outstanding.
            await service.drain()
            self.assertEqual(order[:3],['JOIN-a','scan','JOIN-b'])
            self.assertEqual(order.count('scan'),1)
            self.assertTrue(service._queue)
            await service.drain()
            self.assertEqual(order.count('scan'),2)
            self.assertNotIn('JOIN-fallback',order[:40])
            service.close();now[0]+=61
            before=list(order);await service._foreground();self.assertEqual(order,before)

    async def test_each_reconnect_slice_runs_issuer_recovery_before_queue_is_empty(self):
        service=await self.create()
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        scanned=[]
        async def scan(app,chat):scanned.append(chat)
        with mock.patch.object(service,'chats',return_value=('oc_a','oc_b','oc_c')), \
             mock.patch.object(service,'_discovered',side_effect=scan), \
             mock.patch.object(service,'_refresh_issuers',new_callable=mock.AsyncMock) as refresh:
            for index in range(3):
                await service.drain()
                self.assertEqual(len(scanned),index+1)
                self.assertEqual(refresh.await_count,index+1)
                refresh.assert_awaited_with(limit=1)
        self.assertEqual(scanned,['oc_a','oc_b','oc_c'])
        self.assertFalse(service._queue)

    async def test_failed_first_chat_does_not_drop_reconnect_continuation(self):
        service=await self.create()
        service.enqueue_feed('cli_agent',{'type':'_connected','app':'cli_agent'})
        scanned=[]
        async def scan(app,chat):
            scanned.append(chat)
            if chat=='oc_a':raise RuntimeError('transient')
        with mock.patch.object(service,'chats',return_value=('oc_a','oc_b','oc_c')), \
             mock.patch.object(service,'_discovered',side_effect=scan), \
             mock.patch.object(service,'_refresh_issuers',new_callable=mock.AsyncMock):
            for _ in range(3):await service.drain()
        self.assertEqual(scanned,['oc_a','oc_b','oc_c'])
        self.assertFalse(service._queue)

    async def test_http_pool_injected_and_card_requests_keep_exact_group_lane(self):
        import test_hostd_http_pool as hp
        from hostd.http_pool import HttpPool
        server=hp.Server();pool=HttpPool(connection_factory=server.connect,scheduler=self.scheduler);self.addCleanup(pool.close)
        service=await runtime.OnboardingRuntime.create(runtime.RuntimeConfig(**self.kw),self.db,registrar=None,scheduler=self.scheduler,http_pool=pool,http=self.w.http,runner=self.w.runner,clock=lambda:self.w.now)
        self.addCleanup(service.close)
        client=service.clients['cli_agent'];self.assertIs(client.http_pool,pool)
        mid=await asyncio.to_thread(client.send_card,'oc_group','{}','00000000-0000-4000-8000-000000000001')
        self.assertEqual(mid,'om_fake');self.assertIsNone(client.chat_id)
        self.assertIn('oc_group',self.scheduler.chats)
        server.responses=[hp.Response(200,{'code':0,'data':{'items':[{'message_id':mid,'chat_id':'oc_group'}]}})]
        await asyncio.to_thread(client.message_view,mid,'union_id')
        self.assertEqual(self.w.calls,[])

    async def test_lifecycle_wakes_stops_without_closing_shared_scheduler(self):
        service=await self.create();task=service.start();self.assertIs(task,service.start());service.close();await asyncio.wait_for(task,2)
        self.assertEqual(self.scheduler.run(lambda:'still-open',app_id='cli_agent',timeout=1),'still-open')

if __name__=='__main__':unittest.main()
