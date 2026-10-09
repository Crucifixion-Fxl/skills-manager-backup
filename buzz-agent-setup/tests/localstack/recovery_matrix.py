"""Pure L3 matrix oracles (REC-005/006/007/008/015/019 and installed-timer).

Inputs are plain observations read back from the real relay, native journals,
the external ACP double's logs and the user manager; no process is touched here.
Every function raises AssertionError on the failure it exists to catch.

A slot "view" is ``dict(events=[relay kind-9 events in the original Thread],
checkpoint=<checkpoint file text>, dispatches=[ACP double dispatch records])``.
"""
from collections import Counter, defaultdict

CONTROL_CHECKPOINTS = {"completed": "A\nB\n", "cancelled": "A\n", "waiting-human": "A\n", "waiting-approval": "A\n"}
CONTROL_PROMPTS = {"waiting-human": "QUESTION ", "waiting-approval": "APPROVAL-REQUEST "}


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def continues(view, owner, label):
    """Owner-signed visible automatic continues in one original Thread."""
    return [e for e in view["events"] if e["pubkey"] == owner and e["content"] == "@" + label + " continue"]


def recovery_binding(event):
    tags = [t for t in event["tags"] if t[0] == "recovery"]
    _check(len(tags) == 1 and len(tags[0]) >= 4, "continue " + event["id"] + " lacks one exact recovery binding")
    return dict(attempt=tags[0][1], generation=tags[0][2], works=list(tags[0][3:]))


def by_generation(slot, view, owner, label):
    """{target generation: continue event}; exactly one per (root, generation)."""
    grouped = defaultdict(list)
    for event in continues(view, owner, label):
        grouped[recovery_binding(event)["generation"]].append(event)
    duplicated = {generation: [e["id"] for e in events] for generation, events in grouped.items() if len(events) != 1}
    _check(not duplicated, f"{slot}: more than one continue for one (root, generation): {duplicated}")
    return {generation: events[0] for generation, events in grouped.items()}


def resumes(view):
    """ACP executions that continued durable checkpoint A (the original run starts from empty)."""
    return [d for d in view["dispatches"] if d.get("phase", "start") == "start" and d["checkpoint_before"] == "A\n"]


def done_count(slot, view):
    return sum(1 for e in view["events"] if e["content"] == "DONE " + slot)


def completed_once(slot, view, resumed=1):
    _check(view["checkpoint"] == "A\nB\n", f"{slot}: checkpoint {view['checkpoint']!r} is not exactly A,B")
    _check(len(resumes(view)) == resumed, f"{slot}: {len(resumes(view))} resumed executions, expected {resumed}")
    _check(done_count(slot, view) == 1, f"{slot}: DONE must appear exactly once in the original Thread")


def timer_only(*, invocations, receipts, missed, consumed, direct_calls, manual_continue_sent, continues,
               min_ticks=2, commands=(), service=None):
    """Recovery came only from timer-triggered invocations of the test unit.

    tick:last holds only the latest receipt; a tick longer than the timer
    period can overwrite its predecessor's receipt before readback. Such a
    gap is recorded (``missed``) and must be followed by a later receipt of
    the same unit; it is never filled in. Timer-only activation is proven by
    the manager commands: the test service itself is never started.

    A continue is attributed to an invocation by identity, not only by time
    window: each receipt's ``continued_events`` is the controller's own
    durable record (from ``recovery_tick.complete_tick``) of exactly which
    continue event ids that invocation produced. A same-window manual/bypass
    continue cannot pass as timer evidence merely because ``direct_calls``
    and ``manual_continue_sent`` happen to (wrongly) still read clean; its
    id will not appear in any receipt's ``continued_events``. This identity
    check only applies to invocations whose receipt survived readback: a
    ``missed`` invocation's own receipt was overwritten before readback (see
    above), so it keeps relying on the window plus stream-continuity proof.
    """
    _check(direct_calls == 0, "controller was invoked directly, not by the installed timer")
    _check(manual_continue_sent is False, "a manual continue was sent")
    for command in commands:
        _check(not (service in command and {"start", "restart", "reload-or-restart", "try-restart"} & set(command)),
               f"the test service was started directly by the harness: {command}")
    _check(len(consumed) == len(set(consumed)), "timer invocation IDs must be distinct; one was consumed twice")
    _check(len(receipts) >= min_ticks, f"only {len(receipts)} timer tick receipts observed; need {min_ticks}")
    for key, receipt in receipts.items():
        _check(receipt.get("origin") == "systemd" and receipt.get("invocation_id") == key,
               f"tick {key} is not a systemd-origin receipt of that invocation")
        _check(receipt.get("phase") == "completed", f"tick {key} receipt did not complete")
        _check(receipt.get("ok") is True, f"tick {key} receipt completed but ok is not true")
        _check(key in invocations and invocations[key].get("start") is not None
               and invocations[key].get("end") is not None,
               f"tick {key} does not belong to the test unit's journal")
    for key in consumed:
        _check(key in invocations, f"consumed invocation {key} is not in the test unit's journal")
    last_receipt = max(invocations[key]["start"] for key in receipts)
    for key in missed:
        _check(key in invocations and key not in receipts, f"missed receipt {key} is not an unreceipted unit invocation")
        _check(invocations[key]["start"] < last_receipt,
               f"no later durable receipt follows the overwritten tick {key}")
    attributed = 0
    for event in continues:
        owners = [key for key, value in invocations.items() if value.get("start") is not None
                  and value.get("end") is not None and value["start"] - 1 <= event["created_at"] <= value["end"] + 1]
        _check(owners, f"continue {event['id']} was published outside any timer invocation")
        _check(any(key in receipts or key in missed for key in owners),
               f"continue {event['id']} came from an invocation without a durable systemd tick receipt")
        # Time-window membership only bounds which invocation to trust; it
        # cannot tell a genuine timer-produced continue from a same-window
        # manual/bypass one whose flags happen to (wrongly) still read
        # direct_calls=0/manual_continue_sent=False. Where a receipt survived
        # readback, the controller's own durable record of which event ids it
        # produced this invocation is the actual proof of causation.
        recorded_owners = [key for key in owners if key in receipts]
        if recorded_owners:
            _check(any(event["id"] in receipts[key].get("continued_events", ()) for key in recorded_owners),
                   f"continue {event['id']} is not recorded as produced by any owning invocation's durable receipt")
        attributed += 1
    return dict(timer_invocations_with_receipts=len(receipts), missed_receipts=len(missed),
                continues_attributed=attributed, direct_calls=0, manual_continue_sent=False)


