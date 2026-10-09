"""Paired v7 parsing: a native failure notice is not completion authority."""
import copy
import unittest

import test_agent_recovery as fixtures
import test_recovery_deferred as deferred_fixtures
import recovery_inventory
from recovery_relay import wire
from recovery_attempt import attempt_id


class NoticeTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    run_recovery = fixtures.RecoveryTests.run_recovery
    admitted_snapshot = fixtures.RecoveryTests.admitted_snapshot
    blocked = deferred_fixtures.DeferredTests.blocked

    def test_previous_protocol_cannot_hide_missing_notice_state(self):
        self.current["version"] = 6
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            self.run_recovery()

    def notice_state(self):
        state = self.blocked()
        state["agent_pubkey"] = wire._signer_pubkey("2" * 64)
        for input_id, value in state["deferred"].items():
            value["notice"] = None
            sources = state["input_sources"][input_id]
            route = sources[0]["route"]
            value["attempt"] = attempt_id(state["relay"], state["agent_pubkey"], state["generation"],
                                          route["channel"], route["root"], [s["event_id"] for s in sources])
            state["recovery_attempts"][input_id] = value["attempt"]
        input_id, pending = next(iter(state["deferred"].items()))
        route = state["input_sources"][input_id][0]["route"]
        tags = [["h", route["channel"]], ["e", route["root"], "", "root"],
                ["e", route["root"], "", "reply"], ["buzz:recovery-notice", input_id, pending["reason"]]]
        pending["notice"] = {"event": wire.sign_event("2" * 64, 9, tags, "自动续接未执行：请 owner 检查群权限。", 100),
                             "delivered": False}
        return state, input_id

    def test_signed_notice_does_not_close_or_take_original_work(self):
        state, input_id = self.notice_state()
        for delivered in (False, True):
            state["deferred"][input_id]["notice"]["delivered"] = delivered
            originals, active, terminal = recovery_inventory.validate(state)
            self.assertEqual(set(originals), set(self.old["input_sources"]))
            self.assertEqual(active, set())
            self.assertEqual(terminal, set())

    def test_tampered_or_unbound_notice_is_not_silently_ignored(self):
        state, input_id = self.notice_state()
        for mutation in ("missing", "delivered", "content", "author", "route", "extra"):
            changed = copy.deepcopy(state)
            pending = changed["deferred"][input_id]
            event = pending["notice"]["event"]
            if mutation == "missing":
                pending.pop("notice")
            elif mutation == "delivered":
                pending["notice"]["delivered"] = "yes"
            elif mutation == "content":
                event["content"] = "tampered"
            elif mutation == "author":
                pending["notice"]["event"] = wire.sign_event("3" * 64, 9, event["tags"], event["content"], 100)
            elif mutation == "route":
                event["tags"][0][1] = fixtures.CHANNELS[1]
                pending["notice"]["event"] = wire.sign_event("2" * 64, 9, event["tags"], event["content"], 100)
            else:
                pending["notice"]["private"] = "unexpected"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                recovery_inventory.validate(changed)


if __name__ == "__main__":
    unittest.main()
