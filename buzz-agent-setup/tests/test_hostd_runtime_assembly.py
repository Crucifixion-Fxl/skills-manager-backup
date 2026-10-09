"""Root owns one app feed/pool, tracks new tasks and never activates on config alone."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import test_hostd_wiring_lifecycle as fixture
import test_hostd_worker_store as worker_fixture
from hostd_real_onboarding_fixture import RealOnboardingInput


class RuntimeEnvironment(unittest.TestCase):
    def test_feed_environment_preserves_operator_runtime_paths(self):
        chosen={'HOME':'/home/offline-owner','PATH':'/opt/offline-node/bin:/usr/bin:/bin',
                'HOSTD_NODE_BINARY':'/opt/offline-node/bin/node',
                'HOSTD_LARK_CLI_ENTRY':'/opt/offline-cli/scripts/run.js'}
        with mock.patch.dict(os.environ,chosen,clear=True):
            self.assertEqual(fixture.hd.Hostd._env(),chosen)


class Assembly(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def test_transient_own_preflight_recovery_starts_one_real_feed_and_routes_invite(self):
        from dataclasses import replace
        inputs=RealOnboardingInput(self)
        self.h.onboarding_config=inputs.config;self.h._running=True
        channel='00000000-0000-0000-0000-000000000001'
        inputs.catalog_case.write(inputs.env,inputs.env.read_text().replace('BUZZ_ACP_CHANNELS=\n','BUZZ_ACP_CHANNELS='+channel+'\n'))
        self.h.reg.bindings['alpha']=replace(self.h.reg.bindings['alpha'],channel_id=channel)
        self.h.workers['alpha'].outlet_rescan=False
        inputs.world.bad_policy=True
        feeds=[]
        async def feed(app,bindings):feeds.append(app);await asyncio.Event().wait()
        with inputs.factory_patch(),mock.patch.object(self.h,'feishu_child',feed):
            await self.h.start_onboarding();service=self.h.onboarding
            # Recovered clients use the real CLI adapter and fixture-owned
            # native runner instead of live HTTP; root keeps its actual pool.
            service._http_pool=None
            self.assertNotIn('cli_agent',service.eligible_apps)
            self.assertNotIn('cli_agent',self.h.app_tasks)
            inputs.world.bad_policy=False
            try:
                original=service.effects.registrar.own_admitted;attempts=[]
                async def admitted(record):
                    await original(record);attempts.append(record.app_id)
                    if len(attempts)==1:raise OSError('post-admission notification interrupted')
                with mock.patch.object(service.effects.registrar,'own_admitted',side_effect=admitted):
                    await service.drain();await asyncio.sleep(0)
                    task=self.h.app_tasks['cli_agent']
                    self.assertTrue(service._pending_own)
                    service._own_retry_at.clear()
                    await service.drain()
                self.assertEqual(attempts,['cli_agent','cli_agent'])
                self.assertEqual(feeds,['cli_agent'])
                self.assertEqual(self.h.app_profiles['cli_agent'],(str(inputs.config_dir),str(inputs.data_dir)))
                self.assertTrue(self.h.workers['alpha'].outlet_rescan)
                self.assertTrue({'members','buzz'}<=self.h.dirty['alpha'])
                await service.drain()
                self.assertIs(self.h.app_tasks['cli_agent'],task)
                self.assertEqual(feeds,['cli_agent'])
                await self.h.on_feishu('cli_agent',{'type':'im.chat.member.bot.added_v1','app':'cli_agent',
                    'chat_id':'oc_group','event_id':'evt_recovery','operator_id':{'open_id':'ou_owner','union_id':'on_owner'}},{})
                await service.drain()
                rows=self.h.runtime_store.join_requests()
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'requested')
                self.assertTrue(rows[0]['card_message_id'])
                self.assertEqual(len(inputs.world.cards),1)
            finally:
                for task in self.h._tasks:task.cancel()
                await asyncio.gather(*self.h._tasks,return_exceptions=True)
                await self.h.close_onboarding()

    async def test_recovered_own_profile_collision_is_not_admitted(self):
        inputs=RealOnboardingInput(self)
        self.h.onboarding_config=inputs.config;self.h._running=True
        inputs.world.bad_policy=True
        with inputs.factory_patch():
            await self.h.start_onboarding();service=self.h.onboarding
            self.h.app_profiles['cli_agent']=('/different/config','/different/data')
            inputs.world.bad_policy=False
            await service.drain()
            self.assertNotIn('cli_agent',service.eligible_apps)
            self.assertNotIn(inputs.pub,service.effects.specs)
            self.assertNotIn('cli_agent',self.h.app_tasks)
            await self.h.close_onboarding()

    async def test_root_restores_durable_runtime_before_starting_initial_workers(self):
        from hostd.runtime_registration import RuntimeRegistrar
        from hostd.store import Store
        db=Store(self.h.store_path);self.addCleanup(db.close);self.h.runtime_store=db
        self.h.onboarding_config=SimpleNamespace(trusted_relays=())
        entered=asyncio.Event();observations=[];restored=False
        async def idle(*args,**kwargs):await asyncio.Event().wait()
        async def restore(registrar):
            nonlocal restored
            self.assertIs(registrar.daemon.runtime_store,db)
            self.assertTrue(registrar.daemon._running)
            restored=True
            return {'registered':0,'pending':1,'notice':'怎么解决：核对原申请。复制给 AI：检查重启恢复。'}
        async def worker(*args):
            observations.append(restored);entered.set();await asyncio.Event().wait()
        runtime=SimpleNamespace(records={},effects=SimpleNamespace(specs={}),eligible_apps=(),run=idle,close=mock.Mock(),last_notice='')
        self.h.onboarding=runtime
        with mock.patch.object(RuntimeRegistrar,'restore',restore),mock.patch.object(self.h,'worker',worker), \
                mock.patch.object(self.h,'feishu_child',idle),mock.patch.object(fixture.hd.relay_feed,'follow',idle):
            task=asyncio.create_task(self.h.main())
            try:
                await asyncio.wait_for(entered.wait(),2)
                self.assertTrue(all(observations))
                self.assertEqual(self.h.status['onboarding_restore']['pending'],1)
                self.assertIn('怎么解决',runtime.last_notice)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_optional_startup_status_failure_does_not_prevent_onboarding(self):
        entered=asyncio.Event()
        async def begin():entered.set();await asyncio.Event().wait()
        with mock.patch.object(self.h,'save_status',side_effect=OSError('disk unavailable')),mock.patch.object(self.h,'start_onboarding',begin):
            task=asyncio.create_task(self.h.main())
            try:await asyncio.wait_for(entered.wait(),2)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_factory_receives_shared_process_pool_and_scheduler(self):
        from hostd import onboarding_runtime
        fixture_input=RealOnboardingInput(self)
        worker = self.h.workers['alpha']
        self.h.onboarding_config = fixture_input.config
        with fixture_input.factory_patch():
            await self.h.start_onboarding()
            runtime=self.h.onboarding
            self.assertIs(runtime._scheduler,self.h.scheduler)
            self.assertIs(runtime._http_pool,self.h.http_pool)
            self.assertIs(worker.http_pool,self.h.http_pool)
            self.assertIsInstance(runtime,onboarding_runtime.OnboardingRuntime)
        await self.h.close_onboarding()
        self.assertTrue(runtime._closed)

    async def test_factory_installs_scoped_agent_driver_and_closes_its_store_binding(self):
        from hostd import onboarding_runtime,agent_operations
        # This offline loader isolates dependency imports; use its actual root API class.
        ActionResult=fixture.hd.ActionResult
        fixture_input=RealOnboardingInput(self,private_key='a'*64)
        self.h.onboarding_config=fixture_input.config
        pub=fixture_input.pub
        driver=mock.AsyncMock(return_value=ActionResult(True,'restarted'))
        with fixture_input.factory_patch(), \
                mock.patch.object(agent_operations,'AgentRestartDriver',return_value=driver) as factory:
            await self.h.start_onboarding()
            runtime=self.h.onboarding
            spec=runtime.effects.specs[pub]
            self.assertIs(self.h.restart_agent,driver)
            self.assertIs(factory.call_args.args[0],self.h.runtime_store)
            self.assertIs(factory.call_args.args[1](pub),spec)
            self.assertEqual(await self.h.console_action('agent',pub,'restart'),ActionResult(True,'restarted'))
            driver.assert_awaited_once_with(pub)
        await self.h.close_onboarding()
        self.assertIsNone(self.h.restart_agent)

    async def test_duplicate_app_has_one_lifecycle_task(self):
        self.h._running=True
        entered=asyncio.Event()
        async def child(*args): entered.set();await asyncio.Event().wait()
        with mock.patch.object(self.h,'feishu_child',child):
            first=self.h.spawn_app('cli_alpha')
            second=self.h.spawn_app('cli_alpha')
            self.assertIs(first,second)
            await asyncio.wait_for(entered.wait(),.5)
            self.assertEqual(len(self.h.app_tasks),1)
            first.cancel();await asyncio.gather(first,return_exceptions=True)

    async def test_new_app_task_invalidates_previous_connected_observation_before_start(self):
        self.h._running=True
        self.h.status['apps']['cli_alpha']={'feishu':'connected'}
        async def child(*args):await asyncio.Event().wait()
        with mock.patch.object(self.h,'feishu_child',child):
            task=self.h.spawn_app('cli_alpha')
            try:self.assertEqual(self.h.status['apps']['cli_alpha']['feishu'],'connecting')
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_private_own_app_profile_cannot_replace_existing_app_feed(self):
        fixture_input=RealOnboardingInput(self,private_key='a'*64,app_id='cli_alpha')
        self.h.onboarding_config=fixture_input.config
        with fixture_input.factory_patch():
            with self.assertRaises(ValueError):await self.h.start_onboarding()
        self.assertIsNotNone(fixture_input.service)
        self.assertTrue(fixture_input.service._closed)
        self.assertIsNone(self.h.onboarding)


class Dynamic(worker_fixture.base.TmpCase,unittest.IsolatedAsyncioTestCase):
    assembly=worker_fixture.WorkerAssembly.assembly

    async def test_done_degraded_restore_dispatches_real_worker_without_promoting_status(self):
        await self._assert_done_restore(initial_status='degraded')

    async def test_done_active_restore_remains_recoverable_before_first_round(self):
        await self._assert_done_restore(initial_status='active')

    async def _assert_done_restore(self, *, initial_status):
        from hostd.runtime_registration import RuntimeRegistrar
        from hostd.store import Store,BindingRecord
        env,cfg,_,path,_=self.assembly()
        app=cfg['agents'][cfg['desk_pubkey']]
        name='JOIN-aabbccdd'
        config=self.tmp/'dynamic-bindings'/name/'config.json'
        config.parent.mkdir(parents=True,mode=0o700)
        worker_fixture.base.write_owner_only(config,env.config.read_text())
        record=SimpleNamespace(pubkey=cfg['desk_pubkey'],owner_pubkey=worker_fixture.base.OWNER_PK,
            app_id=app['app_id'],lark_config_dir=Path(app['lark_config_dir']),lark_data_dir=Path(app['lark_data_dir']))
        onboarding=SimpleNamespace(records={app['app_id']:record},effects=SimpleNamespace(specs={}),
            apply=mock.AsyncMock(),cards=SimpleNamespace(send=mock.AsyncMock()))
        db=Store(path);self.addCleanup(db.close)
        db.reconcile_bindings([BindingRecord(name,cfg['channel_id'],cfg['chat_id'],app['app_id'],str(config),
            app['lark_config_dir'],app['lark_data_dir'],cfg['mirror_pubkey'],
            worker_fixture.base.FGS.chat_ref(cfg['chat_id']))],now=100)
        db.register_agent(record.pubkey,owner_pubkey=record.owner_pubkey,app_id=record.app_id,now=100)
        db.create_join(name,record.pubkey,record.owner_pubkey,record.app_id,cfg['chat_id'],kind='new_binding',now=100)
        db.ensure_effect_plan(name,cfg['channel_id'],name,cfg['mirror_env_file'],str(config),now=100)
        db.set_effect_mirror(name,cfg['mirror_pubkey'],now=100)
        db.record_agent_chat(record.pubkey,cfg['chat_id'],worker_fixture.base.FGS.chat_ref(cfg['chat_id']),
            binding_id=name,status='active',now=100)
        db.conn.execute("UPDATE join_request SET status='done' WHERE request_id=?",(name,))
        db.conn.execute('UPDATE binding SET status=? WHERE binding_id=?',(initial_status,name))
        tables=('join_request','effect_plan','agent','agent_chat','effect_step')
        before={table:[tuple(row) for row in db.conn.execute('SELECT * FROM '+table)] for table in tables}
        async def idle(*args,**kwargs):await asyncio.Event().wait()
        # Keep feeds/worker execution idle, but run the real registrar, protected
        # config admission, SQL reconciliation, Worker construction and dispatch.
        # Recreate the daemon twice against the same ledger, exiting before
        # either first round. Both startup restores must remain eligible.
        for attempt in range(2):
            host=worker_fixture.hd.Hostd(worker_fixture.registry.Registry(),self.tmp/f'status-{attempt}.json',state_db=path)
            self.addCleanup(host.http_pool.close);self.addCleanup(host.scheduler.close)
            host._running=True
            host.onboarding_config=SimpleNamespace(binding_dir=str(config.parent.parent),trusted_relays=('https://relay.test',))
            host.onboarding=onboarding;host.runtime_store=db
            host.app_profiles[app['app_id']]=(app['lark_config_dir'],app['lark_data_dir'])
            with mock.patch.object(host,'worker',idle),mock.patch.object(host,'feishu_child',idle), \
                    mock.patch.object(worker_fixture.hd.relay_feed,'follow',idle):
                try:
                    result=await RuntimeRegistrar(host).restore()
                    self.assertEqual((result['registered'],result['pending']),(1,0))
                    tasks=dict(host.binding_tasks[name])
                    again=await RuntimeRegistrar(host).restore()
                    self.assertEqual(again['registered'],1)
                    self.assertEqual(host.binding_tasks[name],tasks)
                    self.assertIn(name,host.reg.bindings)
                    self.assertIsInstance(host.workers[name],worker_fixture.hd.bw.Worker)
                    self.assertFalse(host.workers[name].cached)
                    self.assertEqual(host._binding_state(name),'degraded')
                    self.assertEqual(host.status['bindings'][name]['runs'],0)
                    self.assertEqual(host.status['bindings'][name]['relay'],'connecting')
                    self.assertEqual(host.dirty[name],{'members','buzz','feishu'})
                    self.assertFalse(host.binding_tasks[name]['worker'].done())
                    self.assertEqual({table:[tuple(row) for row in db.conn.execute('SELECT * FROM '+table)]
                                      for table in tables},before)
                    host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()
                finally:
                    for task in host._tasks:task.cancel()
                    await asyncio.gather(*host._tasks,return_exceptions=True)

    async def test_existing_binding_adopts_approved_outlet_without_replacing_sync_reader(self):
        from hostd.store import Store
        env,cfg,_,path,_=self.assembly()
        app=cfg['agents'][cfg['desk_pubkey']]
        reg=worker_fixture.registry.Registry()
        binding=worker_fixture.registry.Binding('test-binding',env.config,env.state_dir,cfg['channel_id'],cfg['chat_id'],
            app['app_id'],app['lark_config_dir'],app['lark_data_dir'],'https://relay.test')
        reg.bindings[binding.name]=binding
        with mock.patch.object(worker_fixture.hd.bw,'Worker',fixture.FakeWorker):
            host=worker_fixture.hd.Hostd(reg,self.tmp/'status.json',state_db=path)
        host._running=True
        own_env=self.tmp/'joining-agent.env'
        host.workers[binding.name].outlet_specs={}
        host.workers[binding.name].outlet_rescan=False
        worker_fixture.base.write_owner_only(own_env,'BUZZ_ACP_CHANNELS='+cfg['channel_id']+'\n')
        spec=SimpleNamespace(pubkey=cfg['desk_pubkey'],env_file=str(own_env),app_id=app['app_id'])
        record=SimpleNamespace(pubkey=cfg['desk_pubkey'],owner_pubkey=worker_fixture.base.OWNER_PK,
            app_id=app['app_id'],env_file=str(own_env),lark_config_dir=Path(app['lark_config_dir']),lark_data_dir=Path(app['lark_data_dir']))
        host.onboarding=SimpleNamespace(records={app['app_id']:record},effects=SimpleNamespace(specs={spec.pubkey:spec}),
            catalog=SimpleNamespace(records=(record,)),relay=SimpleNamespace(owner=record.owner_pubkey))
        host.onboarding_config=SimpleNamespace(trusted_relays=('https://relay.test',))
        db=Store(path);self.addCleanup(db.close);host.runtime_store=db
        db.register_agent(spec.pubkey,owner_pubkey=record.owner_pubkey,app_id=record.app_id,now=100)
        db.create_join('JOIN-aabbccdd',spec.pubkey,record.owner_pubkey,app['app_id'],cfg['chat_id'],kind='channel',binding_id=binding.name,now=100)
        db.conn.execute("UPDATE join_request SET status='approved'")
        plan=db.ensure_effect_plan('JOIN-aabbccdd',binding.channel_id,binding.name,str(own_env),str(env.config),now=100)
        row=db.join_request('JOIN-aabbccdd');original=env.config.read_bytes()
        async def idle(*args,**kwargs):await asyncio.Event().wait()
        feeds=[]
        async def follow(identity,env_file,channel,event,status,**kwargs):
            feeds.append(event)
            await asyncio.Event().wait()
        with mock.patch.object(host,'worker',idle),mock.patch.object(host,'feishu_child',idle),mock.patch.object(worker_fixture.hd.relay_feed,'follow',follow):
            try:
                await host.register_bound_outlet(row,plan)
                self.assertEqual(host.reg.bindings[binding.name],binding)
                self.assertEqual(env.config.read_bytes(),original)
                self.assertIn((binding.name,spec.pubkey),host.outlet_tasks)
                self.assertEqual(host.outlet_status[(binding.name,spec.pubkey)],'connecting')
                self.assertEqual(host.workers[binding.name].outlet_specs,{spec.pubkey:spec})
                self.assertTrue(host.workers[binding.name].outlet_rescan)
                await asyncio.sleep(0)
                host.workers[binding.name].outlet_rescan=False
                await feeds[-1]('outlet',{'type':'_connected'})
                self.assertTrue(host.workers[binding.name].outlet_rescan)
                self.assertEqual(host.dirty[binding.name],{'members','buzz','feishu'})
                await host.register_bound_outlet(row,plan)
                self.assertEqual(len(host.app_tasks),1)
                with self.assertRaises(ValueError):await host.register_bound_outlet(row,dict(plan,config_path='/unrelated/config.json'))
            finally:
                for task in host._tasks:task.cancel()
                await asyncio.gather(*host._tasks,return_exceptions=True)

    async def test_new_binding_gets_worker_and_existing_app_feed_without_fabricated_ready(self):
        env,cfg,_,db,_=self.assembly()
        app=cfg['agents'][cfg['desk_pubkey']]
        reg=worker_fixture.registry.Registry()
        with mock.patch.object(worker_fixture.hd.bw,'Worker',fixture.FakeWorker):
            host=worker_fixture.hd.Hostd(reg,self.tmp/'status.json',state_db=db)
        host._running=True
        record=SimpleNamespace(pubkey=cfg['desk_pubkey'],app_id=app['app_id'],lark_config_dir=Path(app['lark_config_dir']),lark_data_dir=Path(app['lark_data_dir']))
        host.onboarding_config=SimpleNamespace(binding_dir=str(env.config.parent.parent),trusted_relays=('https://relay.test',))
        host.onboarding=SimpleNamespace(records={app['app_id']:record},effects=SimpleNamespace(specs={}))
        host.app_profiles[app['app_id']]=(app['lark_config_dir'],app['lark_data_dir'])
        async def idle(*args,**kw):await asyncio.Event().wait()
        row={'request_id':env.config.parent.name,'agent_id':cfg['desk_pubkey'],'callback_app_id':app['app_id'],'chat_id':cfg['chat_id']}
        plan={'binding_id':env.config.parent.name,'channel_id':cfg['channel_id'],'mirror_pubkey':cfg['mirror_pubkey'],'config_path':str(env.config)}
        with mock.patch.object(host,'worker',idle),mock.patch.object(host,'feishu_child',idle),mock.patch.object(worker_fixture.hd.relay_feed,'follow',idle),mock.patch.object(worker_fixture.hd.bw,'Worker',fixture.FakeWorker):
            await host.register_runtime_binding(row,plan)
            name=plan['binding_id']
            self.assertIn(name,host.reg.bindings)
            self.assertIn(name,host.workers)
            self.assertIn(name,host.binding_tasks)
            self.assertEqual(host._binding_state(name),'pending')
            self.assertEqual(host.status['bindings'][name]['runs'],0)
            self.assertEqual(host.status['bindings'][name]['relay'],'connecting')
            await host.register_runtime_binding(row,plan)
            self.assertEqual(len(host.app_tasks),1)
            for task in host._tasks:task.cancel()
            await asyncio.gather(*host._tasks,return_exceptions=True)


if __name__=='__main__':unittest.main()
