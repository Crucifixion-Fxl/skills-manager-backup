"""Coverage added from the independent test-quality review (mutation testing): behaviours that a broken
implementation could get wrong while every earlier test stayed green.

These are guard tests for behaviour that already existed, not red-first tests; each was checked against a mutant."""
import datetime as dt
import json
import os
import signal
import stat
import subprocess
import time

import pytest

from harness_failover import cli as C
from harness_failover import detect as D
from harness_failover import envfile as E
from harness_failover import profiles as P
from harness_failover import retry as R
from harness_failover import signatures as S
from harness_failover import switch as W
from harness_failover.buzzcli import BuzzCli
from harness_failover.detect import Health

from test_cli import (AGENT_PK, CH, HUMAN_PK, NOW, OWNER_PK, OUTSIDER_PK, FakeBuzz, FakeOps, dead_log, lost_message,
                      make_home, run, state_of, write)

UTC = dt.timezone.utc
SIGS = S.load_signatures()


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ───────────────────────── SystemOps against a fake systemctl and a real /proc ─────────────────────────
@pytest.fixture
def fake_systemd(tmp_path, monkeypatch):
    """A `systemctl` on PATH, plus a real child process whose environ we control (so /proc is genuinely read)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    child = subprocess.Popen(["sleep", "60"], env={
        "BUZZ_ACP_AGENT_COMMAND": "/x/adapter", "BUZZ_ACP_MODEL": "sonnet", "BUZZ_ACP_EFFORT_LEVEL": "medium",
        "CLAUDE_CODE_EXECUTABLE": "/w/claude-buzz", "CODEX_HOME": "/c/home",
        "BUZZ_PRIVATE_KEY": "nsec1THISMUSTNEVERLEAK", "GITLAB_TOKEN": "glpat-NEVERLEAK", "UNRELATED": "y"})
    ctl = bindir / "systemctl"
    ctl.write_text(f"""#!/usr/bin/env bash
echo "$@" >> {log}
case "$*" in
  *"show -p MainPID"*) echo "${{FAKE_MAINPID-{child.pid}}}";;
  *"is-active"*) printf '%s\\n' "${{FAKE_ACTIVE:-active}}";;
  *restart*) exit "${{FAKE_RESTART_RC:-0}}";;
