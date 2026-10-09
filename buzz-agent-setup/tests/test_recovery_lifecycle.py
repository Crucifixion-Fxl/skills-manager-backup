"""Lifecycle feedback describes historical interruption, not a late shutdown."""
import unittest
import copy

import test_agent_recovery as fixture
from agent_recovery import RecoveryStore, recover
from recovery_relay import wire
from recovery_lifecycle import STOPPING


class LifecycleTests(unittest.TestCase):
    setUp = fixture.RecoveryTests.setUp
    tearDown = fixture.RecoveryTests.tearDown
    def run_recovery(self):
        with RecoveryStore(self.db) as store:
            return recover(store, self.relay, agent_name="test-dev", agent_pubkey=self.current["agent_pubkey"],
                           relay=self.current["relay"], current=self.current,
                           snapshots=[self.old], runtime_verified=True)

    def planned(self, delivered=False):
        self.current["agent_pubkey"] = self.old["agent_pubkey"] = wire._signer_pubkey("2" * 64)
        self.old["phase"] = "stopping"
        for routes in self.old["active"].values():
            for route in routes:
                channel, root = route["channel"], route["root"]
                tags = [["h", channel], ["e", root, "", "root"], ["e", root, "", "reply"],
                        ["buzz:recovery-lifecycle", self.old["generation"], "stopping"]]
                self.old["shutdown_notices"][channel + ":" + root] = dict(route=route, notice={
                    "event": wire.sign_event("2" * 64, 9, tags, STOPPING, 100), "delivered": delivered})

    def test_crash_without_stop_hook_reports_interruption_and_recovery_once(self):
        self.assertEqual(self.old["phase"], "ready")
        first = self.run_recovery()
        self.assertEqual(first["continued"], 4)
        self.assertEqual(self.run_recovery()["continued"], 0)
        notices = [e for e in self.relay.events.values() if "启动成功" in e["content"]]
        self.assertEqual(len(notices), 4)
        for notice in notices:
            self.assertIn("异常中断", notice["content"])
            self.assertIn("无需", notice["content"])
            self.assertNotIn("正在停止", notice["content"])
            self.assertFalse(any(tag[0] == "p" for tag in notice["tags"]))

    def test_unsent_planned_shutdown_is_coalesced_not_replayed_after_ready(self):
        self.old["phase"] = "stopping"
        self.old["shutdown_notices"] = {
            route["channel"] + ":" + route["root"]: dict(route=route, notice=None)
            for routes in self.old["active"].values() for route in routes
        }
        self.assertEqual(self.run_recovery()["continued"], 4)
        self.assertEqual(self.run_recovery()["continued"], 0)
        notices = [e for e in self.relay.events.values() if "启动成功" in e["content"]]
        self.assertEqual(len(notices), 4)
        for notice in notices:
            self.assertIn("停机提示未确认送达", notice["content"])
            self.assertIn("无需", notice["content"])
            self.assertNotIn("正在停止", notice["content"])

    def test_frozen_unsent_shutdown_is_never_posted_by_the_controller(self):
        self.planned()
        frozen = [v["notice"]["event"]["id"] for v in self.old["shutdown_notices"].values()]
        original = copy.deepcopy(self.old)
        self.assertEqual(self.run_recovery()["continued"], 4)
        self.assertFalse(set(frozen).intersection(self.relay.events))
        self.assertEqual(self.old, original, "controller mutated native lifecycle evidence")
        self.assertEqual(sum("停机提示未确认送达" in e["content"] for e in self.relay.events.values()), 4)

    def test_confirmed_shutdown_has_no_false_delivery_failure(self):
        self.planned(delivered=True)
        self.assertEqual(self.run_recovery()["continued"], 4)
        notices = [e for e in self.relay.events.values() if "启动成功" in e["content"]]
        self.assertEqual(len(notices), 4)
        self.assertTrue(all("因停止或重启中断" in e["content"] and "未确认送达" not in e["content"] for e in notices))

    def test_completed_during_shutdown_does_not_claim_it_will_continue(self):
        self.planned()
        self.old["active"] = {}
        self.old["triggers"] = {}
        self.old["receipts"] = dict.fromkeys(self.old["receipts"], "completed")
        self.assertEqual(self.run_recovery(), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(self.relay.events, {})

    def test_invalid_shutdown_inventory_fails_closed_before_any_publication(self):
        self.planned()
        valid = copy.deepcopy(self.old)
        for mutation in ("missing", "delivered", "content", "generation", "route", "author", "phase", "extra"):
            self.old = copy.deepcopy(valid)
            pending = next(iter(self.old["shutdown_notices"].values()))
            event = pending["notice"]["event"]
            if mutation == "missing":
                self.old.pop("shutdown_notices")
            elif mutation == "delivered":
                pending["notice"]["delivered"] = "true"
            elif mutation == "content":
                event["content"] = "tampered"
            elif mutation == "phase":
                self.old["phase"] = "ready"
            elif mutation == "extra":
                pending["secret"] = "unknown field"
            else:
                if mutation == "generation":
                    event["tags"][-1][1] = self.current["generation"]
                if mutation == "route":
                    event["tags"][1][1] = "a" * 64
                pending["notice"]["event"] = wire.sign_event("3" * 64 if mutation == "author" else "2" * 64,
                                                            9, event["tags"], event["content"], 100)
            with self.subTest(mutation=mutation):
                result = self.run_recovery()
                self.assertEqual(result["continued"], 0)
                self.assertEqual(result["errors"], ["invalid_runtime_snapshot"])
                self.assertEqual(self.relay.events, {})

    def test_previous_schema_is_rejected_without_dropping_its_journal(self):
        self.current["version"] = 8
        original = copy.deepcopy(self.old)
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            self.run_recovery()
        self.assertEqual(self.old, original)
        self.assertEqual(self.relay.events, {})


if __name__ == "__main__":
    unittest.main()
