"""Lark notifications: exhausted / switched / failed, de-duplicated, never able to break a failover."""
import datetime as dt
import json
import os
import stat

import pytest

from harness_failover import cli as C
from harness_failover import notify as N
from harness_failover import profiles as P

from test_cli import (FakeBuzz, FakeOps, NOW, dead_log, lost_message, make_home, state_of, write)
from harness_failover.buzzcli import BuzzCliError

UTC = dt.timezone.utc
HOME = "/home/u"
BY = {p.id: p for p in P.load_profiles(HOME)}
UNTIL = dt.datetime(2026, 9, 25, 1, 3, 18, tzinfo=UTC)


# ───────────────────────── composing messages ─────────────────────────
def test_every_message_is_labelled_and_names_the_host():
    msgs = [N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "box"),
            N.compose_exhausted("grok", UNTIL, None, 22, "box"),
            N.compose_switched("grok", BY["claude-buzz"], 22, None, "box", NOW),
            N.compose_failure("partial", "box", names=["beta"]),
            N.compose_repaired(["beta"], "box", NOW)]
    for m in msgs:
        assert m.text.startswith("[harness-failover]") and "box" in m.text and "\n\n\n" not in m.text


def test_exhausted_with_a_target_says_where_the_agents_are_going():
    m = N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "box")
    assert m.kind == "exhausted" and "grok" in m.text and "2026-09-25 01:03" in m.text
    assert "22" in m.text and "claude-buzz" in m.text and "sonnet" in m.text and "medium" in m.text


def test_exhausted_without_a_target_is_a_stuck_alert_that_repeats_every_six_hours():
    m = N.compose_exhausted("grok", UNTIL, None, 22, "box")
    assert m.kind == "stuck" and m.remind_hours == 6 and "人工" in m.text


def test_exhausted_with_an_unknown_recovery_time_says_so():
    assert "未知" in N.compose_exhausted("grok", None, BY["claude-buzz"], 22, "box").text


def test_switched_reports_agents_and_the_retry_outcome():
    m = N.compose_switched("grok", BY["claude-buzz"], 22, {"sent": 3, "manual": 1, "errors": 0}, "box", NOW)
    assert m.kind == "switched" and "grok" in m.text and "claude-buzz" in m.text and "22" in m.text
    assert "3" in m.text and "1" in m.text and "人工" in m.text
    assert "重试" not in N.compose_switched("grok", BY["claude-buzz"], 22, None, "box", NOW).text


def test_each_switch_is_its_own_event_but_the_same_exhaustion_is_one_episode():
    a = N.compose_switched("grok", BY["claude-buzz"], 22, None, "box", NOW)
    b = N.compose_switched("grok", BY["claude-buzz"], 22, None, "box", NOW + dt.timedelta(hours=1))
    assert a.key != b.key
    assert N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "box").key == \
        N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "other").key


@pytest.mark.parametrize("kind,facts,needle", [
    ("aborted", {"reason": "env file must be 0600"}, "回滚"),
    ("partial", {"names": ["beta", "alpha"]}, "beta"),
    ("unidentified", {}, "识别"),
    ("retry", {"manual": 2, "errors": 1}, "2"),
    ("drift", {"names": ["beta"]}, "漂移"),
    ("error", {"error_type": "RuntimeError"}, "RuntimeError"),
])
def test_failure_messages_name_what_went_wrong(kind, facts, needle):
    m = N.compose_failure(kind, "box", **facts)
    assert m.kind == "failure" and needle in m.text and m.remind_hours == 6


def test_the_same_failure_has_the_same_key_and_a_different_failure_a_different_one():
    a = N.compose_failure("partial", "box", names=["beta"])
    assert a.key == N.compose_failure("partial", "box", names=["beta"]).key
    assert a.key != N.compose_failure("partial", "box", names=["alpha"]).key


def test_only_safe_labels_reach_the_message_never_free_text():
    m = N.compose_failure("partial", "box", names=["ok-name", "evil\nname <b>", "x" * 80])
    assert "ok-name" in m.text and "evil" not in m.text and "<b>" not in m.text and "x" * 40 not in m.text
    e = N.compose_failure("error", "box", error_type="Runtime\nError: nsec1secret")
    assert "nsec1" not in e.text and "\n\n" not in e.text
    r = N.compose_failure("aborted", "box", reason="/home/u/.config/buzz/agents/a.env has token=abc123")
    assert "abc123" not in r.text and "token" not in r.text