esac
""")
    ctl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    yield log, child
    child.send_signal(signal.SIGKILL)
    child.wait()


def test_system_ops_restart_and_is_active_use_the_right_systemctl_calls(fake_systemd, tmp_path, monkeypatch):
    log, _ = fake_systemd
    ops = C.SystemOps(str(tmp_path))
    assert ops.restart("buzz-local-x.service") == 0
    monkeypatch.setenv("FAKE_RESTART_RC", "5")
    assert ops.restart("buzz-local-x.service") == 5
    monkeypatch.setenv("FAKE_ACTIVE", "inactive")
    assert ops.is_active("buzz-local-x.service") == "inactive"  # stripped, exact
    calls = log.read_text().splitlines()
    assert calls[0] == "--user restart buzz-local-x.service" and "--user is-active buzz-local-x.service" in calls


def test_running_env_reads_proc_but_returns_only_the_non_secret_harness_variables(fake_systemd, tmp_path):
    env = C.SystemOps(str(tmp_path)).running_env("buzz-local-x.service")
    assert env == {"BUZZ_ACP_AGENT_COMMAND": "/x/adapter", "BUZZ_ACP_MODEL": "sonnet", "BUZZ_ACP_EFFORT_LEVEL": "medium",
                   "CLAUDE_CODE_EXECUTABLE": "/w/claude-buzz", "CODEX_HOME": "/c/home"}
    assert "NEVERLEAK" not in json.dumps(env) and "THISMUSTNEVERLEAK" not in json.dumps(env)


@pytest.mark.parametrize("pid", ["0", "", "not-a-pid", "999999999"])
def test_running_env_of_no_process_is_empty_not_an_error(fake_systemd, tmp_path, monkeypatch, pid):
    monkeypatch.setenv("FAKE_MAINPID", pid)
    assert C.SystemOps(str(tmp_path)).running_env("buzz-local-x.service") == {}


def test_running_env_survives_a_hung_systemctl(tmp_path, monkeypatch):
    ops = C.SystemOps(str(tmp_path))
    monkeypatch.setattr(ops, "_run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("systemctl", 15)))
    assert ops.running_env("u") == {}


def test_codex_login_check_passes_codex_home_and_maps_the_outcomes(tmp_path):
    home = tmp_path / "h"
    (home / ".local" / "bin").mkdir(parents=True)
    seen = tmp_path / "seen"
    exe = home / ".local" / "bin" / "codex"

    def script(out="", err="", rc=0):
        exe.write_text(f'#!/usr/bin/env bash\necho "$CODEX_HOME" > {seen}\n'
                       f'{"echo " + repr(out) if out else ":"}\n{"echo " + repr(err) + " >&2" if err else ":"}\nexit {rc}\n')
        exe.chmod(0o755)

    ops = C.SystemOps(str(home))
    script(out="Logged in using ChatGPT")
    assert ops.codex_logged_in("/the/codex/home") is True
    assert seen.read_text().strip() == "/the/codex/home"  # the account home really reaches the child
    script(err="Not logged in", rc=1)
    assert ops.codex_logged_in("/x") is False
    script()
    assert ops.codex_logged_in("/x") is None  # says nothing: never guess
    assert C.SystemOps(str(tmp_path / "nohome")).codex_logged_in("/x") is None  # no codex binary


def test_a_probe_that_times_out_is_unavailable_never_exhausted(tmp_path, monkeypatch):
    ops = C.SystemOps(str(tmp_path))
    monkeypatch.setattr(ops, "_run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 150)))
    prof = {p.id: p for p in P.load_profiles(str(tmp_path))}["claude-buzz"]
    h = ops.probe(prof)
    assert h.status == "unavailable"


def test_a_transient_probe_error_never_triggers_a_switch():
    glm = {p.id: p for p in P.load_profiles("/h")}["glm"]
    body = json.dumps({"subtype": "success", "is_error": True, "result": "Request rejected (429) · [1302][请求过于频繁]"})
    assert C.parse_probe(glm, 1, body, "", SIGS, NOW).status == "unavailable"


def test_codex_ok_output_with_a_failing_exit_code_is_not_success():
    codex = {p.id: p for p in P.load_profiles("/h")}["codex-buzz"]
    assert C.parse_probe(codex, 1, "OK\n", "", SIGS, NOW).status == "unavailable"


# ───────────────────────── BuzzCli: the owner identity at the method boundary ─────────────────────────
def _fake_cli(tmp_path):
    rec = tmp_path / "vars"
    script = tmp_path / "fake-buzz"
    script.write_text(f"#!/usr/bin/env bash\nenv | grep -c '^BUZZ_' > {rec} || true\nprintf '[]'\n")
    script.chmod(0o755)
    return str(script), rec


@pytest.mark.parametrize("call", ["get", "send"])
def test_messages_get_and_send_run_as_the_owner_by_default(tmp_path, call):
    """The nudge must be signed by the owner: neither method may pass the agent's BUZZ_* identity through."""
    path, rec = _fake_cli(tmp_path)
    cli = BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin", "BUZZ_PRIVATE_KEY": "agent-key",
                                                "BUZZ_AUTH_TAG": "t"})
    if call == "get":
        cli.messages_get("CH", 1)
    else:
        cli.messages_send("CH", "x", reply_to="e" * 64, mention="a" * 64)
    assert rec.read_text().strip() == "0"


