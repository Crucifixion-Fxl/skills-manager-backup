"""REC-006/007/008/015/019 flows for the L3 runner (real Relay, native, systemd).

Each flow only stages faults at real process/network boundaries (SIGKILL,
SIGSTOP of the verified native process, a lost HTTP ACK, a Channel role
change, a cancel racing a slow ACP stop). Recovery itself is always the
controller's own: installed timer ticks, or public CLI ticks in direct mode.
The runner never sends a continue and never edits a journal.
"""
import contextlib
import os
import signal
from pathlib import Path
import time

import recovery_controller
import recovery_matrix as oracle


@contextlib.contextmanager
def frozen(h, snapshot):
    """SIGSTOP exactly the verified native process (pidfd), always SIGCONT on exit."""
    descriptor = os.pidfd_open(snapshot["pid"], 0)
    try:
        assert recovery_controller.process_live(snapshot)
        signal.pidfd_send_signal(descriptor, signal.SIGSTOP, None, 0)
        h.wait_for("native receiver stopped", lambda: (
            Path(f"/proc/{snapshot['pid']}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"))
        yield
    finally:
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGCONT, None, 0)
        except ProcessLookupError:
            pass  # killed while stopped: that is the scenario
        finally:
            os.close(descriptor)


def ticks_until(h, name, predicate, *, max_ticks, collected=None):
    """Consume controller ticks in order until predicate() holds; never more than max_ticks."""
    collected = [] if collected is None else collected
    for n in range(max_ticks):
        # Timer ticks run on their own clock: count every tick that already
        # finished before judging, so the recorded schedule is complete.
        if h.caught_up() and predicate():
            return collected
        code, value = h.tick(f"{name}-{n}")
        collected.append(dict(code=code, **value))
    if not predicate():
        raise AssertionError(f"{name}: not reached within {max_ticks} controller ticks")
    return collected


def settle(h, name, ticks=2):
    """At least two further full ticks after recovery: nothing may repeat."""
    h.barrier()
    return [dict(zip(("code", "value"), h.tick(f"{name}-settle-{n}"))) for n in range(ticks)]


def start(h, slots):
    for n, slot in enumerate(slots):
        h.task(slot, h.channels[n % 2])
    h.wait_for("all first executions wrote A", lambda: all(h.checkpoint(slot) == "A\n" for slot in slots))


def crash(h, before, reason):
    h.hold(reason)
    try:
        return h.replace("crash", before)
    finally:
        h.release()


def total_continued(ticks):
    return sum(tick["continued"] for tick in ticks)


def lost_ack(h):
    """REC-006: publication succeeded but the ACK was lost; next ticks read back the same event."""
    slots = [f"lost-ack-{n}" for n in range(4)]
    start(h, slots)
    h.drop_rule.update(root=h.routes[slots[0]]["id"], budget=1)
    before = h.current()
    after = crash(h, before, "lost-ack-crash")
    ticks = ticks_until(h, "lost-ack", lambda: all(h.checkpoint(s) == "A\nB\n" for s in slots), max_ticks=12)
    h.wait_for("lost-ack receipts complete", lambda: not h.current()["active"])
    settle(h, "lost-ack")
    summary = oracle.lost_ack(dropped=list(h.proxy.dropped), slots={s: h.view(s) for s in slots},
                              target=slots[0], owner=h.owner["pubkey"], label=h.label)
    return dict(scenario="REC-006 lost ACK", generation=after["generation"], ticks=ticks,
                proxy_publications=len(h.proxy.publications), proxy_errors=list(h.proxy.errors), **summary)


