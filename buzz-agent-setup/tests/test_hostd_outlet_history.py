"""Outlet pagination stays read-complete before any effect and preserves its slice."""
import json
import unittest
from unittest import mock
import test_hostd_outlet as own

base, outlet = own.base, own.outlet
setUpModule, tearDownModule = base.setUpModule, base.tearDownModule


class OutletHistory(base.TmpCase):
    assembly = own.OwnOutlet.assembly
    event = own.OwnOutlet.event
    history_http = own.OwnOutlet.history_http

    def small_pages(self):
        original = outlet.signed_history
        return mock.patch.object(outlet, 'signed_history', side_effect=lambda *a, **kw: original(*a, page_size=2, **kw))

    def test_full_history_is_verified_before_slice_advances_one_own_cursor(self):
        world, run, adapter = self.assembly()
        events = [self.event(content=f'item{i}', created=run.now_ts - i) for i in range(7)]
        world.events = events
        adapter.http = self.history_http(adapter, events)
        with self.small_pages():
            self.assertEqual(adapter.catch_up(max_events=1), len(events))
        self.assertTrue(adapter.slice_pending)
        self.assertEqual(len([a for a, _ in world.bot_calls if '--content' in a]), 1)
        self.assertEqual(run.mapping_store.cursor_position('test', 'relay', agent_id=base.AGENT2_PK), run.now_ts)
        queued=outlet.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        self.assertEqual({row['source_id'] for row in queued},{e['id'] for e in events if e['created_at']<run.now_ts})
        self.assertLessEqual(adapter._since(),run.now_ts-6)
        self.assertEqual(run.mapping_store.cursor_position('test', 'relay'), 0)

    def test_same_second_boundary_does_not_skip_any_positive_candidate(self):
        world, run, adapter = self.assembly()
        events = [self.event(content=f'tied{i}') for i in range(6)]
        world.events = events
        adapter.http = self.history_http(adapter, events)
        with self.small_pages():
            self.assertEqual(adapter.catch_up(), 6)
        for event in events:
            row = run.mapping_store.delivery_by_source('test', event['id'], 'b2f', agent_id=base.AGENT2_PK)
            self.assertEqual(row['status'], 'acked')
        self.assertEqual(len([a for a, _ in world.bot_calls if '--content' in a]), 6)

    def test_later_page_failure_has_no_effect_or_cursor_or_pending_mutation(self):
        world, run, adapter = self.assembly()
        events = [self.event(content=f'item{i}', created=run.now_ts - i) for i in range(6)]
        world.events = events
        original = self.history_http(adapter, events)
        def http(url, headers, timeout, *, body=None):
            query = json.loads(body)[0] if body and url.endswith('/query') else {}
            if query.get('kinds') == outlet.KINDS and query['until'] < run.now_ts - 1:
                return 503, b'{}'
            return original(url, headers, timeout, body=body)
        adapter.http = http
        before = '\n'.join(run.mapping_store.conn.iterdump())
        with self.small_pages(), self.assertRaises(base.FGS.GroupSyncError):
            adapter.catch_up()
        self.assertEqual('\n'.join(run.mapping_store.conn.iterdump()), before)
        self.assertFalse(any('--content' in a for a, _ in world.bot_calls))

    def test_hless_reaction_still_requires_signed_original_in_this_binding(self):
        world, run, adapter = self.assembly()
        original = self.event()
        world.events = [original]
        adapter.deliver(original)
        reaction = base.FGS.sign_event(base.AGENT2_KEY, 7, [['e', original['id']]], '👍', run.now_ts)
        world.events.append(reaction)
        adapter.http = self.history_http(adapter, [reaction])
        self.assertEqual(adapter.catch_up(), 1)
        row = run.mapping_store.delivery_by_source('test', reaction['id'], 'r2f', agent_id=base.AGENT2_PK)
        self.assertEqual(row['status'], 'acked')
        wrong = base.FGS.sign_event(base.AGENT2_KEY, 7, [['e', 'a' * 64]], '👍', run.now_ts)
        adapter.http = self.history_http(adapter, [wrong])
        before = len(world.reactions)
        with self.assertRaises(base.FGS.GroupSyncError):
            adapter.catch_up()
        self.assertEqual(len(world.reactions), before)


if __name__ == '__main__':
    unittest.main()
