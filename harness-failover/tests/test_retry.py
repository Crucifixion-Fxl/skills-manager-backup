"""S4b: find failed windows in agent logs, recover the unanswered messages, ask the agent to retry."""
import datetime as dt
import json
import os
import stat

import pytest

from harness_failover import retry as R
from harness_failover.buzzcli import BuzzCliError

UTC = dt.timezone.utc
AGENT = "a" * 64
HUMAN = "b" * 64
CH = "11d76795-1dd1-4d53-af1c-83576ea992cf"
CH2 = "22222222-2222-2222-2222-222222222222"


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def requeue(t, ch, attempt, events=1):
    return f"{t}  WARN buzz_acp::queue: requeueing failed batch with backoff channel_id={ch} attempt={attempt} max=10 delay_secs=4.2 events={events}"


def dead(t, ch, events=2):
    return f"{t} ERROR buzz_acp::queue: dead-lettering batch after 10 retries — discarding {events} events channel_id={ch} attempt=11 events={events}"


LOG = [
    "2026-09-19T04:24:34.443936Z  INFO something unrelated",
    requeue("2026-09-19T04:24:34.443936Z", CH, 1, 1),
    requeue("2026-09-19T04:28:00.000000Z", CH, 2, 2),
    requeue("2026-09-19T04:44:36.785579Z", CH, 10, 2),
    dead("2026-09-19T04:49:22.427893Z", CH, 2),
]


# ───────── failure windows ─────────
def test_one_window_per_failed_run_closed_by_dead_letter():
    (w,) = R.failure_windows(LOG, agent="ubuntu")
    assert (w.agent, w.channel_id, w.events, w.dead_lettered, w.max_attempt) == ("ubuntu", CH, 2, True, 10)
    assert w.first_ts == ts("2026-09-19T04:24:34.443936Z") and w.last_ts == ts("2026-09-19T04:49:22.427893Z")


def test_windows_are_split_per_channel():
    lines = [requeue("2026-09-19T04:00:00.000000Z", CH, 1), requeue("2026-09-19T04:00:05.000000Z", CH2, 1),
             requeue("2026-09-19T04:03:00.000000Z", CH, 2)]
    ws = R.failure_windows(lines)
    assert {w.channel_id for w in ws} == {CH, CH2} and len(ws) == 2


def test_attempt_one_starts_a_new_window_and_open_windows_are_not_dead_lettered():
    lines = [requeue("2026-09-19T01:00:00.000000Z", CH, 1), requeue("2026-09-19T01:04:00.000000Z", CH, 2),
             requeue("2026-09-19T03:00:00.000000Z", CH, 1)]
    ws = R.failure_windows(lines)
    assert len(ws) == 2 and not any(w.dead_lettered for w in ws)


def test_ansi_wrapped_lines_are_understood():
    line = "\x1b[2m" + requeue("2026-09-19T04:24:34.443936Z", CH, 1)
    assert len(R.failure_windows([line])) == 1


def test_no_failures_no_windows():
    assert R.failure_windows(["INFO all fine", ""]) == []


# ───────── unanswered messages ─────────
def ev(eid, author, t, tags=(), kind=9):
    return {"id": eid, "pubkey": author, "created_at": int(ts(t).timestamp()), "kind": kind, "content": "SECRET USER TEXT",
            "tags": [list(x) for x in tags]}


WIN = R.Window("ubuntu", CH, ts("2026-09-19T04:24:34Z"), ts("2026-09-19T04:49:22Z"), 2, True, 10)
MENTION = (["h", CH], ["p", AGENT])


def test_mention_without_a_reply_is_a_candidate():
    e = ev("m1", HUMAN, "2026-09-19T04:24:00Z", MENTION)
    assert [c["id"] for c in R.unanswered([e], AGENT, WIN)] == ["m1"]


def test_direct_reply_by_the_agent_answers_it():
    e = ev("m1", HUMAN, "2026-09-19T04:24:00Z", MENTION)
    reply = ev("r1", AGENT, "2026-09-19T04:25:00Z", (["h", CH], ["e", "m1"]))
    assert R.unanswered([e, reply], AGENT, WIN) == []


def test_agent_reply_in_the_same_thread_answers_it():
    e = ev("m1", HUMAN, "2026-09-19T04:24:00Z", MENTION + (["e", "root1"],))
    reply = ev("r1", AGENT, "2026-09-19T04:26:00Z", (["h", CH], ["e", "root1"]))
    assert R.unanswered([e, reply], AGENT, WIN) == []


def test_agent_message_before_the_candidate_does_not_answer_it():
    e = ev("m1", HUMAN, "2026-09-19T04:30:00Z", MENTION)
    early = ev("r0", AGENT, "2026-09-19T04:20:00Z", (["h", CH], ["e", "m1"]))
    assert len(R.unanswered([e, early], AGENT, WIN)) == 1