def controls_untouched(*, controls, before, after, prompts, checkpoints, owner, label, ticks_after):
    """REC-005: finished/cancelled/waiting Threads are never woken, re-run or answered."""
    _check(ticks_after >= 2, f"controls observed across {ticks_after} full ticks; need at least two")
    missing = set(CONTROL_CHECKPOINTS) - set(controls.values())
    _check(not missing, "missing control kinds: " + ", ".join(sorted(missing)))
    for slot, kind in controls.items():
        view = dict(events=after[slot])
        _check(not continues(view, owner, label), f"{slot}: automatic continue in a {kind} control Thread")
        known = {e["id"] for e in before[slot]}
        new = [e["id"] for e in after[slot] if e["id"] not in known]
        _check(not new, f"{slot}: new Thread activity after restart: {new}")
        _check(prompts[slot] == 1, f"{slot}: control executed {prompts[slot]} times")
        _check(checkpoints[slot] == CONTROL_CHECKPOINTS[kind], f"{slot}: checkpoint changed to {checkpoints[slot]!r}")
        if kind in CONTROL_PROMPTS:
            last = [e for e in after[slot] if e["pubkey"] != owner][-1:]
            _check(last and last[0]["content"].startswith(CONTROL_PROMPTS[kind]),
                   f"{slot}: waiting request is no longer the Agent's last word")
    return dict(controls=len(controls), ticks_after=ticks_after)


def lost_ack(*, dropped, slots, target, owner, label):
    """REC-006: an accepted publication whose ACK was lost is read back, never re-signed."""
    _check(dropped, "the lost-ACK fault never fired; nothing was published through the proxy")
    for record in dropped:
        _check(record.get("upstream_status") == 200 and record.get("accepted") is True,
               f"dropped {record.get('event_id')} did not publish successfully upstream")
    for slot, view in slots.items():
        continued = by_generation(slot, view, owner, label)
        _check(len(continued) == 1, f"{slot}: expected one target generation, saw {sorted(continued)}")
        completed_once(slot, view)
    event = next(iter(by_generation(target, slots[target], owner, label).values()))
    _check({record["event_id"] for record in dropped} == {event["id"]},
           f"{target}: continue {event['id']} is not the exact event read back after the lost ACK")
    return dict(read_back_event=event["id"], threads=len(slots))


def crash_before_admission(*, slots, owner, label, stale_generation, final_generation, journals):
    """REC-007: delivered-not-admitted work gets exactly one new-generation attempt; the old one is fenced."""
    stale, final = journals[stale_generation], journals[final_generation]
    for slot, view in slots.items():
        continued = by_generation(slot, view, owner, label)
        _check(set(continued) == {stale_generation, final_generation},
               f"{slot}: expected one continue for each of the two generations, saw {sorted(continued)}")
        old, new = continued[stale_generation]["id"], continued[final_generation]["id"]
        _check(old not in stale["receipts"], f"{slot}: precondition broken, the first continue was admitted")
        _check(old not in final["receipts"] and old not in final.get("recovery_receipts", []),
               f"{slot}: late old-generation continue {old} was admitted by the new generation")
        _check(final["receipts"].get(new) == "completed", f"{slot}: new-generation attempt {new} did not complete")
        completed_once(slot, view)
    return dict(threads=len(slots), stale_generation=stale_generation, final_generation=final_generation)


