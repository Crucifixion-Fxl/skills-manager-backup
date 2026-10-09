import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.round_slots import RoundSlots, AdmissionWithdrawn

class Slots(unittest.IsolatedAsyncioTestCase):
    async def test_eight_worker_live_burst_still_admits_background(self):
        slots=RoundSlots(8,2);order=[];gate=asyncio.Event()
        async def job(name,wait=False):
            async with slots.slot(name):
                order.append(name)
                if wait:await gate.wait()
        first=[]
        for i in range(8):
            name='first'+str(i);slots.promote(name);first.append(asyncio.create_task(job(name,True)))
        await asyncio.sleep(0)
        background=asyncio.create_task(job('background'))
        live=[]
        for i in range(20):
            name='live'+str(i);slots.promote(name);live.append(asyncio.create_task(job(name)))
        await asyncio.sleep(0)
        try:
            self.assertEqual(len(slots.active),8)
        finally:
            gate.set();await asyncio.gather(*first,background,*live)
        self.assertLessEqual(order.index('background'),16)
        self.assertFalse(slots.waiting or slots.active)

    async def test_withdraw_waiting_is_exact_and_never_cancels_active_work(self):
        slots=RoundSlots(1);entered=[];withdrawn=[]
        async def job(name):
            try:
                async with slots.slot(name):entered.append(name)
            except AdmissionWithdrawn:withdrawn.append(name)
        async with slots.slot('running'):
            first=asyncio.create_task(job('paused'));other=asyncio.create_task(job('other'))
            await asyncio.sleep(0)
            self.assertEqual(slots.withdraw_waiting('running'),0)
            self.assertEqual(slots.withdraw_waiting('paused'),1)
            self.assertEqual(slots.withdraw_waiting('paused'),0)
            await first
            self.assertFalse(first.cancelled());self.assertEqual(withdrawn,['paused'])
            self.assertEqual(entered,[]);self.assertEqual([t.name for t in slots.active],['running'])
            self.assertEqual([t.name for t in slots.waiting],['other'])
        await other;self.assertEqual(entered,['other'])

    async def test_already_admitted_before_task_resumes_cannot_be_withdrawn(self):
        slots=RoundSlots(1);entered=[]
        async def job():
            async with slots.slot('granted'):entered.append(True)
        async with slots.slot('first'):
            task=asyncio.create_task(job());await asyncio.sleep(0)
            self.assertEqual([t.name for t in slots.waiting],['granted'])
        # __aexit__ has admitted the next ticket, but that task has not run yet.
        self.assertEqual(entered,[])
        self.assertEqual(slots.withdraw_waiting('granted'),0)
        await task;self.assertEqual(entered,[True]);self.assertFalse(task.cancelled())

    async def test_background_backlog_leaves_slot_for_promoted_real_event(self):
        slots=RoundSlots(4);entered=[];release=asyncio.Event()
        async def job(name):
            async with slots.slot(name):entered.append(name);await release.wait()
        tasks=[asyncio.create_task(job(str(i))) for i in range(8)]
        try:
            await asyncio.sleep(0);self.assertEqual(entered,['0','1','2'])
            slots.promote('7');await asyncio.sleep(0)
            self.assertEqual(entered,['0','1','2','7']);self.assertEqual(len(slots.active),4)
        finally:
            release.set();await asyncio.gather(*tasks)
        self.assertEqual(len(entered),8);self.assertFalse(slots.active or slots.waiting)
    async def test_queued_cancel_and_granted_cancel_release_capacity(self):
        slots=RoundSlots(2);gate=asyncio.Event();entered=[]
        async def job(name):
            async with slots.slot(name):entered.append(name);await gate.wait()
        first=asyncio.create_task(job('first'));waiting=asyncio.create_task(job('wait'))
        await asyncio.sleep(0);waiting.cancel();await asyncio.gather(waiting,return_exceptions=True)
        live=asyncio.create_task(job('live'));await asyncio.sleep(0);slots.promote('live');live.cancel()
        await asyncio.gather(live,return_exceptions=True)
        self.assertEqual(len(slots.active),1);self.assertFalse(slots.waiting)
        gate.set();await first;self.assertFalse(slots.active)
    async def test_live_only_uses_all_capacity_and_background_is_not_starved(self):
        slots=RoundSlots(1);order=[];gate=asyncio.Event()
        async def job(name,wait=False):
            async with slots.slot(name):
                order.append(name)
                if wait:await gate.wait()
        slots.promote('first');first=asyncio.create_task(job('first',True));await asyncio.sleep(0)
        background=asyncio.create_task(job('background'))
        tasks=[]
        for i in range(12):slots.promote(str(i));tasks.append(asyncio.create_task(job(str(i))))
        await asyncio.sleep(0);gate.set();await asyncio.gather(first,background,*tasks)
        self.assertLessEqual(order.index('background'),8)

if __name__=='__main__':unittest.main()
