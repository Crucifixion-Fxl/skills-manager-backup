"""S3b: choosing the target and switching every agent together."""
import datetime as dt
import os

import pytest

from harness_failover import envfile as E
from harness_failover import profiles as P
from harness_failover import switch as W
from harness_failover.detect import Health

HOME = "/home/u"
NOW = dt.datetime(2026, 9, 19, 5, 0, tzinfo=dt.timezone.utc)
PS = P.load_profiles(HOME)
BY = {p.id: p for p in PS}


def H(status, until=None):
    return Health(status, status, until)


ALL_OK = {"grok": H("ok"), "claude-buzz": H("ok"), "codex-buzz": H("ok"), "glm": H("ok")}


def health(**over):
    return {**ALL_OK, **{k: H(v) if isinstance(v, str) else v for k, v in over.items()}}


# ───────── decide ─────────
def test_no_action_when_current_is_healthy():
    assert W.decide("grok", health(), PS).action == "none"


def test_no_action_when_current_health_is_unknown():
    assert W.decide("grok", health(grok="unknown"), PS).action == "none"


def test_switches_to_highest_priority_healthy_profile():
    d = W.decide("grok", health(grok="exhausted"), PS)
    assert (d.action, d.to) == ("switch", "claude-buzz")


def test_skips_exhausted_unavailable_and_unknown_candidates():
    d = W.decide("grok", health(grok="exhausted", **{"claude-buzz": "exhausted", "codex-buzz": "unavailable"}), PS)
    assert (d.action, d.to) == ("switch", "glm")
    d = W.decide("grok", health(grok="exhausted", **{"claude-buzz": "unknown"}), PS)
    assert d.to == "codex-buzz"


def test_unknown_candidate_allowed_only_when_asked():
    h = health(grok="exhausted", **{"claude-buzz": "unknown", "codex-buzz": "unavailable", "glm": "exhausted"})
    assert W.decide("grok", h, PS).action == "stuck"
    assert W.decide("grok", h, PS, allow_unknown=True).to == "claude-buzz"


def test_stuck_when_nothing_is_usable():
    h = health(grok="exhausted", **{"claude-buzz": "exhausted", "codex-buzz": "unavailable", "glm": "exhausted"})
    d = W.decide("grok", h, PS)
    assert d.action == "stuck" and d.to is None


def test_skips_profiles_that_share_the_exhausted_account():
    ps = P.load_profiles(HOME)
    accounts = {"claude-buzz": "acctA", "glm": "acctG", "grok": "acctX", "codex-buzz": "acctC"}
    d = W.decide("claude-buzz", health(**{"claude-buzz": "exhausted"}), ps, account_of=lambda p: accounts[p.id])
    assert d.to == "grok"
    same = dict(accounts, grok="acctA")  # grok on the very same account as the exhausted profile
    d = W.decide("claude-buzz", health(**{"claude-buzz": "exhausted"}), ps, account_of=lambda p: same[p.id])
    assert d.to == "codex-buzz"


def test_unknown_current_profile_never_switches():
    assert W.decide(None, health(), PS).action == "none"


def test_too_soon_guard():
    last = "2026-09-19T04:50:00Z"
    assert W.too_soon(last, NOW, minutes=20) is True
    assert W.too_soon("2026-09-19T04:30:00Z", NOW, minutes=20) is False
    assert W.too_soon(None, NOW) is False
    assert W.too_soon("garbage", NOW) is False


# ───────── running env / launchers ─────────
def test_running_env_matches_claude_profile():
    p = BY["claude-buzz"]
    env = {"BUZZ_ACP_AGENT_COMMAND": p.command, "BUZZ_ACP_MODEL": "sonnet",
           "BUZZ_ACP_MEDIA_MODE": "stock_text_only", "CLAUDE_CODE_EXECUTABLE": p.wrapper,
           "CLAUDE_CONFIG_DIR": p.home}
    assert W.running_env_matches(env, p)
    assert not W.running_env_matches({**env, "BUZZ_ACP_MODEL": "opus[1m]"}, p)
    assert not W.running_env_matches({**env, "CLAUDE_CODE_EXECUTABLE": "/w/claude-glm"}, p)


def test_running_env_matches_codex_needs_codex_home():
    p = BY["codex-buzz"]
    env = {"BUZZ_ACP_AGENT_COMMAND": p.command, "BUZZ_ACP_MODEL": "gpt-5.6-sol",
           "BUZZ_ACP_MEDIA_MODE": "stock_text_only", "CODEX_HOME": p.home}
    assert W.running_env_matches(env, p)
    assert not W.running_env_matches({k: v for k, v in env.items() if k != "CODEX_HOME"}, p)


