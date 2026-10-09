"""Actual Console pause must not wait for unrelated admitted rounds."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock
import test_hostd_console_wiring as fixture
from hostd.round_slots import RoundSlots
from hostd import console_pause

class PauseQueued(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ConsoleWiring.asyncSetUp
    asyncTearDown=fixture.ConsoleWiring.asyncTearDown
    request=fixture.ConsoleWiring.request
    persisted=fixture.ConsoleWiring.persisted
    wait_for=fixture.ConsoleWiring.wait_for
    start_worker=fixture.ConsoleWiring.start_worker

    async def test_pause_finishes_while_another_binding_keeps_its_slot(self):
        calls=[];self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        self.h.notice_hints['alpha']={'f'*64}
        with mock.patch.object(fixture.fixture.hd,'DEBOUNCE',.001):
            async with self.h._round_slots.slot('unrelated'):
                await self.start_worker()
                self.h.mark('alpha','buzz');self.h.mark('alpha','feishu',thread='om_pending')
                await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
                _,op=await self.request('pause')
                await self.wait_for(lambda:console_pause.fence(self.h.console_store,'alpha') is not None)
                await asyncio.sleep(.08)
                row=self.h.console_store.console_operation(op['id'],self.h.console._principal())
                self.assertEqual(row.status,'completed','pause must drain its unadmitted ticket before the unrelated slot ends')
                self.assertEqual(self.persisted(),'paused');self.assertEqual(calls,[])
                self.assertEqual([t.name for t in self.h._round_slots.active],['unrelated'])
                self.assertFalse(any(t.name=='alpha' for t in self.h._round_slots.waiting))
                self.assertTrue({'buzz','feishu'} <= self.h.dirty['alpha'])
                self.assertIn('om_pending',self.h.threads['alpha'])
                self.assertIn('f'*64,self.h.notice_hints['alpha'])

    async def test_pause_during_refresh_prevents_a_later_ticket_from_waiting(self):
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        self.h.onboarding=SimpleNamespace()
        async def refresh(name):entered.set();await release.wait()
        with mock.patch.object(fixture.fixture.hd,'DEBOUNCE',.001),mock.patch.object(self.h,'refresh_outlets',side_effect=refresh):
            async with self.h._round_slots.slot('unrelated'):
                await self.start_worker();self.h.mark('alpha','buzz');self.h.mark('alpha','feishu',thread='om_refresh')
                await entered.wait()
                self.assertFalse(any(t.name=='alpha' for t in self.h._round_slots.waiting))
                _,op=await self.request('pause')
                await self.wait_for(lambda:console_pause.fence(self.h.console_store,'alpha') is not None)
                release.set()
                await self.wait_for(lambda:self.persisted()=='paused')
                row=self.h.console_store.console_operation(op['id'],self.h.console._principal())
                self.assertEqual(row.status,'completed');self.assertEqual(calls,[])
                self.assertFalse(any(t.name=='alpha' for t in self.h._round_slots.waiting))
                self.assertTrue({'buzz','feishu'} <= self.h.dirty['alpha'])
                self.assertIn('om_refresh',self.h.threads['alpha'])

if __name__=='__main__':unittest.main()