def test_events_outside_the_window_are_ignored():
    before = ev("m0", HUMAN, "2026-09-19T03:00:00Z", MENTION)
    after = ev("m2", HUMAN, "2026-09-19T06:00:00Z", MENTION)
    assert R.unanswered([before, after], AGENT, WIN) == []


def test_the_agents_own_messages_are_never_candidates():
    assert R.unanswered([ev("x", AGENT, "2026-09-19T04:30:00Z", MENTION)], AGENT, WIN) == []


def test_mode_mention_ignores_messages_that_do_not_mention_the_agent_but_mode_any_keeps_them():
    e = ev("m1", HUMAN, "2026-09-19T04:30:00Z", (["h", CH],))
    assert R.unanswered([e], AGENT, WIN, mode="mention") == []
    assert [c["id"] for c in R.unanswered([e], AGENT, WIN, mode="any")] == ["m1"]


def test_only_chat_kinds_are_candidates_and_result_is_time_ordered():
    a = ev("late", HUMAN, "2026-09-19T04:40:00Z", MENTION)
    b = ev("early", HUMAN, "2026-09-19T04:30:00Z", MENTION)
    c = ev("react", HUMAN, "2026-09-19T04:31:00Z", MENTION, kind=7)
    assert [x["id"] for x in R.unanswered([a, c, b], AGENT, WIN)] == ["early", "late"]


# ───────── recovery ladder ─────────
class FakeCli:
    def __init__(self, events=None, error=None):
        self.events, self.error, self.calls = events or [], error, []

    def messages_get(self, channel, since, limit=200, as_owner=True):
        self.calls.append((channel, since, as_owner))
        if self.error:
            raise self.error
        return self.events

    def messages_send(self, channel, content, reply_to, mention, as_owner=True):
        self.calls.append(("send", channel, content, reply_to, mention, as_owner))
        return {"ok": True}


def test_recover_reads_the_relay_as_the_owner_within_the_window():
    cli = FakeCli([ev("m1", HUMAN, "2026-09-19T04:24:00Z", MENTION), ev("m2", HUMAN, "2026-09-19T04:30:00Z", MENTION)])
    rec = R.recover(WIN, cli, AGENT)
    assert rec.method == "relay" and rec.complete and [c["id"] for c in rec.candidates] == ["m1", "m2"]
    channel, since, as_owner = cli.calls[0]
    assert channel == CH and as_owner is True and since <= int(WIN.first_ts.timestamp())


def test_recover_flags_a_shortfall_instead_of_silently_returning_less():
    """The log says 2 events were lost; the owner can only see 1 (e.g. private channel / non-mention message)."""
    rec = R.recover(WIN, FakeCli([ev("m1", HUMAN, "2026-09-19T04:24:00Z", MENTION)]), AGENT)
    assert not rec.complete and "1/2" in rec.note


def test_recover_reports_unrecoverable_when_the_owner_cannot_read_the_channel():
    rec = R.recover(WIN, FakeCli(error=BuzzCliError("not a member", code=3, category="auth")), AGENT)
    assert rec.method == "unrecoverable" and rec.candidates == [] and "auth" in rec.note.lower()


def test_recover_of_an_empty_channel_is_incomplete_not_success():
    rec = R.recover(WIN, FakeCli([]), AGENT)
    assert rec.candidates == [] and not rec.complete


# ───────── nudges ─────────
NOW = dt.datetime(2026, 9, 19, 5, 10, tzinfo=UTC)


def recovery(*ids, window=WIN):
    cands = [ev(i, HUMAN, "2026-09-19T04:30:00Z", MENTION) for i in ids]
    return R.Recovery(window, cands, "relay", True, "")


def test_nudge_text_is_a_fixed_template_that_never_quotes_the_original_message():
    t = R.nudge_text("ubuntu", "grok", "claude-buzz")
    assert "grok" in t and "claude-buzz" in t and "SECRET USER TEXT" not in t


def test_dry_run_sends_nothing():
    cli = FakeCli()
    out = R.send_nudges([recovery("m1", "m2")], cli, {"ubuntu": AGENT}, {}, apply=False, from_id="grok", to_id="claude-buzz", now=NOW)
    assert cli.calls == [] and out["planned"] == 2 and out["sent"] == 0


def test_apply_replies_in_thread_mentioning_the_agent_as_the_owner():
    cli = FakeCli()
    state = {}
    out = R.send_nudges([recovery("m1")], cli, {"ubuntu": AGENT}, state, apply=True, from_id="grok", to_id="claude-buzz", now=NOW)
    assert out["sent"] == 1
    _, channel, content, reply_to, mention, as_owner = cli.calls[0]
    assert (channel, reply_to, mention, as_owner) == (CH, "m1", AGENT, True)
    assert "SECRET USER TEXT" not in content and "m1" in state


