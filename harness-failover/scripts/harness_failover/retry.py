"""What failed (from agent logs), which messages were lost (from the relay), and asking the agent to retry.

Logs give channel + time window + count only — no event ids — so lost messages are reconstructed from the relay:
messages that mention the agent inside the window and have no later reply from it. If the count found is smaller
than the count the log says was lost, the result is flagged incomplete instead of silently returning less."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

from .buzzcli import BuzzCliError
from .detect import strip_ansi

UTC = dt.timezone.utc
MARKER = "[harness-failover retry]"
# Log lines are parsed with small anchored regexes, never `.*?` chains: a hostile line once made the old
# pattern backtrack super-linearly (26 KB -> 0.5 s, 220 KB -> minutes) and would have stalled the timer.
MAX_LINE = 4096
LINE_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z)\s")
REQ_MARK = "buzz_acp::queue: requeueing failed batch"
DEAD_MARK = "buzz_acp::queue: dead-lettering batch"
CHANNEL_ID = r"[0-9a-fA-F][0-9a-fA-F-]{7,63}"
CHANNEL_RE = re.compile(rf"^{CHANNEL_ID}$")
F_CHANNEL = re.compile(rf"\bchannel_id=({CHANNEL_ID})(?=\s|$)")
F_ATTEMPT = re.compile(r"\battempt=(\d+)")
F_EVENTS = re.compile(r"\bevents=(\d+)")
F_DISCARD = re.compile(r"\bdiscarding (\d+) events")
LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def safe_label(value) -> str:
    """Only short [A-Za-z0-9._-] labels may reach an owner-signed message; anything else becomes 'unknown'."""
    return value if isinstance(value, str) and LABEL_RE.fullmatch(value) else "unknown"


def allowed_authors(env_vars: dict):
    """Whose messages the agent's author gate forwards (buzz-acp --respond-to). None = anyone.
    Messages from anyone else never enter a batch, so they can never be 'lost' — nudging them would let the
    owner's identity vouch for text the agent deliberately ignores. Fails closed when it cannot tell."""
    mode = (env_vars.get("BUZZ_ACP_RESPOND_TO") or "owner-only").strip().lower()
    owner = (env_vars.get("BUZZ_ACP_AGENT_OWNER") or "").strip().lower()
    owners = {owner} if HEX64.match(owner) else set()
    if mode == "anyone":
        return None
    if mode == "owner-only":
        return owners
    if mode == "allowlist":
        listed = {x.strip().lower() for x in (env_vars.get("BUZZ_ACP_RESPOND_TO_ALLOWLIST") or "").split(",")}
        return owners | {x for x in listed if HEX64.match(x)}
    return set()  # nobody, or a mode we do not know


@dataclass
class Window:
    agent: str
    channel_id: str
    first_ts: object
    last_ts: object
    events: int
    dead_lettered: bool
    max_attempt: int = 0


@dataclass
class Recovery:
    window: Window
    candidates: list = field(default_factory=list)
    method: str = ""  # relay | unrecoverable
    complete: bool = False
    note: str = ""


def _ts(s: str):
    m = re.match(r"^(.*?)(\.\d+)?Z$", s)
    frac = (m.group(2) or "")[:7] if m else ""
    return dt.datetime.fromisoformat((m.group(1) if m else s) + frac + "+00:00")


def failure_windows(lines, agent="") -> list:
    open_w: dict[str, Window] = {}
    done: list[Window] = []
    for raw in lines:
        if len(raw) > MAX_LINE:
            continue
        line = strip_ansi(raw).rstrip("\n")
        ts_m = LINE_TS_RE.match(line)
        if not ts_m:
            continue
        chan = F_CHANNEL.search(line)
        if REQ_MARK in line and chan:
            attempt, events = F_ATTEMPT.search(line), F_EVENTS.search(line)
            if not (attempt and events):
                continue
            t, ch, n, ev = _ts(ts_m.group(1)), chan.group(1), int(attempt.group(1)), int(events.group(1))
            w = open_w.get(ch)
            if w is None or n == 1:
                if w is not None:
                    done.append(w)
                open_w[ch] = Window(agent, ch, t, t, ev, False, n)
            else:
                w.last_ts, w.events, w.max_attempt = t, max(w.events, ev), max(w.max_attempt, n)
        elif DEAD_MARK in line and chan:
            t, ch = _ts(ts_m.group(1)), chan.group(1)
            found = F_DISCARD.search(line) or F_EVENTS.search(line)
            w = open_w.pop(ch, None) or Window(agent, ch, t, t, 0, False, 0)
            w.last_ts, w.events, w.dead_lettered = t, max(w.events, int(found.group(1)) if found else 0), True
            done.append(w)
    done.extend(open_w.values())
    return sorted(done, key=lambda w: w.first_ts)


def _e_tags(ev: dict) -> set:
    return {t[1] for t in ev.get("tags", []) if len(t) > 1 and t[0] == "e"}