def test_buzzcli_error_categories_and_timeout(tmp_path):
    from harness_failover.buzzcli import BuzzCliError
    script = tmp_path / "fake"
    script.write_text("#!/usr/bin/env bash\nexit ${RC:-2}\n")
    script.chmod(0o755)
    for rc, cat in ((1, "input"), (2, "network"), (3, "auth"), (4, "other"), (5, "conflict"), (9, "other")):
        script.write_text(f"#!/usr/bin/env bash\nexit {rc}\n")
        with pytest.raises(BuzzCliError) as e:
            BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": str(script), "PATH": "/usr/bin:/bin"}).run(["x"])
        assert (e.value.code, e.value.category) == (rc, cat)
    script.write_text("#!/usr/bin/env bash\nsleep 5\n")
    with pytest.raises(BuzzCliError) as e:
        BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": str(script), "PATH": "/usr/bin:/bin"}).run(["x"], timeout=0.3)
    assert e.value.category == "timeout"


# ───────────────────────── retry: who counts, and where the window ends ─────────────────────────
WIN = R.Window("ubuntu", CH, ts("2026-09-19T04:24:34Z"), ts("2026-09-19T04:49:22Z"), 2, True, 10)
MENTION = (["h", CH], ["p", AGENT_PK])


def ev(eid, author, t, tags=(), kind=9):
    return {"id": eid, "pubkey": author, "created_at": int(ts(t).timestamp()) if isinstance(t, str) else t,
            "kind": kind, "content": "x", "tags": [list(x) for x in tags]}


def test_a_message_that_mentions_a_different_agent_is_not_ours_to_retry():
    other = ev("m1", HUMAN_PK, "2026-09-19T04:30:00Z", (["h", CH], ["p", "e" * 64]))
    assert R.unanswered([other], AGENT_PK, WIN, mode="mention") == []


def test_only_the_agents_own_reply_answers_a_message_not_another_users():
    e = ev("m1", HUMAN_PK, "2026-09-19T04:30:00Z", MENTION)
    human_reply = ev("r1", OUTSIDER_PK, "2026-09-19T04:31:00Z", (["h", CH], ["e", "m1"]))
    assert [x["id"] for x in R.unanswered([e, human_reply], AGENT_PK, WIN)] == ["m1"]


def test_window_edges_are_exact():
    first, last = int(WIN.first_ts.timestamp()), int(WIN.last_ts.timestamp())
    def at(t):
        return ev(f"m{t}", HUMAN_PK, t, MENTION)
    cands = {e["id"] for e in R.unanswered([at(first - 91), at(first - 90), at(first - 89), at(last), at(last + 1)],
                                            AGENT_PK, WIN)}
    assert cands == {f"m{first - 90}", f"m{first - 89}", f"m{last}"}


def test_a_reply_in_the_same_second_still_answers():
    e = ev("m1", HUMAN_PK, "2026-09-19T04:30:00Z", MENTION)
    same = ev("r1", AGENT_PK, "2026-09-19T04:30:00Z", (["h", CH], ["e", "m1"]))
    assert R.unanswered([e, same], AGENT_PK, WIN) == []


@pytest.mark.parametrize("bad", ["--deadbeef", "-abcdef12", "--evil", "", "abc", "x" * 65])
def test_a_channel_id_that_could_be_read_as_a_cli_flag_is_never_used(bad):
    w = R.Window("ubuntu", bad, WIN.first_ts, WIN.last_ts, 1, True, 10)

    class Boom:
        def messages_get(self, *a, **k):
            raise AssertionError("the CLI must not be called with an invalid channel id")

    assert R.recover(w, Boom(), AGENT_PK).method == "unrecoverable"


def test_an_incomplete_recovery_is_listed_for_a_human_even_though_its_candidates_are_nudged():
    rec = R.Recovery(WIN, [ev("m1", HUMAN_PK, "2026-09-19T04:30:00Z", MENTION)], "relay", False, "found 1/2")
    out = R.send_nudges([rec], FakeBuzz(), {"ubuntu": AGENT_PK}, {}, apply=True, from_id="a", to_id="b", now=NOW)
    assert out["sent"] == 1 and out["manual"] == [{"agent": "ubuntu", "channel": CH, "events": 2, "note": "found 1/2"}]