def test_running_env_matches_media_proxy_profile_needs_proxy_adapter_and_cli(tmp_path):
    profile_file = tmp_path / "profiles.json"
    profile_file.write_text('{"profiles":[{"id":"codex-buzz",'
                            '"command":"~/.local/share/buzz-agent-setup/adapters/codex-acp",'
                            '"media_proxy":"~/.local/share/buzz-agent-setup/proxy/abc/codex-acp",'
                            '"media_buzz_cli":"~/.local/opt/buzz-0.5.23/usr/bin/buzz"}]}')
    p = {x.id: x for x in P.load_profiles(HOME, user_file=str(profile_file))}["codex-buzz"]
    env = {k: v for k, v in p.env_updates().items() if v is not None}
    assert W.running_env_matches(env, p)
    assert not W.running_env_matches({k: v for k, v in env.items() if k != "BUZZ_ACP_MEDIA_ADAPTER_COMMAND"}, p)
    assert not W.running_env_matches({**env, "BUZZ_ACP_MEDIA_BUZZ_CLI": "/wrong/buzz"}, p)
    assert not W.running_env_matches({**env, "BUZZ_ACP_MEDIA_MODE": "stock_text_only"}, p)


def test_running_env_direct_profile_requires_stock_mode_and_no_proxy_fields():
    p = BY["codex-buzz"]
    env = {k: v for k, v in p.env_updates().items() if v is not None}
    assert W.running_env_matches(env, p)
    assert not W.running_env_matches({k: v for k, v in env.items() if k != "BUZZ_ACP_MEDIA_MODE"}, p)
    assert not W.running_env_matches({**env, "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/stale/codex-acp"}, p)


def test_check_launchers_requires_wrapper_support_for_claude_profiles(tmp_path):
    ok = tmp_path / "ok.sh"
    ok.write_text('export CLAUDE_CODE_EXECUTABLE="${HARNESS_CLAUDE_WRAPPER:-x}"\n')
    old = tmp_path / "old.sh"
    old.write_text("export CLAUDE_CODE_EXECUTABLE=/home/u/.local/bin/claude-glm\n")
    W.check_launchers(BY["claude-buzz"], [str(ok)])
    with pytest.raises(E.EnvError):
        W.check_launchers(BY["claude-buzz"], [str(ok), str(old)])
    W.check_launchers(BY["grok"], [str(old)])  # grok/codex do not need the wrapper variable


# ───────── apply_switch ─────────
AGENTS_ENV = "BUZZ_PRIVATE_KEY=nsec1fake\nBUZZ_ACP_AGENT_COMMAND=/old\nBUZZ_ACP_MODEL='grok-4.6'\nBUZZ_ACP_EFFORT_LEVEL=high\n"


class Fake:
    def __init__(self, tmp_path, n=3, active="active", env_ok=True, restart_rc=0):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp, self.order, self.active, self.env_ok, self.rc = tmp_path, [], active, env_ok, restart_rc
        self.agents = []
        for i in range(n):
            f = tmp_path / f"agent{i}.env"
            f.write_text(AGENTS_ENV)
            os.chmod(f, 0o600)
            self.agents.append(dict(name=f"agent{i}", env=str(f), unit=f"buzz-local-agent{i}.service"))
        self.launcher = tmp_path / "run.sh"
        self.launcher.write_text("${HARNESS_CLAUDE_WRAPPER:-x}\n")
        self.profile = BY["claude-buzz"]

    def restart(self, unit):
        self.order.append(unit)
        return self.rc

    def is_active(self, unit):
        return self.active

    def running_env(self, unit):
        p = self.profile
        if not self.env_ok:
            return {}
        env = {k: v for k, v in p.env_updates().items() if v is not None}
        if p.wrapper:
            env["CLAUDE_CODE_EXECUTABLE"] = p.wrapper
        return env

    def run(self, profile=None):
        return W.apply_switch(profile or self.profile, self.agents, launchers=[str(self.launcher)], ts="t",
                              restart=self.restart, is_active=self.is_active, running_env=self.running_env,
                              sleep=lambda s: None, stagger=0)


def test_apply_switch_rewrites_all_envs_and_restarts_each_unit_once_in_order(tmp_path):
    f = Fake(tmp_path)
    res = f.run()
    assert f.order == [a["unit"] for a in f.agents]
    assert res.bad == [] and len(res.agents) == 3 and len(res.backups) == 3
    for a in f.agents:
        text = open(a["env"]).read()
        assert "BUZZ_ACP_MODEL=sonnet" in text and "BUZZ_PRIVATE_KEY=nsec1fake" in text
        assert f"HARNESS_CLAUDE_WRAPPER={f.profile.wrapper}" in text


