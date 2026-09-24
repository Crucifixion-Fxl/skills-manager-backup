#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic route-writer for Buzz relays without Workflow thread replies.

The default relay-0.2.1 mode is a local, one-shot scan with no Workflow or HTTP
listener and does not require a bearer credential:

  gitlab_buzz_route_reply.py --config <route-writer.json> --state-dir <private-dir> --scan-once

The process reads one administrator-signed Channel Canvas snapshot plus new
Desk-published GitLab facts, resolves each Canvas role through the code-owned stable Role
registry, verifies the source event and canonical Thread, then publishes one
idempotent reply as Desk. The legacy loopback HTTP listener remains available
only as an explicit non-default adapter for a Channel Workflow using
``call_webhook``.
"""
from __future__ import annotations

import argparse
from collections import deque
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_sync as sync  # noqa: E402


ENDPOINT = "/v1/route-replies"
MAX_BODY_BYTES = 4096
CLIENT_READ_TIMEOUT_SECONDS = 5
RATE_LIMIT_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_CONCURRENT_REQUESTS = 32
SCAN_OVERLAP_SECONDS = 60
MAX_CANVAS_BYTES = 65_536
MAX_CANVAS_EVENTS = 200
CONFIG_REQUIRED_KEYS = frozenset({"sender_pubkey", "channels", "buzz"})
CONFIG_OPTIONAL_KEYS = frozenset({"secret_env", "scan_since"})
CONFIG_KEYS = CONFIG_REQUIRED_KEYS | CONFIG_OPTIONAL_KEYS
CHANNEL_KEYS = frozenset({"publisher_pubkey", "canvas_admin_pubkeys", "roles"})
ROLE_KEYS = frozenset({"mention", "mention_pubkey"})
REQUEST_KEYS = frozenset({"channel_id", "message_id", "route_id"})
MENTION_RE = re.compile(r"@[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
FORBIDDEN_ROLE_RE = re.compile(r"(?:^|[._-])(?:desk|executor)(?:$|[._-])", re.IGNORECASE)
REASON_RE = re.compile(r"[a-z0-9][a-z0-9+._:/-]{0,79}")
ROUTE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SIG128_RE = re.compile(r"[0-9a-f]{128}")
CANVAS_START = "<!-- gitlab-buzz-routing:v1 -->"
CANVAS_END = "<!-- /gitlab-buzz-routing -->"
CANVAS_COLUMNS = ("route_id", "trigger_prefix", "role", "reason")
ISSUE_ROUTE_PREFIX_RE = re.compile(
    rf"\[gitlab-notify:v1\]\[object:issue\]\[type:{sync.FACT_VALUE}\]"
    rf"\[status:{sync.FACT_VALUE}\]\[state:(?:opened|closed)\]\[change:routing\]"
)
MR_ROUTE_PREFIX_RE = re.compile(
    r"\[gitlab-notify:v1\]\[object:mr\]\[state:opened\]\[draft:no\]\[change:lifecycle\]"
    r"\[transition:reviewable\]"
)
LISTEN_RE = re.compile(r"(127\.0\.0\.1|localhost):([1-9][0-9]{0,4})")


class ConfigError(RuntimeError):
    """Invalid static configuration; the server must not start."""


class RequestError(RuntimeError):
    """Invalid caller-controlled request body."""


class RouteError(RuntimeError):
    """The route could not be proven safe; no write may happen."""


def _strict_keys(value: dict[str, Any], expected: frozenset[str], label: str, error_type: type[RuntimeError]) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise error_type(f"{label} keys do not match the contract")


def validate_channel_rule(rule: Any) -> dict[str, Any]:
    """Validate code-owned trust anchors and Role identities, never route policy."""

    if not isinstance(rule, dict):
        raise ConfigError("each channel rule must be an object")
    _strict_keys(rule, CHANNEL_KEYS, "channel rule", ConfigError)
    publisher = rule.get("publisher_pubkey")
    if not isinstance(publisher, str) or not sync.HEX64_RE.fullmatch(publisher):
        raise ConfigError("channel publisher_pubkey must be 64 lowercase hex")
    admins = rule.get("canvas_admin_pubkeys")
    if (
        not isinstance(admins, list)
        or not 1 <= len(admins) <= 32
        or not all(isinstance(item, str) and sync.HEX64_RE.fullmatch(item) for item in admins)
        or len(set(admins)) != len(admins)
    ):
        raise ConfigError("canvas_admin_pubkeys must contain 1..32 distinct lowercase hex keys")
    roles = rule.get("roles")
    if not isinstance(roles, dict) or not 1 <= len(roles) <= 64:
        raise ConfigError("channel roles must define 1..64 code-owned Role identities")
    seen_pubkeys: set[str] = set()
    seen_mentions: set[str] = set()
    for role_id, role in roles.items():
        if (
            not isinstance(role_id, str)
            or not ROUTE_ID_RE.fullmatch(role_id)
            or FORBIDDEN_ROLE_RE.search(role_id)
        ):
            raise ConfigError("role ids must be non-Desk, non-executor lowercase slugs")
        if not isinstance(role, dict):
            raise ConfigError("each role must be an object")
        _strict_keys(role, ROLE_KEYS, "role", ConfigError)
        mention = role.get("mention")
        mention_pubkey = role.get("mention_pubkey")
        if (
            not isinstance(mention, str)
            or not MENTION_RE.fullmatch(mention)
            or FORBIDDEN_ROLE_RE.search(mention.removeprefix("@"))
        ):
            raise ConfigError("role mention must name one non-Desk, non-executor Agent")
        if not isinstance(mention_pubkey, str) or not sync.HEX64_RE.fullmatch(mention_pubkey):
            raise ConfigError("role mention_pubkey must be 64 lowercase hex")
        if mention in seen_mentions or mention_pubkey in seen_pubkeys:
            raise ConfigError("role mentions and stable pubkeys must be distinct")
        seen_mentions.add(mention)
        seen_pubkeys.add(mention_pubkey)
    trust_pubkeys = [publisher, *admins, *seen_pubkeys]
    if len(trust_pubkeys) != len(set(trust_pubkeys)):
        raise ConfigError("route trust identities must use distinct pubkeys")
    return rule


def _canvas_cell(cell: str) -> str:
    value = cell.strip()
    if value.startswith("`") or value.endswith("`"):
        if len(value) < 2 or not (value.startswith("`") and value.endswith("`")):
            raise RouteError("Canvas route table has malformed inline code")
        value = value[1:-1]
        if "`" in value:
            raise RouteError("Canvas route table has nested inline code")
    if not value or "\r" in value or "\n" in value:
        raise RouteError("Canvas route table has an empty or multiline cell")
    return value


def _canvas_row(line: str) -> tuple[str, str, str, str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise RouteError("Canvas route table row must use four pipe-delimited cells")
    cells = stripped[1:-1].split("|")
    if len(cells) != len(CANVAS_COLUMNS):
        raise RouteError("Canvas route table row must use exactly four cells")
    return tuple(_canvas_cell(cell) for cell in cells)  # type: ignore[return-value]


def parse_canvas_routes(content: Any, roles: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Parse the one deliberately-small Markdown table owned by Channel admins."""

    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CANVAS_BYTES:
        raise RouteError("Canvas content must be UTF-8 text within the size limit")
    if content.count(CANVAS_START) != 1 or content.count(CANVAS_END) != 1:
        raise RouteError("Canvas must contain exactly one versioned GitLab route table")
    start = content.index(CANVAS_START) + len(CANVAS_START)
    end = content.index(CANVAS_END)
    if end <= start:
        raise RouteError("Canvas route table markers are out of order")
    lines = [line for line in content[start:end].splitlines() if line.strip()]
    if len(lines) < 3 or _canvas_row(lines[0]) != CANVAS_COLUMNS:
        raise RouteError("Canvas route table header does not match the v1 contract")
    separator = _canvas_row(lines[1])
    if any(not re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise RouteError("Canvas route table separator is malformed")
    if len(lines) - 2 > 64:
        raise RouteError("Canvas route table exceeds 64 routes")

    routes: dict[str, dict[str, str]] = {}
    seen_prefixes: set[str] = set()
    for line in lines[2:]:
        route_id, prefix, role_id, reason = _canvas_row(line)
        if not ROUTE_ID_RE.fullmatch(route_id):
            raise RouteError("Canvas route id must be a lowercase slug")
        if route_id in routes:
            raise RouteError("Canvas route ids must be distinct")
        if not (ISSUE_ROUTE_PREFIX_RE.fullmatch(prefix) or MR_ROUTE_PREFIX_RE.fullmatch(prefix)):
            raise RouteError("Canvas trigger_prefix is not one complete supported routing prefix")
        if prefix in seen_prefixes:
            raise RouteError("Canvas trigger_prefix values must be distinct")
        if role_id not in roles or FORBIDDEN_ROLE_RE.search(role_id):
            raise RouteError("Canvas route references an unknown or forbidden role")
        if not REASON_RE.fullmatch(reason):
            raise RouteError("Canvas route reason must be one short machine-readable value")
        role = roles[role_id]
        canonical = {
            "route_id": route_id,
            "trigger_prefix": prefix,
            "role": role_id,
            "mention": role["mention"],
            "mention_pubkey": role["mention_pubkey"],
            "reason": reason,
        }
        routes[route_id] = {
            **canonical,
            "policy_sha256": hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        seen_prefixes.add(prefix)
    if not routes:
        raise RouteError("Canvas route table must define at least one route")
    return routes


def select_canvas_policy(events: Any, channel_id: str, channel_rule: dict[str, Any]) -> dict[str, Any]:
    """Select and validate the visible latest signed Canvas event, failing closed."""

    validate_channel_rule(channel_rule)
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_CANVAS_EVENTS:
        raise RouteError("Buzz must return 1..200 Canvas events")
    normalized: list[dict[str, Any]] = []
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("kind") != 40100
            or not isinstance(event.get("id"), str)
            or not sync.HEX64_RE.fullmatch(event["id"])
            or not isinstance(event.get("pubkey"), str)
            or not sync.HEX64_RE.fullmatch(event["pubkey"])
            or not sync._positive_int(event.get("created_at"))
            or not isinstance(event.get("content"), str)
            or not isinstance(event.get("sig"), str)
            or not SIG128_RE.fullmatch(event["sig"])
            or not _exact_channel(event, channel_id)
        ):
            raise RouteError("Canvas event has an invalid signed envelope")
        normalized.append(event)
    latest = max(normalized, key=lambda item: (int(item["created_at"]), item["id"]))
    canonical = json.dumps(
        [0, latest["pubkey"], int(latest["created_at"]), 40100, latest["tags"], latest["content"]],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).digest()
    if (
        not hmac.compare_digest(latest["id"], digest.hex())
        or not sync.nk.schnorr_verify(
            digest, bytes.fromhex(latest["pubkey"]), bytes.fromhex(latest["sig"])
        )
    ):
        raise RouteError("latest Canvas event id or signature is invalid")
    if latest["pubkey"] not in channel_rule["canvas_admin_pubkeys"]:
        raise RouteError("latest Canvas author is not allowlisted")
    routes = parse_canvas_routes(latest["content"], channel_rule["roles"])
    return {
        "event_id": latest["id"],
        "author_pubkey": latest["pubkey"],
        "created_at": int(latest["created_at"]),
        "routes": routes,
    }


def verify_nostr_event_signature(event: dict[str, Any], *, label: str) -> None:
    """Recompute a raw NIP-01 event id and verify its BIP-340 signature."""

    signature = event.get("sig")
    if not isinstance(signature, str) or not SIG128_RE.fullmatch(signature):
        raise RouteError(f"{label} event id or signature is invalid")
    canonical = json.dumps(
        [
            0,
            event["pubkey"],
            int(event["created_at"]),
            int(event["kind"]),
            event["tags"],
            event["content"],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).digest()
    if (
        not hmac.compare_digest(event["id"], digest.hex())
        or not sync.nk.schnorr_verify(
            digest, bytes.fromhex(event["pubkey"]), bytes.fromhex(signature)
        )
    ):
        raise RouteError(f"{label} event id or signature is invalid")


def _same_signed_event(left: Any, right: Any) -> bool:
    """Compare the immutable NIP-01 envelope while ignoring adapter decorations."""

    fields = ("id", "pubkey", "created_at", "kind", "tags", "content", "sig")
    return isinstance(left, dict) and isinstance(right, dict) and all(
        left.get(field) == right.get(field) for field in fields
    )


def validate_config(config: Any, *, mode: str | None = None) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    if set(config) - CONFIG_KEYS or CONFIG_REQUIRED_KEYS - set(config):
        raise ConfigError("config keys do not match the contract")
    if mode not in {None, "scan", "http"}:
        raise ConfigError("config validation mode is invalid")
    secret_env = config.get("secret_env")
    if secret_env is not None:
        if (
            not isinstance(secret_env, str)
            or not sync.TOKEN_ENV_RE.fullmatch(secret_env)
            or secret_env.startswith("BUZZ_")
            or secret_env in sync.CHILD_ENV_KEYS
        ):
            raise ConfigError("secret_env must be an uppercase non-BUZZ environment variable")
    if mode == "http" and secret_env is None:
        raise ConfigError("secret_env is required in HTTP mode")
    scan_since = config.get("scan_since")
    if scan_since is not None:
        try:
            sync.parse_timestamp(scan_since, "scan_since")
        except sync.SyncError as exc:
            raise ConfigError(str(exc)) from None
    if mode == "scan" and scan_since is None:
        raise ConfigError("scan_since is required in local scan mode")
    if not isinstance(config.get("sender_pubkey"), str) or not sync.HEX64_RE.fullmatch(config["sender_pubkey"]):
        raise ConfigError("sender_pubkey must be 64 lowercase hex")
    channels = config.get("channels")
    if not isinstance(channels, dict) or len(channels) != 1:
        raise ConfigError("one route-reply process must configure exactly one channel")
    for channel_id, rule in channels.items():
        if not isinstance(channel_id, str) or not sync.UUID_RE.fullmatch(channel_id):
            raise ConfigError("channel allowlist keys must be lowercase UUIDs")
        validate_channel_rule(rule)
        policy_pubkeys = {
            *rule["canvas_admin_pubkeys"],
            *(role["mention_pubkey"] for role in rule["roles"].values()),
        }
        if config["sender_pubkey"] in policy_pubkeys:
            raise ConfigError("route trust identities must use distinct pubkeys")
        if mode == "scan" and config["sender_pubkey"] != rule["publisher_pubkey"]:
            raise ConfigError("local scan sender_pubkey must equal Desk publisher_pubkey")
        if mode == "http" and config["sender_pubkey"] == rule["publisher_pubkey"]:
            raise ConfigError("HTTP sender and publisher must use distinct pubkeys")
    buzz = config.get("buzz")
    if not isinstance(buzz, dict):
        raise ConfigError("buzz section is required")
    try:
        sync._reject_unknown_keys(buzz, sync.BUZZ_CONFIG_KEYS, "buzz")
        sync.validate_buzz_cli_path(buzz.get("cli_path"), buzz.get("cli_sha256"))
    except sync.SyncError as exc:
        raise ConfigError(str(exc)) from None
    return config


def parse_request(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RequestError("request must be a JSON object")
    _strict_keys(value, REQUEST_KEYS, "request", RequestError)
    channel_id, message_id, route_id = value.get("channel_id"), value.get("message_id"), value.get("route_id")
    if not isinstance(channel_id, str) or not sync.UUID_RE.fullmatch(channel_id):
        raise RequestError("channel_id must be a lowercase UUID")
    if not isinstance(message_id, str) or not sync.HEX64_RE.fullmatch(message_id):
        raise RequestError("message_id must be 64 lowercase hex")
    if not isinstance(route_id, str) or not ROUTE_ID_RE.fullmatch(route_id):
        raise RequestError("route_id must be one lowercase route slug")
    return value


def route_marker(request: dict[str, str], route: dict[str, str]) -> str:
    return (
        f"[gitlab-route:v2][source:{request['message_id']}]"
        f"[route:{request['route_id']}][policy:{route['policy_sha256']}]"
    )


def render_route_message(request: dict[str, str], route: dict[str, str]) -> str:
    return (
        f"{route['mention']} handle GitLab change: {route['reason']}\n"
        f"{route_marker(request, route)}"
    )


def _tags(event: dict[str, Any], name: str) -> list[list[Any]]:
    tags = event.get("tags")
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, list) and tag and tag[0] == name]


def _exact_channel(event: dict[str, Any], channel_id: str) -> bool:
    return [tag[:2] for tag in _tags(event, "h")] == [["h", channel_id]]


def _canonical_root(events: list[dict[str, Any]], trigger: dict[str, Any], channel_id: str) -> str:
    e_tags = _tags(trigger, "e")
    if not e_tags:
        root_id = trigger["id"]
    else:
        explicit_roots = [tag[1] for tag in e_tags if len(tag) >= 4 and tag[3] == "root"]
        candidates = explicit_roots or [tag[1] for tag in e_tags if len(tag) >= 4 and tag[3] == "reply"]
        if len(candidates) != 1 or not isinstance(candidates[0], str) or not sync.HEX64_RE.fullmatch(candidates[0]):
            raise RouteError("trigger has no unique canonical root")
        root_id = candidates[0]
    roots = [event for event in events if event.get("id") == root_id]
    if len(roots) != 1 or not _exact_channel(roots[0], channel_id) or _tags(roots[0], "e"):
        raise RouteError("canonical root was not found as a top-level event in the channel")
    return root_id


def verify_route_event(event: Any, sender_pubkey: str, channel_id: str, event_id: str, content: str,
                       reply_to: str, mention_pubkey: str) -> None:
    if (
        not isinstance(event, dict)
        or event.get("id") != event_id
        or event.get("kind") != 9
        or event.get("pubkey") != sender_pubkey
        or event.get("content") != content
        or not _exact_channel(event, channel_id)
        or [tag[:4] for tag in _tags(event, "e")] != [["e", reply_to, "", "reply"]]
        or [tag[:2] for tag in _tags(event, "p")] != [["p", mention_pubkey]]
    ):
        raise RouteError("Buzz route reply readback does not match author, channel, thread or mention")


class RouteReplyService:
    def __init__(self, channels: dict[str, dict[str, Any]], sender_pubkey: str, buzz: Any, *, state_dir: Path):
        self.channels = channels
        self.sender_pubkey = sender_pubkey
        self.buzz = buzz
        self.state_dir = Path(state_dir)
        self._prepare_private_dir(self.state_dir, "route-reply state directory")
        channel_id = next(iter(channels))
        scope = json.dumps(
            {"channel_id": channel_id, "sender_pubkey": sender_pubkey},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.scope_dir = self.state_dir / f"route-reply-{hashlib.sha256(scope.encode()).hexdigest()[:24]}"
        self._prepare_private_dir(self.scope_dir, "route-reply scope directory")
        self.lock_path = self.scope_dir / "service.lock"
        # The local Desk scanner and HTTP fallback deliberately have different
        # senders.  A sender-scoped service lock therefore cannot exclude the
        # two modes.  Key this lease only by Channel + trusted fact publisher
        # so both configurations contend on the same owner-controlled inode.
        mode_scope = json.dumps(
            {"channel_id": channel_id, "publisher_pubkey": channels[channel_id]["publisher_pubkey"]},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.mode_lock_path = self.state_dir / (
            f"route-mode-{hashlib.sha256(mode_scope.encode()).hexdigest()[:24]}.lock"
        )
        self._route_lock = threading.Lock()

    @staticmethod
    def _prepare_private_dir(path: Path, label: str) -> None:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ConfigError(f"cannot prepare {label}: {type(exc).__name__}") from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or resolved != path.absolute()
        ):
            raise ConfigError(f"{label} must be an owner-only real directory")

    def _open_lock(self):
        handle = None
        try:
            fd = os.open(
                self.lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                os.close(fd)
                raise RouteError("route-reply lock must be an owner-only regular file")
            handle = os.fdopen(fd, "r+")
        except OSError as exc:
            raise RouteError(f"cannot open durable route lock: {type(exc).__name__}") from None
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            handle.close()
            raise RouteError("another route-reply process owns this state scope") from None
        except OSError as exc:
            handle.close()
            raise RouteError(f"cannot lock durable route state: {type(exc).__name__}") from None

    def acquire_mode(self, mode: str):
        """Acquire the process-lifetime local-scan/HTTP exclusion lease."""

        publisher = next(iter(self.channels.values()))["publisher_pubkey"]
        if mode == "scan":
            if self.sender_pubkey != publisher:
                raise ConfigError("local scan sender must equal the Desk publisher")
        elif mode == "http":
            if self.sender_pubkey == publisher:
                raise ConfigError("HTTP sender must be distinct from the Desk publisher")
        else:
            raise ConfigError("route mode lease must be scan or http")

        handle = None
        try:
            fd = os.open(
                self.mode_lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                os.close(fd)
                raise RouteError("route mode lease must be an owner-only regular file")
            handle = os.fdopen(fd, "r+")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            json.dump({"version": 1, "mode": mode}, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            return handle
        except BlockingIOError:
            if handle is not None:
                handle.close()
            raise RouteError("local scanner and HTTP fallback cannot be active together") from None
        except OSError as exc:
            if handle is not None:
                handle.close()
            raise RouteError(f"cannot acquire route mode lease: {type(exc).__name__}") from None

    @staticmethod
    def release_mode(handle: Any) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    def load_policy(self, channel_id: str) -> dict[str, Any]:
        channel_rule = self.channels.get(channel_id)
        if channel_rule is None:
            raise RouteError("channel is not allowlisted")
        return select_canvas_policy(self.buzz.canvas_events(channel_id), channel_id, channel_rule)

    def route(
        self, raw_request: Any, *, policy: dict[str, Any] | None = None,
        source_event: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        # The listener is concurrent so a partial HTTP upload cannot block healthful callers;
        # serialize the durable read-before-write section in and across processes.
        with self._route_lock:
            handle = self._open_lock()
            try:
                return self._route(raw_request, policy=policy, source_event=source_event)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()

    @staticmethod
    def _operation_id(request: dict[str, str]) -> str:
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _operation_path(self, operation_id: str) -> Path:
        return self.scope_dir / f"{operation_id}.json"

    def _read_operation(self, expected: dict[str, Any]) -> dict[str, Any] | None:
        path = self._operation_path(expected["operation_id"])
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RouteError(f"cannot read durable route state: {type(exc).__name__}") from None
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_BODY_BYTES
            ):
                raise RouteError("durable route state must be an owner-only regular file")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                value = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RouteError(f"cannot parse durable route state: {type(exc).__name__}") from None
        finally:
            if fd >= 0:
                os.close(fd)
        allowed = set(expected) | {"status", "event_id"}
        if (
            not isinstance(value, dict)
            or set(value) - allowed
            or value.get("status") not in {"PENDING", "ACKED"}
            or any(value.get(key) != item for key, item in expected.items())
            or (
                "event_id" in value
                and (not isinstance(value["event_id"], str) or not sync.HEX64_RE.fullmatch(value["event_id"]))
            )
            or (value.get("status") == "ACKED" and "event_id" not in value)
        ):
            raise RouteError("durable route state does not match the configured operation")
        return value

    def _write_operation(self, value: dict[str, Any]) -> None:
        try:
            sync.atomic_write_json(self._operation_path(value["operation_id"]), value)
        except OSError as exc:
            raise RouteError(f"cannot persist durable route state: {type(exc).__name__}") from None

    def _ack_operation(self, operation: dict[str, Any], event_id: str) -> None:
        self._write_operation({**operation, "status": "ACKED", "event_id": event_id})

    def _marker_event(self, events: list[dict[str, Any]], marker: str, channel_id: str,
                      root_id: str, mention_pubkey: str) -> dict[str, Any] | None:
        authored = [
            event for event in events
            if event.get("pubkey") == self.sender_pubkey
            and marker in str(event.get("content") or "").splitlines()
        ]
        if any(
            not _exact_channel(event, channel_id)
            or [tag[:4] for tag in _tags(event, "e")] != [["e", root_id, "", "reply"]]
            or [tag[:2] for tag in _tags(event, "p")] != [["p", mention_pubkey]]
            or not isinstance(event.get("id"), str)
            or not sync.HEX64_RE.fullmatch(event["id"])
            for event in authored
        ):
            raise RouteError("signed route marker has invalid channel, thread or mention tags")
        if len(authored) > 1:
            raise RouteError("multiple route replies exist for one source route")
        return authored[0] if authored else None

    def _validated_source(
        self, raw_request: Any, *, policy: dict[str, Any] | None = None,
        source_event: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]], str, dict[str, Any]]:
        request = parse_request(raw_request)
        channel_rule = self.channels.get(request["channel_id"])
        if channel_rule is None:
            raise RouteError("channel is not allowlisted")
        if policy is None:
            policy = self.load_policy(request["channel_id"])
        if (
            not isinstance(policy, dict)
            or not isinstance(policy.get("event_id"), str)
            or not sync.HEX64_RE.fullmatch(policy["event_id"])
            or not isinstance(policy.get("routes"), dict)
        ):
            raise RouteError("Canvas route policy snapshot is invalid")
        route = policy["routes"].get(request["route_id"])
        if route is None:
            raise RouteError("route id is not present in the trusted Canvas")
        publisher_pubkey = channel_rule["publisher_pubkey"]
        events = self.buzz.thread(request["channel_id"], request["message_id"])
        triggers = [event for event in events if event.get("id") == request["message_id"]]
        if len(triggers) != 1:
            raise RouteError("trigger message was not found exactly once")
        trigger = triggers[0]
        if source_event is not None:
            if not _same_signed_event(trigger, source_event):
                raise RouteError("route trigger changed after its signed scan")
        # The local scanner has already authenticated every source event, but the
        # compatibility HTTP path has no signed envelope to compare. An edit must
        # therefore always prove its own NIP-01 identity here as well.
        if source_event is not None or trigger.get("kind") == 40003:
            verify_nostr_event_signature(trigger, label="re-read route trigger")
        routed_trigger = trigger
        if trigger.get("kind") == 40003:
            target = sync.edit_target(trigger)
            if target is None:
                raise RouteError("edit trigger has no unique original target")
            original_thread = self.buzz.thread(request["channel_id"], target)
            originals = [event for event in original_thread if event.get("id") == target]
            if len(originals) != 1:
                raise RouteError("edit trigger original was not found exactly once")
            original = originals[0]
            if (
                original.get("kind") != 9 or original.get("pubkey") != publisher_pubkey
                or not _exact_channel(original, request["channel_id"])
            ):
                raise RouteError("edit trigger does not target one publisher-authored channel message")
            verify_nostr_event_signature(original, label="edit trigger original")
            # Routing uses the edit's authenticated content and id, but the immutable original's
            # thread tags.  The route marker therefore stays idempotent per Git change while the
            # reply lands in the canonical discussion rather than under the overlay event.
            routed_trigger = {**trigger, "id": original["id"], "kind": 9, "tags": original.get("tags")}
            events = [*original_thread, trigger]
        header = sync.parse_header(trigger.get("content"))
        if (
            trigger.get("kind") not in (9, 40003)
            or trigger.get("pubkey") != publisher_pubkey
            or not _exact_channel(trigger, request["channel_id"])
            or not header
            or header.get("object") not in {"issue", "mr"}
            or not sync.matches_trigger_prefix(trigger.get("content"), route["trigger_prefix"])
        ):
            raise RouteError("trigger does not match the server-bound route fact")
        root_id = _canonical_root(events, routed_trigger, request["channel_id"])
        return request, route, events, root_id, policy

    def preview(
        self, raw_request: Any, *, policy: dict[str, Any] | None = None,
        source_event: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Validate a local scan decision without creating state or sending."""

        request, route, _, root_id, policy = self._validated_source(
            raw_request, policy=policy, source_event=source_event,
        )
        return {
            "status": "would_send",
            "root_event_id": root_id,
            "route_id": request["route_id"],
            "mention_pubkey": route["mention_pubkey"],
            "canvas_event_id": policy["event_id"],
        }

    def _route(
        self, raw_request: Any, *, policy: dict[str, Any] | None = None,
        source_event: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        request, route, events, root_id, _ = self._validated_source(
            raw_request, policy=policy, source_event=source_event,
        )
        content = render_route_message(request, route)
        marker = route_marker(request, route)
        operation = {
            "version": 1,
            "operation_id": self._operation_id(request),
            "channel_id": request["channel_id"],
            "message_id": request["message_id"],
            "route_id": request["route_id"],
            "root_event_id": root_id,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "mention_pubkey": route["mention_pubkey"],
            "policy_sha256": route["policy_sha256"],
        }
        recorded = self._read_operation(operation)
        existing = self._marker_event(
            events, marker, request["channel_id"], root_id, route["mention_pubkey"]
        )
        if existing is not None:
            self._ack_operation(operation, existing["id"])
            return {"status": "duplicate", "event_id": existing["id"], "root_event_id": root_id}
        if recorded is not None:
            event_id = recorded.get("event_id")
            if isinstance(event_id, str):
                try:
                    self.buzz.verify(
                        request["channel_id"], event_id, content, root_id, route["mention_pubkey"]
                    )
                except RouteError:
                    if recorded["status"] == "ACKED":
                        raise RouteError("recorded route reply is temporarily unavailable") from None
                    raise RouteError("route reply is still pending readback") from None
                self._ack_operation(operation, event_id)
                return {"status": "duplicate", "event_id": event_id, "root_event_id": root_id}
            raise RouteError("route reply is still pending after an ambiguous send")
        self._write_operation({**operation, "status": "PENDING"})
        event_id = self.buzz.send(request["channel_id"], content, root_id, route["mention_pubkey"])
        self._write_operation({**operation, "status": "PENDING", "event_id": event_id})
        self.buzz.verify(request["channel_id"], event_id, content, root_id, route["mention_pubkey"])
        self._ack_operation(operation, event_id)
        return {"status": "sent", "event_id": event_id, "root_event_id": root_id}


class RouteBuzzCli:
    def __init__(self, config: dict[str, Any], env: dict[str, str], *, runner: Any = subprocess.run,
                 sleeper: Any = time.sleep):
        validate_config(config)
        try:
            sync.validate_publisher_identity(env, config["sender_pubkey"])
            sync.validate_relay_url(env.get("BUZZ_RELAY_URL"))
            self.env = sync.child_env(env, str(config.get("secret_env") or "GITLAB_BUZZ_ROUTE_SECRET"))
        except sync.SyncError as exc:
            raise ConfigError(str(exc)) from None
        self.cli = config["buzz"]["cli_path"]
        self.sender_pubkey = config["sender_pubkey"]
        self.runner = runner
        self.sleeper = sleeper

    def command(self, args: list[str], content: str | None = None) -> Any:
        try:
            result = self.runner([self.cli, *args], input=content, capture_output=True, text=True,
                                 timeout=45, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RouteError(f"Buzz CLI could not run: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise RouteError("Buzz CLI command failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RouteError("Buzz CLI returned non-JSON output") from None

    def thread(self, channel_id: str, message_id: str) -> list[dict[str, Any]]:
        return sync._collect_events(self.command(
            ["messages", "thread", "--channel", channel_id, "--event", message_id, "--limit", "500"]
        ))

    def canvas_events(self, channel_id: str) -> list[dict[str, Any]]:
        """Read raw normalized kind-40100 events so author and signature stay visible."""

        events = sync._collect_events(self.command([
            "messages", "get", "--channel", channel_id, "--kinds", "40100",
            "--limit", str(MAX_CANVAS_EVENTS),
        ]))
        if len(events) >= MAX_CANVAS_EVENTS:
            raise RouteError("Buzz Canvas event window is incomplete")
        return events

    def channel_messages(self, channel_id: str, since_unix: int) -> list[dict[str, Any]]:
        """Read ordinary facts and edit overlays since a durable local cursor."""

        found: dict[str, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(sync.CHANNEL_PAGE_MAX):
            args = [
                "messages", "get", "--channel", channel_id, "--kinds", "9,40003",
                "--since", str(int(since_unix)), "--limit", str(sync.CHANNEL_PAGE_LIMIT),
            ]
            if before is not None:
                args += ["--before", str(before)]
            page = sync._collect_events(self.command(args))
            fresh = [event for event in page if str(event.get("id")) not in found]
            for event in fresh:
                found[str(event.get("id"))] = event
            if len(page) < sync.CHANNEL_PAGE_LIMIT:
                break
            if not fresh:
                raise RouteError("Buzz route scan found more same-second messages than one page holds")
            times = [event.get("created_at") for event in page]
            if not all(sync._positive_int(value) for value in times):
                raise RouteError("Buzz route scan messages have no created_at to page by")
            before = min(times)
        else:
            raise RouteError("Buzz route scan exceeded its page limit")
        return [
            event for event in found.values()
            if isinstance(event, dict) and _exact_channel(event, channel_id)
        ]

    def send(self, channel_id: str, content: str, reply_to: str, mention_pubkey: str) -> str:
        value = self.command(sync.build_send_args(self.cli, channel_id, reply_to, [mention_pubkey])[1:], content)
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if not isinstance(event_id, str) or not sync.HEX64_RE.fullmatch(event_id) or value.get("accepted") is False:
            raise RouteError("Buzz rejected the route reply")
        return event_id

    def verify(self, channel_id: str, event_id: str, content: str, reply_to: str, mention_pubkey: str) -> None:
        for attempt in range(sync.READBACK_ATTEMPTS):
            event = next((item for item in self.thread(channel_id, reply_to) if item.get("id") == event_id), None)
            if event is not None:
                break
            if attempt < sync.READBACK_ATTEMPTS - 1:
                self.sleeper(0.5)
        else:
            raise RouteError("Buzz route reply could not be read back")
        verify_route_event(event, self.sender_pubkey, channel_id, event_id, content, reply_to, mention_pubkey)


class LocalRouteScanner:
    """One durable local scan, fixed inside the current Desk turn."""

    def __init__(self, config: dict[str, Any], service: RouteReplyService, buzz: Any, *,
                 state_dir: Path, clock: Any = time.time):
        validate_config(config, mode="scan")
        self.config = config
        self.service = service
        self.buzz = buzz
        self.channel_id = next(iter(config["channels"]))
        self.channel_rule = config["channels"][self.channel_id]
        self.publisher_pubkey = self.channel_rule["publisher_pubkey"]
        self.sender_pubkey = config["sender_pubkey"]
        self.clock = clock
        self.state_dir = Path(state_dir)
        RouteReplyService._prepare_private_dir(self.state_dir, "route-writer state directory")
        self.cursor_path = service.scope_dir / "scan-cursor.json"
        self.lock_path = service.scope_dir / "scan.lock"
        try:
            self.scan_since = int(sync.parse_timestamp(config["scan_since"], "scan_since").timestamp())
        except (OverflowError, OSError, ValueError) as exc:
            raise ConfigError("scan_since cannot be represented as a Unix timestamp") from exc
        if self.scan_since < 0:
            raise ConfigError("scan_since must not be before the Unix epoch")

    def _open_lock(self):
        handle = None
        try:
            fd = os.open(
                self.lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                os.close(fd)
                raise RouteError("route scan lock must be an owner-only regular file")
            handle = os.fdopen(fd, "r+")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if handle is not None:
                handle.close()
            raise RouteError("another local route scan owns this state scope") from None
        except OSError as exc:
            if handle is not None:
                handle.close()
            raise RouteError(f"cannot lock local route scan: {type(exc).__name__}") from None

    def _read_cursor(self) -> tuple[int, set[str]]:
        try:
            fd = os.open(self.cursor_path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return self.scan_since, set()
        except OSError as exc:
            raise RouteError(f"cannot read route scan cursor: {type(exc).__name__}") from None
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > 65_536
            ):
                raise RouteError("route scan cursor must be an owner-only regular file")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                state = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RouteError(f"cannot parse route scan cursor: {type(exc).__name__}") from None
        finally:
            if fd >= 0:
                os.close(fd)
        recent = state.get("recent") if isinstance(state, dict) else None
        if (
            not isinstance(state, dict)
            or set(state) != {"version", "channel_id", "publisher_pubkey", "sender_pubkey", "cursor", "recent"}
            or state.get("version") != 1
            or state.get("channel_id") != self.channel_id
            or state.get("publisher_pubkey") != self.publisher_pubkey
            or state.get("sender_pubkey") != self.sender_pubkey
            or not sync._positive_int(state.get("cursor"))
            or not isinstance(recent, list)
            or not all(isinstance(item, str) and sync.HEX64_RE.fullmatch(item) for item in recent)
            or len(set(recent)) != len(recent)
        ):
            raise RouteError("route scan cursor does not match this identity and Channel")
        return int(state["cursor"]), set(recent)

    def _write_cursor(self, cursor: int, recent: list[str]) -> None:
        try:
            sync.atomic_write_json(self.cursor_path, {
                "version": 1,
                "channel_id": self.channel_id,
                "publisher_pubkey": self.publisher_pubkey,
                "sender_pubkey": self.sender_pubkey,
                "cursor": cursor,
                "recent": sorted(set(recent)),
            })
        except OSError as exc:
            raise RouteError(f"cannot persist route scan cursor: {type(exc).__name__}") from None

    def _publisher_event(self, event: Any) -> tuple[str, int, str] | None:
        if not isinstance(event, dict) or event.get("pubkey") != self.publisher_pubkey:
            return None
        event_id, created_at, content = event.get("id"), event.get("created_at"), event.get("content")
        if (
            event.get("kind") not in (9, 40003)
            or not isinstance(event_id, str)
            or not sync.HEX64_RE.fullmatch(event_id)
            or not sync._positive_int(created_at)
            or not isinstance(content, str)
            or not _exact_channel(event, self.channel_id)
        ):
            raise RouteError("Bridge-authored route scan event has an invalid envelope")
        if event.get("kind") == 40003 and sync.edit_target(event) is None:
            raise RouteError("Bridge-authored route edit has an invalid target")
        verify_nostr_event_signature(event, label="Bridge-authored route scan")
        return event_id, int(created_at), content

    def _route_id(self, content: str, routes: dict[str, dict[str, str]]) -> str | None:
        matches = [
            route_id for route_id, route in routes.items()
            if sync.matches_trigger_prefix(content, route["trigger_prefix"])
        ]
        if len(matches) > 1:
            raise RouteError("one Bridge fact matches multiple route rules")
        return matches[0] if matches else None

    def run(self, *, dry_run: bool = False) -> dict[str, Any]:
        mode_handle = self.service.acquire_mode("scan")
        try:
            return self._run_mode_locked(dry_run=dry_run)
        finally:
            self.service.release_mode(mode_handle)

    def _run_mode_locked(self, *, dry_run: bool = False) -> dict[str, Any]:
        handle = self._open_lock()
        try:
            cursor, already_seen = self._read_cursor()
            scan_started = int(self.clock())
            if scan_started < cursor:
                raise RouteError("local clock moved behind the durable route cursor")
            policy = self.service.load_policy(self.channel_id)
            since = max(self.scan_since, cursor - SCAN_OVERLAP_SECONDS)
            events = self.buzz.channel_messages(self.channel_id, since)
            if not isinstance(events, list):
                raise RouteError("Buzz route scan did not return a list")
            ordered = sorted(events, key=lambda event: (
                event.get("created_at", 0) if isinstance(event, dict) else 0,
                event.get("id", "") if isinstance(event, dict) else "",
            ))
            matched = sent = duplicate = would_send = 0
            recent: list[str] = []
            recent_boundary = scan_started - SCAN_OVERLAP_SECONDS
            for event in ordered:
                parsed = self._publisher_event(event)
                if parsed is None:
                    continue
                event_id, created_at, content = parsed
                if created_at >= recent_boundary:
                    recent.append(event_id)
                if event_id in already_seen:
                    continue
                route_id = self._route_id(content, policy["routes"])
                if route_id is None:
                    continue
                request = {"channel_id": self.channel_id, "message_id": event_id, "route_id": route_id}
                matched += 1
                if dry_run:
                    self.service.preview(request, policy=policy, source_event=event)
                    would_send += 1
                    continue
                result = self.service.route(request, policy=policy, source_event=event)
                if result["status"] == "sent":
                    sent += 1
                else:
                    duplicate += 1
            if dry_run:
                return {
                    "status": "ok", "dry_run": True, "scanned": len(events),
                    "matched": matched, "would_send": would_send, "cursor": cursor,
                    "canvas_event_id": policy["event_id"],
                }
            self._write_cursor(scan_started, recent)
            return {
                "status": "ok", "scanned": len(events), "matched": matched,
                "sent": sent, "duplicate": duplicate, "cursor": scan_started,
                "canvas_event_id": policy["event_id"],
            }
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def _json_response(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class RequestRateLimiter:
    def __init__(self, limit: int = RATE_LIMIT_REQUESTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
                 clock: Any = time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._seen: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self.clock()
        with self._lock:
            boundary = now - self.window_seconds
            while self._seen and self._seen[0] <= boundary:
                self._seen.popleft()
            if len(self._seen) >= self.limit:
                return False
            self._seen.append(now)
            return True


def audit_record(status: int, path: str, body: bytes) -> dict[str, Any]:
    record: dict[str, Any] = {
        "event": "route_reply_http",
        "status": status,
        "path": ENDPOINT if path == ENDPOINT else "other",
    }
    try:
        request = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return record
    if not isinstance(request, dict):
        return record
    for key, pattern in (
        ("channel_id", sync.UUID_RE), ("message_id", sync.HEX64_RE), ("route_id", ROUTE_ID_RE)
    ):
        value = request.get(key)
        if isinstance(value, str) and pattern.fullmatch(value):
            record[key] = value
    return record


def dispatch_http(path: str, headers: Any, body: bytes, secret: str,
                  service: RouteReplyService) -> tuple[int, bytes]:
    if path != ENDPOINT:
        return 404, _json_response({"status": "error", "error": "not found"})
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    supplied = lowered.get("x-route-secret", "")
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        return 401, _json_response({"status": "error", "error": "unauthorized"})
    if lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return 415, _json_response({"status": "error", "error": "application/json required"})
    if len(body) > MAX_BODY_BYTES:
        return 413, _json_response({"status": "error", "error": "request too large"})
    try:
        request = json.loads(body)
        result = service.route(request)
    except (json.JSONDecodeError, UnicodeDecodeError, RequestError):
        return 400, _json_response({"status": "error", "error": "invalid request"})
    except RouteError:
        return 409, _json_response({"status": "error", "error": "route rejected"})
    except Exception:  # noqa: BLE001 — never expose paths, CLI output or secrets over HTTP
        return 502, _json_response({"status": "error", "error": "route unavailable"})
    return 200, _json_response(result)


def handler_for(secret: str, service: RouteReplyService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gitlab-buzz-route-reply/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(CLIENT_READ_TIMEOUT_SECONDS)

        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            body = b""
            if not self.server.rate_limiter.allow():
                self.close_connection = True
                status, response = 429, _json_response({"status": "error", "error": "rate limit exceeded"})
            else:
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = -1
                if length < 0:
                    status, response = 400, _json_response({"status": "error", "error": "content length required"})
                elif length > MAX_BODY_BYTES:
                    status, response = 413, _json_response({"status": "error", "error": "request too large"})
                else:
                    try:
                        body = self.rfile.read(length)
                    except TimeoutError:
                        self.close_connection = True
                        status, response = 408, _json_response({"status": "error", "error": "request timeout"})
                    else:
                        status, response = dispatch_http(self.path, self.headers, body, secret, service)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass
            sys.stderr.write(json.dumps(audit_record(status, self.path, body), separators=(",", ":")) + "\n")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


class RouteHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: Any, handler: Any):
        self.rate_limiter = RequestRateLimiter()
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(server_address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def build_server(host: str, port: int, secret: str, service: RouteReplyService) -> RouteHTTPServer:
    return RouteHTTPServer((host, port), handler_for(secret, service))


def load_config(path: Path, *, mode: str | None = None) -> dict[str, Any]:
    try:
        config = sync.load_config(path)
    except sync.SyncError as exc:
        raise ConfigError(str(exc)) from None
    return validate_config(config, mode=mode)


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="route-reply config JSON")
    parser.add_argument("--state-dir", required=True, help="owner-only directory for durable idempotency records")
    parser.add_argument("--scan-once", action="store_true",
                        help="scan signed Desk-published GitLab facts locally once; no Workflow or listener")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --scan-once, validate matching facts without replying or advancing cursor")
    parser.add_argument("--listen", default="127.0.0.1:8787", help="loopback host:port; put HTTPS proxy in front")
    args = parser.parse_args(argv)
    if args.dry_run and not args.scan_once:
        raise ConfigError("--dry-run requires --scan-once")
    mode = "scan" if args.scan_once else "http"
    match = LISTEN_RE.fullmatch(args.listen)
    if not match or int(match.group(2)) > 65535:
        raise ConfigError("--listen must be a loopback host and valid TCP port")
    config = load_config(Path(args.config), mode=mode)
    env = dict(os.environ if env is None else env)
    buzz = RouteBuzzCli(config, env)
    service = RouteReplyService(
        config["channels"], config["sender_pubkey"], buzz, state_dir=Path(args.state_dir)
    )
    if args.scan_once:
        try:
            result = LocalRouteScanner(
                config, service, buzz, state_dir=Path(args.state_dir)
            ).run(dry_run=args.dry_run)
        except RouteError as exc:
            print(_json_response({"status": "error", "error": str(exc)}).decode().strip(), file=sys.stderr)
            return 1
        print(_json_response(result).decode().strip())
        return 0
    mode_handle = service.acquire_mode("http")
    try:
        secret_env = config["secret_env"]
        secret = env.get(secret_env, "")
        if len(secret) < 32:
            raise ConfigError(f"{secret_env} must contain at least 32 characters")
        server = build_server(match.group(1), int(match.group(2)), secret, service)
        print(_json_response({"status": "ready", "listen": args.listen}).decode().strip(), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        service.release_mode(mode_handle)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