def test_dry_run_never_touches_state_and_the_default_cap_is_ten():
    state = {}
    recs = [R.Recovery(WIN, [ev(f"m{i:02d}", HUMAN_PK, "2026-09-19T04:30:00Z", MENTION) for i in range(15)], "relay", True, "")]
    out = R.send_nudges(recs, FakeBuzz(), {"ubuntu": AGENT_PK}, state, apply=False, from_id="a", to_id="b", now=NOW)
    assert state == {} and out["planned"] == 10 and out["skipped_cap"] == 5


def test_nudge_text_sanitises_every_label_and_keeps_from_before_to():
    t = R.nudge_text("bad name!", "grok", "claude-buzz")
    assert "@unknown" in t and t.index("grok") < t.index("claude-buzz")
    assert "unknown" in R.nudge_text("ubuntu", "bad from!", "claude-buzz")


def test_corrupt_nudged_state_is_treated_as_empty(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert R.load_state(str(p)) == {}


def test_windows_come_back_in_time_order():
    lines = [
        "2026-09-19T05:00:00.000000Z  WARN buzz_acp::queue: requeueing failed batch with backoff channel_id=22222222-2222-2222-2222-222222222222 attempt=1 max=10 delay_secs=4 events=1",
        "2026-09-19T04:00:00.000000Z  WARN buzz_acp::queue: requeueing failed batch with backoff channel_id=11d76795-1dd1-4d53-af1c-83576ea992cf attempt=1 max=10 delay_secs=4 events=1",
    ]
    assert [w.first_ts.hour for w in R.failure_windows(lines)] == [4, 5]


# ───────────────────────── switch: what "loaded the new config" means ─────────────────────────
HOME = "/home/u"
BY = {p.id: p for p in P.load_profiles(HOME)}


def test_running_env_must_match_command_and_effort_too():
    p = BY["claude-buzz"]
    good = {"BUZZ_ACP_AGENT_COMMAND": p.command, "BUZZ_ACP_MODEL": "sonnet", "BUZZ_ACP_EFFORT_LEVEL": "medium",
            "CLAUDE_CODE_EXECUTABLE": p.wrapper}
    assert W.running_env_matches(good, p)
    assert not W.running_env_matches({**good, "BUZZ_ACP_AGENT_COMMAND": "/old/adapter"}, p)
    assert not W.running_env_matches({**good, "BUZZ_ACP_EFFORT_LEVEL": "high"}, p)


def test_restarts_are_staggered_then_one_settle_wait_then_the_checks_and_attribution_is_per_unit(tmp_path):
    from test_switch import Fake
    f = Fake(tmp_path / "order", n=2)
    events = []
    active = {"buzz-local-agent0.service": "active", "buzz-local-agent1.service": "failed"}
    env_bad = {"buzz-local-agent0.service": True}

    def restart(unit):
        events.append(f"restart:{unit}")
        return 0

    def is_active(unit):
        events.append(f"active?:{unit}")
        return active[unit]

    def running_env(unit):
        return {} if env_bad.get(unit) else f.running_env(unit)

    res = W.apply_switch(f.profile, f.agents, launchers=[str(f.launcher)], ts="t", restart=restart, is_active=is_active,
                         running_env=running_env, sleep=lambda s: events.append(f"sleep:{s}"), stagger=3.0, settle=8.0)
    restarts_and_sleeps = [e for e in events if e.startswith(("restart", "sleep"))]
    assert restarts_and_sleeps == ["restart:buzz-local-agent0.service", "sleep:3.0", "restart:buzz-local-agent1.service",
                                   "sleep:3.0", "sleep:8.0"]
    assert {b["name"] for b in res.bad} == {"agent0", "agent1"}  # agent0: stale env, agent1: not active
    assert [a["name"] for a in res.agents if a["active"] == "active"] == ["agent0"]


def test_backups_are_pruned_after_a_switch_but_a_users_own_backup_names_are_left_alone(tmp_path):
    from test_switch import Fake
    f = Fake(tmp_path / "prune", n=1)
    env = f.agents[0]["env"]
    for i in range(7):
        open(f"{env}.bak.2026010{i}-000000-failover", "w").write("old")
    open(f"{env}.bak.mine", "w").write("manual copy")
    f.run()
    failover_backups = [x for x in os.listdir(os.path.dirname(env)) if x.endswith("-failover")]
    assert len(failover_backups) == 5 and os.path.exists(f"{env}.bak.mine")


def test_decide_with_allow_unknown_still_never_picks_an_exhausted_candidate():
    from test_switch import H, PS, health
    h = health(grok="exhausted", **{"claude-buzz": "exhausted", "codex-buzz": "unknown", "glm": "unavailable"})
    assert W.decide("grok", h, PS, allow_unknown=True).to == "codex-buzz"


def test_too_soon_boundary_and_naive_timestamps():
    assert W.too_soon("2026-09-19T04:40:00Z", NOW, minutes=20) is False  # exactly 20 minutes ago: allowed
    assert W.too_soon("2026-09-19T04:40:01Z", NOW, minutes=20) is True
    assert W.too_soon("2026-09-19T04:50:00", NOW) is True  # naive => treated as UTC


# ───────────────────────── envfile ─────────────────────────
def _env(tmp_path, text, name="a.env"):
    p = tmp_path / name
    p.write_text(text)
    os.chmod(p, 0o600)
    return str(p)


def test_untouched_lines_stay_byte_identical_including_spacing_blanks_and_comments():
    text = "# managed by hand\nKEEP =  spaced   \n\nBUZZ_ACP_MODEL='grok-4.6'\nTRAILING=x  \n"
    assert E.rewrite_text(text, {"BUZZ_ACP_MODEL": "sonnet"}) == "# managed by hand\nKEEP =  spaced   \n\nBUZZ_ACP_MODEL=sonnet\nTRAILING=x  \n"


def test_a_backup_we_did_not_create_is_never_deleted_on_failure(tmp_path):
    p = _env(tmp_path, "BUZZ_ACP_MODEL=x\n")
    open(f"{p}.bak.t-failover", "w").write("someone else's backup")
    with pytest.raises(FileExistsError):
        E.apply_updates([p], {"BUZZ_ACP_MODEL": "sonnet"}, "t")
    assert open(f"{p}.bak.t-failover").read() == "someone else's backup"


def test_our_own_backup_is_removed_when_the_replace_fails(tmp_path, monkeypatch):
    p = _env(tmp_path, "BUZZ_ACP_MODEL=x\n")
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("no")) if str(d) == p else real(s, d))
    with pytest.raises(OSError):
        E.apply_updates([p], {"BUZZ_ACP_MODEL": "sonnet"}, "t")
    assert not [f for f in os.listdir(tmp_path) if ".bak." in f or f.startswith(".env.new.")]
    assert open(p).read() == "BUZZ_ACP_MODEL=x\n"


