"""Signed inbound retries cannot silently change their message identity."""
from pathlib import Path
import sys
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts' / 'hostd'), str(Path(__file__).resolve().parent)]
import test_hostd_store as fixtures
from store import StoreError


class SealedDelivery(unittest.TestCase):
    binding = fixtures.StoreTests.binding

    def setUp(self):
        fixtures.StoreTests.setUp(self)

    def test_seal_survives_empty_reservation_and_rejects_changed_event(self):
        op = self.db.reserve_delivery('alpha', 'om_source', 'f2b', source_at=100, now=101)
        self.db.seal_delivery_content(op.id, 'ab'*32, now=102)
        self.db.reserve_delivery('alpha', 'om_source', 'f2b', source_at=100, now=103)
        self.assertEqual(self.db.conn.execute('SELECT content_hash FROM delivery WHERE id=?', (op.id,)).fetchone()[0], 'ab'*32)
        with self.assertRaises(StoreError):
            self.db.seal_delivery_content(op.id, 'cd'*32, now=104)
        with self.assertRaises(StoreError):
            self.db.reserve_delivery('alpha', 'om_source', 'f2b', content_hash='cd'*32, now=104)
        self.db.seal_delivery_content(op.id, 'ab'*32, now=105)

    def test_ack_must_match_signed_event_and_rollback_keeps_pending(self):
        op = self.db.reserve_delivery('alpha', 'om_source', 'f2b', source_at=100, now=101)
        self.db.seal_delivery_content(op.id, 'ab'*32, now=102)
        with self.assertRaises(StoreError):
            self.db.ack_delivery(op.id, 'cd'*32, now=103)
        self.assertEqual(self.db.cursor_position('alpha', 'feishu'), 0)
        self.db.ack_delivery(op.id, 'ab'*32, now=104)
        self.assertEqual(self.db.cursor_position('alpha', 'feishu'), 100)

    def test_unrelated_direction_cannot_be_sealed_as_inbound(self):
        op = self.db.reserve_delivery('alpha', 'ab'*32, 'b2f', now=101)
        with self.assertRaises(StoreError):
            self.db.seal_delivery_content(op.id, 'cd'*32, now=102)


if __name__ == '__main__':
    unittest.main()
