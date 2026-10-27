"""Request budgets must preserve native ownership checks and query-local freshness."""
from argparse import Namespace
from collections import Counter
from copy import deepcopy
import unittest
from urllib.parse import parse_qs
import uuid

from test_acceptance import FakeClient, ISSUE, TARGET, acc, note
from owner_acceptance import OwnerReports, SCHEMA


class OwnerPendingRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FakeClient()
        self.fixture.data[TARGET]['assignees'] = []
        self.mr_url = 'https://gitlab.addx.ai/example/service/-/merge_requests/7'
        self.mr_path, _ = acc.note_ref(self.mr_url + '#note_1')
        self.mr = {'project_id': 833, 'iid': 7, 'web_url': self.mr_url,
                   'author': {'id': 868}, 'state': 'opened'}
        self.fixture.data[self.mr_path] = self.mr
        self.fixture.lists[TARGET + '/related_merge_requests'] = [self.mr]
        self.requests = []
        self.fail_related = False
        # Keep the real cache and pagination; replace only the remote transport.
        self.client = object.__new__(acc.Client)
        self.client.cache = {}
        self.client.request = self.request

    def request(self, endpoint, method='GET', payload=None):
        self.assertEqual((method, payload), ('GET', None))
        self.requests.append(endpoint)
        path, _, query = endpoint.partition('?')
        if path == TARGET + '/related_merge_requests' and self.fail_related:
            raise acc.Error('API_READ_FAILED')
        if query:
            params = parse_qs(query)
            self.assertEqual(params['per_page'], ['100'])
            page = int(params['page'][0])
            return deepcopy(self.fixture.lists[path][(page - 1) * 100:page * 100])
        return deepcopy(self.fixture.data[path])

    def history(self, count=100):
        record = {'schema': SCHEMA, 'issue': ISSUE, 'owner_id': 868, 'stage': 'gray',
                  'environment': None, 'version': None, 'revision': None, 'scope': ['AC-1'],
                  'result': 'passed', 'reason': 'owner confirmed', 'evidence': [], 'remaining': [],
                  'owner_confirmation': 'acceptance completed', 'decision_note': None,
                  'decision_digest': None, 'mrs': [{k: self.mr[k] for k in ('project_id', 'iid')}
                                                 | {'url': self.mr_url}],
                  'deployment_verified': False, 'authorizes_deployment': False,
                  'reported_at': '2026-10-20T00:00:00Z', 'agent': {}}
        records = [dict(deepcopy(record), event_id=str(uuid.UUID(int=i))) for i in range(1, count + 1)]
        self.set_records(records)
        return records

    def set_records(self, records):
        self.fixture.lists[TARGET + '/notes'] = [
            note(i, 868, acc.block(SCHEMA, record)) for i, record in enumerate(records, 1)]

    def pending(self, service=None):
        service = service or OwnerReports(self.client, acc)
        return service.pending(Namespace(issue=ISSUE, stage='gray', assignee='@me'))

    def related_reads(self):
        return Counter(e.partition('?')[0] for e in self.requests)[TARGET + '/related_merge_requests']

    def test_100_history_records_read_native_associations_once(self):
        self.history()
        result = self.pending()
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['result'], 'passed')
        self.assertEqual(result['items'][0]['receipt'], ISSUE + '#note_100')
        self.assertEqual(result['unverified_records'], [])
        self.assertFalse(result['authorizes_deployment'])
        self.assertEqual(self.related_reads(), 1)
        self.assertEqual(len(self.requests), 6)  # user, Issue, two note pages, associations, native MR

    def test_next_query_refreshes_removed_native_associations(self):
        self.history(4)
        service = OwnerReports(self.client, acc)
        self.assertEqual(self.pending(service)['items'][0]['result'], 'passed')
        self.fixture.lists[TARGET + '/related_merge_requests'] = []
        self.requests.clear()
        result = self.pending(service)
        self.assertEqual(result['items'][0]['result'], 'not-recorded')
        self.assertEqual(len(result['unverified_records']), 4)
        self.assertEqual(self.related_reads(), 1)

    def test_shared_list_keeps_each_records_project_url_and_author_checks(self):
        records = self.history(4)
        records[1]['mrs'][0]['project_id'] = 999  # Same IID in another project is unrelated.
        records[2]['mrs'][0]['url'] = self.mr_url.replace('/7', '/70')
        other = dict(self.mr, iid=8, web_url=self.mr_url.replace('/7', '/8'), author={'id': 999})
        other_path, _ = acc.note_ref(other['web_url'] + '#note_1')
        self.fixture.data[other_path] = other
        self.fixture.lists[TARGET + '/related_merge_requests'].append(other)
        records[3]['mrs'] = [{'project_id': 833, 'iid': 8, 'url': other['web_url']}]
        self.set_records(records)
        result = self.pending()
        self.assertEqual(result['items'][0]['receipt'], ISSUE + '#note_1')
        self.assertEqual(len(result['unverified_records']), 3)
        self.assertTrue(all(r['reason'] == 'OWNER_ROLE_UNVERIFIED' for r in result['unverified_records']))
        self.assertEqual(self.related_reads(), 1)

    def test_later_query_read_failure_cannot_reuse_previous_association_snapshot(self):
        self.history(4)
        service = OwnerReports(self.client, acc)
        self.assertEqual(self.pending(service)['items'][0]['result'], 'passed')
        self.fail_related = True
        self.requests.clear()
        with self.assertRaisesRegex(acc.Error, '^API_READ_FAILED$'):
            self.pending(service)
        self.assertEqual(self.related_reads(), 1)

    def test_issue_assignee_does_not_read_unneeded_mr_associations(self):
        self.history()
        self.fixture.data[TARGET]['assignees'] = [{'id': 868}]
        self.fail_related = True
        result = self.pending()
        self.assertEqual(result['items'][0]['result'], 'passed')
        self.assertEqual(self.related_reads(), 0)

    def test_history_without_mr_evidence_does_not_read_associations(self):
        records = self.history(4)
        for record in records:
            record['mrs'] = []
        self.set_records(records)
        self.fail_related = True
        result = self.pending()
        self.assertEqual(result['items'][0]['result'], 'not-recorded')
        self.assertEqual(len(result['unverified_records']), 4)
        self.assertEqual(self.related_reads(), 0)


if __name__ == '__main__':
    unittest.main()
