"""Signed bounded history recovery, including byte caps and inclusive seconds."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import buzz_feishu_group_sync as gs
from hostd import relay_history as history

KEY = '2'.zfill(64)
OTHER_KEY = '3'.zfill(64)
CHANNEL = '11111111-1111-4111-8111-111111111111'
OTHER_CHANNEL = '22222222-2222-4222-8222-222222222222'
NOW = datetime.fromtimestamp(1000, timezone.utc)


def event(index, at=None, *, key=KEY, kind=9, tags=None):
    return gs.sign_event(key, kind, [['h', CHANNEL]] if tags is None else tags,
                         f'fixture {index}', 900 + index if at is None else at)


class RelayHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = [event(i) for i in range(12)]
        cls.tied = [event(i, 900) for i in range(6)]

    def setUp(self):
        self.rows = copy.deepcopy(self.fixture)
        self.calls = []
        self.size_limit = None
        self.override = None
        self.metrics = {}

    def http(self, url, headers, timeout, *, body=None):
        filters = json.loads(body)
        self.assertEqual(len(filters), 1)
        self.assertGreater(timeout, 0)
        query = filters[0]
        self.calls.append(query)
        if self.size_limit is not None and query['limit'] > self.size_limit:
            raise gs.ResponseTooLarge('fixture local byte cap')
        if self.override:
            answer = self.override(query)
            if answer is not None:
                return answer
        rows = [r for r in self.rows if query['since'] <= r['created_at'] <= query['until']
                and r['kind'] in query['kinds']
                and ('authors' not in query or r['pubkey'] in query['authors'])
                and ('ids' not in query or r['id'] in query['ids'])]
        rows = sorted(rows, key=lambda r: (-r['created_at'], r['id']))[:query['limit']]
        return 200, json.dumps(rows).encode()

    def read(self, query=None, **kwargs):
        return history.signed_history(query or {'kinds': [9], '#h': [CHANNEL]},
            relay='https://relay.test', key=KEY, http=self.http, clock=lambda: NOW,
            diagnostics=self.metrics, **kwargs)

    def test_complete_multi_page_and_boundary_seconds_without_duplicates(self):
        self.rows = self.fixture + self.tied
        # Fixture0 equals tied0, as a real relay would return it only once.
        self.rows = list({r['id']: r for r in self.rows}.values())
        result = self.read(page_size=3)
        self.assertEqual({r['id'] for r in result}, {r['id'] for r in self.rows})
        self.assertEqual(len(result), len(self.rows))
        self.assertTrue(self.metrics['complete'])
        self.assertTrue(any(q['since'] == q['until'] == 900 for q in self.calls))
        self.assertTrue(all(q['until'] <= 1000 for q in self.calls))

    def test_byte_cap_only_shrinks_read_pages_and_keeps_full_history(self):
        self.size_limit = 2
        result = self.read(page_size=8)
        self.assertEqual({r['id'] for r in result}, {r['id'] for r in self.rows})
        self.assertEqual([q['limit'] for q in self.calls[:3]], [8, 4, 2])
        self.assertEqual(self.metrics['size_retries'], 2)

    def test_real_oversized_response_is_typed_and_reduced(self):
        self.rows = self.fixture[:1]
        self.override = lambda q: (200, b' ' * (gs.PEOPLE_API_MAX_BYTES + 1)) if q['limit'] > 1 and q['since'] != q['until'] else None
        result = self.read(page_size=4)
        self.assertEqual(result, self.rows)
        self.assertGreaterEqual(self.metrics['size_retries'], 2)

    def test_absence_decision_requires_one_complete_response(self):
        # A later page could race a backdated insertion into the scanned head.
        # Absence callers must stop before issuing that second query.
        self.override = lambda q: self.rows.append(event(99, 950)) if len(self.calls) == 2 else None
        with self.assertRaisesRegex(history.HistoryIncomplete, 'single response'):
            self.read(page_size=3, require_single_response=True)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.metrics['complete'])
        self.assertEqual(len(self.rows), len(self.fixture))

    def test_single_response_can_reduce_byte_cap_but_not_accept_full_page(self):
        self.rows = self.fixture[:1]
        self.size_limit = 2
        self.assertEqual(self.read(page_size=8, require_single_response=True), self.rows)
        self.assertTrue(self.metrics['single_response'])
        self.assertEqual([q['limit'] for q in self.calls], [8, 4, 2])
        self.rows = self.fixture[:2]
        with self.assertRaisesRegex(history.HistoryIncomplete, 'single response'):
            self.read(page_size=8, require_single_response=True)

    def test_dense_second_cannot_skip_to_previous_second(self):
        self.rows = self.tied
        with self.assertRaisesRegex(history.HistoryIncomplete, 'dense second'):
            self.read(page_size=2, max_page_size=4)
        self.assertFalse(self.metrics['complete'])
        self.assertTrue(all(q['until'] >= 900 for q in self.calls))

    def test_dense_second_blocked_by_bytes_does_not_cycle(self):
        self.rows = self.tied
        self.size_limit = 2
        with self.assertRaisesRegex(history.HistoryIncomplete, 'dense second'):
            self.read(page_size=2)
        self.assertLess(len(self.calls), 8)

    def test_single_event_too_large_is_not_treated_as_absence(self):
        self.size_limit = 0
        with self.assertRaisesRegex(history.HistoryIncomplete, 'single event'):
            self.read(page_size=2)
        self.assertEqual([q['limit'] for q in self.calls], [2, 1])

    def test_partial_later_failure_returns_no_result(self):
        self.override = lambda q: (503, b'{}') if q['until'] < 908 else None
        with self.assertRaisesRegex(history.HistoryIncomplete, 'HTTP'):
            self.read(page_size=3)
        self.assertFalse(self.metrics['complete'])
        self.assertGreater(len(self.calls), 1)
        self.assertEqual(self.metrics['size_retries'], 0)

    def test_http_json_and_untyped_cap_failures_are_not_retried(self):
        for result in ((403, b'{}'), (200, b'{broken'), (200, b'{}')):
            self.calls.clear()
            self.override = lambda q, result=result: result
            with self.subTest(result=result), self.assertRaises(history.HistoryIncomplete):
                self.read()
            self.assertEqual(len(self.calls), 1)
        def untyped(q):
            raise gs.GroupSyncError('people API answer is larger than the cap')
        self.calls.clear(); self.override = untyped
        with self.assertRaises(gs.GroupSyncError):
            self.read()
        self.assertEqual(len(self.calls), 1)

    def test_mutated_cached_event_never_reuses_signature_proof(self):
        self.rows = self.fixture[:1]
        self.assertEqual(self.read(), self.rows)
        changes = ({'content': 'tampered'}, {'tags': [['h', OTHER_CHANNEL]]},
                   {'pubkey': gs._signer_pubkey(OTHER_KEY)}, {'sig': '0' * 128},
                   {'created_at': 899}, {'kind': 7}, {'id': '0' * 64})
        for change in changes:
            bad = dict(self.fixture[0], **change)
            self.override = lambda q, bad=bad: (200, json.dumps([bad]).encode())
            with self.subTest(field=next(iter(change))), self.assertRaises(history.HistoryIncomplete):
                self.read()

    def test_valid_signed_wrong_scope_future_and_duplicate_rows_fail_closed(self):
        variants = ([event(1, tags=[['h', OTHER_CHANNEL]])], [event(1, 1001)],
                    [event(1, key=OTHER_KEY)], [self.fixture[0]] * 2,
                    [event(1, kind=7)], [event(1, tags=[['h', CHANNEL], ['h', CHANNEL]])])
        for rows in variants:
            self.override = lambda q, rows=rows: (200, json.dumps(rows).encode())
            with self.subTest(case=variants.index(rows)), self.assertRaises(history.HistoryIncomplete):
                self.read({'kinds': [9], '#h': [CHANNEL], 'authors': [gs._signer_pubkey(KEY)]})

    def test_unsupported_and_future_filters_fail_before_io(self):
        for change in ({'until': 1001}, {'since': 1001}, {'since': True}, {'#feishu': ['x']},
                       {'ids': ['ab']}, {'authors': []}, {'kinds': [30177]}, {'limit': 1500}):
            with self.subTest(change=change), self.assertRaises(history.HistoryIncomplete):
                self.read(dict({'kinds': [9], '#h': [CHANNEL]}, **change))
        self.assertEqual(self.calls, [])

    def test_withdrawals_without_h_are_transport_only_exact_author_candidates(self):
        withdrawal = event(1, kind=5, tags=[['e', self.fixture[0]['id']]])
        self.rows = [withdrawal]
        query = {'kinds': [5], '#h': [CHANNEL], 'authors': [withdrawal['pubkey']]}
        with self.assertRaises(history.HistoryIncomplete):
            self.read(query)
        self.assertEqual(self.read(query, allow_unscoped_reactions=True), [withdrawal])
        reaction = event(2, kind=7, tags=[['e', self.fixture[0]['id']]])
        self.rows = [reaction]
        self.assertEqual(self.read(dict(query, kinds=[7]), allow_unscoped_reactions=True), [reaction])
        with self.assertRaises(history.HistoryIncomplete):
            self.read(dict(query, kinds=[7]))
        with self.assertRaises(history.HistoryIncomplete):
            self.read({'kinds': [5], '#h': [CHANNEL]}, allow_unscoped_reactions=True)

    def test_boundary_disappears_or_budget_exhausts_without_partial_success(self):
        self.override = lambda q: (200, b'[]') if q['since'] == q['until'] else None
        with self.assertRaisesRegex(history.HistoryIncomplete, 'boundary changed'):
            self.read(page_size=2)
        self.override = None
        for options in ({'max_queries': 1}, {'max_events': 1}, {'max_bytes': 1}):
            with self.subTest(options=options), self.assertRaises(history.HistoryIncomplete):
                self.read(page_size=2, **options)
        self.assertFalse(self.metrics['complete'])

    def test_new_effect_is_visible_after_prior_empty_lookup(self):
        self.rows = []
        self.assertEqual(self.read(), [])
        self.rows = self.fixture[:1]
        self.assertEqual(self.read(), self.rows)
        self.assertEqual(len(self.calls), 2)


if __name__ == '__main__':
    unittest.main()