def test_apply_switch_preserves_media_proxy_contract_atomically(tmp_path):
    profile_file = tmp_path / "profiles.json"
    profile_file.write_text('{"profiles":[{"id":"claude-buzz",'
                            '"command":"~/.local/share/buzz-agent-setup/adapters/claude-agent-acp",'
                            '"media_proxy":"~/.local/share/buzz-agent-setup/proxy/abc/claude-agent-acp",'
                            '"media_buzz_cli":"~/.local/opt/buzz-0.5.23/usr/bin/buzz"}]}')
    p = {x.id: x for x in P.load_profiles(HOME, user_file=str(profile_file))}["claude-buzz"]
    f = Fake(tmp_path / "fleet", n=1)
    f.profile = p
    res = f.run(profile=p)
    assert res.bad == []
    text = open(f.agents[0]["env"]).read()
    assert f"BUZZ_ACP_AGENT_COMMAND={p.media_proxy}" in text
    assert f"BUZZ_ACP_MEDIA_ADAPTER_COMMAND={p.command}" in text
    assert f"BUZZ_ACP_MEDIA_BUZZ_CLI={p.media_buzz_cli}" in text
    assert f"CLAUDE_CONFIG_DIR={p.home}" in text


def test_apply_switch_reports_inactive_agents(tmp_path):
    f = Fake(tmp_path / "inactive", n=2, active="failed")
    assert {b["name"] for b in f.run().bad} == {"agent0", "agent1"}


def test_apply_switch_reports_agents_that_did_not_load_the_new_config(tmp_path):
    f = Fake(tmp_path / "stale", n=1, env_ok=False)
    assert [b["name"] for b in f.run().bad] == ["agent0"]


def test_apply_switch_reports_restart_failures(tmp_path):
    f = Fake(tmp_path / "rc", n=1, restart_rc=1)
    assert [b["name"] for b in f.run().bad] == ["agent0"]


def test_apply_switch_restarts_nothing_when_an_env_write_fails(tmp_path):
    f = Fake(tmp_path, n=2)
    open(f.agents[1]["env"], "w").write("A='unterminated\n")  # cannot be sourced by bash
    os.chmod(f.agents[1]["env"], 0o600)
    with pytest.raises(E.EnvError):
        f.run()
    assert f.order == []
    assert open(f.agents[0]["env"]).read() == AGENTS_ENV  # rolled back


def test_apply_switch_refuses_claude_profile_when_launcher_is_not_ready(tmp_path):
    f = Fake(tmp_path)
    f.launcher.write_text("export CLAUDE_CODE_EXECUTABLE=/home/u/.local/bin/claude-glm\n")
    with pytest.raises(E.EnvError):
        f.run()
    assert f.order == [] and open(f.agents[0]["env"]).read() == AGENTS_ENV


def test_a_last_switch_time_in_the_future_never_blocks_failover_forever():
    """Review P3: a clock jump or edited state.json must not suppress switching indefinitely."""
    assert W.too_soon("2099-01-01T00:00:00Z", NOW) is False


# ───────── reliability review fixes ─────────
import subprocess  # noqa: E402


def test_too_soon_tolerates_any_garbage_in_the_state_file():
    for junk in (12345, ["x"], {"a": 1}, "", "not a date"):
        assert W.too_soon(junk, NOW) is False


def test_inactive_units_are_not_restarted_and_not_reported_as_bad(tmp_path):
    """A stopped/retired agent must not be resurrected by a failover."""
    f = Fake(tmp_path / "stopped", n=2, active="inactive")
    res = f.run()
    assert f.order == [] and res.bad == []
    assert all(a["active"] == "skipped" for a in res.agents)
    for a in f.agents:  # ...but its env file is updated so it comes up on the right harness when started later
        assert "BUZZ_ACP_MODEL=sonnet" in open(a["env"]).read()


def test_a_hung_or_failing_systemd_call_is_recorded_as_bad_not_a_crash(tmp_path):
    f = Fake(tmp_path / "hang", n=2)

    def restart(unit):
        f.order.append(unit)
        if unit.endswith("agent1.service"):
            raise subprocess.TimeoutExpired("systemctl", 30)
        return 0

    res = W.apply_switch(f.profile, f.agents, launchers=[str(f.launcher)], ts="t", restart=restart,
                         is_active=f.is_active, running_env=f.running_env, sleep=lambda s: None, stagger=0)
    assert [b["name"] for b in res.bad] == ["agent1"] and f.order == [a["unit"] for a in f.agents]


