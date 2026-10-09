"""REC-015: delivered != admitted; silence must not create another continue."""
from unittest import mock
import sqlite3
import unittest

import test_agent_recovery as fixtures
import test_recovery_deferred as deferred


class ReceiptTimeoutTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    run_recovery = fixtures.RecoveryTests.run_recovery
    admitted_snapshot = fixtures.RecoveryTests.admitted_snapshot
    blocked = deferred.DeferredTests.blocked

    def at(self, now):
        with mock.patch("time.time", return_value=now):
            return self.run_recovery()

    def slow_notices(self):
        return [event for event in self.relay.events.values() if "尚未确认接收" in event["content"]]

    def test_delivered_without_receipt_reports_once_at_120s_without_new_continue(self):
        self.assertEqual(self.at(1000)["continued"], 4)
        original = [event for event in self.relay.events.values() if event["content"] == "@test-dev continue"]
        self.assertEqual(self.at(1119), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(self.slow_notices(), [])
        result = self.at(1120)
        self.assertEqual(result["continued"], 0)
        self.assertEqual(result["errors"], ["receipt_timeout"] * 4)
        notices = self.slow_notices()
        self.assertEqual(len(notices), 4)
        for event in notices:
            self.assertIn("owner", event["content"])
            self.assertIn("无需", event["content"])
            self.assertEqual(len([tag for tag in event["tags"] if tag[0] == "h"]), 1)
            self.assertEqual([tag[3] for tag in event["tags"] if tag[0] == "e"], ["root", "reply"])
            self.assertFalse(any(tag[0] == "p" for tag in event["tags"]))
        self.at(100000)
        self.assertEqual(self.slow_notices(), notices, "reopening SQLite and later ticks duplicated the warning")
        self.assertEqual([event for event in self.relay.events.values() if event["content"] == "@test-dev continue"], original)

    def test_native_active_completed_cancelled_receipts_suppress_slow_notice(self):
        for status in ("active", "completed", "cancelled"):
            with self.subTest(status=status):
                self.at(1000)
                self.current = self.admitted_snapshot(status)
                self.assertEqual(self.at(1120), {"continued": 0, "errors": [], "continued_events": []})
                self.assertEqual(self.slow_notices(), [])

    def test_known_native_denial_is_not_misreported_as_no_receipt(self):
        with mock.patch("time.time", return_value=1000):
            self.current = self.blocked()
        result = self.at(1120)
        self.assertNotIn("receipt_timeout", result["errors"])
        self.assertEqual(self.slow_notices(), [])

    def test_wait_begins_after_exact_delivery_readback_not_before_publication(self):
        publish = self.relay.publish
        def offline_continue(event):
            if event["content"] == "@test-dev continue":
                raise TimeoutError("offline before accepting event")
            publish(event)
        self.relay.publish = offline_continue
        self.assertEqual(self.at(1000)["continued"], 0)
        self.assertEqual(self.at(1500)["continued"], 0)
        self.assertEqual(self.slow_notices(), [])
        self.relay.publish = publish
        self.assertEqual(self.at(1600)["continued"], 4)
        self.assertEqual(self.at(1719), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(self.at(1720)["errors"], ["receipt_timeout"] * 4)
        self.assertEqual(len(self.slow_notices()), 4)

    def test_lost_warning_ack_reconciles_same_signed_notice_after_restart(self):
        self.at(1000)
        self.relay.lose_ack = True
        first = self.at(1120)
        self.assertIn("recovery_delivery_unverified", first["errors"])
        self.assertEqual(len(self.slow_notices()), 4)
        published = list(self.relay.published)
        self.at(1121)
        self.assertEqual(self.relay.published, published, "lost ACK must read back, not publish new warning")

    def test_timeout_never_bypasses_current_channel_access(self):
        self.at(1000)
        self.relay.refuse.add(fixtures.CHANNELS[1])
        result = self.at(1120)
        self.assertEqual(result["continued"], 0)
        self.assertEqual(len(self.slow_notices()), 2)
        self.assertTrue(all(event["tags"][0] == ["h", fixtures.CHANNELS[0]] for event in self.slow_notices()))

    def test_delivery_ack_and_wait_clock_commit_atomically_on_actual_sqlite_failure(self):
        with fixtures.RecoveryStore(self.db) as store:
            store.delivery("attempt", self.current["generation"], ["a" * 64])
            store.db.execute("CREATE TRIGGER reject_clock BEFORE UPDATE ON delivery_receipts BEGIN SELECT RAISE(ABORT, 'clock write failed'); END")
            tags = [["h", fixtures.CHANNELS[0]]]
            with mock.patch("time.time", return_value=1000), self.assertRaises(sqlite3.IntegrityError):
                store.send(self.relay, "attempt", "@test-dev continue", tags)
            self.assertEqual(store.db.execute("SELECT acked FROM messages WHERE key='attempt'").fetchone(), (0,))
            self.assertEqual(store.db.execute("SELECT first_delivered_at FROM delivery_receipts WHERE key='attempt'").fetchone(), (None,))
            store.db.execute("DROP TRIGGER reject_clock")
            published = list(self.relay.published)
            with mock.patch("time.time", return_value=1050):
                store.send(self.relay, "attempt", "@test-dev continue", tags)
            self.assertEqual(self.relay.published, published, "reconcile accepted remote event rather than publishing again")
            self.assertEqual(store.db.execute("SELECT first_delivered_at FROM delivery_receipts WHERE key='attempt'").fetchone(), (1050,))


if __name__ == "__main__":
    unittest.main()
