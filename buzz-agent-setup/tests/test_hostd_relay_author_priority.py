"""The normal authenticated relay retains own-author urgency without an own feed."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest import mock

import test_hostd_wiring_lifecycle as fixture
import test_hostd_relay_malformed as transport
import test_hostd_outlet_fresh_priority as own_feed


class RelayAuthorPriority(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture.Wiring.setUp(self)
        self.h._running = True
        self.key = '1'.zfill(64)
        self.author = transport.relay.gs._signer_pubkey(self.key)
        self.h.workers['alpha'].outlet_specs = {self.author: SimpleNamespace(pubkey=self.author)}
        self.h.retain_reaction_async = mock.AsyncMock()

    async def asyncTearDown(self):
        await self.h._shutdown()

    def event(self, kind=9, created=1000, *, key=None, channel='ch_alpha'):
        return transport.relay.gs.sign_event(key or self.key, kind, [['h', channel]], 'local fixture', created)

    async def emit(self, event):
        with mock.patch.object(fixture.hd.time, 'time', return_value=1000):
            await self.h.on_relay('alpha', event)

    def hints(self):
        return list(getattr(self.h, 'outlet_author_hints', {}).get('alpha', {}))

    async def test_normal_relay_retains_each_fresh_own_kind_without_own_feed(self):
        with mock.patch.object(self.h._round_slots, 'promote') as promote:
            for kind in (9, 7, 5, 40003):
                for created in (970, 1000):
                    with self.subTest(kind=kind, created=created):
                        self.h.outlet_author_hints = {}
                        await self.emit(self.event(kind, created))
                        self.assertEqual(self.hints(), [self.author])
            self.assertEqual(promote.call_count, 8)
        self.assertEqual(self.h.workers['alpha'].calls, [])
        self.assertTrue(self.h.workers['alpha'].outlet_rescan)

    async def test_hint_reaches_actual_round_snapshot_without_own_feed(self):
        seen = []
        self.h.workers['alpha'].run = lambda *a, **kw: seen.append(kw.get('outlet_authors')) or {'hostd': {'verdict': 'off'}}
        await self.emit(self.event())
        await self.h._round('alpha', {'buzz'}, set())
        self.assertEqual(seen, [(self.author,)])
        self.assertEqual(self.hints(), [])

    async def test_stale_future_and_noninteger_timestamps_do_not_retain_hint(self):
        event = self.event()
        with mock.patch.object(self.h._round_slots, 'promote') as promote:
            for created in (969, 1001, True, 1000.0, '1000', None):
                await self.emit(dict(event, created_at=created))
            promote.assert_not_called()
        self.assertEqual(self.hints(), [])

    async def test_foreign_unknown_and_mirror_authors_do_not_retain_hint(self):
        foreign = self.event(key='3'.zfill(64))
        unknown = self.event(key='4'.zfill(64))
        self.h.workers['beta'].outlet_specs = {foreign['pubkey']: SimpleNamespace()}
        mirror = self.h.mirrors['alpha']
        self.h.workers['alpha'].outlet_specs[mirror] = SimpleNamespace()
        for event in (foreign, unknown, dict(self.event(), pubkey=mirror)):
            await self.emit(event)
        self.assertEqual(self.hints(), [])

    async def test_membership_and_reconnect_events_do_not_retain_author(self):
        for kind in (9000, 9001):
            await self.emit(self.event(kind))
        await self.emit({'type': '_reconnected'})
        self.assertEqual(self.hints(), [])

    async def test_reaction_capture_failure_does_not_retain_or_promote(self):
        self.h.retain_reaction_async.side_effect = ValueError('capture failed')
        with mock.patch.object(self.h._round_slots, 'promote') as promote:
            with self.assertRaises(ValueError):
                await self.emit(self.event(7))
            promote.assert_not_called()
        self.assertEqual(self.hints(), [])

    async def test_wire_validation_rejects_wrong_channel_kind_and_malformed_before_callback(self):
        valid = self.event()
        invalid = [self.event(channel='elsewhere'), self.event(1), dict(valid, sig='0' * 128),
                   dict(valid, pubkey=[]), dict(valid, tags=None), dict(valid, created_at=True), {}]
        env = self.tmp / 'mirror.env'
        env.write_text(f'BUZZ_PRIVATE_KEY={self.key}\nBUZZ_RELAY_URL=wss://relay.test\n')
        env.chmod(0o600)
        socket = transport.Socket(invalid + [valid])
        received = []
        async def callback(name, event):
            received.append(event)
            await self.h.on_relay(name, event)
        async def stop(delay):
            raise asyncio.CancelledError
        with mock.patch.object(transport.relay, '_connect', return_value=socket), \
                mock.patch.object(transport.relay.asyncio, 'sleep', side_effect=stop), \
                mock.patch.object(fixture.hd.time, 'time', return_value=1000):
            with self.assertRaises(asyncio.CancelledError):
                await transport.relay.follow('alpha', str(env), 'ch_alpha', callback,
                    lambda *args: None, trusted_relays=('wss://relay.test',), replay_since=lambda: 0)
        self.assertEqual(received, [{'type': '_reconnected'}, valid])
        self.assertEqual(self.hints(), [self.author])
        self.assertEqual(self.h.workers['alpha'].calls, [])


class DualFeedPriority(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = own_feed.OwnedFeedPriority.asyncSetUp
    asyncTearDown = own_feed.OwnedFeedPriority.asyncTearDown
    emit = own_feed.OwnedFeedPriority.emit

    async def test_two_feed_hints_deduplicate_without_moving_fifo(self):
        author = fixture.old.base.AGENT_PK
        second = 'f' * 64
        self.h.workers['alpha'].outlet_specs[second] = SimpleNamespace()
        with mock.patch.object(fixture.hd.time, 'time', return_value=1000):
            await self.h.on_relay('alpha', {'kind': 9, 'created_at': 1000, 'pubkey': author})
            self.h.retain_outlet_author('alpha', second)
            await self.emit()
        self.assertEqual(list(self.h.outlet_author_hints['alpha']), [author, second])


if __name__ == '__main__':
    unittest.main()