def test_prune_backups_only_touches_failover_backups_of_that_exact_file(tmp_path):
    a, b = _env(tmp_path, "A=1\n", "a.env"), _env(tmp_path, "B=1\n", "b.env")
    for i in range(3):
        open(f"{a}.bak.2026010{i}-failover", "w").write("x")
        open(f"{b}.bak.2026010{i}-failover", "w").write("x")
    open(f"{a}.bak.manual", "w").write("mine")
    E.prune_backups([a], keep=1)
    left = sorted(os.listdir(tmp_path))
    assert "a.env.bak.manual" in left and sum(f.startswith("b.env.bak.") for f in left) == 3
    assert sum(f.startswith("a.env.bak.2026") for f in left) == 1


def test_group_readable_env_files_are_refused_too(tmp_path):
    p = _env(tmp_path, "A=1\n")
    os.chmod(p, 0o640)
    with pytest.raises(E.EnvError):
        E.apply_updates([p], {"A": "2"}, "t")


# ───────────────────────── detect ─────────────────────────
def ev_(t, kind, until=None):
    return (ts(t), kind, ts(until) if until else None, "")


def test_a_quota_error_whose_reset_has_already_passed_is_not_exhausted_even_if_recent():
    """20 minutes old, reset 5 minutes ago: the reset time is authoritative, not the 30-minute fallback."""
    assert D.family_health([ev_("2026-09-19T04:40:00Z", "quota", "2026-09-19T04:55:00Z")], NOW).status == "unknown"