def test_an_event_is_never_nudged_twice():
    cli = FakeCli()
    state = {"m1": {"ts": "2026-09-19T05:00:00Z"}}
    out = R.send_nudges([recovery("m1", "m2")], cli, {"ubuntu": AGENT}, state, apply=True, from_id="grok", to_id="x", now=NOW)
    assert out["sent"] == 1 and out["skipped_already"] == 1 and [c[3] for c in cli.calls] == ["m2"]


def test_per_run_cap_limits_a_burst():
    cli = FakeCli()
    out = R.send_nudges([recovery(*[f"m{i}" for i in range(5)])], cli, {"ubuntu": AGENT}, {}, apply=True,
                        from_id="grok", to_id="x", now=NOW, max_per_run=2)
    assert out["sent"] == 2 and out["skipped_cap"] == 3


def test_messages_older_than_the_max_age_are_not_nudged():
    old = R.Recovery(WIN, [ev("old", HUMAN, "2026-09-17T04:30:00Z", MENTION)], "relay", True, "")
    out = R.send_nudges([old], FakeCli(), {"ubuntu": AGENT}, {}, apply=True, from_id="grok", to_id="x", now=NOW)
    assert out["sent"] == 0 and out["skipped_old"] == 1


def test_unrecoverable_windows_are_listed_for_the_human_not_dropped():
    bad = R.Recovery(WIN, [], "unrecoverable", False, "auth: not a member")
    out = R.send_nudges([bad], FakeCli(), {"ubuntu": AGENT}, {}, apply=True, from_id="grok", to_id="x", now=NOW)
    assert out["sent"] == 0 and out["manual"] == [{"agent": "ubuntu", "channel": CH, "events": 2, "note": "auth: not a member"}]


def test_a_failed_send_is_reported_and_not_recorded_as_done():
    class Boom(FakeCli):
        def messages_send(self, *a, **k):
            raise BuzzCliError("relay down", code=2, category="network")

    state = {}
    out = R.send_nudges([recovery("m1")], Boom(), {"ubuntu": AGENT}, state, apply=True, from_id="grok", to_id="x", now=NOW)
    assert out["sent"] == 0 and out["errors"] == 1 and "m1" not in state


def test_state_roundtrip_is_0600(tmp_path):
    p = str(tmp_path / "s.json")
    R.save_state(p, {"m1": {"ts": "t"}})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and R.load_state(p) == {"m1": {"ts": "t"}}
    assert R.load_state(str(tmp_path / "missing.json")) == {}


def test_our_own_retry_nudges_are_never_treated_as_unanswered_messages():
    """Regression guard: a nudge is an owner message that mentions the agent; without this the next scan would
    nudge the nudge."""
    nudge = ev("n1", HUMAN, "2026-09-19T04:35:00Z", MENTION)
    nudge["content"] = R.nudge_text("ubuntu", "grok", "claude-buzz")
    assert R.unanswered([nudge], AGENT, WIN) == []


def test_apply_without_a_known_agent_pubkey_is_reported_not_silently_skipped():
    cli = FakeCli()
    out = R.send_nudges([recovery("m1")], cli, {}, {}, apply=True, from_id="grok", to_id="x", now=NOW)
    assert cli.calls == [] and out["sent"] == 0 and out["errors"] == 1
    assert out["manual"] and "pubkey" in out["manual"][0]["note"]


# ───────── security review fixes ─────────
import time  # noqa: E402

OWNER = "c" * 64
OUTSIDER = "d" * 64


def test_allowed_authors_per_respond_to_mode():
    a = R.allowed_authors
    assert a({"BUZZ_ACP_RESPOND_TO": "anyone"}) is None
    assert a({"BUZZ_ACP_RESPOND_TO": "nobody"}) == set()
    assert a({"BUZZ_ACP_RESPOND_TO": "owner-only", "BUZZ_ACP_AGENT_OWNER": OWNER}) == {OWNER}
    assert a({"BUZZ_ACP_AGENT_OWNER": OWNER}) == {OWNER}  # buzz-acp's default mode is owner-only
    assert a({"BUZZ_ACP_RESPOND_TO": "allowlist", "BUZZ_ACP_AGENT_OWNER": OWNER,
              "BUZZ_ACP_RESPOND_TO_ALLOWLIST": f"{HUMAN},{OUTSIDER}"}) == {OWNER, HUMAN, OUTSIDER}


