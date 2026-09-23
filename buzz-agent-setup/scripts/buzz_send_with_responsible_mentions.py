#!/usr/bin/env python3
"""Resolve structured owners with fresh Buzz data, send, and strictly read back.

The request is JSON data, never shell arguments.  It identifies GitLab fields;
it cannot provide mention pubkeys.  A GitLab username becomes a pubkey only
through the owner-only `people_file` (GitLab username -> pubkey, the same map
the GitLab sync reads and gitlab_buzz_people_generate.py writes), then the live
Channel member list must show a human.  Static trust anchors and paths live in the owner-controlled config
selected by the Desk runtime environment.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
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
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import buzz_responsible_mentions as resolver  # noqa: E402
import gitlab_buzz_sync as sync  # noqa: E402


CONFIG_ENV = "BUZZ_RESPONSIBLE_CONFIG"
CONFIG_KEYS = frozenset({"version", "sender_pubkey", "state_dir", "gitlab", "channels", "buzz", "people_file"})
GITLAB_KEYS = frozenset({"base_url", "token_env", "projects"})
REQUEST_KEYS = frozenset({"version", "channel_id", "reply_to", "content", "sources"})
GITLAB_SOURCE_KEYS = frozenset({"kind", "project_id", "object", "iid", "field"})
PERSON_SOURCE_KEYS = frozenset({"kind", "username"})
GITLAB_FIELDS = {
    "issue": frozenset({"assignees", "author", "milestone_owner"}),
    "mr": frozenset({"reviewers", "assignees", "author", "milestone_owner"}),
}
SOURCE_NAMES = {
    "reviewers": "gitlab.reviewers",
    "assignees": "gitlab.assignees",
    "author": "gitlab.author",
    "milestone_owner": "gitlab.milestone_owner",
}
MAX_CONTENT_BYTES = 60_000
MAX_SOURCES = 32


class SendError(RuntimeError):
    """The responsible-person boundary could not prove a safe send."""


def _strict(value: dict[str, Any], keys: frozenset[str], label: str) -> None:
    if set(value) != keys:
        raise SendError(f"{label} keys do not match the contract")


def _absolute_path(raw: Any, label: str, *, may_not_exist: bool = False) -> Path:
    if not isinstance(raw, str) or not raw:
        raise SendError(f"{label} must be an absolute path")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise SendError(f"{label} must be an absolute normalized path")
    if not may_not_exist or path.exists() or path.is_symlink():
        try:
            if path.resolve(strict=True) != path:
                raise SendError(f"{label} must not contain symlinks")
        except OSError as exc:
            raise SendError(f"cannot resolve {label}: {type(exc).__name__}") from None
    return path


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = sync.load_config(Path(path))
    except sync.SyncError as exc:
        raise SendError(str(exc)) from None
    if set(config) != CONFIG_KEYS or config.get("version") != 2:
        raise SendError("config keys or version do not match the contract")
    sender = config.get("sender_pubkey")
    if not isinstance(sender, str) or not sync.HEX64_RE.fullmatch(sender):
        raise SendError("sender_pubkey must be 64 lowercase hex")
    config["state_dir"] = str(_absolute_path(config.get("state_dir"), "state_dir", may_not_exist=True))

    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise SendError("gitlab config is required")
    _strict(gitlab, GITLAB_KEYS, "gitlab config")
    try:
        sync._validate_origin(gitlab.get("base_url"), "gitlab.base_url", {"https"}, {"http"})
    except sync.SyncError as exc:
        raise SendError(str(exc)) from None
    token_env = gitlab.get("token_env")
    if (
        not isinstance(token_env, str)
        or not sync.TOKEN_ENV_RE.fullmatch(token_env)
        or token_env.startswith("BUZZ_")
        or token_env in sync.CHILD_ENV_KEYS
    ):
        raise SendError("gitlab.token_env must be an uppercase non-BUZZ env name")
    projects = gitlab.get("projects")
    if (
        not isinstance(projects, list)
        or not projects
        or not all(sync._positive_int(item) for item in projects)
        or len(set(projects)) != len(projects)
    ):
        raise SendError("gitlab.projects must be a non-empty unique project list")

    channels = config.get("channels")
    if (
        not isinstance(channels, list)
        or not channels
        or not all(isinstance(item, str) and sync.UUID_RE.fullmatch(item) for item in channels)
        or len(set(channels)) != len(channels)
    ):
        raise SendError("channels must be a non-empty unique list of lowercase UUIDs")
    people_file = config.get("people_file")
    if not isinstance(people_file, str) or not os.path.isabs(people_file):
        raise SendError("people_file must be an absolute path")
    buzz = config.get("buzz")
    if not isinstance(buzz, dict):
        raise SendError("buzz config is required")
    try:
        sync._reject_unknown_keys(buzz, sync.BUZZ_CONFIG_KEYS, "buzz")
        sync.validate_buzz_cli_path(buzz.get("cli_path"), buzz.get("cli_sha256"))
    except sync.SyncError as exc:
        raise SendError(str(exc)) from None
    return config


def _load_request(path: str | Path) -> dict[str, Any]:
    try:
        if str(path) == "-":
            value = json.load(sys.stdin)
        else:
            input_path = Path(path)
            if input_path.is_symlink():
                raise SendError("input must not be a symlink")
            value = json.loads(input_path.read_text(encoding="utf-8"))
    except SendError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SendError(f"cannot read input: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise SendError("input must be an object")
    _strict(value, REQUEST_KEYS, "input")
    if value.get("version") != 1:
        raise SendError("input version must be 1")
    if not isinstance(value.get("channel_id"), str) or not sync.UUID_RE.fullmatch(value["channel_id"]):
        raise SendError("channel_id must be a lowercase UUID")
    if not isinstance(value.get("reply_to"), str) or not sync.HEX64_RE.fullmatch(value["reply_to"]):
        raise SendError("reply_to must be a 64-hex Thread root")
    content = value.get("content")
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content.encode("utf-8")) > MAX_CONTENT_BYTES
        or "@" in content
        or re.search(r"(?i)nostr:", content)
    ):
        raise SendError("content must be bounded text without free-text mentions")
    sources = value.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise SendError("sources must contain 1..32 structured locators")
    for source in sources:
        if not isinstance(source, dict):
            raise SendError("each source locator must be an object")
        kind = source.get("kind")
        if kind == "gitlab":
            _strict(source, GITLAB_SOURCE_KEYS, "GitLab source locator")
            if source.get("object") not in GITLAB_FIELDS:
                raise SendError("GitLab source object must be issue or mr")
            if source.get("field") not in GITLAB_FIELDS[source["object"]]:
                raise SendError("GitLab source field is not an approved responsibility field")
            if not sync._positive_int(source.get("project_id")) or not sync._positive_int(source.get("iid")):
                raise SendError("GitLab source ids must be positive integers")
        elif kind == "person":
            _strict(source, PERSON_SOURCE_KEYS, "person source locator")
            username = source.get("username")
            if (
                not isinstance(username, str)
                or resolver.USERNAME_RE.fullmatch(username) is None
                or username.casefold() in resolver.RESERVED_NAMES
            ):
                raise SendError("person username is invalid or reserved")
        else:
            raise SendError("source kind must be gitlab or person")
    return value


class BuzzClient:
    def __init__(self, config: dict[str, Any], env: dict[str, str], *, runner: Any, sleeper: Any):
        try:
            sync.validate_publisher_identity(env, config["sender_pubkey"])
            sync.validate_relay_url(env.get("BUZZ_RELAY_URL"))
            self.env = sync.child_env(env, config["gitlab"]["token_env"])
        except sync.SyncError as exc:
            raise SendError(str(exc)) from None
        self.cli = config["buzz"]["cli_path"]
        self.sender = config["sender_pubkey"]
        self.runner = runner
        self.sleeper = sleeper

    def command(self, args: list[str], content: str | None = None) -> Any:
        try:
            result = self.runner(
                [self.cli, *args], input=content, capture_output=True, text=True,
                timeout=45, check=False, env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SendError(f"Buzz CLI could not run: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise SendError("Buzz CLI command failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise SendError("Buzz CLI returned non-JSON output") from None

    def members(self, channel_id: str) -> list[dict[str, str]]:
        value = self.command(["channels", "members", "--channel", channel_id])
        if not isinstance(value, list) or not value:
            raise SendError("Buzz members response must be a non-empty list")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("pubkey"), str)
                or not sync.HEX64_RE.fullmatch(item["pubkey"])
                or not isinstance(item.get("role"), str)
                or item["pubkey"] in seen
            ):
                raise SendError("Buzz members response contains an invalid or duplicate member")
            seen.add(item["pubkey"])
            result.append({"pubkey": item["pubkey"], "role": item["role"]})
        return result

    def thread(self, channel_id: str, root: str) -> list[dict[str, Any]]:
        return sync._collect_events(self.command([
            "messages", "thread", "--channel", channel_id, "--event", root, "--limit", "500",
        ]))

    def send(self, channel_id: str, root: str, content: str, mentions: list[str]) -> str:
        value = self.command(sync.build_send_args(self.cli, channel_id, root, mentions)[1:], content)
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if not isinstance(event_id, str) or not sync.HEX64_RE.fullmatch(event_id) or value.get("accepted") is False:
            raise SendError("Buzz rejected the responsible-person message")
        return event_id

    def verify(
        self, channel_id: str, root: str, event_id: str, content: str, mentions: list[str]
    ) -> None:
        for attempt in range(sync.READBACK_ATTEMPTS):
            event = next((item for item in self.thread(channel_id, root) if item.get("id") == event_id), None)
            if event is not None:
                break
            if attempt < sync.READBACK_ATTEMPTS - 1:
                self.sleeper(0.5)
        else:
            raise SendError("Buzz accepted the message but strict readback is pending")
        p_tags = [str(tag[1]) for tag in sync._tag_values(event, "p") if len(tag) > 1]
        if (
            event.get("kind") != 9
            or event.get("pubkey") != self.sender
            or event.get("content") != content
            or [tag[:2] for tag in sync._tag_values(event, "h")] != [["h", channel_id]]
            or [tag[:4] for tag in sync._tag_values(event, "e")] != [["e", root, "", "reply"]]
            or len(p_tags) != len(set(p_tags))
            or set(p_tags) != set(mentions)
        ):
            raise SendError("Buzz readback does not match author, Channel, Thread or complete p tags")


class StructuredSources:
    def __init__(self, config: dict[str, Any], channel_id: str, env: dict[str, str], buzz: BuzzClient):
        self.config = config
        self.channel_id = channel_id
        self.token = env.get(config["gitlab"]["token_env"], "")
        try:
            sync.validate_token(self.token, config["gitlab"]["token_env"])
        except sync.SyncError as exc:
            raise SendError(str(exc)) from None
        self.buzz = buzz
        self.opener = urllib.request.build_opener(sync.NoRedirectHandler())
        self._objects: dict[tuple[int, str, int], dict[str, Any]] = {}

    def _gitlab_object(self, project_id: int, object_kind: str, iid: int) -> dict[str, Any]:
        if project_id not in self.config["gitlab"]["projects"]:
            raise SendError("GitLab source project is outside the configured allowlist")
        key = (project_id, object_kind, iid)
        if key in self._objects:
            return self._objects[key]
        resource = "issues" if object_kind == "issue" else "merge_requests"
        base = self.config["gitlab"]["base_url"].rstrip("/")
        url = f"{base}/api/v4/projects/{project_id}/{resource}/{iid}"
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token, "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=sync.GITLAB_REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read(sync.GITLAB_RESPONSE_MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SendError(f"GitLab structured source read failed: {type(exc).__name__}") from None
        if len(raw) > sync.GITLAB_RESPONSE_MAX_BYTES:
            raise SendError("GitLab structured source response is too large")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SendError("GitLab structured source returned non-JSON") from None
        if not isinstance(value, dict) or value.get("iid") != iid:
            raise SendError("GitLab structured source identity does not match the locator")
        self._objects[key] = value
        return value

    @staticmethod
    def _usernames(value: dict[str, Any], field: str) -> list[str]:
        if field in {"reviewers", "assignees"}:
            users = value.get(field)
            if not isinstance(users, list):
                raise SendError(f"GitLab {field} must be a list")
        elif field == "author":
            users = [value.get("author")] if value.get("author") is not None else []
        else:
            milestone = value.get("milestone")
            users = [milestone.get("owner")] if isinstance(milestone, dict) and milestone.get("owner") else []
        names: list[str] = []
        for user in users:
            username = user.get("username") if isinstance(user, dict) else None
            if not isinstance(username, str):
                raise SendError(f"GitLab {field} contains an invalid username")
            names.append(username)
        return names

    def candidates(self, locators: list[dict[str, Any]]) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        for locator in locators:
            if locator["kind"] == "person":
                # A GitLab username with no GitLab object behind it (a standing audience seat). It is only a lookup
                # key into the owner-only people_file; the pubkey never comes from the request.
                candidates.append({"username": locator["username"], "source": "people.person"})
                continue
            value = self._gitlab_object(locator["project_id"], locator["object"], locator["iid"])
            candidates.extend(
                {"username": name, "source": SOURCE_NAMES[locator["field"]]}
                for name in self._usernames(value, locator["field"])
            )
        return candidates


class DurableSend:
    def __init__(self, config: dict[str, Any], request: dict[str, Any], buzz: BuzzClient):
        self.config = config
        self.request = request
        self.buzz = buzz
        self.state_dir = Path(config["state_dir"])
        try:
            sync.prepare_private_dir(self.state_dir, "responsible-send state directory")
        except sync.SyncError as exc:
            raise SendError(str(exc)) from None
        scope = hashlib.sha256(
            f"{request['channel_id']}|{config['sender_pubkey']}".encode()
        ).hexdigest()[:24]
        self.scope_dir = self.state_dir / f"responsible-send-{scope}"
        try:
            sync.prepare_private_dir(self.scope_dir, "responsible-send scope directory")
        except sync.SyncError as exc:
            raise SendError(str(exc)) from None
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.operation_id = hashlib.sha256(canonical.encode()).hexdigest()
        self.path = self.scope_dir / f"{self.operation_id}.json"
        self.lock_path = self.scope_dir / "send.lock"

    def open_lock(self):
        try:
            fd = os.open(
                self.lock_path,
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
                raise SendError("responsible-send lock must be owner-only")
            handle = os.fdopen(fd, "r+")
            fcntl.flock(handle, fcntl.LOCK_EX)
            return handle
        except OSError as exc:
            raise SendError(f"cannot lock responsible send: {type(exc).__name__}") from None

    def read(self) -> dict[str, Any] | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SendError(f"cannot read responsible-send state: {type(exc).__name__}") from None
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise SendError("responsible-send state must be owner-only")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                value = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SendError(f"cannot parse responsible-send state: {type(exc).__name__}") from None
        finally:
            if fd >= 0:
                os.close(fd)
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("operation_id") != self.operation_id
            or value.get("status") not in {"PENDING", "ACKED"}
            or not isinstance(value.get("content"), str)
            or not isinstance(value.get("mentions"), list)
            or not all(isinstance(item, str) and sync.HEX64_RE.fullmatch(item) for item in value["mentions"])
            or ("event_id" in value and not sync.HEX64_RE.fullmatch(str(value["event_id"])))
        ):
            raise SendError("responsible-send state does not match this operation")
        return value

    def write(self, value: dict[str, Any]) -> None:
        try:
            sync.atomic_write_json(self.path, value)
        except OSError as exc:
            raise SendError(f"cannot persist responsible-send state: {type(exc).__name__}") from None

    def _matching(self, content: str, mentions: list[str]) -> list[dict[str, Any]]:
        matches = []
        for event in self.buzz.thread(self.request["channel_id"], self.request["reply_to"]):
            p_tags = [str(tag[1]) for tag in sync._tag_values(event, "p") if len(tag) > 1]
            if (
                event.get("pubkey") == self.config["sender_pubkey"]
                and event.get("content") == content
                and [tag[:2] for tag in sync._tag_values(event, "h")] == [["h", self.request["channel_id"]]]
                and [tag[:4] for tag in sync._tag_values(event, "e")]
                == [["e", self.request["reply_to"], "", "reply"]]
                and len(p_tags) == len(set(p_tags))
                and set(p_tags) == set(mentions)
                and isinstance(event.get("id"), str)
                and sync.HEX64_RE.fullmatch(event["id"])
            ):
                matches.append(event)
        return matches

    def resume(self, record: dict[str, Any]) -> dict[str, Any]:
        event_id = record.get("event_id")
        if isinstance(event_id, str):
            self.buzz.verify(
                self.request["channel_id"], self.request["reply_to"], event_id,
                record["content"], record["mentions"],
            )
        else:
            matches = self._matching(record["content"], record["mentions"])
            if len(matches) != 1:
                raise SendError("responsible send is pending after an ambiguous send; refusing to resend")
            event_id = matches[0]["id"]
        self.write({**record, "status": "ACKED", "event_id": event_id})
        return {"status": "duplicate", "event_id": event_id, "mentions": record["mentions"]}

    def send(self, content: str, mentions: list[str]) -> dict[str, Any]:
        record = {
            "version": 1,
            "operation_id": self.operation_id,
            "status": "PENDING",
            "content": content,
            "mentions": mentions,
        }
        self.write(record)
        event_id = self.buzz.send(
            self.request["channel_id"], self.request["reply_to"], content, mentions
        )
        record["event_id"] = event_id
        self.write(record)
        self.buzz.verify(
            self.request["channel_id"], self.request["reply_to"], event_id, content, mentions
        )
        self.write({**record, "status": "ACKED"})
        return {"status": "sent", "event_id": event_id, "mentions": mentions}


def execute(
    config_path: Path,
    input_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    runner: Any = subprocess.run,
    source_reader: Any = None,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    runtime_env = dict(os.environ if env is None else env)
    request = _load_request(input_path)
    config = load_config(Path(config_path))
    if request["channel_id"] not in config["channels"]:
        raise SendError("request channel is outside the configured allowlist")
    buzz = BuzzClient(config, runtime_env, runner=runner, sleeper=sleeper)
    durable = DurableSend(config, request, buzz)
    handle = durable.open_lock()
    try:
        recorded = durable.read()
        if recorded is not None:
            return durable.resume(recorded)
        sources = source_reader or StructuredSources(config, request["channel_id"], runtime_env, buzz)
        candidates = sources.candidates(request["sources"])
        if not candidates:
            raise SendError("structured source locators resolved no responsibility candidate")
        members = buzz.members(request["channel_id"])
        try:
            # Read fresh on every send: a swapped or loosened file is caught here (fail closed, never cached).
            people = sync.effective_people(
                {"people_file": config["people_file"], "publisher_pubkey": config["sender_pubkey"]}
            )
            resolved = resolver.resolve_mentions(
                candidates, aliases=people, profiles=[], members=members, limit=3
            )
        except (sync.SyncError, ValueError) as exc:
            raise SendError(str(exc)) from None
        mentions = [item["pubkey"] for item in resolved["mentions"]]
        if not mentions:
            raise SendError("no verified human responsible person resolved; refusing to send")
        unresolved = [f"{item['username']}({item['reason']})" for item in resolved["unresolved"]]
        lines = [request["content"]]
        if unresolved:
            lines.append("未通知：" + ", ".join(unresolved))
        lines.append(f"[buzz-responsible:v1][operation:{durable.operation_id}]")
        content = "\n".join(lines)
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise SendError("rendered responsible message exceeds the payload budget")
        result = durable.send(content, mentions)
        result["unresolved"] = resolved["unresolved"]
        return result
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    runner: Any = subprocess.run,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="structured request JSON path, or - for stdin")
    args = parser.parse_args(argv)
    runtime_env = dict(os.environ if env is None else env)
    config_path = runtime_env.get(CONFIG_ENV)
    if not config_path:
        result = {"status": "error", "error": f"{CONFIG_ENV} is required"}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2
    try:
        result = execute(Path(config_path), args.input, env=runtime_env, runner=runner)
    except SendError as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") in {"sent", "duplicate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
