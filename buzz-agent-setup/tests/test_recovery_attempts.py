"""REC-013: a receipt must bind the exact canonical recovery attempt."""
import copy
import json
import unittest

import test_agent_recovery as fixtures
import recovery_inventory
from agent_recovery import RecoveryStore
from recovery_relay import canonical_origin
from recovery_attempt import attempt_id


class AttemptTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    run_recovery = fixtures.RecoveryTests.run_recovery
    admitted_snapshot = fixtures.RecoveryTests.admitted_snapshot

    def bound(self):
        self.assertEqual(self.run_recovery()["continued"], 4)
        value = self.admitted_snapshot()
        value["recovery_attempts"] = {event["id"]: tag[1] for event in self.relay.events.values()
                                      for tag in event["tags"] if tag[0] == "recovery"}
        return value

    def test_exact_attempt_receipt_positive_control_preserves_current_ownership(self):
        state = self.bound()
        originals, active, terminal = recovery_inventory.validate(state)
        self.assertEqual(set(originals), active)
        self.assertEqual(terminal, set())

    def test_receipts_cannot_omit_or_change_their_attempt_binding(self):
        state = self.bound()
        for mutation in ("missing", "orphan", "extra", "digest", "agent", "generation", "relay", "source"):
            value = copy.deepcopy(state)
            input_id = next(iter(value["receipts"]))
            if mutation == "missing": value.pop("recovery_attempts")
            elif mutation == "orphan": value["recovery_attempts"].pop(input_id)
            elif mutation == "extra": value["recovery_attempts"]["0" * 64] = "1" * 64
            elif mutation == "digest": value["recovery_attempts"][input_id] = "0" * 64
            elif mutation == "agent": value["agent_pubkey"] = "b" * 64
            elif mutation == "generation": value["generation"] = "00000000-0000-0000-0000-000000000199"
            elif mutation == "relay": value["relay"] = "https://another-relay.invalid"
            else: value["input_sources"][input_id][0]["event_id"] = "d" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                recovery_inventory.validate(value)

    def test_old_protocol_is_rejected_instead_of_inventing_attempt_receipts(self):
        self.current["version"] = 7
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            self.run_recovery()

    def test_outbox_attempt_substitution_cannot_discharge_any_original_work(self):
        state = self.bound()
        with RecoveryStore(self.db) as store:
            key, encoded = store.db.execute("SELECT key,event FROM messages WHERE key LIKE '%:continue' LIMIT 1").fetchone()
            event = json.loads(encoded)
            next(tag for tag in event["tags"] if tag[0] == "recovery")[1] = "0" * 64
            with store.db:
                store.db.execute("UPDATE messages SET event=? WHERE key=?", (json.dumps(event), key))
            with self.assertRaisesRegex(ValueError, "recovery_receipt_inconsistent"):
                store.reconcile(state)
            for work in self.old["input_sources"]:
                self.assertFalse(store.covered(work))

    def test_terminal_receipt_cannot_hide_wrong_delivery_envelope(self):
        self.bound()
        for status in ("completed", "cancelled"):
            state = self.admitted_snapshot(status)
            with RecoveryStore(self.db) as store:
                key, encoded = store.db.execute("SELECT key,event FROM messages WHERE key LIKE '%:continue' LIMIT 1").fetchone()
                for mutation in ("channel", "root", "reply", "agent", "kind", "extra_channel"):
                    event = json.loads(encoded)
                    if mutation == "kind": event["kind"] = 1
                    elif mutation == "extra_channel": event["tags"].append(["h", fixtures.CHANNELS[1]])
                    elif mutation in ("channel", "agent"):
                        tag = next(t for t in event["tags"] if t[0] == ("h" if mutation == "channel" else "p"))
                        tag[1] = fixtures.CHANNELS[1] if mutation == "channel" else "d" * 64
                    else:
                        next(t for t in event["tags"] if t[0] == "e" and t[3] == mutation)[1] = "e" * 64
                    with store.db:
                        store.db.execute("UPDATE messages SET event=? WHERE key=?", (json.dumps(event), key))
                    with self.subTest(status=status, mutation=mutation), self.assertRaisesRegex(ValueError, "recovery_receipt_inconsistent"):
                        store.reconcile(state)
                    self.assertEqual(store.db.execute("SELECT count(*) FROM covered").fetchone()[0], 0)
                with store.db:
                    store.db.execute("UPDATE messages SET event=? WHERE key=?", (encoded, key))

    def test_shared_digest_golden_vector(self):
        self.assertEqual(attempt_id("wss://RELAY.INVALID:443/", "a" * 64,
                                   "00000000-0000-0000-0000-000000000099", fixtures.CHANNELS[0],
                                   "b" * 64, ["1" * 64, "2" * 64]),
                         "cc761139c3569c5dba5203fa82f4f1f4274a1b3488a59d0470612fd914b20324")


class OriginTests(unittest.TestCase):
    def test_equivalent_explicit_origins_match_native_url_canonicalization(self):
        for source, expected in (("wss://RELAY.INVALID:443/", "https://relay.invalid"),
                                 ("ws://LOCALHOST:80", "http://localhost"),
                                 ("https://[0:0:0:0:0:0:0:1]:443", "https://[::1]"),
                                 ("wss://relay.invalid:8443", "https://relay.invalid:8443")):
            with self.subTest(source=source):
                self.assertEqual(canonical_origin(source), expected)

    def test_malformed_or_non_ascii_origins_fail_closed_without_guessing_a_legacy_alias(self):
        for value in ("https://relay.invalid:wrong", " https://relay.invalid", "https://例子.invalid", "https://%72elay.invalid",
                      "https://127.1", "https://0x7f000001", "https://127.0.0.1.",
                      "https://relay.invalid/./", "https://@relay.invalid", "https://relay.invalid?", "https://relay.invalid#"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_origin(value)


if __name__ == "__main__":
    unittest.main()
