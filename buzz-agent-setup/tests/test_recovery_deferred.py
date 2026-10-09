"""REC-011 controller half: a blocked receipt is pending, never terminal."""
import copy
import unittest

import test_agent_recovery as fixtures
import recovery_inventory


class DeferredTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    run_recovery = fixtures.RecoveryTests.run_recovery
    admitted_snapshot = fixtures.RecoveryTests.admitted_snapshot

    def test_ordinary_pending_work_positive_control(self):
        result = self.run_recovery()
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 4, "errors": []})
        self.assertEqual(len(set(events)), 4)

    def test_previous_protocol_cannot_silently_hide_deferred_work(self):
        self.current["version"] = 5
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            self.run_recovery()

    def blocked(self):
        self.assertEqual(self.run_recovery()["continued"], 4)
        value = self.admitted_snapshot("blocked")
        value["deferred"] = {}
        for event in self.relay.events.values():
            if event["id"] in value["receipts"]:
                tag = next(t for t in event["tags"] if t[0] == "recovery")
                value["deferred"][event["id"]] = dict(attempt=tag[1], reason="recovery_source_not_member",
                                                     next_check_at=115, checks=0, notice=None)
        return value

    def test_blocked_is_not_a_terminal_outcome_or_current_ownership(self):
        blocked = self.blocked()
        originals, active, terminal = recovery_inventory.validate(blocked)
        self.assertEqual(set(originals), set(self.old["input_sources"]))
        self.assertEqual(active, set())
        self.assertEqual(terminal, set(), "blocked source was silently marked completed")

    def test_same_generation_reconciles_original_delivery_without_new_continue(self):
        self.current = self.blocked()
        before = copy.deepcopy(self.relay.events)
        self.assertEqual(self.run_recovery(), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(self.relay.events, before)

    def test_death_while_blocked_preserves_work_for_successor_generation(self):
        blocked = self.blocked()
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        result = self.run_recovery(self.old, blocked)
        events = result.pop("continued_events")
        self.assertEqual(result, {"continued": 4, "errors": []})
        self.assertEqual(len(set(events)), 4)

    def test_orphan_and_malformed_deferred_state_is_not_authority(self):
        blocked = self.blocked()
        for mutation in ("missing", "orphan", "attempt", "reason", "checks"):
            value = copy.deepcopy(blocked)
            event_id = next(iter(value["deferred"]))
            if mutation == "missing":
                value.pop("deferred")
            elif mutation == "orphan":
                value["deferred"].pop(event_id)
            else:
                value["deferred"][event_id][mutation] = {"attempt": "wrong", "reason": "PRIVATE TEXT", "checks": -1}[mutation]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                recovery_inventory.validate(value)


if __name__ == "__main__":
    unittest.main()
