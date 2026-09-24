#!/usr/bin/env python3
"""Treat a bot invite as a join request the agent owner approves in the channel (ADR-0018).

A `systemd --user` timer on the owner's host runs this script every 120 seconds under an owner-fixed env that
names a 0600 config (`BUZZ_JOIN_CONFIG`).  There is no LLM in this path.  For every configured agent that pins
`BUZZ_ACP_CHANNELS`, one run, acting as that agent (its own key from its own 0600 env, handed only to the Buzz
CLI child process):

  1. finds the channels it is a member of outside its allowlist, DMs excluded; the first round records every
     channel already there as BASELINE (and so does any later round for a channel put in the allowlist by hand),
     so nothing the owner arranged is touched.  A channel missing from one members read keeps its record until it
     has been gone for ABSENCE_GRACE_SECONDS;
  2. asks what explains the membership: the newest add/remove/join/leave event about the agent in that channel.
     Only an add (the agent in the first `p` tag) can explain it, and events closer than the relay's timestamp skew
     are judged together.  A removal/leave as the newest, or a self-join: recorded, nothing said.  An adder who is
     neither the owner nor a channel owner/admin: explained and left.  An add without `role=bot`: recorded, nothing
     said.  The agent owner alone: approved at once; otherwise a request posted in the channel as a new Thread saying
     what the agent can and cannot do there.  A later round reopens a decision only on newer events;
  3. counts only the agent owner's own signature, before the deadline: a ✅/❌ reaction on the request message, or a
     reply in its Thread whose first line is exactly `/approve JOIN-<id>` / `/deny JOIN-<id>`;
  4. on approval writes the prompt channel table (UUID, date and the owner's own repo names only) and the
     responsible helper config, then the env allowlist last; restarts the unit only while it is running and its
     cgroup holds nothing but the harness (never in the middle of a turn), and confirms `subscribed to channel <id>`
     in its log file or journal before announcing the channel as open.

One channel's failure is recorded against that channel and does not stop the others.
Every message is recorded as PENDING before it is sent and read back strictly; after a crash the last line
(`buzz-join:v1 JOIN-<id> …`) finds it again, so nothing is sent twice.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
import unicodedata
from typing import Any, Callable, Iterator, TextIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_sync as sync  # noqa: E402
import gitlab_buzz_route_reply as route  # noqa: E402 -- reviewed NIP-01 id + BIP-340 signature check
import buzz_feishu_group_sync as fgs  # noqa: E402 -- the relay query and the trusted-mirror rule (ADR-0020)


CONFIG_ENV = "BUZZ_JOIN_CONFIG"
STATE_FILE = "join-state.json"
HEADER = "buzz-join:v1"
FAILURE_HEADER = "buzz-join-failure:v1"
# Shared with provision_gitlab_agent_token.py so the two tools that rewrite agent env files serialise.
ENV_LOCK_NAME = ".gitlab-agent-token-provision.lock"
PROMPT_BEGIN = "<!-- buzz-agent-channels:v1 -->"
PROMPT_END = "<!-- /buzz-agent-channels:v1 -->"

CONFIG_KEYS = frozenset({"version", "owner_pubkey", "buzz", "state_dir", "request_ttl_seconds", "agents",
                         "accept_feishu_approvals"})
AGENT_KEYS = frozenset({"name", "env_file", "unit", "capabilities"})
AGENT_OPTIONAL_KEYS = frozenset({"log_file"})  # absent: the unit logs to the journal
CAPABILITY_KEYS = frozenset({"summary", "repos"})
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._-]{0,99}\.service")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
JOIN_ID_RE = r"JOIN-[0-9a-f]{8}"
APPROVE_RE = re.compile(rf"/approve ({JOIN_ID_RE})")
DENY_RE = re.compile(rf"/deny ({JOIN_ID_RE})")
CHANNELS_LINE = re.compile(r"(\s*(?:export\s+)?)BUZZ_ACP_CHANNELS=(.*)")
CANVAS_REPO_ROW = re.compile(r"\|\s*`(" + REPO_RE.pattern + r")`\s*\|")

APPROVE_EMOJIS = frozenset({"✅"})
DENY_EMOJIS = frozenset({"❌"})
VARIATION_SELECTOR = "\ufe0f"
HUMAN_ROLES = frozenset({"owner", "admin", "member"})
ADMIN_ROLES = frozenset({"owner", "admin"})

DEFAULT_TTL_SECONDS = 7 * 86400
TTL_MIN_SECONDS = 3600
TTL_MAX_SECONDS = 30 * 86400
CLOCK_SLACK_SECONDS = 300
INVITE_SCAN_SECONDS = 30 * 86400
# The relay accepts a client-set created_at within ±900 s of its own clock, so two invites closer than this cannot
# be ordered by timestamp: whoever sent the later one may have stamped it earlier.
INVITE_SKEW_SECONDS = 900
DM_LIST_LIMIT = 200
# A members list that forgets a channel for one round must not drop its record: absence counts only after this long.
ABSENCE_GRACE_SECONDS = 600
REPO_LIST_MAX = 10
CANVAS_ROWS_MAX = 500
MEMBERSHIP_KINDS = "9000,9001,9021,9022"  # add, remove, join request, leave request (NIP-29)
SYSTEMCTL = "/usr/bin/systemctl"
JOURNALCTL = "/usr/bin/journalctl"
CURSOR_RE = re.compile(r"[A-Za-z0-9=;._-]{1,512}")
SYSTEMD_ENV_KEYS = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
# An env that loses the channel again after we re-applied it this many times is someone else's writer: stop restarting.
REAPPLY_MAX = 2
VERIFY_TIMEOUT_SECONDS = 60
VERIFY_POLL_SECONDS = 2
LOG_READ_MAX = 4 * 1024 * 1024
SUMMARY_MAX = 200
NAME_MAX = 60

TERMINAL = frozenset({"BASELINE", "ACTIVE", "LEFT", "WITHDRAWN", "NO_INVITE"})
REOPENABLE = frozenset({"LEFT", "WITHDRAWN", "NO_INVITE"})  # a newer invite starts a new request
STATES = TERMINAL | {"REQUESTED", "APPROVED", "APPLIED", "RESTARTED", "CLOSING"}


class JoinLocked(sync.SyncError):
    """Another run already holds the state directory."""


# ── configuration ────────────────────────────────────────────────────────────


def _hex64(value: Any, name: str) -> str:
    if not isinstance(value, str) or not sync.HEX64_RE.fullmatch(value):
        raise sync.SyncError(f"{name} must be a lowercase 64-character hex pubkey")
    return value


def _absolute(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise sync.SyncError(f"{name} must be an absolute path")
    return value


def _one_line(value: Any, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and not any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in value))


def validate_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise sync.SyncError("config must be a JSON object")
    unknown = sorted(str(key) for key in set(config) - CONFIG_KEYS)
    if unknown:
        raise sync.SyncError(f"config has unknown keys: {', '.join(unknown)}")
    if config.get("version") != 1:
        raise sync.SyncError("config.version must be 1")
    _hex64(config.get("owner_pubkey"), "config.owner_pubkey")
    buzz = config.get("buzz")
    if not isinstance(buzz, dict) or set(buzz) != {"cli_path", "cli_sha256"}:
        raise sync.SyncError("config.buzz must have exactly cli_path and cli_sha256")
    _absolute(buzz["cli_path"], "config.buzz.cli_path")
    if not isinstance(buzz["cli_sha256"], str) or not sync.HEX64_RE.fullmatch(buzz["cli_sha256"]):
        raise sync.SyncError("config.buzz.cli_sha256 must be a lowercase SHA-256 digest")
    _absolute(config.get("state_dir"), "config.state_dir")
    ttl = config.get("request_ttl_seconds", DEFAULT_TTL_SECONDS)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS:
        raise sync.SyncError(f"config.request_ttl_seconds must be {TTL_MIN_SECONDS}..{TTL_MAX_SECONDS}")
    if not isinstance(config.get("accept_feishu_approvals", True), bool):
        raise sync.SyncError("config.accept_feishu_approvals must be true or false")
    agents = config.get("agents")
    if not isinstance(agents, list) or not agents:
        raise sync.SyncError("config.agents must be a non-empty list")
    names: set[str] = set()
    for agent in agents:
        _validate_agent(agent)
        if agent["name"] in names:
            raise sync.SyncError(f"config.agents repeats {agent['name']}")
        names.add(agent["name"])


def _validate_agent(agent: Any) -> None:
    if not isinstance(agent, dict) or not AGENT_KEYS <= set(agent) <= AGENT_KEYS | AGENT_OPTIONAL_KEYS:
        raise sync.SyncError(f"config.agents[] must have {', '.join(sorted(AGENT_KEYS))} and optionally log_file")
    if not isinstance(agent["name"], str) or not NAME_RE.fullmatch(agent["name"]):
        raise sync.SyncError("config.agents[].name must be lowercase letters, digits and hyphens")
    _absolute(agent["env_file"], "config.agents[].env_file")
    if "log_file" in agent:
        _absolute(agent["log_file"], "config.agents[].log_file")
    if not isinstance(agent["unit"], str) or not UNIT_RE.fullmatch(agent["unit"]):
        raise sync.SyncError("config.agents[].unit must be a systemd unit name ending in .service")
    caps = agent["capabilities"]
    if not isinstance(caps, dict) or set(caps) != CAPABILITY_KEYS:
        raise sync.SyncError("config.agents[].capabilities must have exactly summary and repos")
    if not _one_line(caps["summary"], SUMMARY_MAX):
        raise sync.SyncError(f"config.agents[].capabilities.summary must be one line of at most {SUMMARY_MAX} characters")
    repos = caps["repos"]
    if (not isinstance(repos, list) or len(repos) > 50 or len({str(r).lower() for r in repos}) != len(repos)
            or any(not isinstance(r, str) or not REPO_RE.fullmatch(r) for r in repos)):
        raise sync.SyncError("config.agents[].capabilities.repos must be unique GitLab project paths")


def _read_owner_only(path: Path, what: str, *, mode: int | None = 0o600) -> str:
    """A regular, non-symlink file owned by this user (and with exactly `mode` when given)."""

    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None
            text = stream.read()
    except (OSError, UnicodeError):
        raise sync.SyncError(f"cannot read {what}") from None
    finally:
        if fd is not None:
            os.close(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise sync.SyncError(f"{what} must be a regular file owned by this user")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise sync.SyncError(f"{what} must have mode {mode:04o}")
    return text


def load_config(path: Path) -> dict[str, Any]:
    text = _read_owner_only(Path(path), "config")
    try:
        config = json.loads(text)
    except (ValueError, RecursionError):
        raise sync.SyncError("config is not valid JSON") from None
    validate_config(config)
    return config


# ── the agent's own env ──────────────────────────────────────────────────────


def parse_env(text: str) -> dict[str, str]:
    """`KEY=value` lines as a shell would read them for these files: `export`, comments and one level of quotes."""

    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def read_env_file(path: Path) -> dict[str, str]:
    return parse_env(_read_owner_only(Path(path), "agent env file"))


def _allowlist(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if any(not UUID_RE.fullmatch(item) for item in items):
        raise sync.SyncError("BUZZ_ACP_CHANNELS must list lowercase channel UUIDs")
    return items


class Agent:
    """One managed agent as read from the owner config and its own env file."""

    def __init__(self, *, name: str, pubkey: str, env_file: Path, env_text: str, env: dict[str, str],
                 allowlist: list[str], unit: str, log_file: Path | None, capabilities: dict[str, Any],
                 responsible_config: Path | None, prompt_file: Path | None):
        self.name = name
        self.pubkey = pubkey
        self.env_file = env_file
        self.env_text = env_text  # what the last read or write saw: an env edit must start from exactly this
        self.env = env
        self.allowlist = allowlist
        self.unit = unit
        self.log_file = log_file
        self.capabilities = capabilities
        self.responsible_config = responsible_config
        self.prompt_file = prompt_file


def load_agent(agent_config: dict[str, Any], owner_pubkey: str) -> Agent:
    name = agent_config["name"]
    env_file = Path(agent_config["env_file"])
    env_text = _read_owner_only(env_file, "agent env file")
    env = parse_env(env_text)
    pubkey = sync.publisher_pubkey_from_private_key(env.get("BUZZ_PRIVATE_KEY"))
    if env.get("BUZZ_ACP_AGENT_OWNER") != owner_pubkey:
        raise sync.SyncError(f"{name}: BUZZ_ACP_AGENT_OWNER is not the configured owner")
    allowlist = _allowlist(env.get("BUZZ_ACP_CHANNELS", ""))
    if not allowlist:
        raise sync.SyncError(f"{name}: no BUZZ_ACP_CHANNELS allowlist; agents without one are out of scope "
                             "(set their channel_add_policy to owner_only)")

    def optional_path(key: str) -> Path | None:
        value = env.get(key)
        return Path(_absolute(value, f"{name}: {key}")) if value else None

    return Agent(name=name, pubkey=pubkey, env_file=env_file, env_text=env_text, env=env, allowlist=allowlist,
                 unit=agent_config["unit"],
                 log_file=Path(agent_config["log_file"]) if agent_config.get("log_file") else None,
                 capabilities=agent_config["capabilities"],
                 responsible_config=optional_path("BUZZ_RESPONSIBLE_CONFIG"),
                 prompt_file=optional_path("BUZZ_ACP_SYSTEM_PROMPT_FILE"))


def load_agent_for_notice(agent_config: dict[str, Any]) -> Agent:
    """Recover only enough signed identity to publish a fixed configuration alert.

    This does not make the invalid configuration runnable: the returned object is used only for safe group notices.
    """

    env_file = Path(agent_config["env_file"])
    env_text = _read_owner_only(env_file, "agent env file")
    env = parse_env(env_text)
    pubkey = sync.publisher_pubkey_from_private_key(env.get("BUZZ_PRIVATE_KEY"))
    try:
        allowlist = _allowlist(env.get("BUZZ_ACP_CHANNELS", ""))
    except sync.SyncError:
        allowlist = []
    return Agent(
        name=agent_config["name"], pubkey=pubkey, env_file=env_file, env_text=env_text, env=env,
        allowlist=allowlist, unit=agent_config["unit"],
        log_file=Path(agent_config["log_file"]) if agent_config.get("log_file") else None,
        capabilities=agent_config["capabilities"], responsible_config=None, prompt_file=None,
    )


# ── file edits (all idempotent, compare-before-write, atomic) ─────────────────


@contextlib.contextmanager
def env_dir_lock(env_file: Path) -> Iterator[None]:
    parent = Path(env_file).parent
    try:
        metadata = parent.lstat()
    except OSError:
        raise sync.SyncError("agent env directory is missing") from None
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022):
        raise sync.SyncError("agent env directory must be a real directory owned by this user, not group/world writable")
    fd = os.open(parent / ENV_LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lock_metadata = os.fstat(fd)
        if (not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600):
            raise sync.SyncError("agent env lock must be a 0600 regular file owned by this user")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise sync.SyncError("another tool is rewriting agent env files; retry next round") from None
        yield
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def add_channel_to_env(env_file: Path, channel: str, *, expected: str | None = None) -> str:
    """Append `channel` to the single `BUZZ_ACP_CHANNELS=` line, keeping its quoting; return the file as written.

    `expected` is the content the caller last read: anything else on disk means another writer got there first.
    The caller holds `env_dir_lock`.
    """

    env_file = Path(env_file)
    content = _read_owner_only(env_file, "agent env file")
    if expected is not None and content != expected:
        raise sync.SyncError("agent env file changed since it was read; refusing to overwrite it")
    lines = content.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if CHANNELS_LINE.fullmatch(line.rstrip("\r\n"))]
    if len(matches) != 1:
        raise sync.SyncError("agent env file must contain exactly one BUZZ_ACP_CHANNELS line")
    index = matches[0]
    line = lines[index]
    ending = line[len(line.rstrip("\r\n")):]
    match = CHANNELS_LINE.fullmatch(line.rstrip("\r\n"))
    prefix, raw = match.group(1), match.group(2).strip()
    quote = raw[0] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else ""
    items = _allowlist(raw[1:-1] if quote else raw)
    if channel in items:
        return content
    lines[index] = f"{prefix}BUZZ_ACP_CHANNELS={quote}{','.join(items + [channel])}{quote}{ending}"
    updated = "".join(lines)
    _atomic_write(env_file, updated, 0o600)
    return updated


def add_channel_to_responsible(path: Path, channel: str) -> None:
    try:
        config = json.loads(_read_owner_only(Path(path), "responsible helper config"))
    except (ValueError, RecursionError):
        raise sync.SyncError("responsible helper config is not valid JSON") from None
    channels = config.get("channels") if isinstance(config, dict) else None
    if not isinstance(channels, list) or any(not isinstance(c, str) or not UUID_RE.fullmatch(c) for c in channels):
        raise sync.SyncError("responsible helper config must have a channels list of UUIDs")
    if channel in channels:
        return
    channels.append(channel)
    _atomic_write(Path(path), json.dumps(config, ensure_ascii=False, indent=2) + "\n", 0o600)


def add_channel_to_prompt(path: Path, channel: str, row: str) -> bool:
    """Insert `row` just before the end marker.  False (nothing written) when the prompt has no single marker pair."""

    path = Path(path)
    text = _read_owner_only(path, "agent prompt", mode=None)
    if text.count(PROMPT_BEGIN) != 1 or text.count(PROMPT_END) != 1:
        return False
    begin, end = text.index(PROMPT_BEGIN), text.index(PROMPT_END)
    if end < begin:
        return False
    if channel in text[begin:end]:
        return True
    head = text[:end]
    if not head.endswith("\n"):
        head += "\n"
    mode = stat.S_IMODE(path.lstat().st_mode)
    _atomic_write(path, head + row + "\n" + text[end:], mode)
    return True


# ── untrusted text and message rendering ─────────────────────────────────────


NEUTRALISE = str.maketrans({"@": "＠", "<": "＜", ">": "＞", "[": "［", "]": "］", "`": "｀", "|": "｜"})
NOSTR_RE = re.compile(r"nostr:", re.IGNORECASE)
HEADER_WORD_RE = re.compile(r"buzz-join", re.IGNORECASE)


def clean(text: Any, limit: int) -> str:
    """One line, no control/format characters, no mentions, markdown links, `nostr:` references or header words."""

    kept = "".join(ch if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Cn"} else " "
                   for ch in str(text or ""))
    flat = " ".join(kept.translate(NEUTRALISE).replace("://", "：／／").split())
    flat = NOSTR_RE.sub(lambda m: m.group(0)[:-1] + "：", flat)
    flat = HEADER_WORD_RE.sub(lambda m: m.group(0).replace("-", "\u2011"), flat)
    return flat[:limit]


def canvas_repos(text: str | None) -> list[str] | None:
    """Repository paths from the Canvas `## 代码仓库` table; None when the Canvas has no such list."""

    if not text:
        return None
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## 代码仓库")
    except StopIteration:
        return None
    repos: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        match = CANVAS_REPO_ROW.match(line.strip())
        if match and match.group(1) not in repos:
            repos.append(match.group(1))
            if len(repos) >= CANVAS_ROWS_MAX:
                break
    return repos or None


def capability(capabilities: dict[str, Any], canvas_text: str | None) -> dict[str, Any]:
    listed = canvas_repos(canvas_text)
    if listed is None:
        return {"has_list": False, "covered": [], "missing": []}
    mine = {repo.lower(): repo for repo in capabilities["repos"]}
    return {"has_list": True,
            "covered": [mine[repo.lower()] for repo in listed if repo.lower() in mine],  # the owner's spelling
            "missing": [repo for repo in listed if repo.lower() not in mine]}


def _repo_list(repos: list[str]) -> str:
    shown = "、".join(f"`{repo}`" for repo in repos[:REPO_LIST_MAX])
    return shown + (f" 等 {len(repos)} 个" if len(repos) > REPO_LIST_MAX else "")


def capability_lines(agent: Agent, cap: dict[str, Any]) -> list[str]:
    lines = [f"我能做：{clean(agent.capabilities['summary'], SUMMARY_MAX)}"]
    if not cap["has_list"]:
        lines.append("本群 Canvas 没有「代码仓库」清单；遇到我没有权限的请求，我会说明并停下。")
    else:
        covered = _repo_list(cap["covered"]) or "没有"
        missing = (f"没有权限的：{_repo_list(cap['missing'])}（这类请求我会说明缺权限并停下）"
                   if cap["missing"] else "没有权限的：无")
        lines.append(f"本群 Canvas 列出的仓库里，我有权限的：{covered}；{missing}。")
    return lines


def prompt_row(channel: str, cap: dict[str, Any], join_id: str, when: float) -> str:
    """Only the UUID, the date and repo names from the owner's config: nothing a channel member wrote reaches the prompt."""

    day = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%d")
    if not cap["has_list"]:
        scope = "该频道 Canvas 没有代码仓库清单"
    else:
        covered = "、".join(f"`{repo}`" for repo in cap["covered"]) or "没有"
        scope = f"你在这里有权限的仓库：{covered}；该频道其它仓库你没有权限"
    return (f"| — | `{channel}` | {day} 经 owner 同意加入（{join_id}）。{scope}。"
            "缺权限的请求如实说明并停下，不借别的凭据。 |")