def _mentions(ev: dict, pubkey: str) -> bool:
    return any(len(t) > 1 and t[0] == "p" and t[1] == pubkey for t in ev.get("tags", []))


PAGE = 200  # how many events one relay read returns


def quota_windows(windows, quota_times, slack=60) -> list:
    """Retry only windows that are certainly a loss (dead-lettered) or that sit next to a quota error in the agent's
    own log; a transient blip that later succeeded must not be redone (the agent may have side effects)."""
    def near(w):
        lo, hi = w.first_ts.timestamp() - slack, w.last_ts.timestamp() + slack
        return any(lo <= t.timestamp() <= hi for t in quota_times if t)
    return [w for w in windows if w.dead_lettered or near(w)]


def unanswered(events, agent_pubkey, window, mode="mention", kinds=(9,), slack=90, allowed_authors=None) -> list:
    lo = int(window.first_ts.timestamp()) - slack
    hi = int(window.last_ts.timestamp())
    agent_events = [e for e in events if e.get("pubkey") == agent_pubkey]
    out = []
    for e in events:
        if e.get("kind") not in kinds or e.get("pubkey") == agent_pubkey:
            continue
        if not (lo <= e.get("created_at", 0) <= hi):
            continue
        if allowed_authors is not None and e.get("pubkey") not in allowed_authors:
            continue  # the agent's author gate never forwarded it, so it cannot be a lost message
        if str(e.get("content", "")).startswith(MARKER):
            continue  # our own retry nudge
        if mode == "mention" and not _mentions(e, agent_pubkey):
            continue
        refs = {e["id"]} | _e_tags(e)
        if any(a.get("created_at", 0) >= e["created_at"] and _e_tags(a) & refs for a in agent_events):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e["created_at"])


def recover(window, cli, agent_pubkey, mode="mention", allowed_authors=None) -> Recovery:
    if not CHANNEL_RE.match(window.channel_id or ""):
        return Recovery(window, [], "unrecoverable", False, "invalid channel id")
    since = int(window.first_ts.timestamp()) - 90
    try:
        events = cli.messages_get(window.channel_id, since, limit=PAGE, as_owner=True)
    except BuzzCliError as e:
        return Recovery(window, [], "unrecoverable", False, f"{e.category or e.code}: {e}")
    cands = unanswered(events, agent_pubkey, window, mode=mode, allowed_authors=allowed_authors)
    if len(events) >= PAGE:
        return Recovery(window, cands, "relay", False,
                        f"the relay returned a full page ({PAGE} events): the window may be truncated, older messages not checked")
    if window.dead_lettered and len(cands) < window.events:
        note = (f"log says {window.events} events were lost, found {len(cands)} ({len(cands)}/{window.events}) — "
                "possibly a private channel the owner cannot read, or messages that did not mention the agent")
        return Recovery(window, cands, "relay", False, note)
    return Recovery(window, cands, "relay", True, "")


def nudge_text(agent_name, from_id, to_id) -> str:
    """Fixed template. Never quotes the original message: channel text is untrusted input."""
    return (f"{MARKER} @{safe_label(agent_name)} 上面这条消息在 {safe_label(from_id)} 上处理失败，"
            f"已自动切换到 {safe_label(to_id)}。请重新处理这条消息。")


def load_state(path) -> dict:
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def save_state(path, state) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".state.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def send_nudges(recoveries, cli, agent_pubkeys, state, *, apply, from_id, to_id, now, max_per_run=10,
                max_age_hours=24, persist=None) -> dict:
    out = {"planned": 0, "sent": 0, "errors": 0, "skipped_already": 0, "skipped_cap": 0, "skipped_old": 0, "manual": []}
    for rec in recoveries:
        w = rec.window
        if rec.method == "unrecoverable" or not rec.complete:
            out["manual"].append({"agent": w.agent, "channel": w.channel_id, "events": w.events, "note": rec.note})
        pubkey = agent_pubkeys.get(w.agent)
        for e in rec.candidates:
            eid = e["id"]
            if eid in state:
                out["skipped_already"] += 1
                continue
            if now - dt.datetime.fromtimestamp(e["created_at"], UTC) > dt.timedelta(hours=max_age_hours):
                out["skipped_old"] += 1
                continue
            if out["planned"] >= max_per_run:
                out["skipped_cap"] += 1
                continue
            out["planned"] += 1
            if not apply:
                continue
            if not pubkey:
                out["errors"] += 1
                out["manual"].append({"agent": w.agent, "channel": w.channel_id, "events": w.events,
                                      "note": "no agent pubkey known (AGENT_PUBKEY_HEX missing in its env)"})
                continue
            try:
                cli.messages_send(w.channel_id, nudge_text(w.agent, from_id, to_id), reply_to=eid, mention=pubkey, as_owner=True)
            except BuzzCliError:
                out["errors"] += 1
                continue
            state[eid] = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "agent": w.agent, "channel": w.channel_id}
            out["sent"] += 1
            if persist:
                persist(state)  # a crash or timeout later in the run must not make us nudge the same message again
    return out
