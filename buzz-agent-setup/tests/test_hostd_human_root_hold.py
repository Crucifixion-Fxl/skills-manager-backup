"""Missing native roots hold only their unattempted human replies durably."""
import copy
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_hostd_delivery_mapping as fixture
import test_hostd_worker_store as workers
from hostd.recovery import plan_recovery

base, dm, Store, StateAdapter = fixture.base, fixture.dm, fixture.Store, fixture.StateAdapter
gs = base.FGS
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule


def event(body, *, parent=None, created=None, key=base.OWNER_KEY):
    tags = [['h', base.CHANNEL]]
    if parent:
        tags += [['e', parent, '', 'root'], ['e', parent, '', 'reply']]
    return gs.sign_event(key, 9, tags, body, created if created is not None else int(base.NOW.timestamp()))


def writes(world):
    return [argv for argv, _ in world.bot_calls
            if '+messages-send' in argv or '+messages-reply' in argv]


class HumanRootHold(base.TmpCase):
    assembly = fixture.MappedAssembly.assembly

    def test_missing_root_is_durable_and_independent_human_continues(self):
        env, _, world, run = self.assembly()
        run.own_outlets_active = True
        missing = 'ab' * 32
        a, b, c = event('reply A', parent=missing), event('reply B', parent=missing), event('independent', created=run.now_ts + 1)
        bot = event('bot must use own outlet', key=base.AGENT2_KEY)
        world.events = [a, b, c, bot]
        run.buzz_to_feishu()
        run.persist()
        self.assertEqual(run.report['thread_roots_deferred'], 2)
        self.assertEqual(run.report['errors'], 0)
        self.assertEqual(run.report['to_feishu'], 1)
        self.assertEqual(len(writes(world)), 1)
        self.assertIn(c['id'], ' '.join(writes(world)[0]))
        with Store(self.tmp / 'hostd.db') as reopened:
            state = StateAdapter(reopened, 'test', env.state_dir).load()
            self.assertEqual(state.unresolved, {a['id']: a['created_at'], b['id']: b['created_at']})
            self.assertEqual(state.buzz_since, run.now_ts)
            for held in (a, b, bot):
                self.assertNotIn(held['id'], state.b2f)
                self.assertIsNone(reopened.conn.execute('SELECT 1 FROM delivery WHERE source_id=?', (held['id'],)).fetchone())
            self.assertEqual(state.attempts, {})

    def test_old_hold_reopens_then_recovers_under_actual_root_without_duplicate(self):
        env, _, world, run = self.assembly()
        root = event('original root', created=run.now_ts - 10)
        reply = event('held reply', parent=root['id'])
        world.events = [reply]
        run.buzz_to_feishu()
        run.persist()
        with Store(self.tmp / 'hostd.db') as reopened:
            run.state = StateAdapter(reopened, 'test', env.state_dir).load()
        for hours in (7, 9):
            run.now = base.NOW + timedelta(hours=hours)
            run.buzz_to_feishu()
            run.persist()
            self.assertEqual(run.state.unresolved[reply['id']], reply['created_at'])
            self.assertNotIn(reply['id'], run.state.b2f)
            self.assertEqual(writes(world), [])
            plan = plan_recovery(run.state, now=run.now_ts, attempt=12)
            self.assertEqual(plan.reasons, ('held_buzz_dependency',))
            self.assertEqual(plan.next_retry_at, run.now_ts + 60)
        world.events.append(root)
        world.messages = [fixture.bot_message(root, mid='om_real_root')]
        run._history.clear()  # new round does not cache an earlier absence
        run.buzz_to_feishu()
        run.persist()
        sent = writes(world)
        self.assertEqual(len(sent), 1)
        self.assertIn('+messages-reply', sent[0])
        self.assertEqual(sent[0][sent[0].index('--message-id') + 1], 'om_real_root')
        self.assertNotIn(reply['id'], run.state.unresolved)
        run.buzz_to_feishu()
        self.assertEqual(len(writes(world)), 1)

    def test_stale_attempts_retain_original_expiration_rules(self):
        _, _, world, run = self.assembly()
        created = run.now_ts - gs.THREAD_DISCOVERY_SECONDS - 1
        held, pending, retry, unknown = [str(i) * 64 for i in range(1, 5)]
        run.state.unresolved = {key: created for key in (held, pending, retry, unknown)}
        run.state.b2f = {pending: gs._mark(gs.PENDING, created), retry: gs._mark(gs.RETRY, created), unknown: gs.UNKNOWN}
        run._give_up_stale(run.state.b2f, run.state.unresolved)
        self.assertEqual(run.state.unresolved, {held: created})
        self.assertNotIn(held, run.state.b2f)
        self.assertEqual(run.state.b2f[pending], gs.UNKNOWN)
        self.assertEqual(run.state.b2f[retry], gs.FAILED)
        # Compare all actual-attempt semantics to the unchanged base hook.
        self.assertEqual(writes(world), [])
        run.state.f_unresolved = {'om_old': created}
        run._give_up_stale(run.state.f2b, run.state.f_unresolved)
        self.assertEqual(run.state.f2b['om_old'], gs.FAILED)

    def test_unknown_and_expired_pending_reply_never_enter_hold_or_resend(self):
        _, _, world, run = self.assembly()
        unknown = event('unknown reply', parent='ab' * 32)
        pending = event('expired pending reply', parent='ab' * 32)
        world.events = [unknown, pending]
        run.state.b2f = {unknown['id']: gs.UNKNOWN, pending['id']: gs._mark(gs.PENDING, run.now_ts - gs.FEISHU_RETRY_WINDOW_SECONDS - 1)}
        run.state.b2f_senders[pending['id']] = base.AGENT_APP
        with mock.patch.object(run, '_thread_root_parent', side_effect=AssertionError('attempt cannot become hold')):
            run.buzz_to_feishu()
        self.assertEqual(set(run.state.b2f.values()), {gs.UNKNOWN})
        self.assertEqual(run.state.unresolved, {})
        self.assertEqual(writes(world), [])

    def test_root_proof_errors_are_not_converted_to_dependency_holds(self):
        _, _, world, run = self.assembly()
        root = event('original', created=run.now_ts - 100)
        reply = event('reply', parent=root['id'])
        world.events = [root, reply]
        # Use real root lookups/history transports through the production hook.
        with mock.patch.object(run.clients.owner, 'messages', return_value=([], True)):
            with self.assertRaises(gs.GroupSyncError):
                run._thread_root_parent(reply, root['id'], None, {})
        self.assertEqual(run.state.unresolved, {})
        world.messages = [fixture.bot_message(root, mid='om_one'), fixture.bot_message(root, mid='om_two')]
        with self.assertRaises(gs.GroupSyncError):
            run._thread_root_parent(reply, root['id'], None, {})
        self.assertEqual(run.state.unresolved, {})
        self.assertEqual(writes(world), [])

    def test_untrusted_root_receipts_never_release_reply_and_revocation_stops_route(self):
        _, _, world, run = self.assembly()
        run.own_outlets_active = True
        root = event('root', created=run.now_ts - 100)
        reply = event('reply', parent=root['id'])
        world.events = [reply]
        run.buzz_to_feishu()
        invalid_roots = [dict(root, content='forged'),
                         gs.sign_event(base.OWNER_KEY, 9, [['h', 'cd' * 32]], 'wrong channel', root['created_at'])]
        for invalid in invalid_roots:
            world.events = [reply, invalid]
            run._history.clear()
            self.assertIs(run._thread_root_parent(reply, root['id'], None, {}), gs._HELD)
        world.events = [reply, root]
        receipt = fixture.bot_message(root, mid='om_root')
        for changes in ({'chat_id': 'oc_wrong'},
                        {'sender': {'sender_type': 'app', 'id_type': 'app_id', 'id': base.OWNER_APP}},
                        {'sender': {'sender_type': 'user', 'id_type': 'app_id', 'id': base.AGENT_APP}}):
            world.messages = [dict(receipt, **changes)]
            run._history.clear()
            self.assertIs(run._thread_root_parent(reply, root['id'], None, {}), gs._HELD)
        world.messages = [receipt]
        run.roles.pop(base.OWNER_PK)
        run.buzz_to_feishu()
        self.assertEqual(writes(world), [])
        self.assertNotIn(reply['id'], run.state.b2f)


