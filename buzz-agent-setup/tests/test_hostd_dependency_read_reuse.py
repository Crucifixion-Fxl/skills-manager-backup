"""Exact native reads are reusable only within one dependency resolution."""
import copy
from unittest import mock
import test_hostd_delivery_mapping as mapping

base, dm = mapping.base, mapping.dm
setUpModule, tearDownModule = mapping.setUpModule, mapping.tearDownModule


class DependencyReads(base.TmpCase):
    assembly = mapping.MappedAssembly.assembly

    def card(self):
        _, _, world, run = self.assembly()
        event = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'root', run.now_ts)
        world.events = [event]
        world.messages = [mapping.bot_message(event, mid='om_dependency')]
        return world, run, event

    def test_resolve_buzz_reads_one_exact_open_projection(self):
        world, run, event = self.card()
        with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            found = run.resolve_buzz(event)
        self.assertEqual(found.event_id, event['id'])
        self.assertEqual(read.call_args_list, [mock.call('om_dependency', 'open_id')])
        exact_gets = [argv for argv, _ in world.bot_calls
                      if argv[2:5] == ['api', 'GET', '/open-apis/im/v1/messages/om_dependency']]
        self.assertEqual(len(exact_gets), 1)
        self.assertFalse(world.relay_writes)

    def test_new_attempt_rereads_changed_native_sender_and_chat(self):
        world, run, event = self.card()
        original = copy.deepcopy(world.messages[0])
        self.assertIsNotNone(run.resolve_buzz(event))
        other = base.FGS.sign_event(base.OWNER_KEY, 9, [['h', base.CHANNEL]], 'different source', run.now_ts)
        changed_footer = dm.message_card(other, 'Speaker', 'edited', base.API_ORIGIN, base.CHANNEL)
        for change in ({'sender': {'sender_type': 'app', 'id_type': 'app_id', 'id': 'cli_foreign'}},
                       {'chat_id': 'oc_foreign'}, {'deleted': True},
                       {'content': changed_footer, 'body': {'content': changed_footer}}):
            with self.subTest(change=tuple(change)):
                world.messages[0] = dict(copy.deepcopy(original), **change)
                with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
                    self.assertIsNone(run.resolve_buzz(event))
                self.assertGreater(read.call_count, 0)
        self.assertFalse(world.relay_writes)

    def test_replacement_reader_cannot_borrow_prior_direct_read(self):
        world, run, event = self.card()
        self.assertIsNotNone(run.resolve_buzz(event))
        original = run.clients.owner
        replacement = copy.copy(original)
        replacement.app_id = 'cli_other_reader'
        replacement.config_dir = self.tmp / 'other-config'
        replacement.data_dir = self.tmp / 'other-data'
        run.clients.owner = replacement
        with mock.patch.object(original, 'message_view', side_effect=AssertionError('old reader borrowed')), \
                mock.patch.object(replacement, 'message_view', return_value=None) as read:
            self.assertIsNone(run.resolve_buzz(event))
            read.assert_called_once_with('om_dependency', 'open_id')
        self.assertFalse(world.relay_writes)

    def test_incomplete_exact_read_cannot_be_reused_as_a_positive(self):
        world, run, event = self.card()
        original_runner = run.clients.owner.runner
        def incomplete(argv, **kwargs):
            if argv[2:5] == ['api', 'GET', '/open-apis/im/v1/messages/om_dependency']:
                return mapping.bots.ok({'items': [{}]})
            return original_runner(argv, **kwargs)
        with mock.patch.object(run.clients.owner, 'runner', side_effect=incomplete):
            with self.assertRaises(base.FGS.GroupSyncError):
                run.resolve_buzz(event)
        self.assertNotIn(event['id'], run.state.b2f)
        with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            self.assertEqual(run.resolve_buzz(event).message_id, 'om_dependency')
        read.assert_called_once_with('om_dependency', 'open_id')
        self.assertFalse(world.relay_writes)

    def test_missing_or_failed_get_is_not_cached_for_unknown_recovery(self):
        world, run, event = self.card()
        run.state.b2f[event['id']] = base.FGS.UNKNOWN
        with mock.patch.object(run.clients.owner, 'message_view', return_value=None):
            self.assertIsNone(run.resolve_buzz(event))
        self.assertEqual(run.state.b2f[event['id']], base.FGS.UNKNOWN)
        with mock.patch.object(run.clients.owner, 'message_view', side_effect=OSError('synthetic read failure')):
            with self.assertRaises(OSError):
                run.resolve_buzz(event)
        with mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            found = run.resolve_buzz(event)
        self.assertEqual(found.message_id, 'om_dependency')
        self.assertEqual(read.call_args_list, [mock.call('om_dependency', 'open_id')])
        self.assertFalse(world.relay_writes)

    def test_final_union_projection_rechecks_author_after_relay_read(self):
        _, _, world, run = self.assembly()
        mid = 'om_human_dependency'
        world.messages = [base.fmsg(mid, base.ALICE_OPEN, 'root')]
        event = mapping.signed(mid=mid, root=mid, tags=[[base.FGS.FEISHU_AUTHOR_TAG, base.ALICE_PK]])
        world.events = [event]
        original = run.clients.http
        def changed_during_relay(url, headers, timeout, *, body=None):
            answer = original(url, headers, timeout, body=body)
            if url.endswith('/query') and body and any(f.get('kinds') == [9] for f in mapping.json.loads(body)):
                world.message_get_tamper = lambda _mid, projection, item: dict(item, sender={**item['sender'], 'id': base.union_of(base.BOB_OPEN)}) if projection == 'union_id' else item
            return answer
        with mock.patch.object(run.clients, 'http', side_effect=changed_during_relay), \
                mock.patch.object(run.clients.owner, 'message_view', wraps=run.clients.owner.message_view) as read:
            self.assertIsNone(run.resolve_feishu(mid))
        self.assertEqual(read.call_args_list, [mock.call(mid, 'open_id'), mock.call(mid, 'union_id'), mock.call(mid, 'union_id')])
        self.assertNotIn(mid, run.state.f2b)
        self.assertFalse(world.relay_writes)
