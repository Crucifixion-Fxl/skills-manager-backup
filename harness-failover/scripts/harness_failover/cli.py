"""Command line: detect → decide → switch every agent together → find lost messages → nudge them to retry."""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import fcntl
import glob
import json
import os
import re
import signal
import subprocess
import tempfile
import time

from . import detect as D
from . import envfile, learn
from . import notify as NT
from . import profiles as P
from . import retry as R
from . import signatures as S
from . import switch as W
from .buzzcli import BuzzCli

UTC = dt.timezone.utc
TAIL_BYTES = 4_000_000
HISTORY = dt.timedelta(hours=48)
READ_KEYS = [
    "BUZZ_ACP_AGENT_COMMAND", "BUZZ_ACP_AGENT_ARGS", "BUZZ_ACP_MODEL", "BUZZ_ACP_EFFORT_LEVEL",
    "HARNESS_CLAUDE_WRAPPER", "CODEX_HOME", "AGENT_PUBKEY_HEX",
    "BUZZ_ACP_RESPOND_TO", "BUZZ_ACP_RESPOND_TO_ALLOWLIST", "BUZZ_ACP_AGENT_OWNER", "HARNESS_FAILOVER_PINNED"]
PINNED_TRUE = {"1", "true", "yes"}  # anything else (absent, "", "0") is not pinned
PROC_KEYS = ("BUZZ_ACP_AGENT_COMMAND", "BUZZ_ACP_MODEL", "BUZZ_ACP_EFFORT_LEVEL", "CLAUDE_CODE_EXECUTABLE", "CODEX_HOME")
ISO = "%Y-%m-%dT%H:%M:%SZ"


# ───────────────────────────── probes ─────────────────────────────
PROBE_PROMPT = "Reply with exactly: OK"


