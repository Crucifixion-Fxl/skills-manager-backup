"""Production authenticated websocket loop, fake transport and no credentials."""
import asyncio
import json
from unittest import mock
import test_hostd_relay_malformed as base

class ReplayTests(base.MalformedRelay):
    async def replay(self,provider=None,connections=1,auth_ok=True):
        sockets=[base.Socket([]) for _ in range(connections)]
        if not auth_ok:
            for socket in sockets:
                original=socket.ack
                socket.frames[1]=lambda original=original:[*original()[:2],False,'refused']
        statuses=[];counter=0
        async def event(*args):pass
        async def sleep(delay):
            nonlocal counter
            counter+=1
            if counter>=connections:raise asyncio.CancelledError
        with mock.patch.object(base.relay,'_connect',side_effect=sockets),mock.patch.object(base.relay.asyncio,'sleep',side_effect=sleep),mock.patch.object(base.relay.time,'time',return_value=2000):
            with self.assertRaises(asyncio.CancelledError):
                await base.relay.follow('binding',str(self.env),'channel-a',event,lambda n,k,v:statuses.append(v),trusted_relays=('wss://relay.test',),replay_since=provider)
        return sockets,statuses
    async def test_default_replays_nine_hundred_seconds(self):
        sockets,_=await self.replay()
        self.assertEqual(next(row[2]['since'] for row in sockets[0].sent if row[0]=='REQ'),1100)
    async def test_provider_is_queried_each_reconnect_with_no_double_overlap(self):
        provider=mock.Mock(side_effect=[123,456]);sockets,_=await self.replay(provider,connections=2)
        self.assertEqual(provider.call_count,2)
        self.assertEqual([next(row[2]['since'] for row in s.sent if row[0]=='REQ') for s in sockets],[123,456])
    async def test_future_negative_noninteger_provider_never_authenticates_or_subscribes(self):
        for value in [True,-1,2001,'10',1.2,None]:
            sockets,statuses=await self.replay(lambda value=value:value)
            self.assertFalse(any(row[0] in ['AUTH','REQ'] for row in sockets[0].sent))
            self.assertNotIn('connected',statuses)
    async def test_authentication_refusal_never_reports_ready(self):
        sockets,statuses=await self.replay(lambda:123,auth_ok=False)
        self.assertNotIn('connected',statuses)
        self.assertFalse(any(row[0]=='REQ' for row in sockets[0].sent))
