"""Original work responsibility, through the production recovery round."""
import copy
import unittest

import test_agent_recovery as fixtures


class SourceTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    run_recovery = fixtures.RecoveryTests.run_recovery

    def continues(self):
        return [event for event in self.relay.events.values() if event["content"] == "@test-dev continue"]

    def test_delivery_binds_sorted_original_work_not_owner_message(self):
        self.assertEqual(self.run_recovery()["continued"], 4)
        sources = self.old["input_sources"]
        delivered = set()
        for event in self.continues():
            tag = next(tag for tag in event["tags"] if tag[0] == "recovery")
            self.assertGreater(len(tag), 3, "owner continue has lost its original request")
            self.assertEqual(tag[3:], sorted(set(tag[3:])))
            for work in tag[3:]:
                source = sources[work][0]
                self.assertIn(["h", source["route"]["channel"]], event["tags"])
                self.assertIn(["e", source["route"]["root"], "", "root"], event["tags"])
                delivered.add(work)
        self.assertEqual(delivered, set(sources))

    def test_terminal_original_cannot_revive_when_controller_database_is_new(self):
        terminal = copy.deepcopy(self.old)
        terminal["generation"] = "00000000-0000-0000-0000-000000000097"
        terminal["active"] = terminal["triggers"] = {}
        terminal["receipts"] = dict.fromkeys(terminal["receipts"], "completed")
        self.assertEqual(self.run_recovery(self.old, terminal)["continued"], 0)
        self.assertEqual(self.relay.published, [])

    def test_current_runtime_ownership_suppresses_old_copy_without_ledger(self):
        self.current = copy.deepcopy(self.old)
        self.current["generation"] = "00000000-0000-0000-0000-000000000099"
        self.assertEqual(self.run_recovery()["continued"], 0)
        self.assertEqual(self.relay.published, [])

    def test_identity_substitution_between_generations_blocks_all_sends(self):
        changed = copy.deepcopy(self.old)
        changed["generation"] = "00000000-0000-0000-0000-000000000097"
        next(iter(changed["input_sources"].values()))[0]["signed_author"] = "c" * 64
        result = self.run_recovery(self.old, changed)
        self.assertTrue(result["errors"])
        self.assertEqual(self.relay.published, [])

    def test_missing_original_binding_is_not_inferred_from_thread_owner(self):
        self.old["input_sources"].pop(next(iter(self.old["input_sources"])))
        self.assertTrue(self.run_recovery()["errors"])
        self.assertEqual(self.relay.published, [])

    def test_empty_history_positive_control(self):
        empty = fixtures.snapshot("00000000-0000-0000-0000-000000000097")
        self.assertEqual(self.run_recovery(empty), {"continued": 0, "errors": [], "continued_events": []})


if __name__ == "__main__":
    unittest.main()
