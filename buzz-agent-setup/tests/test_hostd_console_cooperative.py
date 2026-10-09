"""Console resume proves healthy work resumed, not that all history drained."""
import unittest
import test_hostd_console_resume_durable as fixture
from hostd.store import Store

class CooperativeResume(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ResumeDurable.asyncSetUp
    asyncTearDown=fixture.ResumeDurable.asyncTearDown
    request=fixture.ResumeDurable.request
    persisted=fixture.ResumeDurable.persisted
    wait_for=fixture.ResumeDurable.wait_for
    pause=fixture.ResumeDurable.pause

    async def report(self, *, errors=0, **meta):
        await self.pause()
        calls=[]
        report={'errors':errors,'hostd':dict(verdict='off',retry_phases=['buzz'],cooperative_phases=['buzz'],pending_targets=0,outlet_pending=1)}
        report['hostd'].update(meta)
        self.h.workers['alpha'].run=lambda *args,**kw:calls.append(True) or report
        _,op=await self.request('resume')
        result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(calls,[True])
        row=self.h.console_store.console_operation(op['id'],self.h.console._principal())
        return result,row

    async def test_cooperative_slice_completes_original_resume_without_claiming_empty_history(self):
        result,row=await self.report()
        self.assertEqual(result['status'],'completed')
        self.assertIsNotNone(row.receipt_hash)
        self.assertEqual(self.persisted(),'active')
        self.assertEqual(self.h.retry_phases['alpha'],{'buzz'})

    async def test_mixed_noncooperative_retry_cannot_complete(self):
        result,row=await self.report(retry_phases=['buzz','feishu'])
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)
        self.assertEqual(self.persisted(),'degraded')

    async def test_unknown_delivery_error_cannot_hide_behind_cooperative_phase(self):
        with Store(self.h.store_path) as db:
            delivery=db.reserve_delivery('alpha','a'*64,'b2f',source_at=1,now=2)
            db.fail_delivery(delivery.id,unknown=True,now=3)
        result,row=await self.report(errors=1)
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)
        self.assertEqual(self.persisted(),'degraded')
        with Store(self.h.store_path) as db:self.assertEqual(db.delivery_record(delivery.id)['status'],'unknown')

    async def test_pending_target_cannot_complete_even_with_both_cooperative_phases(self):
        result,row=await self.report(pending_targets=1,cooperative_phases=['buzz','feishu'])
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)

    async def test_lost_claim_cannot_complete(self):
        result,row=await self.report(verdict='lost')
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)
        self.assertEqual(self.persisted(),'conflict')

    async def test_invalid_retry_metadata_cannot_complete(self):
        result,row=await self.report(retry_phases='buzz')
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)

    async def test_invalid_cooperative_metadata_cannot_complete(self):
        result,row=await self.report(cooperative_phases='buzz')
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)

    async def test_invalid_pending_metadata_cannot_complete(self):
        result,row=await self.report(pending_targets=False)
        self.assertEqual(result['status'],'unknown');self.assertIsNone(row.receipt_hash)

if __name__=='__main__':unittest.main()
