"""Complete policy reads remain bounded and cannot broaden ordinary queries."""
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import buzz_feishu_group_sync as gs
from recovery_relay import RecoveryRelay
from hostd import signed_reads as reads


class PolicySnapshot(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = '2'.zfill(64)
        cls.rows = [gs.sign_event(cls.key, 30177, [['d', f'{n:064x}']], '{}', 1)
                    for n in range(1000)]

    def setUp(self):
        self.response = self.rows[:475]
        self.calls = []
        def http(url, headers, timeout, *, body=None):
            self.calls.append(json.loads(body))
            return 200, json.dumps(self.response).encode()
        self.reader = reads.SignedReader(RecoveryRelay('https://relay.test', self.key,
            'a' * 64, http=http, now=lambda: datetime.fromtimestamp(1000, timezone.utc)))

    async def test_complete_single_response_above_old_cap(self):
        self.assertEqual(await self.reader.read('policy_snapshot'), self.response)
        self.assertEqual(self.calls, [[{'kinds': [30177], 'limit': 1000}]])

    async def test_raw_cap_is_checked_before_deduplication(self):
        self.response = self.rows[:999]
        self.assertEqual(len(await self.reader.read('policy_snapshot')), 999)
        for count in (1000, 1001):
            self.response = [self.rows[0]] * count
            with self.subTest(count=count), self.assertRaises(reads.ReadFailure):
                await self.reader.read('policy_snapshot')

    async def test_any_invalid_row_rejects_whole_response(self):
        bad = copy.deepcopy(self.rows[0]); bad['sig'] = '0' * 128
        wrong = gs.sign_event(self.key, 0, [], '{}', 1)
        future = gs.sign_event(self.key, 30177, [['d', 'a' * 64]], '{}', 100000)
        for rows in ([self.rows[0], bad], [wrong], [future], [self.rows[0]] * 2):
            self.response = rows
            with self.subTest(rows=len(rows)), self.assertRaises(reads.ReadFailure):
                await self.reader.read('policy_snapshot')

    async def test_operation_has_no_caller_filters_or_capacity_override(self):
        for kwargs in ({'filters': []}, {'channel': 'x'}, {'limit': 10000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(reads.ReadFailure):
                await self.reader.read('policy_snapshot', **kwargs)
        self.assertEqual(self.calls, [])

    async def test_ordinary_query_retains_256_cap(self):
        with self.assertRaises(reads.ReadFailure):
            await self.reader.read('query', filters=[{'kinds': [30177], 'limit': 257}])

    async def test_real_child_roundtrip_and_deadline_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'events.json').write_text(json.dumps(self.response))
            script = root / 'child.py'
            script.write_text('import sys,pathlib\nsys.path.insert(0,' + repr(str(Path(reads.__file__).parents[1])) +
                ')\nfrom hostd.signed_reads import child_main\n' +
                'def http(*a,**kw):return 200,pathlib.Path(' + repr(str(root / 'events.json')) + ').read_bytes()\nchild_main(http=http)\n')
            payload = dict(version=1, origin='https://relay.test', key=self.key,
                           pin='a' * 64, now=1000, operation='policy_snapshot')
            self.assertEqual(await reads.child_read(payload, command=[sys.executable, str(script)]), self.response)


if __name__ == '__main__': unittest.main()