def crash_after_admission(*, slots, owner, label, admitted_generation, final_generation, journals):
    """REC-008: the new journal takes over admitted work without a parallel duplicate dispatch."""
    admitted, final = journals[admitted_generation], journals[final_generation]
    pids = {admitted["pid"]: admitted_generation, final["pid"]: final_generation}
    for slot, view in slots.items():
        continued = by_generation(slot, view, owner, label)
        _check(set(continued) == {admitted_generation, final_generation},
               f"{slot}: expected one continue per generation, saw {sorted(continued)}")
        _check(admitted["receipts"].get(continued[admitted_generation]["id"]) == "active",
               f"{slot}: precondition broken, the first continue was not admitted and running")
        _check(final["receipts"].get(continued[final_generation]["id"]) == "completed",
               f"{slot}: the taking-over attempt did not complete")
        runs = Counter(pids.get(d["native_pid"], "unknown") for d in resumes(view))
        _check(runs == Counter({admitted_generation: 1, final_generation: 1}),
               f"{slot}: resumed executions per generation {dict(runs)}; parallel or duplicate dispatch")
        completed_once(slot, view, resumed=2)
    return dict(threads=len(slots), admitted_generation=admitted_generation, final_generation=final_generation)


def cancel_interleave(*, cancelled, recovered, owner, label, final_generation, journals):
    """REC-015: cancelled work is never revived; un-cancelled queued work still recovers."""
    dead = {work for view in cancelled.values() for work in view["work"]}
    everything = list(cancelled.items()) + list(recovered.items())
    for slot, view in everything:
        for event in continues(view, owner, label):
            revived = dead & set(recovery_binding(event)["works"])
            _check(not revived, f"{slot}: continue {event['id']} revived cancelled work {sorted(revived)}")
    for slot, view in cancelled.items():
        # The same Thread may legitimately continue NEW post-cancel work; only
        # a continue that binds cancelled work (checked above) is a revival.
        _check(len(view["dispatches"]) == 1, f"{slot}: cancelled work executed again")
        _check(view["checkpoint"] == "A\n" and done_count(slot, view) == 0, f"{slot}: cancelled work progressed")
    for slot, view in recovered.items():
        continued = by_generation(slot, view, owner, label)
        _check(set(continued) == {final_generation}, f"{slot}: expected one continue for {final_generation}")
        event = continued[final_generation]
        _check(sorted(recovery_binding(event)["works"]) == sorted(view["work"]),
               f"{slot}: recovered work set {recovery_binding(event)['works']} != un-cancelled {view['work']}")
        _check(journals[final_generation]["receipts"].get(event["id"]) == "completed", f"{slot}: not completed")
        completed_once(slot, view)
    return dict(cancelled_never_revived=len(cancelled), recovered=len(recovered))


def stuck_rotation(*, ticks, recoverable, blocked, hung, owner, label, max_ticks, ticks_to_recover=None):
    """REC-019: bounded rotation; stuck targets neither starve others nor spam notices."""
    _check(len(ticks) >= 4, f"only {len(ticks)} consecutive ticks observed")
    over = [tick for tick in ticks if not 0 <= tick["scheduled"] <= 2 or tick["continued"] > tick["scheduled"]]
    _check(not over, f"per-Agent tick bound exceeded: {over}")
    needed = ticks_to_recover if ticks_to_recover is not None else len(ticks)
    for slot, view in recoverable.items():
        continued = by_generation(slot, view, owner, label)
        _check(len(continued) == 1, f"{slot}: starved or duplicated, continues {sorted(continued)}")
        completed_once(slot, view)
    _check(needed <= max_ticks, f"recoverable targets needed {needed} ticks, bound {max_ticks}")
    for slot, view in blocked.items():
        _check(not continues(view, owner, label), f"{slot}: blocked route was continued")
        notices = [e for e in view["events"] if e["pubkey"] == owner and e["content"] != "@" + label + " continue"
                   and "owner" in e["content"] and not e["content"].startswith("@")]
        _check(len(notices) == 1, f"{slot}: {len(notices)} failure notices over {len(ticks)} ticks; expected one")
        _check(len(view["dispatches"]) == 1, f"{slot}: blocked work executed again")
    for slot, view in hung.items():
        _check(len(continues(view, owner, label)) == 1, f"{slot}: stuck target was re-sent a continue")
        _check(len(resumes(view)) == 1, f"{slot}: stuck target dispatched in parallel")
    return dict(recovered=len(recoverable), ticks=len(ticks), ticks_to_recover=needed,
                blocked=len(blocked), hung=len(hung))