def _billing(pct, cap=0, used=0, prepaid=0, ts_="2026-09-19T04:56:35.000Z", end="2026-09-25T00:00:00+00:00"):
    return json.dumps({"ts": ts_, "msg": "billing: fetched credits config", "ctx": {"config": {
        "creditUsagePercent": pct, "onDemandCap": {"val": cap}, "onDemandUsed": {"val": used},
        "prepaidBalance": {"val": prepaid}, "billingPeriodEnd": end}}})


def _402(t):
    return json.dumps({"ts": t, "msg": "shell.turn.inference_failed", "ctx": {"status_code": 402, "message": "x"}})


def test_grok_thresholds():
    assert D.grok_health([_billing(100.0)], NOW).status == "exhausted"
    assert D.grok_health([_billing(99.9)], NOW).status == "ok"
    assert D.grok_health([_billing(90.0)], NOW).status == "ok"
    assert D.grok_health([_billing(100.0, cap=50, used=50)], NOW).status == "exhausted"  # cap fully used = no headroom
    assert D.grok_health([_billing(100.0, cap=50, used=49)], NOW).status == "ok"


def test_grok_uses_the_latest_402_and_a_six_hour_window():
    assert D.grok_health([_402("2026-09-18T19:00:00.000Z"), _402("2026-09-19T04:00:00.000Z")], NOW).status == "exhausted"
    assert D.grok_health([_402("2026-09-18T22:59:00.000Z")], NOW).status == "unknown"  # just over 6h


def test_the_history_cutoff_applies_to_error_lines_and_other_harness_signatures_are_ignored():
    old = json.dumps({"type": "system", "subtype": "api_error", "timestamp": "2026-09-01T00:00:00.000Z",
                      "error": {"status": 429, "message": "[1308][x 2026-09-24 09:00:00 后可继续使用]"}})
    assert D.quota_events([old], SIGS, NOW - dt.timedelta(hours=48)) == []
    grok_text = json.dumps({"type": "system", "subtype": "api_error", "timestamp": "2026-09-19T04:00:00.000Z",
                            "error": {"status": 402, "message": "402 Payment Required usage balance exhausted"}})
    assert D.quota_events([grok_text], SIGS, NOW - dt.timedelta(hours=48)) == []  # a grok signature is not a claude one


def test_ansi_is_stripped_and_the_match_carries_its_timestamp():
    line = ("\x1b[2m2026-09-19T04:24:34.395337Z\x1b[0m \x1b[31mERROR\x1b[0m responses API error status=402 "
            "Payment Required error_message=Grok Build usage balance exhausted")
    (m,) = D.scan_agent_log([line], SIGS).matches
    assert m[0] == ts("2026-09-19T04:24:34.395337Z") and m[1:] == ("grok-402-balance", "quota")


@pytest.mark.parametrize("text", [
    "ERROR request failed: billing balance too low", "ERROR credit exhausted for org", "ERROR upstream overloaded",
    "ERROR responses API error status=529 something"])
def test_suspicious_words_and_529_are_flagged_for_learning(text):
    assert len(D.scan_agent_log(["2026-09-19T05:00:00.000000Z " + text], SIGS).unclassified) == 1