def test_long_name_lists_are_truncated():
    m = N.compose_failure("partial", "box", names=[f"agent{i}" for i in range(30)])
    assert "agent0" in m.text and "agent29" not in m.text and "30" in m.text


# ───────────────────────── de-duplication ─────────────────────────
def test_one_off_events_are_sent_once_and_reminders_only_after_the_interval():
    stuck = N.compose_exhausted("grok", UNTIL, None, 22, "box")
    st = {}
    assert N.should_send(st, stuck, NOW)
    st = N.record(st, stuck, NOW)
    assert not N.should_send(st, stuck, NOW + dt.timedelta(hours=5, minutes=59))
    assert N.should_send(st, stuck, NOW + dt.timedelta(hours=6))
    exhausted = N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "box")
    st = N.record({}, exhausted, NOW)
    assert not N.should_send(st, exhausted, NOW + dt.timedelta(days=3))  # a one-off episode never repeats


def test_recording_forgets_entries_older_than_a_week():
    old = N.compose_failure("partial", "box", names=["a"])
    st = N.record({}, old, NOW - dt.timedelta(days=8))
    st = N.record(st, N.compose_failure("partial", "box", names=["b"]), NOW)
    assert list(st) == [N.compose_failure("partial", "box", names=["b"]).key]


# ───────────────────────── sending through lark-cli ─────────────────────────
def fake_lark(tmp_path, out='{"ok": true}', rc=0, err=""):
    d = tmp_path / "nvm-bin"
    d.mkdir(exist_ok=True)
    rec = tmp_path / "rec"
    exe = d / "lark-cli"
    exe.write_text(f"""#!/usr/bin/env bash
printf '%s\\0' "$@" > '{rec}.args'
env | grep -cE '^(BUZZ_|GITLAB_|ANTHROPIC_)' > '{rec}.secrets' || true
printf '%s' "$PATH" > '{rec}.path'
case "$1 $2" in
  "auth status") printf '%s' '{{"identities": {{"user": {{"status": "ready", "openId": "ou_abc123", "userName": "Jane"}}, "bot": {{"status": "ready"}}}}}}'; exit 0;;
esac
printf '%s' '{out}'
[ -n '{err}' ] && printf '%s' '{err}' >&2
exit {rc}
""")
    exe.chmod(0o755)
    return str(exe), rec


def configure(home, exe, **over):
    cfg = {"enabled": True, "recipient_open_id": "ou_abc123", "identity": "bot", "lark_cli": exe, **over}
    write(f"{home}/.config/buzz/harness-failover/notify.json", json.dumps(cfg), 0o600)


def read(rec, ext):
    return open(f"{rec}.{ext}").read()


def test_send_runs_lark_cli_as_the_bot_to_the_recipient_with_an_idempotency_key(tmp_path):
    exe, rec = fake_lark(tmp_path)
    configure(str(tmp_path), exe)
    msg = N.compose_failure("partial", "box", names=["beta"])
    ok, why = N.LarkNotifier(str(tmp_path), host="box").send(msg)
    assert ok, why
    args = read(rec, "args").split("\0")[:-1]  # NUL-separated: the text itself is multi-line
    assert args[:2] == ["im", "+messages-send"]
    for flag, val in (("--user-id", "ou_abc123"), ("--text", msg.text), ("--as", "bot")):
        assert args[args.index(flag) + 1] == val
    key = args[args.index("--idempotency-key") + 1]
    assert key.startswith("hf-") and len(key) == 27 and key == N.LarkNotifier(str(tmp_path)).idempotency_key(msg)


def test_the_child_gets_no_secrets_and_can_find_node_next_to_lark_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat" + "-should-not-leak")
    monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec-should-not-leak")
    exe, rec = fake_lark(tmp_path)
    configure(str(tmp_path), exe)
    N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "box"))
    assert read(rec, "secrets").strip() == "0"
    assert os.path.dirname(exe) in read(rec, "path").split(":")  # a timer's PATH has no nvm node dir


@pytest.mark.parametrize("rc,out", [(1, ""), (0, '{"ok": false, "error": "no permission"}')])
def test_a_failed_send_is_reported_not_raised(tmp_path, rc, out):
    exe, _ = fake_lark(tmp_path, out=out, rc=rc, err="denied token=abc123")
    configure(str(tmp_path), exe)
    ok, why = N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "box"))
    assert ok is False and "abc123" not in why and "\n" not in why


