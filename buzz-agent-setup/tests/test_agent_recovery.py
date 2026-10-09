"""Generic restart recovery: real persistence, deterministic fake relay boundary."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_recovery import JOURNAL_VERSION, RecoveryStore, recover
from recovery_relay import RouteBlocked
from recovery_attempt import attempt_id

AGENT = "a" * 64
OWNER = "f" * 64
CHANNELS = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]


def snapshot(generation, active=None, phase="ready"):
    state = dict(version=JOURNAL_VERSION, generation=generation, agent_pubkey=AGENT, relay="https://relay.invalid",
                runtime_policy=dict(owner=OWNER, respond_to="owner-only", allowlist=[]),
                phase=phase, channels=CHANNELS[:], active=active or {}, cancelled_turns=[],
                receipts={}, triggers={}, input_sources={}, recovery_receipts=[], recovery_attempts={}, deferred={}, shutdown_notices={})
    seed_originals(state)
    return state


def seed_originals(state):
    """State-only fixture; signed source authorization uses real Relay tests."""
    for turn, routes in state["active"].items():
        if turn in state["triggers"]:
            continue
        state["triggers"][turn] = []
        for route in routes:
            work = hashlib.sha256(json.dumps([turn, route], sort_keys=True).encode()).hexdigest()
            state["triggers"][turn].append(work)
            state["receipts"][work] = "active"
            state["input_sources"][work] = [dict(event_id=work, signed_author=OWNER,
                                                kind="signed-event", route=copy.deepcopy(route))]


class Relay:
    """Ambiguous writes are accepted remotely before raising locally."""
    def __init__(self):
        self.owner = OWNER
        self.events = {}
        self.published = []
        self.lose_ack = False
        self.refuse = set()

    def sign(self, content, tags):
        event = dict(pubkey=OWNER, kind=9, content=content, tags=tags, created_at=100)
        event["id"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        return event

    def validate_route(self, channel, root, agent):
        if channel in self.refuse:
            raise ValueError("agent_not_subscribed")
        return "owner-only"

    def validate_source(self, source, agent, policy):
        return source["signed_author"]

    def lookup(self, event_id):
        return copy.deepcopy(self.events.get(event_id))

    def publish(self, event):
        self.published.append(event["id"])
        self.events[event["id"]] = copy.deepcopy(event)
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("response lost")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "recovery.sqlite3"
        self.relay = Relay()
        self.current = snapshot("00000000-0000-0000-0000-000000000099")
        self.old = snapshot("00000000-0000-0000-0000-000000000098", {
            f"00000000-0000-0000-0000-{n:012d}": [dict(channel=channel, root=root * 64)]
            for n, channel, root in [(1, CHANNELS[0], "1"), (2, CHANNELS[0], "2"),
                                     (3, CHANNELS[1], "3"), (4, CHANNELS[1], "4")]})

    def tearDown(self):
        self.tmp.cleanup()

    def run_recovery(self, *old, ready=True):
        with RecoveryStore(self.db) as store:
            return recover(store, self.relay, agent_name="test-dev", agent_pubkey=AGENT,
                           relay="https://relay.invalid", current=self.current,
                           snapshots=list(old or [self.old]), runtime_verified=ready)

    def continues_all_four_threads_without_manual_sends(self):
        result = self.run_recovery()
        self.assertEqual(result["continued"], 4)
        messages = [e for e in self.relay.events.values() if e["content"] == "@test-dev continue"]
        self.assertEqual(len(messages), 4)
        # continued_events is the controller's own durable record of exactly
        # which continue ids this round produced (see !1043 code review P1).
        self.assertEqual(sorted(result["continued_events"]), sorted(e["id"] for e in messages))
        for e in messages:
            self.assertEqual([t for t in e["tags"] if t[0] == "p"], [["p", AGENT]])
            self.assertEqual(len([t for t in e["tags"] if t[0] == "h"]), 1)
            self.assertEqual([t[3] for t in e["tags"] if t[0] == "e"], ["root", "reply"])
        self.assertEqual(len(self.relay.events), 8)  # startup + continue per Thread

    test_planned_restart = continues_all_four_threads_without_manual_sends
    test_crash_or_power_loss_without_shutdown_notice = continues_all_four_threads_without_manual_sends

    def test_native_current_source_schema_can_recover_uncancelled_work(self):
        """Both snapshots at the schema this controller actually supports (not a stale pinned number)."""
        self.old["version"] = self.current["version"] = JOURNAL_VERSION
        self.assertEqual(self.run_recovery()["continued"], 4)

    def test_pre_signal_protocol_is_not_silently_accepted(self):
        self.current["version"] = 3
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertEqual(self.relay.published, [])

    def test_cancelled_receipt_closes_the_original_delivery_without_replay(self):
        self.run_recovery()
        dead = self.admitted_snapshot("cancelled")
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        self.assertEqual(self.run_recovery(self.old, dead), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(len(self.relay.events), 8)

    def test_active_execution_cannot_also_have_a_cancellation_tombstone(self):
        self.old["cancelled_turns"] = [next(iter(self.old["active"]))]
        result = self.run_recovery()
        self.assertTrue(result["errors"])
        self.assertEqual(result["continued"], 0)
        self.assertEqual(self.relay.published, [])

    def test_malformed_generation_cannot_hide_a_handoff_and_replay_other_history(self):
        malformed = snapshot("00000000-0000-0000-0000-000000000097")
        malformed["cancelled_turns"] = ["not-a-turn-id"]
        result = self.run_recovery(self.old, malformed)
        self.assertTrue(result["errors"])
        self.assertEqual(result["continued"], 0)
        self.assertEqual(self.relay.published, [])

    def test_second_poll_and_controller_restart_do_not_duplicate(self):
        self.run_recovery()
        before = list(self.relay.published)
        self.assertEqual(self.run_recovery()["continued"], 0)
        self.assertEqual(self.relay.published, before)

    def test_unknown_publish_result_reads_back_same_event_after_reopening_database(self):
        self.relay.lose_ack = True
        first = self.run_recovery()
        self.assertTrue(first["errors"])
        self.run_recovery()
        self.assertEqual(len(self.relay.published), len(set(self.relay.published)))
        self.assertEqual(sum(e["content"] == "@test-dev continue" for e in self.relay.events.values()), 4)

    def test_not_ready_or_wrong_binary_proof_never_continues(self):
        self.assertEqual(self.run_recovery(ready=False)["continued"], 0)
        self.assertEqual(len(self.relay.events), 4)
        self.assertTrue(all("启动状态尚未核实" in e["content"] for e in self.relay.events.values()))
        self.current["phase"] = "starting"
        self.assertEqual(self.run_recovery()["continued"], 0)
        self.assertEqual(len(self.relay.events), 4)

    def test_verified_route_policy_block_is_visible_once_without_a_mention(self):
        def blocked(*args):
            raise RouteBlocked("agent_not_member", "Agent 已不在群里，请 owner 确认是否重新邀请。", notice_allowed=True)
        self.relay.validate_route = blocked
        self.assertEqual(self.run_recovery()["continued"], 0)
        self.assertEqual(len(self.relay.events), 4)
        self.assertTrue(all("重新邀请" in e["content"] and not any(t[0] == "p" for t in e["tags"])
                            for e in self.relay.events.values()))
        self.run_recovery()
        self.assertEqual(len(self.relay.events), 4)

    def test_unverified_access_never_leaks_a_thread_notice(self):
        def blocked(*args):
            raise RouteBlocked("owner_not_member", "owner 无权访问。", notice_allowed=False)
        self.relay.validate_route = blocked
        self.assertTrue(self.run_recovery()["errors"])
        self.assertEqual(self.relay.events, {})

    def test_live_generation_never_replayed(self):
        self.assertEqual(self.run_recovery(self.current)["continued"], 0)

    def test_completed_and_cancelled_are_absent_from_active_and_never_replayed(self):
        self.old["active"] = {}
        self.old["triggers"] = {}
        self.old["receipts"] = dict.fromkeys(self.old["receipts"], "completed")
        self.assertEqual(self.run_recovery(), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(self.relay.published, [])

    def test_repeated_turns_same_thread_coalesce_once(self):
        route = next(iter(self.old["active"].values()))
        self.old["active"]["00000000-0000-0000-0000-000000000010"] = route
        seed_originals(self.old)
        self.assertEqual(self.run_recovery()["continued"], 4)

    def test_wrong_identity_or_community_cannot_authorize_recovery(self):
        for key, value in [("agent_pubkey", "b" * 64), ("relay", "https://other.invalid")]:
            bad = copy.deepcopy(self.old)
            bad[key] = value
            with self.subTest(key=key):
                self.assertTrue(self.run_recovery(bad)["errors"])
                self.assertEqual(self.relay.published, [])

    def test_one_blocked_channel_does_not_prevent_other_channel_and_is_visible(self):
        self.current["channels"] = [CHANNELS[0]]
        result = self.run_recovery()
        self.assertEqual(result["continued"], 2)
        self.assertEqual(len(result["errors"]), 2)
        notices = [e for e in self.relay.events.values() if "尚未订阅" in e["content"]]
        self.assertEqual(len(notices), 2)
        self.assertTrue(all(not any(t[0] == "p" for t in e["tags"]) for e in notices))
        before = len(self.relay.events)
        self.run_recovery()
        self.assertEqual(len(self.relay.events), before)
        self.current["channels"] = CHANNELS[:]
        self.assertEqual(self.run_recovery()["continued"], 2)

    def test_second_agent_restart_recovers_new_interruption_in_same_thread(self):
        self.run_recovery()
        interrupted = self.admitted_snapshot("completed")
        interrupted["active"] = {"00000000-0000-0000-0000-000000000020": [dict(channel=CHANNELS[0], root="1" * 64)]}
        seed_originals(interrupted)
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        self.assertEqual(self.run_recovery(self.old, interrupted)["continued"], 1)

    def test_crash_after_publish_before_admission_redelivers_to_next_generation(self):
        self.run_recovery()
        dead = copy.deepcopy(self.current)
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        self.assertEqual(self.run_recovery(self.old, dead)["continued"], 4)
        messages = [e for e in self.relay.events.values() if e["content"] == "@test-dev continue"]
        self.assertEqual(len(messages), 8)
        self.assertEqual(len({e["id"] for e in messages}), 8)

    def test_runtime_completed_receipt_suppresses_replay_on_later_restart(self):
        self.run_recovery()
        dead = self.admitted_snapshot("completed")
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        self.assertEqual(self.run_recovery(self.old, dead), {"continued": 0, "errors": [], "continued_events": []})
        self.assertEqual(len(self.relay.events), 8)

    def test_admitted_but_interrupted_receipt_transfers_to_new_runtime_task(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        self.assertEqual(self.run_recovery(self.old, dead)["continued"], 4)
        self.assertEqual(self.run_recovery(self.old, dead)["continued"], 0)

    def admitted_snapshot(self, status="active"):
        dead = copy.deepcopy(self.current)
        dead["active"] = copy.deepcopy(self.old["active"])
        messages = [event for event in self.relay.events.values() if event["content"] == "@test-dev continue"]
        dead["receipts"] = {event["id"]: status for event in messages}
        dead["recovery_receipts"] = sorted(dead["receipts"])
        dead["recovery_attempts"] = {e["id"]: tag[1] for e in messages for tag in e["tags"] if tag[0] == "recovery"}
        sources = {s["event_id"]: s for values in self.old["input_sources"].values() for s in values}
        dead["input_sources"] = {e["id"]: [copy.deepcopy(sources[work]) for tag in e["tags"]
                                         if tag[0] == "recovery" for work in tag[3:]] for e in messages}
        dead["triggers"] = {
            turn: [event["id"] for event in messages
                   if ["h", routes[0]["channel"]] in event["tags"]
                   and ["e", routes[0]["root"], "", "root"] in event["tags"]]
            for turn, routes in dead["active"].items()}
        if status != "active":
            dead["active"], dead["triggers"] = {}, {}
        return dead

    def assert_inconsistent_receipt_keeps_original_responsibility(self, dead):
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        published = list(self.relay.published)
        self.assertEqual(self.run_recovery(self.old, dead)["errors"], ["invalid_runtime_snapshot"])
        self.assertEqual(self.relay.published, published)
        with RecoveryStore(self.db) as store:
            self.assertEqual(store.db.execute("SELECT count(*) FROM covered").fetchone()[0], 0,
                             "even an earlier valid row must not be covered before all receipt bindings validate")

    def test_orphan_active_receipt_does_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        dead["active"] = {}
        dead["triggers"] = {}
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_missing_trigger_link_does_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        dead["triggers"] = {}
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_wrong_thread_receipt_does_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        last_turn = list(dead["active"])[-1]
        dead["active"][last_turn][0]["root"] = "e" * 64
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_wrong_channel_receipt_does_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        last_turn = list(dead["active"])[-1]
        dead["active"][last_turn][0]["channel"] = CHANNELS[0]
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_ambiguous_receipt_owners_do_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        first_turn = next(iter(dead["active"]))
        extra = "00000000-0000-0000-0000-000000000050"
        dead["active"][extra] = copy.deepcopy(dead["active"][first_turn])
        dead["triggers"][extra] = dead["triggers"][first_turn][:]
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_terminal_receipt_with_active_owner_does_not_discharge_original_work(self):
        self.run_recovery()
        dead = self.admitted_snapshot()
        last_event = list(dead["receipts"])[-1]
        dead["receipts"][last_event] = "completed"
        self.assert_inconsistent_receipt_keeps_original_responsibility(dead)

    def test_unrelated_generation_receipt_cannot_acknowledge_delivery(self):
        self.run_recovery()
        unrelated = self.admitted_snapshot("completed")
        unrelated["generation"] = "00000000-0000-0000-0000-000000000097"
        # Keep the foreign snapshot internally valid so this still exercises
        # the independent persisted delivery-target fence, not digest parsing.
        for input_id, sources in unrelated["input_sources"].items():
            route = sources[0]["route"]
            unrelated["recovery_attempts"][input_id] = attempt_id(
                unrelated["relay"], AGENT, unrelated["generation"], route["channel"], route["root"],
                [source["event_id"] for source in sources])
        self.current["generation"] = "00000000-0000-0000-0000-000000000100"
        before = list(self.relay.published)
        with self.assertRaisesRegex(ValueError, "recovery_receipt_inconsistent"):
            self.run_recovery(self.old, unrelated)
        self.assertEqual(self.relay.published, before)

    def test_readback_mismatch_is_not_acknowledged(self):
        original = self.relay.lookup
        self.relay.lookup = lambda event_id: ({**original(event_id), "content": "tampered"}
                                             if original(event_id) else None)
        result = self.run_recovery()
        self.assertEqual(result["continued"], 0)
        self.assertTrue(result["errors"])

    def test_malformed_thread_never_falls_back_to_channel_top_level(self):
        self.old["active"] = {"00000000-0000-0000-0000-000000000021": [dict(channel=CHANNELS[0], root="")]}
        self.assertTrue(self.run_recovery()["errors"])
        self.assertEqual(self.relay.published, [])


if __name__ == "__main__":
    unittest.main()
