"""Own-host claim publisher: actual signing/HTTP/CLI parsers, synthetic IO only."""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock
from datetime import timedelta
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_buzz_feishu_group_sync as base
import test_hostd_bot_clients as bots
from hostd import bot_clients as bc,bot_round as br,join_effects as effects,remote_approval as approval
from hostd.store import Store
import recovery_authority as authority

gs=base.FGS
setUpModule=base.setUpModule
tearDownModule=base.tearDownModule
AGENT_KEY=base.AGENT2_KEY;AGENT=base.AGENT2_PK
PIN_KEY='7'.zfill(64);PIN=gs._signer_pubkey(PIN_KEY)
OTHER_CHANNEL='00000000-0000-0000-0000-000000000002'


def profile(key,owner_key,when,condition=''):
    pub=gs._signer_pubkey(key);owner=gs._signer_pubkey(owner_key)
    digest=hashlib.sha256(f'nostr:agent-auth:{pub}:{condition}'.encode()).digest()
    tag=['auth',owner,condition,gs.sync.nk.schnorr_sign(digest,bytes.fromhex(owner_key),bytes(32)).hex()]
    return gs.sign_event(key,0,[tag],'{}',when)


def latest_policy(events,pub):
    return authority.latest([e for e in events if e['kind']==30177 and authority.tags(e,'d')==[['d',pub]]])


def parsed_app(events,roles,channel,chat,now,mirror,owner,claimed_at):
    own_profile=authority.latest([e for e in events if e['kind']==0 and e['pubkey']==AGENT])
    own_policy=latest_policy(events,AGENT)
    mirror_profile=authority.latest([e for e in events if e['kind']==0 and e['pubkey']==mirror])
    policy=latest_policy(events,mirror)
    roster=gs.sign_event(PIN_KEY,39002,[['d',channel]]+[['p',p,'',r] for p,r in roles.items()],'',now)
    scope=approval.ApprovalScope(AGENT,base.OWNER_PK,base.AGENT_APP,channel,gs.chat_ref(chat),mirror,owner,claimed_at)
    context=approval.ProofContext(scope,PIN,own_profile,own_policy,(mirror_profile,),(policy,),roster,now,True)
    return approval.sync_app(context)


