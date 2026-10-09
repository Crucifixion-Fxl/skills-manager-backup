"""Exercise the actual refresh_outlets callback and real round admission."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest import mock
import test_hostd_wiring_lifecycle as fixture
from hostd.round_slots import RoundSlots
from hostd.store import Store

class OwnedFeedPriority(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture.Wiring.setUp(self)
        self.h._running=True
        self.h.workers['alpha'].outlet_specs={}
        self.tasks=[]
        pub=fixture.old.base.AGENT_PK
        spec=SimpleNamespace(pubkey=pub,env_file=self.tmp/'own.env')
        self.h.onboarding=SimpleNamespace(catalog=SimpleNamespace(records=[]),
            effects=SimpleNamespace(specs={pub:spec}),relay=SimpleNamespace(owner=fixture.old.base.OWNER_PK))
        self.h.onboarding_config=SimpleNamespace(trusted_relays={'https://relay.test'})
        self.h.retain_reaction_async=mock.AsyncMock()
        self.enterContext(mock.patch('hostd.bot_admission.channel_candidate',return_value=True))
        ready=asyncio.Event()
        async def feed(identity,env,channel,on_event,on_status,**kw):
            self.assertEqual(kw['author'],pub)
            self.callback=lambda event:on_event(identity,event)
            ready.set();await asyncio.Event().wait()
        self.enterContext(mock.patch.object(fixture.hd.relay_feed,'follow',side_effect=feed))
        await self.h.refresh_outlets('alpha');await asyncio.wait_for(ready.wait(),1)
        self.h.dirty['alpha'].clear();self.h.wake['alpha'].clear()

    async def asyncTearDown(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        # Fixture onboarding is only the already-authorized adapter assembly.
        self.h.onboarding=None
        await self.h._shutdown()

    async def emit(self,kind=9,created=1000):
        with mock.patch.object(fixture.hd.time,'time',return_value=1000):
            await self.callback({'kind':kind,'created_at':created})

    async def test_callback_promotes_existing_waiting_ticket_into_reserved_fourth_slot(self):
        slots=self.h._round_slots;entered=[];release=asyncio.Event()
        async def job(name):
            async with slots.slot(name):entered.append(name);await release.wait()
        self.tasks=[asyncio.create_task(job(name)) for name in ('a','b','c','alpha')]
        try:
            await asyncio.sleep(0);self.assertEqual(entered,['a','b','c'])
            await self.emit();await asyncio.sleep(0)
            self.assertIn('alpha',entered,'actual owned feed callback must promote its waiting binding')
            self.assertEqual(len(slots.active),4)
            self.assertEqual(self.h.dirty['alpha'],{'buzz'})
        finally:release.set();await asyncio.gather(*self.tasks)

    async def test_only_fresh_allowed_kinds_promote(self):
        with mock.patch.object(self.h._round_slots,'promote') as promote:
            for kind in (9,7,5,40003):
                for stamp in (970,1000):
                    await self.emit(kind,stamp)
            self.assertEqual(promote.call_count,8)
            for kind,stamp in ((9,969),(9,1001),(9,True),(9,1000.0),(9,'1000'),(0,1000),(9000,1000),(None,1000)):
                await self.emit(kind,stamp)
            await self.callback({'type':'_reconnected'})
            self.assertEqual(promote.call_count,8)

    async def test_capture_failure_never_promotes_or_acknowledges_event(self):
        self.h.retain_reaction_async.side_effect=ValueError('fixed capture failure')
        with mock.patch.object(self.h._round_slots,'promote') as promote:
            with self.assertRaises(ValueError):await self.emit(7)
            promote.assert_not_called();self.assertFalse(self.h.dirty['alpha'])

    async def test_paused_binding_keeps_hint_but_never_dispatches(self):
        with Store(self.h.store_path) as db:db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            task=asyncio.create_task(self.h.worker('alpha'));self.tasks.append(task)
            await self.emit();await asyncio.sleep(.03)
        self.assertFalse(self.h.workers['alpha'].calls)
        self.assertIn('buzz',self.h.dirty['alpha'])
        self.assertFalse(self.h._round_slots.active)

    async def test_continuous_callback_promotions_preserve_background_service(self):
        self.h._round_slots=RoundSlots(1);slots=self.h._round_slots;order=[];release=asyncio.Event()
        async def background():
            async with slots.slot('background'):order.append('background')
        async def live():
            for _ in range(16):
                await self.emit()
                async with slots.slot('alpha'):order.append('alpha')
        async with slots.slot('occupied'):
            bg=asyncio.create_task(background());urgent=asyncio.create_task(live());self.tasks.extend((bg,urgent))
            await asyncio.sleep(0)
        await asyncio.gather(bg,urgent)
        self.assertLessEqual(order.index('background'),8)

if __name__=='__main__':unittest.main()