def probe_command(profile, home):
    """(argv, env) for one minimal real request against the profile, or None when history is authoritative (grok).
    The env is minimal and carries no BUZZ_* identity."""
    base = {"HOME": home, "PATH": f"{home}/.local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    if profile.harness == "claude" and profile.wrapper:
        return ([profile.wrapper, "-p", PROBE_PROMPT, "--model", profile.model, "--output-format", "json"],
                {**base, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    if profile.harness == "codex":
        return ([f"{home}/.local/bin/codex", "exec", "--skip-git-repo-check", "--ephemeral", "-m", profile.model,
                 "-c", f'model_reasoning_effort="{profile.effort}"', PROBE_PROMPT], {**base, "CODEX_HOME": profile.home})
    return None


def _one_line(text: str) -> str:
    """The most informative single line of a harness's output (its ERROR line, else the last line), redacted."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    pick = next((l for l in reversed(lines) if l.lower().startswith("error")), lines[-1] if lines else "no output")
    return re.sub(r"\s+", " ", learn.redact(pick))[:140]


def parse_probe(profile, rc, stdout, stderr, sigs, now):
    text = f"{stdout}\n{stderr}"
    if profile.harness == "claude":
        try:
            o = json.loads(stdout)
        except ValueError:
            o = None
        if isinstance(o, dict) and not o.get("is_error") and o.get("subtype") == "success":
            return D.Health("ok", "probe succeeded", None, {"probe": "ok"})
        if isinstance(o, dict):
            text = str(o.get("result", ""))
    elif rc == 0 and stdout.strip().upper().startswith("OK"):
        return D.Health("ok", "probe succeeded", None, {"probe": "ok"})
    m = S.classify(text, sigs, harness=profile.harness, after=now)
    if m and m.kind == "quota":
        return D.Health("exhausted", f"probe hit {m.id}", m.until)
    return D.Health("unavailable", "probe failed: " + _one_line(text))


def parse_codex_login(rc, stdout, stderr):
    """True/False when `codex login status` says so, None when it says nothing usable (never guess).
    "Not logged in" is printed on stderr with rc=1, so both streams matter."""
    text = f"{stdout}\n{stderr}".lower()
    if "not logged in" in text:
        return False
    if rc == 0 and "logged in" in text:
        return True
    return None


# ───────────────────────────── real system operations ─────────────────────────────
class SystemOps:
    """The only place that touches systemd, /proc and the harness CLIs. Tests inject a fake."""

    def __init__(self, home):
        self.home = home
        self.sleep = time.sleep

    def _run(self, argv, env=None, timeout=120):
        # stdin closed: `codex exec` (and friends) block on an inherited stdin.
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)

    def restart(self, unit):
        return self._run(["systemctl", "--user", "restart", unit], timeout=30).returncode

    def is_active(self, unit):
        return self._run(["systemctl", "--user", "is-active", unit], timeout=15).stdout.strip()

    def running_env(self, unit):
        """Only the non-secret harness variables of the running process — proof the new config was loaded."""
        try:
            pid = self._run(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit], timeout=15).stdout.strip()
            if not pid.isdigit() or pid == "0":
                return {}
            raw = open(f"/proc/{pid}/environ", "rb").read().decode(errors="replace").split("\0")
        except (OSError, subprocess.TimeoutExpired):
            return {}
        env = dict(kv.split("=", 1) for kv in raw if "=" in kv)
        return {k: env[k] for k in PROC_KEYS if k in env}

    def codex_logged_in(self, codex_home):
        exe = f"{self.home}/.local/bin/codex"
        if not os.path.exists(exe):
            return None
        env = {"HOME": self.home, "PATH": "/usr/bin:/bin", "CODEX_HOME": codex_home}
        try:
            p = self._run([exe, "login", "status"], env=env, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_codex_login(p.returncode, p.stdout or "", p.stderr or "")

    def probe(self, profile):
        """One real minimal request; None = no probe for this harness (history is authoritative)."""
        cmd = probe_command(profile, self.home)
        if cmd is None:
            return None
        argv, env = cmd
        try:
            p = self._run(argv, env=env, timeout=150)
        except (OSError, subprocess.TimeoutExpired) as e:
            return D.Health("unavailable", f"probe failed: {type(e).__name__}")
        return parse_probe(profile, p.returncode, p.stdout, p.stderr, S.load_signatures(), dt.datetime.now(UTC))


# ───────────────────────────── gathering ─────────────────────────────
class Ctx:
    def __init__(self, home, now, ops, buzz, out, notifier=None):
        self.home, self.now, self.ops, self.buzz, self.out = home, now, ops, buzz, out
        self.notifier = notifier or NT.LarkNotifier(home)
        self.agents_dir = f"{home}/.config/buzz/agents"
        self.state_dir = f"{home}/.config/buzz/harness-failover"
        self.state_path = f"{self.state_dir}/state.json"
        self.nudged_path = f"{self.state_dir}/nudged.json"
        self.unknown_path = f"{self.state_dir}/unknown-errors.jsonl"
        self.profiles = P.load_profiles(home, user_file=f"{home}/.config/buzz/harness-profiles.json")
        self.sigs = S.load_signatures()

    def ensure_state_dir(self):
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)

    def host(self) -> str:
        return getattr(self.notifier, "host", "") or ""

    def notify(self, msg) -> None:
        """Best effort, de-duplicated. A broken notifier can never change the outcome of a failover, and a message
        that could not be delivered is not recorded, so the next tick tries again."""
        try:
            sent = self.load_state().get("notified")
            sent = sent if isinstance(sent, dict) else {}
            if not NT.should_send(sent, msg, self.now):
                return
            ok, why = self.notifier.send(msg)
            if ok:
                self.save_state(notified=NT.record(sent, msg, self.now))
            elif why:
                self.out(f"(notification not sent: {why})")
        except Exception as e:  # noqa: BLE001 - a notification must never take a failover down (even a full disk)
            self.out(f"(notification skipped: {type(e).__name__})")

    def load_state(self) -> dict:
        try:
            return json.load(open(self.state_path))
        except (OSError, ValueError):
            return {}

    def save_state(self, delete=(), **updates):
        self.ensure_state_dir()
        st = {**self.load_state(), **updates}
        for key in delete:
            st.pop(key, None)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".state.")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(st, f)
        os.replace(tmp, self.state_path)

    def agents(self) -> list[dict]:
        out = []
        for path in sorted(glob.glob(f"{self.agents_dir}/*.env")):
            v = envfile.read_vars(path, READ_KEYS)
            if not v.get("BUZZ_ACP_AGENT_COMMAND"):
                continue  # not an ACP agent (e.g. gitlab-notify)
            name = os.path.basename(path)[:-4]
            pinned = str(v.get("HARNESS_FAILOVER_PINNED", "")).strip().lower() in PINNED_TRUE
            out.append(dict(name=name, env=path, unit=f"buzz-local-{name}.service", vars=v, pinned=pinned,
                            log=f"{self.agents_dir}/{name}.log", pubkey=v.get("AGENT_PUBKEY_HEX", "")))
        return out

    def launchers(self) -> list[str]:
        found = [f"{self.agents_dir}/run-agent.sh"]
        for p in sorted(glob.glob(f"{self.agents_dir}/run-*.sh")):
            if p in found:
                continue
            text = open(p, errors="replace").read()
            if "run-agent.sh" not in text and "BUZZ_ACP_AGENT_COMMAND" in text:
                found.append(p)
        return found


def tail_lines(path, nbytes=TAIL_BYTES):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            data = f.read().decode(errors="replace").splitlines()
    except OSError:
        return []
    return data[1:] if size > nbytes else data


def claude_lines(profile, cutoff):
    for f in glob.glob(f"{profile.home}/projects/*/*.jsonl"):
        try:  # a session file can be pruned between the listing and the read
            if dt.datetime.fromtimestamp(os.path.getmtime(f), UTC) < cutoff:
                continue
            with open(f, errors="replace") as fh:
                yield from fh
        except OSError:
            continue


def account_of(profile):
    if profile.harness == "claude":
        try:
            acct = (json.load(open(f"{profile.home}/.claude.json")).get("oauthAccount") or {}).get("accountUuid")
            if acct:
                return f"claude:{acct}"
        except (OSError, ValueError):
            pass
        return f"provider:{profile.provider}" if profile.provider else None
    return f"{profile.harness}:{profile.id}" if profile.harness == "grok" else None


def merge_probe(ctx: Ctx, p, h):
    """One real minimal request against a profile, folded into its history-based health. Nothing to learn from
    probing a profile that is already known to be dead."""
    if h.status in ("exhausted", "unavailable"):
        return h
    pr = ctx.ops.probe(p)
    if pr is not None and (pr.status != "ok" or h.status == "unknown"):
        return D.Health(pr.status, pr.reason, pr.until, {**h.evidence, **pr.evidence})
    return h


def gather_health(ctx: Ctx, probe: bool) -> dict:
    health = {}
    for p in ctx.profiles:
        if p.harness == "grok":
            h = D.grok_health(tail_lines(f"{ctx.home}/.grok/logs/unified.jsonl"), ctx.now)
        elif p.harness == "claude":
            h = D.family_health(D.quota_events(claude_lines(p, ctx.now - HISTORY), ctx.sigs, ctx.now - HISTORY, provider=p.provider), ctx.now)
        else:
            h = D.codex_health(ctx.ops.codex_logged_in(p.home), ctx.now)
        health[p.id] = merge_probe(ctx, p, h) if probe else h
    return health


def probe_candidates(ctx: Ctx, rep: dict) -> dict:
    """Only when a switch is actually needed: verify the fallback candidates with a real request (never the current
    profile) and decide again. A healthy tick therefore costs no quota at all."""
    for p in ctx.profiles:
        if p.id != rep["current"]:
            rep["health"][p.id] = merge_probe(ctx, p, rep["health"][p.id])
    rep["decision"] = W.decide(rep["current"], rep["health"], ctx.profiles, account_of=account_of)
    return rep


def learn_unknowns(ctx: Ctx) -> None:
    """Record suspicious agent-log errors newer than the last scan, so repeated runs do not inflate counts.
    Best effort: a full disk or a corrupt store must never stop a failover decision."""
    try:
        cursor = ctx.load_state().get("learn_cursor", "")
        fresh = []
        for a in ctx.agents():
            for line in D.scan_agent_log(tail_lines(a["log"], 2_000_000), ctx.sigs).unclassified:
                m = D.LOG_TS_RE.match(line)
                if m and m.group(1) <= cursor:
                    continue
                fresh.append(line)
        ctx.ensure_state_dir()
        if fresh:
            learn.record_unknown(fresh, ctx.unknown_path, ctx.now)
        if os.path.exists(ctx.unknown_path):
            learn.prune_known(ctx.unknown_path, ctx.sigs)
        ctx.save_state(learn_cursor=ctx.now.strftime("%Y-%m-%dT%H:%M:%S.000000Z"))
    except (OSError, ValueError) as e:
        ctx.out(f"(learning store skipped: {type(e).__name__})")


def active_drift(ctx: Ctx, agents) -> list:
    """Agents whose *running* process disagrees with their env file (only units that are active)."""
    live = [a for a in agents if W._safe(ctx.ops.is_active, a["unit"], "unknown") == "active"]
    return W.drifted(live, ctx.ops.running_env)


def build_report(ctx: Ctx, probe: bool) -> dict:
    agents = ctx.agents()
    pinned = [a["name"] for a in agents if a["pinned"]]
    # A pinned agent runs a one-off config the fleet profiles don't describe (that is the point of pinning it):
    # counting it would only ever add noise to "unknown" and never to a real profile, diluting the majority vote
    # for an agent that deliberately isn't part of the fleet's shared harness.
    dist = collections.Counter(P.identify(a["vars"], ctx.profiles) or "unknown" for a in agents if not a["pinned"])
    current = dist.most_common(1)[0][0] if dist else "unknown"
    health = gather_health(ctx, probe)
    learn_unknowns(ctx)
    return dict(now=ctx.now.strftime(ISO), agents=len(agents), pinned=pinned, current=current, distribution=dict(dist),
                health=health, decision=W.decide(current, health, ctx.profiles, account_of=account_of),
                drift=active_drift(ctx, agents))


def report_dict(rep: dict) -> dict:
    iso = lambda d: d.astimezone(UTC).strftime(ISO) if d else None  # noqa: E731
    return {**rep, "health": {k: dict(status=h.status, reason=h.reason, until=iso(h.until), evidence=h.evidence)
                              for k, h in rep["health"].items()},
            "decision": dict(action=rep["decision"].action, to=rep["decision"].to, reason=rep["decision"].reason)}


def render(rep: dict) -> str:
    r = report_dict(rep)
    lines = [f"now {r['now']}  agents={r['agents']}  current={r['current']} {r['distribution']}"]
    for pid, h in r["health"].items():
        until = f" until {h['until']}" if h["until"] else ""
        lines.append(f"  {pid:12s} {h['status'].upper():11s}{until}  — {h['reason']}")
    d = r["decision"]
    lines.append(f"decision: {d['action']}" + (f" → {d['to']}" if d["to"] else "") + f"  ({d['reason']})")
    if r.get("drift"):
        lines.append(f"drift: {len(r['drift'])} agent(s) run a different config than their env file: {', '.join(r['drift'])}")
    if r.get("pinned"):
        lines.append(f"pinned: {len(r['pinned'])} agent(s) excluded from switching: {', '.join(r['pinned'])}")
    return "\n".join(lines)


# ───────────────────────────── retry ─────────────────────────────
def do_retry(ctx: Ctx, since_hours: float, from_id: str, to_id: str, apply: bool, mode: str = "mention"):
    """Find lost messages and nudge (dry-run unless apply). Returns the result dict, or None if it refused."""
    from_id, to_id = R.safe_label(from_id), R.safe_label(to_id)
    if apply and "unknown" in (from_id, to_id):
        ctx.out("refusing to post: which harness failed over is unknown — pass --from and --to")
        return None
    agents = ctx.agents()
    horizon = ctx.now - dt.timedelta(hours=since_hours)
    windows = []
    for a in agents:
        found = [w for w in R.failure_windows(tail_lines(a["log"]), agent=a["name"]) if w.last_ts >= horizon]
        quota_times = [m[0] for m in D.scan_agent_log(tail_lines(a["log"]), ctx.sigs).matches if m[2] == "quota"]
        windows += R.quota_windows(found, quota_times)
    pubkeys = {a["name"]: a["pubkey"] for a in agents if a["pubkey"]}
    gates = {a["name"]: R.allowed_authors(a["vars"]) for a in agents}
    recoveries = []
    for w in windows:
        if w.agent not in pubkeys:
            recoveries.append(R.Recovery(w, [], "unrecoverable", False, "no agent pubkey known (AGENT_PUBKEY_HEX missing)"))
        else:
            recoveries.append(R.recover(w, ctx.buzz, pubkeys[w.agent], mode=mode, allowed_authors=gates[w.agent]))
    state = R.load_state(ctx.nudged_path)

    def persist(st):
        ctx.ensure_state_dir()
        R.save_state(ctx.nudged_path, st)

    res = R.send_nudges(recoveries, ctx.buzz, pubkeys, state, apply=apply, from_id=from_id, to_id=to_id, now=ctx.now,
                        persist=persist if apply else None)
    ctx.out(f"retry: windows={len(windows)} planned={res['planned']} sent={res['sent']} errors={res['errors']} "
            f"already={res['skipped_already']} old={res['skipped_old']} capped={res['skipped_cap']}"
            + ("" if apply else "  [dry-run: add --apply to send]"))
    for m in res["manual"]:
        ctx.out(f"  manual: agent={m['agent']} channel={m['channel']} lost_events={m['events']} — {m['note']}")
    return res


def finish_retry(ctx: Ctx, res, from_id, to_id, apply: bool) -> int:
    """Exit code + the pending queue: a failed or capped send is retried on the next tick; anything that needs a
    human (manual list, errors) is a non-zero exit so it shows up in `systemctl --user --failed`."""
    if res is None:
        return 1
    if apply:
        if res["errors"] or res["skipped_cap"]:
            attempts = ctx.load_state().get("pending_retry", {}).get("attempts", 0) + 1
            ctx.save_state(pending_retry={"from": from_id, "to": to_id, "since": ctx.now.strftime(ISO), "attempts": attempts})
        else:
            ctx.save_state(delete=("pending_retry",))
    return 4 if (res["errors"] or res["manual"]) else 0


PENDING_MAX_AGE = dt.timedelta(hours=24)


def notify_if_human_needed(ctx: Ctx, res) -> None:
    if res and (res["errors"] or res["manual"]):
        ctx.notify(NT.compose_failure("retry", ctx.host(), manual=len(res["manual"]), errors=res["errors"]))


def maintenance(ctx: Ctx, a, rep) -> int:
    """What every tick does even when no switch is needed: repair agents left half-switched, then finish any
    retry that failed earlier."""
    rc = 0
    agents = ctx.agents()
    if rep.get("drift") and a.apply:
        names = set(rep["drift"])
        todo = [x for x in agents if x["name"] in names]
        ctx.out(f"drift: restarting {len(todo)} agent(s) that run a stale config: {', '.join(sorted(names))}")
        results = W.restart_and_verify(None, todo, restart=ctx.ops.restart, is_active=ctx.ops.is_active,
                                       running_env=ctx.ops.running_env, sleep=ctx.ops.sleep)
        bad = [r["name"] for r in results if r["restart_rc"] != 0 or r["active"] not in ("active", "skipped") or not r["running_env_ok"]]
        if bad:
            ctx.out(f"drift repair incomplete: {', '.join(bad)}")
            ctx.notify(NT.compose_failure("drift", ctx.host(), names=bad))
            rc = 3
        else:
            ctx.notify(NT.compose_repaired(sorted(names), ctx.host(), ctx.now))
    pending = ctx.load_state().get("pending_retry")
    if pending and a.apply and a.retry:
        since = None
        try:
            since = dt.datetime.fromisoformat(str(pending.get("since", "")).replace("Z", "+00:00"))
        except ValueError:
            pass
        if since is None or ctx.now - since > PENDING_MAX_AGE:
            ctx.out("pending retry is older than 24h: dropped")
            ctx.save_state(delete=("pending_retry",))
        else:
            res = do_retry(ctx, a.retry_hours, pending.get("from"), pending.get("to"), apply=True)
            rc = rc or finish_retry(ctx, res, pending.get("from"), pending.get("to"), True)
            notify_if_human_needed(ctx, res)
    return rc


# ───────────────────────────── switch ─────────────────────────────
def cmd_switch(ctx: Ctx, a) -> int:
    rep = build_report(ctx, False)  # history only: the timer runs every 5 minutes, a healthy tick must be free
    if a.probe and a.to == "auto" and rep["decision"].action != "none":
        rep = probe_candidates(ctx, rep)
    elif a.probe and a.to in rep["health"]:
        # An explicit target is verified before 22 agents are pointed at it — that target only, never the rest.
        target_profile = next(p for p in ctx.profiles if p.id == a.to)
        rep["health"][a.to] = merge_probe(ctx, target_profile, rep["health"][a.to])
    ctx.out(render(rep))
    if rep["current"] == "unknown":
        ctx.out("cannot identify the current harness (env files match no profile) — refusing to guess; check `profiles`")
        if a.apply:
            ctx.notify(NT.compose_failure("unidentified", ctx.host()))
        return 6
    by_id = {p.id: p for p in ctx.profiles}
    target = a.to
    if target == "auto":
        d = rep["decision"]
        if d.action == "none":
            ctx.out("→ 无需切换")
            return maintenance(ctx, a, rep)
        if d.action == "stuck":
            ctx.out("→ stuck: 当前 harness 已耗尽，且没有可用的备选，请人工介入")
            if a.apply:
                ctx.notify(NT.compose_exhausted(rep["current"], rep["health"][rep["current"]].until, None, rep["agents"], ctx.host()))
            return 2
        target = d.to
    elif target not in by_id:
        ctx.out(f"unknown profile: {target}")
        return 1
    elif rep["health"][target].status in ("exhausted", "unavailable") and not a.force:
        # Pointing 22 agents at a harness that cannot produce tokens is worse than staying where we are.
        h = rep["health"][target]
        why = "已耗尽" if h.status == "exhausted" else f"不可用（{h.reason}）"
        ctx.out(f"{target} {why}，拒绝切换（--force 可覆盖）")
        return 1
    switchable = [x for x in ctx.agents() if not x["pinned"]]
    if not a.apply:
        msg = f"[dry-run] would switch {len(switchable)} agents together to {target}; add --apply"
        if rep.get("pinned"):
            msg += f"; {len(rep['pinned'])} pinned agent(s) skipped: {', '.join(rep['pinned'])}"
        ctx.out(msg)
        return 0
    previous = ctx.load_state().get("last_switch", "")
    if W.too_soon(previous, ctx.now) and not a.force:
        ctx.out("skipped: last switch was too soon (< 20 min); use --force to override")
        return maintenance(ctx, a, rep)
    current_health = rep["health"].get(rep["current"])
    if current_health is not None and current_health.status == "exhausted":
        ctx.notify(NT.compose_exhausted(rep["current"], current_health.until, by_id[target], rep["agents"], ctx.host()))
    # Recorded BEFORE the restarts: a crash or kill mid-loop must not let the next run start a second full restart.
    ctx.save_state(last_switch=ctx.now.strftime(ISO), current=target, **{"from": rep["current"]})
    agents = switchable  # never a pinned agent: apply_switch rewrites every env file it is handed, no exceptions
    try:
        res = W.apply_switch(by_id[target], agents, launchers=ctx.launchers(), ts=ctx.now.strftime("%Y%m%d-%H%M%S"),
                             restart=ctx.ops.restart, is_active=ctx.ops.is_active, running_env=ctx.ops.running_env,
                             sleep=ctx.ops.sleep)
    except (envfile.EnvError, OSError) as e:
        ctx.save_state(last_switch=previous, current=rep["current"])  # nothing was restarted: do not block a retry
        ctx.out(f"switch aborted, nothing was restarted: {e}")
        ctx.notify(NT.compose_failure("aborted", ctx.host(), reason=str(e)))
        return 1
    ctx.save_state(backups=res.backups)
    if res.bad:
        ctx.out(f"switched to {target}, but {len(res.bad)} agent(s) are not healthy: " + ", ".join(b["name"] for b in res.bad)
                + " (the next run repairs stale ones)")
        ctx.notify(NT.compose_failure("partial", ctx.host(), names=[b["name"] for b in res.bad]))
        return 3
    msg = f"switched {len(res.agents)} agents to {target}: every process confirmed to load the new config"
    if rep.get("pinned"):
        msg += f"; {len(rep['pinned'])} pinned agent(s) skipped: {', '.join(rep['pinned'])}"
    ctx.out(msg)
    rc, res_retry, summary = 0, None, None
    try:
        if a.retry:
            res_retry = do_retry(ctx, a.retry_hours, rep["current"], target, apply=True)
            rc = finish_retry(ctx, res_retry, rep["current"], target, True)
            if res_retry is not None:
                summary = {"sent": res_retry["sent"], "manual": len(res_retry["manual"]), "errors": res_retry["errors"]}
    finally:  # the switch itself succeeded: the human must hear that even if the retry step blew up
        ctx.notify(NT.compose_switched(rep["current"], by_id[target], len(res.agents), summary, ctx.host(), ctx.now))
    notify_if_human_needed(ctx, res_retry)
    return rc


# ───────────────────────────── entry ─────────────────────────────
def _label(value: str) -> str:
    if R.safe_label(value) != value:
        raise argparse.ArgumentTypeError("must be a short label of [A-Za-z0-9._-] (max 32 chars)")
    return value


@contextlib.contextmanager
def run_lock(ctx: Ctx):
    """One mutating run at a time (timer, an operator, an agent). Yields False if another run holds the lock."""
    ctx.ensure_state_dir()
    fd = os.open(f"{ctx.state_dir}/lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)  # closing releases the lock


def main(argv=None, *, home=None, now=None, ops=None, buzz=None, out=print, notifier=None) -> int:
    home = home or os.path.expanduser("~")
    ap = argparse.ArgumentParser(prog="harness-failover", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="health of every profile + a recommendation (changes no agent)")
    d.add_argument("--probe", action="store_true")
    d.add_argument("--json", action="store_true")
    s = sub.add_parser("switch", help="switch ALL agents together (dry-run unless --apply)")
    s.add_argument("--to", default="auto")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--probe", action="store_true", help="when a switch is needed, verify the candidates with a real request first")
    s.add_argument("--force", action="store_true")
    s.add_argument("--retry", action="store_true", help="after a clean switch, nudge the agents to redo lost messages")
    s.add_argument("--retry-hours", type=float, default=6.0)
    r = sub.add_parser("retry", help="find messages lost to failures and @agent them to retry (dry-run unless --apply)")
    r.add_argument("--since-hours", type=float, default=6.0)
    r.add_argument("--apply", action="store_true")
    r.add_argument("--mode", choices=("mention", "any"), default="mention")
    r.add_argument("--from", dest="from_id", type=_label)
    r.add_argument("--to", dest="to_id", type=_label)
    ln = sub.add_parser("learn", help="unclassified errors seen in agent logs")
    ln.add_argument("--propose", action="store_true")
    ln.add_argument("--prune", action="store_true")
    sub.add_parser("profiles", help="list profiles")
    nt = sub.add_parser("notify", help="Lark notifications: --status (default), --setup, --test")
    grp = nt.add_mutually_exclusive_group()
    grp.add_argument("--status", action="store_true")
    grp.add_argument("--setup", action="store_true", help="read your open_id from lark-cli and enable notifications")
    grp.add_argument("--test", action="store_true", help="send ONE test message to you")
    a = ap.parse_args(argv)

    mutating = (a.cmd in ("switch", "retry") and a.apply) or (a.cmd == "learn" and a.prune)
    when = now or dt.datetime.now(UTC)
    try:
        ctx = Ctx(home, when, ops or SystemOps(home), buzz or BuzzCli(home=home), out, notifier)
    except Exception as e:
        if mutating:  # e.g. a bad profiles file: every tick would otherwise die silently
            report_startup_failure(home, when, out, notifier, e)
        raise
    if not mutating:
        return dispatch(ctx, a)
    previous = _install_sigterm()
    try:
        with run_lock(ctx) as got:
            if not got:
                ctx.out("busy: another harness-failover run holds the lock; skipping this one")
                return 0
            try:
                return dispatch(ctx, a)
            except Exception as e:
                ctx.notify(NT.compose_failure("error", ctx.host(), error_type=type(e).__name__))
                raise
    finally:
        _restore_sigterm(previous)


class Terminated(Exception):
    """SIGTERM (systemd stopping a run that hit TimeoutStartSec, or a shutdown)."""


def _install_sigterm():
    def handler(signum, frame):
        raise Terminated("SIGTERM")
    try:
        return signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread
        return None


def _restore_sigterm(previous) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def report_startup_failure(home, when, out, notifier, exc) -> None:
    """Notify about a crash that happened before a full Ctx could be built (state paths only)."""
    stub = Ctx.__new__(Ctx)
    stub.home, stub.now, stub.out = home, when, out
    stub.notifier = notifier or NT.LarkNotifier(home)
    stub.state_dir = f"{home}/.config/buzz/harness-failover"
    stub.state_path = f"{stub.state_dir}/state.json"
    stub.notify(NT.compose_failure("error", stub.host(), error_type=type(exc).__name__))


def cmd_notify(ctx: Ctx, a) -> int:
    if a.setup:
        try:
            cfg = ctx.notifier.setup()
        except NT.NotifyError as e:
            ctx.out(f"notify setup failed: {e}")
            return 1
        ctx.out(f"notifications enabled: {cfg['identity']} → ou_…{cfg['recipient_open_id'][-3:]} via {cfg['lark_cli']}")
        return 0
    if a.test:
        text = f"{NT.HEAD} 通知测试：这是一条测试消息，说明 harness-failover 能通过飞书通知你。\n主机：{NT._clean(ctx.host())}"
        ok, why = ctx.notifier.send(NT.Message("test", f"test:{ctx.now.strftime('%Y%m%dT%H%M%S')}", text, None))
        ctx.out("test message sent" if ok else f"test message NOT sent: {why or 'notifications are not configured'}")
        return 0 if ok else 1
    st = ctx.notifier.status()
    if st.get("enabled"):
        ctx.out(f"notifications: enabled ({st.get('identity')} → {st.get('recipient')} via {st.get('lark_cli')})")
    else:
        ctx.out(f"notifications: disabled — {st.get('reason') or st.get('problem') or 'run `notify --setup`'}")
    return 0


def dispatch(ctx: Ctx, a) -> int:
    if a.cmd == "detect":
        rep = build_report(ctx, a.probe)
        ctx.out(json.dumps(report_dict(rep), ensure_ascii=False, indent=1) if a.json else render(rep))
        n = len(learn.load_unknown(ctx.unknown_path))
        if n and not a.json:
            ctx.out(f"{n} unclassified error sample(s) seen in agent logs — run `harness-failover learn --propose` "
                    "and update the skill (references/self-update.md)")
        return 0
    if a.cmd == "switch":
        return cmd_switch(ctx, a)
    if a.cmd == "retry":
        st = ctx.load_state()
        from_id = a.from_id or R.safe_label(st.get("from"))
        to_id = a.to_id or R.safe_label(st.get("current"))
        res = do_retry(ctx, a.since_hours, from_id, to_id, a.apply, a.mode)
        rc = finish_retry(ctx, res, from_id, to_id, a.apply)
        if a.apply:
            notify_if_human_needed(ctx, res)
        return rc
    if a.cmd == "notify":
        return cmd_notify(ctx, a)
    if a.cmd == "learn":
        if a.prune:
            ctx.out(f"pruned {learn.prune_known(ctx.unknown_path, ctx.sigs)} entries now explained by a signature")
        entries = learn.load_unknown(ctx.unknown_path)
        ctx.out(learn.propose(entries) if a.propose else f"{len(entries)} unclassified error sample(s) recorded")
        return 0
    if a.cmd == "profiles":
        cur = collections.Counter(P.identify(x["vars"], ctx.profiles) for x in ctx.agents()).most_common(1)
        cur_id = cur[0][0] if cur else None
        for p in ctx.profiles:
            ctx.out(f"{p.id:12s} {p.harness:7s} model={p.model:12s} effort={p.effort:7s} priority={p.priority}"
                    + ("  (current)" if p.id == cur_id else ""))
        return 0
    return 1