class HumanHoldWorker(base.TmpCase):
    assembly = workers.WorkerAssembly.assembly

    def test_worker_checkpoint_and_restart_reaches_held_history(self):
        env, _, world, path, factory = self.assembly()
        worker = factory()
        worker.run({'buzz'})
        a = event('held', parent='ab' * 32)
        c = event('independent', created=a['created_at'] + 1)
        world.events = [a, c]
        report = worker.run({'buzz'})
        self.assertEqual(report['to_feishu'], 1)
        self.assertEqual(report['errors'], 0)
        self.assertEqual(report['hostd']['recovery_reasons'], ['held_buzz_dependency'])
        with Store(path) as store:
            state = StateAdapter(store, 'test-binding', env.state_dir).load()
            self.assertEqual(state.unresolved, {a['id']: a['created_at']})
        restarted = factory()
        restarted.clock = lambda: base.NOW + timedelta(hours=7)
        restarted.retry_attempt = 12
        world.clock = restarted.clock()
        report = restarted.run({'buzz'})
        self.assertEqual(report['thread_roots_deferred'], 1)
        self.assertEqual(report['to_feishu'], 0)
        self.assertEqual(report['hostd']['next_retry_at'], int(restarted.clock().timestamp()) + 60)
        with Store(path) as store:
            state = StateAdapter(store, 'test-binding', env.state_dir).load()
            self.assertEqual(state.unresolved, {a['id']: a['created_at']})
            self.assertNotIn(a['id'], state.b2f)
        self.assertEqual(len(writes(world)), 1)

    def test_fault_after_hold_checkpoint_retains_source_and_phase_cursor(self):
        env, _, world, path, factory = self.assembly()
        worker = factory()
        worker.run({'buzz'})
        before = int(base.NOW.timestamp())
        worker.clock = lambda: base.NOW + timedelta(seconds=10)
        world.clock = worker.clock()
        a = event('held before failure', parent='ab' * 32, created=before + 1)
        c = event('independent interrupted', created=before + 2)
        world.events = [a, c]
        with mock.patch.object(workers.bw.dm.MappedHostdRound, '_prepare_outbound', side_effect=OSError('injected before independent write')):
            report = worker.run({'buzz'})
        self.assertGreater(report['errors'], 0)
        with Store(path) as store:
            state = StateAdapter(store, 'test-binding', env.state_dir).load()
            self.assertEqual(state.buzz_since, before)
            self.assertEqual(state.unresolved, {a['id']: a['created_at']})
            self.assertFalse(state.b2f)
        self.assertEqual(writes(world), [])
        restarted = factory()
        restarted.clock = worker.clock
        report = restarted.run({'buzz'})
        self.assertEqual(report['to_feishu'], 1)
        self.assertEqual(len(writes(world)), 1)


class HoldRecoveryPlan(unittest.TestCase):
    def test_unattempted_only_backoff_without_state_mutation(self):
        state = gs.State(unresolved={'ab' * 32: 1})
        original = copy.deepcopy(state)
        for attempt, delay in [(0, 1), (1, 2), (5, 32), (6, 60), (99, 60)]:
            plan = plan_recovery(state, now=99999, attempt=attempt)
            self.assertEqual(plan.phases, frozenset({'buzz'}))
            self.assertEqual(plan.reasons, ('held_buzz_dependency',))
            self.assertEqual(plan.next_retry_at, 99999 + delay)
        self.assertEqual(state, original)
        for value in (gs.UNKNOWN, gs.FAILED, gs.SKIPPED, 'om_ack', 'pending:1'):
            state.b2f['ab' * 32] = value
            self.assertFalse(plan_recovery(state, now=99999).phases)
        self.assertFalse(plan_recovery(gs.State(), now=99999).phases)


if __name__ == '__main__':
    unittest.main()