def _ttl_text(ttl: int) -> str:
    if ttl % 86400 == 0:
        return f"{ttl // 86400} 天"
    if ttl % 3600 == 0:
        return f"{ttl // 3600} 小时"
    return f"{-(-ttl // 60)} 分钟"


def join_header(join_id: str, kind: str = "") -> str:
    return f"{HEADER} {join_id}" + (f" {kind}" if kind else "")


def make_join_id(agent_pubkey: str, channel: str, basis: str) -> str:
    return "JOIN-" + hashlib.sha256(f"{agent_pubkey}|{channel}|{basis}".encode("utf-8")).hexdigest()[:8]


# ── state ────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def state_lock(state_dir: Path) -> Iterator[None]:
    sync.prepare_private_dir(Path(state_dir), "join state dir")
    fd = os.open(Path(state_dir) / "join.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JoinLocked("join requests are locked") from None
        yield
    finally:
        os.close(fd)


def load_state(state_dir: Path) -> dict[str, Any]:
    """A state file that does not parse stops the run: resetting it would re-ask every open request."""

    path = Path(state_dir) / STATE_FILE
    if not path.exists():
        return {"version": 1, "agents": {}}
    try:
        state = json.loads(_read_owner_only(path, "join state"))
    except (ValueError, RecursionError):
        raise sync.SyncError("join state is not valid JSON") from None
    agents = state.get("agents") if isinstance(state, dict) else None
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(agents, dict):
        raise sync.SyncError("join state has an unexpected shape")
    for pubkey, entry in agents.items():
        notices = entry.get("failure_notices", {}) if isinstance(entry, dict) else None
        notices_good = isinstance(notices, dict) and all(
            UUID_RE.fullmatch(str(ch))
            and isinstance(notice, dict)
            and isinstance(notice.get("code"), str)
            and notice.get("code") in {"unexplained", "discovery", "directory", "mirror", "processing",
                                       "activation", "configuration"}
            and _is_int(notice.get("at"))
            and (notice.get("incident_id") is None
                 or bool(re.fullmatch(r"[0-9a-f]{16}", str(notice.get("incident_id")))))
            and all(notice.get(key) is None or _is_int(notice.get(key))
                    for key in ("pending_at", "recovery_pending_at"))
            and all(notice.get(key) is None or bool(sync.HEX64_RE.fullmatch(str(notice.get(key))))
                    for key in ("event", "recovery_event"))
            and isinstance(notice.get("recovering", False), bool)
            for ch, notice in notices.items()
        )
        if (not sync.HEX64_RE.fullmatch(str(pubkey)) or not isinstance(entry, dict)
                or not isinstance(entry.get("channels"), dict)
                or any(not UUID_RE.fullmatch(str(ch)) or not isinstance(rec, dict) or rec.get("state") not in STATES
                       for ch, rec in entry["channels"].items())
                or not notices_good):
            raise sync.SyncError("join state has an invalid record")
    return state


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    sync.atomic_write_json(Path(state_dir) / STATE_FILE, state)


def new_record(state_name: str, channel_name: str, now: float) -> dict[str, Any]:
    return {"state": state_name, "name": channel_name, "seen_at": int(now), "join_id": None, "inviter": None,
            "invite_event": None, "outcome": None, "request_event": None, "requested_at": None,
            "owner_notified": False, "pending_send": None, "capability": None, "decided_at": None,
            "followups": [], "restart_deferred_since": None, "restarted_at": None, "log_offset": None,
            "closed_event": None, "active_event": None, "invite_at": None, "absent_since": None,
            "withdrawn_from": None, "reapplied": 0, "journal_cursor": None}


def failure_header(agent_pubkey: str, channel: str, code: str, incident_id: str, phase: str = "failed") -> str:
    identity = hashlib.sha256(
        f"{agent_pubkey}|{channel}|{code}|{incident_id}|{phase}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{FAILURE_HEADER} {identity} {code} {phase}"


# ── adapters ─────────────────────────────────────────────────────────────────


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _e_targets(event: dict[str, Any]) -> set[str]:
    return {tag[1] for tag in sync._tag_values(event, "e") if len(tag) > 1 and isinstance(tag[1], str)
            and sync.HEX64_RE.fullmatch(tag[1])}


def authentic(event: Any) -> bool:
    """The id is the NIP-01 hash of the event and the signature verifies against its pubkey (the relay is not trusted
    to have checked)."""

    if not isinstance(event, dict):
        return False
    try:
        route.verify_nostr_event_signature(event, label="event")
    except (route.RouteError, KeyError, TypeError, ValueError):
        return False
    return True


class AgentBuzz(sync.BuzzCli):
    """The pinned Buzz CLI acting as one agent.  The CLI path is validated by `make_agent_buzz`, not here."""

    def __init__(self, cli: str, env: dict[str, str], *, base_env: dict[str, str] | None = None,
                 runner: Any = subprocess.run, sleeper: Any = time.sleep, channel: str | None = None, http: Any = None):
        # HOME, PATH, locale, CA and proxy settings come only from the timer; BUZZ_* identity keys only from the agent.
        merged = {key: value for key, value in (base_env or {}).items() if not key.startswith("BUZZ_")}
        merged.update({key: value for key, value in env.items() if key.startswith("BUZZ_")})
        sync.validate_relay_url(merged.get("BUZZ_RELAY_URL"))
        self.cli = cli
        self.env = sync.child_env(merged, "GITLAB_TOKEN")
        self.publisher = sync.publisher_pubkey_from_private_key(merged.get("BUZZ_PRIVATE_KEY"))
        self.channel = channel
        self.runner = runner
        self.sleeper = sleeper
        self.http = http or fgs._http_get  # the relay's POST /query, signed in this process (trusted_mirrors)

    def for_channel(self, channel: str) -> "AgentBuzz":
        if not UUID_RE.fullmatch(channel):
            raise sync.SyncError("channel id must be a lowercase UUID")
        scoped = copy.copy(self)
        scoped.channel = channel
        return scoped

    def _raw(self, args: list[str]) -> str:
        try:
            result = self.runner([self.cli, *args], capture_output=True, text=True, timeout=45, check=False,
                                 env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise sync.SyncError(f"Buzz CLI {' '.join(args[:2])} could not run: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise sync.BuzzCliError(" ".join(args[:2]), result.returncode)
        return result.stdout

    def member_channels(self) -> dict[str, str]:
        value = self.command(["channels", "list", "--member"])
        rows = value if isinstance(value, list) else value.get("channels") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise sync.SyncError("Buzz channels list did not return a list")
        found: dict[str, str] = {}
        for row in rows:
            channel = row.get("channel_id") if isinstance(row, dict) else None
            if not isinstance(channel, str) or not UUID_RE.fullmatch(channel):
                raise sync.SyncError("Buzz channels list returned an invalid channel")
            found[channel] = str(row.get("name") or "")
        return found

    def dm_channels(self) -> set[str]:
        """A DM is a channel too (relay-v0.2.1 emits its NIP-29 member event), so `channels list --member` shows it."""

        value = self.command(["dms", "list", "--limit", str(DM_LIST_LIMIT)])
        if not isinstance(value, list):
            raise sync.SyncError("Buzz dms list did not return a list")
        return {row["dm_id"] for row in value
                if isinstance(row, dict) and isinstance(row.get("dm_id"), str) and UUID_RE.fullmatch(row["dm_id"])}

    def _scan(self, kinds: str, since: int) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(sync.CHANNEL_PAGE_MAX):
            args = ["messages", "get", "--channel", self.channel, "--kinds", kinds, "--since", str(int(since)),
                    "--limit", str(sync.CHANNEL_PAGE_LIMIT)]
            if before is not None:
                args += ["--before", str(before)]
            page = sync._collect_events(self.command(args))
            fresh = [event for event in page if str(event["id"]) not in found]
            for event in fresh:
                found[str(event["id"])] = event
            if len(page) < sync.CHANNEL_PAGE_LIMIT:
                break
            if not fresh:
                raise sync.SyncError("Buzz scan found more same-second events than one page holds")
            times = [event.get("created_at") for event in page]
            if not all(_is_int(value) and value > 0 for value in times):
                raise sync.SyncError("Buzz events have no created_at to page by")
            before = min(times)
        else:
            raise sync.SyncError("Buzz scan exceeded its page limit")
        return list(found.values())

    def membership_events(self, since: int) -> list[dict[str, Any]]:
        """Every add/remove/join/leave event of this channel since `since`; the caller picks the ones about the agent."""

        return [event for event in self._scan(MEMBERSHIP_KINDS, since)
                if event.get("kind") in (9000, 9001, 9021, 9022)
                and [tag[:2] for tag in sync._tag_values(event, "h")] == [["h", self.channel]]]

    def channel_reactions(self, since: int) -> list[dict[str, Any]]:
        return [event for event in self._scan("7", since) if event.get("kind") == 7]

    def canvas(self) -> str | None:
        return self._raw(["canvas", "get", "--channel", self.channel]) or None

    def leave(self) -> None:
        self._raw(["channels", "leave", "--channel", self.channel])

    def trusted_mirrors(self, candidates: set[str], roles: dict[str, str]) -> set[str] | None:
        """The candidates whose word about a Feishu person counts in this channel (ADR-0020, fgs.parse_trusted_mirrors): one
        POST /query as this agent (its NIP-OA auth tag with it) for their profiles and policies. ``None`` means the
        directory could not be verified; that is not evidence that a mirror is untrusted."""
        bots = {pubkey for pubkey in candidates if roles.get(pubkey) == "bot"}
        if not bots:
            return set()
        try:
            key = fgs.secret_hex(self.env.get("BUZZ_PRIVATE_KEY", ""), "agent env")
            url = fgs.relay_query_url(self.env.get("BUZZ_RELAY_URL", ""))
            body = json.dumps(fgs.directory_filters(bots), separators=(",", ":")).encode()
            headers = {"Authorization": fgs.nip98_header(key, "POST", url, dt.datetime.now(dt.timezone.utc), body=body),
                       "Content-Type": "application/json", "Accept": "application/json"}
            if self.env.get("BUZZ_AUTH_TAG"):
                headers["x-auth-tag"] = self.env["BUZZ_AUTH_TAG"]
            status, answer = self.http(url, headers, fgs.DIRECTORY_TIMEOUT, body=body)
            events = json.loads(answer) if status == 200 else None
        except (OSError, ValueError, fgs.GroupSyncError):
            return None
        if not isinstance(events, list):
            return None
        verified = [event for event in events if fgs._nip01_event_verified(event)]
        return fgs.parse_trusted_mirrors(verified, bots, roles)


def make_agent_buzz_factory(config: dict[str, Any]) -> Callable[[Agent], AgentBuzz]:
    """Validates the pinned CLI (path, ELF, owner, digest) once per run, then builds one adapter per agent."""

    cli: list[str] = []

    def factory(agent: Agent) -> AgentBuzz:
        if not cli:
            cli.append(str(sync.validate_buzz_cli_path(config["buzz"]["cli_path"], config["buzz"]["cli_sha256"])))
        return AgentBuzz(cli[0], agent.env, base_env=dict(os.environ))

    return factory


class SystemOps:
    """systemd --user, the unit's cgroup and the agent's log (an append-only file, or the journal)."""

    def __init__(self, runner: Any = subprocess.run, *, base_env: dict[str, str] | None = None,
                 cgroup_root: Path = Path("/sys/fs/cgroup")):
        self.runner = runner
        source = os.environ if base_env is None else base_env
        self.env = {key: source[key] for key in SYSTEMD_ENV_KEYS if key in source}
        self.cgroup_root = Path(cgroup_root)

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        try:
            return self.runner(argv, capture_output=True, text=True, timeout=120, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise sync.SyncError(f"{Path(argv[0]).name} {argv[2] if len(argv) > 2 else ''} could not run: "
                                 f"{type(exc).__name__}") from None

    def _systemctl(self, verb: str, unit: str, *extra: str) -> subprocess.CompletedProcess:
        if not UNIT_RE.fullmatch(unit):
            raise sync.SyncError("invalid systemd unit name")
        return self._run([SYSTEMCTL, "--user", verb, unit, *extra])

    def restart(self, unit: str) -> None:
        if self._systemctl("restart", unit).returncode != 0:
            raise sync.SyncError(f"systemctl --user restart {unit} failed")

    def is_active(self, unit: str) -> bool:
        result = self._systemctl("is-active", unit)
        return result.returncode == 0 and str(result.stdout).strip() == "active"

    def unit_status(self, unit: str) -> tuple[str, str]:
        result = self._systemctl("show", unit, "-p", "LoadState", "-p", "ActiveState")
        values = dict(line.split("=", 1) for line in str(result.stdout).splitlines() if "=" in line)
        return values.get("LoadState", "unknown"), values.get("ActiveState", "unknown")

    def is_busy(self, unit: str) -> bool:
        """Anything beside the harness process in the unit's cgroup (an ACP agent: a turn, or a warm pool).

        The harness logs no line per turn, and a turn can be silent for many minutes, so the log cannot tell.  Any
        doubt counts as busy: a restart would cut a turn.
        """

        result = self._systemctl("show", unit, "-p", "ControlGroup", "--value")
        group = str(result.stdout).strip()
        if result.returncode != 0 or not group.startswith("/") or ".." in Path(group).parts:
            return True
        try:
            procs = (self.cgroup_root / group.lstrip("/") / "cgroup.procs").read_text(encoding="utf-8")
        except OSError:
            return True
        return len([line for line in procs.splitlines() if line.strip()]) != 1

    def journal_cursor(self, unit: str) -> str | None:
        """The journal position of the unit before a restart; None when it has no entries yet."""

        if not UNIT_RE.fullmatch(unit):
            raise sync.SyncError("invalid systemd unit name")
        result = self._run([JOURNALCTL, "--user", "-u", unit, "-n", "0", "--show-cursor", "--no-pager", "-o", "cat"])
        if result.returncode != 0:
            raise sync.SyncError(f"journalctl for {unit} failed")
        for line in str(result.stdout).splitlines():
            if line.startswith("-- cursor: "):
                cursor = line[len("-- cursor: "):].strip()
                if not CURSOR_RE.fullmatch(cursor):
                    raise sync.SyncError(f"journalctl for {unit} returned an unexpected cursor")
                return cursor
        return None

    def journal_after(self, unit: str, cursor: str | None) -> str:
        if not UNIT_RE.fullmatch(unit) or (cursor is not None and not CURSOR_RE.fullmatch(cursor)):
            raise sync.SyncError("invalid systemd unit name or journal cursor")
        argv = [JOURNALCTL, "--user", "-u", unit, "-o", "cat", "--no-pager"]
        if cursor is not None:
            argv += ["--after-cursor", cursor]
        result = self._run(argv)
        if result.returncode != 0:
            raise sync.SyncError(f"journalctl for {unit} failed")
        return str(result.stdout)[-LOG_READ_MAX:]

    def log_size(self, path: Path) -> int:
        try:
            return os.stat(path).st_size
        except FileNotFoundError:
            return 0

    def log_since(self, path: Path, offset: int) -> str:
        try:
            with open(path, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                handle.seek(offset if 0 <= offset <= size else 0)  # a truncated log restarts from the top
                return handle.read(LOG_READ_MAX).decode("utf-8", "replace")
        except FileNotFoundError:
            return ""


# ── one agent ────────────────────────────────────────────────────────────────


class AgentRun:
    def __init__(self, agent: Agent, buzz: Any, entry: dict[str, Any], config: dict[str, Any], system: Any,
                 clock: Callable[[], float], sleeper: Callable[[float], None], save: Callable[[], None]):
        self.agent = agent
        self.buzz = buzz
        self.entry = entry
        self.channels: dict[str, dict[str, Any]] = entry["channels"]
        self.notices: dict[str, dict[str, Any]] = entry.setdefault("failure_notices", {})
        for notice in self.notices.values():
            if isinstance(notice, dict):
                notice.setdefault("incident_id", secrets.token_hex(8))
                notice.setdefault("recovering", False)
                notice.setdefault("recovery_pending_at", None)
                notice.setdefault("recovery_event", None)
        self.owner = config["owner_pubkey"]
        self.ttl = config.get("request_ttl_seconds", DEFAULT_TTL_SECONDS)
        self.accept_feishu = config.get("accept_feishu_approvals", True)  # ADR-0020: answers given in Feishu, via a trusted mirror
        self.system = system
        self.clock = clock
        self.sleeper = sleeper
        self.save = save
        self.counts: dict[str, Any] = {"baseline": 0, "requested": 0, "approved": 0, "manual": 0, "active": 0,
                                       "left": 0, "deferred": 0, "withdrawn": 0, "drift": 0, "error": None}
        self.errors: list[str] = []
        self.failed_this_round: set[tuple[str, str]] = set()
        self._owner_name: str | None = None

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.channels.values():
            counts[rec["state"]] = counts.get(rec["state"], 0) + 1
        return counts

    def _isolated(self, ch: str, step: Callable[[], None], failure: str) -> None:
        """One channel's failure is recorded against that channel and does not stop the others."""

        try:
            step()
        except sync.SyncError as exc:
            self.errors.append(f"{ch[:8]}: {exc}")
            self._report_failure(ch, failure)
        except Exception as exc:  # noqa: BLE001 - fixed group text is safe; exception text remains local only
            self.errors.append(f"{ch[:8]}: {type(exc).__name__}")  # the message may carry paths or peer text
            self._report_failure(ch, failure)
        else:
            self._recover_failure(ch, failure)

    # messages

    def owner_name(self, scoped: Any) -> str:
        if self._owner_name is None:
            names = scoped.member_names([self.owner])
            self._owner_name = clean(names.get(self.owner) or self.owner[:12], NAME_MAX)
        return self._owner_name

    def _find_sent(self, scoped: Any, header: str, since: int) -> dict[str, Any] | None:
        for event in scoped.channel_messages(since):
            lines = str(event.get("content") or "").strip().splitlines()
            if (event.get("pubkey") == self.agent.pubkey and isinstance(event.get("id"), str)
                    and sync.HEX64_RE.fullmatch(event["id"]) and lines and lines[-1].strip() == header
                    and authentic(event)):
                return event
        return None

    def _failure_text(self, code: str) -> str:
        if code == "unexplained":
            return (f"{self.agent.name} 暂时不能处理这次入群：无法确认这次入群是谁发起的，因此没有开通，也没有扩大权限。\n"
                    "失败原因：缺少有效的 bot 邀请记录，或最新记录是自助加入、移除、无 bot 角色。\n"
                    "恢复方法：请频道管理员重新邀请（先把我移出，再以 bot 身份加入）；新邀请出现后我会重新处理。")
        if code in {"discovery", "directory"}:
            return (f"{self.agent.name} 的入群检查失败：本轮无法确认邀请来源，因此没有开通，也没有扩大权限。\n"
                    "失败原因：Buzz 频道成员或邀请记录暂时读取失败。\n"
                    "恢复方法：下一轮自动重试；若持续出现，请频道管理员联系 Agent owner 检查 Relay 和频道权限。")
        if code == "mirror":
            return (f"{self.agent.name} 暂时无法验证飞书镜像身份，因此没有开通，也不会退出本群。\n"
                    "失败原因：镜像身份目录或签名资料暂时不可用；这不代表邀请者不可信。\n"
                    "恢复方法：下一轮自动重试；若持续出现，请频道管理员联系 Agent owner 检查 Relay 目录读取。")
        if code == "activation":
            return (f"{self.agent.name} 的 Agent 开通失败：现在还不能在本群响应。\n"
                    "失败原因：Agent 服务没有正常运行，或重启后的频道订阅尚未确认。\n"
                    "恢复方法：下一轮自动重试；若持续出现，请联系 Agent owner 检查本机服务和订阅日志。")
        if code == "configuration":
            return (f"{self.agent.name} 的入群审批本机配置不可用：当前不能可靠处理新申请，也不会扩大权限。\n"
                    "失败原因：Agent 身份配置、频道清单或审批消息适配器没有通过本机校验。\n"
                    "恢复方法：下一轮自动重试；请联系 Agent owner 修复本机配置和服务，恢复后本群会收到通知。")
        return (f"{self.agent.name} 的入群审批处理失败：本轮没有完成开通，也没有扩大权限。\n"
                "失败原因：Agent 本机配置或 Buzz 操作暂不可用。\n"
                "恢复方法：下一轮自动重试；若持续出现，请联系 Agent owner 检查配置和服务状态。")

    def _send_failure(self, ch: str, notice: dict[str, Any]) -> None:
        """Publish one safe status with crash/unknown-outcome recovery; raw exceptions never enter the channel."""

        header = failure_header(self.agent.pubkey, ch, notice["code"], notice["incident_id"])
        if notice.get("pending_at") is not None:
            found = self._find_sent(self.buzz.for_channel(ch), header,
                                    int(notice["pending_at"]) - CLOCK_SLACK_SECONDS)
            if found is not None:
                notice.update(event=found["id"], pending_at=None)
                self.save()
                return
            notice["pending_at"] = None
            self.save()
        notice["pending_at"] = int(self.clock())
        self.save()
        channel = self.channels.get(ch, {})
        reply_to = channel.get("request_event") if isinstance(channel, dict) else None
        event_id = self.buzz.for_channel(ch).send(
            f"{self._failure_text(notice['code'])}\n{header}", reply_to=reply_to)
        notice.update(event=event_id, pending_at=None)
        self.save()

    def _report_failure(self, ch: str, code: str, *, send: bool = True) -> None:
        self.failed_this_round.add((ch, code))
        notice = self.notices.get(ch)
        if (not isinstance(notice, dict) or notice.get("code") != code
                or notice.get("recovering") is True):
            notice = {"code": code, "at": int(self.clock()), "incident_id": secrets.token_hex(8),
                      "pending_at": None, "event": None, "recovering": False,
                      "recovery_pending_at": None, "recovery_event": None}
            self.notices[ch] = notice
            self.save()
        if notice.get("event") is not None or not send:
            return
        try:
            self._send_failure(ch, notice)
        except Exception:  # noqa: BLE001 - pending state is durable; raw details never enter the group
            pass  # pending_at remains durable; a later round retries the same deterministic status

    def _send_recovery(self, ch: str, notice: dict[str, Any]) -> None:
        if notice.get("recovery_event") is not None:
            return
        header = failure_header(self.agent.pubkey, ch, notice["code"], notice["incident_id"], "recovered")
        if notice.get("recovery_pending_at") is not None:
            found = self._find_sent(self.buzz.for_channel(ch), header,
                                    int(notice["recovery_pending_at"]) - CLOCK_SLACK_SECONDS)
            if found is not None:
                notice.update(recovery_event=found["id"], recovery_pending_at=None)
                self.save()
                return
            notice["recovery_pending_at"] = None
            self.save()
        notice["recovery_pending_at"] = int(self.clock())
        self.save()
        event_id = self.buzz.for_channel(ch).send(
            "刚才提示的入群故障已恢复，流程会继续；最终以本群里的“已开通”消息为准。\n" + header,
            reply_to=notice["event"],
        )
        notice.update(recovery_event=event_id, recovery_pending_at=None)
        self.save()

    def _recover_failure(self, ch: str, code: str) -> None:
        notice = self.notices.get(ch)
        if not isinstance(notice, dict) or notice.get("code") != code:
            return
        if notice.get("event") is None:
            del self.notices[ch]  # the failure was never visible, so do not announce a stale recovery
            self.save()
            return
        notice["recovering"] = True
        self.save()
        try:
            self._send_recovery(ch, notice)
        except Exception:  # noqa: BLE001 - recovery remains pending without leaking raw details
            return  # keep the active notice; recovery is attempted again next round
        del self.notices[ch]
        self.save()

    def _retry_failure_notices(self, members: dict[str, str]) -> None:
        for ch, notice in list(self.notices.items()):
            if ch not in members or not isinstance(notice, dict):
                continue
            try:
                if notice.get("recovering"):
                    self._send_recovery(ch, notice)
                    if notice.get("recovery_event") is not None:
                        del self.notices[ch]
                        self.save()
                elif notice.get("event") is None:
                    self._send_failure(ch, notice)
            except Exception:  # noqa: BLE001 - retry later with the same incident marker
                continue

    def report_configuration_failure(self) -> None:
        """Best-effort alert for a bad agent env; never guess whether a target is a group."""

        try:
            dms = self.buzz.dm_channels()
            members = self.buzz.member_channels()
        except Exception:  # noqa: BLE001 - the outer run persists a pending retry
            self.entry["configuration_pending"] = True
            self.save()
            return
        if not members:
            # The agent has at least its previously configured channels. An empty answer is an unreadable directory,
            # not proof that there is nowhere to notify.
            self.entry["configuration_pending"] = True
            self.save()
            return
        for ch in members:
            if ch in dms:
                continue
            try:
                roles = self.buzz.for_channel(ch).channel_members()
            except Exception:  # noqa: BLE001 - without roles this target is not proven to be a group
                self.entry["configuration_pending"] = True
                self.save()
                continue
            if any(role in ADMIN_ROLES for role in roles.values()):
                self._report_failure(ch, "configuration")

    def _verified_groups(self, members: dict[str, str], dms: set[str]) -> dict[str, str]:
        """Return only targets proved to be administered groups.

        Live Buzz can omit an existing DM from ``dms list``. A real group has a current owner/admin; a two-member DM
        has only member roles. Unknown or unreadable role sets are therefore never safe notification targets.
        """

        groups: dict[str, str] = {}
        for ch, name in members.items():
            if ch in dms:
                continue
            try:
                roles = self.buzz.for_channel(ch).channel_members()
            except Exception as exc:  # noqa: BLE001 - fixed local error only; no unverified-target message
                self.errors.append(f"{ch[:8]}: group role check failed: {type(exc).__name__}")
                continue
            if any(role in ADMIN_ROLES for role in roles.values()):
                groups[ch] = name
        return groups

    def send_once(self, ch: str, rec: dict[str, Any], kind: str, text: str, *, reply_to: str | None,
                  mentions: tuple[str, ...] = ()) -> dict[str, Any]:
        """PENDING first, then send.  A PENDING marker left by a crash is settled by `settle_pending` next round.

        The strict readback (content, author, channel, Thread, `p` tags) happens inside `send`, inherited from
        `gitlab_buzz_sync.BuzzCli`: a mismatch raises, so the record keeps its PENDING marker and does not advance.
        """

        header = join_header(rec["join_id"], "" if kind == "request" else kind)
        rec["pending_send"] = {"header": header, "at": int(self.clock())}
        self.save()
        event_id = self.buzz.for_channel(ch).send(f"{text}\n{header}", reply_to=reply_to, mentions=mentions)
        rec["pending_send"] = None
        self.save()
        return {"id": event_id, "created_at": int(self.clock())}

    def settle_pending(self, ch: str, rec: dict[str, Any]) -> None:
        """A crash between PENDING and the post-send save: adopt the message if the relay has it."""

        pending = rec.get("pending_send")
        if not pending:
            return
        header = pending.get("header", "")
        found = self._find_sent(self.buzz.for_channel(ch), header, pending["at"] - CLOCK_SLACK_SECONDS)
        rec["pending_send"] = None
        if found is not None:
            kind = header.split(" ")[2] if header.count(" ") >= 2 else "request"
            mentioned = self.owner in {str(tag[1]) for tag in sync._tag_values(found, "p") if len(tag) > 1}
            if kind == "request":
                rec["request_event"] = found["id"]
                rec["requested_at"] = found.get("created_at") or int(self.clock())
                rec["owner_notified"] = mentioned
            elif kind == "notify":
                rec["owner_notified"] = mentioned
            elif kind == "active":
                rec["active_event"] = found["id"]
            elif kind == "closed":
                rec["closed_event"] = found["id"]
        self.save()

    # discovery

    def _about_agent(self, event: dict[str, Any]) -> bool:
        """An add/remove names its member in the first `p` tag (the relay ignores the rest); a join/leave is signed
        by the member itself."""

        if event.get("kind") in (9000, 9001):
            p_tags = sync._tag_values(event, "p")
            return bool(p_tags) and len(p_tags[0]) > 1 and p_tags[0][1] == self.agent.pubkey
        return event.get("kind") in (9021, 9022) and event.get("pubkey") == self.agent.pubkey

    def membership_about_agent(self, events: list[dict[str, Any]], after: int | None = None,
                               after_event: str | None = None, ignore_own_leave: bool = False) -> list[dict[str, Any]]:
        """The membership events about this agent that a previous decision has not seen.

        Seconds alone are not a sufficient cursor. Group sync therefore signs a persistent stream and sequence into its
        member events: a later position in the same stream is new even after a legitimate signer rotation or when it has
        an earlier client timestamp. Events without that proof retain the old strict-time rule.
        """

        mine = [event for event in events if self._about_agent(event) and _is_int(event.get("created_at"))
                and not (ignore_own_leave and event.get("kind") == 9022) and authentic(event)]
        if after is None:
            return mine
        cursor = next((event for event in mine if event.get("id") == after_event), None)
        cursor_position = fgs.member_event_position(cursor)

        def unseen(event: dict[str, Any]) -> bool:
            position = fgs.member_event_position(event)
            if cursor_position is not None and position is not None and position[0] == cursor_position[0]:
                # A stream is the producer's durable clock. Never replay an older position merely because its
                # client-created timestamp is newer, and do not lose a newer position when the signing key rotates.
                return position[1] > cursor_position[1]
            return event["created_at"] > after

        return [event for event in mine if unseen(event)]

    @staticmethod
    def newest_membership(mine: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Order one persistent stream's writes inside the relay's ambiguous client-clock window."""
        if not mine:
            return None
        max_time = max(event["created_at"] for event in mine)
        window = [event for event in mine if event["created_at"] >= max_time - INVITE_SKEW_SECONDS]
        positions = [fgs.member_event_position(event) for event in window]
        if (all(position is not None for position in positions)
                and len({position[0] for position in positions}) == 1
                and len({position[1] for position in positions}) == len(positions)):
            return max(window, key=lambda event: fgs.member_event_position(event)[1])
        return max(mine, key=lambda event: (event["created_at"], str(event.get("id"))))

    def classify_membership(self, mine: list[dict[str, Any]], roles: dict[str, str]) -> tuple[dict | None, str]:
        """What explains the agent's current membership: `auto`, `request`, `declined` or `none`.

        Only the newest membership event about the agent counts; an older add never stands in for a newer default-role
        add, removal or self-join.  Events within INVITE_SKEW_SECONDS of the newest cannot be ordered by their
        client-set timestamps, so they are judged together: a removal/leave as the newest or a self-join among them
        means nothing explains the membership; then an adder who is neither the owner nor a channel owner/admin means
        declined, except for a trusted Feishu mirror owned by a channel owner/admin; then an add without `role=bot`
        means nothing explains it; only owner adds mean auto.  A trusted mirror starts an approval request rather than
        auto-approving because it proves the administrative bridge, not which Feishu person initiated the change.
        """

        if not mine:
            return None, "none"
        newest = self.newest_membership(mine)
        assert newest is not None
        max_time = max(event["created_at"] for event in mine)
        window = [event for event in mine if event["created_at"] >= max_time - INVITE_SKEW_SECONDS]
        if newest.get("kind") != 9000 or any(event.get("kind") == 9021 for event in window):
            return None, "none"
        adds = [event for event in window if event.get("kind") == 9000]
        adders = {event.get("pubkey") for event in adds}
        bot_adders = {str(adder) for adder in adders if isinstance(adder, str) and roles.get(adder) == "bot"}
        trusted_mirrors = self.buzz.trusted_mirrors(bot_adders, roles) if bot_adders else set()
        if trusted_mirrors is None:
            return newest, "unavailable"
        if any(adder != self.owner and roles.get(adder) not in ADMIN_ROLES and adder not in trusted_mirrors
               for adder in adders):
            return newest, "declined"
        if any(["role", "bot"] not in [tag[:2] for tag in sync._tag_values(event, "role")] for event in adds):
            return None, "none"
        if adders == {self.owner}:
            return newest, "auto"
        return newest, "request"

    def detect(self, ch: str, name: str) -> None:
        rec = self.channels.get(ch)
        if rec is not None and rec["state"] == "BASELINE":
            return
        if rec is not None and rec["state"] == "ACTIVE":
            self.counts["drift"] += 1  # approved once, since dropped from the allowlist by hand: leave it to the owner
            return
        if rec is not None and rec["state"] not in REOPENABLE:
            return
        scoped = self.buzz.for_channel(ch)
        now = self.clock()
        roles = scoped.channel_members()
        events = scoped.membership_events(int(now - INVITE_SCAN_SECONDS))
        # Only events newer than what an earlier decision saw reopen it; our own leave is ours, not news.
        mine = self.membership_about_agent(events, after=rec.get("invite_at") if rec is not None else None,
                                           after_event=rec.get("invite_event") if rec is not None else None,
                                           ignore_own_leave=rec is not None and rec["state"] == "LEFT")
        invite, verdict = self.classify_membership(mine, roles)
        if rec is not None:
            if invite is not None:
                pass  # a newer invite: a new request below
            elif rec["state"] == "WITHDRAWN" and not mine and rec.get("withdrawn_from"):
                self._restore(rec)  # nothing happened while it looked gone: it was a bad members read
                return
            elif rec["state"] == "LEFT":
                rec["state"] = "CLOSING"  # the leave did not stick; the notice is already out
                self.save()
                return
            else:
                return
        rec = new_record("REQUESTED", name, now)
        if verdict == "unavailable":
            # Keep the invite eligible for the next round: a directory outage is neither a rejection nor a reason to
            # consume the membership cursor. The agent remains present but unavailable until trust can be verified.
            rec.update(state="NO_INVITE", outcome="mirror_unavailable")
            self.channels[ch] = rec
            self.save()
            self.errors.append(f"{ch[:8]}: mirror directory unavailable")
            self._report_failure(ch, "mirror")
            return
        if invite is None:
            # Nothing explains the membership (no add, a self-join, a non-bot add, a DM the list missed): do not guess
            # or leave, but tell this group exactly why the agent remains unavailable and how an admin can recover.
            # Remember the newest event seen, so only something newer can reopen it; an old event scrolling out of the
            # 30-day scan must not change the verdict.
            cursor = self.newest_membership(mine)
            rec.update(state="NO_INVITE", invite_event=cursor.get("id") if cursor is not None else None,
                       invite_at=cursor["created_at"] if cursor is not None else int(now) - INVITE_SKEW_SECONDS)
            self.channels[ch] = rec
            self.save()
            self._report_failure(ch, "unexplained")
            return
        self._recover_failure(ch, "unexplained")
        self._recover_failure(ch, "mirror")
        rec.update(join_id=make_join_id(self.agent.pubkey, ch, invite["id"]), inviter=invite.get("pubkey"),
                   invite_event=invite["id"], invite_at=invite["created_at"],
                   capability=capability(self.agent.capabilities, scoped.canvas()))
        if verdict == "auto":
            rec.update(state="APPROVED", outcome="auto_approved", decided_at=int(now))
        elif verdict == "declined":
            rec.update(state="CLOSING", outcome="declined_inviter", decided_at=int(now))
        self.channels[ch] = rec
        self.save()
        if rec["state"] == "REQUESTED":
            self.counts["requested"] += 1
        if rec["state"] in {"REQUESTED", "APPROVED"}:
            self.post_request(ch, rec, roles)

    def _restore(self, rec: dict[str, Any]) -> None:
        rec.update(state=rec["withdrawn_from"], withdrawn_from=None, absent_since=None)
        self.save()

    def post_request(self, ch: str, rec: dict[str, Any], roles: dict[str, str]) -> None:
        scoped = self.buzz.for_channel(ch)
        owner = self.owner_name(scoped)
        me = self.agent.name
        room = clean(rec["name"], NAME_MAX) or "本群"
        lines: list[str]
        mentions: tuple[str, ...] = ()
        if rec["outcome"] == "auto_approved":
            lines = [f"我是 {me}，owner {owner} 把我拉进了「{room}」，正在开通：我空闲时会重启一次，开通后在这里回复。"]
            lines += capability_lines(self.agent, rec["capability"])
        else:
            lines = [f"我是 {me}（owner：{owner}），收到了加入「{room}」的邀请。这次加入需要 owner 同意；{owner} 同意之前，我不会回应本群的 @。"]
            lines += capability_lines(self.agent, rec["capability"])
            ask = (f"同意请在这条消息上点 ✅，或在本 Thread 回复 /approve {rec['join_id']}；"
                   f"不同意点 ❌ 或回复 /deny {rec['join_id']}。{_ttl_text(self.ttl)}内没有答复，我会自动退出本群。")
            if roles.get(self.owner) in HUMAN_ROLES:
                lines.append(f"{owner}：{ask}")
                mentions = (self.owner,)
            else:
                lines.append(f"我的 owner {owner} 还不是本群成员：请管理员先把 {owner} 加进来，加进来后我会在这里提醒。")
                lines.append(f"{owner} {ask}")
        sent = self.send_once(ch, rec, "request", "\n".join(lines), reply_to=None, mentions=mentions)
        rec["request_event"] = sent["id"]
        rec["requested_at"] = sent["created_at"] or int(self.clock())
        rec["owner_notified"] = bool(mentions)  # only a delivered mention counts
        self.save()

    # decisions

    def decide(self, ch: str, rec: dict[str, Any], roles: dict[str, str]) -> None:
        scoped = self.buzz.for_channel(ch)
        now = self.clock()
        deadline = rec["requested_at"] + self.ttl
        if not rec["owner_notified"] and roles.get(self.owner) in HUMAN_ROLES and now < deadline:
            owner = self.owner_name(scoped)
            text = (f"{owner}，请决定是否让我加入本群：在上面那条消息上点 ✅ 同意、❌ 不同意，"
                    f"或回复 /approve {rec['join_id']}、/deny {rec['join_id']}。")
            self.send_once(ch, rec, "notify", text, reply_to=rec["request_event"], mentions=(self.owner,))
            rec["owner_notified"] = True
            self.save()
        try:
            signals = self._signals(scoped, rec, deadline, roles)
        except sync.SyncError:
            if now < deadline:
                raise
            signals = []  # unreadable (e.g. the request was deleted) and past the deadline: leaving is the safe side
        if signals:
            verdict = min(signals)[2]
            if verdict == "approve":
                rec.update(state="APPROVED", outcome="approved", decided_at=int(now))
            else:
                rec.update(state="CLOSING", outcome="denied", decided_at=int(now))
            self.save()
        elif now >= deadline:
            rec.update(state="CLOSING", outcome="expired", decided_at=int(now))
            self.save()

    def _signals(self, scoped: Any, rec: dict[str, Any], deadline: float,
                 roles: dict[str, str] | None = None) -> list[tuple[int, str, str]]:
        since = int(rec["requested_at"] - CLOCK_SLACK_SECONDS)
        root, join_id = rec["request_event"], rec["join_id"]

        def in_time(event: dict[str, Any]) -> bool:
            return _is_int(event.get("created_at")) and since <= event["created_at"] <= deadline

        def in_window(event: dict[str, Any]) -> bool:
            return event.get("pubkey") == self.owner and in_time(event)

        def verdict_of(event: dict[str, Any]) -> str | None:
            emoji = str(event.get("content") or "").replace(VARIATION_SELECTOR, "").strip()
            return "approve" if emoji in APPROVE_EMOJIS else "deny" if emoji in DENY_EMOJIS else None

        signals: list[tuple[int, str, str]] = []
        relayed: list[tuple[dict[str, Any], str]] = []
        for event in scoped.channel_reactions(since):
            if _e_targets(event) != {root} or not in_time(event):
                continue
            verdict = verdict_of(event)
            if verdict is None:
                continue
            if event.get("pubkey") == self.owner:
                if authentic(event):
                    signals.append((event["created_at"], str(event["id"]), verdict))
                continue
            # ADR-0020: the owner's answer given in Feishu, put on the request by the group sync's mirror.
            authors = [tag[1] for tag in sync._tag_values(event, fgs.FEISHU_AUTHOR_TAG) if len(tag) > 1]
            joins = [tag[1] for tag in sync._tag_values(event, "join") if len(tag) > 1]
            if self.accept_feishu and authors == [self.owner] and joins in ([], [join_id]):
                relayed.append((event, verdict))
        for event in scoped.thread(root) if self.accept_feishu else []:
            # ADR-0020: the owner's `/approve JOIN-<id>` written in Feishu, signed by the mirror with the person and the request.
            if (event.get("pubkey") == self.owner or not in_time(event) or event.get("kind", 9) != 9
                    or root not in _e_targets(event)):
                continue
            authors = [tag[1] for tag in sync._tag_values(event, fgs.FEISHU_AUTHOR_TAG) if len(tag) > 1]
            joins = [tag[1] for tag in sync._tag_values(event, "join") if len(tag) > 1]
            head, sep, body = str(event.get("content") or "").strip().split("\n", 1)[0].partition("：")
            if authors != [self.owner] or joins != [join_id] or not sep or not head.startswith("[飞书] "):
                continue
            for pattern, verdict in ((APPROVE_RE, "approve"), (DENY_RE, "deny")):
                match = pattern.fullmatch(body.strip())
                if match and match.group(1) == join_id:
                    relayed.append((event, verdict))
        if relayed:
            trusted = scoped.trusted_mirrors({str(event.get("pubkey")) for event, _ in relayed}, roles or {})
            if trusted is None:
                raise sync.SyncError("trusted mirror directory is temporarily unavailable")
            for event, verdict in relayed:
                if event.get("pubkey") in trusted and authentic(event):
                    signals.append((event["created_at"], str(event["id"]), verdict))
        for event in scoped.thread(root):
            if not in_window(event) or event.get("kind", 9) != 9 or root not in _e_targets(event):
                continue
            first = str(event.get("content") or "").strip().split("\n", 1)[0].strip()
            for pattern, verdict in ((APPROVE_RE, "approve"), (DENY_RE, "deny")):
                match = pattern.fullmatch(first)
                if match and match.group(1) == join_id and authentic(event):
                    signals.append((event["created_at"], str(event["id"]), verdict))
        return signals

    def close(self, ch: str, rec: dict[str, Any]) -> None:
        scoped = self.buzz.for_channel(ch)
        owner = self.owner_name(scoped)
        if rec["closed_event"] is None:
            text = {
                "declined_inviter": (f"我是 {self.agent.name}（owner：{owner}）。只有本群的管理员邀请我，才会转给 owner 审批；"
                                     "这次的邀请不是管理员发起的，我先退出本群。需要的话请管理员重新邀请。"),
                "denied": f"owner {owner} 没有同意我加入本群，我先退出。",
                "expired": f"{_ttl_text(self.ttl)}内 owner {owner} 没有回复，我先退出本群；需要的话请管理员重新邀请。",
            }[rec["outcome"]]
            sent = self.send_once(ch, rec, "closed", text, reply_to=rec["request_event"])
            rec["closed_event"] = sent["id"]
            self.save()
        scoped.leave()
        rec["state"] = "LEFT"
        self.save()
        self.counts["left"] += 1

    # applying

    def apply(self, ch: str, rec: dict[str, Any]) -> None:
        """Optional pieces first, each a follow-up if it cannot be done; the env allowlist, the switch, last."""

        agent = self.agent
        followups: list[str] = []
        with env_dir_lock(agent.env_file):
            row = prompt_row(ch, rec["capability"], rec["join_id"], rec["decided_at"] or self.clock())
            try:
                prompt_done = agent.prompt_file is not None and add_channel_to_prompt(agent.prompt_file, ch, row)
            except (sync.SyncError, OSError):
                prompt_done = False
            if not prompt_done:
                followups.append("我的 prompt 读不到或没有频道表标记块，owner 需要手工把本群加进 prompt 的频道表。")
            if agent.responsible_config is not None:
                try:
                    add_channel_to_responsible(agent.responsible_config, ch)
                except (sync.SyncError, OSError):
                    followups.append("责任人 helper 配置读不到或格式不对，owner 需要检查 BUZZ_RESPONSIBLE_CONFIG。")
            agent.env_text = add_channel_to_env(agent.env_file, ch, expected=agent.env_text)
        rec.update(state="APPLIED", followups=followups)
        self.save()

    def restart_when_idle(self, eligible_groups: set[str]) -> bool:
        """Restart only a running unit whose cgroup holds nothing but the harness; a busy agent waits, however long."""

        applied = [rec for ch, rec in self.channels.items()
                   if ch in eligible_groups and rec["state"] == "APPLIED"]
        if not applied:
            return False
        load, active = self.system.unit_status(self.agent.unit)
        if load != "loaded" or active != "active":
            raise sync.SyncError(f"{self.agent.name}: {self.agent.unit} is {load}/{active}; not starting it")
        now = self.clock()
        if self.system.is_busy(self.agent.unit):
            for rec in applied:
                rec["restart_deferred_since"] = rec["restart_deferred_since"] or int(now)
            self.counts["deferred"] += len(applied)
            self.save()
            return False
        offset = self.system.log_size(self.agent.log_file) if self.agent.log_file else None
        cursor = None if self.agent.log_file else self.system.journal_cursor(self.agent.unit)
        self.system.restart(self.agent.unit)
        for rec in applied:
            rec.update(state="RESTARTED", restarted_at=int(now), log_offset=offset, journal_cursor=cursor)
        self.save()
        return True

    def _log_after_restart(self, rec: dict[str, Any]) -> str:
        if self.agent.log_file:
            return self.system.log_since(self.agent.log_file, rec["log_offset"] or 0)
        return self.system.journal_after(self.agent.unit, rec.get("journal_cursor"))

    def verify(self, eligible_groups: set[str], *, just_restarted: bool) -> None:
        restarted = {ch: rec for ch, rec in self.channels.items()
                     if ch in eligible_groups and rec["state"] == "RESTARTED"}
        if not restarted:
            return
        deadline = self.clock() + (VERIFY_TIMEOUT_SECONDS if just_restarted else 0)
        verified: set[str] = set()
        while True:
            if self.system.is_active(self.agent.unit):
                for ch, rec in restarted.items():
                    if f"subscribed to channel {ch}" in self._log_after_restart(rec):
                        verified.add(ch)
            if len(verified) == len(restarted) or self.clock() >= deadline:
                break
            self.sleeper(VERIFY_POLL_SECONDS)
        for ch in sorted(verified):
            self._isolated(ch, lambda ch=ch: self.announce(ch, restarted[ch]), "activation")
        missing = sorted(set(restarted) - verified)
        if not missing:
            return
        allowlist = _allowlist(read_env_file(self.agent.env_file).get("BUZZ_ACP_CHANNELS", ""))
        for ch in missing:
            rec = restarted[ch]
            if ch in allowlist:
                self.errors.append(f"{ch[:8]}: restarted, but not active or not subscribed yet")
            elif rec.get("reapplied", 0) < REAPPLY_MAX:  # someone rewrote the env after our edit: apply it again
                rec.update(state="APPROVED", log_offset=None, restart_deferred_since=None,
                           reapplied=rec.get("reapplied", 0) + 1)
                self.errors.append(f"{ch[:8]}: the env lost this channel after the restart; applying it again")
            else:
                self.errors.append(f"{ch[:8]}: the env keeps losing this channel; not restarting again "
                                   "(check the tool that rewrites agent env files, e.g. harness-failover)")
            self._report_failure(ch, "activation")
        self.save()

    def announce(self, ch: str, rec: dict[str, Any]) -> None:
        cap = rec["capability"]
        lines = [f"已开通，现在可以在本群 @ 我（{self.agent.name}）。", "还需要人补的："]
        summary = clean(self.agent.capabilities["summary"], SUMMARY_MAX)
        lines.append(f"- 请管理员在本群 Canvas 的 Agent 表加一行：| {self.agent.name} | `{self.agent.pubkey}` | {summary} |")
        if cap["missing"]:
            lines.append(f"- 本群仓库 {_repo_list(cap['missing'])} 我没有权限；需要的话请 owner 另走 token 申请。")
        lines += [f"- {item}" for item in rec["followups"]]
        if rec["active_event"] is None:
            sent = self.send_once(ch, rec, "active", "\n".join(lines), reply_to=rec["request_event"])
            rec["active_event"] = sent["id"]
        rec["state"] = "ACTIVE"
        self.save()
        self.counts["active"] += 1

    # the round

    def _absent_long_enough(self, rec: dict[str, Any], now: float) -> bool:
        if rec.get("absent_since") is None:
            rec["absent_since"] = int(now)
            self.save()
            return False
        return now - rec["absent_since"] >= ABSENCE_GRACE_SECONDS

    def progress_absent(self, ch: str, rec: dict[str, Any]) -> None:
        """Advance only absence bookkeeping; never read or write an unverified target."""

        state_name = rec["state"]
        if state_name == "BASELINE":
            if self._absent_long_enough(rec, self.clock()):
                del self.channels[ch]
                self.save()
            return
        if state_name in TERMINAL:
            return
        if state_name == "CLOSING":
            rec["state"] = "LEFT"
            self.save()
        elif self._absent_long_enough(rec, self.clock()):
            rec.update(state="WITHDRAWN", withdrawn_from=state_name)
            self.counts["withdrawn"] += 1
            self.save()

    def progress(self, ch: str, rec: dict[str, Any], members: dict[str, str]) -> None:
        self.settle_pending(ch, rec)
        state_name = rec["state"]
        if ch in members:
            if rec.get("absent_since") is not None:
                rec["absent_since"] = None
                self.save()
            if state_name == "WITHDRAWN" and rec.get("withdrawn_from") in {"APPLIED", "RESTARTED"}:
                # Approved and already in the env allowlist (so detection never sees it): finish it.
                self._restore(rec)
                state_name = rec["state"]
        elif state_name == "BASELINE":
            if self._absent_long_enough(rec, self.clock()):
                del self.channels[ch]  # it left: a later invite is new
                self.save()
            return
        if state_name in TERMINAL:
            return
        if ch not in members:
            if state_name == "CLOSING":
                rec["state"] = "LEFT"
                self.save()
            elif self._absent_long_enough(rec, self.clock()):
                rec.update(state="WITHDRAWN", withdrawn_from=state_name)
                self.counts["withdrawn"] += 1
                self.save()
            return
        if state_name in {"REQUESTED", "CLOSING"} and ch in self.agent.allowlist and rec["closed_event"] is None:
            rec.update(state="ACTIVE", outcome="manual")  # the owner put it in the allowlist by hand
            self.counts["manual"] += 1
            self.save()
            return
        if state_name in {"REQUESTED", "APPROVED"} and rec["request_event"] is None:
            self.post_request(ch, rec, self.buzz.for_channel(ch).channel_members())
        if rec["state"] == "REQUESTED":
            self.decide(ch, rec, self.buzz.for_channel(ch).channel_members())
        if rec["state"] == "CLOSING":
            self.close(ch, rec)
        elif rec["state"] == "APPROVED":
            self.counts["approved"] += 1
            self.apply(ch, rec)

    def run(self) -> None:
        try:
            dms = self.buzz.dm_channels()
        except Exception as exc:  # noqa: BLE001 - do not risk treating a private DM as a group
            for ch in self.channels:
                self._report_failure(ch, "directory", send=False)
            raise sync.SyncError(f"{self.agent.name}: DM exclusion unavailable: {type(exc).__name__}") from None
        try:
            members = self.buzz.member_channels()
        except Exception as exc:  # noqa: BLE001 - a successful DM list is not complete proof that a target is a group
            for ch in self.channels:
                self._report_failure(ch, "directory", send=False)
            raise sync.SyncError(f"{self.agent.name}: member channels unavailable: {type(exc).__name__}") from None
        if not members:
            # The agent is always in at least its allowlisted channels: an empty list is a bad read, not a fact.
            for ch in self.channels:
                self._report_failure(ch, "directory", send=False)
            raise sync.SyncError(f"{self.agent.name}: Buzz returned no member channels; skipping this round")
        group_members = self._verified_groups(members, dms)
        self._retry_failure_notices(group_members)
        if self.entry.pop("configuration_pending", False):
            for ch in group_members:
                self._report_failure(ch, "configuration")
            self.save()
        for ch in group_members:
            self._recover_failure(ch, "configuration")
        for ch in group_members:
            self._recover_failure(ch, "directory")
        first_round = "initialized_at" not in self.entry
        for ch, name in group_members.items():
            if ch in self.channels:
                continue
            if first_round or ch in self.agent.allowlist:
                # Whatever is there before this script, or put in the allowlist by hand, is the owner's business.
                self.channels[ch] = new_record("BASELINE", name, self.clock())
                self.counts["baseline"] += 1
        if first_round:
            self.entry["initialized_at"] = int(self.clock())
            self.save()
            return
        self.save()
        for ch, name in sorted(group_members.items()):
            if ch not in self.agent.allowlist:
                self._isolated(ch, lambda ch=ch, name=name: self.detect(ch, name), "discovery")
        for ch in sorted(self.channels):
            rec = self.channels.get(ch)
            if rec is None:
                continue
            if ch in group_members:
                self._isolated(ch, lambda ch=ch, rec=rec: self.progress(ch, rec, group_members), "processing")
            elif ch not in members:
                self.progress_absent(ch, rec)
        try:
            just_restarted = self.restart_when_idle(set(group_members))
        except sync.SyncError as exc:
            self.errors.append(str(exc))
            for ch, rec in self.channels.items():
                if rec.get("state") in {"APPLIED", "RESTARTED"} and ch in group_members:
                    self._report_failure(ch, "activation")
            just_restarted = False
        except Exception as exc:  # noqa: BLE001 - fixed activation text prevents exception leakage
            self.errors.append(f"{self.agent.name}: restart step failed: {type(exc).__name__}")
            for ch, rec in self.channels.items():
                if rec.get("state") in {"APPLIED", "RESTARTED"} and ch in group_members:
                    self._report_failure(ch, "activation")
            just_restarted = False
        self.verify(set(group_members), just_restarted=just_restarted)
        for ch, rec in self.channels.items():
            if ch in group_members and rec.get("state") == "ACTIVE":
                self._recover_failure(ch, "activation")
        if self.errors:
            raise sync.SyncError("; ".join(self.errors)[:1000])


# ── run ──────────────────────────────────────────────────────────────────────


def run(config: dict[str, Any], *, state_dir: Path, make_buzz: Any = None, system: Any = None,
        clock: Callable[[], float] = time.time, sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    validate_config(config)
    system = system or SystemOps()
    make_buzz = make_buzz or make_agent_buzz_factory(config)
    results: dict[str, dict[str, Any]] = {}
    try:
        with state_lock(Path(state_dir)):
            state = load_state(Path(state_dir))

            def save() -> None:
                save_state(Path(state_dir), state)

            for agent_config in config["agents"]:
                name = agent_config["name"]
                counts: dict[str, Any] = {"error": None}
                job: AgentRun | None = None
                try:
                    agent = load_agent(agent_config, config["owner_pubkey"])
                except sync.SyncError as exc:
                    counts["error"] = str(exc)
                    try:
                        notice_agent = load_agent_for_notice(agent_config)
                        entry = state["agents"].setdefault(
                            notice_agent.pubkey, {"name": name, "channels": {}}
                        )
                        entry["name"] = name
                        job = AgentRun(notice_agent, make_buzz(notice_agent), entry, config, system,
                                       clock, sleeper, save)
                        job.report_configuration_failure()
                    except Exception:  # noqa: BLE001 - no safe message adapter; retain a retry marker only
                        for entry in state["agents"].values():
                            if isinstance(entry, dict) and entry.get("name") == name:
                                entry["configuration_pending"] = True
                except Exception as exc:  # noqa: BLE001 - one agent's bug must not stop the others
                    counts["error"] = f"{name}: {type(exc).__name__}"
                    for entry in state["agents"].values():
                        if isinstance(entry, dict) and entry.get("name") == name:
                            entry["configuration_pending"] = True
                else:
                    entry = state["agents"].setdefault(agent.pubkey, {"name": name, "channels": {}})
                    entry["name"] = name
                    try:
                        job = AgentRun(agent, make_buzz(agent), entry, config, system, clock, sleeper, save)
                        counts = job.counts
                        job.run()
                    except sync.SyncError as exc:
                        counts["error"] = str(exc)
                    except Exception as exc:  # noqa: BLE001 - persist and report safely on the next healthy round
                        entry["configuration_pending"] = True
                        counts["error"] = f"{name}: {type(exc).__name__}"
                finally:
                    if job is not None:
                        counts["states"] = job.state_counts()
                    save()
                results[name] = counts
    except JoinLocked:
        return {"status": "locked"}
    status = "error" if any(counts.get("error") for counts in results.values()) else "ok"
    return {"status": status, "agents": results}


# ── entrypoint ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)  # zero arguments: the owner-fixed env names the config
    runtime_env = dict(os.environ if env is None else env)
    out = stdout or sys.stdout

    def emit(payload: dict[str, Any]) -> None:
        out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    path = runtime_env.get(CONFIG_ENV)
    if not path:
        emit({"status": "error", "error": f"{CONFIG_ENV} is required"})
        return 2
    try:
        config = load_config(Path(path))
        result = run(config, state_dir=Path(config["state_dir"]))
    except sync.SyncError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except (OSError, ValueError) as exc:
        emit({"status": "error", "error": type(exc).__name__})
        return 1
    emit(result)
    return 0 if result.get("status") in {"ok", "locked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
