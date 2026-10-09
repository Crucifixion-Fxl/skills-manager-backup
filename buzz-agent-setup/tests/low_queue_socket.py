"""Synthetic WebSocket wire only; actual relay AUTH/follow methods stay real."""
import asyncio
import json

import buzz_feishu_group_sync as gs


class LowQueueSocket:
    def __init__(self, case, *, mirror_pubkey, channel, kinds, challenge):
        self.case = case
        self.mirror_pubkey = mirror_pubkey
        self.channel = channel
        self.kinds = list(kinds)
        self.challenge = challenge
        self.queue = asyncio.Queue()
        self.queue.put_nowait(json.dumps(['AUTH', challenge]))
        self.entered = False
        self.exited = False
        self.auth_event = None
        self.released_ok = False
        self.expected_since = None
        self.subscription = None
        self.request = None
        self.close_subscription = None

    async def __aenter__(self):
        self.case.assertFalse(self.entered, 'unexpected second connection on the low socket')
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    async def send(self, raw):
        frame = json.loads(raw)
        if frame[0] == 'AUTH':
            self.case.assertEqual(len(frame), 2)
            self.case.assertIsNone(self.auth_event)
            event = frame[1]
            self.case.assertTrue(gs._nip01_event_verified(event))
            self.case.assertEqual((event['kind'], event['pubkey']),
                                  (22242, self.mirror_pubkey))
            self.case.assertEqual(event['content'], '')
            self.case.assertIn(['relay', 'wss://relay.test'], event['tags'])
            self.case.assertIn(['challenge', self.challenge], event['tags'])
            self.case.assertRegex(event['id'], r'^[0-9a-f]{64}$')
            self.auth_event = event
            # Keep the actual matching OK off the wire until the next genuine
            # queue getter is pending. No alternate AUTH/runtime is invented.
        elif frame[0] == 'REQ':
            self.case.assertTrue(self.released_ok)
            self.case.assertEqual(len(frame), 3)
            self.case.assertIsNone(self.request)
            self.case.assertRegex(frame[1], r'^h[0-9a-f]{6}$')
            self.case.assertIsNotNone(self.expected_since)
            self.case.assertEqual(frame[2], {'#h': [self.channel],
                                            'kinds': self.kinds,
                                            'since': self.expected_since})
            self.subscription = frame[1]
            self.request = frame[2]
            self.queue.put_nowait(json.dumps(['EOSE', self.subscription]))
        else:
            self.case.assertEqual(frame, ['CLOSE', self.subscription])
            self.case.assertIsNotNone(self.subscription)
            self.close_subscription = frame[1]

    def release_actual_matching_ok(self, getter):
        self.case.assertIsNotNone(self.auth_event)
        self.case.assertFalse(self.released_ok)
        self.case.assertIn(getter, self.queue._getters)
        self.case.assertFalse(getter.done())
        self.queue.put_nowait(json.dumps(['OK', self.auth_event['id'], True, '']))
        self.released_ok = True