def test_the_queue_noise_line_is_never_flagged():
    line = ("2026-09-19T04:24:34.443936Z  WARN buzz_acp::queue: requeueing failed batch with backoff channel_id="
            "11d76795-1dd1-4d53-af1c-83576ea992cf attempt=1 max=10 delay_secs=4 events=1 (rate limit)")
    assert D.scan_agent_log([line], SIGS).unclassified == []


# ───────────────────────── cli ─────────────────────────
def test_detect_twice_does_not_inflate_the_unknown_error_count(tmp_path):
    line = "2026-09-19T04:50:00.000000Z ERROR responses API error status=529 error_message=Daily quota window closed"
    home = make_home(tmp_path, log_lines=[line])
    run(["detect"], home)
    run(["detect"], home, now=NOW + dt.timedelta(minutes=30))
    entries = json.loads("[" + ",".join(open(f"{home}/.config/buzz/harness-failover/unknown-errors.jsonl").read().splitlines()) + "]")
    assert sorted(e["count"] for e in entries) == [2]  # the same line seen by both agents' logs, once per scan — never re-counted


def test_force_overrides_an_exhausted_target_and_the_minimum_interval(tmp_path):
    home = make_home(tmp_path)
    ops = FakeOps(home)
    rc, _ = run(["switch", "--to", "grok", "--apply"], home, ops=ops)
    assert rc == 1 and ops.restarted == []
    rc, _ = run(["switch", "--to", "grok", "--apply", "--force"], home, ops=ops)
    assert rc == 0 and len(ops.restarted) == 2
    write(f"{home}/.config/buzz/harness-failover/state.json", json.dumps({"last_switch": "2026-09-19T04:50:00Z"}), 0o600)
    ops.restarted.clear()
    assert run(["switch", "--to", "auto", "--apply"], home, ops=ops)[0] == 0 and ops.restarted == []
    later = NOW + dt.timedelta(seconds=5)  # backup names are per-second: a same-second rerun is refused on purpose
    assert run(["switch", "--to", "auto", "--apply", "--force"], home, ops=ops, now=later)[0] == 0 and len(ops.restarted) == 2


def test_a_bad_env_file_aborts_the_whole_switch_with_exit_1_and_no_restart(tmp_path):
    home = make_home(tmp_path)
    os.chmod(f"{home}/.config/buzz/agents/beta.env", 0o644)
    ops = FakeOps(home)
    rc, out = run(["switch", "--to", "auto", "--apply"], home, ops=ops)
    assert rc == 1 and "aborted" in out and ops.restarted == []
    assert "BUZZ_ACP_MODEL='grok-4.6'" in open(f"{home}/.config/buzz/agents/alpha.env").read()  # rolled back / untouched
    assert not state_of(home).get("last_switch")  # a failed attempt must not block the next try


def test_state_records_backups_and_the_direction_of_the_switch(tmp_path):
    home = make_home(tmp_path)
    run(["switch", "--to", "auto", "--apply"], home)
    st = state_of(home)
    assert st["from"] == "grok" and st["current"] == "claude-buzz" and len(st["backups"]) == 2


def test_the_switch_retry_chain_names_the_old_harness_first(tmp_path):
    home = make_home(tmp_path, log_lines=dead_log())
    buzz = FakeBuzz([lost_message()])
    run(["switch", "--to", "auto", "--apply", "--retry"], home, buzz=buzz)
    content = buzz.sent[0]["content"]
    assert content.index("grok") < content.index("claude-buzz")


