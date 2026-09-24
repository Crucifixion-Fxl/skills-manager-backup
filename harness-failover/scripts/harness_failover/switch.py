"""Choosing a failover target and switching every agent together."""
from __future__ import annotations

import datetime as dt
import subprocess
import time
from dataclasses import dataclass, field

from . import envfile


@dataclass
class Decision:
    action: str  # none | switch | stuck
    to: str | None = None
    reason: str = ""


@dataclass
class SwitchResult:
    target: str
    backups: list = field(default_factory=list)
    agents: list = field(default_factory=list)  # dicts: name, restart_rc, active, running_env_ok

    @property
    def bad(self) -> list:
        return [a for a in self.agents
                if a["restart_rc"] != 0 or a["active"] not in ("active", "skipped") or not a["running_env_ok"]]


def decide(current, health, profiles, account_of=None, allow_unknown=False) -> Decision:
    """Only ever leaves a harness that is *exhausted*; never switches on unknown. Never falls back to a
    profile on the same account as the exhausted one."""
    if current not in health or health[current].status != "exhausted":
        return Decision("none", None, f"当前 {current} 未判定为耗尽")
    cur = next((p for p in profiles if p.id == current), None)
    cur_acct = account_of(cur) if (account_of and cur) else None
    ok = ("ok", "unknown") if allow_unknown else ("ok",)
    for p in profiles:  # already sorted by priority
        if p.id == current or p.id not in health or health[p.id].status not in ok:
            continue
        if cur_acct is not None and account_of(p) == cur_acct:
            continue
        until = health[current].until
        return Decision("switch", p.id, f"{current} 已耗尽（至 {until or '未知'}）；{p.id} 可用")
    return Decision("stuck", None, f"{current} 已耗尽，但没有可用的备选")


def too_soon(last_iso, now, minutes=20) -> bool:
    """True if the last switch was less than `minutes` ago. Anything unreadable, or a time in the future
    (clock jump, edited state file), never blocks a failover."""
    if not isinstance(last_iso, str):
        return False
    try:
        last = dt.datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return dt.timedelta(0) <= now - last < dt.timedelta(minutes=minutes)


def check_launchers(profile, launchers) -> None:
    """A claude-family profile only takes effect if every launcher honours HARNESS_CLAUDE_WRAPPER;
    otherwise the agent would silently keep running through the hard-coded default wrapper."""
    if profile.harness != "claude":
        return
    for path in launchers:
        if "HARNESS_CLAUDE_WRAPPER" not in open(path, errors="replace").read():
            raise envfile.EnvError(f"launcher does not support HARNESS_CLAUDE_WRAPPER: {path}")


def running_env_matches(env: dict, profile) -> bool:
    want = profile.env_updates()
    ok = env.get("BUZZ_ACP_AGENT_COMMAND") == profile.launch_command and env.get("BUZZ_ACP_MODEL") == profile.model
    ok = ok and env.get("BUZZ_ACP_EFFORT_LEVEL", profile.effort) == profile.effort
    for key in ("BUZZ_ACP_MEDIA_ADAPTER_COMMAND", "BUZZ_ACP_MEDIA_BUZZ_CLI", "BUZZ_ACP_MEDIA_MODE"):
        actual, expected = env.get(key), want.get(key)
        ok = ok and (actual in (None, "") if expected is None else actual == expected)
    if profile.harness == "claude":
        ok = ok and env.get("CLAUDE_CODE_EXECUTABLE") == profile.wrapper
        ok = ok and env.get("CLAUDE_CONFIG_DIR") == profile.home
    if profile.harness == "codex":
        ok = ok and env.get("CODEX_HOME") == profile.home
    return ok


SYSTEMD_ERRORS = (OSError, subprocess.TimeoutExpired)


def _safe(fn, arg, default):
    try:
        return fn(arg)
    except SYSTEMD_ERRORS:
        return default


