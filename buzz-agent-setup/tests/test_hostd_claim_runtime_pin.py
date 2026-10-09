"""Real Worker/daemon claim signing with a protected explicit runtime relay pin."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import test_hostd_claim_sync_app as publisher
import test_hostd_wiring_lifecycle as lifecycle
import test_hostd_agent_catalog as catalog_fixture
from hostd.onboarding_runtime import RuntimeConfig,OnboardingRuntime
from hostd.runtime_registration import RuntimeRegistrar
from hostd.store import Store
from hostd_real_onboarding_fixture import RealOnboardingInput

gs=publisher.gs
hd=lifecycle.hd
setUpModule=publisher.setUpModule
tearDownModule=publisher.tearDownModule


class PinFixture:
    assembly=publisher.RoundPublisher.assembly
    seed=publisher.RoundPublisher.seed
    policy=publisher.RoundPublisher.policy
    claim_writes=publisher.RoundPublisher.claim_writes

    def prepare(self):
        env,cfg,world,run=self.assembly()
        http=run.clients.http
        def clients(config,environment):
            # Use the actual root Worker's module namespace for its strict class
            # boundary. The low transport is shared with the signed publisher.
            return hd.bw.bc.build_clients(config,environment,runner=world,http=http,
                                          trusted_relays=('https://relay.test',))
        return env,cfg,world,clients

    def worker(self,env,clients,clock,*,constructor_pin=False):
        kwargs={'claim_relay_pubkey':publisher.PIN} if constructor_pin else {}
        return hd.bw.Worker(env.config,env.state_dir,base_env=env.base_env,
            store_path=self.tmp/'sql'/'hostd.db',binding_id='binding',client_factory=clients,
            clock=clock,**kwargs)

    def proof(self,world,cfg):
        event=publisher.latest_policy(world.relay_events,publisher.base.MIRROR_PK)
        self.assertTrue(gs._nip01_event_verified(event));self.assertEqual(event['pubkey'],publisher.base.OWNER_PK)
        entry=json.loads(event['content'])['feishu']['bindings'][0]
        self.assertEqual(entry['sync_app'],{'version':1,'app_id':publisher.base.AGENT_APP})
        roles={m['pubkey']:m['role'] for m in world.members}
        answer=publisher.parsed_app(world.relay_events,roles,cfg['channel_id'],cfg['chat_id'],
            int(world.clock.timestamp()),cfg['mirror_pubkey'],publisher.base.OWNER_PK,entry['claimed_at'])
        self.assertEqual(answer.app_id,publisher.base.AGENT_APP)
        return entry

    def runtime(self,env,cfg):
        binding_dir=self.tmp/'approved';binding_dir.mkdir(mode=0o700,exist_ok=True)
        legacy=publisher.base.write_owner_only(self.tmp/'legacy.json','{}')
        catalog=publisher.base.write_owner_only(self.tmp/'catalog.json','{}')
        value={'version':1,'owner_env_file':str(env.signer_env),'relay_url':'https://relay.test',
            'relay_pubkey':publisher.PIN,'template_config':str(env.config),'binding_dir':str(binding_dir),
            'legacy_join_path':str(legacy),'catalog_path':str(catalog),'trusted_relays':['https://relay.test']}
        path=publisher.base.write_owner_only(self.tmp/'startup.json',json.dumps(value))
        return path,RuntimeConfig.load(path)

    def registry(self,env,cfg,*,empty=False):
        reg=hd.registry.Registry()
        if not empty:
            app=cfg['agents'][cfg['desk_pubkey']]
            reg.bindings['binding']=hd.registry.Binding('binding',env.config,env.state_dir,cfg['channel_id'],cfg['chat_id'],
                app['app_id'],app['lark_config_dir'],app['lark_data_dir'],'https://relay.test')
        return reg

    def transport(self,worker,env,world,clients):
        worker.client_factory=clients;worker.base_env=env.base_env;worker.clock=lambda:world.clock
        # Native bot CLI adapter with low fake runner; no real token/profile IO.
        worker.http_pool=None


class WorkerPin(PinFixture,publisher.base.TmpCase):
    def test_actual_worker_claim_phase_passes_explicit_pin_to_real_round(self):
        env,cfg,world,clients=self.prepare();worker=self.worker(env,clients,lambda:world.clock)
        worker.claim_relay_pubkey=publisher.PIN
        result=worker.run(set())
        self.assertEqual(result['hostd']['verdict'],'won');self.assertEqual(len(self.claim_writes(world)),1)
        self.proof(world,cfg)

    def test_worker_constructor_accepts_reviewed_pin_and_signs_claim(self):
        env,cfg,world,clients=self.prepare();worker=self.worker(env,clients,lambda:world.clock,constructor_pin=True)
        self.assertEqual(worker.claim_relay_pubkey,publisher.PIN)
        self.assertEqual(worker.run(set())['claim_published'],1);self.proof(world,cfg)

    def test_cached_setup_retains_pin_and_stable_claim_when_renewing(self):
        env,cfg,world,clients=self.prepare();worker=self.worker(env,clients,lambda:world.clock)
        worker.claim_relay_pubkey=publisher.PIN;worker.run(set());first=self.proof(world,cfg)
        world.clock+=publisher.timedelta(seconds=gs.CLAIM_HEARTBEAT_SECONDS+1)
        worker.claim_at=0
        result=worker.run(set());second=self.proof(world,cfg)
        self.assertEqual(result['hostd']['setup'],'cached')
        self.assertEqual(worker.claim_relay_pubkey,publisher.PIN)
        self.assertEqual(second['claimed_at'],first['claimed_at']);self.assertEqual(len(self.claim_writes(world)),2)

    def test_missing_malformed_and_wrong_pin_never_inferred_from_valid_response(self):
        env,cfg,world,clients=self.prepare()
        for pin in (None,True,'broken','f'*64):
            with self.subTest(pin=pin):
                worker=self.worker(env,clients,lambda:world.clock)
                if pin is not None:worker.claim_relay_pubkey=pin
                with self.assertRaises(gs.GroupSyncError):worker.run(set())
                self.assertEqual(self.claim_writes(world),[])

    def test_fresh_signed_revocation_cannot_borrow_cached_setup_roles(self):
        env,cfg,world,clients=self.prepare();worker=self.worker(env,clients,lambda:world.clock)
        worker.claim_relay_pubkey=publisher.PIN;worker.run(set())
        world.claim_roster_mode='revoked';worker.claim_at=0
        with self.assertRaises(gs.GroupSyncError):worker.run(set())
        self.assertEqual(len(self.claim_writes(world)),1)

    def test_wrong_actual_profile_and_scope_fail_before_announcement(self):
        env,cfg,world,clients=self.prepare()
        worker=self.worker(env,clients,lambda:world.clock);worker.claim_relay_pubkey=publisher.PIN
        profile=Path(cfg['agents'][cfg['desk_pubkey']]['lark_config_dir'])/'config.json'
        original=profile.read_bytes();publisher.base.write_owner_only(profile,json.dumps({'apps':[{'appId':'cli_wrong'}]}))
        with self.assertRaises(gs.GroupSyncError):worker.run(set())
        publisher.base.write_owner_only(profile,original.decode());world.bot_scope_failure=True
        with self.assertRaises(gs.GroupSyncError):worker.run(set())
        self.assertEqual(self.claim_writes(world),[])


class DaemonPin(PinFixture,publisher.base.TmpCase,unittest.IsolatedAsyncioTestCase):
    async def cancel(self,host):
        tasks=list(host._tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await host.close_onboarding()

    async def test_initial_daemon_worker_uses_only_protected_runtime_config_pin(self):
        env,cfg,world,clients=self.prepare();_,runtime=self.runtime(env,cfg)
        host=hd.Hostd(self.registry(env,cfg),self.tmp/'status.json',state_db=self.tmp/'daemon.db',onboarding_config=runtime)
        worker=host.workers['binding'];self.transport(worker,env,world,clients)
        result=await asyncio.to_thread(worker.run,set())
        self.assertEqual(result['claim_published'],1);self.proof(world,cfg)
        self.assertEqual(worker.claim_relay_pubkey,runtime.relay_pubkey)

    @unittest.skipUnless(
        catalog_fixture.AESGCM,
        "cryptography AESGCM required; run this contract in the actual-dependency environment",
    )
    async def test_dynamic_approved_sql_registration_supplies_same_reviewed_pin(self):
        env,cfg,world,clients=self.prepare()
        app=cfg['agents'][cfg['desk_pubkey']]
        actual=RealOnboardingInput(self,private_key=publisher.AGENT_KEY,
            owner_key=publisher.base.OWNER_KEY,app_id=app['app_id'],
            profile_config_dir=app['lark_config_dir'],profile_data_dir=app['lark_data_dir'],
            relay_key=publisher.PIN_KEY)
        self.assertEqual(actual.config.relay_pubkey,publisher.PIN)
        request='JOIN-aabbccdd';directory=Path(actual.config.binding_dir)/request;directory.mkdir(mode=0o700)
        mirror=publisher.base.write_owner_only(directory/'mirror.env',env.mirror_env.read_text())
        cfg=copy.deepcopy(cfg);cfg['mirror_env_file']=str(mirror)
        config=publisher.base.write_owner_only(directory/'config.json',json.dumps(cfg))
        host=hd.Hostd(self.registry(env,cfg,empty=True),self.tmp/'status.json',state_db=self.tmp/'dynamic.db',onboarding_config=actual.config)
        host._running=True
        # Real protected catalog/profile and signed source reads produce the
        # registration record; no fake service.record is injected.
        with actual.factory_patch():
            await host.start_onboarding()
        db=host.runtime_store
        db.register_agent(publisher.AGENT,owner_pubkey=publisher.base.OWNER_PK,app_id=app['app_id'],now=100)
        db.create_join(request,publisher.AGENT,publisher.base.OWNER_PK,app['app_id'],cfg['chat_id'],kind='new_binding',now=100)
        db.conn.execute("UPDATE join_request SET status='approved'")
        db.ensure_effect_plan(request,cfg['channel_id'],request,str(mirror),str(config),now=100)
        db.set_effect_mirror(request,cfg['mirror_pubkey'],now=100)
        row,plan=db.join_request(request),db.effect_plan(request)
        async def idle(*args,**kwargs):await asyncio.Event().wait()
        with mock.patch.object(host,'feishu_child',idle),mock.patch.object(hd.relay_feed,'follow',idle):
            try:
                await RuntimeRegistrar(host).register(row,plan)
                worker=host.workers[request];self.transport(worker,env,world,clients)
                host.dirty[request]=set();host.wake[request].clear()
                result=await asyncio.to_thread(worker.run,set())
                self.assertEqual(result['claim_published'],1);self.proof(world,cfg)
                self.assertEqual(worker.claim_relay_pubkey,actual.config.relay_pubkey)
            finally:await self.cancel(host)

    async def test_binding_field_and_untyped_context_cannot_supply_a_pin(self):
        env,cfg,world,clients=self.prepare();cfg['relay_pubkey']=publisher.PIN
        publisher.base.write_owner_only(env.config,json.dumps(cfg))
        for runtime in (None,SimpleNamespace(relay_pubkey=publisher.PIN)):
            with self.subTest(context=type(runtime).__name__):
                host=hd.Hostd(self.registry(env,cfg),self.tmp/'status.json',state_db=self.tmp/'untyped.db',onboarding_config=runtime)
                worker=host.workers['binding'];self.transport(worker,env,world,clients)
                with self.assertRaises(gs.GroupSyncError):await asyncio.to_thread(worker.run,set())
                self.assertIsNone(getattr(worker,'claim_relay_pubkey',None));self.assertEqual(self.claim_writes(world),[])

    async def test_protected_startup_file_rejects_malformed_pin_and_permissions(self):
        env,cfg,_,_=self.prepare();path,_=self.runtime(env,cfg);original=path.read_bytes()
        for pin in (None,True,'invalid'):
            with self.subTest(pin=pin):
                value=json.loads(original);value['relay_pubkey']=pin
                publisher.base.write_owner_only(path,json.dumps(value))
                with self.assertRaises(ValueError):RuntimeConfig.load(path)
        publisher.base.write_owner_only(path,original.decode());path.chmod(0o644)
        with self.assertRaises(ValueError):RuntimeConfig.load(path)

if __name__=='__main__':unittest.main()
