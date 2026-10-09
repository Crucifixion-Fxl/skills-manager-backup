"""Untrusted relay events stay isolated on the current authenticated subscription."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HOSTD = Path(__file__).resolve().parents[1] / 'scripts' / 'hostd'
sys.path.insert(0, str(HOSTD))
sys.path.insert(0, str(HOSTD.parent))
# The fake transport exercises the production follow loop without SDK/network.
spec = importlib.util.spec_from_file_location('hostd_relay_malformed_under_test', HOSTD / 'relay_feed.py')
relay = importlib.util.module_from_spec(spec)
if importlib.util.find_spec('websockets'):
    spec.loader.exec_module(relay)
else:
    with mock.patch.dict(sys.modules, {'websockets': types.ModuleType('websockets')}):
        spec.loader.exec_module(relay)


class Socket:
    def __init__(self, events):
        self.frames = [['AUTH', 'test-challenge'], self.ack, *[self.frame(ev) for ev in events]]
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    def ack(self):
        auth = next(row[1] for row in self.sent if row[0] == 'AUTH')
        return ['OK', auth['id'], True, '']

    def frame(self, event):
        def emit():
            sub = next(row[1] for row in self.sent if row[0] == 'REQ')
            return ['EVENT', sub, event]
        return emit

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        row = self.frames.pop(0)
        return json.dumps(row() if callable(row) else row)

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class MalformedRelay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = '0' * 63 + '1'
        self.env = Path(self.tmp.name) / 'mirror.env'
        self.env.write_text(f'BUZZ_PRIVATE_KEY={self.key}\nBUZZ_RELAY_URL=wss://relay.test\n')
        self.env.chmod(0o600)
        self.valid = relay.gs.sign_event(self.key, 9, [['h', 'channel-a']], 'valid', 100)

    async def follow(self, sockets, *, validator=None, sleeps=1):
        received, statuses, delays = [], [], []
        async def event(name, value):
            received.append(value)
        async def sleep(delay):
            delays.append(delay)
            if len(delays) >= sleeps:
                raise asyncio.CancelledError
        original = relay.gs._nip01_event_verified
        with mock.patch.object(relay, '_connect', side_effect=sockets) as connect, \
                mock.patch.object(relay.asyncio, 'sleep', side_effect=sleep), \
                mock.patch.object(relay.gs, '_nip01_event_verified', side_effect=validator or original):
            with self.assertRaises(asyncio.CancelledError):
                await relay.follow('binding', str(self.env), 'channel-a', event,
                                   lambda name, kind, value: statuses.append(value), trusted_relays=('wss://relay.test',))
        return received, statuses, delays, connect.call_count

    async def test_bad_fields_never_reach_signature_verifier_and_valid_event_survives_same_subscription(self):
        changes = [
            ('id', []), ('id', 'a' * 63), ('pubkey', {}), ('pubkey', 'A' * 64),
            ('sig', None), ('sig', 'a' * 127), ('content', []), ('content', True),
            ('content', 'x' * (2 ** 20 + 1)), ('created_at', True), ('created_at', 2 ** 64),
            ('content', '\ud800'), ('content', '😀' * (2 ** 18 + 1)),
            ('tags', [['h', 'channel-a'], ['x', {}]]), ('tags', [['h', 'channel-a']] + [['x']] * 4096),
            ('tags', [['h', 'channel-a'], ['x'] * 65]),
            ('tags', [['h', 'channel-a'], ['x', 'a' * 8193]]),
            ('tags', [['h', 'channel-a']] + [['x', 'a' * 8192]] * 33),
        ]
        invalid = [dict(self.valid, **{field: value}) for field, value in changes]
        calls = []
        original = relay.gs._nip01_event_verified
        def verify(value):
            calls.append(value)
            return original(value)
        socket = Socket(invalid * 2 + [self.valid])
        received, statuses, _, connections = await self.follow([socket], validator=verify)
        self.assertEqual(received, [{'type': '_reconnected'}, self.valid])
        self.assertEqual(calls, [self.valid])
        self.assertEqual(connections, 1)
        self.assertEqual(sum(row[0] == 'REQ' for row in socket.sent), 1)
        self.assertNotIn('test-challenge', ' '.join(statuses))

    async def test_signed_deletion_without_channel_tag_wakes_receipt_reconciliation(self):
        deleted = relay.gs.sign_event(self.key, 5, [['e', self.valid['id']]], '', 101)
        rejected = [
            relay.gs.sign_event(self.key, 5, [['e', self.valid['id']], ['h', 'other']], '', 101),
            relay.gs.sign_event(self.key, 5, [['e', 'not-an-event']], '', 101),
            relay.gs.sign_event(self.key, 5, [['e', self.valid['id']], ['e', 'a' * 64]], '', 101),
            relay.gs.sign_event(self.key, 9, [['e', self.valid['id']]], '', 101),
            relay.gs.sign_event(self.key, 5, [['e', self.valid['id']], ['e']], '', 101),
            dict(deleted, sig='0' * 128),
        ]
        received, _, _, _ = await self.follow([Socket([*rejected, deleted])])
        self.assertEqual(received, [{'type': '_reconnected'}, deleted])

    async def test_signed_native_reaction_without_channel_tag_wakes_target_reconciliation(self):
        reaction = relay.gs.sign_event(self.key, 7, [['e', self.valid['id']]], '👍', 101)
        wrong = relay.gs.sign_event(self.key, 7, [['e', self.valid['id']], ['h', 'other']], '👍', 101)
        duplicate = relay.gs.sign_event(self.key, 7, [['e', self.valid['id']], ['e']], '👍', 101)
        received, _, _, _ = await self.follow([Socket([wrong, duplicate, dict(reaction,sig='0'*128), reaction])])
        self.assertEqual(received, [{'type': '_reconnected'}, reaction])

    async def test_signature_verifier_exception_rejects_only_that_event(self):
        poisoned = dict(self.valid, id='a' * 64)
        original = relay.gs._nip01_event_verified
        def verify(value):
            if value['id'] == poisoned['id']:
                raise RuntimeError('RAW_SECRET_PII_CANARY')
            return original(value)
        received, statuses, _, count = await self.follow([Socket([poisoned] * 10 + [self.valid])], validator=verify)
        self.assertEqual(received, [{'type': '_reconnected'}, self.valid])
        self.assertEqual(count, 1)
        self.assertNotIn('RAW_SECRET_PII_CANARY', ' '.join(statuses))

    async def test_quick_authenticated_disconnects_do_not_reset_backoff(self):
        sockets = [Socket([]) for _ in range(8)]
        with mock.patch.object(relay.time, 'monotonic', return_value=100):
            _, _, delays, count = await self.follow(sockets, sleeps=8)
        self.assertEqual(count, 8)
        self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 60, 60])

    async def test_stable_authenticated_session_resets_retry_backoff(self):
        clock = mock.Mock(wraps=relay.time)
        clock.monotonic.side_effect = [100, 100, 100, 161, 200, 200]
        with mock.patch.object(relay, 'time', clock):
            _, _, delays, count = await self.follow([Socket([]) for _ in range(3)], sleeps=3)
        self.assertEqual(count, 3)
        self.assertEqual(delays, [1, 1, 2])


if __name__ == '__main__':
    unittest.main()