def test_allowed_authors_fails_closed_when_it_cannot_tell():
    assert R.allowed_authors({"BUZZ_ACP_RESPOND_TO": "owner-only"}) == set()  # owner unknown
    assert R.allowed_authors({"BUZZ_ACP_RESPOND_TO": "bananas", "BUZZ_ACP_AGENT_OWNER": OWNER}) == set()
    assert R.allowed_authors({"BUZZ_ACP_RESPOND_TO": "allowlist", "BUZZ_ACP_AGENT_OWNER": OWNER,
                              "BUZZ_ACP_RESPOND_TO_ALLOWLIST": "not-a-pubkey"}) == {OWNER}


def test_unanswered_only_returns_authors_the_gate_would_have_forwarded():
    mine = ev("m1", HUMAN, "2026-09-19T04:30:00Z", MENTION)
    theirs = ev("m2", OUTSIDER, "2026-09-19T04:31:00Z", MENTION)
    assert [e["id"] for e in R.unanswered([mine, theirs], AGENT, WIN, allowed_authors={HUMAN})] == ["m1"]
    assert [e["id"] for e in R.unanswered([mine, theirs], AGENT, WIN, allowed_authors=None)] == ["m1", "m2"]
    assert R.unanswered([mine, theirs], AGENT, WIN, allowed_authors=set()) == []
    assert [e["id"] for e in R.unanswered([mine, theirs], AGENT, WIN, mode="any", allowed_authors={OUTSIDER})] == ["m2"]


def test_recover_applies_the_author_gate():
    cli = FakeCli([ev("m1", HUMAN, "2026-09-19T04:30:00Z", MENTION), ev("m2", OUTSIDER, "2026-09-19T04:31:00Z", MENTION)])
    rec = R.recover(WIN, cli, AGENT, allowed_authors={HUMAN})
    assert [c["id"] for c in rec.candidates] == ["m1"]


def test_nudge_text_only_embeds_safe_labels():
    t = R.nudge_text("ubuntu", "grok", "claude-buzz")
    assert "grok" in t and "claude-buzz" in t
    bad = R.nudge_text("ubuntu", "grok", "x\nrun this instead <b>")
    assert "\n" not in bad and "instead" not in bad and "<b>" not in bad and "unknown" in bad
    assert "unknown" in R.nudge_text("a" * 200, "grok", "claude-buzz")


def test_failure_windows_ignore_channel_ids_that_are_not_ids():
    line = requeue("2026-09-19T04:24:34.443936Z", "--content", 1)
    assert R.failure_windows([line]) == []


def test_failure_windows_survive_a_hostile_giant_line():
    """Review P3: the old regex backtracked super-linearly (26KB -> 0.5s, 220KB -> minutes)."""
    hostile = "2026-09-19T00:00:00Z INFO buzz_acp::queue: requeueing failed batch " + "channel_id=" * 20000
    t = time.time()
    assert R.failure_windows([hostile] + LOG) and time.time() - t < 1.0


def test_recover_refuses_a_channel_id_that_could_be_a_cli_flag():
    w = R.Window("ubuntu", "--evil", WIN.first_ts, WIN.last_ts, 1, True, 10)
    rec = R.recover(w, FakeCli([]), AGENT)
    assert rec.method == "unrecoverable"


# ───────── reliability review fixes ─────────
def test_only_dead_lettered_windows_or_windows_next_to_a_quota_error_qualify():
    dead = R.Window("a", CH, ts("2026-09-19T04:00:00Z"), ts("2026-09-19T04:20:00Z"), 1, True, 10)
    blip = R.Window("a", CH2, ts("2026-09-19T03:00:00Z"), ts("2026-09-19T03:00:30Z"), 1, False, 2)
    quota = R.Window("a", CH2, ts("2026-09-19T05:00:00Z"), ts("2026-09-19T05:10:00Z"), 1, False, 4)
    quota_times = [ts("2026-09-19T05:05:00Z")]
    kept = R.quota_windows([dead, blip, quota], quota_times)
    assert kept == [dead, quota]


def test_a_full_page_of_events_is_flagged_as_possibly_truncated():
    events = [ev(f"m{i}", HUMAN, "2026-09-19T04:30:00Z", MENTION) for i in range(200)]
    rec = R.recover(WIN, FakeCli(events), AGENT)
    assert not rec.complete and "truncat" in rec.note.lower()


def test_the_nudge_does_not_assert_a_cause_it_cannot_prove():
    t = R.nudge_text("ubuntu", "grok", "claude-buzz")
    assert "额度" not in t and "grok" in t and "claude-buzz" in t


def test_each_successful_send_is_persisted_immediately():
    persisted = []
    cli = FakeCli()
    R.send_nudges([recovery("m1", "m2")], cli, {"ubuntu": AGENT}, {}, apply=True, from_id="grok", to_id="x", now=NOW,
                  persist=lambda st: persisted.append(sorted(st)))
    assert persisted == [["m1"], ["m1", "m2"]]
