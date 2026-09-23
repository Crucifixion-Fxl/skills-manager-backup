#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""GitLab → Buzz change sync, run as a deterministic Desk-owned Agent Step.

The child process posts facts only, using the Desk identity: one root
per Issue and per MR, replies in that thread when the object or its comments
change, and a binding note in GitLab. Push, branch, and branch-pipeline facts
prefer the associated Issue thread, then an MR thread, then a feature-branch
thread; only unmatched protected-branch and unattributable activity stay in
the channel summary. It never mentions agents; channel
the Desk route gate matches the machine header line (last line on new
facts, first line on legacy facts). Mentions are mapped human Channel members
only, chosen per message type from structured GitLab fields (assignees, author,
reviewers, pipeline/deployment user) plus exact `@username` tokens of a project
member's comment (buzz-deploy ADR-0018); at most three per message. A message with `p` tags also
shows them in one visible line `🔔 通知 @name @name` above the header (ADR-0012; plain names without
`@` when a display name is not a unique plain token). GitLab-sourced
text has `@` and `nostr:` neutralized so the Buzz CLI cannot resolve it into
mentions. Bad data on one Issue, MR, MR group or milestone (a malformed origin marker, a
bound root that is gone or is not a readable top-level root, a broken binding) stalls only that object:
it is skipped without any write, listed in the result's `stalled` (named, with the reason),
retried every round, and announced by one channel notice per object and reason per UTC day
that tags the Channel owner, while every other object keeps syncing (ADR-0009, ADR-0010).
An event-driven MR group or milestone keeps its event records in the cache until it succeeds.
An origin marker may name any top-level message of the channel that Desk did not write, a
person's included (ADR-0014): the object binds to it and its facts are replies in that thread, with
no plaque. A well-formed marker whose root cannot be used (deleted, a reply, another channel) is
skipped: the object syncs with a plaque of its own and the round lists it in `origin_fallbacks`.
A bare `buzz://message?…` link is only a hint (ADR-0011): it counts as an origin when it
leads to a Desk plaque or fact and is ignored otherwise. Transport, authentication,
identity, configuration and uncertain-delivery errors still stop the run at the first
failure without advancing the cursor; facts already ACKed are kept (ADR-0004,
references/gitlab-buzz-sync.md).

Usage:
  gitlab_buzz_sync.py --config <channel-sync.json> [--dry-run]
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import email.utils
import fcntl
import fnmatch
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import buzz_responsible_mentions as responsible  # noqa: E402 -- deterministic sibling policy gate

REFERENCE_SCRIPTS = Path(__file__).resolve().parents[1] / "references" / "scripts"
if str(REFERENCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REFERENCE_SCRIPTS))
import nostrkit as nk  # noqa: E402 -- reviewed, dependency-free sibling used for preflight key derivation


BINDING_PREFIX = "gitlab-buzz-binding:v1"
ORIGIN_PREFIX = "gitlab-buzz-origin:v1"
BUZZ_CLI_RELEASE_DIR = "buzz-0.5.23"
ELF_MAGIC = b"\x7fELF"
TITLE_LIMIT = 200
COMMENT_LIMIT = 4000
# The stalled reason is posted to the Channel, so it must never carry a credential-shaped string (a token, a key, a
# long opaque id) that an exception message happened to include; anything this long in one run is masked.
STALLED_REASON_LIMIT = 300
STALLED_GROUP_RECORD_LIMIT = 200  # records kept in the cache per stalled event-driven group
STALLED_SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")
DIFF_LIMIT = 61_440
MAINTAINER_ACCESS = 40
CHANGES = ("routing", "content", "activity")
MR_CHANGES = ("lifecycle", "update", "activity")
# ADR-0015: the cross-link an MR leaves in a related thread that is not its binding thread. It shares the MR header
# but is not a fact change (it is not in MR_CHANGES): readers of a thread's MR history skip it.
MR_XREF = "xref"
MR_STATES = ("opened", "merged", "closed", "locked")
OBJECTS = ("issue", "mr")
FACT_LINES = ("title", "url", "labels", "assignees", "milestone", "description")
MR_FACT_LINES = ("title", "url", "branches", "sha", "labels", "reviewers", "assignees", "milestone", "description",
                 "author")
# Plaque roots (issue #78): a thread root that is a pure topic card. The only machine
# readable line is the bare object URL on the last line — canonical and unforgeable enough
# to identify the object deterministically (crash recovery, root scans) without a tag wall.
PLAQUE_URL_RE = re.compile(r"https?://[^\s\x00-\x1f\x7f]+")
# GitLab now hands out /-/work_items/N as the web_url of some issues (and Tasks); both are the issue kind.
PLAQUE_ISSUE_TAIL_RE = re.compile(r"/-/(?:issues|work_items)/([1-9][0-9]*)$")
PLAQUE_WORK_ITEM_TAIL_RE = re.compile(r"/-/work_items/([1-9][0-9]*)$")
PLAQUE_MR_TAIL_RE = re.compile(r"/-/merge_requests/([1-9][0-9]*)$")
PLAQUE_MILESTONE_TAIL_RE = re.compile(r"/-/milestones/([1-9][0-9]*)$")
PLAQUE_BRANCH_MARK = "/-/tree/"
OBJECT_ICON = {"issue": "📋", "mr": "🔀", "branch": "🌿", "milestone": "🎯"}
STATE_WORDS = {"opened": "已打开", "closed": "已关闭", "merged": "已合并", "locked": "已锁定"}
CHILD_ENV_KEYS = frozenset(
    {
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        # A sandbox that only reaches the relay through an egress proxy needs these or the CLI child fails DNS.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "BUZZ_RELAY_URL",
        "BUZZ_PRIVATE_KEY",
        "BUZZ_AUTH_TAG",
    }
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

FACT_VALUE = r"[a-z0-9][a-z0-9-]{0,31}"
EVENT_KEY_PAT = r"[a-z_]+-[A-Za-z0-9_.-]{1,80}"
EVENT_KEYS_PAT = rf"{EVENT_KEY_PAT}(?:,{EVENT_KEY_PAT})*"
HEADER_TRAILER_PAT = (
    r"(?:\[desc:([0-9a-f]{12}|-)\])?"
    r"(?:\[note:([1-9][0-9]*)\])?"
    rf"(?:\[events:({EVENT_KEYS_PAT})\])?"
)
HEADER_TRAILER_STRIP = re.compile(
    r"(?:\[desc:[^\]]*\])?(?:\[note:[^\]]*\])?(?:\[events:[^\]]*\])?$"
)
ISSUE_HEADER_RE = re.compile(
    rf"\[gitlab-notify:v1\]\[object:issue\]\[type:({FACT_VALUE})\]\[status:({FACT_VALUE})\]"
    r"\[state:(opened|closed)\]\[change:(routing|content|activity)\]"
    r"\[project:([1-9][0-9]{0,11})\]\[issue:([1-9][0-9]{0,11})\]"
    + HEADER_TRAILER_PAT
)
MR_HEADER_RE = re.compile(
    r"\[gitlab-notify:v1\]\[object:mr\]\[state:(opened|merged|closed|locked)\]\[draft:(yes|no)\]"
    r"\[change:(lifecycle|update|activity|xref)\]\[transition:(reviewable|none)\]"
    r"\[project:([1-9][0-9]{0,11})\]\[mr:([1-9][0-9]{0,11})\]"
    + HEADER_TRAILER_PAT
)
BRANCH_HEADER_RE = re.compile(
    r"\[gitlab-notify:v1\]\[object:branch\]\[change:activity\]"
    r"\[project:([1-9][0-9]{0,11})\]\[branch:([a-f0-9]{12})\]"
    + HEADER_TRAILER_PAT
)
# 2026-09-18 notification policy (#77, confirmed by the channel owner; refined twice
# the same day): Buzz carries Issue / MR / milestone threads plus release-class and
# failure-class instant notices — tag create/delete, new Release, deployment failed
# or blocked, default-branch pipeline failed — and the project access-token expiry
# warning. Everything else GitLab emits — branch pushes, feature flags, wiki,
# membership, commit/snippet comments, pipeline and deployment happy paths — is not
# recorded at all, so the digest ladder and summary publisher below stay unreachable
# from live data on purpose (kept for synthetic records and a possible policy revert;
# removal is tracked in the follow-up issue referenced from the MR).
NOTIFIED_OBJECTS = frozenset({"issue", "mr", "note", "milestone", "pipeline", "deployment",
                              "access_token", "tag", "release"})
# GitLab digest records that carry a git ref. Branch create/delete stay object=push
# (event=branch_created/branch_deleted). object=branch is only the Buzz thread header.
PLACEABLE_DIGEST_OBJECTS = frozenset({"push", "pipeline", "deployment"})
LABEL_VALUE_RE = re.compile(FACT_VALUE)
BINDING_LINE_RE = re.compile(r"<!-- gitlab-buzz-binding:v1 (\{.*\}) -->")
ORIGIN_LINE_RE = re.compile(r"<!-- gitlab-buzz-origin:v1 (\{[^{}]*\}) -->")
BUZZ_LINK_RE = re.compile(r"buzz://message\?([^\s<>\"'`]+)")
NOTE_LINE_RE = re.compile(r"note: ([1-9][0-9]*)")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
USERNAME_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,254}")
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})"
)
TOKEN_ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
DIFF_SECTION_RE = re.compile(r"(?m)^(?=diff --git )")
BINDING_KEYS = frozenset({"project_id", "object", "iid", "channel_id", "root_event_id"})
ORIGIN_KEYS = frozenset({"channel_id", "root_event_id"})
POLLING_GAPS = ("emoji", "release_update_delete", "intermediate_states",
                "mr_unapproval", "discussion_resolution", "auto_merge_setting")
TOP_HEADER_RE = re.compile(
    r"\[gitlab-notify:v1\]\[object:(push|tag|note|milestone|wiki|member|other|pipeline|deployment|release|feature_flag|access_token|sync|activity)\]"
    r"\[event:([a-z0-9_-]{1,40})\]\[project:([1-9][0-9]{0,11})\]"
    + HEADER_TRAILER_PAT
)
EVENT_KEY_RE = re.compile(EVENT_KEY_PAT)
MR_REF_RE = re.compile(r"refs/merge-requests/([1-9][0-9]*)/head")
TERMINAL_PIPELINE_STATUSES = frozenset({"success", "failed", "canceled"})
DEPLOYMENT_STATUSES = frozenset({"running", "success", "failed", "canceled", "blocked"})
DIGEST_REF_LIMIT = 6
DIGEST_BYTE_LIMIT = 60_000  # the Buzz CLI rejects message content over 65,536 bytes
DIGEST_OVERHEAD_RESERVE = 4_096  # header, summary and refs lines of one digest chunk
SUMMARY_REQUEST_BYTE_LIMIT = 60_000
SUMMARY_FACT_KEYS = (
    "object", "event", "created_at", "actor", "ref", "title", "url", "commits",
)
SUMMARY_OBJECTS = frozenset({
    "push", "note", "milestone", "wiki", "member", "other", "pipeline", "deployment", "sync",
})
DAILY_WINDOW_SKEW = dt.timedelta(minutes=15)  # Desk and GitLab clocks may disagree around midnight
NOSTR_URI_RE = re.compile(r"(?i)(nostr):")
DAILY_OBJECTS = frozenset({"access_token", "sync"})
CONFIG_KEYS = frozenset({"channel_id", "publisher_pubkey", "since", "include_confidential", "exclude", "diff", "audience",
                         "agent_pubkeys", "people", "people_file", "gitlab", "buzz", "mute_events"})
# `mute_events` entries are `<object>:<event>` or `<object>:*`. Only top-level instant notices can be
# muted, and only with the event names the script really emits, so a typo fails loud instead of muting
# nothing. Thread records (issue/mr/note/milestone, MR pipeline results) and `sync` are never mutable.
MUTABLE_EVENTS = {
    "pipeline": frozenset({"failed"}),
    "deployment": frozenset({"failed", "blocked"}),
    "access_token": frozenset({"expiring"}),
    "tag": frozenset({"tag_created", "tag_deleted", "pushed"}),
    "release": frozenset({"created"}),
}
MUTE_EVENT_RE = re.compile(r"([a-z_]+):([a-z_]+|\*)")
GITLAB_CONFIG_KEYS = frozenset({"base_url", "token_env", "bot_user_id", "bot_username", "projects"})
BUZZ_CONFIG_KEYS = frozenset({"cli_path", "cli_sha256"})
DIFF_CONFIG_KEYS = frozenset({"enabled", "private"})


class SyncError(RuntimeError):
    """Fail-closed error: the run must stop without further writes."""


class GitLabHTTPError(SyncError):
    """GitLab answered with an HTTP error status."""

    def __init__(self, method: str, path: str, status: int):
        super().__init__(f"GitLab {method} {path} failed: HTTP {status}")
        self.status = status


class ObjectError(SyncError):
    """Bad data on one GitLab object or its Buzz thread: stop before crossing its cursor."""


class OriginUnusable(ObjectError):
    """A well-formed origin names a root that cannot be used (ADR-0014): the object syncs as if it had no origin."""


class BuzzCliError(SyncError):
    """The Buzz CLI exited non-zero; exit 1 is bad input or not found, 2+ are relay, auth or other failures."""

    def __init__(self, command: str, returncode: int):
        super().__init__(f"Buzz CLI {command} failed ({returncode})")
        self.returncode = returncode


class BuzzSendRejected(SyncError):
    """The send definitively did not happen: no event can exist, so a bound retry is safe."""


class RelatedQueryError(SyncError):
    """A routing related-object lookup failed; the record degrades to digest and the round continues."""


def _related_failure(exc: BaseException) -> str:
    """One redaction-safe token for why a related lookup degraded: the HTTP status or the type name."""

    return f"HTTP {exc.status}" if isinstance(exc, GitLabHTTPError) else type(exc).__name__


def _object_data(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Run pure per-object parsing; its validation errors stall only that object."""

    try:
        return function(*args, **kwargs)
    except GitLabHTTPError:
        raise
    except SyncError as exc:
        raise ObjectError(str(exc)) from None
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        raise ObjectError(f"unexpected {type(exc).__name__} in GitLab object data") from None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_timestamp(value: Any, name: str = "timestamp") -> dt.datetime:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise SyncError(f"{name} must be an ISO-8601 timestamp with timezone")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncError(f"{name} is not a valid timestamp") from exc


# ── shared text helpers ──────────────────────────────────────────────────────


def neutralize(text: str) -> str:
    """Stop the Buzz CLI from resolving GitLab-authored `@Name` or `nostr:npub1…` into mentions."""

    return NOSTR_URI_RE.sub(r"\1：", text.replace("@", "＠"))


def _single_line(value: Any) -> str:
    return " ".join(str(value).split())


def sanitize_title(title: Any) -> str:
    if not isinstance(title, str):
        raise SyncError("title must be a string")
    # str.split() also splits on CR, LF, U+2028 and U+2029.
    return " ".join(title.split())[:TITLE_LIMIT]


def label_value(labels: Any, prefix: str) -> str:
    if not isinstance(labels, list):
        raise SyncError("labels must be a list")
    values = [
        label[len(prefix):]
        for label in labels
        if isinstance(label, str) and label.startswith(prefix)
    ]
    if len(values) == 1 and LABEL_VALUE_RE.fullmatch(values[0]):
        return values[0]
    return "unknown"


def _list_value(values: set[str]) -> list[str]:
    return sorted(value for value in values if value)


def _label_text(label: str) -> str:
    text = neutralize(_single_line(label)).replace(",", "，")
    return "－" if text == "-" else text  # "-" is how an empty list renders


def _label_list(labels: Any, skip_prefixes: tuple[str, ...] = ()) -> list[str]:
    return _list_value({
        _label_text(label)
        for label in labels or []
        if isinstance(label, str) and not (skip_prefixes and label.startswith(skip_prefixes))
    })


def _usernames(items: Any) -> list[str]:
    return _list_value({
        neutralize(_single_line(item.get("username", "")))
        for item in items or []
        if isinstance(item, dict)
    })


def _valid_url(value: Any, subject: str) -> str:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise SyncError(f"{subject} web_url is invalid")
    return value


def _trailer_from_groups(groups: tuple[str | None, ...]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    desc, note, events = groups[-3], groups[-2], groups[-1]
    if desc:
        extra["desc"] = desc
    if note:
        extra["note"] = int(note)
    if events:
        extra["events"] = [token for token in events.split(",") if EVENT_KEY_RE.fullmatch(token)]
    return extra


def _header_trailer(*, desc: str | None = None, note: int | None = None,
                    events: list[str] | tuple[str, ...] | None = None) -> str:
    parts: list[str] = []
    if desc not in (None, ""):
        parts.append(f"[desc:{desc}]")
    if note is not None:
        parts.append(f"[note:{note}]")
    if events:
        parts.append("[events:" + ",".join(events) + "]")
    return "".join(parts)


def _with_trailer(header: str, *, desc: str | None = None, note: int | None = None,
                  events: list[str] | tuple[str, ...] | None = None) -> str:
    parsed = _parse_header_line(header)
    if parsed is None:
        raise SyncError("header is invalid")
    base = HEADER_TRAILER_STRIP.sub("", header)
    merged = base + _header_trailer(
        desc=parsed.get("desc") if desc is None else desc,
        note=parsed.get("note") if note is None else note,
        events=parsed.get("events") if events is None else list(events),
    )
    if _parse_header_line(merged) is None:
        raise SyncError("rendered header trailer is invalid")
    return merged


def _parse_header_line(line: str) -> dict[str, Any] | None:
    match = ISSUE_HEADER_RE.fullmatch(line)
    if match:
        groups = match.groups()
        type_, status, state, change, project, iid = groups[:6]
        return {
            "object": "issue",
            "type": type_,
            "status": status,
            "state": state,
            "change": change,
            "project": int(project),
            "issue": int(iid),
            **_trailer_from_groups(groups),
        }
    match = MR_HEADER_RE.fullmatch(line)
    if match:
        groups = match.groups()
        state, draft, change, transition, project, iid = groups[:6]
        return {
            "object": "mr",
            "state": state,
            "draft": draft,
            "change": change,
            "transition": transition,
            "project": int(project),
            "mr": int(iid),
            **_trailer_from_groups(groups),
        }
    match = BRANCH_HEADER_RE.fullmatch(line)
    if match:
        groups = match.groups()
        project, branch_hash = groups[:2]
        return {
            "object": "branch",
            "change": "activity",
            "project": int(project),
            "branch": branch_hash,
            **_trailer_from_groups(groups),
        }
    match = TOP_HEADER_RE.fullmatch(line)
    if match:
        groups = match.groups()
        object_kind, event, project = groups[:3]
        return {"object": object_kind, "event": event, "project": int(project), **_trailer_from_groups(groups)}
    return None


def header_line(content: Any) -> str | None:
    """Machine header: first line on legacy facts, last non-empty line on new facts."""

    if not isinstance(content, str) or not content:
        return None
    lines = content.split("\n")
    if _parse_header_line(lines[0]) is not None:
        return lines[0]
    last = next((line for line in reversed(lines) if line != ""), "")
    if last and _parse_header_line(last) is not None:
        return last
    return None


def parse_header(content: Any) -> dict[str, Any] | None:
    line = header_line(content)
    return None if line is None else _parse_header_line(line)


def matches_trigger_prefix(content: Any, prefix: str) -> bool:
    line = header_line(content)
    return isinstance(prefix, str) and bool(line) and line.startswith(prefix)


def _legacy_header_first(content: str) -> bool:
    return bool(content) and _parse_header_line(content.split("\n", 1)[0]) is not None


def notice_body_lines(content: str) -> list[str]:
    """Human-visible plus machine-footer lines, with the header stripped."""

    lines = str(content or "").split("\n")
    if not lines:
        return []
    if _parse_header_line(lines[0]) is not None:
        return lines[1:]
    while lines and lines[-1] == "":
        lines = lines[:-1]
    if lines and _parse_header_line(lines[-1]) is not None:
        return lines[:-1]
    return lines


def _notice(body: list[str], header: str) -> str:
    return "\n".join([*body, header])


def _extend_notice(notice: str, extra: list[str]) -> str:
    line = header_line(notice)
    if line is None:
        raise SyncError("notice is missing a header")
    body_extra: list[str] = []
    note: int | None = None
    events: list[str] | None = None
    for item in extra:
        match = NOTE_LINE_RE.fullmatch(item)
        if match:
            note = int(match.group(1))
            continue
        if item.startswith("events: "):
            events = [token for token in item[len("events: "):].split(",") if EVENT_KEY_RE.fullmatch(token)]
            continue
        body_extra.append(item)
    return _notice(
        [*notice_body_lines(notice), *body_extra],
        _with_trailer(line, note=note, events=events),
    )


NOTIFIED_PREFIX = "🔔 通知 "
CONTENT_BYTE_LIMIT = 65_000  # the Buzz CLI rejects message content over 65,536 bytes; keep room for the line
PROFILE_CHUNK = 20  # pubkeys per `buzz users get` call
# A display name is written as `@name` only when it is one plain token: unicode word characters, dots and hyphens,
# no spaces or quotes or colons, so the CLI reads exactly that token and nothing else.
DISPLAY_MENTION_RE = re.compile(r"\w[\w.\-]{0,31}")


def notified_tokens(
    pubkeys: list[str] | tuple[str, ...], people: dict[str, str], names: dict[str, str] | None,
) -> list[str]:
    """One visible token per notified person, in mention order, deduplicated.

    `@<display name>` (a real mention, the client highlights it) when that name is a plain token, is not a reserved
    group word, and no *other* channel member's name contains it (so the CLI's resolver can only find this person);
    otherwise the plain GitLab username, or a short pubkey when the person is not in `people`. Fallbacks carry no
    ASCII `@` and go through the same neutralization as every other GitLab-derived text. The explicit `p` tags are
    unchanged either way, so the readback still compares an exact set."""

    by_key: dict[str, str] = {}
    for username in sorted(people):
        by_key.setdefault(people[username], username)
    known = {key: value for key, value in (names or {}).items() if isinstance(value, str)}
    folded = {key: value.casefold() for key, value in known.items()}
    tokens: list[str] = []
    for key in pubkeys:
        display = known.get(key, "")
        token = None
        if (
            display
            and DISPLAY_MENTION_RE.fullmatch(display)
            and display.casefold() not in responsible.RESERVED_NAMES
            and not any(other != key and display.casefold() in name for other, name in folded.items())
        ):
            token = "@" + display
        if token is None:
            token = neutralize(_single_line(by_key.get(key) or f"{key[:8]}…"))
        if token not in tokens:
            tokens.append(token)
    return tokens


def with_notified_line(content: str, tokens: list[str]) -> str:
    """Add `🔔 通知 @a @b` just above the machine header (last body line stays a `note:`/`events:` machine line, the
    header stays last); legacy header-first bodies get it at the end. No tokens, or a message that would pass the
    CLI's size limit, is returned unchanged: the `p` tags still notify."""

    if not tokens:
        return content
    line = NOTIFIED_PREFIX + " ".join(tokens)
    header = header_line(content)
    if header is None or _legacy_header_first(content):
        out = content.rstrip("\n") + "\n" + line
    else:
        body = notice_body_lines(content)
        index = len(body)
        while index > 0 and (
            body[index - 1] == "" or NOTE_LINE_RE.fullmatch(body[index - 1]) or body[index - 1].startswith("events: ")
        ):
            index -= 1
        gap = [""] if index and body[index - 1] != "" else []
        out = _notice([*body[:index], *gap, line, *body[index:]], header)
    return out if len(out.encode("utf-8")) <= CONTENT_BYTE_LIMIT else content


def _footer_line(content: str) -> str:
    for line in reversed(notice_body_lines(content)):
        if line:
            return line
    return ""


def _machine_scan_lines(content: str) -> list[str]:
    """Lines that may carry note:/events: keys. New facts: last body line only."""

    if _legacy_header_first(content):
        return notice_body_lines(content)
    footer = _footer_line(content)
    return [footer] if footer else []


def _posted_note_ids(content: str) -> set[int]:
    posted: set[int] = set()
    header = parse_header(content)
    if header and header.get("note") is not None:
        posted.add(int(header["note"]))
    for line in _machine_scan_lines(content):
        match = NOTE_LINE_RE.fullmatch(line)
        if match:
            posted.add(int(match.group(1)))
    return posted


def _posted_event_keys(content: str) -> set[str]:
    keys: set[str] = set()
    header = parse_header(content)
    if header:
        keys |= {token for token in header.get("events") or [] if EVENT_KEY_RE.fullmatch(token)}
    for line in _machine_scan_lines(content):
        if line.startswith("events: "):
            keys |= {token for token in line[len("events: "):].split(",") if EVENT_KEY_RE.fullmatch(token)}
    return keys


def _comment_lines(note: dict[str, Any]) -> list[str]:
    note_id = note.get("id")
    if not _positive_int(note_id):
        raise SyncError("comment note id must be a positive integer")
    author = neutralize(_single_line((note.get("author") or {}).get("username") or "?"))
    markdown = _note_markdown(note.get("body"))
    lines = [f"by: {author}", ""]
    if markdown:
        lines.extend(markdown.split("\n"))
    lines.append(f"note: {note_id}")
    return lines


def _sanitize_comment_line(line: str) -> str:
    """Keep markdown, but stop a GitLab note from forging header / note / events lines."""

    text = neutralize(line.rstrip("\n"))
    if _parse_header_line(text) is not None:
        return text.replace("[gitlab-notify:", "[gitlab-notify：", 1)
    if NOTE_LINE_RE.fullmatch(text):
        return text.replace("note:", "note：", 1)
    if text.startswith("events: "):
        return "events：" + text[len("events:"):]
    return text


def _note_markdown(body: Any) -> str:
    """A note body without HTML comments, newlines kept so Buzz can render markdown."""

    text = re.sub(r"<!--.*?-->", "", str(body or ""), flags=re.S)
    lines = [_sanitize_comment_line(line) for line in text.splitlines()]
    rendered = "\n".join(lines).strip("\n")
    if len(rendered) > COMMENT_LIMIT:
        rendered = rendered[:COMMENT_LIMIT].rstrip() + "…"
    return rendered


