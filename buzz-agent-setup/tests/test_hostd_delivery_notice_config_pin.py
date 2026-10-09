"""Private real Root/config-pin regression proposal; not imported or run."""
from pathlib import Path
import json
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS), str(SCRIPTS / 'hostd')]
import test_hostd_delivery_notice_wiring as wiring
from hostd import binding_worker, config
from hostd.safety import read_owned

setUpModule = wiring.setUpModule
tearDownModule = wiring.tearDownModule


class DeliveryNoticeConfigPinTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_binding_without_duplicate_pin_observes_signed_source_from_reviewed_root_pin(self):
        w = wiring.WiringWorld(self)
        h = await w.root()
        w.ready()  # Existing controlled integer UTC isolates the separate default FLOAT bug.
        lane = w.lanes['initial']
        self.assertIs(type(h.workers[lane.name]), binding_worker.Worker)
        raw = json.loads(read_owned(lane.env.config))
        self.assertNotIn('claim_relay_pubkey', raw,
            'real binding must not require a duplicate new trust field')
        parsed = config.load_config(lane.env.config)  # Actual protected + strict legacy delegation.
        self.assertEqual((parsed['channel_id'], parsed['chat_id']), (lane.channel, lane.chat))
        self.assertEqual(h.workers[lane.name].claim_relay_pubkey, h.onboarding_config.relay_pubkey)
        self.assertEqual(h.workers[lane.name].claim_relay_pubkey, wiring.keys.PIN)
        self.assertIs(h.workers[lane.name].http_pool, h.http_pool)
        self.assertIs(h.workers[lane.name].scheduler, h.scheduler)
        await w.drive(lane, phase='members')
        self.assertEqual(w.binding_state(lane.name), 'active')
        self.assertTrue(any(any(f.get('kinds') == [39002] and f.get('authors') == [wiring.keys.PIN]
            and f.get('#d') == [lane.channel] for f in batch) for batch in lane.query_calls),
            'actual setup must authenticate the current roster using the reviewed explicit pin')
        source = lane.source('original-binding-config-no-duplicate-pin')
        self.assertTrue(wiring.gs._nip01_event_verified(source))
        self.assertNotIn(wiring.keys.PUB, lane.cfg['agents'])
        self.assertNotIn(wiring.keys.PUB, h.onboarding.effects.specs)
        self.assertNotIn(wiring.keys.APP, h.app_profiles)
        await w.drive(lane, source)
        self.assertEqual(w.binding_state(lane.name), 'active')
        self.assertNotIn('claim_relay_pubkey', json.loads(read_owned(lane.env.config)))
        row = w.row(lane.name, source)
        self.assertIsNotNone(row,
            'actual protected original binding config must reach signed observation through the explicit Root pin')
        self.assertEqual(row.state, 'waiting')
        self.assertEqual((row.source_id, row.source_author_pubkey, row.source_app_id, row.root_message_id),
                         (source['id'], wiring.keys.PUB, wiring.keys.APP, lane.root_mid))
        self.assertEqual((row.first_observed_at, row.deadline_at), (w.now, w.now + 60))
        self.assertTrue(any(any(f.get('ids') == [source['id']] for f in batch) for batch in lane.query_calls),
            'observation must re-read the full actual signed source')
        self.assertFalse(w.effects, 'no proxy, notice POST or foreign private lane is a setup shortcut')