class RoundPublisher(base.TmpCase):
    def assembly(self,*,policy=None,verified=True,pin_constructor=False):
        env=base.Env(self.tmp,binding_claim=True)
        cfg=gs.load_config(env.config);cfg['agents']={AGENT:cfg['agents'][base.AGENT_PK]};cfg['desk_pubkey']=AGENT
        base.write_owner_only(env.config,json.dumps(cfg))
        base.write_owner_only(self.tmp/'agent-cfg'/'config.json',json.dumps({'apps':[{'appId':base.AGENT_APP}]}))
        world=bots.BotWorld(self.tmp)
        world.members=[dict(m,pubkey=AGENT) if m['pubkey']==base.AGENT_PK else m for m in world.members if m['pubkey']!=base.CAROL_PK]
        world.members=list({m['pubkey']:m for m in world.members}.values())
        world.needs_auth_tag.clear()
        world.claim_roster_mode='valid'
        when=int(base.NOW.timestamp())
        world.relay_events=[profile(AGENT_KEY,base.OWNER_KEY,when-10),profile(base.MIRROR_KEY,base.OWNER_KEY,when-10),
            gs.sign_event(base.OWNER_KEY,30177,[['d',AGENT]],json.dumps({'feishu':{'app_id':base.AGENT_APP}}),when-10)]
        def http(url,headers,timeout,*,body=None):
            payload=json.loads(body) if body is not None else None
            if isinstance(payload,list) and payload and payload[0].get('kinds')==[39002]:
                auth=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]))
                self.assertTrue(gs._nip01_event_verified(auth));self.assertEqual(auth['pubkey'],base.OWNER_PK)
                authority.exact_tag(auth,'u',url);authority.exact_tag(auth,'method','POST')
                authority.exact_tag(auth,'payload',hashlib.sha256(body).hexdigest())
                stamp=int(world.clock.timestamp())
                tags=[['d',base.CHANNEL]]+[['p',m['pubkey'],'',m['role']] for m in world.members]
                roster=gs.sign_event(PIN_KEY,39002,tags,'',stamp)
                rows=[roster]
                if world.claim_roster_mode=='forged':roster['sig']='0'*128
                if world.claim_roster_mode=='revoked':
                    revoked=[t if t[:2]!=['p',AGENT] else ['p',AGENT,'','member'] for t in tags]
                    rows.append(gs.sign_event(PIN_KEY,39002,revoked,'',stamp+1))
                return 200,json.dumps(rows).encode()
            return world.http_get(url,headers,timeout,body=body)
        clients=bc.build_clients(cfg,env.base_env,runner=world,http=http,trusted_relays=('https://relay.test',))
        kwargs={'claim_relay_pubkey':PIN}
        run=br.HostdRound(cfg,clients,gs.State(binding=base.CHANNEL+'|'+base.CHAT),gs._new_report(),base.NOW,lambda:None,auth_clock=lambda:world.clock,**kwargs)
        if policy is not None:self.seed(world,run,policy)
        if verified:run.verify_identities();run.load_people();run.verify_desk()
        return env,cfg,world,run

    def seed(self,world,run,*,sync=None,extra=False):
        stamp=run.now_ts
        entry={'channel':base.CHANNEL,'chat_ref':gs.chat_ref(base.CHAT),'claimed_at':stamp-100,'heartbeat':stamp,'policy':gs.claim_policy(run.cfg)}
        if sync is not None:entry['sync_app']=sync
        doc={'name':'reviewed mirror','respond_to':'allowlist','custom':{'preserved':True},'feishu':{'mirror':True,'bindings':[entry]}}
        if extra:doc['feishu']['bindings'].append({'channel':OTHER_CHANNEL,'chat_ref':'b'*64,'claimed_at':stamp-200,'heartbeat':stamp,'sync_app':{'version':1,'app_id':'cli_other'},'policy':{'custom':'keep'}})
        head=latest_policy(world.relay_events,base.MIRROR_PK)
        created=max(stamp-1,head['created_at']+1) if head is not None else stamp-1
        event=gs.sign_event(base.OWNER_KEY,30177,[['d',base.MIRROR_PK]],json.dumps(doc),created)
        world.relay_events=[e for e in world.relay_events if not(e['kind']==30177 and authority.tags(e,'d')==[['d',base.MIRROR_PK]])]+[event]
        return doc

    def policy(self,world):return json.loads(latest_policy(world.relay_events,base.MIRROR_PK)['content'])
    def claim_writes(self,world):return [e for e in world.relay_writes if e['kind']==30177]
    def no_publication(self,run,world):
        before=len(self.claim_writes(world))
        try:run.check_claims()
        except gs.GroupSyncError:pass
        self.assertEqual(len(self.claim_writes(world)),before)

    def test_parent_claim_publishes_actual_local_sync_app_and_signed_parser_proof(self):
        _,_,world,run=self.assembly();self.assertEqual(run.check_claims(),'won')
        event=self.claim_writes(world)[0];self.assertTrue(gs._nip01_event_verified(event));self.assertEqual(event['pubkey'],base.OWNER_PK)
        doc=self.policy(world);self.assertNotIn('app_id',doc['feishu'])
        entry=doc['feishu']['bindings'][0];self.assertEqual(entry.get('sync_app'),{'version':1,'app_id':base.AGENT_APP})
        proof=parsed_app(world.relay_events,run.roles,base.CHANNEL,base.CHAT,run.now_ts,base.MIRROR_PK,base.OWNER_PK,entry['claimed_at'])
        self.assertEqual(proof.app_id,base.AGENT_APP)

    def test_missing_recent_sync_field_repaired_without_heartbeat_interval(self):
        _,_,world,run=self.assembly();old=self.seed(world,run)
        self.assertEqual(run.check_claims(),'won');self.assertEqual(len(self.claim_writes(world)),1)
        entry=self.policy(world)['feishu']['bindings'][0]
        self.assertEqual(entry.get('sync_app'),{'version':1,'app_id':base.AGENT_APP});self.assertEqual(entry['claimed_at'],old['feishu']['bindings'][0]['claimed_at'])

    def test_changed_or_malformed_recent_sync_field_is_repaired_immediately(self):
        _,_,world,run=self.assembly()
        for value in ({'version':1,'app_id':'cli_foreign'},{'version':True,'app_id':base.AGENT_APP},{'version':2,'app_id':base.AGENT_APP}):
            with self.subTest(value=value):
                self.seed(world,run,sync=value);before=len(self.claim_writes(world));run.check_claims()
                self.assertEqual(len(self.claim_writes(world)),before+1)
                self.assertEqual(self.policy(world)['feishu']['bindings'][0]['sync_app'],{'version':1,'app_id':base.AGENT_APP})

    def test_heartbeat_preserves_sync_stable_claim_other_bindings_and_policy_fields(self):
        _,_,world,run=self.assembly();old=self.seed(world,run,sync={'version':1,'app_id':base.AGENT_APP},extra=True)
        run.now=base.NOW+timedelta(seconds=gs.CLAIM_HEARTBEAT_SECONDS+1);world.clock=run.now
        run.check_claims();doc=self.policy(world)
        self.assertEqual(doc['feishu']['bindings'][0].get('sync_app'),old['feishu']['bindings'][0]['sync_app'])
        self.assertEqual(doc['feishu']['bindings'][0]['claimed_at'],old['feishu']['bindings'][0]['claimed_at'])
        self.assertEqual(doc['feishu']['bindings'][1],old['feishu']['bindings'][1]);self.assertEqual(doc['custom'],old['custom']);self.assertEqual(doc['respond_to'],old['respond_to'])

    def test_matching_recent_field_does_not_needlessly_publish(self):
        _,_,world,run=self.assembly();self.seed(world,run,sync={'version':1,'app_id':base.AGENT_APP})
        run.check_claims();self.assertEqual(self.claim_writes(world),[])

    def test_removed_channel_bot_and_group_bot_cannot_attest_from_old_setup(self):
        _,_,world,run=self.assembly()
        original=copy.deepcopy(world.members)
        world.members=[dict(m,role='member') if m['pubkey']==AGENT else m for m in original];self.no_publication(run,world)
        world.members=original;world.bots.pop(base.AGENT_APP);self.no_publication(run,world)

    def test_actual_profile_or_read_scope_change_cannot_publish(self):
        _,_,world,run=self.assembly()
        base.write_owner_only(self.tmp/'agent-cfg'/'config.json',json.dumps({'apps':[{'appId':'cli_wrong'}]}));self.no_publication(run,world)
        base.write_owner_only(self.tmp/'agent-cfg'/'config.json',json.dumps({'apps':[{'appId':base.AGENT_APP}]}));world.bot_scope_failure=True;self.no_publication(run,world)

    def test_public_own_policy_or_empty_oa_owner_change_cannot_attest(self):
        _,_,world,run=self.assembly();stamp=run.now_ts
        world.relay_events.append(gs.sign_event(base.OWNER_KEY,30177,[['d',AGENT]],json.dumps({'feishu':{'app_id':'cli_wrong'}}),stamp));self.no_publication(run,world)
        world.relay_events=[e for e in world.relay_events if not(e['kind']==30177 and authority.tags(e,'d')==[['d',AGENT]])]
        world.relay_events.append(gs.sign_event(base.OWNER_KEY,30177,[['d',AGENT]],json.dumps({'feishu':{'app_id':base.AGENT_APP}}),stamp))
        world.relay_events.append(profile(AGENT_KEY,base.OWNER_KEY,stamp+1,'kind=9'));self.no_publication(run,world)

    def test_explicit_constructor_pin_is_used_for_actual_signed_roster(self):
        _,_,world,run=self.assembly(pin_constructor=True)
        self.assertEqual(run.claim_relay_pubkey,PIN);run.check_claims()
        self.assertEqual(self.policy(world)['feishu']['bindings'][0].get('sync_app'),{'version':1,'app_id':base.AGENT_APP})

    def test_missing_or_wrong_explicit_relay_pin_cannot_announce(self):
        for pin in (None,'f'*64):
            with self.subTest(pin=pin):
                _,_,world,run=self.assembly();run.claim_relay_pubkey=pin;self.no_publication(run,world)

    def test_forged_actual_roster_cannot_announce(self):
        _,_,world,run=self.assembly();world.claim_roster_mode='forged';self.no_publication(run,world)

    def test_latest_signed_roster_revocation_beats_old_valid_bot_role(self):
        _,_,world,run=self.assembly();world.claim_roster_mode='revoked';self.no_publication(run,world)

    def test_mixed_valid_and_forged_packets_are_pending_not_filtered_complete(self):
        for kind in (39002,0,30177):
            with self.subTest(kind=kind):
                _,_,world,run=self.assembly();original=run.clients.http
                def mixed(url,headers,timeout,*,body=None):
                    status,data=original(url,headers,timeout,body=body)
                    payload=json.loads(body) if body is not None else None
                    selected=(isinstance(payload,list) and payload and payload[0].get('kinds')==[kind]
                        and (kind==39002 or kind==0 and payload[0].get('authors')==sorted({AGENT,base.MIRROR_PK})
                             or kind==30177 and payload[0].get('#d')==[AGENT]))
                    if selected:
                        rows=json.loads(data);old=rows[0]
                        key={39002:PIN_KEY,0:AGENT_KEY if old['pubkey']==AGENT else base.MIRROR_KEY,30177:base.OWNER_KEY}[kind]
                        # A valid historical answer cannot establish completeness
                        # when the same signed-query packet contains a bad frame.
                        invalid=gs.sign_event(key,kind,old['tags'],old['content'],old['created_at']+1)
                        invalid['sig']='0'*128
                        return status,json.dumps(rows+[invalid]).encode()
                    return status,data
                run.clients.http=mixed;self.no_publication(run,world)

    def test_withdrawal_removes_only_exact_entry_even_after_bot_leaves(self):
        _,_,world,run=self.assembly();old=self.seed(world,run,sync={'version':1,'app_id':base.AGENT_APP},extra=True)
        view=gs.read_binding_claims(run._relay_claims,also={base.MIRROR_PK});world.bots.pop(base.AGENT_APP)
        run._write_claim(view,None)
        doc=self.policy(world);self.assertEqual(doc['feishu']['bindings'],old['feishu']['bindings'][1:]);self.assertEqual(doc['custom'],old['custom'])

    def test_legacy_round_publisher_does_not_add_new_sync_app_field(self):
        env,cfg,world,_=self.assembly()
        clients=gs._clients(cfg,env.base_env,world,http=world.http_get)
        legacy=gs.Round(cfg,clients,gs.State(),gs._new_report(),base.NOW,lambda:None,auth_clock=lambda:base.NOW)
        legacy.verify_identities();legacy.load_people();legacy.verify_desk();legacy.check_claims()
        self.assertNotIn('sync_app',self.policy(world)['feishu']['bindings'][0])