def drifted(agents, running_env) -> list:
    """Names of agents whose running process was started with a different configuration than their env file says
    (e.g. a restart timed out half-way through a switch). {} from running_env means 'cannot tell' — not drift."""
    out = []
    for a in agents:
        env = _safe(running_env, a["unit"], {})
        if not env:
            continue
        v = a["vars"]
        want = {"BUZZ_ACP_AGENT_COMMAND": v.get("BUZZ_ACP_AGENT_COMMAND"), "BUZZ_ACP_MODEL": v.get("BUZZ_ACP_MODEL"),
                "BUZZ_ACP_EFFORT_LEVEL": v.get("BUZZ_ACP_EFFORT_LEVEL")}
        media_adapter, media_cli, media_mode = (
            v.get("BUZZ_ACP_MEDIA_ADAPTER_COMMAND"),
            v.get("BUZZ_ACP_MEDIA_BUZZ_CLI"),
            v.get("BUZZ_ACP_MEDIA_MODE"),
        )
        proxy_tuple = bool(media_adapter) and bool(media_cli) and not media_mode
        direct_tuple = not media_adapter and not media_cli and media_mode == "stock_text_only"
        if not (proxy_tuple or direct_tuple):
            out.append(a["name"])
            continue
        if proxy_tuple:
            want["BUZZ_ACP_MEDIA_ADAPTER_COMMAND"] = media_adapter
            want["BUZZ_ACP_MEDIA_BUZZ_CLI"] = media_cli
            want["BUZZ_ACP_MEDIA_MODE"] = None
        else:
            want["BUZZ_ACP_MEDIA_ADAPTER_COMMAND"] = None
            want["BUZZ_ACP_MEDIA_BUZZ_CLI"] = None
            want["BUZZ_ACP_MEDIA_MODE"] = "stock_text_only"
        for key in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_EXECUTABLE"):
            if v.get(key):
                want[key] = v[key]
        if v.get("HARNESS_CLAUDE_WRAPPER"):
            want["CLAUDE_CODE_EXECUTABLE"] = v["HARNESS_CLAUDE_WRAPPER"]
        if v.get("CODEX_HOME"):
            want["CODEX_HOME"] = v["CODEX_HOME"]
        if any((env.get(k) not in (None, "") if val is None else env.get(k) != val) for k, val in want.items()):
            out.append(a["name"])
    return out


def restart_and_verify(profile_or_none, agents, *, restart, is_active, running_env, sleep, stagger=3.0,
                       settle=8.0) -> list:
    """Restart the given agents (staggered; stopped units are skipped, never resurrected), then prove each running
    process loaded its config. Any systemd hiccup is recorded as a bad agent, never raised."""
    results = []
    for a in agents:
        if _safe(is_active, a["unit"], "unknown") == "inactive":
            results.append({"name": a["name"], "restart_rc": 0, "skipped": True})
            continue
        try:
            rc = restart(a["unit"])
        except SYSTEMD_ERRORS:
            rc = -1
        results.append({"name": a["name"], "restart_rc": rc})
        sleep(stagger)  # avoid N buzz-acp reconnecting to the relay at once
    sleep(settle)
    for r, a in zip(results, agents):
        if r.get("skipped"):
            r["active"], r["running_env_ok"] = "skipped", True
            continue
        r["active"] = _safe(is_active, a["unit"], "unknown")
        env = _safe(running_env, a["unit"], {})
        r["running_env_ok"] = running_env_matches(env, profile_or_none) if profile_or_none is not None else not drifted([a], lambda u: env)
    return results


def apply_switch(profile, agents, *, launchers, ts, restart, is_active, running_env, sleep=None, stagger=3.0,
                 settle=8.0) -> SwitchResult:
    """All-or-nothing on the env files (verified + rolled back on failure), then staggered restarts, then proof
    that each *running* process loaded the new configuration."""
    sleep = sleep or time.sleep
    check_launchers(profile, launchers)
    paths = [a["env"] for a in agents]
    backups = envfile.apply_updates(paths, profile.env_updates(), ts)
    envfile.prune_backups(paths, keep=5)
    results = restart_and_verify(profile, agents, restart=restart, is_active=is_active, running_env=running_env,
                                 sleep=sleep, stagger=stagger, settle=settle)
    return SwitchResult(profile.id, backups, results)