def test_missing_config_disabled_config_or_missing_binary_all_mean_not_sent(tmp_path):
    n = N.LarkNotifier(str(tmp_path))
    assert n.send(N.compose_failure("unidentified", "b"))[0] is False
    exe, rec = fake_lark(tmp_path)
    configure(str(tmp_path), exe, enabled=False)
    assert N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "b"))[0] is False
    assert not os.path.exists(f"{rec}.args")  # disabled means nothing was run
    configure(str(tmp_path), str(tmp_path / "gone"))
    ok, why = N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "b"))
    assert ok is False and "not found" in why


def test_a_hung_lark_cli_times_out_instead_of_blocking_the_failover(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "lark-cli"
    exe.write_text("#!/usr/bin/env bash\nsleep 30\n")
    exe.chmod(0o755)
    configure(str(tmp_path), str(exe))
    n = N.LarkNotifier(str(tmp_path))
    n.timeout = 0.5
    ok, why = n.send(N.compose_failure("unidentified", "b"))
    assert ok is False and "timed out" in why


@pytest.mark.parametrize("bad", [{"recipient_open_id": "ou_x; rm -rf"}, {"recipient_open_id": "--user-id"},
                                 {"identity": "root"}, {"lark_cli": "relative/lark-cli"}])
def test_a_config_that_could_smuggle_flags_is_rejected(tmp_path, bad):
    exe, rec = fake_lark(tmp_path)
    configure(str(tmp_path), exe, **bad)
    ok, why = N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "b"))
    assert ok is False and not os.path.exists(f"{rec}.args")


# ───────────────────────── setup / status ─────────────────────────
def test_setup_reads_your_open_id_from_lark_cli_and_writes_a_private_config(tmp_path):
    exe, _ = fake_lark(tmp_path)
    cfg = N.LarkNotifier(str(tmp_path), which=lambda: exe).setup()
    assert cfg["recipient_open_id"] == "ou_abc123" and cfg["lark_cli"] == exe and cfg["enabled"] is True
    path = f"{tmp_path}/.config/buzz/harness-failover/notify.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and json.load(open(path))["identity"] == "bot"


def test_setup_refuses_when_lark_cli_is_missing_or_not_logged_in(tmp_path):
    with pytest.raises(N.NotifyError):
        N.LarkNotifier(str(tmp_path), which=lambda: None).setup()
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "lark-cli"
    exe.write_text("#!/usr/bin/env bash\nprintf '%s' '{\"identities\": {\"user\": {\"status\": \"expired\"}}}'\n")
    exe.chmod(0o755)
    with pytest.raises(N.NotifyError):
        N.LarkNotifier(str(tmp_path), which=lambda: str(exe)).setup()


def test_status_says_whether_notifications_are_on_without_printing_the_full_id(tmp_path):
    exe, _ = fake_lark(tmp_path)
    assert N.LarkNotifier(str(tmp_path)).status()["enabled"] is False
    configure(str(tmp_path), exe)
    st = N.LarkNotifier(str(tmp_path)).status()
    assert st["enabled"] is True and st["identity"] == "bot" and "ou_abc123" not in json.dumps(st)


# ───────────────────────── wired into the CLI ─────────────────────────
class FakeNotifier:
    def __init__(self, ok=True, raises=False):
        self.sent, self.ok, self.raises = [], ok, raises
        self.host = "box"

    def send(self, msg):
        if self.raises:
            raise RuntimeError("lark exploded")
        if not self.ok:
            return False, "lark down"
        self.sent.append(msg)
        return True, ""

    def status(self):
        return {"enabled": True, "identity": "bot", "recipient": "ou_…123", "lark_cli": "/x/lark-cli"}

    def setup(self):
        return {"recipient_open_id": "ou_abc123", "lark_cli": "/x/lark-cli", "enabled": True, "identity": "bot"}


def nrun(argv, home, notifier, ops=None, buzz=None, now=NOW):
    lines = []
    rc = C.main(argv, home=home, now=now, ops=ops or FakeOps(home), buzz=buzz or FakeBuzz(), notifier=notifier,
                out=lines.append)
    return rc, "\n".join(str(x) for x in lines)


