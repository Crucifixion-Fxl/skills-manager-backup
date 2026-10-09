"""Actual bot/SQL assembly keeps read gaps durable and schedules explicit work."""
import copy
import json
from datetime import timedelta
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_hostd_worker_store as fixture
import test_hostd_bot_clients as bots

base = fixture.base
Store = fixture.Store
StateAdapter = fixture.StateAdapter


class WorkerRecovery(base.TmpCase):
    assembly = fixture.WorkerAssembly.assembly

    def partial_world(self, worker, world, predicate):
        def runner(argv, **kwargs):
            result = world(argv, **kwargs)
            if predicate(argv):
                import json
                payload = json.loads(result.stdout)
                payload['data']['has_more'] = True
                result.stdout = json.dumps(payload)
            return result
        original = worker.client_factory
        def clients(cfg, env):
            built = original(cfg, env)
            for client in built.agents.values():
                client.runner = runner
            return built
        worker.client_factory = clients

    def test_new_hostd_reads_nine_hundred_second_overlap(self):
        _, _, world, _, factory = self.assembly()
        world.messages = [base.fmsg('om_delayed', base.ALICE_OPEN, 'delayed', when=base.NOW - timedelta(seconds=600))]
        report = factory().run({'feishu'}, threads=set())
        self.assertEqual(report['to_buzz'], 1)
        starts = [int(json.loads(argv[argv.index('--params') + 1])['start_time']) for argv, _ in world.bot_calls
                  if argv[2:5] == ['api', 'GET', '/open-apis/im/v1/messages']]
        self.assertIn(int(base.NOW.timestamp()) - 900, starts)

    def test_incomplete_discovery_keeps_sql_cursor_and_delivery_ack(self):
        env, _, world, db, factory = self.assembly()
        worker = factory()
        worker.run({'buzz'})
        with Store(db) as store:
            before = StateAdapter(store, 'test-binding', env.state_dir).load().feishu_since
        world.messages = [base.fmsg('om_one', base.ALICE_OPEN, 'one')]
        self.partial_world(worker, world, lambda argv: '+chat-messages-list' in argv and 'desc' in argv)
        report = worker.run({'feishu'}, threads=set())
        self.assertEqual(report['to_buzz'], 1)
        self.assertEqual(report['hostd']['retry_phases'], ['feishu'])
        self.assertEqual(report['hostd']['next_retry_at'], int(base.NOW.timestamp()) + 1)
        with Store(db) as store:
            state = StateAdapter(store, 'test-binding', env.state_dir).load()
            self.assertEqual(state.feishu_since, before)
            self.assertTrue(base.FGS.HEX64_RE.fullmatch(state.f2b['om_one']))
        # A restart re-reads the retained window and keeps the acknowledged send.
        self.assertEqual(factory().run({'feishu'}, threads=set())['to_buzz'], 0)

    def test_partial_empty_thread_does_not_advance_polled_cursor(self):
        env, _, world, db, factory = self.assembly()
        worker = factory()
        worker.run({'buzz'})
        old = int(base.NOW.timestamp()) - 500
        with Store(db) as store:
            adapter = StateAdapter(store, 'test-binding', env.state_dir)
            state = adapter.load()
            state.threads['om_old'] = old
            state.polled['om_old'] = old
            adapter.save(state, now=int(base.NOW.timestamp()))
        self.partial_world(worker, world, lambda argv: '+threads-messages-list' in argv)
        report = worker.run({'feishu'}, threads={'om_old'})
        self.assertIn('feishu', report['hostd']['retry_phases'])
        with Store(db) as store:
            self.assertEqual(StateAdapter(store, 'test-binding', env.state_dir).load().polled['om_old'], old)

    def test_interrupted_read_is_reported_and_other_phase_can_finish(self):
        _, _, world, _, factory = self.assembly()
        worker = factory()
        original = worker.client_factory
        def clients(cfg, env):
            built = original(cfg, env)
            def failed(*args, **kwargs):
                raise bots.bc.BotCliError('chat messages', -1, 'network', definite=False)
            built.owner.messages = failed
            return built
        worker.client_factory = clients
        report = worker.run({'feishu', 'buzz'})
        self.assertGreater(report['errors'], 0)
        self.assertEqual(report['hostd']['retry_phases'], ['feishu'])

    def test_pending_target_arms_retry_without_inventing_message(self):
        env, _, world, db, factory = self.assembly()
        worker = factory()
        worker.run({'buzz'})
        with Store(db) as store:
            store.enqueue_target('test-binding', base.AGENT_APP, 'om_missing', '', 'im.message.receive_v1', now=int(base.NOW.timestamp()))
        report = worker.run({'feishu'})
        self.assertEqual(report['hostd']['pending_targets'], 1)
        self.assertEqual(report['hostd']['retry_phases'], ['feishu'])
        self.assertEqual(report['to_buzz'], 0)

    def test_unknown_delivery_requests_readback_without_scheduling_resend(self):
        env, _, _, db, factory = self.assembly()
        worker = factory()
        worker.run({'feishu'})
        with Store(db) as store:
            adapter = StateAdapter(store, 'test-binding', env.state_dir)
            state = adapter.load()
            state.b2f['ab' * 32] = base.FGS.UNKNOWN
            adapter.save(state, now=int(base.NOW.timestamp()))
        report = worker.run(set())
        self.assertEqual(report['hostd']['retry_phases'], [])
        self.assertIsNone(report['hostd']['next_retry_at'])
        self.assertIn('readback_required', report['hostd']['recovery_reasons'])


class ClientReadEvidence(base.TmpCase):
    client = bots.ClientContracts.client

    def test_partial_read_is_tagged_with_actual_active_phase(self):
        client, _ = self.client([{'messages': [], 'has_more': True}])
        client.active_phase = 'feishu'
        client.thread_messages('om_root')
        self.assertEqual(client.partial_reads, {'feishu'})

    def test_missing_reaction_result_marks_read_incomplete(self):
        client, _ = self.client([{'success_msg_reaction_details': [], 'fail_msg_reaction_details': []}])
        client.active_phase = 'feishu'
        self.assertEqual(client.reaction_details(['om_one'], 'union_id'), {})
        self.assertEqual(client.partial_reads, {'feishu'})


if __name__ == '__main__':
    unittest.main()