def test_retry_takes_labels_from_state_and_honours_mode_any_and_the_time_horizon(tmp_path):
    home = make_home(tmp_path, log_lines=dead_log())
    write(f"{home}/.config/buzz/harness-failover/state.json", json.dumps({"from": "grok", "current": "claude-buzz"}), 0o600)
    plain = {**lost_message(), "id": "p" * 64, "tags": [["h", CH]]}  # does not mention the agent
    buzz = FakeBuzz([plain])
    run(["retry", "--apply"], home, buzz=buzz)
    assert buzz.sent == []  # default mode = mention
    run(["retry", "--apply", "--mode", "any"], home, buzz=buzz)
    assert len(buzz.sent) == 1 and "grok" in buzz.sent[0]["content"] and "claude-buzz" in buzz.sent[0]["content"]
    buzz.sent.clear()
    os.unlink(f"{home}/.config/buzz/harness-failover/nudged.json")
    run(["retry", "--apply", "--mode", "any", "--since-hours", "0.001"], home, buzz=buzz)
    assert buzz.sent == []  # the failure is older than the horizon


def test_a_probe_that_finds_the_harness_broken_overrides_healthy_history_but_dead_profiles_are_not_probed(tmp_path):
    home = make_home(tmp_path)
    ops = FakeOps(home)
    probed = []
    ops.probe = lambda prof: probed.append(prof.id) or Health("unavailable", "probe failed")
    rep = json.loads(run(["detect", "--json", "--probe"], home, ops=ops)[1])
    assert rep["health"]["claude-buzz"]["status"] == "unavailable"  # history said ok, the probe wins
    assert "grok" not in probed and "codex-buzz" not in probed  # exhausted / not logged in: nothing to learn


def test_account_of_distinguishes_accounts_and_shares_none_across_harnesses(tmp_path):
    home = str(tmp_path / "home")
    by = {p.id: p for p in P.load_profiles(home)}
    write(f"{home}/.claude-buzz/.claude.json", json.dumps({"oauthAccount": {"accountUuid": "abc-123"}}))
    assert C.account_of(by["claude-buzz"]) == "claude:abc-123"
    assert C.account_of(by["glm"]) == "provider:glm"  # no login file: fall back to the provider
    assert C.account_of(by["grok"]) == "grok:grok" and C.account_of(by["codex-buzz"]) is None


def test_launchers_are_run_agent_plus_only_scripts_that_launch_directly(tmp_path):
    home = make_home(tmp_path)
    d = f"{home}/.config/buzz/agents"
    write(f"{d}/run-delegating.sh", "exec run-agent.sh delegating\n", 0o700)
    write(f"{d}/run-direct.sh", "if [ \"$BUZZ_ACP_AGENT_COMMAND\" = x ]; then :; fi\nexec buzz-acp\n", 0o700)
    write(f"{d}/run-unrelated.sh", "echo hello\n", 0o700)
    ctx = C.Ctx(home, NOW, FakeOps(home), FakeBuzz(), print)
    assert [os.path.basename(p) for p in ctx.launchers()] == ["run-agent.sh", "run-direct.sh"]


def test_tail_lines_drops_the_partial_first_line_and_tolerates_a_missing_file(tmp_path):
    p = tmp_path / "log"
    p.write_text("".join(f"line-{i:03d}\n" for i in range(100)))
    small = C.tail_lines(str(p), nbytes=10 ** 6)
    assert small[0] == "line-000" and len(small) == 100  # whole file: nothing dropped
    tail = C.tail_lines(str(p), nbytes=45)
    assert tail[-1] == "line-099" and all(x.startswith("line-") and len(x) == 8 for x in tail)  # no half line
    assert C.tail_lines(str(tmp_path / "nope")) == []


def test_claude_history_older_than_the_cutoff_is_not_read(tmp_path):
    home = str(tmp_path / "home")
    prof = {p.id: p for p in P.load_profiles(home)}["claude-buzz"]
    fresh = tmp_path / "home/.claude-buzz/projects/p/new.jsonl"
    old = tmp_path / "home/.claude-buzz/projects/p/old.jsonl"
    write(str(fresh), "fresh\n")
    write(str(old), "stale\n")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    assert list(C.claude_lines(prof, dt.datetime.now(UTC) - dt.timedelta(hours=48))) == ["fresh\n"]