def crash_before_admission(h):
    """REC-007: continue delivered to a frozen receiver, then the target crashes again."""
    slots = [f"rec007-{n}" for n in range(4)]
    start(h, slots)
    first = h.current()
    h.hold("rec007-first-crash")
    try:
        stale = h.replace("crash", first)
        with frozen(h, stale):
            h.release()
            delivered = ticks_until(h, "rec007-deliver-frozen", lambda: sum(
                1 for s in slots if stale["generation"] in oracle_generations(h, s)) == 4, max_ticks=8)
            h.hold("rec007-second-crash")
            frozen_journal = h.journals()[stale["generation"]]
            assert not frozen_journal["active"], "frozen receiver admitted work"
            assert all(h.checkpoint(s) == "A\n" for s in slots), "frozen receiver executed work"
            final = h.replace("crash", stale)
    finally:
        h.release()
    recovered = ticks_until(h, "rec007-recover", lambda: all(h.checkpoint(s) == "A\nB\n" for s in slots),
                            max_ticks=12)
    h.wait_for("rec007 receipts complete", lambda: not h.current()["active"])
    settle(h, "rec007")
    summary = oracle.crash_before_admission(slots={s: h.view(s) for s in slots}, owner=h.owner["pubkey"],
                                            label=h.label, stale_generation=stale["generation"],
                                            final_generation=final["generation"], journals=h.journals())
    stale_ids = [oracle.by_generation(s, h.view(s), h.owner["pubkey"], h.label)[stale["generation"]]["id"]
                 for s in slots]
    return dict(scenario="REC-007 crash before admission", delivered_ticks=delivered, recovery_ticks=recovered,
                stale_continue_ids=stale_ids, late_event_seen_by_new_generation=h.log_mentions(final, stale_ids),
                **summary)


def oracle_generations(h, slot):
    return set(oracle.by_generation(slot, h.view(slot), h.owner["pubkey"], h.label))


def crash_after_admission(h):
    """REC-008: admitted resumed work is running when the target crashes again."""
    slots = [f"hang-{n}" for n in range(4)]
    start(h, slots)
    first = h.current()
    admitted = crash(h, first, "rec008-first-crash")

    def running():
        state = h.journals().get(admitted["generation"], {})
        bound = [oracle.by_generation(s, h.view(s), h.owner["pubkey"], h.label).get(admitted["generation"])
                 for s in slots]
        return (all(event is not None and state.get("receipts", {}).get(event["id"]) == "active" for event in bound)
                and all(len(oracle.resumes(h.view(s))) == 1 for s in slots))
    delivered = ticks_until(h, "rec008-admit", running, max_ticks=8)
    assert all(h.checkpoint(s) == "A\n" for s in slots)
    final = crash(h, h.current(), "rec008-second-crash")
    recovered = ticks_until(h, "rec008-recover", lambda: all(h.checkpoint(s) == "A\nB\n" for s in slots),
                            max_ticks=12)
    h.wait_for("rec008 receipts complete", lambda: not h.current()["active"])
    settle(h, "rec008")
    summary = oracle.crash_after_admission(slots={s: h.view(s) for s in slots}, owner=h.owner["pubkey"],
                                           label=h.label, admitted_generation=admitted["generation"],
                                           final_generation=final["generation"], journals=h.journals())
    return dict(scenario="REC-008 crash after admission", admit_ticks=delivered, recovery_ticks=recovered, **summary)


