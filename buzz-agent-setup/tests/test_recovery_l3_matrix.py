"""L1 regression of the L3 matrix oracles; these doubles never count as an L3 pass.

Each positive control is the observation a passing real run produces. Every
negative case is the failure the oracle exists to catch; a permissive oracle
lets it pass and must turn this file red.
"""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "localstack"))
sys.path.insert(0, str(HERE.parent / "scripts"))
SPEC = importlib.util.spec_from_file_location("recovery_matrix", HERE / "localstack/recovery_matrix.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

OWNER, AGENT, OTHER = "0" * 64, "1" * 64, "2" * 64
LABEL = "brl3-test"
CONTINUE = "@" + LABEL + " continue"
G1, G2, G3 = "g-one", "g-two", "g-three"


def event(eid, pubkey, content, created_at=100, tags=()):
    return dict(id=eid, pubkey=pubkey, content=content, created_at=created_at, tags=[list(t) for t in tags])


def resume(eid, generation, works, created_at=100):
    return event(eid, OWNER, CONTINUE, created_at, [("p", AGENT), ("recovery", "attempt-" + eid, generation, *works)])


def dispatch(slot, before, native_pid, t=1.0):
    return dict(slot=slot, phase="start", checkpoint_before=before, native_pid=native_pid, pid=native_pid + 1, t=t)


def view(slot, events, checkpoint="A\nB\n", dispatches=None):
    return dict(events=events, checkpoint=checkpoint,
                dispatches=dispatches if dispatches is not None else [dispatch(slot, "", 11), dispatch(slot, "A\n", 22)])


def done(slot):
    return event("done-" + slot, AGENT, "DONE " + slot)


class TimerOracleTests(unittest.TestCase):
    def good(self):
        invocations = {"a" * 32: dict(start=100.0, end=104.0), "b" * 32: dict(start=115.0, end=118.0),
                       "c" * 32: dict(start=130.0, end=131.0)}
        receipts = {key: dict(origin="systemd", invocation_id=key, phase="completed", ok=True, continued_events=[])
                    for key in invocations}
        receipts["a" * 32]["continued_events"] = ["x"]
        receipts["b" * 32]["continued_events"] = ["y"]
        return dict(invocations=invocations, receipts=receipts, missed=set(), consumed=list(invocations),
                    direct_calls=0, manual_continue_sent=False,
                    continues=[event("x", OWNER, CONTINUE, created_at=103), event("y", OWNER, CONTINUE, created_at=116)])

    def test_L1_REC_TIMER_001_timer_triggered_recovery_positive_control(self):
        summary = M.timer_only(**self.good())
        self.assertEqual(summary["timer_invocations_with_receipts"], 3)
        self.assertEqual(summary["continues_attributed"], 2)

    def test_L1_REC_TIMER_002_rejects_direct_controller_call(self):
        values = self.good()
        values["direct_calls"] = 1
        with self.assertRaisesRegex(AssertionError, "direct"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_003_rejects_cli_origin_receipt(self):
        values = self.good()
        values["receipts"]["b" * 32]["origin"] = "cli"
        with self.assertRaisesRegex(AssertionError, "systemd"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_004_rejects_continue_outside_any_timer_invocation(self):
        values = self.good()
        values["continues"].append(event("z", OWNER, CONTINUE, created_at=108))
        with self.assertRaisesRegex(AssertionError, "outside"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_005_rejects_continue_from_invocation_without_durable_receipt(self):
        values = self.good()
        del values["receipts"]["a" * 32]
        with self.assertRaisesRegex(AssertionError, "receipt"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_009_overwritten_receipt_is_recorded_not_invented(self):
        """A tick longer than the 15 s period overwrites the previous receipt before readback."""
        values = self.good()
        del values["receipts"]["a" * 32]
        values["missed"] = {"a" * 32}
        self.assertEqual(M.timer_only(**values)["missed_receipts"], 1)
        values = self.good()
        del values["receipts"]["c" * 32]
        values["missed"] = {"c" * 32}
        with self.assertRaisesRegex(AssertionError, "later"):
            M.timer_only(**values)  # nothing after the gap proves the receipt stream continued

    def test_L1_REC_TIMER_010_rejects_manager_starting_the_service_directly(self):
        for command in (["start", "buzz-recovery-l3-x.service"], ["restart", "buzz-recovery-l3-x.service"]):
            values = self.good()
            values["commands"] = [["daemon-reload"], ["enable", "--runtime", "--now", "buzz-recovery-l3-x.timer"], command]
            values["service"] = "buzz-recovery-l3-x.service"
            with self.subTest(command=command), self.assertRaisesRegex(AssertionError, "directly"):
                M.timer_only(**values)

    def test_L1_REC_TIMER_011_rejects_completed_but_failed_receipt(self):
        """Code review on !1043 (recovery_timer.py:211 sets exit code from receipt["ok"], but
        the oracle only checked phase=="completed"; a completed-but-failed round must not
        count as valid installed-timer evidence."""
        values = self.good()
        values["receipts"]["b" * 32]["ok"] = False
        with self.assertRaisesRegex(AssertionError, "ok"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_006_rejects_receipt_not_from_the_test_unit_journal(self):
        values = self.good()
        values["receipts"]["d" * 32] = dict(origin="systemd", invocation_id="d" * 32, phase="completed", ok=True)
        with self.assertRaisesRegex(AssertionError, "journal"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_007_rejects_manual_continue_and_reused_invocation(self):
        values = self.good()
        values["manual_continue_sent"] = True
        with self.assertRaisesRegex(AssertionError, "manual"):
            M.timer_only(**values)
        values = self.good()
        values["consumed"].append("a" * 32)
        with self.assertRaisesRegex(AssertionError, "distinct"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_008_requires_at_least_two_ticks(self):
        values = self.good()
        values["receipts"] = {"a" * 32: values["receipts"]["a" * 32]}
        values["continues"] = values["continues"][:1]
        with self.assertRaisesRegex(AssertionError, "tick"):
            M.timer_only(**values)

    def test_L1_REC_TIMER_012_rejects_continue_whose_id_is_missing_from_its_invocations_receipt(self):
        """Code review P1 on !1043: recovery_matrix.py:90-97's old oracle proved
        timer-only activation from a time window plus self-reported
        direct_calls/manual_continue_sent flags alone. A manual/bypass continue
        racing into the same window as a legitimate, receipted timer invocation
        -- with those flags wrongly still reporting clean -- would have passed
        undetected. A continue must now also be recorded, by the controller's
        own durable receipt, as one this specific invocation actually produced."""
        values = self.good()
        # "z" lands inside invocation b's receipted window (115.0-118.0), but
        # b's own durable receipt only ever recorded producing "y", not "z".
        values["continues"].append(event("z", OWNER, CONTINUE, created_at=116))
        with self.assertRaisesRegex(AssertionError, "not recorded"):
            M.timer_only(**values)


class ControlOracleTests(unittest.TestCase):
    KINDS = {"completed-control": "completed", "cancelled-control": "cancelled",
             "waiting-human-control": "waiting-human", "waiting-approval-control": "waiting-approval"}

    def good(self):
        before = {"completed-control": [event("c1", OWNER, "@x task"), done("completed-control")],
                  "cancelled-control": [event("k1", OWNER, "@x task"), event("k2", OWNER, "!cancel")],
                  "waiting-human-control": [event("h1", OWNER, "@x task"),
                                            event("h2", AGENT, "QUESTION waiting-human-control: ok?")],
                  "waiting-approval-control": [event("a1", OWNER, "@x task"),
                                               event("a2", AGENT, "APPROVAL-REQUEST waiting-approval-control: ok?")]}
        return dict(controls=dict(self.KINDS), before=before, after=copy.deepcopy(before),
                    prompts={slot: 1 for slot in self.KINDS},
                    checkpoints={"completed-control": "A\nB\n", "cancelled-control": "A\n",
                                 "waiting-human-control": "A\n", "waiting-approval-control": "A\n"},
                    owner=OWNER, label=LABEL, ticks_after=2)

    def test_L1_REC_005_controls_positive_control(self):
        self.assertEqual(M.controls_untouched(**self.good())["controls"], 4)

    def test_L1_REC_005_rejects_automatic_continue_in_waiting_human_thread(self):
        values = self.good()
        values["after"]["waiting-human-control"].append(resume("r", G2, ["h1"]))
        with self.assertRaisesRegex(AssertionError, "waiting-human-control"):
            M.controls_untouched(**values)

    def test_L1_REC_005_rejects_new_execution_or_reply_in_control(self):
        for slot, change in (("waiting-approval-control", "prompt"), ("completed-control", "reply"),
                             ("cancelled-control", "checkpoint")):
            values = self.good()
            if change == "prompt":
                values["prompts"][slot] = 2
            elif change == "reply":
                values["after"][slot].append(done(slot) | dict(id="again"))
            else:
                values["checkpoints"][slot] = "A\nB\n"
            with self.subTest(slot=slot, change=change), self.assertRaisesRegex(AssertionError, slot):
                M.controls_untouched(**values)

    def test_L1_REC_005_rejects_changed_approval_state(self):
        values = self.good()
        values["after"]["waiting-approval-control"].append(event("ap", OWNER, "/approve"))
        with self.assertRaisesRegex(AssertionError, "waiting-approval-control"):
            M.controls_untouched(**values)

    def test_L1_REC_005_requires_two_full_ticks_and_every_control_kind(self):
        values = self.good()
        values["ticks_after"] = 1
        with self.assertRaisesRegex(AssertionError, "tick"):
            M.controls_untouched(**values)
        values = self.good()
        del values["controls"]["waiting-approval-control"]
        with self.assertRaisesRegex(AssertionError, "waiting-approval"):
            M.controls_untouched(**values)


class LostAckOracleTests(unittest.TestCase):
    def good(self):
        slots = {f"lost-ack-{n}": view(f"lost-ack-{n}", [event(f"o{n}", OWNER, "@x task"), resume(f"c{n}", G2, [f"o{n}"]),
                                                          done(f"lost-ack-{n}")]) for n in range(4)}
        return dict(dropped=[dict(event_id="c0", upstream_status=200, accepted=True)], slots=slots,
                    target="lost-ack-0", owner=OWNER, label=LABEL)

    def test_L1_REC_006_lost_ack_positive_control(self):
        self.assertEqual(M.lost_ack(**self.good())["read_back_event"], "c0")

    def test_L1_REC_006_rejects_two_continues_for_one_root(self):
        values = self.good()
        values["slots"]["lost-ack-0"]["events"].insert(2, resume("c0b", G2, ["o0"]))
        with self.assertRaisesRegex(AssertionError, "lost-ack-0"):
            M.lost_ack(**values)

    def test_L1_REC_006_rejects_resigned_event_instead_of_exact_readback(self):
        values = self.good()
        values["slots"]["lost-ack-0"]["events"][1] = resume("different", G2, ["o0"])
        with self.assertRaisesRegex(AssertionError, "read back"):
            M.lost_ack(**values)

    def test_L1_REC_006_rejects_fault_that_did_not_publish(self):
        for record in ([], [dict(event_id="c0", upstream_status=502, accepted=False)]):
            values = self.good()
            values["dropped"] = record
            with self.subTest(record=record), self.assertRaisesRegex(AssertionError, "publish"):
                M.lost_ack(**values)

    def test_L1_REC_006_rejects_duplicate_execution_and_unaffected_thread_regression(self):
        values = self.good()
        values["slots"]["lost-ack-0"]["dispatches"].append(dispatch("lost-ack-0", "A\n", 22, t=2.0))
        with self.assertRaisesRegex(AssertionError, "lost-ack-0"):
            M.lost_ack(**values)
        values = self.good()
        values["slots"]["lost-ack-3"]["checkpoint"] = "A\n"
        with self.assertRaisesRegex(AssertionError, "lost-ack-3"):
            M.lost_ack(**values)


def journal(generation, pid, receipts, recovery_receipts=()):
    return dict(generation=generation, pid=pid, receipts=dict(receipts), recovery_receipts=list(recovery_receipts))


class CrashBeforeAdmissionOracleTests(unittest.TestCase):
    def good(self):
        slots = {f"rec007-{n}": view(f"rec007-{n}", [event(f"o{n}", OWNER, "@x task"), resume(f"s{n}", G2, [f"o{n}"]),
                                                      resume(f"f{n}", G3, [f"o{n}"]), done(f"rec007-{n}")],
                                     dispatches=[dispatch(f"rec007-{n}", "", 11), dispatch(f"rec007-{n}", "A\n", 33)])
                 for n in range(2)}
        journals = {G2: journal(G2, 22, {}), G3: journal(G3, 33, {f"f{n}": "completed" for n in range(2)},
                                                          [f"f{n}" for n in range(2)])}
        return dict(slots=slots, owner=OWNER, label=LABEL, stale_generation=G2, final_generation=G3, journals=journals)

    def test_L1_REC_007_positive_control(self):
        self.assertEqual(M.crash_before_admission(**self.good())["threads"], 2)

    def test_L1_REC_007_rejects_late_old_generation_event_admitted(self):
        for field in ("receipts", "recovery_receipts"):
            values = self.good()
            if field == "receipts":
                values["journals"][G3]["receipts"]["s0"] = "completed"
            else:
                values["journals"][G3]["recovery_receipts"].append("s0")
            with self.subTest(field=field), self.assertRaisesRegex(AssertionError, "old-generation"):
                M.crash_before_admission(**values)

    def test_L1_REC_007_rejects_precondition_that_was_already_admitted(self):
        values = self.good()
        values["journals"][G2]["receipts"]["s1"] = "active"
        with self.assertRaisesRegex(AssertionError, "admitted"):
            M.crash_before_admission(**values)

    def test_L1_REC_007_rejects_missing_or_extra_new_generation_attempt(self):
        values = self.good()
        values["slots"]["rec007-0"]["events"].insert(3, resume("f0b", G3, ["o0"]))
        with self.assertRaisesRegex(AssertionError, "rec007-0"):
            M.crash_before_admission(**values)
        values = self.good()
        del values["slots"]["rec007-1"]["events"][2]
        with self.assertRaisesRegex(AssertionError, "rec007-1"):
            M.crash_before_admission(**values)

    def test_L1_REC_007_rejects_duplicate_execution_or_missing_done(self):
        values = self.good()
        values["slots"]["rec007-0"]["dispatches"].append(dispatch("rec007-0", "A\n", 22))
        with self.assertRaisesRegex(AssertionError, "rec007-0"):
            M.crash_before_admission(**values)
        values = self.good()
        values["slots"]["rec007-1"]["events"].pop()
        with self.assertRaisesRegex(AssertionError, "rec007-1"):
            M.crash_before_admission(**values)


class CrashAfterAdmissionOracleTests(unittest.TestCase):
    def good(self):
        slots = {f"hang-{n}": view(f"hang-{n}", [event(f"o{n}", OWNER, "@x task"), resume(f"a{n}", G2, [f"o{n}"]),
                                                  resume(f"f{n}", G3, [f"o{n}"]), done(f"hang-{n}")],
                                   dispatches=[dispatch(f"hang-{n}", "", 11), dispatch(f"hang-{n}", "A\n", 22),
                                               dispatch(f"hang-{n}", "A\n", 33)])
                 for n in range(2)}
        journals = {G2: journal(G2, 22, {f"a{n}": "active" for n in range(2)}),
                    G3: journal(G3, 33, {f"f{n}": "completed" for n in range(2)})}
        return dict(slots=slots, owner=OWNER, label=LABEL, admitted_generation=G2, final_generation=G3,
                    journals=journals)

    def test_L1_REC_008_positive_control(self):
        self.assertEqual(M.crash_after_admission(**self.good())["threads"], 2)

    def test_L1_REC_008_rejects_parallel_duplicate_dispatch_in_one_generation(self):
        values = self.good()
        values["slots"]["hang-0"]["dispatches"].append(dispatch("hang-0", "A\n", 33))
        with self.assertRaisesRegex(AssertionError, "hang-0"):
            M.crash_after_admission(**values)

    def test_L1_REC_008_rejects_old_responsibility_redispatched_by_a_second_continue(self):
        values = self.good()
        values["slots"]["hang-1"]["events"].insert(3, resume("f1b", G3, ["o1"]))
        with self.assertRaisesRegex(AssertionError, "hang-1"):
            M.crash_after_admission(**values)

    def test_L1_REC_008_rejects_two_runs_in_the_new_generation_instead_of_a_hand_over(self):
        values = self.good()
        values["slots"]["hang-0"]["dispatches"][1] = dispatch("hang-0", "A\n", 33, t=0.5)
        with self.assertRaisesRegex(AssertionError, "hang-0"):
            M.crash_after_admission(**values)

    def test_L1_REC_008_rejects_unadmitted_precondition_and_repeated_b(self):
        values = self.good()
        values["journals"][G2]["receipts"]["a0"] = "completed"
        with self.assertRaisesRegex(AssertionError, "admitted"):
            M.crash_after_admission(**values)
        values = self.good()
        values["slots"]["hang-1"]["checkpoint"] = "A\nB\nB\n"
        with self.assertRaisesRegex(AssertionError, "hang-1"):
            M.crash_after_admission(**values)


class CancelInterleaveOracleTests(unittest.TestCase):
    """Thread C holds cancelled work and new post-cancel work; Thread Q holds un-cancelled queued work."""

    def good(self):
        thread_c = [event("o", OWNER, "@x task"), event("q", OWNER, "@x again"), event("k", OWNER, "!cancel"),
                    event("p", OWNER, "@x new"), resume("r", G2, ["p"]), done("postcancel-a")]
        cancelled = {"slowcancel-a": view("slowcancel-a", thread_c, checkpoint="A\n",
                                          dispatches=[dispatch("slowcancel-a", "", 11)])}
        cancelled["slowcancel-a"]["work"] = ["o", "q"]
        recovered = {"postcancel-a": view("postcancel-a", thread_c),
                     "queued-b": view("queued-b", [event("b1", OWNER, "@x task"), event("b2", OWNER, "@x more"),
                                                   resume("s", G2, ["b1", "b2"]), done("queued-b")])}
        recovered["postcancel-a"]["work"] = ["p"]
        recovered["queued-b"]["work"] = ["b1", "b2"]
        journals = {G2: journal(G2, 22, {"r": "completed", "s": "completed"})}
        return dict(cancelled=cancelled, recovered=recovered, owner=OWNER, label=LABEL, final_generation=G2,
                    journals=journals)

    def test_L1_REC_015_positive_control(self):
        self.assertEqual(M.cancel_interleave(**self.good())["cancelled_never_revived"], 1)

    def test_L1_REC_015_rejects_revived_cancelled_work(self):
        values = self.good()
        values["recovered"]["postcancel-a"]["events"].insert(5, resume("z", G1, ["q"]))
        with self.assertRaisesRegex(AssertionError, "revived cancelled work"):
            M.cancel_interleave(**values)
        values = self.good()
        values["recovered"]["postcancel-a"]["events"][4] = resume("r", G2, ["p", "o"])
        with self.assertRaisesRegex(AssertionError, "revived cancelled work"):
            M.cancel_interleave(**values)
        values = self.good()
        values["cancelled"]["slowcancel-a"]["dispatches"].append(dispatch("slowcancel-a", "A\n", 22))
        with self.assertRaisesRegex(AssertionError, "slowcancel-a"):
            M.cancel_interleave(**values)

    def test_L1_REC_015_rejects_uncancelled_queued_work_dropped(self):
        values = self.good()
        values["recovered"]["queued-b"]["events"][2] = resume("s", G2, ["b1"])
        with self.assertRaisesRegex(AssertionError, "queued-b"):
            M.cancel_interleave(**values)
        values = self.good()
        values["recovered"]["postcancel-a"]["checkpoint"] = "A\n"
        with self.assertRaisesRegex(AssertionError, "postcancel-a"):
            M.cancel_interleave(**values)


class StuckRotationOracleTests(unittest.TestCase):
    def good(self):
        recoverable = {f"ok-{n}": view(f"ok-{n}", [event(f"o{n}", OWNER, "@x task"), resume(f"c{n}", G2, [f"o{n}"]),
                                                    done(f"ok-{n}")]) for n in range(5)}
        blocked = view("blocked-0", [event("b", OWNER, "@x task"), event("n", OWNER, "Agent 在本群不是机器人角色 owner")],
                       checkpoint="A\n", dispatches=[dispatch("blocked-0", "", 11)])
        hung = view("stuck-0", [event("h", OWNER, "@x task"), resume("hc", G2, ["h"])], checkpoint="A\n",
                    dispatches=[dispatch("stuck-0", "", 11), dispatch("stuck-0", "A\n", 22)])
        ticks = [dict(scheduled=2, continued=2), dict(scheduled=2, continued=2), dict(scheduled=2, continued=2),
                 dict(scheduled=1, continued=0), dict(scheduled=1, continued=0)]
        return dict(ticks=ticks, recoverable=recoverable, blocked={"blocked-0": blocked}, hung={"stuck-0": hung},
                    owner=OWNER, label=LABEL, max_ticks=6)

    def test_L1_REC_019_positive_control(self):
        self.assertEqual(M.stuck_rotation(**self.good())["recovered"], 5)

    def test_L1_REC_019_rejects_unbounded_per_tick_concurrency(self):
        values = self.good()
        values["ticks"][0]["scheduled"] = 3
        with self.assertRaisesRegex(AssertionError, "bound"):
            M.stuck_rotation(**values)

    def test_L1_REC_019_rejects_starved_recoverable_target(self):
        values = self.good()
        values["recoverable"]["ok-4"] = view("ok-4", [event("o4", OWNER, "@x task")], checkpoint="A\n",
                                             dispatches=[dispatch("ok-4", "", 11)])
        with self.assertRaisesRegex(AssertionError, "ok-4"):
            M.stuck_rotation(**values)

    def test_L1_REC_019_rejects_slow_recovery_beyond_bound(self):
        values = self.good()
        values["ticks"] += [dict(scheduled=1, continued=0)] * 4
        values["max_ticks"] = 4
        values["ticks_to_recover"] = 9
        with self.assertRaisesRegex(AssertionError, "bound"):
            M.stuck_rotation(**values)

    def test_L1_REC_019_rejects_repeated_notices_and_resent_stuck_continue(self):
        values = self.good()
        values["blocked"]["blocked-0"]["events"].append(event("n2", OWNER, "Agent 在本群不是机器人角色 owner"))
        with self.assertRaisesRegex(AssertionError, "blocked-0"):
            M.stuck_rotation(**values)
        values = self.good()
        values["hung"]["stuck-0"]["events"].append(resume("hc2", G2, ["h"]))
        with self.assertRaisesRegex(AssertionError, "stuck-0"):
            M.stuck_rotation(**values)
        values = self.good()
        values["blocked"]["blocked-0"]["events"].append(resume("bc", G2, ["b"]))
        with self.assertRaisesRegex(AssertionError, "blocked-0"):
            M.stuck_rotation(**values)


if __name__ == "__main__":
    unittest.main()


TIMER_SPEC = importlib.util.spec_from_file_location("recovery_timer", HERE / "localstack/recovery_timer.py")
T = importlib.util.module_from_spec(TIMER_SPEC)
TIMER_SPEC.loader.exec_module(T)


class InstalledTimerDriverTests(unittest.TestCase):
    """The driver can only observe timer ticks of its own uniquely named test unit."""

    def driver(self, invocations, receipts=(), missed=()):
        timer = T.InstalledTimer("buzz-recovery-l3-abc123def0", release="/r", config_path="/tmp/c.json",
                                 state_dir="/tmp/s", runtime_dir="/tmp")
        timer.installed_at = timer.floor = 100.0
        timer.collect = lambda: invocations
        timer.receipts = {key: dict(ok=True) for key in receipts}
        timer.missed = set(missed)
        return timer

    @staticmethod
    def invocation(key, start, end=None, line='{"agents": {"x": {"errors": [], "retry_pending": 0}}}'):
        return dict(invocation_id=key, start=start, end=end if end is not None else start + 2, stdout=[line])

    def test_real_and_foreign_unit_names_are_refused(self):
        for name in ("buzz-agent-recovery", "buzz-brl3-abcdef", "buzz-recovery-l3-", "buzz-recovery-l3-UPPER1"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                T.InstalledTimer(name, release="/r", config_path="/tmp/c.json", state_dir="/tmp/s", runtime_dir="/tmp")

    def test_ticks_are_consumed_once_in_start_order_after_the_barrier(self):
        found = {"b": self.invocation("b", 130.0), "a": self.invocation("a", 115.0), "old": self.invocation("old", 90.0)}
        timer = self.driver(found, receipts=("a", "b", "old"))
        self.assertEqual(timer.next_tick(timeout=1)[1]["invocation_id"], "a")
        self.assertEqual(timer.next_tick(timeout=1)[1]["invocation_id"], "b")
        with self.assertRaises(T.TimerError):
            timer.next_tick(timeout=1)  # "old" started before the barrier; never replayed
        self.assertEqual(timer.consumed, ["a", "b"])

    def test_unfinished_or_receiptless_invocation_is_not_a_tick(self):
        running = self.invocation("r", 120.0)
        running["end"] = None
        timer = self.driver({"r": running, "n": self.invocation("n", 125.0)}, receipts=("r",))
        with self.assertRaises(T.TimerError):
            timer.next_tick(timeout=1)

    def test_failed_round_is_reported_nonzero_from_its_durable_receipt(self):
        timer = self.driver({"f": self.invocation("f", 120.0)}, receipts=("f",))
        timer.receipts["f"]["ok"] = False
        done, _ = timer.next_tick(timeout=1)
        self.assertEqual(done.returncode, 1)

    def test_barrier_excludes_ticks_that_already_ran_and_catch_up_needs_all_consumed(self):
        found = {"a": self.invocation("a", 115.0), "b": self.invocation("b", 130.0)}
        timer = self.driver(found, receipts=("a", "b"))
        timer._idle = lambda: True
        self.assertFalse(timer.caught_up())
        timer.next_tick(timeout=1)
        self.assertFalse(timer.caught_up(), "an unconsumed finished tick means the schedule is incomplete")
        timer.next_tick(timeout=1)
        self.assertTrue(timer.caught_up())
        found["c"] = self.invocation("c", 150.0)
        timer.floor = 160.0  # barrier(): only ticks starting later are "further" ticks
        self.assertTrue(timer.caught_up())
        with self.assertRaises(T.TimerError):
            timer.next_tick(timeout=1)
