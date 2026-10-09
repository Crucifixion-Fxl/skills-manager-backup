"""Two bounded S7 owner SignedReader filter regressions; offline transport only."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]

import buzz_feishu_group_sync as gs
from hostd.signed_reads import ReadFailure, SignedReader
from recovery_relay import RecoveryRelay


OWNER_KEY = '1'.zfill(64)
RELAY_KEY = '2'.zfill(64)
CHANNEL = 'a0000000-0000-4000-8000-00000000000a'
ORIGIN = 'https://relay.s7.test'
NOW = 1_800_000_000


class SignedReadNoticeFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner_pubkey = gs._signer_pubkey(OWNER_KEY)
        self.relay_pubkey = gs._signer_pubkey(RELAY_KEY)
        self.source = gs.sign_event(
            OWNER_KEY, 9,
            [['h', CHANNEL], ['e', 'a' * 64, '', 'root']],
            'SIGNED_SOURCE_FIXTURE', NOW)
        self.edit = gs.sign_event(
            OWNER_KEY, 40003,
            [['e', self.source['id'], '', 'edit'], ['h', CHANNEL]],
            '{"text":"SIGNED_EDIT_FIXTURE"}', NOW + 1)
        self.events = [self.source, self.edit]
        self.filters = [
            {'kinds': [9], 'ids': [self.source['id']], '#h': [CHANNEL], 'limit': 2},
            {'kinds': [40003], 'authors': [self.owner_pubkey],
             '#e': [self.source['id']], '#h': [CHANNEL], 'limit': 257},
        ]
        self.http_calls = []
        relay = RecoveryRelay(
            ORIGIN, OWNER_KEY, self.relay_pubkey, http=self.http,
            now=lambda: datetime.fromtimestamp(NOW, timezone.utc))
        self.reader = SignedReader(relay)

    @staticmethod
    def _tagged(event, tag_name, value):
        return any(len(tag) >= 2 and tag[0] == tag_name and tag[1] == value
                   for tag in event['tags'])

    def http(self, url, headers, timeout, *, body=None):
        self.http_calls.append((url, body))
        if url != ORIGIN + '/query':
            raise AssertionError('unexpected signed-read endpoint')
        if headers.get('Content-Type') != 'application/json':
            raise AssertionError('signed query content type changed')
        auth = json.loads(base64.b64decode(
            headers['Authorization'].split(' ', 1)[1]))
        if (not gs._nip01_event_verified(auth)
                or auth['pubkey'] != self.owner_pubkey):
            raise AssertionError('owner NIP-98 request signature was not valid')
        filters = json.loads(body)
        if filters != self.filters:
            raise AssertionError('signed query filter packet changed')
        result = []
        for query in filters:
            matches = []
            for event in self.events:
                if 'kinds' in query and event['kind'] not in query['kinds']:
                    continue
                if 'authors' in query and event['pubkey'] not in query['authors']:
                    continue
                if 'ids' in query and event['id'] not in query['ids']:
                    continue
                if '#e' in query and not any(
                        self._tagged(event, 'e', value) for value in query['#e']):
                    continue
                if '#h' in query and not any(
                        self._tagged(event, 'h', value) for value in query['#h']):
                    continue
                matches.append(event)
            result.extend(matches[:query.get('limit', len(matches))])
        return 200, json.dumps(result, separators=(',', ':')).encode()

    async def test_real_signed_source_and_edit_queries_accept_bounded_e_and_h(self):
        try:
            rows = await self.reader.read('query', filters=self.filters)
        except ReadFailure:
            self.fail('actual SignedReader rejected S7 #e/#h filters before signed query')
        self.assertEqual(len(self.http_calls), 1)
        self.assertEqual(self.http_calls[0][0], ORIGIN + '/query')
        self.assertEqual(json.loads(self.http_calls[0][1]), self.filters)
        self.assertEqual([event['id'] for event in rows],
                         [self.source['id'], self.edit['id']])
        self.assertTrue(all(gs._nip01_event_verified(event) for event in rows))

    async def test_malformed_new_and_existing_filters_fail_before_transport(self):
        bad_filters = (
            [{'kinds': [9], '#e': ['A' * 64]}],
            [{'kinds': [9], '#e': ['a' * 63]}],
            [{'kinds': [9], '#e': 'a' * 64}],
            [{'kinds': [9], '#e': ['a' * 64] * 257}],
            [{'kinds': [9], '#h': ['not-a-channel-uuid']}],
            [{'kinds': [9], '#h': [CHANNEL.upper()]}],
            [{'kinds': [9], '#h': CHANNEL}],
            [{'kinds': [9], '#h': [CHANNEL] * 257}],
            [{'kinds': [9], '#feishu': ['om_private']}],
            [{'kinds': [True]}],
            [{'kinds': [9], 'limit': 0}],
            [{'kinds': [9], 'limit': 258}],
            [{'authors': ['not-a-lowercase-pubkey']}],
            [{'kinds': [9], 'unrecognized': ['value']}],
        )
        for filters in bad_filters:
            with self.subTest(filters=filters):
                with self.assertRaises(ReadFailure) as raised:
                    await self.reader.read('query', filters=filters)
                self.assertNotIn(OWNER_KEY, str(raised.exception))
                self.assertNotIn('om_private', str(raised.exception))
        self.assertEqual(self.http_calls, [])


if __name__ == '__main__':
    unittest.main()
