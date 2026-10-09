"""Private default-production-clock regression proposal; not executed.

Immutable snapshot composes the reviewed three wiring modules and coordinator.
Only fixture wire timestamps follow actual UTC; Hostd.notice_clock is untouched.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS), str(SCRIPTS / 'hostd')]
import test_hostd_delivery_notice_wiring as wiring
from hostd import binding_worker

setUpModule = wiring.setUpModule
tearDownModule = wiring.tearDownModule


class WallClockWiringWorld(wiring.WiringWorld):
    """Only the lower signed/native world clock follows ordinary real UTC."""
    def setup_lane(self, name, channel, chat):
        # Parent __init__ seeds an old static date before setup_lane. Override
        # that low wire input BEFORE claims/profiles/root/source are signed.
        self.utc_datetime()
        return super().setup_lane(name, channel, chat)

    def utc_datetime(self):
        current = datetime.now(timezone.utc)
        self.now = int(current.timestamp())
        return current


class DeliveryNoticeDefaultClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_root_clock_observes_actual_signed_source_waiting(self):
        w = WallClockWiringWorld(self)
        h = await w.root()  # Actual Hostd + native pool + protected runtime factory.
        # Do not call WiringWorld.ready(): it replaces notice_clock for the
        # original controlled-UTC tests. No Root/Worker/coordinator clock patch.
        self.assertIs(type(h.workers['initial']), binding_worker.Worker)
        self.assertIs(h.workers['initial'].http_pool, h.http_pool)
        self.assertIs(h.workers['initial'].scheduler, h.scheduler)
        self.assertEqual(h.workers['initial'].claim_relay_pubkey, wiring.keys.PIN)
        self.assertTrue(callable(getattr(h, 'notice_wake_once', None)))
        lane = w.lanes['initial']
        await w.drive(lane, phase='members')
        self.assertEqual(w.binding_state(lane.name), 'active')
        w.utc_datetime()  # Low event input, not the producer clock or proof.
        source = lane.source('default-clock-foreign-source')
        self.assertTrue(wiring.gs._nip01_event_verified(source))
        self.assertNotIn(wiring.keys.PUB, lane.cfg['agents'])
        self.assertNotIn(wiring.keys.PUB, h.onboarding.effects.specs)
        self.assertNotIn(wiring.keys.APP, h.app_profiles)
        await w.drive(lane, source)
        self.assertEqual(w.binding_state(lane.name), 'active')
        row = w.row(lane.name, source)
        self.assertIsNotNone(row,
            'actual default Hostd UTC must reach fresh signed observation, not silently leave the source unobserved')
        self.assertEqual(row.state, 'waiting')
        self.assertEqual(row.source_id, source['id'])
        self.assertEqual(row.deadline_at, row.first_observed_at + 60)
        self.assertEqual(row.source_author_pubkey, wiring.keys.PUB)
        self.assertEqual(row.source_app_id, wiring.keys.APP)
        self.assertEqual(row.root_message_id, lane.root_mid)
        self.assertTrue(any(any(f.get('ids') == [source['id']] for f in batch)
            for batch in lane.query_calls), 'default clock must reach the full authenticated signed source read')
        self.assertFalse(w.effects, 'waiting observation must neither proxy B nor create a notice before its deadline')