def _note_text(body: Any) -> str:
    """Legacy one-line excerpt; kept for tests and older callers that still fold comments."""

    text = re.sub(r"<!--.*?-->", " ", str(body or ""), flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    return neutralize(_single_line(text))


def _message_fields(content: str, keys: tuple[str, ...]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in notice_body_lines(content):
        key, sep, value = line.partition(": ")
        if sep and key in keys and key not in fields:
            fields[key] = value
    return fields


MR_FIELD_SEP = " · "


def _field_text(value: str) -> str:
    """Free text inside a compact MR line must not contain the field separator, or readback splits it."""

    return value.replace("·", "•")
MR_PEOPLE_KEYS = ("labels", "reviewers", "assignees", "milestone")


def _compact_mr_fields(content: str, mr_iid: int) -> dict[str, str]:
    """Read back the compact MR body; the legacy `key: value` body is handled by _message_fields."""

    lines = notice_body_lines(content)
    fields: dict[str, str] = {}
    prefix = f"!{mr_iid} "
    if not lines or not lines[0].startswith(prefix):
        return fields
    title, sep, author = lines[0][len(prefix):].rpartition(MR_FIELD_SEP)
    fields["title"], fields["author"] = (title, author) if sep else (lines[0][len(prefix):], "?")
    if len(lines) > 1:
        branches, *parts = lines[1].split(MR_FIELD_SEP)
        fields["branches"] = branches
        for part in parts:
            key, _, value = part.partition(" ")
            if key in ("sha", "desc"):
                fields["sha" if key == "sha" else "description"] = value
    for line in lines[2:]:
        if line.startswith(MR_PEOPLE_KEYS):
            for part in line.split(MR_FIELD_SEP):
                key, _, value = part.partition(" ")
                if key in MR_PEOPLE_KEYS and key not in fields:
                    fields[key] = value
        elif line.startswith(("http://", "https://")) and "url" not in fields:
            fields["url"] = line
    return fields


def _as_list(value: str) -> list[str]:
    return [] if value in {"", "-"} else value.split(",")


# ── plaque roots and styled bodies (issue #78) ───────────────────────────────


def _md_escape(text: str) -> str:
    """GitLab free text inside markdown link text or bold lines cannot forge syntax."""

    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_unescape(text: str) -> str:
    return text.replace("\\]", "]").replace("\\[", "[").replace("\\\\", "\\")


def plaque_url(content: Any) -> str | None:
    """The bare object URL on the last non-empty line — a plaque's machine identity."""

    if not isinstance(content, str):
        return None
    lines = [line for line in content.split("\n") if line.strip()]
    if not lines:
        return None
    last = lines[-1].strip()
    return last if PLAQUE_URL_RE.fullmatch(last) else None


def url_path_tail(query: str) -> str:
    """`/-/issues/818` for `http://host/group/project/-/issues/818`; any other query is returned unchanged."""

    if PLAQUE_URL_RE.fullmatch(query) and "/-/" in query:
        return "/-/" + query.split("/-/", 1)[1]
    return query


def canonical_plaque_url(url: str) -> str:
    """The /-/issues/N form of an issue URL: new plaques are written in it and recovery compares in it, while a
    plaque an older run published with the work_items form stays readable (engineering/skills#101)."""

    return PLAQUE_WORK_ITEM_TAIL_RE.sub(r"/-/issues/\1", url)


def plaque_identity(url: str) -> dict[str, Any] | None:
    """(object, iid) carried by a plaque URL; branch plaques carry no iid (ref is the URL tail)."""

    match = PLAQUE_ISSUE_TAIL_RE.search(url)
    if match:
        return {"object": "issue", "iid": int(match.group(1))}
    match = PLAQUE_MR_TAIL_RE.search(url)
    if match:
        return {"object": "mr", "iid": int(match.group(1))}
    match = PLAQUE_MILESTONE_TAIL_RE.search(url)
    if match:
        return {"object": "milestone", "iid": int(match.group(1))}
    if PLAQUE_BRANCH_MARK in url:
        return {"object": "branch"}
    return None


def branch_plaque_url(web_url: str, ref: str) -> str:
    return f"{web_url.rstrip('/')}/-/tree/{urllib.parse.quote(normalize_git_ref(ref), safe='/')}"


def render_issue_plaque(fact: dict[str, Any], project_path: str) -> str:
    return "\n".join([
        f"{OBJECT_ICON['issue']} **#{fact['issue']} {_md_escape(fact['title'])}**",
        project_path,
        canonical_plaque_url(fact["url"]),
    ])


def render_mr_plaque(fact: dict[str, Any], project_path: str) -> str:
    return "\n".join([
        f"{OBJECT_ICON['mr']} **!{fact['mr']} {_md_escape(fact['title'])}**",
        project_path,
        fact["url"],
    ])


def render_branch_plaque(ref: str, url: str, project_path: str) -> str:
    return "\n".join([
        f"{OBJECT_ICON['branch']} **{_md_escape(neutralize(normalize_git_ref(ref)))}**",
        project_path,
        url,
    ])


def render_milestone_plaque(title: str, url: str, project_path: str) -> str:
    return "\n".join([
        f"{OBJECT_ICON['milestone']} **{_md_escape(title)}**",
        project_path,
        url,
    ])


def _issue_style(fact: dict[str, Any], change: str, first: bool,
                 state_changed: bool | None) -> tuple[str, str]:
    if first:
        return OBJECT_ICON["issue"], f"首次同步 · {STATE_WORDS[fact['state']]}"
    if change == "routing":
        if state_changed:
            return "🔄", f"状态流转 → {STATE_WORDS[fact['state']]}"
        return "🏷", "归类变更"
    if change == "content":
        return "✏️", "标题/描述更新"
    return "🏷", "字段更新"


def _mr_style(fact: dict[str, Any], change: str, became_reviewable: bool, first: bool,
              sha_changed: bool | None) -> tuple[str, str]:
    draft = " · Draft" if fact["draft"] == "yes" else ""
    if change == "lifecycle":
        if became_reviewable:
            return "👀", "可评审"
        if first:
            return OBJECT_ICON["mr"], f"新建 · {STATE_WORDS[fact['state']]}{draft}"
        return "🔄", f"状态流转 → {STATE_WORDS[fact['state']]}{draft}"
    if change == "update":
        return ("📦", "新提交") if sha_changed else ("✏️", "字段更新")
    return "💬", "活动"


ACTIVITY_STYLE = {
    ("pipeline", "success"): ("✅", "流水线通过"),
    ("pipeline", "failed"): ("❌", "流水线失败"),
    ("pipeline", "canceled"): ("⚪", "流水线取消"),
    ("deployment", "failed"): ("🔴", "部署失败"),
    ("deployment", "blocked"): ("🟠", "部署受阻"),
    ("deployment", "running"): ("🚀", "部署中"),
    ("deployment", "success"): ("🚀", "部署完成"),
    ("deployment", "canceled"): ("⚪", "部署取消"),
    ("push", "push"): ("📦", "推送"),
    ("note", "commented"): ("💬", "评论"),
    ("mr", "approved"): ("✔", "已批准"),
}

INSTANT_STYLE = {
    ("deployment", "failed"): ("🔴", "部署失败"),
    ("deployment", "blocked"): ("🟠", "部署受阻"),
    ("pipeline", "failed"): ("❌", "主分支流水线失败"),
    ("access_token", "expiring"): ("⏳", "Access token 即将到期"),
    ("sync", "stalled"): ("⚠️", "同步卡住（该对象本轮被跳过）"),
    ("tag", "tag_created"): ("🏷", "新建 tag"),
    ("tag", "tag_deleted"): ("🏷", "删除 tag"),
    ("release", "created"): ("🚀", "新 Release"),
    ("milestone", "created"): ("🎯", "里程碑创建"),
    ("milestone", "opened"): ("🎯", "里程碑启动"),
    ("milestone", "started"): ("🎯", "里程碑启动"),
    ("milestone", "closed"): ("🎯", "里程碑关闭"),
    ("milestone", "reopened"): ("🎯", "里程碑重启"),
    ("milestone", "expired"): ("🎯", "里程碑到期"),
    ("milestone", "destroyed"): ("🎯", "里程碑删除"),
}


def _activity_style(record: dict[str, Any]) -> tuple[str, str]:
    return ACTIVITY_STYLE.get((record.get("object"), record.get("event")), ("💬", "活动"))


_ISSUE_HEADLINE_RE = re.compile(r".+ \*\*.+\*\* · \[#([1-9][0-9]*)(?: (.*))?\]\((https?://[^)\s]+)\)")
_MR_HEADLINE_RE = re.compile(
    r".+ \*\*.+\*\* · \[!([1-9][0-9]*)(?: (.*))?\]\((https?://[^)\s]+)\) · (.+)")
ISSUE_META_KEYS = ("labels", "assignees", "milestone", "desc")


def _styled_issue_fields(content: str) -> dict[str, str] | None:
    """Read back the styled issue body (issue #78); None marks a legacy body."""

    lines = notice_body_lines(content)
    if not lines:
        return None
    match = _ISSUE_HEADLINE_RE.fullmatch(lines[0])
    if match is None:
        return None
    iid, title, url = match.groups()
    fields = {"title": _md_unescape(title or ""), "url": url}
    if len(lines) > 1 and lines[1].split(" ", 1)[0] in ISSUE_META_KEYS:
        for part in lines[1].split(MR_FIELD_SEP):
            key, _, value = part.partition(" ")
            if key in ISSUE_META_KEYS:
                fields["description" if key == "desc" else key] = value
    return fields


def _styled_mr_fields(content: str, mr_iid: int) -> dict[str, str] | None:
    """Read back the styled MR body (issue #78); None marks a legacy body."""

    lines = notice_body_lines(content)
    if not lines:
        return None
    match = _MR_HEADLINE_RE.fullmatch(lines[0])
    if match is None or int(match.group(1)) != mr_iid:
        return None
    _, title, url, author = match.groups()
    fields = {"title": _md_unescape(title or ""), "url": url, "author": author}
    if len(lines) > 1:
        parts = lines[1].split(MR_FIELD_SEP)
        fields["branches"] = parts[0]
        for part in parts[1:]:
            key, _, value = part.partition(" ")
            if key in ("sha", "desc", *MR_PEOPLE_KEYS):
                fields["description" if key == "desc" else key] = value
    return fields


def _latest_publisher_message(
    events: list[dict[str, Any]], publisher_pubkey: str, project_id: int, object_kind: str, iid: int,
    skip_change: str | tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    skipped = (skip_change,) if isinstance(skip_change, str) else tuple(skip_change or ())
    latest: tuple[tuple[Any, Any], dict[str, Any], dict[str, Any]] | None = None
    for event in events:
        if event.get("pubkey") != publisher_pubkey:
            continue
        header = parse_header(event.get("content"))
        if (
            not header
            or header["object"] != object_kind
            or header["project"] != project_id
            or header.get(object_kind) != iid
            or header.get("change") in skipped
        ):
            continue
        key = (event.get("created_at", 0), event.get("id", ""))
        if latest is None or key > latest[0]:
            latest = (key, event, header)
    return None if latest is None else (latest[1], latest[2])


# ── Issue facts ──────────────────────────────────────────────────────────────


def issue_fact(issue: dict[str, Any], project_id: int) -> dict[str, Any]:
    if not _positive_int(project_id):
        raise SyncError("project id must be a positive integer")
    iid = issue.get("iid")
    if not _positive_int(iid):
        raise SyncError("issue iid must be a positive integer")
    state = issue.get("state")
    if state not in {"opened", "closed"}:
        raise SyncError(f"issue {iid} state must be opened or closed")
    labels = issue.get("labels")
    description = issue.get("description") or ""
    if not isinstance(description, str):
        raise SyncError(f"issue {iid} description must be a string")
    milestone = issue.get("milestone")
    return {
        "type": label_value(labels, "type::"),
        "status": label_value(labels, "status::"),
        "state": state,
        "title": neutralize(sanitize_title(issue.get("title"))),
        "project": project_id,
        "issue": iid,
        "url": _valid_url(issue.get("web_url"), f"issue {iid}"),
        "labels": [_field_text(label) for label in _label_list(labels, ("type::", "status::"))],
        "assignees": _usernames(issue.get("assignees")),
        "milestone": (
            _field_text(neutralize(_single_line(milestone["title"])))
            if isinstance(milestone, dict) and milestone.get("title") else "-"
        ),
        "description": _description_hash(description, f"issue {iid}"),
    }


def _description_hash(description: Any, subject: str) -> str:
    if description is None:
        return "-"
    if not isinstance(description, str):
        raise SyncError(f"{subject} description must be a string")
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:12] if description else "-"


def render_message(fact: dict[str, Any], change: str, *, first: bool = False,
                   state_changed: bool | None = None, style: tuple[str, str] | None = None) -> str:
    if change not in CHANGES:
        raise SyncError(f"change must be one of {CHANGES}")
    header = (
        f"[gitlab-notify:v1][object:issue][type:{fact['type']}][status:{fact['status']}]"
        f"[state:{fact['state']}][change:{change}]"
        f"[project:{fact['project']}][issue:{fact['issue']}]"
    )
    if not ISSUE_HEADER_RE.fullmatch(header):
        raise SyncError("rendered header is invalid")
    header = _with_trailer(header, desc=None if fact["description"] == "-" else fact["description"])
    # Styled body (issue #78): headline answers "why was this sent", the meta line keeps
    # every field the next run compares machine-readable; empty fields are omitted.
    # desc lives in the header trailer (script-only).
    icon, phrase = style or _issue_style(fact, change, first, state_changed)
    title = _md_escape(fact["title"])
    link_text = f"#{fact['issue']} {title}" if title else f"#{fact['issue']}"
    meta = []
    if fact["labels"]:
        meta.append(f"labels {','.join(fact['labels'])}")
    if fact["assignees"]:
        meta.append(f"assignees {','.join(fact['assignees'])}")
    if fact["milestone"] != "-":
        meta.append(f"milestone {fact['milestone']}")
    return _notice([
        f"{icon} **{phrase}** · [{link_text}]({fact['url']})",
        *([MR_FIELD_SEP.join(meta)] if meta else []),
    ], header)


def _comment_fact(fact: dict[str, Any], note: dict[str, Any]) -> dict[str, Any]:
    """The fact whose headline link lands on the comment itself (GitLab's #note_<id> anchor).

    The link is display-only: neither the previous-fact readback nor note de-duplication reads it.
    """

    note_id = note.get("id")
    if not _positive_int(note_id):
        raise SyncError("comment note id must be a positive integer")
    return {**fact, "url": f"{fact['url']}#note_{note_id}"}


def render_comment_message(fact: dict[str, Any], note: dict[str, Any]) -> str:
    return _extend_notice(
        render_message(_comment_fact(fact, note), "activity", style=("💬", "评论")),
        _comment_lines(note),
    )


def classify_change(previous: dict[str, Any] | None, current: dict[str, Any]) -> str | None:
    if previous is None:
        return "routing"
    if any(previous.get(key) != current[key] for key in ("type", "status", "state")):
        return "routing"
    if previous.get("title") != current["title"]:
        return "content"
    if any(previous.get(key) != current[key] for key in ("labels", "assignees", "milestone", "description")):
        return "activity"
    return None


def previous_fact_from_thread(
    events: list[dict[str, Any]], publisher_pubkey: str, project_id: int, issue_iid: int
) -> dict[str, Any] | None:
    latest = _latest_publisher_message(events, publisher_pubkey, project_id, "issue", issue_iid)
    if latest is None:
        return None
    event, header = latest
    fields = _styled_issue_fields(event["content"])
    if fields is None:
        fields = _message_fields(event["content"], FACT_LINES)
    return {
        "type": header["type"],
        "status": header["status"],
        "state": header["state"],
        "title": fields.get("title", ""),
        "labels": _as_list(fields.get("labels", "-")),
        "assignees": _as_list(fields.get("assignees", "-")),
        "milestone": fields.get("milestone", "-"),
        "description": header.get("desc") or fields.get("description", "-"),
    }


# ── MR facts and mentions ────────────────────────────────────────────────────


def mr_fact(mr: dict[str, Any], project_id: int) -> dict[str, Any]:
    if not _positive_int(project_id):
        raise SyncError("project id must be a positive integer")
    iid = mr.get("iid")
    if not _positive_int(iid):
        raise SyncError("merge request iid must be a positive integer")
    state = mr.get("state")
    if state not in MR_STATES:
        raise SyncError(f"merge request {iid} state must be one of {MR_STATES}")
    draft = mr.get("draft", mr.get("work_in_progress", False))
    if not isinstance(draft, bool):
        raise SyncError(f"merge request {iid} draft must be a boolean")
    sha = mr.get("sha")
    source = neutralize(_single_line(mr.get("source_branch") or "-"))
    target = neutralize(_single_line(mr.get("target_branch") or "-"))
    return {
        "state": state,
        "draft": "yes" if draft else "no",
        "title": neutralize(sanitize_title(mr.get("title"))),
        "project": project_id,
        "mr": iid,
        "url": _valid_url(mr.get("web_url"), f"merge request {iid}"),
        "branches": f"{source} -> {target}",
        "sha": sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{7,64}", sha) else "-",
        "labels": [_field_text(label) for label in _label_list(mr.get("labels"))],
        "reviewers": _usernames(mr.get("reviewers")),
        "assignees": _usernames(mr.get("assignees")),
        "milestone": (
            _field_text(neutralize(_single_line(mr["milestone"]["title"])))
            if isinstance(mr.get("milestone"), dict) and mr["milestone"].get("title") else "-"
        ),
        "description": _description_hash(mr.get("description"), f"merge request {iid}"),
        "author": neutralize(_single_line((mr.get("author") or {}).get("username") or "?")),
    }


def render_mr_message(fact: dict[str, Any], change: str, unmapped: list[str] | tuple[str, ...] = (),
                      issue_links: list[str] | tuple[str, ...] = (), *,
                      became_reviewable: bool = False, first: bool = False,
                      sha_changed: bool | None = None,
                      style: tuple[str, str] | None = None) -> str:
    if change not in MR_CHANGES:
        raise SyncError(f"merge request change must be one of {MR_CHANGES}")
    if not isinstance(became_reviewable, bool):
        raise SyncError("became_reviewable must be a boolean")
    if became_reviewable and (change != "lifecycle" or fact["state"] != "opened" or fact["draft"] != "no"):
        raise SyncError("reviewable transition requires an opened non-draft lifecycle fact")
    transition = "reviewable" if became_reviewable else "none"
    header = (
        f"[gitlab-notify:v1][object:mr][state:{fact['state']}][draft:{fact['draft']}]"
        f"[change:{change}][transition:{transition}][project:{fact['project']}][mr:{fact['mr']}]"
    )
    if not MR_HEADER_RE.fullmatch(header):
        raise SyncError("rendered merge request header is invalid")
    header = _with_trailer(header, desc=fact["description"])
    # Styled body (issue #78, evolving the 2026-09-17 compact format): the headline answers
    # "why was this sent" with the title as a markdown link; every field the next run
    # compares stays machine-readable on the meta line, empty ones are omitted.
    # desc lives in the header trailer (script-only).
    icon, phrase = style or _mr_style(fact, change, became_reviewable, first, sha_changed)
    title = _md_escape(fact["title"])
    link_text = f"!{fact['mr']} {title}" if title else f"!{fact['mr']}"
    meta = [fact["branches"], f"sha {_short_sha(fact['sha'])}"]
    meta.extend(f"{key} {','.join(fact[key])}" for key in ("labels", "reviewers", "assignees") if fact[key])
    if fact["milestone"] != "-":
        meta.append(f"milestone {fact['milestone']}")
    body = [
        f"{icon} **{phrase}** · [{link_text}]({fact['url']}){MR_FIELD_SEP}{fact['author']}",
        MR_FIELD_SEP.join(meta),
    ]
    if issue_links:
        body.append("issues: " + ",".join(issue_links))
    if unmapped:
        body.append("unmapped: " + ",".join(neutralize(_single_line(name)) for name in unmapped))
    return _notice(body, header)


def render_mr_comment_message(fact: dict[str, Any], note: dict[str, Any]) -> str:
    return _extend_notice(
        render_mr_message(_comment_fact(fact, note), "activity", style=("💬", "评论")),
        _comment_lines(note),
    )


def render_mr_activity(fact: dict[str, Any], record: dict[str, Any], jobs: list[str] | tuple[str, ...] = ()) -> str:
    extra: list[str] = []
    if record.get("actor") not in (None, "", "-", "?"):
        extra.append(f"by: {record['actor']}")
    if jobs:
        extra.append("jobs: " + ",".join(neutralize(_single_line(job)).replace(",", "，") for job in jobs))
    extra.append(f"events: {record['key']}")
    return _extend_notice(
        render_mr_message(fact, "activity", style=_activity_style(record)),
        extra,
    )


XREF_STYLE = ("🔗", "MR 关联 · 事实在别处")


def render_mr_xref(fact: dict[str, Any], thread_link: str) -> str:
    """The cross-link an MR leaves, once, in every related thread that is not its binding thread (ADR-0015).

    It is not a fact: no mention, no reviewable transition (`change:xref`, `transition:none`), and readers of a
    thread's MR history skip it. The headline keeps the styled MR shape (title link, author); the second line points
    at the binding thread, where every later status, comment, commit, pipeline and Diff goes."""

    if not isinstance(thread_link, str) or not thread_link.startswith("buzz://message?") \
            or any(ch.isspace() for ch in thread_link):
        raise SyncError("cross-link target must be one Buzz message link")
    header = (
        f"[gitlab-notify:v1][object:mr][state:{fact['state']}][draft:{fact['draft']}]"
        f"[change:{MR_XREF}][transition:none][project:{fact['project']}][mr:{fact['mr']}]"
    )
    if not MR_HEADER_RE.fullmatch(header):
        raise SyncError("rendered merge request cross-link header is invalid")
    title = _md_escape(fact["title"])
    link_text = f"!{fact['mr']} {title}" if title else f"!{fact['mr']}"
    icon, phrase = XREF_STYLE
    return _notice([
        f"{icon} **{phrase}** · [{link_text}]({fact['url']}){MR_FIELD_SEP}{fact['author']}",
        f"之后的状态、评论、新提交、流水线和 Diff 只在那个 Thread：{thread_link}",
    ], header)


def mr_xref_posted(events: list[dict[str, Any]], publisher_pubkey: str, project_id: int, mr_iid: int) -> bool:
    """Whether Desk already left this MR's cross-link in the thread: the idempotency check, like the last-fact read."""

    for event in events:
        if event.get("pubkey") != publisher_pubkey:
            continue
        header = parse_header(event.get("content"))
        if (
            header is not None and header["object"] == "mr" and header.get("change") == MR_XREF
            and header["project"] == project_id and header.get("mr") == mr_iid
        ):
            return True
    return False


def _short_sha(sha: Any) -> str:
    return sha[:12] if isinstance(sha, str) and sha != "-" else "-"


def classify_mr_change(previous: dict[str, Any] | None, current: dict[str, Any]) -> str | None:
    if previous is None:
        return "lifecycle"
    if any(previous.get(key) != current[key] for key in ("state", "draft")):
        return "lifecycle"
    if _short_sha(previous.get("sha")) != _short_sha(current["sha"]):
        return "update"
    if any(previous.get(key) != current[key]
           for key in ("labels", "reviewers", "title", "branches", "assignees", "milestone", "description")):
        return "update"
    return None


def previous_mr_fact_from_thread(
    events: list[dict[str, Any]], publisher_pubkey: str, project_id: int, mr_iid: int
) -> dict[str, Any] | None:
    # Activity replies (comments, pipelines, approvals) render whatever MR state was read with them; only
    # root, lifecycle and update messages record the state this sync has acknowledged. A cross-link (ADR-0015)
    # carries no snapshot at all.
    latest = _latest_publisher_message(
        events, publisher_pubkey, project_id, "mr", mr_iid, skip_change=("activity", MR_XREF))
    if latest is None:
        return None
    event, header = latest
    fields = _styled_mr_fields(event["content"], mr_iid)
    if fields is None:
        fields = _message_fields(event["content"], MR_FACT_LINES)
        if "sha" not in fields:
            fields = _compact_mr_fields(event["content"], mr_iid)
    return {
        "state": header["state"],
        "draft": header["draft"],
        "title": fields.get("title", ""),
        "branches": fields.get("branches", "-"),
        "sha": fields.get("sha", "-"),
        "labels": _as_list(fields.get("labels", "-")),
        "reviewers": _as_list(fields.get("reviewers", "-")),
        "assignees": _as_list(fields.get("assignees", "-")),
        "milestone": fields.get("milestone", "-"),
        "description": header.get("desc") or fields.get("description", "-"),
        "author": fields.get("author", "?"),
    }


GITLAB_BOT_USERNAME_RE = re.compile(r"(?:project|group)_[0-9]+_bot")

# MR→Issue branch-name association (2026-09-17 决策): only the LAST path segment
# may associate, and only in GitLab's own issue-branch shapes — `<key>-<iid>-<slug>`
# (e.g. naturehood-401-video-search) or a bare `<iid>-<slug>` where the slug must
# contain a letter (so date-shaped tails like release-2026-09-16 never associate).
_BRANCH_ISSUE_RE = re.compile(r"^(?:[a-z][a-z0-9]*-)?([1-9][0-9]{1,5})-(?=[a-z0-9]*[a-z])[a-z0-9][a-z0-9.-]+$")


def branch_issue_iids(source_branch: str) -> list[int]:
    """The single issue iid a source branch names, under the whitelist shapes only."""

    segment = str(source_branch or "").rstrip("/").rsplit("/", 1)[-1]
    match = _BRANCH_ISSUE_RE.fullmatch(segment)
    return [int(match.group(1))] if match else []


def normalize_git_ref(ref: str) -> str:
    value = _single_line(ref or "")
    for prefix in ("refs/heads/", "refs/tags/"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def branch_key(project_id: int, ref: str) -> str:
    if not _positive_int(project_id):
        raise SyncError("project id must be a positive integer")
    name = normalize_git_ref(ref)
    if not name:
        raise SyncError("branch ref is empty")
    return hashlib.sha256(f"{project_id}\0{name}".encode()).hexdigest()[:12]


def ref_is_protected(ref: str, default_branch: str, protected: list[dict[str, Any]] | tuple[dict[str, Any], ...] = ()) -> bool:
    name = normalize_git_ref(ref)
    if not name:
        return False
    if default_branch and name == default_branch:
        return True
    for item in protected:
        pattern = str((item or {}).get("name") or "")
        if pattern and fnmatch.fnmatch(name, pattern):
            return True
    return False


def canonical_mr(mrs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One MR for unassociated git activity: opened non-draft, else newest merged, else draft."""

    eligible = [
        mr for mr in mrs
        if isinstance(mr, dict) and _positive_int(mr.get("iid"))
        and str(mr.get("state") or "") in {"opened", "merged"}
    ]
    if not eligible:
        return None

    def rank(mr: dict[str, Any]) -> tuple[int, str, int]:
        state = str(mr.get("state") or "")
        draft = bool(mr.get("draft") or mr.get("work_in_progress"))
        if state == "opened" and not draft:
            bucket = 3
        elif state == "merged":
            bucket = 2
        elif state == "opened":
            bucket = 1
        else:
            bucket = 0
        return (bucket, str(mr.get("updated_at") or ""), int(mr["iid"]))

    return max(eligible, key=rank)


def strip_target_suffix(source: str, target: str) -> str:
    """`fix/x-staging` targeting `staging` reduces to `fix/x` (2026-09-18 MR-group rule).

    The suffix must be the whole target branch name prefixed by one dash; anything
    else keeps the branch as its own key. Git ref names cannot contain spaces, so
    the `source -> target` split in `mr_group_keys` is unambiguous.
    """

    if not source or not target or source == "-" or target == "-":
        return ""
    suffix = "-" + target
    if not source.endswith(suffix) or len(source) == len(suffix):
        return ""
    return source[: -len(suffix)]


def mr_group_keys(fact: dict[str, Any]) -> tuple[str, str]:
    """(exact, stripped) MR-group keys for an MR fact; the stripped key is author-guarded."""

    source, _, target = str(fact.get("branches") or "").partition(" -> ")
    return source, strip_target_suffix(source, target)


def git_activity_event_lines(record: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if record.get("ref"):
        lines.append(f"ref: {record['ref']}")
    if record.get("actor") not in (None, "", "-", "?"):
        lines.append(f"by: {record['actor']}")
    if record.get("commits"):
        lines.append(f"commits: {record['commits']}")
    if record.get("url"):
        lines.append(f"url: {record['url']}")
    lines.append(f"events: {record['key']}")
    return lines


def render_issue_git_activity(fact: dict[str, Any], record: dict[str, Any]) -> str:
    return _extend_notice(
        render_message(fact, "activity", style=_activity_style(record)),
        git_activity_event_lines(record),
    )


def render_branch_root(project_id: int, ref: str, url: str) -> str:
    name = neutralize(normalize_git_ref(ref))
    header = (
        f"[gitlab-notify:v1][object:branch][change:activity]"
        f"[project:{project_id}][branch:{branch_key(project_id, normalize_git_ref(ref))}]"
    )
    if not BRANCH_HEADER_RE.fullmatch(header):
        raise SyncError("rendered branch header is invalid")
    return _notice([f"branch: {name}", f"url: {_safe_url(url)}"], header)


def render_branch_activity(project_id: int, ref: str, record: dict[str, Any]) -> str:
    return _extend_notice(
        render_branch_root(project_id, ref, record.get("url") or ""),
        git_activity_event_lines(record),
    )


def branch_name_from_root(content: str) -> str:
    for line in notice_body_lines(content):
        if line.startswith("branch: "):
            return line[len("branch: "):]
    return ""


def mr_mention_targets(
    mr: dict[str, Any], members: list[dict[str, Any]], people: dict[str, str],
    agent_pubkeys: list[str] | tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    """Compatibility surface for the MR human policy, including the max-3 budget."""

    roles = {pubkey: "member" for pubkey in people.values()}
    mentions, unresolved = resolve_mr_mention_targets(
        mr, members, people, roles, agent_pubkeys=agent_pubkeys
    )
    # Older callers called every unmapped username simply ``unmapped``.  Keep
    # that display for missing aliases, while budget and safety rejections
    # retain their explicit reason.
    rendered = [
        item.split("(", 1)[0] if item.endswith("(profile_not_found)") else item
        for item in unresolved
    ]
    return mentions, rendered


def _mr_mention_candidates(mr: dict[str, Any], members: list[dict[str, Any]]) -> list[dict[str, str]]:
    author = (mr.get("author") or {}).get("username")
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    inputs = [
        *((item.get("username"), "gitlab.reviewers") for item in (mr.get("reviewers") or [])
          if isinstance(item, dict)),
        *((member.get("username"), "gitlab.maintainers") for member in members
          if isinstance(member, dict) and isinstance(member.get("access_level"), int)
          and member["access_level"] >= MAINTAINER_ACCESS and member.get("bot") is not True),
    ]
    for name, source in inputs:
        if (
            not isinstance(name, str)
            or not name
            or name == author
            or GITLAB_BOT_USERNAME_RE.match(name)
            or name in seen
        ):
            continue
        seen.add(name)
        candidates.append({"username": name, "source": source})
    return candidates


def resolve_mr_mention_targets(
    mr: dict[str, Any],
    members: list[dict[str, Any]],
    people: dict[str, str],
    channel_roles: dict[str, str],
    *,
    agent_pubkeys: list[str] | tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    return resolve_attention_targets(
        _mr_mention_candidates(mr, members), people, channel_roles, agent_pubkeys=agent_pubkeys
    )


_COMMENT_AT_RE = re.compile(r"(?<![A-Za-z0-9_.@/:-])@([A-Za-z0-9_.-]+)")
_COMMENT_HTML_RE = re.compile(r"<!--.*?-->", re.S)
_COMMENT_INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
_COMMENT_FENCE_RE = re.compile(r"\s*(?:`{3,}|~{3,})")
COMMENT_MENTION_SOURCE = "gitlab.comment_mention"
# The only opt-in the sync makes on the shared responsible-mentions gate (ADR-0018).
SYNC_OPT_IN_SOURCES = frozenset({COMMENT_MENTION_SOURCE})


def comment_mention_candidates(
    body: Any, project_usernames: Any, commenter: str
) -> list[dict[str, str]]:
    """Exact `@username` tokens of a comment body that name a project member (ADR-0018).

    Deterministic, never fuzzy: prose is only read for an `@` that starts a word,
    the token must equal a project member's username (case-sensitive), and code
    fences, inline code, quotes, HTML comments, e-mail addresses, URLs, `@group/path`
    and the reserved broadcast names never count. The commenter and GitLab bot users
    are dropped; first-seen order is kept. Mapping, Channel membership and the
    attention budget are the resolver's job.
    """

    if not isinstance(body, str) or not body:
        return []
    members = project_usernames if isinstance(project_usernames, (set, frozenset)) else set(project_usernames or ())
    text = _COMMENT_HTML_RE.sub("", body)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    fenced = False
    for line in text.split("\n"):
        if _COMMENT_FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">"):
            continue
        line = _COMMENT_INLINE_CODE_RE.sub(" ", line)
        for match in _COMMENT_AT_RE.finditer(line):
            token = match.group(1).rstrip(".-")
            if (
                line[match.end():match.end() + 1] == "/"
                or not token
                or responsible.USERNAME_RE.fullmatch(token) is None
                or token.casefold() in responsible.RESERVED_NAMES
                or token == commenter
                or token in seen
                or token not in members
                or GITLAB_BOT_USERNAME_RE.match(token)
            ):
                continue
            seen.add(token)
            found.append({"username": token, "source": COMMENT_MENTION_SOURCE})
    return found


def attention_candidates(source: str, names: Any, *, exclude: Any = ()) -> list[dict[str, str]]:
    """Structured-field candidates: usernames from one GitLab field, minus the excluded ones."""

    skip = {name for name in exclude if isinstance(name, str)}
    return [
        {"username": name, "source": source}
        for name in dict.fromkeys(names or ())
        if isinstance(name, str) and name and name not in skip
    ]


def resolve_attention_targets(
    candidates: list[dict[str, str]],
    people: dict[str, str],
    channel_roles: dict[str, str],
    *,
    agent_pubkeys: list[str] | tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    """(p-tag pubkeys, unresolved `name(reason)`): the one gate every sync @ goes through.

    A candidate is tagged only when it is mapped in `people`, is a current human
    Channel member, is not an agent, and fits the three-person budget; duplicates
    and GitLab bot users are dropped first, order is kept.
    """

    agent_set = set(agent_pubkeys)
    aliases = {name: pubkey for name, pubkey in people.items() if pubkey not in agent_set}
    seen: set[str] = set()
    usable: list[dict[str, str]] = []
    unresolved: list[str] = []
    for item in candidates:
        name = item["username"]
        if name in seen or GITLAB_BOT_USERNAME_RE.match(name) or people.get(name) in agent_set:
            continue
        seen.add(name)
        if responsible.USERNAME_RE.fullmatch(name) is None or name.casefold() in responsible.RESERVED_NAMES:
            unresolved.append(f"{name}(invalid_username)")
            continue
        usable.append(item)
    resolved = responsible.resolve_mentions(
        usable,
        aliases=aliases,
        profiles=[],
        members=[{"pubkey": pubkey, "role": role} for pubkey, role in channel_roles.items()],
        limit=3,
        extra_sources=SYNC_OPT_IN_SOURCES,
    )
    return (
        [item["pubkey"] for item in resolved["mentions"]],
        [*unresolved, *(f"{item['username']}({item['reason']})" for item in resolved["unresolved"])],
    )


def becomes_reviewable(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """Opened and not draft now, and first seen, draft before, or reopened from closed (not unlocked)."""

    if current["draft"] != "no" or current["state"] != "opened":
        return False
    return previous is None or previous.get("draft") == "yes" or previous.get("state") == "closed"


def _mr_state_actor(mr: dict[str, Any], state: str) -> str:
    """Who merged or closed the MR, when GitLab says so (merge_user / merged_by / closed_by)."""

    for key in (("merge_user", "merged_by") if state == "merged" else ("closed_by",)):
        user = mr.get(key)
        if isinstance(user, dict) and isinstance(user.get("username"), str):
            return user["username"]
    return ""


def mr_mentions(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    full_targets: list[str],
    people: dict[str, str],
) -> list[str]:
    if current["draft"] != "no" or current["state"] != "opened":
        return []
    if becomes_reviewable(previous, current):
        return sorted(full_targets)
    added = set(current["reviewers"]) - set((previous or {}).get("reviewers") or []) - {current["author"]}
    return sorted({people[name] for name in added if name in people and not GITLAB_BOT_USERNAME_RE.match(name)})


def pending_comments(
    notes: list[dict[str, Any]],
    thread_events: list[dict[str, Any]],
    publisher_pubkey: str,
    bot_user_id: int,
    since: str,
) -> list[dict[str, Any]]:
    posted: set[int] = set()
    for event in thread_events:
        if event.get("pubkey") != publisher_pubkey or parse_header(event.get("content")) is None:
            continue
        posted.update(_posted_note_ids(str(event.get("content") or "")))
    boundary = parse_timestamp(since, "since")
    pending = []
    for note in notes:
        note_id = note.get("id")
        if (
            not _positive_int(note_id)
            or note.get("system") is True
            or note.get("internal") is True
            or note.get("confidential") is True
            or (note.get("author") or {}).get("id") == bot_user_id
            or str(note.get("body") or "").startswith(f"<!-- {BINDING_PREFIX} ")  # any bot's binding note
            or note_id in posted
        ):
            continue
        if parse_timestamp(note.get("created_at"), "note created_at") < boundary:
            continue
        pending.append(note)
    return sorted(pending, key=lambda item: item["id"])


# ── top-level notifications ──────────────────────────────────────────────────


def _event_slug(action: Any) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", str(action or "unknown").lower()).strip("_")
    return (slug or "unknown")[:40]


def _safe_url(value: Any) -> str:
    """A server-provided URL, or empty when it could break the line-based message format."""

    return value if isinstance(value, str) and not any(ch.isspace() for ch in value) else ""


def _record(key: str, object_kind: str, event: str, placement: str, project_id: int, **fields: Any) -> dict[str, Any]:
    sha = fields.get("sha") or ""
    if sha and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{7,64}", sha)):
        sha = ""
    return {"key": key, "object": object_kind, "event": event, "placement": placement, "project": project_id,
            "created_at": fields.get("created_at"), "actor": fields.get("actor", "-"), "ref": fields.get("ref", ""),
            "title": fields.get("title", ""), "url": _safe_url(fields.get("url", "")), "mr_iid": fields.get("mr_iid"),
            "commits": fields.get("commits", 0), "source_id": fields.get("source_id"), "sha": sha}


def record_from_event(event: dict[str, Any], project_id: int, web_url: str) -> dict[str, Any] | None:
    event_id = event.get("id")
    if not _positive_int(event_id):
        raise SyncError("GitLab event id must be a positive integer")
    action = str(event.get("action_name") or "")
    target = event.get("target_type")
    common = {
        "created_at": event.get("created_at"),
        "actor": neutralize(_single_line((event.get("author") or {}).get("username") or "?")),
        "url": web_url,
    }
    key = f"event-{event_id}"
    push = event.get("push_data")
    if isinstance(push, dict):
        raw_ref = _single_line(push.get("ref") or "")
        ref, quoted = neutralize(raw_ref), urllib.parse.quote(raw_ref, safe="/")
        if push.get("ref_type") != "tag":
            return None  # policy 2026-09-18: branch pushes are not notified (NOTIFIED_OBJECTS)
        # Tags restored 13:16 the same day at the owner's request: tag moves are release signals.
        if action.startswith("deleted"):
            kind = "tag_deleted"
        elif action.startswith("pushed new"):
            kind = "tag_created"
        else:
            kind = "pushed"
        sha = push.get("commit_to") if isinstance(push.get("commit_to"), str) else ""
        return _record(key, "tag", kind, "instant", project_id,
                       **{**common, "url": f"{web_url}/-/tags/{quoted}"}, ref=ref,
                       commits=push.get("commit_count") or 0,
                       title=neutralize(sanitize_title(push.get("commit_title") or "")), sha=sha)
    title = neutralize(sanitize_title(event.get("target_title") or ""))
    if target in ("Issue", "WorkItem"):
        return _record(key, "issue", _event_slug(action), "thread", project_id, **common, title=title)
    if target == "MergeRequest":
        placement = "mr_thread" if action == "approved" else "thread"
        return _record(key, "mr", _event_slug(action), placement, project_id, **common, title=title,
                       mr_iid=event.get("target_iid"))
    if target in ("Note", "DiffNote", "DiscussionNote"):
        note = event.get("note") or {}
        noteable = note.get("noteable_type")
        if noteable in ("Issue", "WorkItem", "MergeRequest"):
            return _record(key, "note", "comment", "thread", project_id, **common)
        return None  # policy 2026-09-18: commit/snippet comments are not notified
    if target == "Milestone":
        milestone_iid = event.get("target_iid")
        url = (f"{web_url}/-/milestones/{milestone_iid}" if _positive_int(milestone_iid)
               else f"{web_url}/-/milestones")
        # GitLab 18 recordings carry target_iid; when an instance omits it the record
        # arrives without identity and _sync_milestone_records resolves it by title.
        return _record(key, "milestone", _event_slug(action), "milestone_thread", project_id,
                       **{**common, "url": url}, title=title,
                       source_id=int(milestone_iid) if _positive_int(milestone_iid) else None)
    return None  # policy 2026-09-18: wiki, membership and other activity are not notified


def record_from_pipeline(pipeline: dict[str, Any], project_id: int, default_branch: str) -> dict[str, Any] | None:
    status = pipeline.get("status")
    if status not in TERMINAL_PIPELINE_STATUSES:
        return None
    pipeline_id = pipeline.get("id")
    if not _positive_int(pipeline_id):
        raise SyncError("GitLab pipeline id must be a positive integer")
    ref = str(pipeline.get("ref") or "")
    mr_ref = MR_REF_RE.fullmatch(ref)
    if mr_ref:
        placement, mr_iid = "mr_thread", int(mr_ref.group(1))
    elif ref == default_branch and status == "failed":
        placement, mr_iid = "instant", None
    else:
        # policy 2026-09-18: happy-path and non-default-branch pipelines are not notified
        return None
    sha = pipeline.get("sha") if isinstance(pipeline.get("sha"), str) else ""
    return _record(f"pipeline-{pipeline_id}-{status}", "pipeline", status, placement, project_id,
                   created_at=pipeline.get("updated_at"), ref=neutralize(_single_line(ref)),
                   title=f"#{pipeline_id}", url=str(pipeline.get("web_url") or ""), mr_iid=mr_iid,
                   source_id=pipeline_id, sha=sha)


def record_from_deployment(deployment: dict[str, Any], project_id: int, web_url: str) -> dict[str, Any] | None:
    status = deployment.get("status")
    if status not in DEPLOYMENT_STATUSES:
        return None
    deployment_id = deployment.get("id")
    if not _positive_int(deployment_id):
        raise SyncError("GitLab deployment id must be a positive integer")
    environment = neutralize(_single_line((deployment.get("environment") or {}).get("name") or "-"))
    if status not in ("failed", "blocked"):
        # policy 2026-09-18: only deployment failures stay instant; happy paths are not notified
        return None
    return _record(f"deployment-{deployment_id}-{status}", "deployment", status, "instant", project_id,
                   created_at=deployment.get("updated_at"), ref=neutralize(_single_line(deployment.get("ref") or "")),
                   title=environment, url=f"{web_url}/-/environments",
                   sha=deployment.get("sha") if isinstance(deployment.get("sha"), str) else "")


def record_from_release(release: dict[str, Any], project_id: int, cursor: str) -> dict[str, Any] | None:
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise SyncError("GitLab release has no tag_name")
    if parse_timestamp(release.get("created_at"), "release created_at") < parse_timestamp(cursor, "cursor"):
        return None
    # Restored 2026-09-18 13:16 at the owner's request: a new Release is release-class signal.
    return _record(f"release-{project_id}-" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:12], "release", "created",
                   "instant", project_id, created_at=release.get("created_at"),
                   ref=neutralize(_single_line(tag)), title=neutralize(sanitize_title(release.get("name") or tag)),
                   url=str((release.get("_links") or {}).get("self") or ""))


def record_from_access_token(token: dict[str, Any], project_id: int, web_url: str, today: str) -> dict[str, Any] | None:
    if token.get("revoked") is True or token.get("active") is False:
        return None
    expires_at = token.get("expires_at")
    try:
        days_left = (dt.date.fromisoformat(str(expires_at)) - dt.date.fromisoformat(today)).days
    except ValueError:
        return None
    if not 0 <= days_left <= 7:
        return None
    token_id = token.get("id")
    if not _positive_int(token_id):
        raise SyncError("GitLab access token id must be a positive integer")
    name = neutralize(_single_line(token.get("name") or "-"))
    return _record(f"access_token-{token_id}-{today}", "access_token", "expiring", "instant", project_id,
                   title=f"{name} ({expires_at})", url=f"{web_url}/-/settings/access_tokens")


def render_record(record: dict[str, Any]) -> str:
    header = f"[gitlab-notify:v1][object:{record['object']}][event:{record['event']}][project:{record['project']}]"
    if not TOP_HEADER_RE.fullmatch(header):
        raise SyncError("rendered top-level header is invalid")
    header = _with_trailer(header, events=[record["key"]])
    # Styled body (issue #78): icon + phrase headline, bare URL; ref/by/commits stay visible.
    # events lives in the header trailer (script-only dedup key).
    icon, phrase = INSTANT_STYLE.get((record["object"], record["event"]), ("🔔", "通知"))
    body = [f"{icon} **{phrase}** · {_md_escape(str(record.get('title') or record['object']))}"]
    body.append(record["url"])
    if record.get("ref"):
        body.append(f"ref: {record['ref']}")
    if record.get("actor") not in (None, "", "-", "?"):
        body.append(f"by: {record['actor']}")
    if record.get("commits"):
        body.append(f"commits: {record['commits']}")
    return _notice(body, header)


def render_digest(records: list[dict[str, Any]], project_id: int) -> str:
    counts = Counter(record["object"] for record in records)
    summary = " · ".join(f"{count} {name}" for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    refs: list[str] = []
    for record in records:
        if record.get("ref") and record["ref"] not in refs:
            refs.append(record["ref"])
    header = f"[gitlab-notify:v1][object:activity][event:digest][project:{project_id}]"
    if not TOP_HEADER_RE.fullmatch(header):
        raise SyncError("rendered digest header is invalid")
    header = _with_trailer(header, events=[record["key"] for record in records])
    body = [f"summary: {summary}"]
    if refs:
        body.append("refs: " + ",".join(refs[:DIGEST_REF_LIMIT]) + ("…" if len(refs) > DIGEST_REF_LIMIT else ""))
    return _notice(body, header)


def _chunks_by_keys(records: list[dict[str, Any]], limit: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    budget = limit - DIGEST_OVERHEAD_RESERVE
    current: list[dict[str, Any]] = []
    used = 0
    for record in records:
        size = len(record["key"].encode("utf-8")) + 1 + record.get("_line_bytes", 0)
        if current and used + size > budget:
            chunks.append(current)
            current, used = [], 0
        current.append(record)
        used += size
    if current:
        chunks.append(current)
    return chunks


def _checked_size(message: str, limit: int) -> str:
    if len(message.encode("utf-8")) > limit:
        raise SyncError("rendered notice exceeds the Buzz message size limit")
    return message


def render_digests(records: list[dict[str, Any]], project_id: int, limit: int = DIGEST_BYTE_LIMIT) -> list[str]:
    """One or more digests, each under the CLI content limit and each carrying its own events: line."""

    return [_checked_size(render_digest(chunk, project_id), limit) for chunk in _chunks_by_keys(records, limit)]


def _summary_fact(record: dict[str, Any]) -> dict[str, Any]:
    """Return only semantic fields that may cross from deterministic sync to Desk AI."""

    fact = {key: record.get(key) for key in SUMMARY_FACT_KEYS}
    if (
        fact["object"] not in SUMMARY_OBJECTS
        or not isinstance(fact["event"], str)
        or not fact["event"]
        or (fact["created_at"] is not None and not isinstance(fact["created_at"], str))
        or not all(isinstance(fact[key], str) for key in ("actor", "ref", "title", "url"))
        or not isinstance(fact["commits"], int)
        or isinstance(fact["commits"], bool)
        or fact["commits"] < 0
    ):
        raise SyncError("activity record cannot become a summary fact")
    return fact


def build_summary_request_payloads(
    records: list[dict[str, Any]], project_id: int, visibility: str,
    limit: int = SUMMARY_REQUEST_BYTE_LIMIT,
) -> list[dict[str, Any]]:
    """Chunk normalized activity into durable private requests, never channel messages."""

    if not _positive_int(project_id) or visibility not in {"public", "internal", "private"}:
        raise SyncError("summary request scope is invalid")

    def payload(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "version": 1,
            "project_id": project_id,
            "visibility": visibility,
            "source_keys": [str(item["key"]) for item in items],
            "facts": [_summary_fact(item) for item in items],
        }

    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record.get("key"), str) or not EVENT_KEY_RE.fullmatch(record["key"]):
            raise SyncError("activity record has an invalid private source key")
        candidate_items = [*current, record]
        candidate = payload(candidate_items)
        size = len(json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        if current and size > limit:
            chunks.append(payload(current))
            current = [record]
            candidate = payload(current)
            size = len(json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        else:
            current = candidate_items
        if size > limit:
            raise SyncError("one activity fact exceeds the summary request size limit")
    if current:
        chunks.append(payload(current))
    return chunks


def delivery_change_id(channel_id: str, kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"channel_id": channel_id, "kind": kind, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def facts_digest(facts: Any) -> str:
    """The opaque receipt binding one AI prose turn to exactly the facts the runner showed."""
    if not isinstance(facts, list):
        raise SyncError("summary facts must be a list")
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_public_summary_request(request: Any) -> None:
    if (
        not isinstance(request, dict)
        or set(request) != {"request_id", "project_id", "facts", "facts_sha256"}
        or not isinstance(request.get("request_id"), str)
        or not HEX64_RE.fullmatch(request["request_id"])
        or not _positive_int(request.get("project_id"))
        or not isinstance(request.get("facts"), list)
        or not request["facts"]
        or len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > SUMMARY_REQUEST_BYTE_LIMIT
    ):
        raise SyncError("public summary request has an invalid schema")
    for fact in request["facts"]:
        if not isinstance(fact, dict) or set(fact) != set(SUMMARY_FACT_KEYS):
            raise SyncError("public summary fact has an invalid schema")
        _summary_fact(fact)
    receipt = request["facts_sha256"]
    if not isinstance(receipt, str) or not HEX64_RE.fullmatch(receipt) or receipt != facts_digest(request["facts"]):
        raise SyncError("public summary request facts receipt does not match its facts")


def public_summary_request(change_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not _positive_int(payload.get("project_id"))
        or not isinstance(payload.get("facts"), list)
        or not payload["facts"]
    ):
        raise SyncError("durable summary request has an invalid schema")
    request = {
        "request_id": change_id,
        "project_id": payload["project_id"],
        "facts": [dict(fact) if isinstance(fact, dict) else fact for fact in payload["facts"]],
        "facts_sha256": facts_digest(payload["facts"]),
    }
    validate_public_summary_request(request)
    return request


def posted_keys(events: list[dict[str, Any]], publisher_pubkey: str) -> set[str]:
    keys: set[str] = set()
    for event in events:
        if event.get("pubkey") != publisher_pubkey or parse_header(event.get("content")) is None:
            continue
        keys |= _posted_event_keys(str(event.get("content") or ""))
    return keys


def events_after_date(cursor: str) -> str:
    return (parse_timestamp(cursor, "cursor") - dt.timedelta(days=1)).date().isoformat()


def events_since(events: list[dict[str, Any]], cursor: str) -> list[dict[str, Any]]:
    boundary = parse_timestamp(cursor, "cursor")
    return [event for event in events if parse_timestamp(event.get("created_at"), "event created_at") >= boundary]


# ── binding and root recovery ────────────────────────────────────────────────


def _validate_binding(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise SyncError("binding must contain exactly project_id, object, iid, channel_id, root_event_id")
    if binding["object"] not in OBJECTS:
        raise SyncError(f"binding object must be one of {OBJECTS}")
    if not _positive_int(binding["project_id"]) or not _positive_int(binding["iid"]):
        raise SyncError("binding project_id and iid must be positive integers")
    if not isinstance(binding["channel_id"], str) or not UUID_RE.fullmatch(binding["channel_id"]):
        raise SyncError("binding channel_id must be a UUID")
    if not isinstance(binding["root_event_id"], str) or not HEX64_RE.fullmatch(binding["root_event_id"]):
        raise SyncError("binding root_event_id must be 64 lowercase hex")
    return binding


def render_binding_note(binding: dict[str, Any], link: str) -> str:
    payload = json.dumps(_validate_binding(binding), sort_keys=True, separators=(",", ":"))
    if not isinstance(link, str) or "\n" in link or "-->" in link:
        raise SyncError("binding link must be a single line")
    return f"<!-- {BINDING_PREFIX} {payload} -->\n🔗 Buzz Thread: {link}"


def parse_binding(
    notes: list[dict[str, Any]],
    bot_user_id: int,
    project_id: int,
    object_kind: str,
    iid: int,
    channel_id: str,
) -> str | None:
    roots: set[str] = set()
    for note in notes:
        author = note.get("author") or {}
        body = note.get("body")
        if author.get("id") != bot_user_id or not isinstance(body, str):
            continue
        first = body.split("\n", 1)[0]
        if not first.startswith(f"<!-- {BINDING_PREFIX} "):
            continue
        match = BINDING_LINE_RE.fullmatch(first)
        if not match:
            raise SyncError(f"note {note.get('id')}: malformed binding marker")
        try:
            binding = _validate_binding(json.loads(match.group(1)))
        except json.JSONDecodeError as exc:
            raise SyncError(f"note {note.get('id')}: binding marker is not JSON") from exc
        # Other channels and other objects (a cloned or moved Issue keeps its notes) are not this binding.
        if binding["channel_id"] != channel_id:
            continue
        if (binding["project_id"], binding["object"], binding["iid"]) != (project_id, object_kind, iid):
            continue
        roots.add(binding["root_event_id"])
    if len(roots) > 1:
        raise SyncError(f"{object_kind} {iid} has conflicting binding notes")
    return next(iter(roots), None)


def _checked_origin(origin: Any) -> dict[str, Any]:
    if not isinstance(origin, dict) or set(origin) != ORIGIN_KEYS:
        raise SyncError("origin must contain exactly channel_id, root_event_id")
    if not isinstance(origin["channel_id"], str) or not UUID_RE.fullmatch(origin["channel_id"]):
        raise SyncError("origin channel_id must be a UUID")
    if not isinstance(origin["root_event_id"], str) or not HEX64_RE.fullmatch(origin["root_event_id"]):
        raise SyncError("origin root_event_id must be 64 lowercase hex")
    return {"channel_id": origin["channel_id"], "root_event_id": origin["root_event_id"]}


def render_origin_marker(channel_id: str, root_event_id: str) -> str:
    payload = json.dumps(
        _checked_origin({"channel_id": channel_id, "root_event_id": root_event_id}),
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"<!-- {ORIGIN_PREFIX} {payload} -->"


def render_origin_block(channel_id: str, root_event_id: str) -> str:
    """Human-visible Buzz deep link plus the hidden origin marker."""

    return f"{buzz_message_link(channel_id, root_event_id)}\n{render_origin_marker(channel_id, root_event_id)}"


def _origins_from_buzz_links(text: str) -> list[dict[str, Any]]:
    """Well-formed bare `buzz://message?…` deep links in a text, as origin hints (ADR-0011).

    A bare link is what a person pastes from Buzz, and it is also what documentation about the syntax looks like
    (`buzz://message?…`, `…&id=<root>`). Only a link with a UUID channel and 64-hex ids counts; anything else is
    prose and is ignored, so explaining the syntax can never stall an object."""

    found: list[dict[str, Any]] = []
    for match in BUZZ_LINK_RE.finditer(text):
        query = urllib.parse.parse_qs(match.group(1), keep_blank_values=True)
        channels = query.get("channel", [])
        ids = query.get("id", [])
        threads = query.get("thread", [])
        if len(channels) != 1 or len(ids) != 1 or len(threads) > 1:
            continue
        root = threads[0] if threads else ids[0]
        if not (UUID_RE.fullmatch(channels[0]) and HEX64_RE.fullmatch(ids[0]) and HEX64_RE.fullmatch(root)):
            continue
        found.append(_checked_origin({"channel_id": channels[0], "root_event_id": root}))
    return found


def origin_note_bodies(notes: Any, bot_user_id: int) -> list[str]:
    """Human, non-secret comment bodies that may carry origin. Binding/bot/system notes skipped."""

    if not notes:
        return []
    if not isinstance(notes, list):
        raise SyncError("notes must be a list")
    bodies: list[str] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        if (
            note.get("system") is True
            or note.get("internal") is True
            or note.get("confidential") is True
            or (note.get("author") or {}).get("id") == bot_user_id
            or str(note.get("body") or "").startswith(f"<!-- {BINDING_PREFIX} ")
        ):
            continue
        body = note.get("body")
        if isinstance(body, str) and body:
            bodies.append(body)
    return bodies


def parse_origin_sources(text: Any) -> list[tuple[dict[str, Any], bool]]:
    """Every unique origin in one text as (origin, hint). The HTML marker is strict: malformed JSON or fields fail
    closed. A bare deep link is only a hint (ADR-0011): the caller ignores one that does not lead to a Desk plaque
    or fact. The same target named by both counts once, as the marker."""

    if text in (None, ""):
        return []
    if not isinstance(text, str):
        raise SyncError("description must be a string")
    candidates: list[tuple[dict[str, Any], bool]] = []
    for match in ORIGIN_LINE_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise SyncError("origin marker is not JSON") from exc
        candidates.append((_checked_origin(payload), False))
    candidates.extend((item, True) for item in _origins_from_buzz_links(text))
    unique: list[tuple[dict[str, Any], bool]] = []
    seen: set[tuple[str, str]] = set()
    for item, hint in candidates:
        key = (item["channel_id"], item["root_event_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append((item, hint))
    return unique


def parse_origins(text: Any) -> list[dict[str, Any]]:
    """Every unique origin in one text. Absent → []; a malformed marker fails closed."""

    return [item for item, _ in parse_origin_sources(text)]


def parse_origin(description: Any) -> dict[str, Any] | None:
    """First unique origin in one text, or None. Malformed still fail closed."""

    found = parse_origins(description)
    return found[0] if found else None


def origin_named_root_id(event: dict[str, Any], named_id: str) -> str:
    """If the named event is a reply, return its thread root; otherwise named_id."""

    if not isinstance(event, dict) or event.get("id") != named_id:
        return named_id
    tags = _tag_values(event, "e")
    if not tags:
        return named_id
    for tag in tags:
        if len(tag) >= 4 and tag[3] == "root" and isinstance(tag[1], str) and HEX64_RE.fullmatch(tag[1]):
            return tag[1]
    if len(tags[0]) > 1 and isinstance(tags[0][1], str) and HEX64_RE.fullmatch(tags[0][1]):
        return tags[0][1]
    raise SyncError("origin link does not name a thread root")


def origin_top_level_root(event: Any, channel_id: str) -> str | None:
    """The id of a top-level kind-9 message of this channel, whoever wrote it (ADR-0014); otherwise None.

    Structure only: exactly one `h` tag equal to the channel, no `e` tag (not a reply), a 64-hex id. The content is
    never read, so a person's text that looks like a plaque or a header is just text."""

    if not isinstance(event, dict) or event.get("kind") != 9:
        return None
    h_tags = _tag_values(event, "h")
    if len(h_tags) != 1 or h_tags[0][1:2] != [channel_id] or _tag_values(event, "e"):
        return None
    event_id = event.get("id")
    if not isinstance(event_id, str) or not HEX64_RE.fullmatch(event_id):
        return None
    return event_id


def origin_canonical_root(
    event: dict[str, Any], publisher_pubkey: str, channel_id: str,
) -> str | None:
    """Desk-published top-level plaque or fact in this channel; otherwise None. The rule for a bare deep link
    (ADR-0011); an HTML marker may name any top-level message instead (`origin_top_level_root`, ADR-0014)."""

    if event.get("pubkey") != publisher_pubkey:
        return None
    root = origin_top_level_root(event, channel_id)
    if root is None:
        return None
    content = event.get("content")
    if parse_header(content) is None and plaque_url(content) is None:
        return None
    return root


def _tag_values(event: dict[str, Any], name: str) -> list[list[Any]]:
    return [tag for tag in event.get("tags") or [] if isinstance(tag, list) and tag and tag[0] == name]


def reply_e_tags_match(event: dict[str, Any], reply_to: str) -> bool:
    """Accept the CLI's two NIP-10 shapes for a reply's e-tags.

    Replying to the thread root yields one ``reply``-marked e-tag; replying to
    another reply yields ``[root-marker, reply-marker(parent)]``.  Both must
    carry exactly one reply marker pointing at the intended parent, and at most
    one leading root marker.
    """

    tags = [tag[:4] for tag in _tag_values(event, "e")]
    if len(tags) == 1:
        return tags[0] == ["e", reply_to, "", "reply"]
    if len(tags) == 2:
        return (
            tags[0][3] == "root" and tags[1] == ["e", reply_to, "", "reply"]
        )
    return False


def select_root(
    events: list[dict[str, Any]],
    publisher_pubkey: str,
    channel_id: str,
    project_id: int,
    object_kind: str,
    iid: int,
    expected_plaque_url: str | None = None,
) -> str | None:
    candidates = []
    for event in events:
        if event.get("pubkey") != publisher_pubkey or event.get("kind") != 9:
            continue
        h_tags = _tag_values(event, "h")
        if len(h_tags) != 1 or h_tags[0][1:2] != [channel_id] or _tag_values(event, "e"):
            continue
        header = parse_header(event.get("content"))
        if (
            header
            and header["object"] == object_kind
            and header["project"] == project_id
            and header.get(object_kind) == iid
        ):
            candidates.append(event["id"])
        elif (
            expected_plaque_url is not None
            and (found := plaque_url(event.get("content"))) is not None
            and canonical_plaque_url(found) == canonical_plaque_url(expected_plaque_url)
        ):
            candidates.append(event["id"])  # plaque root (issue #78): identity is the bare URL line
    if len(candidates) > 1:
        raise SyncError(f"{object_kind} {iid} has {len(candidates)} candidate roots")
    return candidates[0] if candidates else None


# ── selection ────────────────────────────────────────────────────────────────


def excluded_by_rules(item: dict[str, Any], config: dict[str, Any]) -> bool:
    assignees = {entry.get("username") for entry in item.get("assignees") or [] if isinstance(entry, dict)}
    labels = set(item.get("labels") or [])
    return any(
        rule.get("assignee_username") in assignees or rule.get("label") in labels
        for rule in config.get("exclude") or []
    )


def issue_selected(issue: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str | None]:
    confidential = issue.get("confidential")
    if not isinstance(confidential, bool):
        raise SyncError(f"issue {issue.get('iid')}: confidential must be an explicit boolean")
    if confidential and not config.get("include_confidential", False):
        return False, "confidential"
    if excluded_by_rules(issue, config):
        return False, "excluded"
    return True, None


def is_backfill(item: dict[str, Any], since: str, has_binding: bool) -> bool:
    created = parse_timestamp(item.get("created_at"), "created_at")
    return not has_binding and created < parse_timestamp(since, "since")


# ── configuration and process boundary ───────────────────────────────────────


def _validate_origin(raw: Any, name: str, secure: set[str], plain: set[str]) -> str:
    if not isinstance(raw, str) or not raw:
        raise SyncError(f"{name} is required")
    try:
        parsed = urllib.parse.urlsplit(raw)
        parsed.port
    except ValueError as exc:
        raise SyncError(f"{name} is invalid") from exc
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SyncError(f"{name} must be an exact origin without credentials, path or query")
    if parsed.scheme in secure:
        return raw
    if parsed.scheme in plain and parsed.hostname in LOOPBACK_HOSTS:
        return raw
    raise SyncError(f"{name} must use {sorted(secure)} (plain schemes only on loopback)")


def validate_relay_url(raw: Any) -> str:
    return _validate_origin(raw, "BUZZ_RELAY_URL", {"wss", "https"}, {"ws", "http"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_buzz_cli_path(raw_path: Any, expected_sha256: Any) -> Path:
    """Return the pinned raw Buzz CLI, never the owner-key wrapper."""

    if not isinstance(raw_path, str) or not raw_path:
        raise SyncError("buzz.cli_path is required")
    if not isinstance(expected_sha256, str) or not HEX64_RE.fullmatch(expected_sha256):
        raise SyncError("buzz.cli_sha256 must be a lowercase SHA-256 digest")
    path = Path(raw_path)
    if not path.is_absolute() or BUZZ_CLI_RELEASE_DIR not in path.parts or path.name != "buzz":
        raise SyncError(f"buzz.cli_path must be the absolute {BUZZ_CLI_RELEASE_DIR} buzz binary")
    try:
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise SyncError("buzz.cli_path must not be a symlink or wrapper")
        metadata = path.stat()
        with path.open("rb") as handle:
            magic = handle.read(len(ELF_MAGIC))
    except OSError as exc:
        raise SyncError("buzz.cli_path is not a readable regular executable") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK) or magic != ELF_MAGIC:
        raise SyncError("buzz.cli_path must be an executable ELF binary")
    if metadata.st_uid not in {0, os.geteuid()} or metadata.st_mode & 0o022:
        raise SyncError("buzz.cli_path must be owner-controlled and not group/world-writable")
    if sha256_file(path) != expected_sha256:
        raise SyncError("buzz.cli_sha256 does not match the pinned Buzz binary")
    return path


def _reject_unknown_keys(section: dict[str, Any], allowed: frozenset[str], name: str) -> None:
    unknown = sorted(str(key) for key in set(section) - allowed)
    if unknown:
        raise SyncError(f"{name} has unknown keys: {', '.join(unknown)}")


def _check_people(people: Any, forbidden: set[str]) -> None:
    """The humans-only rules for a people map, wherever it came from. Messages never carry a pubkey value."""
    if not isinstance(people, dict):
        raise SyncError("people must map GitLab usernames to Buzz pubkeys")
    for username, pubkey in people.items():
        if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
            raise SyncError("people keys must be GitLab usernames")
        if not isinstance(pubkey, str) or not HEX64_RE.fullmatch(pubkey):
            raise SyncError(f"people[{username}] must be a 64-hex Buzz pubkey")
        if pubkey in forbidden:
            raise SyncError("people must list humans only; Desk and agent pubkeys are not allowed")


def load_people_file(path_value: Any) -> dict[str, str]:
    """The shared people map: an absolute, owner-only, non-symlink JSON object read fresh on every call."""
    if not isinstance(path_value, str) or not os.path.isabs(path_value):
        raise SyncError("people_file must be an absolute path")
    return _read_owner_only_json(Path(path_value), "people_file")


def effective_people(config: dict[str, Any]) -> dict[str, str]:
    """This channel's people: the shared people_file (if any) overlaid by the inline `people`.

    Every call re-reads the file and re-applies the humans-only rules against *this* channel's Desk and agent
    pubkeys, so a file swapped after validation is caught where it is used (fail closed, never cached).
    """
    inline = config.get("people") or {}
    if "people_file" not in config:
        return inline
    shared = load_people_file(config["people_file"])
    _check_people(inline, set())  # a non-object inline map is rejected before it is merged
    merged = {**shared, **inline}
    forbidden = {key for key in (config.get("agent_pubkeys") or []) if isinstance(key, str)}
    if isinstance(config.get("publisher_pubkey"), str):
        forbidden.add(config["publisher_pubkey"])
    _check_people(merged, forbidden)
    return merged


def validate_mute_events(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise SyncError("mute_events must be a list of '<object>:<event>' strings")
    for entry in value:
        match = MUTE_EVENT_RE.fullmatch(entry) if isinstance(entry, str) else None
        if not match or match.group(1) not in MUTABLE_EVENTS or (
                match.group(2) != "*" and match.group(2) not in MUTABLE_EVENTS[match.group(1)]):
            allowed = ", ".join(f"{name}:{{{'|'.join(sorted(events))}}}" for name, events in sorted(MUTABLE_EVENTS.items()))
            raise SyncError(f"mute_events entries must be '<object>:*' or one of {allowed}: {entry!r}")


def is_muted(record: dict[str, Any], config: dict[str, Any]) -> bool:
    muted = config.get("mute_events")
    if not isinstance(muted, list):
        return False
    return f"{record['object']}:{record['event']}" in muted or f"{record['object']}:*" in muted


def validate_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise SyncError("config must be a JSON object")
    _reject_unknown_keys(config, CONFIG_KEYS, "config")
    if not isinstance(config.get("channel_id"), str) or not UUID_RE.fullmatch(config["channel_id"]):
        raise SyncError("channel_id must be a lowercase UUID")
    if not isinstance(config.get("publisher_pubkey"), str) or not HEX64_RE.fullmatch(config["publisher_pubkey"]):
        raise SyncError("publisher_pubkey must be 64 lowercase hex")
    parse_timestamp(config.get("since"), "since")
    if not isinstance(config.get("include_confidential", False), bool):
        raise SyncError("include_confidential must be a boolean")
    if config.get("audience") is not None:
        # ADR-0006: channel membership itself is the audience consent. The
        # retired block must be deleted so nobody mistakes it for a live gate.
        raise SyncError(
            "audience is retired (ADR-0006): channel membership is the audience consent; remove the block"
        )
    validate_mute_events(config.get("mute_events"))
    exclude = config.get("exclude", [])
    if not isinstance(exclude, list):
        raise SyncError("exclude must be a list of rules")
    for rule in exclude:
        if (
            not isinstance(rule, dict)
            or len(rule) != 1
            or not set(rule) <= {"assignee_username", "label"}
            or not isinstance(next(iter(rule.values())), str)
            or not next(iter(rule.values()))
        ):
            raise SyncError("exclude rules must be {assignee_username: str} or {label: str}")
    diff = config.get("diff", {"enabled": False, "private": False})
    if not isinstance(diff, dict) or not all(isinstance(diff.get(key, False), bool) for key in ("enabled", "private")):
        raise SyncError("diff.enabled and diff.private must be booleans")
    _reject_unknown_keys(diff, DIFF_CONFIG_KEYS, "diff")
    agent_pubkeys = config.get("agent_pubkeys", [])
    if not isinstance(agent_pubkeys, list) or not all(
        isinstance(pubkey, str) and HEX64_RE.fullmatch(pubkey) for pubkey in agent_pubkeys
    ):
        raise SyncError("agent_pubkeys must be a list of 64-hex Buzz pubkeys")
    forbidden_mentions = set(agent_pubkeys) | {config["publisher_pubkey"]}
    _check_people(config.get("people", {}), forbidden_mentions)
    if "people_file" in config:
        effective_people(config)  # loads and checks the shared file too: a broken one refuses the whole config

    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise SyncError("gitlab section is required")
    _reject_unknown_keys(gitlab, GITLAB_CONFIG_KEYS, "gitlab")
    _validate_origin(gitlab.get("base_url"), "gitlab.base_url", {"https"}, {"http"})
    token_env = gitlab.get("token_env")
    if (
        not isinstance(token_env, str)
        or not TOKEN_ENV_RE.fullmatch(token_env)
        or token_env.startswith("BUZZ_")
        or token_env in CHILD_ENV_KEYS
    ):
        raise SyncError("gitlab.token_env must be an uppercase env name outside BUZZ_* and the CLI allowlist")
    if not _positive_int(gitlab.get("bot_user_id")):
        raise SyncError("gitlab.bot_user_id must be a positive integer")
    if not isinstance(gitlab.get("bot_username"), str) or not gitlab["bot_username"]:
        raise SyncError("gitlab.bot_username is required")
    projects = gitlab.get("projects")
    if not isinstance(projects, list) or not projects or not all(_positive_int(p) for p in projects):
        raise SyncError("gitlab.projects must be a non-empty list of project ids")
    if len(set(projects)) != len(projects):
        raise SyncError("gitlab.projects must not repeat a project id")

    buzz = config.get("buzz")
    if not isinstance(buzz, dict):
        raise SyncError("buzz section is required")
    _reject_unknown_keys(buzz, BUZZ_CONFIG_KEYS, "buzz")
    validate_buzz_cli_path(buzz.get("cli_path"), buzz.get("cli_sha256"))


def build_send_args(
    cli: str, channel_id: str, reply_to: str | None = None, mentions: tuple[str, ...] | list[str] = ()
) -> list[str]:
    if not UUID_RE.fullmatch(channel_id):
        raise SyncError("channel id must be a UUID")
    args = [cli, "messages", "send", "--channel", channel_id, "--content", "-"]
    if reply_to is not None:
        if not isinstance(reply_to, str) or not HEX64_RE.fullmatch(reply_to):
            raise SyncError("reply target must be a 64-hex event id")
        args += ["--reply-to", reply_to]
    for pubkey in mentions:
        if not isinstance(pubkey, str) or not HEX64_RE.fullmatch(pubkey):
            raise SyncError("mention target must be a 64-hex pubkey")
        args += ["--mention", pubkey]
    return args


def child_env(parent: dict[str, str], token_env: str) -> dict[str, str]:
    env = {key: parent[key] for key in CHILD_ENV_KEYS if key in parent}
    env.pop(token_env, None)
    for required in ("BUZZ_PRIVATE_KEY", "BUZZ_RELAY_URL"):
        if not env.get(required):
            raise SyncError(f"{required} is required in the Desk Agent runtime environment")
    return env


def _scope_digest(config: dict[str, Any]) -> str:
    scope = config["channel_id"] + "|" + ",".join(str(pid) for pid in sorted(config["gitlab"]["projects"]))
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def lock_paths(config: dict[str, Any], state_dir: Path) -> list[Path]:
    """One writer lock per channel/project pair, acquired in sorted order."""

    root = Path(state_dir)
    return [
        root / ("gitlab-buzz-sync-project-" + hashlib.sha256(
            f"{config['channel_id']}|{project_id}".encode("utf-8")
        ).hexdigest()[:16] + ".lock")
        for project_id in sorted(config["gitlab"]["projects"])
    ]


def lock_path(config: dict[str, Any], state_dir: Path) -> Path:
    """Compatibility helper for single-project callers; runs acquire every path from `lock_paths`."""

    return lock_paths(config, state_dir)[0]


def acquire_scope_locks(
    scopes: list[tuple[dict[str, Any], Path]], label: str,
) -> list[Any]:
    """Lock a fixed inventory in stable order so selection cannot race a sync writer."""

    handles: list[Any] = []
    try:
        paths: list[Path] = []
        for config, state_dir in scopes:
            prepare_private_dir(Path(state_dir), label)
            paths.extend(lock_paths(config, Path(state_dir)))
        if len(paths) != len(set(paths)):
            raise SyncError("fixed inventory contains duplicate lock scopes")
        for path in sorted(paths, key=str):
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            handle = os.fdopen(fd, "r+")
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError(f"{label} is locked") from None
        return handles
    except Exception:
        for handle in reversed(handles):
            handle.close()
        raise


# ── diff projection ──────────────────────────────────────────────────────────


def _diff_file(section: str) -> str:
    for line in section.split("\n"):
        if line.startswith("+++ b/"):
            return line[len("+++ b/"):]
    for line in section.split("\n"):
        if line.startswith("--- a/"):
            return line[len("--- a/"):]
    header = re.match(r"diff --git a/(\S+) b/(\S+)", section)
    if not header:
        raise SyncError("diff section has no file header")
    return header.group(2)


def split_diff_by_file(diff_text: str, limit: int = DIFF_LIMIT) -> tuple[list[dict[str, str]], list[str]]:
    chunks: list[dict[str, str]] = []
    skipped: list[str] = []
    for section in DIFF_SECTION_RE.split(diff_text):
        if not section.startswith("diff --git "):
            continue
        path = _diff_file(section)
        if len(section.encode("utf-8")) > limit:
            skipped.append(path)
        else:
            chunks.append({"file": path, "diff": section})
    return chunks, skipped


def partition_diff_files(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep files with a postable textual diff; name the rest so the run counts them as skipped."""

    usable: list[dict[str, Any]] = []
    skipped: list[str] = []
    for item in files:
        old, new = (str(item.get(key) or "") for key in ("old_path", "new_path"))
        if not new or any(ch in old + new for ch in "\r\n"):
            skipped.append("<invalid path>")
        elif item.get("too_large") is True or item.get("collapsed") is True or not str(item.get("diff") or "").strip():
            # GitLab empties pruned diffs; renames and binaries have no hunk either.
            skipped.append(new)
        else:
            usable.append(item)
    return usable, skipped


def unified_diff_from_files(files: list[dict[str, Any]]) -> str:
    sections = []
    for item in files:
        old = str(item.get("old_path") or item.get("new_path") or "")
        new = str(item.get("new_path") or old)
        if not new or any(ch in new + old for ch in "\r\n"):
            raise SyncError("merge request diff file path is invalid")
        body = str(item.get("diff") or "")
        if body and not body.endswith("\n"):
            body += "\n"
        preimage = "/dev/null" if item.get("new_file") is True else f"a/{old}"
        postimage = "/dev/null" if item.get("deleted_file") is True else f"b/{new}"
        sections.append(f"diff --git a/{old} b/{new}\n--- {preimage}\n+++ {postimage}\n{body}")
    return "".join(sections)


def build_diff_args(cli: str, channel_id: str, *, repo: str, commit: str, file_path: str, reply_to: str,
                    source_branch: str, target_branch: str, pr: int) -> list[str]:
    if not UUID_RE.fullmatch(channel_id):
        raise SyncError("channel id must be a UUID")
    parsed = urllib.parse.urlsplit(repo or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SyncError("diff repo must be an http(s) repository URL")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise SyncError("diff commit must be a hex sha")
    if not isinstance(reply_to, str) or not HEX64_RE.fullmatch(reply_to):
        raise SyncError("diff reply target must be a 64-hex event id")
    if not _positive_int(pr) or not file_path or any(ch in file_path for ch in "\r\n"):
        raise SyncError("diff pr and file path are invalid")
    return [cli, "messages", "send-diff", "--channel", channel_id, "--diff", "-", "--repo", repo,
            "--commit", commit, f"--file={file_path}", "--reply-to", reply_to,
            f"--source-branch={source_branch}", f"--target-branch={target_branch}", "--pr", str(pr)]


def diff_already_posted(events: list[dict[str, Any]], publisher_pubkey: str, commit: str, file_path: str) -> bool:
    return any(
        event.get("kind") == 40008
        and event.get("pubkey") == publisher_pubkey
        and ["commit", commit] in (event.get("tags") or [])
        and ["file", file_path] in (event.get("tags") or [])
        for event in events
    )


def diff_allowed(project_visibility: str, diff_config: dict[str, Any]) -> bool:
    if not diff_config.get("enabled", False):
        return False
    if project_visibility == "public":
        return True
    return bool(diff_config.get("private", False))


# ── webhook normalization ────────────────────────────────────────────────────
# Not wired into Syncer. A future GitLab project-webhook source must yield the
# same change records as polling (object, event, placement, project, ref,
# mr_iid), so it can replace "fetch events" without changing identity, dedupe,
# Thread or routing semantics. Keys are `webhook-…`, not polling keys.


WEBHOOK_ZERO_SHA = "0" * 40
WEBHOOK_ATTACHED_KINDS = frozenset({"build"})
ISSUE_WEBHOOK_ACTIONS = {"open": "opened", "close": "closed", "reopen": "reopened", "update": "updated"}
# `approval`/`unapproval` are one user's (un)approval while approval rules are unmet; polling sees one
# `approved` event per approving user either way.
MR_WEBHOOK_ACTIONS = {**ISSUE_WEBHOOK_ACTIONS, "merge": "accepted", "approved": "approved", "approval": "approved",
                      "unapproved": "unapproved", "unapproval": "unapproved"}
MILESTONE_WEBHOOK_ACTIONS = {"create": "created", "close": "closed", "reopen": "reopened", "delete": "destroyed"}
RELEASE_WEBHOOK_ACTIONS = {"create": "created", "update": "updated", "delete": "deleted"}


def _webhook_key(object_kind: str, *identity: Any) -> str:
    canonical = json.dumps([object_kind, *identity], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "webhook-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _webhook_id(value: Any, name: str) -> int:
    if not _positive_int(value):
        raise SyncError(f"GitLab webhook {name} must be a positive integer")
    return value


def _webhook_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SyncError(f"GitLab webhook has no {name}")
    return value


def _webhook_attrs(payload: dict[str, Any]) -> dict[str, Any]:
    attrs = payload.get("object_attributes")
    if not isinstance(attrs, dict):
        raise SyncError(f"GitLab {payload['object_kind']} webhook has no object_attributes")
    return attrs


def _webhook_event(actions: dict[str, str], action: Any) -> str:
    return actions.get(str(action or ""), _event_slug(action))


def _webhook_title(value: Any) -> str:
    return neutralize(sanitize_title(value or ""))


def _webhook_tag_push(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                      common: dict[str, Any]) -> dict[str, Any]:
    full_ref = _webhook_text(payload.get("ref"), "push ref")
    raw_ref = _single_line(full_ref.removeprefix("refs/tags/"))
    ref, quoted = neutralize(raw_ref), urllib.parse.quote(raw_ref, safe="/")
    before, after = payload.get("before"), payload.get("after")
    if after == WEBHOOK_ZERO_SHA:
        kind = "tag_deleted"
    elif before == WEBHOOK_ZERO_SHA:
        kind = "tag_created"
    else:
        kind = "pushed"
    commits = [item for item in payload.get("commits") or [] if isinstance(item, dict)]
    head = payload.get("checkout_sha") or after
    latest = next((item for item in commits if item.get("id") == head), commits[-1] if commits else {})
    title = latest.get("title") or str(latest.get("message") or "").split("\n", 1)[0]
    return _record(_webhook_key("tag_push", project_id, full_ref, before, after),
                   "tag", kind, "instant", project_id,
                   **{**common, "url": f"{common['url']}/-/tags/{quoted}"}, ref=ref,
                   commits=payload.get("total_commits_count") or 0,
                   title=_webhook_title(title),
                   sha=after if isinstance(after, str) and after != WEBHOOK_ZERO_SHA else "")


def _webhook_release(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                     common: dict[str, Any]) -> dict[str, Any] | None:
    tag = _webhook_text(payload.get("tag"), "release tag")
    event = _webhook_event(RELEASE_WEBHOOK_ACTIONS, payload.get("action"))
    if event == "updated":
        return None  # note edits are not release-class signal; polling cannot see them either
    # A new or removed release is instant like a tag; note edits stay silent.
    return _record(_webhook_key("release", project_id, payload.get("id"), tag, event), "release", event,
                   "instant", project_id, created_at=payload.get("created_at"),
                   ref=neutralize(_single_line(tag)), title=_webhook_title(payload.get("name") or tag),
                   url=str(payload.get("url") or ""))


def _webhook_note(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                  common: dict[str, Any]) -> dict[str, Any] | None:
    attrs = _webhook_attrs(payload)
    note_id = _webhook_id(attrs.get("id"), "note id")
    edited = attrs.get("action") == "update"
    suffix = "_edited" if edited else ""
    key = _webhook_key("note", project_id, note_id, "update" if edited else "create",
                       attrs.get("updated_at") if edited else None)
    created_at = attrs.get("updated_at") if edited else attrs.get("created_at")
    noteable = attrs.get("noteable_type")
    if noteable in ("Issue", "WorkItem", "MergeRequest"):
        return _record(key, "note", "comment" + suffix, "thread", project_id, **common, created_at=created_at)
    return None  # policy 2026-09-18: commit/snippet comments are not notified


def _webhook_issue(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                   common: dict[str, Any]) -> dict[str, Any]:
    attrs = _webhook_attrs(payload)
    iid = _webhook_id(attrs.get("iid"), "issue iid")
    event = _webhook_event(ISSUE_WEBHOOK_ACTIONS, attrs.get("action"))
    return _record(_webhook_key("issue", project_id, iid, event, attrs.get("updated_at")), "issue", event, "thread",
                   project_id, **common, created_at=attrs.get("updated_at"), title=_webhook_title(attrs.get("title")))


def _webhook_merge_request(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                           common: dict[str, Any]) -> dict[str, Any]:
    attrs = _webhook_attrs(payload)
    iid = _webhook_id(attrs.get("iid"), "merge request iid")
    event = _webhook_event(MR_WEBHOOK_ACTIONS, attrs.get("action"))
    # Approvals need not move updated_at; the acting user keeps two approvers' changes apart.
    key = _webhook_key("merge_request", project_id, iid, event, attrs.get("updated_at"), common["actor"])
    return _record(key, "mr", event, "mr_thread" if event == "approved" else "thread", project_id, **common,
                   created_at=attrs.get("updated_at"), title=_webhook_title(attrs.get("title")), mr_iid=iid)


def _webhook_pipeline(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                      common: dict[str, Any]) -> dict[str, Any] | None:
    attrs = _webhook_attrs(payload)
    pipeline_id = _webhook_id(attrs.get("id"), "pipeline id")
    status = attrs.get("status")
    if not isinstance(status, str) or status not in TERMINAL_PIPELINE_STATUSES:
        return None
    ref = str(attrs.get("ref") or "")
    merge_request = payload.get("merge_request")
    mr_iid = merge_request.get("iid") if isinstance(merge_request, dict) else None
    if not _positive_int(mr_iid):
        mr_ref = MR_REF_RE.fullmatch(ref)
        mr_iid = int(mr_ref.group(1)) if mr_ref else None
    if mr_iid is not None:
        # MR pipeline webhooks carry the source branch as ref; the Pipelines API shows the MR head ref.
        placement, ref = "mr_thread", f"refs/merge-requests/{mr_iid}/head"
    elif ref == str(project.get("default_branch") or "") and status == "failed":
        placement = "instant"
    else:
        # policy 2026-09-18: happy-path and non-default-branch pipelines are not notified
        return None
    return _record(_webhook_key("pipeline", project_id, pipeline_id, status), "pipeline", status, placement,
                   project_id, created_at=attrs.get("finished_at") or attrs.get("created_at"),
                   ref=neutralize(_single_line(ref)), title=f"#{pipeline_id}", url=str(attrs.get("url") or ""),
                   mr_iid=mr_iid, source_id=pipeline_id)


def _webhook_deployment(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                        common: dict[str, Any]) -> dict[str, Any] | None:
    deployment_id = _webhook_id(payload.get("deployment_id"), "deployment id")
    status = payload.get("status")
    if not isinstance(status, str) or status not in DEPLOYMENT_STATUSES:
        return None
    environment = payload.get("environment")
    if isinstance(environment, dict):
        environment = environment.get("name")
    if status not in ("failed", "blocked"):
        # policy 2026-09-18: only deployment failures stay instant; happy paths are not notified
        return None
    return _record(_webhook_key("deployment", project_id, deployment_id, status), "deployment", status, "instant",
                   project_id, created_at=payload.get("status_changed_at"),
                   ref=neutralize(_single_line(payload.get("ref") or "")),
                   title=neutralize(_single_line(environment or "-")), url=f"{common['url']}/-/environments",
                   sha=payload.get("sha") if isinstance(payload.get("sha"), str) else "")


def _webhook_milestone(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                       common: dict[str, Any]) -> dict[str, Any]:
    attrs = _webhook_attrs(payload)
    milestone_id = _webhook_id(attrs.get("id"), "milestone id")
    event = _webhook_event(MILESTONE_WEBHOOK_ACTIONS, payload.get("action") or attrs.get("action"))
    milestone_iid = attrs.get("iid")
    iid = int(milestone_iid) if _positive_int(milestone_iid) else milestone_id
    return _record(_webhook_key("milestone", project_id, milestone_id, event, attrs.get("updated_at")), "milestone",
                   event, "milestone_thread", project_id,
                   **{**common, "url": f"{common['url']}/-/milestones/{iid}"},
                   created_at=attrs.get("updated_at"), title=_webhook_title(attrs.get("title")), source_id=iid)


def _webhook_access_token(payload: dict[str, Any], project_id: int, project: dict[str, Any],
                          common: dict[str, Any]) -> dict[str, Any] | None:
    # GitLab 18 sends expiring_access_token at 60, 30 and 7 days (top-level `interval`);
    # only the 7-day notice matches the polling reminder window.
    if payload.get("interval") not in (None, "seven_days"):
        return None
    attrs = _webhook_attrs(payload)
    token_id = _webhook_id(attrs.get("id"), "access token id")
    expires = _single_line(attrs.get("expires_at") or "-")
    name = sanitize_title(attrs.get("name") or "")
    return _record(_webhook_key("access_token", project_id, token_id, "expiring", expires), "access_token",
                   "expiring", "instant", project_id,
                   **{**common, "url": f"{common['url']}/-/settings/access_tokens"},
                   title=_webhook_title(f"{name} expires {expires}"))


WEBHOOK_HANDLERS = {
    "tag_push": _webhook_tag_push, "release": _webhook_release,
    "note": _webhook_note, "issue": _webhook_issue,
    "work_item": _webhook_issue,
    "merge_request": _webhook_merge_request, "pipeline": _webhook_pipeline, "deployment": _webhook_deployment,
    "milestone": _webhook_milestone, "access_token": _webhook_access_token,
}


def normalize_webhook(payload: Any) -> dict[str, Any]:
    """Map one GitLab project-webhook payload to polling-equivalent change records.

    status `ok` may carry no records (non-terminal pipeline or deployment, or a kind the
    2026-09-18 notification policy does not carry, e.g. commit comments); `attached` is a
    job event that rides on its pipeline message; `unsupported` is a kind this sync does
    not handle at all (push, wiki, feature flag, emoji, vulnerability, unknown).
    """

    if not isinstance(payload, dict):
        raise SyncError("GitLab webhook payload must be a JSON object")
    object_kind = payload.get("object_kind")
    if not isinstance(object_kind, str) or not object_kind:
        raise SyncError("GitLab webhook payload has no object_kind")
    handler = WEBHOOK_HANDLERS.get(object_kind)
    if object_kind == "access_token" and payload.get("event_name") != "expiring_access_token":
        handler = None
    if handler is None and object_kind not in WEBHOOK_ATTACHED_KINDS:
        return {"status": "unsupported", "records": []}
    project = payload.get("project")
    project_id = project.get("id") if isinstance(project, dict) else None
    if not _positive_int(project_id):
        raise SyncError("GitLab webhook payload has no valid project.id")
    if handler is None:
        return {"status": "attached", "records": []}
    user = payload.get("user")
    username = (user.get("username") if isinstance(user, dict) else None) or payload.get("user_username") or "?"
    common = {"actor": neutralize(_single_line(username)), "url": str(project.get("web_url") or "")}
    record = handler(payload, project_id, project, common)
    return {"status": "ok", "records": [record] if record else []}


# ── runtime ──────────────────────────────────────────────────────────────────


CURSOR_OVERLAP = dt.timedelta(seconds=60)
DEDUPE_OVERLAP = dt.timedelta(minutes=10)
SEARCH_LIMIT = 100
CHANNEL_PAGE_LIMIT = 200  # `messages get` clamps --limit to 200
CHANNEL_PAGE_MAX = 500
THREAD_REPLY_LIMIT = 500  # `messages thread` clamps --limit to 500 and returns the newest replies
ROOT_SEARCH_SKEW = dt.timedelta(minutes=15)  # relay accepts created_at within ±15 min
READBACK_ATTEMPTS = 5
OUTBOX_ACK_LIMIT = 1000
GITLAB_REQUEST_TIMEOUT_SECONDS = 30
GITLAB_RUN_BUDGET_SECONDS = 600
GITLAB_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
GITLAB_PAGE_MAX = 100


def format_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def buzz_message_link(channel_id: str, root_event_id: str) -> str:
    return f"buzz://message?channel={channel_id}&id={root_event_id}&thread={root_event_id}"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def prepare_private_dir(path: Path, label: str) -> None:
    """Create or validate an owner-only, non-symlink state directory."""

    path = Path(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SyncError(f"cannot prepare {label}: {type(exc).__name__}") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or resolved != path.absolute()
    ):
        raise SyncError(f"{label} must be an owner-only real directory")


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the GitLab token never crosses an origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def validate_token(token: Any, token_env: str) -> str:
    """The token itself is never echoed; a stray CR from a CRLF env file would otherwise leak via header errors."""

    if not isinstance(token, str) or not token:
        raise SyncError(f"missing GitLab token environment variable {token_env}")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token):
        raise SyncError(f"GitLab token in {token_env} contains whitespace or control characters")
    return token


def publisher_pubkey_from_private_key(private_key: Any) -> str:
    """Derive the Nostr x-only pubkey without contacting the relay."""

    if not isinstance(private_key, str) or private_key != private_key.strip():
        raise SyncError("BUZZ_PRIVATE_KEY must be one exact nsec or lowercase hex key")
    try:
        if private_key.startswith("nsec1"):
            secret = nk.bech32_decode(private_key, "nsec")
        elif HEX64_RE.fullmatch(private_key):
            secret = bytes.fromhex(private_key)
        else:
            raise ValueError("unsupported key encoding")
        if len(secret) != 32:
            raise ValueError("wrong key length")
        return nk.pubkey_xonly(secret).hex()
    except (ValueError, TypeError, OverflowError):
        raise SyncError("BUZZ_PRIVATE_KEY is not a valid Nostr private key") from None


def validate_publisher_identity(env: dict[str, str], expected_pubkey: str) -> str:
    """Fail before building network adapters when config and signing key differ."""

    actual = publisher_pubkey_from_private_key(env.get("BUZZ_PRIVATE_KEY"))
    if actual != expected_pubkey:
        raise SyncError("BUZZ_PRIVATE_KEY does not match publisher_pubkey")
    return actual


class GitLabClient:
    def __init__(self, config: dict[str, Any], env: dict[str, str], *, opener: Any = None,
                 clock: Any = time.monotonic):
        gitlab = config["gitlab"]
        token = validate_token(env.get(gitlab["token_env"]), gitlab["token_env"])
        self.api = gitlab["base_url"].rstrip("/") + "/api/v4"
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirectHandler())
        self.bot_user_id = gitlab["bot_user_id"]
        self.bot_username = gitlab["bot_username"]
        self.server_date: str | None = None
        self.clock = clock
        self.deadline = clock() + GITLAB_RUN_BUDGET_SECONDS

    def check_budget(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise SyncError("GitLab run budget exceeded")
        return remaining

    def request(self, method: str, path: str, *, params: dict[str, str] | None = None,
                body: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        data = urllib.parse.urlencode(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, method=method, data=data,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            timeout = min(GITLAB_REQUEST_TIMEOUT_SECONDS, self.check_budget())
            with self.opener.open(request, timeout=timeout) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read(GITLAB_RESPONSE_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GitLabHTTPError(method, path, exc.code) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SyncError(f"GitLab {method} {path} failed: {getattr(exc, 'reason', exc)}") from None
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise SyncError(f"GitLab {method} {path} failed: {type(exc).__name__}") from None
        self.check_budget()
        if len(raw) > GITLAB_RESPONSE_MAX_BYTES:
            raise SyncError(f"GitLab {method} {path} response is too large")
        try:
            return (json.loads(raw) if raw else None), headers
        except json.JSONDecodeError:
            raise SyncError(f"GitLab {method} {path} returned non-JSON") from None

    def paged(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        page, result = 1, []
        while True:
            values, headers = self.request("GET", path, params={**params, "page": str(page), "per_page": "100"})
            if not isinstance(values, list):
                raise SyncError(f"GitLab {path} did not return a list")
            result.extend(values)
            next_page = headers.get("x-next-page", "")
            if not next_page:
                return result
            if not next_page.isdigit() or int(next_page) <= page or int(next_page) > GITLAB_PAGE_MAX:
                raise SyncError(f"GitLab {path} pagination did not advance")
            page = int(next_page)

    def current_user(self) -> dict[str, Any]:
        value, headers = self.request("GET", "user")
        self.server_date = headers.get("date")
        return value if isinstance(value, dict) else {}

    def scan_time(self) -> str:
        try:
            moment = email.utils.parsedate_to_datetime(self.server_date or "")
        except (TypeError, ValueError):
            raise SyncError("GitLab response has no valid Date header") from None
        if moment.tzinfo is None:
            raise SyncError("GitLab Date header has no timezone")
        return format_timestamp(moment)

    def project(self, project_id: int) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{project_id}")
        if not isinstance(value, dict) or value.get("id") != project_id:
            raise SyncError(f"GitLab project {project_id} readback mismatch")
        return value

    def issues(self, project_id: int, updated_after: str) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{project_id}/issues",
            {"state": "all", "order_by": "created_at", "sort": "asc", "updated_after": updated_after},
        )

    def merge_requests(self, project_id: int, updated_after: str) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{project_id}/merge_requests",
            {"state": "all", "order_by": "created_at", "sort": "asc", "updated_after": updated_after},
        )

    def members(self, project_id: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/members/all", {})

    def events(self, project_id: int, after_date: str) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/events", {"after": after_date, "sort": "asc"})

    def milestones(self, project_id: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/milestones", {"state": "all"})

    def releases(self, project_id: int) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{project_id}/releases",
            {"order_by": "created_at", "sort": "desc"},
        )

    def pipelines(self, project_id: int, updated_after: str) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/pipelines",
                          {"updated_after": updated_after, "order_by": "id", "sort": "asc"})

    def deployments(self, project_id: int, updated_after: str) -> list[dict[str, Any]]:
        # GitLab rejects updated_after unless the sort is updated_at (HTTP 400), so deployments keep that order.
        return self.paged(f"projects/{project_id}/deployments",
                          {"updated_after": updated_after, "order_by": "updated_at", "sort": "asc"})

    def optional_paged(self, path: str, params: dict[str, str]) -> list[dict[str, Any]] | None:
        """Read an endpoint the bot may lack permission for; None means unavailable, not empty."""

        try:
            return self.paged(path, params)
        except GitLabHTTPError as exc:
            if exc.status in (401, 403, 404):
                return None
            raise

    def access_tokens(self, project_id: int) -> list[dict[str, Any]] | None:
        return self.optional_paged(f"projects/{project_id}/access_tokens", {})

    def notes(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/issues/{iid}/notes", {"sort": "asc"})

    def mr_notes(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/merge_requests/{iid}/notes", {"sort": "asc"})

    def issue(self, project_id: int, iid: int) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{project_id}/issues/{iid}")
        if not isinstance(value, dict) or value.get("iid") != iid:
            raise SyncError(f"GitLab issue {iid} readback mismatch")
        return value

    def merge_request(self, project_id: int, iid: int) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{project_id}/merge_requests/{iid}")
        if not isinstance(value, dict) or value.get("iid") != iid:
            raise SyncError(f"GitLab merge request {iid} readback mismatch")
        return value

    def mr_closes_issues(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/merge_requests/{iid}/closes_issues", {})

    def related_merge_requests(self, project_id: int, issue_iid: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/issues/{issue_iid}/related_merge_requests", {})

    def merge_requests_by_source_branch(self, project_id: int, source_branch: str) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{project_id}/merge_requests",
            {"state": "all", "source_branch": source_branch, "order_by": "updated_at", "sort": "desc"},
        )

    def commit_merge_requests(self, project_id: int, sha: str) -> list[dict[str, Any]]:
        if not sha:
            return []
        try:
            return self.paged(f"projects/{project_id}/repository/commits/{urllib.parse.quote(sha, safe='')}/merge_requests", {})
        except GitLabHTTPError as exc:
            if exc.status == 404:
                return []
            raise

    def mr_related_issues(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        try:
            return self.paged(f"projects/{project_id}/merge_requests/{iid}/related_issues", {})
        except GitLabHTTPError as exc:
            if exc.status in (403, 404):
                return []
            raise

    def protected_branches(self, project_id: int) -> list[dict[str, Any]]:
        try:
            return self.paged(f"projects/{project_id}/protected_branches", {})
        except GitLabHTTPError as exc:
            if exc.status == 404:
                return []
            raise

    def mr_diffs(self, project_id: int, iid: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/merge_requests/{iid}/diffs", {})

    def pipeline(self, project_id: int, pipeline_id: int) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{project_id}/pipelines/{pipeline_id}")
        if not isinstance(value, dict) or value.get("id") != pipeline_id:
            raise SyncError(f"GitLab pipeline {pipeline_id} readback mismatch")
        return value

    def pipeline_jobs(self, project_id: int, pipeline_id: int) -> list[dict[str, Any]]:
        return self.paged(f"projects/{project_id}/pipelines/{pipeline_id}/jobs", {"scope[]": "failed"})

    def _add_note(self, noteable_path: str, body: str) -> int:
        value, _ = self.request("POST", f"{noteable_path}/notes", body={"body": body})
        note_id = value.get("id") if isinstance(value, dict) else None
        if not _positive_int(note_id):
            raise SyncError("GitLab note POST returned no note id")
        readback, _ = self.request("GET", f"{noteable_path}/notes/{note_id}")
        author = readback.get("author") if isinstance(readback, dict) else None
        if (
            not isinstance(author, dict)
            or author.get("id") != self.bot_user_id
            or author.get("username") != self.bot_username
            or readback.get("body") != body
        ):
            raise SyncError("GitLab note readback did not match author and body")
        return note_id

    def add_note(self, project_id: int, iid: int, body: str) -> int:
        return self._add_note(f"projects/{project_id}/issues/{iid}", body)

    def add_mr_note(self, project_id: int, iid: int, body: str) -> int:
        return self._add_note(f"projects/{project_id}/merge_requests/{iid}", body)


def _collect_events(value: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if all(key in node for key in ("id", "pubkey", "content", "tags")):
                found.setdefault(str(node["id"]), node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return list(found.values())


class BuzzCli:
    def __init__(self, config: dict[str, Any], env: dict[str, str], *,
                 runner: Any = subprocess.run, sleeper: Any = time.sleep):
        validate_relay_url(env.get("BUZZ_RELAY_URL"))
        self.cli = str(validate_buzz_cli_path(config["buzz"]["cli_path"], config["buzz"]["cli_sha256"]))
        self.env = child_env(env, config["gitlab"]["token_env"])
        self.channel = config["channel_id"]
        self.publisher = config["publisher_pubkey"]
        self.runner = runner
        self.sleeper = sleeper

    def command(self, args: list[str], content: str | None = None) -> Any:
        try:
            result = self.runner(
                [self.cli, *args], input=content, capture_output=True, text=True,
                timeout=45, check=False, env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncError(f"Buzz CLI {' '.join(args[:2])} could not run: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise BuzzCliError(" ".join(args[:2]), result.returncode)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise SyncError("Buzz CLI returned non-JSON output") from None

    def thread(self, event_id: str) -> list[dict[str, Any]]:
        args = ["messages", "thread", "--channel", self.channel, "--event", event_id, "--limit", "500"]
        for attempt in range(READBACK_ATTEMPTS):
            try:
                return _collect_events(self.command(args))
            except BuzzCliError:
                if attempt >= READBACK_ATTEMPTS - 1:
                    raise
                # A newly accepted root can briefly be absent from the relay's
                # thread index. This is a read-only retry; never repeat send.
                self.sleeper(0.5)
        raise AssertionError("unreachable")

    def search_roots(self, query: str, object_kind: str, iid: int, since_unix: int) -> list[dict[str, Any]]:
        # The query is the object URL (issue #78): plaque roots carry no header words, but every root form —
        # plaque or legacy fact — contains the canonical URL. Relay 0.2.1 full-text search finds nothing for a
        # full URL (engineering/skills#106) but finds its `/-/…` path tail, so the relay gets the tail and the
        # result is kept only where the content holds the exact URL.
        relay_query = url_path_tail(query)
        events = _collect_events(self.command([
            "messages", "search", "--author", self.publisher,
            "--query", relay_query, "--since", str(int(since_unix)),
            "--limit", str(SEARCH_LIMIT),
        ]))
        if len(events) >= SEARCH_LIMIT:
            raise ObjectError(f"{object_kind} {iid}: Buzz root search hit its result cap; refusing an ambiguous recovery")
        if any(event.get("pubkey") != self.publisher for event in events):
            raise SyncError("Buzz root search returned events outside the publisher author filter")
        if relay_query != query:
            events = [event for event in events if query in str(event.get("content") or "")]
        return events

    def send_diff(self, diff: str, *, repo: str, commit: str, file_path: str, reply_to: str,
                  source_branch: str, target_branch: str, pr: int) -> str:
        args = build_diff_args(self.cli, self.channel, repo=repo, commit=commit, file_path=file_path,
                               reply_to=reply_to, source_branch=source_branch, target_branch=target_branch, pr=pr)[1:]
        value = self.command(args, diff)
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if not isinstance(event_id, str) or not HEX64_RE.fullmatch(event_id) or value.get("accepted") is False:
            raise SyncError("Buzz rejected the diff or returned no event id")
        for attempt in range(READBACK_ATTEMPTS):
            event = next((e for e in self.thread(reply_to) if e.get("id") == event_id), None)
            if event is not None:
                break
            if attempt < READBACK_ATTEMPTS - 1:
                self.sleeper(0.5)
        else:
            raise SyncError("Buzz diff write could not be read back")
        tags = event.get("tags") or []
        if (
            event.get("kind") != 40008
            or event.get("pubkey") != self.publisher
            or [tag[:2] for tag in _tag_values(event, "h")] != [["h", self.channel]]
            or [tag[:4] for tag in _tag_values(event, "e")] != [["e", reply_to, "", "reply"]]
            or ["commit", commit] not in tags
            or ["file", file_path] not in tags
        ):
            raise SyncError("Buzz diff readback does not match author, channel, thread, commit or file")
        return event_id

    def channel_messages(self, since_unix: int) -> list[dict[str, Any]]:
        """Every message in this channel since `since_unix`, paged backwards by time without a result cap."""

        found: dict[str, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(CHANNEL_PAGE_MAX):
            args = ["messages", "get", "--channel", self.channel, "--kinds", "9", "--since", str(int(since_unix)),
                    "--limit", str(CHANNEL_PAGE_LIMIT)]
            if before is not None:
                args += ["--before", str(before)]
            page = _collect_events(self.command(args))
            fresh = [event for event in page if str(event["id"]) not in found]
            for event in fresh:
                found[str(event["id"])] = event
            if len(page) < CHANNEL_PAGE_LIMIT:
                break
            # --before is inclusive, so a full page that adds nothing means one second holds more than a page.
            if not fresh:
                raise SyncError("Buzz channel scan found more same-second messages than one page holds")
            times = [event.get("created_at") for event in page]
            if not all(_positive_int(value) for value in times):
                raise SyncError("Buzz channel messages have no created_at to page by")
            before = min(times)
        else:
            raise SyncError("Buzz channel scan exceeded its page limit")
        return [event for event in found.values()
                if [tag[:2] for tag in _tag_values(event, "h")] == [["h", self.channel]]]

    def channel_members(self) -> dict[str, str]:
        """Member pubkey → channel role (member, bot, admin, …)."""

        value = self.command(["channels", "members", "--channel", self.channel])
        if not isinstance(value, list) or not value:
            raise SyncError("Buzz channel members did not return a non-empty list")
        result: dict[str, str] = {}
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("pubkey"), str)
                or not HEX64_RE.fullmatch(item["pubkey"])
                or not isinstance(item.get("role"), str)
                or not item["role"]
                or item["pubkey"] in result
            ):
                raise SyncError("Buzz channel members returned an invalid or duplicate member")
            result[item["pubkey"]] = item["role"]
        return result

    def member_names(self, pubkeys: list[str] | tuple[str, ...]) -> dict[str, str]:
        """Pubkey → profile display name (`buzz users get --pubkey …`), for the visible mention line only."""

        keys = [key for key in dict.fromkeys(pubkeys) if HEX64_RE.fullmatch(key)]
        result: dict[str, str] = {}
        for start in range(0, len(keys), PROFILE_CHUNK):
            args = ["users", "get"]
            for key in keys[start:start + PROFILE_CHUNK]:
                args += ["--pubkey", key]
            rows = self.command(args)
            for row in rows if isinstance(rows, list) else []:
                if (
                    isinstance(row, dict)
                    and isinstance(row.get("pubkey"), str)
                    and HEX64_RE.fullmatch(row["pubkey"])
                    and isinstance(row.get("display_name"), str)
                ):
                    result[row["pubkey"]] = row["display_name"].strip()
        return result

    def send(self, content: str, reply_to: str | None = None, mentions: tuple[str, ...] | list[str] = ()) -> str:
        args = build_send_args(self.cli, self.channel, reply_to, mentions)[1:]
        try:
            value = self.command(args, content)
        except BuzzCliError as exc:
            if exc.returncode == 1:
                # Exit 1 is a local rejection (bad input, not found): nothing was sent.
                raise BuzzSendRejected(f"Buzz CLI rejected the message locally ({exc.returncode})") from None
            raise
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if value.get("accepted") is False:
            raise BuzzSendRejected("Buzz relay rejected the message")
        if not isinstance(event_id, str) or not HEX64_RE.fullmatch(event_id):
            # Accepted-or-not is unknowable here; only readback may prove it later.
            raise SyncError("Buzz send outcome is unknown: no event id")
        for attempt in range(READBACK_ATTEMPTS):
            event = next((e for e in self.thread(reply_to or event_id) if e.get("id") == event_id), None)
            if event is not None:
                break
            if attempt < READBACK_ATTEMPTS - 1:
                self.sleeper(0.5)
        else:
            raise SyncError("Buzz message write could not be read back")
        expected_e: list[list[Any]] = [] if reply_to is None else [["e", reply_to, "", "reply"]]
        p_tags = [str(tag[1]) for tag in _tag_values(event, "p") if len(tag) > 1]
        if (
            event.get("content") != content
            or event.get("pubkey") != self.publisher
            or [tag[:2] for tag in _tag_values(event, "h")] != [["h", self.channel]]
            or (reply_to is None and [tag[:4] for tag in _tag_values(event, "e")] != [])
            or (reply_to is not None and not reply_e_tags_match(event, reply_to))
            or len(p_tags) != len(set(p_tags))
            or set(p_tags) != set(mentions)
        ):
            raise SyncError("Buzz message readback does not match author, channel, thread or mentions")
        return event_id


class Syncer:
    def __init__(self, config: dict[str, Any], gitlab: Any, buzz: Any, *, state_dir: Path):
        self.config = config
        self.gitlab = gitlab
        self.buzz = buzz
        self.state_dir = Path(state_dir)
        self.channel = config["channel_id"]
        self.publisher = config["publisher_pubkey"]
        self.bot_user_id = config["gitlab"]["bot_user_id"]
        self.bot_username = config["gitlab"]["bot_username"]
        self.people: dict[str, str] = effective_people(config)
        self._members: dict[int, list[dict[str, Any]]] = {}
        self._pipeline_users: dict[tuple[int, int], str] = {}
        self._record_users: dict[str, str] = {}
        self._channel_member_roles: dict[str, str] | None = None
        self._mr_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
        self._projects: dict[int, dict[str, Any]] = {}
        self._project_visibilities: dict[int, str] = {}
        self._scans: list[tuple[int, list[dict[str, Any]]]] = []
        self._issues_scanned: dict[int, list[dict[str, Any]]] = {}
        # related_merge_requests is slow on GitLab; one lookup per Issue per run, shared by every MR.
        self._related_mr_iids: dict[tuple[int, int], set[int]] = {}
        self._degraded: list[str] = []
        self._display_names: dict[str, str] | None = None
        self._name_fallbacks = 0
        self._origin_fallbacks: list[dict[str, Any]] = []
        self._stalled_objects: set[tuple[int, str, int]] = set()
        self._stalled_records: dict[int, list[dict[str, Any]]] = {}
        self._retained_groups: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
        self._previous_groups: dict[tuple[int, str, int], list[dict[str, Any]]] = {}

    def _check_run_budget(self) -> None:
        check = getattr(self.gitlab, "check_budget", None)
        if callable(check):
            check()

    @staticmethod
    def _project_visibility(project_id: int, project: Any) -> str:
        if not isinstance(project, dict) or project.get("id") != project_id:
            raise SyncError(f"GitLab project {project_id} readback mismatch")
        visibility = project.get("visibility")
        if visibility not in {"public", "internal", "private"}:
            raise SyncError(f"GitLab project {project_id} has an invalid visibility")
        return str(visibility)

    def _check_delivery_gates(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "buzz_message":
            header = parse_header(payload.get("content"))
            project_id = header.get("project") if isinstance(header, dict) else None
            if project_id is None:
                # Plaque roots (issue #78) carry no header; the caller states the project.
                project_id = payload.get("project_id")
        else:
            project_id = payload.get("project_id")
        if project_id not in self._project_visibilities:
            raise SyncError("delivery does not identify one configured GitLab project")
        baseline = self._project_visibilities[project_id]
        current = self._project_visibility(project_id, self.gitlab.project(project_id))
        self._check_run_budget()
        if current != baseline:
            raise SyncError(f"GitLab project {project_id} visibility changed during the sync run")

    def cache_path(self) -> Path:
        return self.state_dir / f"gitlab-buzz-sync-{_scope_digest(self.config)}.cache.json"

    def outbox_path(self) -> Path:
        return self.state_dir / f"gitlab-buzz-sync-{_scope_digest(self.config)}.outbox.json"

    def _read_outbox(self) -> dict[str, Any]:
        try:
            value = json.loads(self.outbox_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "pending": [], "acked": []}
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"cannot read durable outbox: {type(exc).__name__}") from None
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or not isinstance(value.get("pending"), list)
            or not isinstance(value.get("acked"), list)
        ):
            raise SyncError("durable outbox has an invalid schema")
        return value

    def _write_outbox(self, value: dict[str, Any]) -> None:
        atomic_write_json(self.outbox_path(), value)

    def _queue_delivery(self, kind: str, payload: dict[str, Any]) -> str:
        change_id = delivery_change_id(self.channel, kind, payload)
        ledger = self._read_outbox()
        if not any(item.get("change_id") == change_id for item in ledger["pending"]):
            ledger["pending"].append({
                "status": "PENDING",
                "change_id": change_id,
                "kind": kind,
                "payload": payload,
                "queued_at": format_timestamp(dt.datetime.now(dt.timezone.utc)),
            })
            self._write_outbox(ledger)
        return change_id

    def _ack_delivery(self, change_id: str, *, recovered: bool = False, result: Any = None) -> None:
        ledger = self._read_outbox()
        pending = next((item for item in ledger["pending"] if item.get("change_id") == change_id), None)
        ledger["pending"] = [item for item in ledger["pending"] if item.get("change_id") != change_id]
        record: dict[str, Any] = {
            "status": "ACKED",
            "change_id": change_id,
            "kind": pending.get("kind") if isinstance(pending, dict) else "unknown",
            "acked_at": format_timestamp(dt.datetime.now(dt.timezone.utc)),
        }
        if recovered:
            record["recovered"] = True
        if isinstance(result, (str, int)) and not isinstance(result, bool):
            record["result_id"] = result
        if isinstance(pending, dict) and pending.get("kind") == "summary_request":
            payload = pending.get("payload")
            publication = pending.get("publication")
            if isinstance(payload, dict):
                record["project_id"] = payload.get("project_id")
                record["source_keys"] = list(payload.get("source_keys") or [])
            if isinstance(publication, dict) and isinstance(publication.get("content_sha256"), str):
                record["content_sha256"] = publication["content_sha256"]
            # Channel readback can never prove a prose summary, so published
            # source keys need a home that survives scope-digest changes and
            # the outbox ACK cap: an unbounded per-project journal.
            if isinstance(payload, dict) and _positive_int(payload.get("project_id")):
                self._journal_acked_summary_keys(
                    payload["project_id"], payload.get("source_keys") or [],
                )
        ledger["acked"] = [
            item for item in ledger["acked"] if item.get("change_id") != change_id
        ][-(OUTBOX_ACK_LIMIT - 1):] + [record]
        self._write_outbox(ledger)

    def _deliver(self, kind: str, payload: dict[str, Any], action: Any) -> Any:
        self._check_run_budget()
        if kind in {"buzz_message", "buzz_diff", "gitlab_note"}:
            self._check_delivery_gates(kind, payload)
        change_id = self._queue_delivery(kind, payload)
        result = action()
        self._check_run_budget()
        self._ack_delivery(change_id, result=result)
        return result

    def _pending_satisfied(self, item: dict[str, Any]) -> bool:
        kind, payload = item.get("kind"), item.get("payload")
        if not isinstance(payload, dict):
            raise SyncError("durable outbox pending payload is invalid")
        if kind == "gitlab_note":
            project_id, iid, object_kind = payload.get("project_id"), payload.get("iid"), payload.get("object")
            notes = (self.gitlab.mr_notes(project_id, iid) if object_kind == "mr" else
                     self.gitlab.notes(project_id, iid))
            return any(
                note.get("body") == payload.get("body")
                and (note.get("author") or {}).get("id") == self.bot_user_id
                for note in notes
            )
        if kind == "buzz_diff":
            events = self.buzz.thread(str(payload.get("reply_to") or ""))
            return diff_already_posted(
                events, self.publisher, str(payload.get("commit") or ""), str(payload.get("file_path") or "")
            )
        if kind != "buzz_message":
            raise SyncError("durable outbox pending operation kind is invalid")
        reply_to = payload.get("reply_to")
        if reply_to:
            events = self.buzz.thread(reply_to)
        else:
            header = parse_header(payload.get("content"))
            plaque = plaque_url(payload.get("content"))
            if header and header["object"] in OBJECTS:
                object_kind = header["object"]
                events = self.buzz.search_roots(
                    self._object_url(header["project"], object_kind, header[object_kind]),
                    object_kind, header[object_kind], 0,
                )
            elif plaque is not None:
                # Plaque roots (issue #78): search by the URL line; kind/iid are not
                # derivable here and only decorate error messages.
                events = self.buzz.search_roots(plaque, "issue", 0, 0)
            else:
                queued = parse_timestamp(item.get("queued_at"), "outbox queued_at")
                events = self.buzz.channel_messages(int((queued - DEDUPE_OVERLAP).timestamp()))
        expected_mentions = set(payload.get("mentions") or [])
        for event in events:
            if event.get("pubkey") != self.publisher or event.get("content") != payload.get("content"):
                continue
            if [tag[:2] for tag in _tag_values(event, "h")] != [["h", self.channel]]:
                continue
            if reply_to:
                if not reply_e_tags_match(event, reply_to):
                    continue
            elif [tag[:4] for tag in _tag_values(event, "e")] != []:
                continue
            mentions = {str(tag[1]) for tag in _tag_values(event, "p") if len(tag) > 1}
            if mentions == expected_mentions:
                return True
        return False

    def _retry_pending_binding(self, item: dict[str, Any]) -> None:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raise SyncError("durable outbox pending binding payload is invalid")
        project_id, iid = payload.get("project_id"), payload.get("iid")
        object_kind, body = payload.get("object"), payload.get("body")
        if (
            project_id not in self.config["gitlab"]["projects"]
            or not _positive_int(iid)
            or object_kind not in {"issue", "mr"}
            or not isinstance(body, str)
            or parse_binding(
                [{"author": {"id": self.bot_user_id}, "body": body}], self.bot_user_id,
                project_id, object_kind, iid, self.channel,
            ) is None
        ):
            raise SyncError("durable outbox pending binding is outside the configured scope")
        self._check_delivery_gates("gitlab_note", payload)
        if object_kind == "mr":
            self.gitlab.add_mr_note(project_id, iid, body)
        else:
            self.gitlab.add_note(project_id, iid, body)

    def _reconcile_pending(self) -> int:
        recovered = 0
        for item in list(self._read_outbox()["pending"]):
            change_id = item.get("change_id")
            if not isinstance(change_id, str) or not HEX64_RE.fullmatch(change_id):
                raise SyncError("durable outbox pending change_id is invalid")
            if item.get("kind") == "summary_request":
                # Desk and the restricted publisher own this semantic phase.
                # Sync must neither publish machine prose nor ACK it on their behalf.
                continue
            if not self._pending_satisfied(item):
                # A binding note has deterministic content and duplicate copies bind
                # the same root, so it is safe to retry after a negative readback.
                # Buzz messages and diffs cannot be retried without risking a second
                # externally visible event; those remain fail-closed.
                if item.get("kind") != "gitlab_note":
                    raise SyncError(f"durable outbox delivery {change_id[:12]} is still pending")
                self._retry_pending_binding(item)
                if not self._pending_satisfied(item):
                    raise SyncError(f"durable outbox delivery {change_id[:12]} retry was not readable")
            self._ack_delivery(change_id, recovered=True)
            # The public summary's recovered counter is object-binding recovery,
            # not a count of every ACK repaired in the delivery ledger.
            recovered += int(item.get("kind") == "gitlab_note")
        return recovered

    def _pending_summary_requests(self) -> list[dict[str, Any]]:
        requests = []
        for item in self._read_outbox()["pending"]:
            if item.get("kind") != "summary_request":
                continue
            if item.get("status") not in {"PENDING", "SUMMARIZING", "PUBLISHING"}:
                raise SyncError("durable summary request has an invalid state")
            requests.append(public_summary_request(item.get("change_id"), item.get("payload")))
        return requests

    def _summary_journal_path(self, project_id: int) -> Path:
        return self.state_dir / f"acked-summary-{project_id}.json"

    @staticmethod
    def _checked_source_keys(source_keys: Any) -> list[str]:
        if not isinstance(source_keys, list) or not all(
            isinstance(key, str) and EVENT_KEY_RE.fullmatch(key) for key in source_keys
        ):
            raise SyncError("acked summary request has invalid private source keys")
        return source_keys

    def _journal_acked_summary_keys(self, project_id: int, source_keys: Any) -> None:
        keys = self._checked_source_keys(source_keys)
        path = self._summary_journal_path(project_id)
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            journal = {"version": 1, "keys": []}
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"cannot read acked summary journal: {type(exc).__name__}") from None
        if (
            not isinstance(journal, dict)
            or journal.get("version") != 1
            or not isinstance(journal.get("keys"), list)
        ):
            raise SyncError("acked summary journal has an invalid schema")
        journal["keys"] = sorted(set(self._checked_source_keys(journal["keys"])) | set(keys))
        atomic_write_json(path, journal)

    def _acked_summary_keys(self) -> set[str]:
        projects = set(self.config["gitlab"]["projects"])
        keys: set[str] = set()
        # The per-project journal survives scope splits/merges (the outbox file
        # name changes) and the outbox ACK cap; the outbox sweep below also
        # migrates ACKed keys recorded before the journal existed.
        for project_id in sorted(projects):
            path = self._summary_journal_path(project_id)
            try:
                journal = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                raise SyncError(f"cannot read acked summary journal: {type(exc).__name__}") from None
            if (
                not isinstance(journal, dict)
                or journal.get("version") != 1
                or not isinstance(journal.get("keys"), list)
            ):
                raise SyncError("acked summary journal has an invalid schema")
            keys.update(self._checked_source_keys(journal["keys"]))
        for outbox_path in sorted(self.state_dir.glob("gitlab-buzz-sync-*.outbox.json")):
            try:
                ledger = json.loads(outbox_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SyncError(f"cannot read durable outbox {outbox_path.name}: {type(exc).__name__}") from None
            if not isinstance(ledger, dict) or not isinstance(ledger.get("acked"), list):
                raise SyncError("durable outbox has an invalid schema")
            for item in ledger["acked"]:
                if item.get("kind") != "summary_request" or item.get("project_id") not in projects:
                    continue
                keys.update(self._checked_source_keys(item.get("source_keys")))
        return keys

    def read_cache(self, scan_started: dt.datetime | None = None) -> dict[str, Any] | None:
        """The cache if it still belongs to this config and its cursor is sane; otherwise None."""

        try:
            cache = json.loads(self.cache_path().read_text(encoding="utf-8"))
            if (
                cache.get("channel_id") != self.channel
                or cache.get("since") != self.config["since"]
                or cache.get("projects") != sorted(self.config["gitlab"]["projects"])
            ):
                return None
            cursor = parse_timestamp(cache["cursor"])
            if cursor < parse_timestamp(self.config["since"]):
                return None
            if scan_started is not None and cursor > scan_started:
                return None
            stalled = cache.get("stalled", [])
            if not isinstance(stalled, list):
                return None
            cache["stalled"] = [
                (item["project"], item["object"], item["iid"]) for item in stalled
                if isinstance(item, dict) and item.get("project") in self.config["gitlab"]["projects"]
                and item.get("object") in OBJECTS and _positive_int(item.get("iid"))
            ]
            cache["stalled_groups"] = self._read_stalled_groups(cache.get("stalled_groups"))
            return cache
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SyncError):
            return None

    def _read_stalled_groups(self, value: Any) -> dict[tuple[int, str, int], list[dict[str, Any]]]:
        """The event-driven groups (MR group, milestone) that stalled before, with their records. A malformed
        entry is dropped, never trusted: the cursor and the other groups stay valid."""

        groups: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
        if not isinstance(value, list):
            return groups
        for item in value:
            if not (
                isinstance(item, dict) and item.get("project") in self.config["gitlab"]["projects"]
                and item.get("object") in ("mr", "milestone") and _positive_int(item.get("iid"))
                and isinstance(item.get("records"), list)
            ):
                continue
            records = [
                record for record in item["records"]
                if isinstance(record, dict) and isinstance(record.get("key"), str) and record["key"]
            ][-STALLED_GROUP_RECORD_LIMIT:]
            if records:
                groups[(item["project"], item["object"], item["iid"])] = records
        return groups

    def read_cursor(self, scan_started: dt.datetime | None = None) -> str | None:
        cache = self.read_cache(scan_started)
        return cache["cursor"] if cache else None

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        prepare_private_dir(self.state_dir, "sync state directory")
        handles = []
        try:
            for path in lock_paths(self.config, self.state_dir):
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.fchmod(fd, 0o600)
                handle = os.fdopen(fd, "r+")
                handles.append(handle)
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {"status": "locked"}
            return self._run_locked(dry_run)
        except OSError as exc:
            raise SyncError(f"cannot open the sync lock: {type(exc).__name__}") from None
        finally:
            for handle in reversed(handles):
                handle.close()

    def _run_locked(self, dry_run: bool) -> dict[str, Any]:
        # A Syncer may run more than once; nothing from an earlier run may leak into this one.
        self._degraded = []
        self._display_names, self._name_fallbacks = None, 0
        self._stalled_objects, self._stalled_records = set(), {}
        self._origin_fallbacks = []
        self._retained_groups, self._previous_groups = {}, {}
        summary: dict[str, Any] = {
            "status": "ok", "dry_run": dry_run, "created": 0, "updated": 0, "activity": 0,
            "mr_created": 0, "mr_updated": 0, "mr_xrefs": 0, "recovered": 0, "unchanged": 0,
            "skipped": {"confidential": 0, "excluded": 0, "backfill": 0, "milestone_identity": 0}, "links": [],
            "notified": {"instant": 0, "milestone": 0}, "gaps": list(POLLING_GAPS),
            "unbound": 0, "diffs": 0, "diff_skipped": 0, "unavailable": [], "stalled": [],
            "origin_fallbacks": self._origin_fallbacks,
            "summary_requests": [], "degraded": self._degraded,
        }
        self._members, self._channel_member_roles, self._mr_snapshots = {}, None, {}
        self._pipeline_users, self._record_users = {}, {}
        self._scans = []
        self._issues_scanned = {}
        self._related_mr_iids = {}
        self._protected_branch_cache = {}
        self._project_visibilities = {}
        self._check_run_budget()
        user = self.gitlab.current_user()
        if user.get("id") != self.bot_user_id or user.get("username") != self.bot_username:
            raise SyncError("GitLab token identity does not match the configured bot")
        scan_started = parse_timestamp(self.gitlab.scan_time(), "GitLab server time")
        self._scan_date = scan_started.astimezone(dt.timezone.utc).date().isoformat()
        cache = self.read_cache(scan_started)
        updated_after = cache["cursor"] if cache else self.config["since"]
        previously_stalled = cache["stalled"] if cache else []
        self._previous_groups = dict(cache["stalled_groups"]) if cache else {}
        order = lambda item: (str(item.get("created_at", "")), str(item.get("iid")))  # noqa: E731
        # Read every configured project before any write, so one unreadable project means zero writes.
        projects = {project_id: self.gitlab.project(project_id) for project_id in self.config["gitlab"]["projects"]}
        self._projects = projects
        self._project_visibilities = {
            project_id: self._project_visibility(project_id, project) for project_id, project in projects.items()
        }
        if not dry_run:
            # Finish or fail every crash-surviving delivery before publishing facts
            # discovered by this run. Otherwise a stale PENDING could be hidden
            # behind newer externally visible writes.
            self._check_run_budget()
            summary["recovered"] += self._reconcile_pending()
            self._check_run_budget()
            summary["summary_requests"] = self._pending_summary_requests()
            if summary["summary_requests"]:
                # Preserve source order and prevent an unbounded semantic backlog.
                # The next tick will resume here until the restricted publisher ACKs.
                return summary
        for project_id, project in projects.items():
            self._check_run_budget()
            issues = self._with_stalled(project_id, "issue", self.gitlab.issues(project_id, updated_after),
                                        previously_stalled)
            self._issues_scanned[project_id] = issues
            for issue in sorted(issues, key=order):
                self._isolated(summary, project_id, "issue", issue, self._sync_issue, project_id, issue, summary,
                               dry_run)
            mrs = self._with_stalled(project_id, "mr", self.gitlab.merge_requests(project_id, updated_after),
                                     previously_stalled)
            for mr in sorted(mrs, key=order):
                self._isolated(summary, project_id, "mr", mr, self._sync_mr, project_id, project, mr, summary,
                               dry_run)
            self._sync_top_level(project_id, project, updated_after, summary, dry_run)
        if not dry_run:
            self._check_run_budget()
            atomic_write_json(self.cache_path(), {
                "channel_id": self.channel, "since": self.config["since"],
                "projects": sorted(self.config["gitlab"]["projects"]),
                "cursor": format_timestamp(scan_started - CURSOR_OVERLAP),
                "stalled": [{key: item[key] for key in ("project", "object", "iid")} for item in summary["stalled"]],
                "stalled_groups": [
                    {"project": project, "object": kind, "iid": iid, "records": records}
                    for (project, kind, iid), records in sorted(self._retained_groups.items())
                ],
            })
        if self._name_fallbacks:
            summary["name_fallbacks"] = self._name_fallbacks  # profile reads failed; mention lines used plain names
        if self._degraded:
            # The round completed (cursor advanced, digests delivered); related lookups degraded
            # some records to digest. Timer and desk runner already accept this status as success.
            summary["status"] = "degraded"
        return summary

    def _with_stalled(self, project_id: int, object_kind: str, listed: list[dict[str, Any]],
                      previously_stalled: list[tuple[int, str, int]]) -> list[dict[str, Any]]:
        """Add objects that stalled before but were not updated since, so a fix on either side is picked up."""

        items = list(listed)
        seen = {item.get("iid") for item in items}
        for stalled_project, stalled_kind, iid in previously_stalled:
            if (stalled_project, stalled_kind) != (project_id, object_kind) or iid in seen:
                continue
            read = self.gitlab.issue if object_kind == "issue" else self.gitlab.merge_request
            try:
                items.append(read(project_id, iid))
            except GitLabHTTPError as exc:
                if exc.status != 404:
                    raise
            seen.add(iid)
        return items

    def _merge_retained_groups(self, project_id: int, object_kind: str,
                               by_iid: dict[int, list[dict[str, Any]]]) -> None:
        """Put the records of groups that stalled in an earlier round back in front of this round's records."""

        for (project, kind, iid), saved in self._previous_groups.items():
            if (project, kind) != (project_id, object_kind):
                continue
            keys = {record["key"] for record in saved}
            by_iid[iid] = [*saved, *(record for record in by_iid.get(iid, []) if record["key"] not in keys)]

    def _run_group(self, summary: dict[str, Any], project_id: int, object_kind: str, iid: int,
                   group: list[dict[str, Any]], function: Any, dry_run: bool) -> None:
        """Run one event-driven group (MR group, milestone). Its records come from GitLab events at or after the
        cursor and cannot be re-derived once the cursor moves past them, so a group that hits an object-level data
        error is stalled like an Issue (ADR-0009) and keeps its records in the cache until it succeeds."""

        self._check_run_budget()
        try:
            function(project_id, iid, group, summary, dry_run)
        except ObjectError as exc:
            kept = group[-STALLED_GROUP_RECORD_LIMIT:]
            self._retained_groups[(project_id, object_kind, iid)] = [
                json.loads(json.dumps(record, default=str)) for record in kept
            ]
            self._stall(summary, project_id, object_kind, {"iid": iid}, str(exc))

    def _isolated(self, summary: dict[str, Any], project_id: int, object_kind: str, item: dict[str, Any],
                  function: Any, *args: Any) -> None:
        """Run one Issue or MR; bad data on that object stalls only it (ADR-0009).

        Only ObjectError is contained. Transport, authentication, identity, configuration, budget and
        uncertain-delivery errors are other SyncError subclasses and still stop the whole run."""
        self._check_run_budget()
        try:
            function(*args)
        except ObjectError as exc:
            self._stall(summary, project_id, object_kind, item, str(exc))

    def _stall(self, summary: dict[str, Any], project_id: int, object_kind: str, item: dict[str, Any],
               reason: str) -> None:
        """Record one stalled object and queue one notice per object and reason per UTC day.

        The object had no write in this round that was not already ACKed by its own delivery. The cursor still
        advances; the object is re-read and retried every round (`_with_stalled`) until its data is fixed."""

        iid = item.get("iid") if _positive_int(item.get("iid")) else 0
        if (project_id, object_kind, iid) in self._stalled_objects:
            return
        self._stalled_objects.add((project_id, object_kind, iid))
        reason = STALLED_SECRET_RE.sub("***", neutralize(_single_line(reason)))[:STALLED_REASON_LIMIT]
        summary["stalled"].append({"project": project_id, "object": object_kind, "iid": iid, "reason": reason})
        self._signal_degrade(f"{project_id}:{object_kind}:{iid}:stalled")
        url = item.get("web_url")
        if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
            url = str(self._projects.get(project_id, {}).get("web_url") or "")
        digest = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:8]
        self._stalled_records.setdefault(project_id, []).append(_record(
            f"sync_stalled-{project_id}-{object_kind}-{iid}-{digest}-{self._scan_date}", "sync", "stalled", "instant",
            project_id, ref=f"{object_kind} {iid}", title=reason, url=url))

    def _link(self, summary: dict[str, Any], root: str) -> None:
        link = buzz_message_link(self.channel, root)
        if link not in summary["links"]:
            summary["links"].append(link)

    def _member_display_names(self) -> dict[str, str]:
        """Display names of the current channel members, read once per round; {} when the read fails or the
        client cannot read profiles (the line then falls back to plain usernames, nothing else changes)."""

        if self._display_names is None:
            names: dict[str, str] = {}
            reader = getattr(self.buzz, "member_names", None)
            if callable(reader):
                try:
                    names = dict(reader(list(self.buzz.channel_members())))
                except (SyncError, BuzzCliError):
                    self._name_fallbacks += 1
            self._display_names = names
        return self._display_names

    def _send_message(self, content: str, reply_to: str | None = None,
                      mentions: tuple[str, ...] | list[str] = (),
                      project_id: int | None = None) -> str:
        if mentions:
            content = with_notified_line(
                content, notified_tokens(list(mentions), self.people, self._member_display_names()),
            )
        payload = {"content": content, "reply_to": reply_to, "mentions": list(mentions),
                   "project_id": project_id}
        return self._deliver(
            "buzz_message", payload,
            lambda: self.buzz.send(content, reply_to=reply_to, mentions=mentions),
        )

    def _send_diff(self, diff: str, *, repo: str, commit: str, file_path: str, reply_to: str,
                   source_branch: str, target_branch: str, pr: int, project_id: int) -> str:
        payload = {
            "diff": diff,
            "repo": repo,
            "commit": commit,
            "file_path": file_path,
            "reply_to": reply_to,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "pr": pr,
            "project_id": project_id,
        }
        return self._deliver(
            "buzz_diff", payload,
            lambda: self.buzz.send_diff(
                diff, repo=repo, commit=commit, file_path=file_path, reply_to=reply_to,
                source_branch=source_branch, target_branch=target_branch, pr=pr,
            ),
        )

    def _write_binding(self, project_id: int, object_kind: str, iid: int, root: str) -> None:
        binding = {"project_id": project_id, "object": object_kind, "iid": iid,
                   "channel_id": self.channel, "root_event_id": root}
        body = render_binding_note(binding, buzz_message_link(self.channel, root))
        payload = {"project_id": project_id, "object": object_kind, "iid": iid, "body": body}
        if object_kind == "mr":
            self._deliver("gitlab_note", payload, lambda: self.gitlab.add_mr_note(project_id, iid, body))
        else:
            self._deliver("gitlab_note", payload, lambda: self.gitlab.add_note(project_id, iid, body))

    def _object_url(self, project_id: int, object_kind: str, iid: int) -> str:
        if object_kind not in ("issue", "mr"):
            raise SyncError(f"object kind {object_kind} has no canonical URL")
        web_url = str((self._projects.get(project_id) or {}).get("web_url") or "").rstrip("/")
        if not web_url:
            raise SyncError(f"project {project_id} has no web_url for root recovery")
        tail = "issues" if object_kind == "issue" else "merge_requests"
        return f"{web_url}/-/{tail}/{iid}"

    def _project_path(self, project_id: int) -> str:
        return str((self._projects.get(project_id) or {}).get("path_with_namespace") or "")

    def _branch_plaque_url(self, project_id: int, ref: str) -> str:
        web_url = str((self._projects.get(project_id) or {}).get("web_url") or "").rstrip("/")
        if not web_url:
            raise SyncError(f"project {project_id} has no web_url for the branch plaque")
        return branch_plaque_url(web_url, ref)

    def _bound_root(self, project_id: int, object_kind: str, iid: int, item: dict[str, Any],
                    notes: list[dict[str, Any]], summary: dict[str, Any], dry_run: bool) -> tuple[str | None, bool]:
        """Return (root, is_new). A None root with is_new=True means a new root must be created."""

        root = _object_data(parse_binding, notes, self.bot_user_id, project_id, object_kind, iid, self.channel)
        if root is not None:
            return root, False
        if _object_data(is_backfill, item, self.config["since"], has_binding=False):
            summary["skipped"]["backfill"] += 1
            return None, False
        created = _object_data(parse_timestamp, item.get("created_at"), "created_at")
        url = self._object_url(project_id, object_kind, iid)
        since_unix = int((created - ROOT_SEARCH_SKEW).timestamp())
        candidates = self.buzz.search_roots(url, object_kind, iid, since_unix)
        if object_kind == "issue" and not candidates:
            # A plaque an older run published from a work_items web_url carries that form on its URL line.
            candidates = self.buzz.search_roots(
                url.replace("/-/issues/", "/-/work_items/"), object_kind, iid, since_unix)
        root = _object_data(select_root, candidates, self.publisher, self.channel, project_id, object_kind, iid, url)
        if root is None:
            return None, True
        if not dry_run:
            self._write_binding(project_id, object_kind, iid, root)
        summary["recovered"] += 1
        self._link(summary, root)
        return root, False

    def _thread(self, root: str, project_id: int, object_kind: str, iid: int,
                expected_plaque: str | None = None) -> list[dict[str, Any]]:
        try:
            events = self.buzz.thread(root)
        except BuzzCliError as exc:
            if exc.returncode != 1:  # exit 1 is bad input or not found: a deleted or foreign root
                raise
            raise ObjectError(f"{object_kind} {iid}: bound root cannot be read ({exc})") from None
        root_event = next((event for event in events if event.get("id") == root), None)
        header = parse_header(root_event.get("content")) if root_event else None
        root_plaque = plaque_url(root_event.get("content")) if root_event else None
        # Plaque roots (issue #78) carry no header; their identity is the bare object URL
        # on the last line, checked structurally (kind, iid). Binding notes are written by
        # this bot for exactly (project, object, iid, channel), so project identity is
        # trusted from the note exactly as it is for header roots of a renamed project.
        ident = plaque_identity(root_plaque) if header is None and root_plaque is not None else None
        # A merged-in MR binds to its associated Issue's root (2026-09-17): the
        # root is the Issue's, while the MR facts live as replies below.
        merged_into_issue = object_kind == "mr" and (
            (header is not None and header["object"] == "issue")
            or (ident is not None and ident["object"] == "issue")
        )
        # An MR-group member binds to the earliest sibling MR's root (2026-09-18):
        # same acceptance as merge-in, but the foreign root is an MR root with another iid.
        grouped_mr = object_kind == "mr" and (
            (header is not None and header["object"] == "mr"
             and header.get("project") == project_id and header.get("mr") != iid)
            or (ident is not None and ident["object"] == "mr" and ident["iid"] != iid)
        )
        own_header = (
            header is not None
            and header["object"] == object_kind
            and header.get("project") == project_id
            and header.get(object_kind) == iid
        )
        branch_root = object_kind == "branch" and (
            (header is not None and header["object"] == "branch")
            or (ident is not None and ident["object"] == "branch"
                and expected_plaque is not None and root_plaque == expected_plaque)
        )
        own_plaque = (
            ident is not None and ident["object"] == object_kind and ident.get("iid") == iid
        )
        origin_bound = object_kind in ("issue", "mr", "milestone") and (
            header is not None or ident is not None
        ) and not merged_into_issue and not grouped_mr and not own_header and not own_plaque
        foreign_root = merged_into_issue or grouped_mr or origin_bound
        plaque_ok = root_plaque is not None and (own_plaque or foreign_root or branch_root)
        # An origin may bind an object to a top-level message somebody wrote (ADR-0014). Such a root is judged by
        # structure only: content that looks like a plaque or header is ignored, and a Desk plaque or fact keeps
        # the per-object rules below.
        open_root = (
            object_kind in ("issue", "mr", "milestone")
            and root_event is not None
            and root_event.get("pubkey") != self.publisher
            and origin_top_level_root(root_event, self.channel) is not None
        )
        if not open_root and (
            root_event is None
            or root_event.get("pubkey") != self.publisher
            or (not header and not plaque_ok)
            or (header is not None and (
                (header["object"] != object_kind and not foreign_root)
                or (
                    not foreign_root
                    and not branch_root
                    and (header["project"], header.get(object_kind)) != (project_id, iid)
                )
                or (branch_root and header["project"] != project_id)
            ))
        ):
            raise ObjectError(f"{object_kind} {iid}: bound root is not a readable Desk root for this object")
        if sum(1 for event in events if event.get("id") != root) >= THREAD_REPLY_LIMIT:
            raise ObjectError(f"{object_kind} {iid}: thread reached {THREAD_REPLY_LIMIT} replies; older history is unreadable")
        return events

    def _sync_issue(self, project_id: int, issue: dict[str, Any], summary: dict[str, Any], dry_run: bool) -> None:
        selected, reason = _object_data(issue_selected, issue, self.config)
        if not selected:
            summary["skipped"][reason] += 1
            return
        listed_fact = _object_data(issue_fact, issue, project_id)
        iid = listed_fact["issue"]
        issue = self.gitlab.issue(project_id, iid)
        self._check_run_budget()
        fact = _object_data(issue_fact, issue, project_id)
        if fact["issue"] != iid:
            raise ObjectError(f"issue {iid}: fresh response changed object identity")
        selected, reason = _object_data(issue_selected, issue, self.config)
        if not selected:
            summary["skipped"][reason] += 1
            return
        notes = self.gitlab.notes(project_id, iid)
        root, is_new = self._bound_root(project_id, "issue", iid, issue, notes, summary, dry_run)
        if is_new:
            summary["created"] += 1
            # A preview validates the origin too (read-only), so it shows what would stall (#105).
            origin_roots = self._resolve_origin_roots(issue, notes, subject="issue", project_id=project_id)
            if not dry_run:
                mentions = self._issue_new_mentions(project_id, fact)
                if origin_roots:
                    self._write_binding(project_id, "issue", iid, origin_roots[0])
                    for dest in origin_roots:
                        self._send_message(render_message(fact, "routing", first=True), reply_to=dest,
                                           mentions=mentions if dest == origin_roots[0] else ())
                        self._link(summary, dest)
                    return
                # Plaque root first (issue #78), binding before the first fact: a crash in
                # either window reruns through recovery (search by URL) or the update path.
                root = self._send_message(render_issue_plaque(fact, self._project_path(project_id)),
                                          project_id=project_id)
                self._write_binding(project_id, "issue", iid, root)
                self._send_message(render_message(fact, "routing", first=True), reply_to=root,
                                   mentions=mentions)
                self._link(summary, root)
            return
        if root is None:
            return
        destinations = [root]
        for extra in self._resolve_origin_roots(issue, notes, subject="issue", project_id=project_id):
            if extra not in destinations:
                destinations.append(extra)
        author = (issue.get("author") or {}).get("username")
        if not self._post_issue_to_roots(project_id, fact, notes, destinations, summary, dry_run,
                                         author=author if isinstance(author, str) else ""):
            summary["unchanged"] += 1

    def _post_issue_to_roots(
        self, project_id: int, fact: dict[str, Any], notes: list[dict[str, Any]],
        destinations: list[str], summary: dict[str, Any], dry_run: bool, author: str = "",
    ) -> bool:
        iid = fact["issue"]
        posted = False
        for dest in destinations:
            # people are tagged in the bound (first) thread only, like the MR rules
            attend = dest == destinations[0] and not dry_run
            events = self._thread(dest, project_id, "issue", iid)
            previous = previous_fact_from_thread(events, self.publisher, project_id, iid)
            change = classify_change(previous, fact)
            if change is not None:
                if not dry_run:
                    added = (
                        [name for name in fact["assignees"] if name not in previous["assignees"]]
                        if previous is not None and attend and fact["state"] == "opened" else []
                    )
                    self._send_message(
                        render_message(
                            fact, change, first=previous is None,
                            state_changed=previous is not None and previous.get("state") != fact["state"],
                        ),
                        reply_to=dest,
                        mentions=self._attention(
                            project_id, attention_candidates("gitlab.assignees", added))[0],
                    )
                summary["updated" if change != "activity" else "activity"] += 1
                self._link(summary, dest)
                posted = True
            comments = _object_data(
                pending_comments, notes, events, self.publisher, self.bot_user_id, self.config["since"]
            )
            for note in comments:
                if not dry_run:
                    commenter = (note.get("author") or {}).get("username")
                    mentions = self._attention(
                        project_id,
                        [*attention_candidates("gitlab.assignees", fact["assignees"], exclude=[commenter]),
                         *attention_candidates("gitlab.author", [author], exclude=[commenter])],
                        note=note,
                    )[0] if attend else []
                    self._send_message(render_comment_message(fact, note), reply_to=dest, mentions=mentions)
                summary["activity"] += 1
                self._link(summary, dest)
                posted = True
        return posted

    def _issue_new_mentions(self, project_id: int, fact: dict[str, Any]) -> list[str]:
        """A new open Issue tells its mapped assignees; a closed one is history, not a request."""

        if fact["state"] != "opened":
            return []
        return self._attention(project_id, attention_candidates("gitlab.assignees", fact["assignees"]))[0]

    def _mentionable(self) -> dict[str, str]:
        """People who can be mentioned: the CLI refuses p tags for pubkeys outside the channel."""

        if not self.people:
            return {}
        # This is intentionally a fresh read at each send decision.  A person
        # removed from the Channel, or changed to a bot role, must not retain a
        # p-tag capability through a run-local cache.
        roles = self.buzz.channel_members()
        return {
            name: pubkey for name, pubkey in self.people.items()
            if roles.get(pubkey) in responsible.HUMAN_ROLES
        }

    def _mr_targets(
        self, project_id: int, mr: dict[str, Any], *, include_maintainers: bool = True
    ) -> tuple[list[str], list[str]]:
        if project_id not in self._members:
            self._members[project_id] = self.gitlab.members(project_id)
        # The resolver needs the complete fresh role snapshot, not a prefiltered
        # alias map, so it can record non-member/bot rejection reasons.
        channel_roles = self.buzz.channel_members() if self.people else {}
        members = self._members[project_id] if include_maintainers else []
        return resolve_mr_mention_targets(
            mr,
            members,
            self.people,
            channel_roles,
            agent_pubkeys=self.config.get("agent_pubkeys", []),
        )

    def _attention(
        self, project_id: int, candidates: list[dict[str, str]], note: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        """p-tag pubkeys for one message (ADR-0018): the commenter's exact `@username` tokens first, then the
        structured candidates, all through the one gate; a fresh Channel read per decision, none without `people`."""

        if not self.people:
            return [], []
        named = self._comment_named(project_id, note) if note is not None else []
        merged = [*named, *candidates]
        if not merged:
            return [], []
        return resolve_attention_targets(
            merged, self.people, self.buzz.channel_members(),
            agent_pubkeys=self.config.get("agent_pubkeys", []),
        )

    def _comment_named(self, project_id: int, note: dict[str, Any]) -> list[dict[str, str]]:
        """Members a project member's comment names; a commenter outside the project names nobody."""

        commenter = (note.get("author") or {}).get("username")
        if not isinstance(commenter, str) or not commenter:
            return []
        if project_id not in self._members:
            self._members[project_id] = self._gitlab_call("members", project_id, default=[]) or []
        usernames = {
            member.get("username") for member in self._members[project_id]
            if isinstance(member, dict) and isinstance(member.get("username"), str)
        }
        if commenter not in usernames:
            return []
        return comment_mention_candidates(note.get("body"), usernames, commenter)

    def _pipeline_trigger(self, project_id: int, record: dict[str, Any]) -> list[dict[str, str]]:
        """The user who triggered a failed pipeline; read only when someone could be tagged, once per pipeline."""

        if not self.people or record.get("object") != "pipeline" or record.get("event") != "failed":
            return []
        pipeline_id = record.get("source_id")
        if not _positive_int(pipeline_id):
            return []
        key = (project_id, pipeline_id)
        if key not in self._pipeline_users:
            try:
                detail = self._gitlab_call("pipeline", project_id, pipeline_id, default={}) or {}
            except GitLabHTTPError as exc:
                if exc.status not in (403, 404):
                    raise
                detail = {}
            user = (detail.get("user") or {}).get("username") if isinstance(detail, dict) else None
            self._pipeline_users[key] = user if isinstance(user, str) else ""
        return attention_candidates("gitlab.pipeline_user", [self._pipeline_users[key]])

    def _record_attention(self, project_id: int, record: dict[str, Any]) -> list[str]:
        """p tags of a top-level record: failed deployments and default-branch pipelines tell their user, and a
        stalled-object notice tells the Channel owner (ADR-0010)."""

        if record.get("object") == "sync" and record.get("event") == "stalled":
            return self._channel_owner_tags()
        if not self.people:
            return []
        if record.get("object") == "deployment":
            candidates = attention_candidates("gitlab.deployer", [self._record_users.get(record["key"])])
        else:
            candidates = self._pipeline_trigger(project_id, record)
        return self._attention(project_id, candidates)[0]

    def _channel_owner_tags(self) -> list[str]:
        """The Channel owner(s) to tell about a stalled object. Nobody watches `degraded`, and a stalled object stays
        stalled until someone fixes its data. The owner comes from a fresh member read, needs no `people` mapping,
        and is never an agent or the Desk itself."""

        try:
            roles = self.buzz.channel_members()
        except BuzzCliError:
            raise
        except SyncError:
            return []  # an unreadable member list must not turn one stalled object into a failed run
        excluded = set(self.config.get("agent_pubkeys") or []) | {self.publisher}
        return sorted(pubkey for pubkey, role in roles.items() if role == "owner" and pubkey not in excluded)[:3]

    def _sync_mr(self, project_id: int, project: dict[str, Any], mr: dict[str, Any],
                 summary: dict[str, Any], dry_run: bool) -> None:
        if _positive_int(mr.get("iid")):
            self._mr_snapshots[(project_id, mr["iid"])] = mr
        if excluded_by_rules(mr, self.config):
            summary["skipped"]["excluded"] += 1
            return
        fact = _object_data(mr_fact, mr, project_id)
        iid = fact["mr"]
        notes = self.gitlab.mr_notes(project_id, iid)
        root, is_new = self._bound_root(project_id, "mr", iid, mr, notes, summary, dry_run)
        if is_new:
            summary["mr_created"] += 1
            if dry_run:
                self._resolve_origin_roots(mr, notes, subject="mr", project_id=project_id)  # a preview shows what would stall (#105)
                return
            # ADR-0015: an MR's facts and Diffs go to ONE thread, the one its binding names. Where that is: the first
            # GitLab-closing Issue, then the branch-name whitelist Issue, then the first origin, then the branch family,
            # then a plaque of its own. Every other related thread (further Issues, `related_merge_requests` hits, the
            # other origins) gets one cross-link now and nothing after; the reverse lookup only makes links.
            placing, related_only = self.mr_issue_plan(project_id, mr, iid)
            origin_roots = self._resolve_origin_roots(mr, notes, subject="mr", project_id=project_id)
            placed = self._reachable_issue_roots(project_id, placing, iid, summary, dry_run)
            if placing and not placed and not origin_roots:
                return  # every closing / branch Issue is excluded or unreadable: the MR is not published
            reviewable = fact["draft"] == "no" and fact["state"] == "opened"
            related_roots = self._existing_issue_roots(project_id, related_only, summary)
            links = self._linked_issue_links(project_id, iid, related_roots)
            if placed or origin_roots:
                primary = placed[0] if placed else origin_roots[0]
                full, unmapped = self._mr_targets(project_id, mr) if reviewable else ([], [])
                mentions = mr_mentions(None, fact, full, {})
                # Cross-links first, then the binding, then the fact: a crash after a cross-link reruns this path and
                # finds it in its thread (no second one); a crash after the binding reruns through the update path,
                # never into a second per-MR root (review F3).
                self._post_mr_xrefs(project_id, fact, primary, [*placed[1:], *origin_roots, *related_roots], summary)
                events = self._thread(primary, project_id, "mr", iid)
                self._write_binding(project_id, "mr", iid, primary)
                if previous_mr_fact_from_thread(events, self.publisher, project_id, iid) is None:
                    # a discussion thread takes one review assignment (ADR-0014); an Issue thread has no such rule
                    fired = not placed and self._thread_fired_reviewable(events)
                    fire = reviewable and not fired
                    self._send_message(
                        render_mr_message(fact, "lifecycle", unmapped if fire else [], links,
                                          became_reviewable=fire, first=True),
                        reply_to=self._mr_chain_parent(events, primary, project_id, iid),
                        mentions=mentions if fire else (),
                    )
                self._link(summary, primary)
                self._post_diffs(project_id, project, mr, fact, primary,
                                 self._thread(primary, project_id, "mr", iid), summary, dry_run)
                return
            # 2026-09-18 MR groups (issue #77): an unassociated MR joins its branch family's
            # thread — earliest sibling's root, own sub-chain inside it.
            group_root = self._find_mr_group_root(project_id, fact)
            if group_root is not None:
                group_events: list[dict[str, Any]] | None
                try:
                    group_events = self._thread(group_root, project_id, "mr", iid)
                except ObjectError:
                    group_events = None  # unreadable or full group thread: open an own root instead
                if group_events is not None:
                    fire = reviewable and not self._thread_fired_reviewable(group_events)
                    full, unmapped = self._mr_targets(project_id, mr) if fire else ([], [])
                    mentions = mr_mentions(None, fact, full, {})
                    self._post_mr_xrefs(project_id, fact, group_root, related_roots, summary)
                    # Bind before any fact is sent (review F3): a crash after a send must rerun through the bound
                    # path, never a second root.
                    self._write_binding(project_id, "mr", iid, group_root)
                    if previous_mr_fact_from_thread(group_events, self.publisher, project_id, iid) is None:
                        self._send_message(
                            render_mr_message(fact, "lifecycle", unmapped if fire else [], links,
                                              became_reviewable=fire, first=True),
                            reply_to=self._mr_chain_parent(group_events, group_root, project_id, iid),
                            mentions=mentions if fire else (),
                        )
                    self._link(summary, group_root)
                    self._post_diffs(project_id, project, mr, fact, group_root, group_events, summary, dry_run)
                    return
            full, unmapped = self._mr_targets(project_id, mr) if reviewable else ([], [])
            mentions = mr_mentions(None, fact, full, {})
            # Plaque root (issue #78): identity card at the top level, facts reply below.
            root = self._send_message(render_mr_plaque(fact, self._project_path(project_id)),
                                      project_id=project_id)
            self._post_mr_xrefs(project_id, fact, root, related_roots, summary)
            self._write_binding(project_id, "mr", iid, root)
            self._register_mr_group_keys(project_id, fact, root, dry_run)
            self._send_message(
                render_mr_message(fact, "lifecycle", unmapped, links,
                                  became_reviewable=reviewable, first=True),
                reply_to=root,
                mentions=mentions,
            )
            self._link(summary, root)
            self._post_diffs(project_id, project, mr, fact, root, [], summary, dry_run)
            return
        if root is None:
            return
        events = self._thread(root, project_id, "mr", iid)
        root_event = next((event for event in events if event.get("id") == root), None)
        # Only a Desk message is a plaque or a fact; the text of a person's root is never read as one (ADR-0014).
        desk_root = root_event is not None and root_event.get("pubkey") == self.publisher
        root_header = parse_header(root_event.get("content")) if desk_root else None
        root_ident = (
            plaque_identity(plaque_url(root_event.get("content")))
            if desk_root and root_header is None else None
        )
        merged_into_issue = (
            (root_header is not None and root_header["object"] == "issue")
            or (root_ident is not None and root_ident["object"] == "issue")
        )
        grouped_mr = (
            (root_header is not None and root_header["object"] == "mr" and root_header.get("mr") != iid)
            or (root_ident is not None and root_ident["object"] == "mr" and root_ident["iid"] != iid)
        )
        own_mr_root = (
            (root_header is not None and root_header.get("object") == "mr" and root_header.get("mr") == iid)
            or (root_ident is not None and root_ident["object"] == "mr" and root_ident["iid"] == iid)
        )
        if own_mr_root:
            # own per-MR root: (re)register the group anchor keys; idempotent self-heal
            # that also covers roots created before MR groups existed (issue #77).
            self._register_mr_group_keys(project_id, fact, root, dry_run)
        origin_bound = not own_mr_root and not merged_into_issue and not grouped_mr
        reply_parent = (
            self._mr_chain_parent(events, root, project_id, iid)
            if (merged_into_issue or grouped_mr or origin_bound) else root
        )
        previous = previous_mr_fact_from_thread(events, self.publisher, project_id, iid)
        change = classify_mr_change(previous, fact)
        if change is not None:
            reviewable = becomes_reviewable(previous, fact)
            if reviewable and self._thread_is_mr_group(events) and self._thread_fired_reviewable(events):
                reviewable = False  # one review assignment per group thread (2026-09-18); solo MRs unchanged
            if reviewable:
                mentions, unmapped = self._mr_targets(project_id, mr)
            else:
                added = set(fact["reviewers"]) - set((previous or {}).get("reviewers") or []) - {fact["author"]}
                reviewer_items = [
                    item for item in (mr.get("reviewers") or [])
                    if isinstance(item, dict) and item.get("username") in added
                ]
                mentions, unmapped = self._mr_targets(
                    project_id, {**mr, "reviewers": reviewer_items}, include_maintainers=False
                ) if added else ([], [])
            if (
                not dry_run and change == "lifecycle" and previous is not None
                and fact["state"] in ("merged", "closed") and previous.get("state") != fact["state"]
            ):
                # the outcome goes to the author, unless the author was the one who did it
                actor = _mr_state_actor(mr, fact["state"])
                told, _ = self._attention(
                    project_id, attention_candidates("gitlab.author", [fact["author"]], exclude=[actor]))
                mentions = list(dict.fromkeys([*told, *mentions]))[:3]
            if not dry_run:
                self._send_message(
                    render_mr_message(
                        fact, change, unmapped, became_reviewable=reviewable,
                        first=previous is None,
                        sha_changed=previous is not None
                        and _short_sha(previous.get("sha")) != _short_sha(fact["sha"]),
                    ),
                    reply_to=reply_parent,
                    mentions=mentions,
                )
            summary["mr_updated"] += 1
            self._link(summary, root)
        self._post_diffs(project_id, project, mr, fact, root, events, summary, dry_run)
        comments = _object_data(
            pending_comments, notes, events, self.publisher, self.bot_user_id, self.config["since"]
        )
        for note in comments:
            if not dry_run:
                commenter = (note.get("author") or {}).get("username")
                mentions = self._attention(
                    project_id,
                    [*attention_candidates("gitlab.author", [fact["author"]], exclude=[commenter]),
                     *attention_candidates("gitlab.reviewers", fact["reviewers"], exclude=[commenter])],
                    note=note,
                )[0]
                self._send_message(render_mr_comment_message(fact, note), reply_to=reply_parent,
                                   mentions=mentions)
            summary["activity"] += 1
            self._link(summary, root)
        if change is None and not comments:
            summary["unchanged"] += 1

    def _linked_issue_links(
        self, project_id: int, mr_iid: int, related_roots: list[str] | tuple[str, ...] = (),
    ) -> list[str]:
        """Buzz links for the `issues:` line of the first fact: the bound threads of GitLab's closes_issues
        (branch-name conventions are not an approved linking rule), then the threads of the Issues the reverse
        `related_merge_requests` lookup found (ADR-0015: it only makes links)."""

        issue_iids = [
            issue["iid"] for issue in self.gitlab.mr_closes_issues(project_id, mr_iid)
            if isinstance(issue, dict) and _positive_int(issue.get("iid"))
            and issue.get("project_id", project_id) == project_id  # cross-project closing issues are not ours
        ]
        links = []
        for issue_iid in issue_iids:
            try:
                notes = self.gitlab.notes(project_id, issue_iid)
            except GitLabHTTPError as exc:
                if exc.status == 404:
                    continue
                raise
            try:
                root = parse_binding(notes, self.bot_user_id, project_id, "issue", issue_iid, self.channel)
            except SyncError:
                continue  # the Issue's own sync reports its broken binding
            if root:
                links.append(buzz_message_link(self.channel, root))
        for root in related_roots:
            link = buzz_message_link(self.channel, root)
            if link not in links:
                links.append(link)
        return links

    def mr_issue_plan(self, project_id: int, mr: dict[str, Any], mr_iid: int,
                      *, with_related: bool = True) -> tuple[list[int], list[int]]:
        """`(placing, related_only)`: the ordered Issue iids this MR belongs to (2026-09-17, narrowed by ADR-0015).

        `placing` are the Issues that may decide where the MR lives: GitLab closes_issues, then the branch-name
        whitelist shape. `related_only` are the ones only the issue-side `related_merge_requests` reverse lookup over
        this run's scanned issues found (GitLab does not expose "branch created from issue" on the API): they only
        make links. Every iid is verified to exist, and the two lists together are capped at 3. `with_related=False`
        skips the reverse lookup (it costs one GitLab call per scanned Issue and only feeds links)."""

        iids: list[int] = []
        placing: set[int] = set()

        def add(issue_iid: Any, decides: bool) -> None:
            if not _positive_int(issue_iid):
                return
            if issue_iid not in iids:
                iids.append(issue_iid)
            if decides:
                placing.add(issue_iid)

        for issue in self.gitlab.mr_closes_issues(project_id, mr_iid):
            if isinstance(issue, dict) and issue.get("project_id", project_id) == project_id:
                add(issue.get("iid"), True)
        for issue_iid in branch_issue_iids(str(mr.get("source_branch") or "")):
            add(issue_iid, True)
        if with_related:
            for issue in self._issues_scanned.get(project_id, []):
                if not isinstance(issue, dict) or not _positive_int(issue.get("iid")):
                    continue
                key = (project_id, issue["iid"])
                if key not in self._related_mr_iids:
                    try:
                        related = self.gitlab.related_merge_requests(project_id, issue["iid"])
                    except GitLabHTTPError as exc:
                        if exc.status != 404:
                            raise
                        related = []
                    self._related_mr_iids[key] = {
                        item["iid"] for item in related if isinstance(item, dict) and _positive_int(item.get("iid"))
                    }
                if mr_iid in self._related_mr_iids[key]:
                    add(issue["iid"], False)
        verified: list[int] = []
        for issue_iid in iids[:3]:
            try:
                if isinstance(self.gitlab.issue(project_id, issue_iid), dict):
                    verified.append(issue_iid)
            except GitLabHTTPError as exc:
                if exc.status != 404:
                    raise
        return [iid for iid in verified if iid in placing], [iid for iid in verified if iid not in placing]

    def mr_issue_associations(self, project_id: int, mr: dict[str, Any], mr_iid: int) -> list[int]:
        """Every Issue the MR is related to, placing ones first (see `mr_issue_plan`)."""

        placing, related_only = self.mr_issue_plan(project_id, mr, mr_iid)
        return [*placing, *related_only]

    def _reachable_issue_root(self, project_id: int, issue_iid: int, mr_iid: int,
                              summary: dict[str, Any], dry_run: bool) -> str | None:
        """An associated Issue thread the MR can post into, or None when that Issue is excluded or its
        thread cannot take more replies: one bad association must not stall the whole Channel (review F4)."""

        try:
            root = self._ensure_issue_root(project_id, issue_iid, summary, dry_run)
            if root is not None:
                self._thread(root, project_id, "mr", mr_iid)
        except ObjectError:
            summary["skipped"]["excluded"] += 1
            return None
        return root

    def _reachable_issue_roots(self, project_id: int, issue_iids: list[int], mr_iid: int,
                               summary: dict[str, Any], dry_run: bool) -> list[str]:
        """The thread roots of the reachable Issues among `issue_iids`, in order, without repeats."""

        roots: list[str] = []
        for issue_iid in issue_iids:
            root = self._reachable_issue_root(project_id, issue_iid, mr_iid, summary, dry_run)
            if root is not None and root not in roots:
                roots.append(root)
        return roots

    def _existing_issue_roots(self, project_id: int, issue_iids: list[int], summary: dict[str, Any]) -> list[str]:
        """Roots of the Issues among `issue_iids` that already have a Thread and may be written to. A hit of the
        `related_merge_requests` lookup only makes a link (ADR-0015), so an Issue without a Thread (older than
        `since`, stalled) is not given one for it, and an excluded one is skipped and counted."""

        roots: list[str] = []
        for issue_iid in issue_iids:
            try:
                issue = self.gitlab.issue(project_id, issue_iid)
                selected, _reason = _object_data(issue_selected, issue, self.config)
                if not selected:
                    summary["skipped"]["excluded"] += 1
                    continue
                root = parse_binding(self.gitlab.notes(project_id, issue_iid), self.bot_user_id, project_id, "issue",
                                     issue_iid, self.channel)
            except GitLabHTTPError as exc:
                if exc.status != 404:
                    raise
                continue
            except SyncError:
                continue  # the Issue's own sync reports its broken binding
            if root is not None and root not in roots:
                roots.append(root)
        return roots

    def _post_mr_xrefs(self, project_id: int, fact: dict[str, Any], primary: str, others: list[str],
                       summary: dict[str, Any]) -> None:
        """One cross-link to the binding thread in every other related thread (ADR-0015), when the MR first appears.

        Idempotent per thread: a thread that already holds this MR's cross-link is left alone, so a rerun after a
        crash never repeats one. A thread that cannot take a reply (full, unreadable) is skipped and counted, never
        a stall. Nothing else is ever posted there: no fact, no Diff, no mention."""

        link = buzz_message_link(self.channel, primary)
        for dest in dict.fromkeys(others):
            if dest == primary:
                continue
            try:
                events = self._thread(dest, project_id, "mr", fact["mr"])
            except ObjectError:
                summary["skipped"]["excluded"] += 1
                continue
            if mr_xref_posted(events, self.publisher, project_id, fact["mr"]):
                continue
            self._send_message(render_mr_xref(fact, link), reply_to=dest)
            summary["mr_xrefs"] += 1
            self._link(summary, dest)

    def _ensure_issue_root(self, project_id: int, issue_iid: int,
                           summary: dict[str, Any], dry_run: bool) -> str | None:
        """The associated Issue's thread root, creating the root+binding if absent."""

        issue = self.gitlab.issue(project_id, issue_iid)
        selected, reason = _object_data(issue_selected, issue, self.config)
        if not selected:
            raise ObjectError(f"issue {issue_iid}: associated with an MR but excluded ({reason})")
        fact = _object_data(issue_fact, issue, project_id)
        notes = self.gitlab.notes(project_id, issue_iid)
        root = _object_data(parse_binding, notes, self.bot_user_id, project_id, "issue", issue_iid, self.channel)
        if root is not None or dry_run:
            return root
        root = self._send_message(render_issue_plaque(fact, self._project_path(project_id)),
                                  project_id=project_id)
        self._write_binding(project_id, "issue", issue_iid, root)
        self._send_message(render_message(fact, "routing", first=True), reply_to=root)
        summary["created"] += 1
        self._link(summary, root)
        return root

    def _mr_chain_parent(self, events: list[dict[str, Any]], root: str, project_id: int, mr_iid: int) -> str:
        """Inside a merged Issue thread, an MR's next fact replies to its previous fact."""

        latest = _latest_publisher_message(events, self.publisher, project_id, "mr", mr_iid, skip_change=MR_XREF)
        return latest[0]["id"] if latest is not None else root

    def _post_diffs(self, project_id: int, project: dict[str, Any], mr: dict[str, Any], fact: dict[str, Any],
                    root: str, events: list[dict[str, Any]], summary: dict[str, Any], dry_run: bool) -> None:
        if fact["sha"] == "-" or not diff_allowed(str(project.get("visibility") or "private"),
                                                  self.config.get("diff") or {}):
            return
        files, unusable = partition_diff_files(self.gitlab.mr_diffs(project_id, fact["mr"]))
        chunks, skipped = split_diff_by_file(unified_diff_from_files(files))
        summary["diff_skipped"] += len(unusable) + len(skipped)
        for chunk in chunks:
            if diff_already_posted(events, self.publisher, fact["sha"], chunk["file"]):
                continue
            if not dry_run:
                self._send_diff(
                    chunk["diff"], repo=str(project.get("http_url_to_repo") or ""), commit=fact["sha"],
                    file_path=chunk["file"], reply_to=root, source_branch=str(mr.get("source_branch") or ""),
                    target_branch=str(mr.get("target_branch") or ""), pr=fact["mr"], project_id=project_id)
            summary["diffs"] += 1

    def _sync_mr_records(self, project_id: int, records: list[dict[str, Any]],
                         summary: dict[str, Any], dry_run: bool) -> None:
        by_mr: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            if _positive_int(record.get("mr_iid")):
                by_mr.setdefault(record["mr_iid"], []).append(record)
        self._merge_retained_groups(project_id, "mr", by_mr)
        for mr_iid, group in sorted(by_mr.items()):
            self._run_group(summary, project_id, "mr", mr_iid, group, self._sync_mr_group, dry_run)

    def _sync_mr_group(self, project_id: int, mr_iid: int, group: list[dict[str, Any]],
                       summary: dict[str, Any], dry_run: bool) -> None:
        listed = self._mr_snapshots.get((project_id, mr_iid))
        if listed is not None and excluded_by_rules(listed, self.config):
            return  # _sync_mr already counted it
        notes = self.gitlab.mr_notes(project_id, mr_iid)  # transport errors fail the run, like any other read
        try:
            root = parse_binding(notes, self.bot_user_id, project_id, "mr", mr_iid, self.channel)
            binding_error = None
        except SyncError as exc:
            root, binding_error = None, exc
        # Render from the snapshot this run already compared, so an activity reply never carries state that the
        # lifecycle/update check has not seen. Exclusion is decided before the binding error or the Thread matters.
        if root is None:
            # Activity of an unbound MR that a closing reference or the branch-name whitelist ties to an Issue lands
            # in that Issue thread and establishes the binding, instead of going unbound (2026-09-17, ADR-0015).
            try:
                mr = listed or self.gitlab.merge_request(project_id, mr_iid)
            except GitLabHTTPError as exc:
                if exc.status != 404 or binding_error is not None:
                    raise
                summary["unbound"] += len(group)
                return
        else:
            mr = listed or self.gitlab.merge_request(project_id, mr_iid)
        if excluded_by_rules(mr, self.config):
            summary["skipped"]["excluded"] += 1
            return
        if binding_error is not None:
            raise ObjectError(str(binding_error)) from None
        if root is None:
            # ADR-0015: only closes_issues and the branch-name whitelist decide a binding; a related hit does not
            placing = self.mr_issue_plan(project_id, mr, mr_iid, with_related=False)[0] if not dry_run else []
            if not placing:
                summary["unbound"] += len(group)
                return
            root = self._ensure_issue_root(project_id, placing[0], summary, dry_run)
            if root is None:
                summary["unbound"] += len(group)
                return
            self._write_binding(project_id, "mr", mr_iid, root)
        events = self._thread(root, project_id, "mr", mr_iid)
        root_event = next((event for event in events if event.get("id") == root), None)
        desk_root = root_event is not None and root_event.get("pubkey") == self.publisher
        root_header = parse_header(root_event.get("content")) if desk_root else None
        reply_parent = root
        if root_header is not None and root_header["object"] == "issue":
            reply_parent = self._mr_chain_parent(events, root, project_id, mr_iid)
        posted = posted_keys(events, self.publisher)
        fresh = [record for record in group if record["key"] not in posted]
        if not fresh:
            return
        fact = _object_data(mr_fact, mr, project_id)
        for record in fresh:
            jobs: list[str] = []
            if record["object"] == "pipeline" and record["event"] == "failed" and record.get("source_id"):
                jobs = [str(job.get("name")) for job in self.gitlab.pipeline_jobs(project_id, record["source_id"])
                        if isinstance(job, dict) and job.get("name")]
            if not dry_run:
                if record["object"] == "mr" and record["event"] == "approved":
                    candidates = attention_candidates(
                        "gitlab.author", [fact["author"]], exclude=[record.get("actor")])
                else:
                    candidates = self._pipeline_trigger(project_id, record)
                self._send_message(render_mr_activity(fact, record, jobs), reply_to=reply_parent,
                                   mentions=self._attention(project_id, candidates)[0])
            summary["activity"] += 1
            self._link(summary, root)

    def _channel_messages(self, since_unix: int, cursor: str) -> list[dict[str, Any]]:
        for scanned_since, events in self._scans:
            if scanned_since <= since_unix:
                return events  # an earlier scan of this run already covers the window
        try:
            events = self.buzz.channel_messages(since_unix)
            self._scans.append((since_unix, events))
            return events
        except SyncError as exc:
            if cursor == self.config["since"]:
                raise SyncError(f"{exc}; there is no local cursor cache yet, so move config since closer to now") from None
            raise

    def _gitlab_call(self, name: str, *args: Any, default: Any = None) -> Any:
        fn = getattr(self.gitlab, name, None)
        if not callable(fn):
            return default
        return fn(*args)

    def _signal_degrade(self, signal: str) -> None:
        """Record one project-scoped degrade reason; repeats within a round collapse."""

        if signal not in self._degraded:
            self._degraded.append(signal)

    def _protected_branches(self, project_id: int) -> list[dict[str, Any]] | None:
        """Known protected-branch rows, or None when GitLab would not confirm the set."""

        cached = getattr(self, "_protected_branch_cache", None)
        if cached is None:
            self._protected_branch_cache = {}
            cached = self._protected_branch_cache
        if project_id not in cached:
            fn = getattr(self.gitlab, "protected_branches", None)
            if not callable(fn):
                cached[project_id] = []
            else:
                try:
                    value = fn(project_id)
                except GitLabHTTPError as exc:
                    if exc.status in (401, 403):
                        self._signal_degrade(f"{project_id}:protected_branches:HTTP {exc.status}")
                        cached[project_id] = None
                    else:
                        raise
                else:
                    cached[project_id] = [item for item in (value or []) if isinstance(item, dict)]
        return cached[project_id]

    def _mrs_for_git_activity(self, project_id: int, ref: str, sha: str) -> list[dict[str, Any]]:
        found: dict[int, dict[str, Any]] = {}

        def add(items: Any) -> None:
            for mr in items or []:
                if isinstance(mr, dict) and _positive_int(mr.get("iid")) and mr["iid"] not in found:
                    found[mr["iid"]] = mr
                    self._mr_snapshots[(project_id, mr["iid"])] = mr

        if ref:
            listed = self._gitlab_call("merge_requests_by_source_branch", project_id, ref, default=None)
            if listed is None:
                listed = [
                    mr for (pid, _iid), mr in self._mr_snapshots.items()
                    if pid == project_id and normalize_git_ref(str(mr.get("source_branch") or "")) == ref
                ]
            add(listed)
        if sha:
            add(self._gitlab_call("commit_merge_requests", project_id, sha, default=[]))
        return list(found.values())

    def _issues_for_activity_mrs(self, project_id: int, mrs: list[dict[str, Any]]) -> list[int]:
        iids: list[int] = []

        def add(issue_iid: Any) -> None:
            if _positive_int(issue_iid) and issue_iid not in iids:
                iids.append(issue_iid)

        for mr in mrs:
            mr_iid = mr["iid"]
            for issue in self.gitlab.mr_closes_issues(project_id, mr_iid):
                if isinstance(issue, dict) and issue.get("project_id", project_id) == project_id:
                    add(issue.get("iid"))
            for issue_iid in branch_issue_iids(str(mr.get("source_branch") or "")):
                add(issue_iid)
            related_issues = self._gitlab_call("mr_related_issues", project_id, mr_iid, default=[])
            for issue in related_issues or []:
                if not isinstance(issue, dict) or issue.get("project_id", project_id) != project_id:
                    continue
                issue_iid = issue.get("iid")
                if not _positive_int(issue_iid):
                    continue
                key = (project_id, issue_iid)
                if key not in self._related_mr_iids:
                    try:
                        related = self.gitlab.related_merge_requests(project_id, issue_iid)
                    except GitLabHTTPError as exc:
                        if exc.status != 404:
                            raise
                        related = []
                    self._related_mr_iids[key] = {
                        item["iid"] for item in related if isinstance(item, dict) and _positive_int(item.get("iid"))
                    }
                if mr_iid in self._related_mr_iids[key]:
                    add(issue_iid)
        verified: list[int] = []
        for issue_iid in iids:
            if len(verified) >= 3:
                break
            try:
                issue = self.gitlab.issue(project_id, issue_iid)
            except GitLabHTTPError as exc:
                if exc.status == 404:
                    continue
                raise
            selected, _reason = issue_selected(issue, self.config)
            if selected:
                verified.append(issue_iid)
        return verified

    def _git_activity_destination(
        self, project_id: int, project: dict[str, Any], record: dict[str, Any],
        candidates: list[dict[str, Any]] | None = None,
    ) -> tuple[str, Any]:
        if record.get("object") not in PLACEABLE_DIGEST_OBJECTS:
            return "digest", None
        ref = normalize_git_ref(record.get("ref") or "")
        sha = str(record.get("sha") or "")
        if not ref:
            return "digest", None
        try:
            mrs = self._mrs_for_git_activity(project_id, ref, sha)
            issues = self._issues_for_activity_mrs(project_id, mrs)
        except (GitLabHTTPError, SyncError) as exc:
            # An unconfirmed related lookup must not fail open into a branch thread, and must
            # not abort the round either: the record degrades to digest (issue #70). Per-endpoint
            # 404-as-empty-set semantics stay inside the lookups themselves.
            raise RelatedQueryError(_related_failure(exc)) from None
        if issues:
            return "issue", issues
        chosen = canonical_mr(mrs)
        if chosen is not None:
            return "mr", chosen["iid"]
        default_branch = str(project.get("default_branch") or "")
        try:
            protected = self._protected_branches(project_id)
        except (GitLabHTTPError, SyncError) as exc:
            raise RelatedQueryError(_related_failure(exc)) from None
        if protected is None:
            return "digest", None
        if ref_is_protected(ref, default_branch, protected):
            return "digest", None
        if record.get("event") == "branch_deleted":
            if self._find_branch_root(project_id, ref, candidates):
                return "branch", ref
            return "digest", None
        return "branch", ref

    def _branch_bindings_path(self, project_id: int) -> Path:
        return self.state_dir / f"branch-bindings-{project_id}.json"

    def _load_branch_bindings(self, project_id: int) -> dict[str, str]:
        path = self._branch_bindings_path(project_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"cannot read branch bindings: {type(exc).__name__}") from None
        if not isinstance(payload, dict):
            raise SyncError("branch bindings have an invalid schema")
        bindings: dict[str, str] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str) and HEX64_RE.fullmatch(value):
                bindings[key] = value
        return bindings

    def _store_branch_binding(self, project_id: int, ref: str, root: str) -> None:
        bindings = self._load_branch_bindings(project_id)
        bindings[normalize_git_ref(ref)] = root
        atomic_write_json(self._branch_bindings_path(project_id), bindings)

    def _mr_group_bindings_path(self, project_id: int) -> Path:
        return self.state_dir / f"mr-group-bindings-{project_id}.json"

    def _load_mr_group_bindings(self, project_id: int) -> dict[str, dict[str, str]]:
        """MR-group anchors: group key → {root, author}. Same lifecycle as branch
        bindings (issue #71): a corrupt file — unreadable, bad JSON, or any single
        entry off-schema — fails the round closed; a lost file only means later
        siblings open a new thread, existing bindings still hold."""

        path = self._mr_group_bindings_path(project_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"cannot read MR group bindings: {type(exc).__name__}") from None
        if not isinstance(payload, dict):
            raise SyncError("MR group bindings have an invalid schema")
        bindings: dict[str, dict[str, str]] = {}
        for key, value in payload.items():
            if (
                isinstance(key, str) and isinstance(value, dict)
                and isinstance(value.get("root"), str) and HEX64_RE.fullmatch(value["root"])
                and isinstance(value.get("author"), str) and value["author"]
            ):
                bindings[key] = {"root": value["root"], "author": value["author"]}
            else:
                # Fail the round like the file-level corruption above (reference contract):
                # silently dropping one anchor would fork that family's later siblings into
                # a new thread instead of stopping for a human to inspect the state file.
                raise SyncError(f"MR group bindings have an invalid schema at key {key!r}")
        return bindings

    def _store_mr_group_binding(self, project_id: int, key: str, root: str, author: str) -> None:
        bindings = self._load_mr_group_bindings(project_id)
        if key not in bindings:  # first writer wins; a slot never migrates to a newer root
            bindings[key] = {"root": root, "author": author}
            atomic_write_json(self._mr_group_bindings_path(project_id), bindings)

    def _register_mr_group_keys(self, project_id: int, fact: dict[str, Any], root: str,
                                dry_run: bool) -> None:
        """Register a per-MR root as the group anchor for its exact branch key, and for
        the stripped `<base>-<target>` key when that slot is free. Idempotent self-heal:
        legacy per-MR roots register the same way, so future siblings can join them."""

        if dry_run:
            return
        exact, stripped = mr_group_keys(fact)
        if not exact or exact == "-":
            return
        self._store_mr_group_binding(project_id, exact, root, fact["author"])
        if stripped:
            self._store_mr_group_binding(project_id, stripped, root, fact["author"])

    def _find_mr_group_root(self, project_id: int, fact: dict[str, Any]) -> str | None:
        """Group thread root for an unassociated MR, or None. The stripped key is
        author-guarded (same prefix by someone else is not the same change); the
        exact branch key groups regardless of author — it is the same branch."""

        exact, stripped = mr_group_keys(fact)
        state = self._load_mr_group_bindings(project_id)
        if stripped:
            entry = state.get(stripped)
            if entry is not None and entry["author"] == fact["author"]:
                return entry["root"]
        if exact and exact != "-":
            entry = state.get(exact)
            if entry is not None:
                return entry["root"]
        return None

    def _thread_fired_reviewable(self, events: list[dict[str, Any]]) -> bool:
        for event in events:
            if event.get("pubkey") != self.publisher:
                continue  # a participant's text that looks like a header is not a Desk fact (ADR-0014)
            header = parse_header(event.get("content"))
            if header is not None and header.get("object") == "mr" and header.get("transition") == "reviewable":
                return True
        return False

    def _thread_is_mr_group(self, events: list[dict[str, Any]]) -> bool:
        """Two or more distinct MR iids posted in one thread: it is a group thread, so the
        one-review-assignment rule applies. Solo per-MR threads keep today's behaviour."""
        iids = set()
        for event in events:
            if event.get("pubkey") != self.publisher:
                continue
            header = parse_header(event.get("content"))
            if header is not None and header.get("object") == "mr" and header.get("change") != MR_XREF:
                iids.add(header.get("mr"))
        return len(iids) >= 2

    def _find_branch_root(
        self, project_id: int, ref: str, candidates: list[dict[str, Any]] | None = None,
    ) -> str | None:
        name = neutralize(normalize_git_ref(ref))
        expected = branch_key(project_id, normalize_git_ref(ref))
        cached = self._load_branch_bindings(project_id).get(normalize_git_ref(ref))
        if cached:
            return cached
        plaque = self._branch_plaque_url(project_id, ref)
        for event in candidates or []:
            if event.get("pubkey") != self.publisher or _tag_values(event, "e"):
                continue
            header = parse_header(event.get("content"))
            if (
                header
                and header.get("object") == "branch"
                and header.get("project") == project_id
                and header.get("branch") == expected
                and branch_name_from_root(str(event.get("content") or "")) == name
            ):
                return str(event.get("id") or "") or None
            if plaque_url(event.get("content")) == plaque:  # plaque branch root (issue #78)
                return str(event.get("id") or "") or None
        return None

    def _ensure_branch_root(
        self, project_id: int, ref: str, url: str, summary: dict[str, Any], dry_run: bool,
        candidates: list[dict[str, Any]] | None = None,
    ) -> str | None:
        root = self._find_branch_root(project_id, ref, candidates)
        if root is not None or dry_run:
            return root
        # Plaque root (issue #78): the branch card; activities reply below. The stored
        # binding and the URL line agree, so both recovery paths find the same root.
        root = self._send_message(
            render_branch_plaque(ref, self._branch_plaque_url(project_id, ref),
                                 self._project_path(project_id)),
            project_id=project_id,
        )
        self._store_branch_binding(project_id, ref, root)
        self._link(summary, root)
        return root

    def _checked_origin_root(self, origin: dict[str, Any], *, subject: str, hint: bool) -> str:
        """The thread root one origin names, or OriginUnusable.

        An HTML marker may name a top-level message of this channel that someone else than Desk wrote (ADR-0014); a
        bare deep link (`hint`), and Desk's own message, only a Desk-published plaque or fact (ADR-0011). A named Desk
        reply walks to its thread root; a reply of anyone else is not a top-level message."""

        if origin["channel_id"] != self.channel:
            raise OriginUnusable(f"{subject} origin channel_id does not match this channel")
        named = origin["root_event_id"]
        events = self._origin_thread(named, subject)
        named_event = next((event for event in events if event.get("id") == named), None)
        if named_event is None:
            raise OriginUnusable(f"{subject} origin root cannot be read")
        try:
            root_id = origin_named_root_id(named_event, named)
        except SyncError as exc:
            raise OriginUnusable(f"{subject} {exc}") from None
        if root_id != named:
            events = self._origin_thread(root_id, subject)
        root_event = next((event for event in events if event.get("id") == root_id), None)
        if root_event is None:
            raise OriginUnusable(f"{subject} origin root cannot be read")
        if hint or root_event.get("pubkey") == self.publisher:
            # Desk's own message is a root only as a plaque or a fact; a bare link leads only to those.
            if origin_canonical_root(root_event, self.publisher, self.channel) is None:
                raise OriginUnusable(
                    f"{subject} origin root is not a Desk-published top-level plaque or fact in this channel"
                )
        else:
            # Somebody else's root: the marker must name that top-level message itself, or a Desk fact in its thread.
            # A reply written by anyone else is not one (ADR-0014); it only walks to a Desk plaque, above.
            if _tag_values(named_event, "e") and named_event.get("pubkey") != self.publisher:
                raise OriginUnusable(f"{subject} origin root is a reply, not a top-level message in this channel")
            if origin_top_level_root(root_event, self.channel) is None:
                raise OriginUnusable(f"{subject} origin root is not a top-level message in this channel")
        return root_id

    def _origin_thread(self, event_id: str, subject: str) -> list[dict[str, Any]]:
        """Read one origin thread. Exit 1 is bad input or not found (this origin is unusable); every other CLI
        failure is a relay or auth problem and stops the run, not just the object."""

        try:
            return self.buzz.thread(event_id)
        except BuzzCliError as exc:
            if exc.returncode != 1:
                raise
            raise OriginUnusable(f"{subject} origin root cannot be read ({exc})") from None

    def _note_origin_fallback(self, project_id: int, subject: str, iid: int, reason: str) -> None:
        """Record that a well-formed marker was unusable and the object syncs without it: one entry per object and
        reason per round. The round stays `ok`; the object is not behind."""

        reason = STALLED_SECRET_RE.sub("***", neutralize(_single_line(reason)))[:STALLED_REASON_LIMIT]
        entry = {"project": project_id, "object": subject, "iid": iid, "reason": reason}
        if entry not in self._origin_fallbacks:
            self._origin_fallbacks.append(entry)

    def _resolve_origin_roots(
        self, payload: dict[str, Any] | str | None, notes: list[dict[str, Any]] | None = None,
        *, subject: str, project_id: int,
    ) -> list[str]:
        """Thread roots named by description and human comments, first-seen order.

        A malformed marker stalls the object. A well-formed marker whose root is unusable is skipped and recorded
        in `origin_fallbacks` (ADR-0014); an unusable bare deep link is a plain reference and is ignored silently
        (ADR-0011). A relay or authentication failure reading a root still stops the run."""

        texts: list[str] = []
        description = payload.get("description") if isinstance(payload, dict) else payload
        if description not in (None, ""):
            if not isinstance(description, str):
                raise ObjectError("description must be a string")
            texts.append(description)
        texts.extend(origin_note_bodies(notes, self.bot_user_id))
        found: list[str] = []
        seen: set[str] = set()
        # An unusable origin names its object, so the message says which one description to fix.
        iid = payload.get("iid") if isinstance(payload, dict) else None
        valid_iid = isinstance(iid, int) and not isinstance(iid, bool) and iid > 0
        label = f"{subject} {iid}" if valid_iid else subject
        for text in texts:
            try:
                sources = parse_origin_sources(text)
            except ObjectError:
                raise
            except SyncError as exc:
                raise ObjectError(f"{label} {exc}") from None
            for origin, hint in sources:
                try:
                    root_id = self._checked_origin_root(origin, subject=label, hint=hint)
                except OriginUnusable as exc:
                    if not hint:
                        self._note_origin_fallback(project_id, subject, iid if valid_iid else 0, str(exc))
                    continue
                if root_id not in seen:
                    seen.add(root_id)
                    found.append(root_id)
        return found

    def _resolve_origin_root(
        self, payload: dict[str, Any] | str | None, *, subject: str, project_id: int,
        notes: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """First origin root, or None if absent."""

        roots = self._resolve_origin_roots(payload, notes, subject=subject, project_id=project_id)
        return roots[0] if roots else None

    def _ensure_mr_root_for_activity(
        self, project_id: int, mr_iid: int, summary: dict[str, Any], dry_run: bool,
    ) -> str | None:
        mr = self._mr_snapshots.get((project_id, mr_iid)) or self.gitlab.merge_request(project_id, mr_iid)
        if excluded_by_rules(mr, self.config):
            return None
        notes = self.gitlab.mr_notes(project_id, mr_iid)
        root = parse_binding(notes, self.bot_user_id, project_id, "mr", mr_iid, self.channel)
        if root is not None or dry_run:
            return root
        fact = _object_data(mr_fact, mr, project_id)
        # A branch-family thread already anchors this MR (2026-09-18): the activity rides
        # it instead of forking a fresh per-MR root. The MR's own sync binds it later.
        origin_root = self._resolve_origin_root(mr, subject="mr", project_id=project_id, notes=notes)
        if origin_root is not None:
            self._thread(origin_root, project_id, "mr", mr_iid)
            return origin_root
        group_root = self._find_mr_group_root(project_id, fact)
        if group_root is not None:
            try:
                self._thread(group_root, project_id, "mr", mr_iid)
                return group_root
            except ObjectError:
                pass  # unreadable or full: fall through to a fresh root
        # Plaque root (issue #78): the first lifecycle fact arrives with the MR's own sync.
        root = self._send_message(render_mr_plaque(fact, self._project_path(project_id)),
                                  project_id=project_id)
        self._write_binding(project_id, "mr", mr_iid, root)
        self._register_mr_group_keys(project_id, fact, root, dry_run)
        summary["mr_created"] += 1
        self._link(summary, root)
        return root

    def _milestone_bindings_path(self, project_id: int) -> Path:
        return self.state_dir / f"milestone-bindings-{project_id}.json"

    def _load_milestone_bindings(self, project_id: int) -> dict[int, str]:
        """Milestone roots have no GitLab-side binding note (milestones take no notes),
        so the anchor is local state — same fail-closed lifecycle as branch bindings."""

        path = self._milestone_bindings_path(project_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"cannot read milestone bindings: {type(exc).__name__}") from None
        if not isinstance(payload, dict):
            raise SyncError("milestone bindings have an invalid schema")
        bindings: dict[int, str] = {}
        for key, value in payload.items():
            if (
                isinstance(key, str) and key.isdigit() and isinstance(value, str)
                and 1 <= int(key) <= 10**12 and HEX64_RE.fullmatch(value)
            ):
                bindings[int(key)] = value
            else:
                raise SyncError(f"milestone bindings have an invalid schema at key {key!r}")
        return bindings

    def _store_milestone_binding(self, project_id: int, milestone_iid: int, root: str) -> None:
        bindings = self._load_milestone_bindings(project_id)
        bindings[milestone_iid] = root  # idempotent: a re-found root re-anchors after state loss
        atomic_write_json(self._milestone_bindings_path(project_id), {
            str(key): value for key, value in sorted(bindings.items())
        })

    def _milestone_plaque_url(self, project_id: int, milestone_iid: int) -> str:
        web_url = str((self._projects.get(project_id) or {}).get("web_url") or "").rstrip("/")
        if not web_url:
            raise SyncError(f"project {project_id} has no web_url for the milestone plaque")
        return f"{web_url}/-/milestones/{milestone_iid}"

    def _find_milestone_root(self, project_id: int, milestone_iid: int) -> str | None:
        """Bound state first; on a miss, search the channel for the plaque URL — the
        same full-history recovery the outbox uses, so lost state files do not fork
        long-lived milestone threads. An ambiguous search (cap hit) raises ObjectError
        and stops the round, exactly like Issue/MR root recovery."""

        cached = self._load_milestone_bindings(project_id).get(milestone_iid)
        if cached:
            return cached
        events = self.buzz.search_roots(
            self._milestone_plaque_url(project_id, milestone_iid), "milestone", milestone_iid, 0,
        )
        for event in events:
            if not _tag_values(event, "e") and plaque_url(event.get("content")) is not None:
                root = str(event.get("id") or "")
                if root:
                    return root
        return None

    def _ensure_milestone_root(self, project_id: int, milestone_iid: int, title: str,
                               summary: dict[str, Any], dry_run: bool) -> str | None:
        """The milestone's plaque thread root, creating it if absent."""

        root = self._find_milestone_root(project_id, milestone_iid)
        if root is not None:
            if not dry_run:
                self._store_milestone_binding(project_id, milestone_iid, root)  # re-anchor after state loss
            return root
        origin_root = self._resolve_origin_root(  # a preview validates the origin too (#105)
            self._milestone_items(project_id).get(milestone_iid), subject="milestone", project_id=project_id,
        )
        if dry_run:
            return None
        if origin_root is not None:
            self._store_milestone_binding(project_id, milestone_iid, origin_root)
            summary["created"] += 1
            self._link(summary, origin_root)
            return origin_root
        root = self._send_message(
            render_milestone_plaque(title, self._milestone_plaque_url(project_id, milestone_iid),
                                    self._project_path(project_id)),
            project_id=project_id,
        )
        self._store_milestone_binding(project_id, milestone_iid, root)
        summary["created"] += 1
        self._link(summary, root)
        return root

    def _resolve_milestone_iid(self, project_id: int, record: dict[str, Any]) -> int | None:
        """Milestone identity for a record: the events API usually omits target_iid for
        milestone events, so the fallback is an exact title match against the project's
        milestone list (fetched once per run)."""

        if _positive_int(record.get("source_id")):
            return int(record["source_id"])
        title = str(record.get("title") or "")
        if not title:
            return None
        items = self._milestone_items(project_id)
        iid = next(
            (item.get("iid") for item in items.values() if str(item.get("title") or "") == title),
            None,
        )
        return int(iid) if _positive_int(iid) else None

    def _milestone_items(self, project_id: int) -> dict[int, dict[str, Any]]:
        cache = getattr(self, "_milestone_item_cache", None)
        if cache is None:
            cache = self._milestone_item_cache = {}
        if project_id not in cache:
            cache[project_id] = {
                int(item["iid"]): item
                for item in (self.gitlab.milestones(project_id) or [])
                if isinstance(item, dict) and _positive_int(item.get("iid"))
            }
        return cache[project_id]

    def _sync_milestone_records(self, project_id: int, records: list[dict[str, Any]],
                                summary: dict[str, Any], dry_run: bool) -> None:
        by_iid: dict[int, list[dict[str, Any]]] = {}
        unresolved = 0
        for record in records:
            milestone_iid = self._resolve_milestone_iid(project_id, record)
            if milestone_iid is None:
                unresolved += 1
                continue
            by_iid.setdefault(milestone_iid, []).append(record)
        if unresolved:
            # Visible, not silent: an unresolvable milestone event is a dropped fact.
            self._signal_degrade(f"{project_id}:milestone_identity:{unresolved} unresolved")
            summary["skipped"]["milestone_identity"] += unresolved
        self._merge_retained_groups(project_id, "milestone", by_iid)
        for milestone_iid, group in sorted(by_iid.items()):
            self._run_group(summary, project_id, "milestone", milestone_iid, group, self._sync_milestone_group,
                            dry_run)

    def _sync_milestone_group(self, project_id: int, milestone_iid: int, group: list[dict[str, Any]],
                              summary: dict[str, Any], dry_run: bool) -> None:
        root = self._ensure_milestone_root(project_id, milestone_iid, str(group[-1].get("title") or ""),
                                           summary, dry_run)
        if root is None:
            return
        destinations = [root]
        for extra in self._resolve_origin_roots(
            self._milestone_items(project_id).get(milestone_iid), subject="milestone", project_id=project_id,
        ):
            if extra not in destinations:
                destinations.append(extra)
        url = self._milestone_plaque_url(project_id, milestone_iid)
        for dest in destinations:
            events = self._thread(dest, project_id, "milestone", milestone_iid)
            posted = posted_keys(events, self.publisher)
            for record in group:
                if record["key"] in posted:
                    continue
                payload = dict(record)
                payload["url"] = url  # the canonical per-milestone URL, also on title-resolved records
                if not dry_run:
                    self._send_message(render_record(payload), reply_to=dest)
                summary["notified"]["milestone"] += 1
                self._link(summary, dest)

    def _place_git_activity(
        self, project_id: int, project: dict[str, Any], record: dict[str, Any],
        summary: dict[str, Any], dry_run: bool,
        candidates: list[dict[str, Any]] | None = None,
    ) -> bool:
        try:
            kind, target = self._git_activity_destination(project_id, project, record, candidates)
        except RelatedQueryError as exc:
            self._signal_degrade(f"{project_id}:related_query:{exc}")
            return False
        if kind == "digest":
            return False
        if dry_run:
            summary["activity"] += 1
            return True
        if kind == "issue":
            posted_any = False
            for issue_iid in target:
                try:
                    root = self._ensure_issue_root(project_id, issue_iid, summary, dry_run)
                except ObjectError:
                    summary["skipped"]["excluded"] += 1
                    continue
                if root is None:
                    continue
                events = self._thread(root, project_id, "issue", issue_iid)
                if record["key"] in posted_keys(events, self.publisher):
                    posted_any = True
                    continue
                issue = self.gitlab.issue(project_id, issue_iid)
                fact = _object_data(issue_fact, issue, project_id)
                self._send_message(render_issue_git_activity(fact, record), reply_to=root)
                summary["activity"] += 1
                self._link(summary, root)
                posted_any = True
            return posted_any
        if kind == "mr":
            root = self._ensure_mr_root_for_activity(project_id, target, summary, dry_run)
            if root is None:
                return False
            events = self._thread(root, project_id, "mr", target)
            if record["key"] in posted_keys(events, self.publisher):
                return True
            mr = self._mr_snapshots.get((project_id, target)) or self.gitlab.merge_request(project_id, target)
            fact = _object_data(mr_fact, mr, project_id)
            self._send_message(render_mr_activity(fact, record), reply_to=root)
            summary["activity"] += 1
            self._link(summary, root)
            return True
        ref = str(target)
        web_url = str(project.get("web_url") or "")
        quoted = urllib.parse.quote(normalize_git_ref(ref), safe="/")
        url = record.get("url") or f"{web_url}/-/commits/{quoted}"
        root = self._ensure_branch_root(project_id, ref, url, summary, dry_run, candidates)
        if root is None:
            return False
        events = self._thread(root, project_id, "branch", 0,
                              expected_plaque=self._branch_plaque_url(project_id, ref))
        if record["key"] in posted_keys(events, self.publisher):
            return True
        self._send_message(render_branch_activity(project_id, ref, record), reply_to=root)
        summary["activity"] += 1
        self._link(summary, root)
        return True

    def _sync_top_level(self, project_id: int, project: dict[str, Any], cursor: str,
                        summary: dict[str, Any], dry_run: bool) -> None:
        web_url = str(project.get("web_url") or "")
        default_branch = str(project.get("default_branch") or "")
        records: list[dict[str, Any]] = []
        for event in events_since(self.gitlab.events(project_id, events_after_date(cursor)), cursor):
            record = record_from_event(event, project_id, web_url)
            if record is not None:
                records.append(record)
        for pipeline in self.gitlab.pipelines(project_id, cursor):
            record = record_from_pipeline(pipeline, project_id, default_branch)
            if record is not None:
                records.append(record)
        for deployment in self.gitlab.deployments(project_id, cursor):
            record = record_from_deployment(deployment, project_id, web_url)
            if record is not None:
                records.append(record)
                deployer = (deployment.get("user") or {}).get("username")
                if isinstance(deployer, str) and deployer:
                    self._record_users[record["key"]] = deployer
        for release in self.gitlab.releases(project_id):
            record = record_from_release(release, project_id, cursor)
            if record is not None:
                records.append(record)
        # policy 2026-09-18: feature flags are not notified, so they are not
        # fetched at all (NOTIFIED_OBJECTS is the authoritative list)
        tokens = self.gitlab.access_tokens(project_id)
        if tokens is None:
            summary["unavailable"].append(f"{project_id}:access_tokens")
        else:
            records.extend(record_from_access_token(item, project_id, web_url, self._scan_date) for item in tokens)
        unique: dict[str, dict[str, Any]] = {}
        for record in records:
            if not record or record["key"] in unique:  # offset pages can list one item twice
                continue
            if record["placement"] == "instant" and is_muted(record, self.config):
                continue  # mute_events silences top-level notices only; thread facts always flow
            unique[record["key"]] = record
        records = list(unique.values())
        self._sync_mr_records(project_id, [r for r in records if r["placement"] == "mr_thread"], summary, dry_run)
        self._sync_milestone_records(
            project_id, [r for r in records if r["placement"] == "milestone_thread"], summary, dry_run)
        records = [record for record in records if record["placement"] in ("instant", "digest")]
        records.extend(self._stalled_records.pop(project_id, []))
        if not records:
            return
        # Policy 2026-09-18 (#77): live records no longer carry placement "digest", so the
        # attribution ladder and summary publisher below only run for synthetic inputs and a
        # possible policy revert. Their removal is tracked separately once the policy holds
        # in production.
        # A record newer than the cursor can only have been posted after the cursor; daily-keyed token expiry
        # notices can have been posted any time since UTC midnight.
        window_starts = []
        if any(record["object"] not in DAILY_OBJECTS for record in records):
            window_starts.append(parse_timestamp(cursor, "cursor") - DEDUPE_OVERLAP)
        if any(record["object"] in DAILY_OBJECTS for record in records):
            window_starts.append(dt.datetime.fromisoformat(f"{self._scan_date}T00:00:00+00:00") - DAILY_WINDOW_SKEW)
        scanned = self._channel_messages(int(min(window_starts).timestamp()), cursor)
        # Only top-level notices count: an MR thread reply can carry the same key without being a notice.
        posted = posted_keys([event for event in scanned if not _tag_values(event, "e")], self.publisher)
        posted |= self._acked_summary_keys()
        fresh = [record for record in records if record["key"] not in posted]
        for record in (record for record in fresh if record["placement"] == "instant"):
            if not dry_run:
                self._send_message(render_record(record), mentions=self._record_attention(project_id, record))
            summary["notified"]["instant"] += 1
        digest_records = [record for record in fresh if record["placement"] == "digest"]
        remaining: list[dict[str, Any]] = []
        for record in digest_records:
            if self._place_git_activity(project_id, project, record, summary, dry_run, scanned):
                continue
            remaining.append(record)
        for payload in build_summary_request_payloads(
            remaining, project_id, self._project_visibilities[project_id]
        ):
            request_id = delivery_change_id(self.channel, "summary_request", payload)
            if not dry_run:
                request_id = self._queue_delivery("summary_request", payload)
            request = public_summary_request(request_id, payload)
            if request not in summary["summary_requests"]:
                summary["summary_requests"].append(request)


def select_summary_request(
    scopes: list[tuple[dict[str, Any], Path]], *, claim: bool,
) -> dict[str, Any] | None:
    """Select, and optionally reserve, one globally oldest fixed-inventory request.

    Callers must hold every scope lock returned by :func:`acquire_scope_locks`.
    A durable SUMMARIZING/PUBLISHING state binds later model prose to the facts
    previously returned by the runner without exposing a target selector.
    """

    candidates: list[tuple[Any, str, str, Syncer, dict[str, Any], dict[str, Any]]] = []
    for config, state_dir in scopes:
        syncer = Syncer(config, None, None, state_dir=Path(state_dir))
        ledger = syncer._read_outbox()
        for item in ledger["pending"]:
            if item.get("kind") != "summary_request":
                continue
            change_id = item.get("change_id")
            status = item.get("status")
            if (
                not isinstance(change_id, str)
                or not HEX64_RE.fullmatch(change_id)
                or status not in {"PENDING", "SUMMARIZING", "PUBLISHING"}
            ):
                raise SyncError("pending summary request has invalid identity or state")
            queued = parse_timestamp(item.get("queued_at"), "summary queued_at")
            request = public_summary_request(change_id, item.get("payload"))
            candidates.append((queued, change_id, str(state_dir), syncer, item, request))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[:3])
    if len(candidates) > 1 and candidates[0][:3] == candidates[1][:3]:
        raise SyncError("oldest summary request is ambiguous")
    claimed = [candidate for candidate in candidates if candidate[4]["status"] != "PENDING"]
    if len(claimed) > 1:
        raise SyncError("fixed inventory contains multiple claimed summary requests")
    selected = claimed[0] if claimed else candidates[0]
    if claimed and selected is not candidates[0]:
        raise SyncError("claimed summary request is not the globally oldest request")
    if claim and selected[4]["status"] == "PENDING":
        syncer, item = selected[3], selected[4]
        ledger = syncer._read_outbox()
        durable = next(
            (entry for entry in ledger["pending"] if entry.get("change_id") == item["change_id"]),
            None,
        )
        if not isinstance(durable, dict) or durable.get("status") != "PENDING":
            raise SyncError("oldest summary request changed before it could be claimed")
        durable["status"] = "SUMMARIZING"
        syncer._write_outbox(ledger)
        selected = (*selected[:4], durable, selected[5])
    return {
        "config": selected[3].config,
        "state_dir": selected[3].state_dir,
        "item": selected[4],
        "request": selected[5],
    }


def _read_owner_only_json(path: Path, label: str) -> dict[str, Any]:
    """Read a JSON object from an owner-only regular file, never a symlink (config and shared people file)."""
    path = Path(path)
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or path.resolve(strict=True) != path.absolute()
        ):
            raise SyncError(f"{label} must be an owner-only regular file, not a symlink")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            value = json.load(handle)
    except SyncError:
        raise
    except OSError as exc:
        if path.is_symlink():
            raise SyncError(f"{label} must be an owner-only regular file, not a symlink") from None
        raise SyncError(f"cannot read {label} {path.name}: {type(exc).__name__}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SyncError(f"cannot read {label} {path.name}: {type(exc).__name__}") from None
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(value, dict):
        raise SyncError(f"{label} must be a JSON object")
    return value


def load_config(path: Path) -> dict[str, Any]:
    return _read_owner_only_json(Path(path), "config")


def default_adapters(config: dict[str, Any], env: dict[str, str], dry_run: bool) -> tuple[Any, Any]:
    validate_publisher_identity(env, config["publisher_pubkey"])
    return GitLabClient(config, env), BuzzCli(config, env)


def redact(text: str, env: dict[str, str], token_env: str | None = None) -> str:
    for key, value in env.items():
        if value and len(value) >= 8 and (key == token_env or key.startswith("BUZZ_") or "TOKEN" in key or "KEY" in key):
            text = text.replace(value, "***")
    return text


def execute(
    config_path: Path,
    state_dir: Path,
    *,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
    adapter_factory: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Run one sync and return its stable JSON envelope without writing stdout.

    The Desk Agent Step calls this function with fixed runtime-owned paths and
    inherited credentials. Keeping output out of this layer also preserves a
    stable programmatic contract for tests and future adapters.
    """

    runtime_env = dict(os.environ if env is None else env)
    token_env: str | None = None
    try:
        config = load_config(Path(config_path))
        validate_config(config)
        token_env = config["gitlab"]["token_env"]
        validate_relay_url(runtime_env.get("BUZZ_RELAY_URL"))
        child_env(runtime_env, token_env)
        validate_token(runtime_env.get(token_env), token_env)
        gitlab, buzz = (adapter_factory or default_adapters)(config, runtime_env, dry_run)
        result = Syncer(config, gitlab, buzz, state_dir=Path(state_dir)).run(dry_run=dry_run)
        return 0, result
    except SyncError as exc:
        return 1, {"status": "error", "error": redact(str(exc), runtime_env, token_env)}
    except Exception as exc:  # noqa: BLE001 — callers require JSON on every failure
        # Only the type is returned: exception messages may carry paths or secrets.
        return 1, {"status": "error", "error": f"unexpected {type(exc).__name__}"}


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    adapter_factory: Any = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="channel sync config JSON")
    parser.add_argument("--state-dir", help="cache and lock directory (default ~/.local/state/buzz/gitlab-buzz-sync)")
    parser.add_argument("--dry-run", action="store_true", help="plan writes without sending")
    args = parser.parse_args(argv)
    runtime_env = dict(os.environ if env is None else env)
    state_dir = (Path(args.state_dir) if args.state_dir else
                 Path(runtime_env.get("HOME", ".")) / ".local/state/buzz/gitlab-buzz-sync")
    code, result = execute(
        Path(args.config), state_dir, dry_run=args.dry_run, env=runtime_env, adapter_factory=adapter_factory
    )
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
