"""Own-key subscription is one author/channel, after the real NIP-42 ACK."""
import asyncio
import hashlib
import json
from unittest import mock
import test_hostd_relay_malformed as fixture


class OutletFeed(fixture.MalformedRelay):
    async def own(self, *, author=None, events=()):
        gs = fixture.relay.gs
        agent = gs._signer_pubkey(self.key)
        owner_key = '2'.zfill(64)
        owner = gs._signer_pubkey(owner_key)
        sig = gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{agent}:'.encode()).digest(), bytes.fromhex(owner_key), bytes(32)).hex()
        self.env.write_text(self.env.read_text() + 'BUZZ_AUTH_TAG=' + json.dumps(['auth', owner, '', sig]) + '\n')
        socket = fixture.Socket(events)
        received, statuses = [], []
        async def event(name, ev): received.append(ev)
        async def stop(delay): raise asyncio.CancelledError
        with mock.patch.object(fixture.relay, '_connect', return_value=socket), \
                mock.patch.object(fixture.relay.asyncio, 'sleep', side_effect=stop):
            try:
                await fixture.relay.follow('binding', str(self.env), 'channel-a', event,
                    lambda n,k,v: statuses.append((k,v)), trusted_relays=('wss://relay.test',),
                    replay_since=lambda: 0, author=author or agent, status_kind='outlet')
            except asyncio.CancelledError:
                pass
        return socket, received, statuses

    async def test_req_is_exact_own_author_channel_and_outlet_kinds(self):
        socket, _, statuses = await self.own()
        filt = next(row[2] for row in socket.sent if row[0] == 'REQ')
        self.assertEqual(filt['authors'], [fixture.relay.gs._signer_pubkey(self.key)])
        self.assertEqual(filt['#h'], ['channel-a'])
        self.assertEqual(filt['kinds'], [9,7,5,40003])
        self.assertIn(('outlet', 'connected'), statuses)

    async def test_key_author_mismatch_never_opens_or_authenticates(self):
        socket, received, statuses = await self.own(author='a'*64)
        self.assertEqual(socket.sent, [])
        self.assertEqual(received, [])
        self.assertNotIn(('outlet', 'connected'), statuses)

    async def test_wire_cannot_bypass_author_unique_channel_or_kind(self):
        gs = fixture.relay.gs
        foreign = gs.sign_event('3'.zfill(64), 9, [['h','channel-a']], 'foreign', 100)
        multiple = gs.sign_event(self.key, 9, [['h','channel-a'],['h','elsewhere']], 'multiple', 100)
        member = gs.sign_event(self.key, 9000, [['h','channel-a']], '', 100)
        _, received, _ = await self.own(events=[foreign,multiple,member,self.valid])
        events = [row for row in received if row.get('type') != '_reconnected']
        self.assertEqual([row['id'] for row in events], [self.valid['id']])

    async def test_own_deletion_hint_still_requires_exact_author(self):
        gs = fixture.relay.gs
        tags = [['e', self.valid['id']]]
        own = gs.sign_event(self.key, 5, tags, '', 101)
        foreign = gs.sign_event('3'.zfill(64), 5, tags, '', 101)
        socket, received, statuses = await self.own(events=[foreign, own])
        self.assertTrue(any(row[0] == 'REQ' for row in socket.sent))
        self.assertIn(('outlet', 'connected'), statuses)
        self.assertEqual(received, [{'type': '_reconnected'}, own])

    async def test_own_reaction_hint_still_requires_exact_author(self):
        gs = fixture.relay.gs
        tags = [['e', self.valid['id']]]
        own = gs.sign_event(self.key, 7, tags, '👍', 101)
        foreign = gs.sign_event('3'.zfill(64), 7, tags, '👍', 101)
        _, received, _ = await self.own(events=[foreign, own])
        self.assertEqual(received, [{'type': '_reconnected'}, own])