def test_a_failing_status_probe_is_recorded_as_bad(tmp_path):
    f = Fake(tmp_path / "probe", n=1)
    res = W.apply_switch(f.profile, f.agents, launchers=[str(f.launcher)], ts="t", restart=f.restart,
                         is_active=lambda u: (_ for _ in ()).throw(OSError("dbus gone")), running_env=f.running_env,
                         sleep=lambda s: None, stagger=0)
    assert [b["name"] for b in res.bad] == ["agent0"]


def test_drifted_finds_agents_whose_running_process_disagrees_with_their_env_file():
    p = BY["claude-buzz"]
    agents = [
        {"name": "ok", "unit": "u1", "vars": {**{k: v for k, v in p.env_updates().items() if v}}},
        {"name": "old", "unit": "u2", "vars": {**{k: v for k, v in p.env_updates().items() if v}}},
        {"name": "unreadable", "unit": "u3", "vars": {**{k: v for k, v in p.env_updates().items() if v}}},
    ]
    good = {"BUZZ_ACP_AGENT_COMMAND": p.command, "BUZZ_ACP_MODEL": "sonnet", "BUZZ_ACP_EFFORT_LEVEL": "medium",
            "BUZZ_ACP_MEDIA_MODE": "stock_text_only", "CLAUDE_CODE_EXECUTABLE": p.wrapper,
            "CLAUDE_CONFIG_DIR": p.home}
    running = {"u1": good, "u2": {**good, "BUZZ_ACP_MODEL": "grok-4.6", "CLAUDE_CODE_EXECUTABLE": ""}, "u3": {}}
    assert W.drifted(agents, lambda unit: running[unit]) == ["old"]  # {} = cannot tell, not drift


def test_drifted_checks_media_proxy_runtime_fields(tmp_path):
    profile_file = tmp_path / "profiles.json"
    profile_file.write_text('{"profiles":[{"id":"codex-buzz",'
                            '"command":"~/.local/share/buzz-agent-setup/adapters/codex-acp",'
                            '"media_proxy":"~/.local/share/buzz-agent-setup/proxy/abc/codex-acp",'
                            '"media_buzz_cli":"~/.local/opt/buzz-0.5.23/usr/bin/buzz"}]}')
    p = {x.id: x for x in P.load_profiles(HOME, user_file=str(profile_file))}["codex-buzz"]
    vars_ = {k: v for k, v in p.env_updates().items() if v is not None}
    agents = [{"name": "a", "unit": "u", "vars": vars_}]
    assert W.drifted(agents, lambda _: vars_) == []
    stale = {**vars_, "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/old/codex-acp"}
    assert W.drifted(agents, lambda _: stale) == ["a"]
    stale_mode = {**vars_, "BUZZ_ACP_MEDIA_MODE": "stock_text_only"}
    assert W.drifted(agents, lambda _: stale_mode) == ["a"]


def test_drifted_direct_mode_rejects_missing_mode_and_stale_proxy_fields():
    p = BY["codex-buzz"]
    vars_ = {k: v for k, v in p.env_updates().items() if v is not None}
    agents = [{"name": "a", "unit": "u", "vars": vars_}]
    assert W.drifted(agents, lambda _: vars_) == []
    missing_mode = {k: v for k, v in vars_.items() if k != "BUZZ_ACP_MEDIA_MODE"}
    assert W.drifted(agents, lambda _: missing_mode) == ["a"]
    legacy_agents = [{"name": "legacy", "unit": "u", "vars": missing_mode}]
    assert W.drifted(legacy_agents, lambda _: missing_mode) == ["legacy"]
    stale_proxy = {**vars_, "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/stale/codex-acp"}
    assert W.drifted(agents, lambda _: stale_proxy) == ["a"]


@pytest.mark.parametrize("invalid_media", [
    {"BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/proxy/codex-acp"},
    {"BUZZ_ACP_MEDIA_BUZZ_CLI": "/usr/bin/buzz"},
    {"BUZZ_ACP_MEDIA_MODE": "proxy"},
    {
        "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/proxy/codex-acp",
        "BUZZ_ACP_MEDIA_BUZZ_CLI": "/usr/bin/buzz",
        "BUZZ_ACP_MEDIA_MODE": "stock_text_only",
    },
])
def test_drifted_rejects_incomplete_or_conflicting_media_tuple(invalid_media):
    p = BY["codex-buzz"]
    base = {
        k: v for k, v in p.env_updates().items()
        if v is not None and k not in {
            "BUZZ_ACP_MEDIA_ADAPTER_COMMAND",
            "BUZZ_ACP_MEDIA_BUZZ_CLI",
            "BUZZ_ACP_MEDIA_MODE",
        }
    }
    vars_ = {**base, **invalid_media}
    agents = [{"name": "invalid", "unit": "u", "vars": vars_}]
    assert W.drifted(agents, lambda _: vars_) == ["invalid"]