def cancel_interleave(h):
    """REC-015: queued work, a cancel whose ACP stop is slow, and new post-cancel work in the same Thread."""
    cancelled, post, queued = "slowcancel-c", "postcancel-c", "queued-q"
    h.task(cancelled, h.channels[0])
    h.task(queued, h.channels[1])
    h.wait_for("both first executions wrote A", lambda: h.checkpoint(cancelled) == "A\n" and h.checkpoint(queued) == "A\n")
    cancelled_follow = h.followup(cancelled, cancelled)
    queued_follow = h.followup(queued, queued)
    h.wait_for("follow-ups durably queued behind running work", lambda: (
        (state := h.current()) and all(state["receipts"].get(e) == "active" for e in (cancelled_follow, queued_follow))))
    assert len(h.view(cancelled)["dispatches"]) == 1 and len(h.view(queued)["dispatches"]) == 1
    h.cancel(cancelled)
    # Inside the double's 3-second slow cancel: new work in the same Thread.
    post_event = h.followup(cancelled, post)
    h.wait_for("post-cancel work started after the cancelled stop", lambda: h.checkpoint(post) == "A\n", timeout=60)
    state = h.current()
    tombstoned = {h.original_inputs[cancelled], cancelled_follow}
    assert all(state["receipts"].get(e) == "cancelled" for e in tombstoned), "cancel was not durably recorded"
    final = crash(h, state, "rec015-crash")
    ticks = ticks_until(h, "rec015-recover", lambda: all(h.checkpoint(s) == "A\nB\n" for s in (post, queued)),
                        max_ticks=12)
    h.wait_for("rec015 receipts complete", lambda: not h.current()["active"])
    settle(h, "rec015")
    views = {s: h.view(s) for s in (cancelled, post, queued)}
    views[cancelled]["work"] = sorted(tombstoned)
    views[post]["work"] = [post_event]
    views[queued]["work"] = sorted([h.original_inputs[queued], queued_follow])
    summary = oracle.cancel_interleave(cancelled={cancelled: views[cancelled]},
                                       recovered={post: views[post], queued: views[queued]},
                                       owner=h.owner["pubkey"], label=h.label, final_generation=final["generation"],
                                       journals=h.journals())
    return dict(scenario="REC-015 cancel vs queued start", recovery_ticks=ticks,
                cancelled_work=sorted(tombstoned), **summary)


def stuck_target(h):
    """REC-019: one blocked route and one never-finishing target beside five recoverable Threads."""
    recoverable = [f"ok-{n}" for n in range(5)]
    blocked, hung = "blocked-0", "stuck-0"
    # Rotation walks routes sorted by (channel, root). Put the blocked route in
    # the lexicographically first Channel so it competes from the first tick.
    first, *others = sorted(h.channels)
    for n, slot in enumerate(recoverable):
        h.task(slot, others[n % 2])
    h.task(hung, others[0])
    h.task(blocked, first)
    everything = recoverable + [hung, blocked]
    h.wait_for("seven first executions wrote A", lambda: all(h.checkpoint(s) == "A\n" for s in everything))
    before = h.current()
    h.hold("rec019-crash")
    try:
        after = h.replace("crash", before)
        h.set_role(first, "member")  # this route stays blocked for the rest of the run
    finally:
        h.release()
    started = time.monotonic()
    ticks = ticks_until(h, "rec019", lambda: all(h.checkpoint(s) == "A\nB\n" for s in recoverable), max_ticks=10)
    needed = len(ticks)
    contended = any("agent_not_bot" in tick["errors"] for tick in ticks)
    assert contended, "the blocked route never competed for a slot before the others recovered"
    h.barrier()
    ticks += [dict(code=code, **value) for code, value in (h.tick(f"rec019-more-{n}") for n in range(max(0, 4 - needed) + 2))]
    summary = oracle.stuck_rotation(ticks=ticks, recoverable={s: h.view(s) for s in recoverable},
                                    blocked={blocked: h.view(blocked)}, hung={hung: h.view(hung)},
                                    owner=h.owner["pubkey"], label=h.label, max_ticks=8, ticks_to_recover=needed)
    return dict(scenario="REC-019 stuck target rotation", generation=after["generation"],
                blocked_contended_before_recovery=contended,
                seconds=round(time.monotonic() - started, 1),
                tick_schedule=[dict(scheduled=t["scheduled"], continued=t["continued"], errors=t["errors"]) for t in ticks],
                **summary)


SCENARIOS = {"lost-ack": lost_ack, "crash-before-admission": crash_before_admission,
             "crash-after-admission": crash_after_admission, "cancel-interleave": cancel_interleave,
             "stuck-target": stuck_target}
