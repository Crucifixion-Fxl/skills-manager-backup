"""Actual SQL and daemon observations; metadata alone never activates a binding."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

import test_hostd_worker_store as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    from hostd.runtime_registration import RuntimeRegistrar
except ImportError:
    RuntimeRegistrar = None
from hostd.join_effects import RegistrationProof
from hostd.store import Store, BindingRecord


class Registration(fixture.base.TmpCase, unittest.IsolatedAsyncioTestCase):
    assembly = fixture.WorkerAssembly.assembly

    def prepare(self, *, kind='new_binding'):
        self.assertIsNotNone(RuntimeRegistrar)
        env,cfg,world,path,factory = self.assembly()
        db=Store(path);self.addCleanup(db.close)
        app=cfg['agents'][cfg['desk_pubkey']]
        request='JOIN-1234abcd'
        db.reconcile_bindings([BindingRecord('test-binding',cfg['channel_id'],cfg['chat_id'],app['app_id'],str(env.config),
            app['lark_config_dir'],app['lark_data_dir'],cfg['mirror_pubkey'])],now=100)
        db.register_agent(cfg['desk_pubkey'],owner_pubkey=fixture.base.OWNER_PK,app_id=app['app_id'],now=100)
        db.create_join(request,cfg['desk_pubkey'],fixture.base.OWNER_PK,app['app_id'],cfg['chat_id'],kind=kind,
                       binding_id='test-binding' if kind == 'channel' else None,now=100)
        db.conn.execute("UPDATE join_request SET status='approved' WHERE request_id=?",(request,))
        plan=db.ensure_effect_plan(request,cfg['channel_id'],'test-binding',cfg['mirror_env_file'],str(env.config),now=100)
        db.set_effect_mirror(request,cfg['mirror_pubkey'],now=100)
        plan=db.effect_plan(request);row=db.join_request(request)
        pending=asyncio.Event()
        task=asyncio.create_task(pending.wait());self.addAsyncCleanup(self.cancel,task)
        spec=SimpleNamespace(pubkey=cfg['desk_pubkey'],owner_pubkey=fixture.base.OWNER_PK,app_id=app['app_id'])
        host=SimpleNamespace(runtime_store=db,onboarding=SimpleNamespace(records={app['app_id']:spec}),
            workers={'test-binding':SimpleNamespace(config=env.config,binding_id='test-binding',last={'hostd':{'outlet_results':{cfg['desk_pubkey']:{'status':'verified','app_id':app['app_id'],'pending':0,'checked_at':int(fixture.base.NOW.timestamp())}}}})},
            status={'apps':{app['app_id']:{'feishu':'connected'}},'bindings':{'test-binding':{'relay':'connected','runs':1}}},
            app_tasks={app['app_id']:task},binding_tasks={'test-binding':{'worker':task,'relay':task}},
            outlet_tasks={('test-binding',cfg['desk_pubkey']):task},outlet_status={('test-binding',cfg['desk_pubkey']):'connected'},
            _binding_state=lambda name:'active',register_runtime_binding=None)
        host.reg=fixture.registry.Registry()
        host.reg.bindings['test-binding']=fixture.registry.Binding('test-binding',env.config,self.tmp/'state',cfg['channel_id'],cfg['chat_id'],
            app['app_id'],app['lark_config_dir'],app['lark_data_dir'],'wss://relay.test')
        host.register_bound_outlet=mock.AsyncMock()
        registrar=RuntimeRegistrar(host,clock=lambda:fixture.base.NOW.timestamp())
        return registrar,host,row,plan

    async def cancel(self,task):
        task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_all_observed_required_components_produce_typed_proof(self):
        registrar,host,row,plan=self.prepare()
        proof=await registrar.readback(row,plan)
        self.assertIsInstance(proof,RegistrationProof)
        self.assertTrue(all((proof.worker_active,proof.reader_connected,proof.relay_connected,proof.outlet_active)))
        self.assertNotIn('BUZZ_PRIVATE_KEY',repr(proof))

    async def test_missing_connection_or_pending_outlet_cannot_activate(self):
        registrar,host,row,plan=self.prepare()
        host.outlet_status[(plan['binding_id'],row['agent_id'])]='connecting'
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.outlet_active)
        host.status['apps'][row['callback_app_id']]['feishu']='disconnected'
        self.assertFalse((await registrar.readback(row,plan)).reader_connected)
        host.workers[plan['binding_id']].last['hostd']['outlet_results'][row['agent_id']]['pending']=1
        self.assertFalse((await registrar.readback(row,plan)).outlet_active)

    async def test_agent_readiness_observation_survives_unrelated_report_but_never_renews_ttl(self):
        registrar,host,row,plan=self.prepare()
        worker=host.workers[plan['binding_id']]
        observation=dict(worker.last['hostd']['outlet_results'][row['agent_id']])
        def observe(agent,db):
            self.assertEqual(agent,row['agent_id']);self.assertIs(db,host.runtime_store)
            return dict(observation)
        worker.outlet_readiness=observe
        worker.last={'hostd':{'dirty':['notice'],'outlet_results':{}}}
        self.assertTrue((await registrar.readback(row,plan)).outlet_active)
        registrar.clock=lambda:fixture.base.NOW.timestamp()+301
        self.assertFalse((await registrar.readback(row,plan)).outlet_active)
        registrar.clock=lambda:fixture.base.NOW.timestamp()
        observation.clear()
        self.assertFalse((await registrar.readback(row,plan)).outlet_active)

    async def test_stopped_worker_and_stale_outlet_receipt_never_become_active(self):
        registrar,host,row,plan=self.prepare()
        task=host.binding_tasks[plan['binding_id']]['worker'];await self.cancel(task)
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active)
        self.assertFalse(proof.reader_connected)
        host.workers[plan['binding_id']].last['hostd']['outlet_results'][row['agent_id']]['checked_at']=100
        self.assertFalse((await registrar.readback(row,plan)).outlet_active)

    async def test_forged_request_or_plan_is_refused_before_registration(self):
        registrar,host,row,plan=self.prepare()
        for argument,other in ((dict(row,callback_app_id='cli_other'),plan),(row,dict(plan,channel_id='11111111-1111-4111-8111-111111111111'))):
            with self.assertRaises(ValueError):await registrar.readback(argument,other)

    async def test_other_app_outlet_or_other_worker_config_cannot_reuse_recent_proof(self):
        registrar,host,row,plan=self.prepare()
        result=host.workers[plan['binding_id']].last['hostd']['outlet_results'][row['agent_id']]
        result['app_id']='cli_elsewhere'
        self.assertFalse((await registrar.readback(row,plan)).outlet_active)
        host.workers[plan['binding_id']].config=Path('/another/config.json')
        self.assertFalse((await registrar.readback(row,plan)).worker_active)

    async def test_bound_registration_adopts_existing_binding_and_reads_actual_sync_app(self):
        registrar,host,row,plan=self.prepare(kind='channel')
        binding=host.reg.bindings[plan['binding_id']]
        from dataclasses import replace
        host.reg.bindings[plan['binding_id']]=replace(binding,sync_app_id='cli_sync')
        host.app_tasks['cli_sync']=host.app_tasks[row['callback_app_id']]
        host.status['apps']['cli_sync']={'feishu':'connected'}
        await registrar.register(row,plan)
        host.register_bound_outlet.assert_awaited_once_with(row,plan)
        proof=await registrar.readback(row,plan)
        self.assertTrue(proof.reader_connected)
        self.assertEqual(proof.app_id,row['callback_app_id'])
        host.status['apps']['cli_sync']['feishu']='disconnected'
        self.assertFalse((await registrar.readback(row,plan)).reader_connected)
        host.status['apps']['cli_sync']['feishu']='connected'
        host.status['apps'][row['callback_app_id']]['feishu']='disconnected'
        self.assertFalse((await registrar.readback(row,plan)).reader_connected)

    async def test_bound_adoption_refuses_unrelated_registered_binding(self):
        registrar,host,row,plan=self.prepare(kind='channel')
        from dataclasses import replace
        host.reg.bindings[plan['binding_id']]=replace(host.reg.bindings[plan['binding_id']],chat_id='oc_other')
        with self.assertRaises(ValueError):await registrar.register(row,plan)
        host.register_bound_outlet.assert_not_awaited()


    def restore_case(self, *, status='applied', kind='new_binding', grant=False):
        registrar,host,row,plan=self.prepare(kind=kind)
        db=host.runtime_store
        if kind=='new_binding':
            name=row['request_id'];config=self.tmp/'dynamic-bindings'/name/'config.json'
            config.parent.mkdir(parents=True,mode=0o700);config.write_bytes(Path(plan['config_path']).read_bytes());config.chmod(0o600)
            old=next(binding for binding in db.bindings() if binding['binding_id']==plan['binding_id'])
            db.conn.execute('DELETE FROM binding WHERE binding_id=?',(plan['binding_id'],))
            db.reconcile_bindings([BindingRecord(name,plan['channel_id'],row['chat_id'],row['callback_app_id'],str(config),old['config_dir'],old['data_dir'],plan['mirror_pubkey'],fixture.base.FGS.chat_ref(row['chat_id']))],now=100)
            db.conn.execute('UPDATE effect_plan SET binding_id=?,config_path=? WHERE request_id=?',(name,str(config),row['request_id']))
            host.reg.bindings.clear()  # Simulates legacy-only startup registry.
        else:
            db.conn.execute("UPDATE effect_plan SET mirror_pubkey='' WHERE request_id=?",(row['request_id'],))
        db.conn.execute('UPDATE join_request SET status=? WHERE request_id=?',(status,row['request_id']))
        row=db.join_request(row['request_id']);plan=db.effect_plan(row['request_id'])
        if grant:
            db.record_agent_chat(row['agent_id'],row['chat_id'],fixture.base.FGS.chat_ref(row['chat_id']),binding_id=plan['binding_id'],status='active',now=100)
        host.register_runtime_binding=mock.AsyncMock()
        host.register_bound_outlet=mock.AsyncMock()
        host.onboarding.apply=mock.AsyncMock();host.onboarding.cards=SimpleNamespace(send=mock.AsyncMock())
        return registrar,host,row,plan

    async def test_restore_applied_new_dynamic_binding_without_replaying_effects(self):
        registrar,host,row,plan=self.restore_case()
        before=[dict(value) for value in host.runtime_store.conn.execute('SELECT * FROM effect_plan')]
        result=await registrar.restore()
        self.assertEqual(result['registered'],1);self.assertEqual(result['pending'],0)
        host.register_runtime_binding.assert_awaited_once_with(row,plan)
        host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()
        self.assertEqual(host.runtime_store.join_request(row['request_id'])['status'],'applied')
        self.assertEqual([dict(value) for value in host.runtime_store.conn.execute('SELECT * FROM effect_plan')],before)

    async def test_restore_done_requires_exact_active_grant_and_normal_register_stays_closed(self):
        registrar,host,row,plan=self.restore_case(status='done',grant=True)
        with self.assertRaises(ValueError):await registrar.register(row,plan)
        with self.assertRaises(ValueError):await registrar.readback(row,plan)
        result=await registrar.restore()
        self.assertEqual(result['registered'],1);self.assertEqual(result['pending'],0)
        host.register_runtime_binding.assert_awaited_once_with(row,plan,restore_done=True)
        self.assertEqual(host.runtime_store.join_request(row['request_id'])['status'],'done')

    async def test_targeted_restore_dispatches_only_exact_existing_request(self):
        registrar,host,row,plan=self.restore_case(status='done',grant=True)
        self.assertEqual((await registrar.restore(request_id='JOIN-ffffffff'))['registered'],0)
        host.register_runtime_binding.assert_not_awaited()
        result=await registrar.restore(request_id=row['request_id'])
        self.assertEqual((result['registered'],result['pending']),(1,0))
        host.register_runtime_binding.assert_awaited_once_with(row,plan,restore_done=True)
        host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()

    async def test_restore_done_degraded_binding_preserves_grant_and_status(self):
        registrar,host,row,plan=self.restore_case(status='done',grant=True)
        db=host.runtime_store
        db.conn.execute("UPDATE binding SET status='degraded'")
        before=list(db.conn.iterdump())
        with self.assertRaises(ValueError):await registrar.register(row,plan)
        with self.assertRaises(ValueError):await registrar.readback(row,plan)
        result=await registrar.restore()
        self.assertEqual((result['registered'],result['pending']),(1,0))
        host.register_runtime_binding.assert_awaited_once_with(row,plan,restore_done=True)
        host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()
        self.assertEqual(list(db.conn.iterdump()),before)

    async def test_restore_done_nonrecoverable_binding_states_remain_closed(self):
        registrar,host,row,plan=self.restore_case(status='done',grant=True)
        db=host.runtime_store
        for status in ('pending','paused','conflict','retired'):
            with self.subTest(status=status):
                db.conn.execute('UPDATE binding SET status=?',(status,))
                result=await registrar.restore()
                self.assertEqual((result['registered'],result['pending']),(0,1))
                host.register_runtime_binding.assert_not_awaited()
                self.assertEqual(db.bindings()[0]['status'],status)
        host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()

    async def test_restore_done_missing_retired_or_wrong_grant_remains_pending(self):
        registrar,host,row,plan=self.restore_case(status='done')
        db=host.runtime_store
        cases=('missing','retired','wrong_binding','wrong_owner','wrong_app','retired_agent','retired_binding')
        for status,case in ((status,case) for status in ('active','degraded') for case in cases):
            with self.subTest(status=status,case=case):
                db.conn.execute('DELETE FROM agent_chat')
                db.conn.execute("UPDATE agent SET status='active',owner_pubkey=?,app_id=?",(row['owner_pubkey'],row['callback_app_id']))
                db.conn.execute('UPDATE binding SET status=?',(status,))
                if case!='missing':db.record_agent_chat(row['agent_id'],row['chat_id'],fixture.base.FGS.chat_ref(row['chat_id']),binding_id=plan['binding_id'],status='active',now=100)
                if case=='retired':db.conn.execute("UPDATE agent_chat SET status='retired'")
                if case=='wrong_binding':db.conn.execute('UPDATE agent_chat SET binding_id=NULL')
                if case=='wrong_owner':db.conn.execute('UPDATE agent SET owner_pubkey=?',('a'*64,))
                if case=='wrong_app':db.conn.execute("UPDATE agent SET app_id='cli_other'")
                if case=='retired_agent':db.conn.execute("UPDATE agent SET status='retired'")
                if case=='retired_binding':db.conn.execute("UPDATE binding SET status='retired'")
                result=await registrar.restore()
                self.assertEqual(result['registered'],0);self.assertEqual(result['pending'],1)
                self.assertIn('怎么解决',result['notice']);self.assertIn('复制给 AI',result['notice'])
                host.register_runtime_binding.assert_not_awaited()

    async def test_restore_done_bound_uses_existing_adoption_and_keeps_sync_reader(self):
        registrar,host,row,plan=self.restore_case(status='done',kind='channel',grant=True)
        from dataclasses import replace
        binding=host.reg.bindings[plan['binding_id']]
        host.reg.bindings[plan['binding_id']]=replace(binding,sync_app_id='cli_distinct_sync')
        result=await registrar.restore()
        self.assertEqual(result['registered'],1)
        host.register_bound_outlet.assert_awaited_once_with(row,plan,restore_done=True);host.register_runtime_binding.assert_not_awaited()
        self.assertEqual(host.reg.bindings[plan['binding_id']].sync_app_id,'cli_distinct_sync')

    async def test_restore_unknown_registration_failure_holds_then_explicit_retry_dispatches(self):
        registrar,host,row,plan=self.restore_case(status='approved')
        host.register_runtime_binding.side_effect=ValueError('PRIVATE unsafe config / path')
        result=await registrar.restore()
        self.assertEqual(result['registered'],0);self.assertEqual(result['pending'],1)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn(plan['config_path'],json.dumps(result))
        self.assertEqual(host.runtime_store.join_request(row['request_id'])['status'],'approved')
        host.register_runtime_binding.side_effect=None
        result=await registrar.restore();self.assertEqual(result['registered'],1)
        self.assertEqual(host.register_runtime_binding.await_count,2)
        host.onboarding.apply.assert_not_awaited();host.onboarding.cards.send.assert_not_awaited()

    async def test_restore_ineligible_or_missing_saved_plan_never_registers(self):
        registrar,host,row,plan=self.restore_case(status='denied')
        self.assertEqual((await registrar.restore())['registered'],0)
        host.runtime_store.conn.execute("UPDATE join_request SET status='approved'")
        host.runtime_store.conn.execute('DELETE FROM effect_plan')
        result=await registrar.restore();self.assertEqual(result['pending'],1)
        host.register_runtime_binding.assert_not_awaited()

    async def test_restore_catalog_mismatch_one_bad_request_does_not_abort_good_sibling(self):
        registrar,host,row,plan=self.restore_case(status='done')
        db=host.runtime_store
        good='JOIN-abcdef12';channel='11111111-1111-4111-8111-111111111111';chat='oc_sibling'
        config=self.tmp/'dynamic-bindings'/good/'config.json';config.parent.mkdir(parents=True,mode=0o700)
        cfg=json.loads(Path(plan['config_path']).read_bytes());cfg.update(channel_id=channel,chat_id=chat)
        config.write_text(json.dumps(cfg));config.chmod(0o600)
        profile=next(binding for binding in db.bindings() if binding['binding_id']==plan['binding_id'])
        db.reconcile_bindings([BindingRecord(good,channel,chat,row['callback_app_id'],str(config),profile['config_dir'],profile['data_dir'],plan['mirror_pubkey'])],now=101)
        db.create_join(good,row['agent_id'],row['owner_pubkey'],row['callback_app_id'],chat,kind='new_binding',now=101)
        db.conn.execute("UPDATE join_request SET status='applied' WHERE request_id=?",(good,))
        db.ensure_effect_plan(good,channel,good,plan['secret_ref'],str(config),now=101)
        db.set_effect_mirror(good,plan['mirror_pubkey'],now=101)
        result=await registrar.restore()
        self.assertEqual(result['pending'],1);self.assertEqual(result['registered'],1)
        self.assertEqual(host.register_runtime_binding.await_args.args[0]['request_id'],good)
        host.register_runtime_binding.reset_mock()
        host.onboarding.records[row['callback_app_id']].owner_pubkey='f'*64
        result=await registrar.restore();self.assertEqual(result['registered'],0);self.assertEqual(result['pending'],2)
        host.register_runtime_binding.assert_not_awaited()

    async def test_restore_unsafe_or_missing_actual_config_failure_is_pending_only(self):
        registrar,host,row,plan=self.restore_case(status='applied')
        from hostd.safety import read_owned
        async def daemon_register(current,saved):
            # Only daemon IO is a spy; protected source validation is real.
            self.assertEqual(current['request_id'],row['request_id'])
            self.assertEqual(saved,plan)
            read_owned(saved['config_path'])
        host.register_runtime_binding.side_effect=daemon_register
        config=Path(plan['config_path']);config.chmod(0o644)
        result=await registrar.restore();self.assertEqual(result['pending'],1);self.assertEqual(result['registered'],0)
        config.unlink()
        result=await registrar.restore();self.assertEqual(result['pending'],1);self.assertEqual(result['registered'],0)
        self.assertNotIn(str(config),json.dumps(result));host.onboarding.apply.assert_not_awaited()
        self.assertEqual(host.runtime_store.join_request(row['request_id'])['status'],'applied')

if __name__=='__main__':unittest.main()