def kinds(n):
    return [m.kind for m in n.sent]


def test_a_successful_failover_sends_exhausted_first_then_switched(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    rc, _ = nrun(["switch", "--to", "auto", "--apply"], home, n)
    assert rc == 0 and kinds(n) == ["exhausted", "switched"]
    assert "grok" in n.sent[0].text and "claude-buzz" in n.sent[0].text
    assert "2" in n.sent[1].text and "claude-buzz" in n.sent[1].text


def test_the_switched_message_carries_the_retry_summary(tmp_path):
    home, n = make_home(tmp_path, log_lines=dead_log()), FakeNotifier()
    nrun(["switch", "--to", "auto", "--apply", "--retry"], home, n, buzz=FakeBuzz([lost_message()]))
    assert kinds(n) == ["exhausted", "switched"] and "重试" in n.sent[1].text and "1" in n.sent[1].text


def test_stuck_is_one_message_then_silence_then_a_reminder_after_six_hours(tmp_path):
    home, n = make_home(tmp_path, claude_ok=False), FakeNotifier()
    for minutes in (0, 30, 60):
        rc, _ = nrun(["switch", "--to", "auto", "--apply"], home, n, now=NOW + dt.timedelta(minutes=minutes))
        assert rc == 2
    assert kinds(n) == ["stuck"]
    nrun(["switch", "--to", "auto", "--apply"], home, n, now=NOW + dt.timedelta(hours=6, minutes=1))
    assert kinds(n) == ["stuck", "stuck"]


def test_a_partial_switch_sends_a_failure_naming_the_agent_and_no_success(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    ops = FakeOps(home)
    ops.fail_restart = {"buzz-local-beta.service"}
    rc, _ = nrun(["switch", "--to", "auto", "--apply"], home, n, ops=ops)
    assert rc == 3 and kinds(n) == ["exhausted", "failure"] and "beta" in n.sent[1].text


def test_an_aborted_switch_and_an_unidentifiable_fleet_are_reported(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    os.chmod(f"{home}/.config/buzz/agents/beta.env", 0o644)
    assert nrun(["switch", "--to", "auto", "--apply"], home, n)[0] == 1
    assert kinds(n) == ["exhausted", "failure"] and "回滚" in n.sent[1].text
    home2, n2 = make_home(tmp_path / "two"), FakeNotifier()
    for name in ("alpha", "beta"):
        p = f"{home2}/.config/buzz/agents/{name}.env"
        open(p, "w").write(open(p).read().replace("grok-buzz-acp.sh", "some-other-harness"))
    assert nrun(["switch", "--to", "auto", "--apply"], home2, n2, ops=FakeOps(home2))[0] == 6
    assert kinds(n2) == ["failure"] and "识别" in n2.sent[0].text


def test_retry_items_that_need_a_human_are_reported_even_after_a_good_switch(tmp_path):
    home, n = make_home(tmp_path, log_lines=dead_log()), FakeNotifier()
    buzz = FakeBuzz(error=BuzzCliError("not a member", code=3, category="auth"))
    rc, _ = nrun(["switch", "--to", "auto", "--apply", "--retry"], home, n, buzz=buzz)
    assert rc == 4 and kinds(n) == ["exhausted", "switched", "failure"]


def test_a_repaired_drift_is_announced(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    ops = FakeOps(home)
    ops.fail_restart = {"buzz-local-beta.service"}
    nrun(["switch", "--to", "auto", "--apply"], home, n, ops=ops)
    ops.fail_restart = set()
    n.sent.clear()
    nrun(["switch", "--to", "auto", "--apply"], home, n, ops=ops, now=NOW + dt.timedelta(minutes=30))
    assert kinds(n) == ["repaired"] and "beta" in n.sent[0].text


def test_a_manual_switch_to_a_healthy_target_announces_only_the_result(tmp_path):
    home, n = make_home(tmp_path, grok_pct=10.0), FakeNotifier()
    rc, _ = nrun(["switch", "--to", "claude-buzz", "--apply"], home, n)
    assert rc == 0 and kinds(n) == ["switched"]


def test_read_only_and_dry_run_commands_never_notify(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    for argv in (["detect"], ["detect", "--json"], ["switch", "--to", "auto"], ["profiles"],
                 ["retry", "--since-hours", "6", "--from", "grok", "--to", "claude-buzz"]):
        nrun(argv, home, n)
    assert n.sent == []


def test_a_broken_notifier_never_changes_the_outcome_and_is_retried_next_tick(tmp_path):
    home = make_home(tmp_path)
    for notifier in (FakeNotifier(ok=False), FakeNotifier(raises=True)):
        h = make_home(tmp_path / str(id(notifier)))
        rc, out = nrun(["switch", "--to", "auto", "--apply"], h, notifier)
        assert rc == 0 and "notification" in out.lower()
        assert "notified" not in state_of(h) or state_of(h)["notified"] == {}  # not recorded: it will be retried
    working = FakeNotifier()
    stuck_home = make_home(tmp_path / "stuck", claude_ok=False)
    nrun(["switch", "--to", "auto", "--apply"], stuck_home, FakeNotifier(ok=False))
    nrun(["switch", "--to", "auto", "--apply"], stuck_home, working, now=NOW + dt.timedelta(minutes=30))
    assert kinds(working) == ["stuck"]  # the failed attempt did not use up the notice


def test_an_unexpected_exception_is_reported_and_then_still_raised(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    ops = FakeOps(home)
    ops.restart = lambda unit: (_ for _ in ()).throw(RuntimeError("killed mid-loop"))
    with pytest.raises(RuntimeError):
        nrun(["switch", "--to", "auto", "--apply"], home, n, ops=ops)
    assert kinds(n)[-1] == "failure" and "RuntimeError" in n.sent[-1].text


def test_notify_setup_status_and_test_commands(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    rc, out = nrun(["notify", "--status"], home, n)
    assert rc == 0 and "enabled" in out.lower()
    rc, out = nrun(["notify", "--setup"], home, n)
    assert rc == 0 and "ou_abc123" not in out  # the id itself is not echoed
    assert n.sent == []
    rc, out = nrun(["notify", "--test"], home, n)
    assert rc == 0 and kinds(n) == ["test"] and "测试" in n.sent[0].text


# ───────── added from mutation testing of this feature ─────────
def test_a_relative_lark_cli_path_is_refused_even_when_such_a_file_exists_in_the_cwd(tmp_path, monkeypatch):
    """The path check itself must reject it — not just 'file not found'."""
    exe, rec = fake_lark(tmp_path)
    (tmp_path / "lark-cli").write_text(open(exe).read())
    (tmp_path / "lark-cli").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    configure(str(tmp_path), "lark-cli")
    ok, why = N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "b"))
    assert ok is False and "absolute" in why and not os.path.exists(f"{rec}.args")


def test_lark_cli_never_inherits_stdin(tmp_path, monkeypatch):
    import subprocess as sp
    exe, _ = fake_lark(tmp_path)
    configure(str(tmp_path), exe)
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return sp.CompletedProcess(argv, 0, '{"ok": true}', "")

    monkeypatch.setattr(sp, "run", fake_run)
    assert N.LarkNotifier(str(tmp_path)).send(N.compose_failure("unidentified", "b"))[0] is True
    assert seen.get("stdin") is sp.DEVNULL and seen.get("timeout")


def test_setup_refuses_a_user_identity_that_is_not_ready_even_if_it_has_an_open_id(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "lark-cli"
    exe.write_text("#!/usr/bin/env bash\nprintf '%s' '{\"identities\": {\"user\": {\"status\": \"expired\", \"openId\": \"ou_abc123\"}}}'\n")
    exe.chmod(0o755)
    with pytest.raises(N.NotifyError) as e:
        N.LarkNotifier(str(tmp_path), which=lambda: str(exe)).setup()
    assert "not ready" in str(e.value)
    assert not os.path.exists(f"{tmp_path}/.config/buzz/harness-failover/notify.json")


def test_a_dry_run_stays_silent_even_when_the_fleet_is_stuck(tmp_path):
    home, n = make_home(tmp_path, claude_ok=False), FakeNotifier()
    rc, out = nrun(["switch", "--to", "auto"], home, n)  # no --apply
    assert rc == 2 and "stuck" in out.lower() and n.sent == []


# ───────── from the independent review of this feature ─────────
import dataclasses  # noqa: E402
import signal  # noqa: E402

from harness_failover import retry as R  # noqa: E402


def test_an_exhaustion_with_no_known_reset_can_notify_again_after_six_hours():
    """A key of '...:na' used to be suppressed for a week: A->B->A within 7 days lost the early warning."""
    m = N.compose_exhausted("grok", None, BY["claude-buzz"], 22, "box")
    assert m.remind_hours == 6
    st = N.record({}, m, NOW)
    assert not N.should_send(st, m, NOW + dt.timedelta(hours=1)) and N.should_send(st, m, NOW + dt.timedelta(hours=6))
    assert N.compose_exhausted("grok", UNTIL, BY["claude-buzz"], 22, "box").remind_hours is None  # known reset: one-off


def test_the_retry_failure_key_does_not_depend_on_the_volatile_counts():
    a = N.compose_failure("retry", "box", manual=1, errors=0)
    assert a.key == N.compose_failure("retry", "box", manual=2, errors=3).key


def test_a_state_write_failure_never_escapes_notify(tmp_path):
    home, lines = make_home(tmp_path), []
    ctx = C.Ctx(home, NOW, FakeOps(home), FakeBuzz(), lines.append, FakeNotifier())

    def boom(*a, **k):
        raise OSError("no space left on device")

    ctx.save_state = boom
    ctx.notify(N.compose_failure("unidentified", "box"))  # must not raise
    assert any("notification" in x for x in lines)


def test_the_success_notice_is_sent_even_if_the_retry_step_blows_up(tmp_path):
    home, n = make_home(tmp_path, log_lines=dead_log()), FakeNotifier()

    class Exploding(FakeBuzz):
        def messages_get(self, *a, **k):
            raise RuntimeError("relay exploded")

    with pytest.raises(RuntimeError):
        nrun(["switch", "--to", "auto", "--apply", "--retry"], home, n, buzz=Exploding())
    assert kinds(n) == ["exhausted", "switched", "failure"] and "RuntimeError" in n.sent[-1].text


def test_a_crash_before_the_run_even_starts_is_still_reported(tmp_path):
    """Review: a bad profiles file made every tick die silently; that is exactly what a human must hear about."""
    home, n = make_home(tmp_path), FakeNotifier()
    write(f"{home}/.config/buzz/harness-profiles.json", json.dumps({"profiles": []}), 0o666)  # world-writable: refused
    with pytest.raises(P.ProfileError):
        nrun(["switch", "--to", "auto", "--apply"], home, n)
    assert kinds(n) == ["failure"] and "ProfileError" in n.sent[0].text


def test_a_read_only_command_that_crashes_at_startup_is_not_reported(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    write(f"{home}/.config/buzz/harness-profiles.json", json.dumps({"profiles": []}), 0o666)
    with pytest.raises(P.ProfileError):
        nrun(["detect"], home, n)
    assert n.sent == []


def test_sigterm_from_systemd_is_reported_and_the_handler_is_restored(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    ops = FakeOps(home)
    ops.restart = lambda unit: os.kill(os.getpid(), signal.SIGTERM)
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(Exception) as e:
        nrun(["switch", "--to", "auto", "--apply"], home, n, ops=ops)
    assert "Terminated" in type(e.value).__name__
    assert "Terminated" in n.sent[-1].text and signal.getsignal(signal.SIGTERM) == before


def test_a_trailing_newline_does_not_pass_the_label_filter():
    assert R.safe_label("abc\n") == "unknown" and R.safe_label("abc") == "abc"
    assert "\n" not in N._names(["evil\n"])


def test_a_model_name_cannot_smuggle_a_link_into_the_message():
    evil = dataclasses.replace(BY["claude-buzz"], model="https://evil.example/x")
    text = N.compose_exhausted("grok", UNTIL, evil, 22, "box").text
    assert "://" not in text and "/" not in text.replace("/medium", "")


def test_a_notified_entry_dated_in_the_future_cannot_silence_alerts():
    m = N.compose_failure("partial", "box", names=["a"])
    future = (NOW + dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert N.should_send({m.key: future}, m, NOW) is True
    assert m.key not in N.record({m.key: future}, N.compose_failure("partial", "box", names=["b"]), NOW)


def test_a_notified_value_that_is_not_a_dict_does_not_disable_notifications(tmp_path):
    home, n = make_home(tmp_path), FakeNotifier()
    write(f"{home}/.config/buzz/harness-failover/state.json", json.dumps({"notified": "oops"}), 0o600)
    nrun(["switch", "--to", "auto", "--apply"], home, n)
    assert kinds(n) == ["exhausted", "switched"]
