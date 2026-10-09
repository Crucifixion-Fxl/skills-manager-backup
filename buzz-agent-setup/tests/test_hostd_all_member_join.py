"""All-member JOIN observation preserves scope and never replays UNKNOWN."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path[:0]=[str(TESTS),str(TESTS.parent/'scripts'),str(TESTS.parent/'scripts'/'hostd')]
from hostd import binding_worker  # production import order
from hostd import join_effects as effects
from hostd.store import Store, BindingRecord
from hostd import native_runtime
import test_hostd_native_runtime as native_fixture
import buzz_feishu_group_sync as gs

CHANNEL='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'

class AllMemberNative(unittest.TestCase):
    setUp=native_fixture.NativeProof.setUp
    write=native_fixture.NativeProof.write
    write_snapshot=native_fixture.NativeProof.write_snapshot

    def mode(self):
        self.write(self.agent_env,'\n'.join(x for x in self.agent_env.read_text().splitlines()
            if not x.startswith('BUZZ_ACP_CHANNELS='))+'\nBUZZ_ACP_SUBSCRIBE=mentions\n')
        self.actual.pop('BUZZ_ACP_CHANNELS');self.actual['BUZZ_ACP_SUBSCRIBE']='mentions'

    def test_actual_generation_target_subscription_all_member_ready_and_lazy(self):
        self.mode()
        for phase in ('ready','starting'):
            self.write_snapshot(dict(self.current,phase=phase,channels=[*self.current['channels'],OTHER]))
            self.assertTrue(native_runtime.subscribed(self.spec,self.channel,self.identity))
        self.assertTrue(native_runtime.subscribed(self.spec,OTHER,self.identity))
        self.assertFalse(native_runtime.subscribed(self.spec,'33333333-3333-4333-8333-333333333333',self.identity))
        self.write_snapshot(dict(self.current,phase='starting',channels=[]))
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

    def test_mode_drift_explicit_empty_and_wrong_process_fail_closed(self):
        self.mode()
        for key,value in [('BUZZ_ACP_SUBSCRIBE','all'),('BUZZ_ACP_CHANNELS','')]:
            saved=dict(self.actual);self.actual[key]=value
            self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))
            self.actual.clear();self.actual.update(saved)
        self.write_snapshot(dict(self.current,process_start_ticks='0'))
        self.assertFalse(native_runtime.subscribed(self.spec,self.channel,self.identity))

class AllMemberJoin(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.base.chmod(0o700)
        self.owner_key,self.agent_key,self.relay_key='2'*64,'3'*64,'4'*64
        self.owner,self.agent,self.pin=map(gs._signer_pubkey,(self.owner_key,self.agent_key,self.relay_key))
        def file(name,text):
            p=self.base/name;p.write_text(text);p.chmod(0o600);return str(p)
        self.prompt=file('prompt',effects.legacy.PROMPT_BEGIN+'\n'+CHANNEL+'\n'+effects.legacy.PROMPT_END+'\n')
        self.responsible=file('responsible',json.dumps({'channels':[CHANNEL]}))
        self.env=file('agent.env',f'BUZZ_PRIVATE_KEY={self.agent_key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_ACP_SUBSCRIBE=mentions\nBUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n')
        timer=file('timer',json.dumps({'version':1,'owner_pubkey':self.owner,'agents':[]}))
        owner_env=file('owner.env','BUZZ_PRIVATE_KEY='+self.owner_key+'\n')
        config=file('config',json.dumps({'channel_id':CHANNEL,'chat_id':'oc_group','sync_app_id':'cli_reader'}))
        self.spec=effects.AgentSpec(self.agent,self.owner,'cli_agent',self.env,self.prompt,self.responsible,'agent.service',timer,str(self.base))
        self.db=Store(self.base/'sql/hostd.db');self.addCleanup(self.db.close)
        self.db.reconcile_bindings([BindingRecord('alpha',CHANNEL,'oc_group','cli_reader',config,'/reader','/data')],now=10)
        self.db.register_agent(self.agent,owner_pubkey=self.owner,app_id='cli_agent',now=10)
        self.db.create_join('JOIN-12345678',self.agent,self.owner,'cli_agent','oc_group',kind='channel',binding_id='alpha',now=10)
        gen=self.db.reserve_join_card('JOIN-12345678',now=10)
        self.db.finish_join_card('JOIN-12345678',gen,'om_card',now=10)
        self.assertTrue(self.db.decide_join('JOIN-12345678','evt_approval',self.owner,'cli_agent','om_card',gen,approved=True,now=11))
        self.row=self.db.join_request('JOIN-12345678')
        self.plan=self.db.ensure_effect_plan(self.row['request_id'],CHANNEL,'alpha',self.env,config,now=11)
        self.members=True;self.signature=True;self.native=True;self.app_present=True;self.native_calls=0
        auth=['auth',self.owner,'',gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{self.agent}:'.encode()).digest(),bytes.fromhex(self.owner_key),b'\0'*32).hex()]
        profile=gs.sign_event(self.agent_key,0,[auth],'{}',10)
        def http(url,headers,timeout,body=None):
            self.assertTrue(url.endswith('/query'),'readback must never publish')
            kind=json.loads(body)[0]['kinds'][0]
            roster=gs.sign_event(self.relay_key if self.signature else '5'*64,39002,[['d',CHANNEL],['p',self.owner,'','owner']]+([['p',self.agent,'','bot']] if self.members else []),'',20)
            return 200,json.dumps([roster] if kind==39002 else [profile] if kind==0 else []).encode()
        relay=effects.Nip98Relay('https://relay.test',owner_env,self.pin,http=http,trusted_relays=('https://relay.test',),clock=lambda:100)
        test=self
        class Ops:
            def unit_status(self,u):return 'loaded','active'
            def process(self,u):return 123,1,effects.legacy.parse_env(Path(test.env).read_text())
            def invocation_id(self,u):return 'a'*32
            def native_subscription(self,s,c,i):test.native_calls+=1;return test.native and c==CHANNEL
            def restart(self,u):raise AssertionError('no restart')
            def journal_invocation(self,*a):raise AssertionError('all-member requires native proof')
        class Client:
            app_id='cli_agent'
            def member_listing(self,*a,**k):return gs.MemberListing({}, {'cli_agent':'ou_bot'} if test.app_present else {},True)
        class Registrar:
            async def register(self,*a):raise AssertionError('readback cannot dispatch')
            async def readback(self,row,plan):return effects.RegistrationProof(row['request_id'],'alpha',CHANNEL,'oc_group','cli_agent',True,True,True,True,100,'e'*64)
        self.ops=Ops()
        self.adapter=effects.JoinEffects(self.db,{self.agent:self.spec},relay,effects.AgentRuntime(self.ops),Registrar(),clients={'cli_agent':Client()},clock=lambda:100)

    def test_unknown_env_reconciles_without_file_write_then_fresh_readback(self):
        digest=effects._hash([self.agent,CHANNEL,self.env])
        self.db.reserve_effect_step(self.row['request_id'],'agent_env',digest,now=20)
        self.db.defer_effect_step(self.row['request_id'],'agent_env',status='unknown',now=20)
        before={p:p.read_bytes() for p in (Path(self.env),Path(self.prompt),Path(self.responsible))}
        with mock.patch.object(self.adapter.files,'replace',side_effect=AssertionError('no file effect')):
            self.assertTrue(self.adapter._config_effects(self.row,self.spec,CHANNEL))
            self.assertTrue(asyncio.run(self.adapter.readback(self.row)).verified)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        step=next(x for x in self.db.effect_steps(self.row['request_id']) if x['step']=='agent_env')
        self.assertEqual((step['status'],step['attempts']),('verified',2))
        self.assertGreater(self.native_calls,0)

    def test_fresh_signed_member_native_and_app_are_all_required(self):
        for field in ('members','signature','native','app_present'):
            with self.subTest(field=field):
                setattr(self,field,False)
                self.assertFalse(asyncio.run(self.adapter.readback(self.row)).verified)
                self.assertEqual(self.db.conn.execute('SELECT count(*) FROM agent_chat').fetchone()[0],0)
                setattr(self,field,True)

    def test_revoked_approval_and_explicit_empty_scope_never_pass(self):
        self.db.conn.execute("UPDATE join_request SET status='denied'")
        self.assertFalse(asyncio.run(self.adapter.readback(self.row)).verified)
        self.db.conn.execute("UPDATE join_request SET status='approved'")
        Path(self.env).write_text(Path(self.env).read_text()+'BUZZ_ACP_CHANNELS=\n')
        self.assertFalse(asyncio.run(self.adapter.readback(self.row)).verified)

    def test_readback_alone_observes_unknown_without_dispatch_or_step_rewrite(self):
        digest=effects._hash([self.agent,CHANNEL,self.env])
        self.db.reserve_effect_step(self.row['request_id'],'agent_env',digest,now=20)
        self.db.defer_effect_step(self.row['request_id'],'agent_env',status='unknown',now=20)
        before=self.db.effect_steps(self.row['request_id'])
        with mock.patch.object(self.adapter,'apply',side_effect=AssertionError('no replay')):
            self.assertTrue(asyncio.run(self.adapter.readback(self.row)).verified)
        self.assertEqual(before,self.db.effect_steps(self.row['request_id']))

    def test_all_member_never_falls_back_to_historical_journal(self):
        with mock.patch.object(self.ops,'native_subscription',return_value=None):
            self.assertFalse(self.adapter.runtime.verify(self.spec,CHANNEL))
        with mock.patch.object(self.ops,'native_subscription',return_value=False):
            self.assertFalse(self.adapter.runtime.verify(self.spec,CHANNEL))

    def test_explicit_finite_exclusion_overrides_subscribe_mode(self):
        Path(self.env).write_text(Path(self.env).read_text()+'BUZZ_ACP_CHANNELS='+OTHER+'\n')
        self.assertFalse(self.adapter.runtime.verify(self.spec,CHANNEL))
        self.assertEqual(self.native_calls,0)
        self.assertFalse(asyncio.run(self.adapter.readback(self.row)).verified)

    def test_process_identity_change_during_native_proof_is_rejected(self):
        original=self.ops.process
        def native(*args):
            self.ops.process=lambda u:(124,2,original(u)[2])
            return True
        with mock.patch.object(self.ops,'native_subscription',side_effect=native):
            self.assertFalse(self.adapter.runtime.verify(self.spec,CHANNEL))