class NewBindingPublisher(base.TmpCase):
    def fixture(self):
        # The legacy speed fixture fixes Schnorr nonce bytes to zero; use a
        # valid synthetic private key for actual new mirror generation.
        counter=iter(range(1,10000))
        patch=mock.patch.object(effects.secrets,'token_hex',side_effect=lambda n:('8'.zfill(64) if n==32 else f'{next(counter):0{n*2}x}'))
        patch.start();self.addCleanup(patch.stop)
        env=base.Env(self.tmp,binding_claim=True);template=gs.load_config(env.config)
        template['agents']={AGENT:template['agents'][base.AGENT_PK]};template['desk_pubkey']=AGENT
        base.write_owner_only(env.config,json.dumps(template))
        profile_dir=self.tmp/'agent-cfg';data_dir=self.tmp/'agent-data'
        base.write_owner_only(profile_dir/'config.json',json.dumps({'apps':[{'appId':base.AGENT_APP}]}))
        prompt=base.write_owner_only(self.tmp/'prompt','Prompt\n'+effects.legacy.PROMPT_BEGIN+'\n'+effects.legacy.PROMPT_END+'\n')
        responsible=base.write_owner_only(self.tmp/'responsible','{"channels":[]}')
        agentenv=base.write_owner_only(self.tmp/'agent.env',f'BUZZ_PRIVATE_KEY={AGENT_KEY}\nBUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\nBUZZ_ACP_CHANNELS={base.CHANNEL}\nBUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
        timer=base.write_owner_only(self.tmp/'timer',json.dumps({'version':1,'owner_pubkey':base.OWNER_PK,'agents':[]}))
        binding_dir=self.tmp/'bindings';binding_dir.mkdir(mode=0o700)
        spec=effects.AgentSpec(AGENT,base.OWNER_PK,base.AGENT_APP,str(agentenv),str(prompt),str(responsible),'agent.service',str(timer),str(self.tmp),str(binding_dir),base.AGENT_APP,str(profile_dir),str(data_dir),str(env.config))
        self.db=Store(self.tmp/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.db.register_agent(AGENT,owner_pubkey=base.OWNER_PK,app_id=base.AGENT_APP,now=10)
        self.db.create_join('JOIN-12345678',AGENT,base.OWNER_PK,base.AGENT_APP,base.CHAT,kind='new_binding',now=10)
        self.db.conn.execute("UPDATE join_request SET status='approved'")
        self.rows={AGENT:profile(AGENT_KEY,base.OWNER_KEY,95)}
        self.policies={AGENT:gs.sign_event(base.OWNER_KEY,30177,[['d',AGENT]],json.dumps({'feishu':{'app_id':base.AGENT_APP}}),95)}
        self.roles={};self.channel='';self.publications=[];self.claim_roles=[];self.clock=100;self.complete=True;self.preparation_age=0
        self.bot_calls=[]
        def cli(argv,**kwargs):
            self.bot_calls.append(argv);self.assertEqual(argv[argv.index('--as')+1],'bot');self.assertEqual(kwargs['env']['LARKSUITE_CLI_CONFIG_DIR'],str(profile_dir))
            if '/open-apis/application/v6/scopes' in argv:data={'scopes':[{'scope_name':scope,'scope_type':'tenant','grant_status':1} for scope in bc.READ_SCOPE_GROUPS]}
            else:
                kind=argv[argv.index('--member-types')+1]
                data={'has_more':False,'truncations':[] if self.complete else [{'limit':1}]}
                if kind=='user':data.update(users=[{'member_id':'on_owner','name':'ignored'}],user_total=1)
                else:data.update(bots=[{'app_id':base.AGENT_APP,'member_id':'ou_bot'}],bot_total=1)
            return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':data}),'')
        self.bot=bc.BotLarkCli(base.AGENT_APP,profile_dir,data_dir,base_env={'HOME':str(self.tmp)},runner=cli)
        def http(url,headers,timeout,*,body=None):
            signed=json.loads(base64.b64decode(headers['Authorization'].split(' ',1)[1]));self.assertTrue(gs._nip01_event_verified(signed));self.assertIn(signed['pubkey'],(base.OWNER_PK,gs._signer_pubkey('8'.zfill(64))))
            authority.exact_tag(signed,'u',url)
            if body is None:
                authority.exact_tag(signed,'method','GET')
                channel=url.split('/channels/',1)[1].split('/',1)[0]
                return 200,json.dumps({'channel':channel,'as_of':'1970-01-01T00:01:40Z','people':{},'union_ids':{base.OWNER_PK:'on_owner'},'emails':{}}).encode()
            authority.exact_tag(signed,'method','POST');authority.exact_tag(signed,'payload',hashlib.sha256(body).hexdigest())
            data=json.loads(body)
            if url.endswith('/query'):
                rows=[]
                source=list(self.rows.values())+list(self.policies.values())
                if self.channel:
                    source += [gs.sign_event(PIN_KEY,39000,[['d',self.channel],['name','hostd JOIN-12345678'],['private']],'',self.clock),
                               gs.sign_event(PIN_KEY,39002,[['d',self.channel]]+[['p',p,'',r] for p,r in self.roles.items()],'',self.clock)]
                for f in data:
                    for e in source:
                        if e['kind'] not in f['kinds'] or ('authors' in f and e['pubkey'] not in f['authors']):continue
                        if any(k.startswith('#') and not any(t[:1]==[k[1:]] and len(t)>1 and t[1] in v for t in e['tags']) for k,v in f.items()):continue
                        rows.append(e)
                return 200,json.dumps(rows).encode()
            event=data;self.assertTrue(gs._nip01_event_verified(event));self.publications.append(event)
            if event['kind']==9007:self.channel=event['tags'][0][1];self.roles[base.OWNER_PK]='owner'
            elif event['kind']==0:self.rows[event['pubkey']]=event
            elif event['kind']==9000:self.roles[event['tags'][1][1]]=event['tags'][2][1]
            elif event['kind']==30177:
                self.claim_roles.append(dict(self.roles));self.policies[event['tags'][0][1]]=event
            return 200,json.dumps({'accepted':True,'event_id':event['id']}).encode()
        self.relay=effects.Nip98Relay('https://relay.test',env.signer_env,PIN,http=http,trusted_relays=('https://relay.test',),clock=lambda:self.clock)
        owner=self
        class Registrar:
            async def prepare_members(self,row,plan):
                # Compute the receipt from actual owner-authenticated people IO
                # and the actual selected bot parser; never return preset readiness.
                answer=await owner.relay.read('people',base.CHANNEL)
                listing=owner.bot.member_listing(row['chat_id'],'union_id')
                reverse={u:p for p,u in answer.union_ids.items()}
                people=tuple(sorted((reverse[u],u) for u in listing.users if u in reverse))
                pending=tuple(sorted(set(listing.users)-set(reverse)))
                digest=hashlib.sha256(json.dumps([people,pending],separators=(',',':')).encode()).hexdigest()
                return effects.MemberPreparation(base.AGENT_APP,row['chat_id'],owner.clock-owner.preparation_age,listing.complete,people,pending,digest)
            async def register(self,*args):pass
            async def readback(self,*args):return None  # No fabricated root readiness/activation.
        class Ops:
            restarts=0;running=effects.legacy.parse_env(Path(agentenv).read_text());log=''
            def unit_status(self,unit):return 'loaded','active'
            def is_busy(self,unit):return False
            def process(self,unit):return 123,self.restarts+1,self.running
            def invocation_id(self,unit):return f'{self.restarts+1:032x}'
            def journal_invocation(self,unit,pid,invocation):return self.log
            def restart(self,unit):self.restarts+=1;self.running=effects.legacy.parse_env(Path(agentenv).read_text());self.log='subscribed to channel '+owner.channel
        self.adapter=effects.JoinEffects(self.db,{AGENT:spec},self.relay,effects.AgentRuntime(Ops()),Registrar(),clients={base.AGENT_APP:self.bot},clock=lambda:self.clock)
        return self.db.join_request('JOIN-12345678'),spec

    def test_real_new_effect_claim_attestation_after_actual_member_writes_and_parser_proof(self):
        row,spec=self.fixture();asyncio.run(self.adapter.apply(row))
        plan=self.db.effect_plan(row['request_id']);event=self.policies[plan['mirror_pubkey']]
        self.assertEqual(self.claim_roles[0].get(AGENT),'bot','claim must not attest before actual bot membership')
        self.assertEqual(self.claim_roles[0].get(plan['mirror_pubkey']),'bot')
        entry=json.loads(event['content'])['feishu']['bindings'][0]
        self.assertEqual(entry.get('sync_app'),{'version':1,'app_id':base.AGENT_APP})
        proof=parsed_app(list(self.rows.values())+list(self.policies.values()),self.roles,plan['channel_id'],base.CHAT,self.clock,plan['mirror_pubkey'],base.OWNER_PK,entry['claimed_at'])
        self.assertEqual(proof.app_id,base.AGENT_APP);self.assertEqual(self.db.bindings()[0]['status'],'pending')

    def direct_claim_fixture(self):
        row,spec=self.fixture();plan=self.adapter._plan(row,spec)
        _,mirror,_,published_profile=self.adapter._mirror(row,spec,plan)
        plan=self.db.effect_plan(row['request_id']);self.channel=plan['channel_id']
        self.rows[mirror]=published_profile
        self.roles={base.OWNER_PK:'owner',AGENT:'bot',mirror:'bot'}
        return row,plan,self.adapter._candidate_config(row,spec,plan)

    def test_original_unknown_legacy_claim_intent_never_repinned_or_replayed(self):
        row,plan,cfg=self.direct_claim_fixture()
        entry={'channel':plan['channel_id'],'chat_ref':gs.chat_ref(row['chat_id']),'claimed_at':plan['created_at'],'heartbeat':100,'policy':gs.claim_policy(cfg)}
        content=gs.with_binding_claim(None,name='hostd-mirror',channel=plan['channel_id'],ref=entry['chat_ref'],entry=entry)
        original=self.relay.event(30177,[['d',plan['mirror_pubkey']]],content,100)
        digest=effects._hash([original['id']])
        self.db.reserve_effect_step(row['request_id'],'claim',digest,now=100,operation_at=100)
        self.db.defer_effect_step(row['request_id'],'claim',status='unknown',now=100)
        before=copy.deepcopy(self.db.effect_steps(row['request_id']))
        try:asyncio.run(self.adapter._claim(row,plan,cfg))
        except effects.EffectError:pass
        self.assertFalse(self.claim_roles);self.assertEqual(self.db.effect_steps(row['request_id']),before)

    def test_current_denied_sql_snapshot_cannot_publish_direct_claim(self):
        row,plan,cfg=self.direct_claim_fixture()
        self.db.conn.execute("UPDATE join_request SET status='denied'")
        row=self.db.join_request(row['request_id'])
        with self.assertRaises(effects.EffectError):asyncio.run(self.adapter._claim(row,plan,cfg))
        self.assertFalse(self.claim_roles)

    def test_stale_preparation_and_incomplete_members_cannot_publish_new_claim(self):
        row,_=self.fixture();self.preparation_age=301
        with self.assertRaises(effects.EffectError):asyncio.run(self.adapter.apply(row))
        self.assertEqual(self.publications,[])
        self.preparation_age=0;self.complete=False
        with self.assertRaises(effects.EffectError):asyncio.run(self.adapter.apply(row))
        self.assertEqual(self.publications,[])

    def test_changed_real_local_profile_after_preparation_cannot_attest(self):
        row,spec=self.fixture();original=self.relay.publish
        def changed(event):
            result=original(event)
            if event['kind']==9007:base.write_owner_only(Path(spec.reader_config_dir)/'config.json',json.dumps({'apps':[{'appId':'cli_wrong'}]}))
            return result
        self.relay.publish=changed
        with self.assertRaises(effects.EffectError):asyncio.run(self.adapter.apply(row))
        self.assertFalse(self.claim_roles)

if __name__=='__main__':unittest.main()
