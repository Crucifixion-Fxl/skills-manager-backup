#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic admission gate for a repo maintainer agent (references/repo-maintainer-agent.md).

No LLM decides who may address the maintainer agent. Three subcommands:

- `refresh`: read the project's GitLab members (members/all, every page), keep humans with access_level >= 40,
  map each username to a Buzz pubkey through the shared people file, and atomically write a 0600 roster.
- `admit`: is this raw event pubkey a current Maintainer of this project? From the roster file (with TTL) or,
  with `--live`, from a fresh members fetch. Exit 0 admitted, 1 denied, 2 the answer is unavailable.
- `claim`: one-shot replay ledger. The first claim of an event id wins; a second is refused.

Fail closed: a missing, stale, malformed, foreign-project or insecure roster, an API error or an incomplete
page walk is exit 2 and is never read as "not a maintainer" nor as "allowed". The roster can only become
empty by a failed refresh, never wider. `refresh` prints only a count; `admit` prints a reason code and never
the list. The GitLab token is read from an environment variable, never argv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from gitlab_buzz_people_generate import (  # noqa: E402  (same tree; reuse the 0600 reader/writer and patterns)
    GITLAB_BOT_USERNAME_RE, HEX64_RE, USERNAME_RE, GenerateError, _atomic_write_json, _owner_only_json,
)

MAINTAINER_ACCESS = 40
ROSTER_VERSION = 1
DEFAULT_TTL_SECONDS = 1800
MAX_FUTURE_SKEW = timedelta(seconds=60)
PAGE_SIZE = 100
MAX_PAGES = 50
BASE_URL_RE = re.compile(r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?")


class InputError(ValueError):
    """The data itself is wrong (exit 1): nothing is written."""


class RosterError(RuntimeError):
    """The answer is unavailable (exit 2): callers must not treat it as denied or allowed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fetch_members(base_url: str, token: str, project_id: int,
                  opener: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, Any]]:
    if not BASE_URL_RE.fullmatch(base_url):
        raise InputError("base-url must be exactly https://host[:port]")
    rows: list[dict[str, Any]] = []
    page = 1
    for _ in range(MAX_PAGES):
        url = f"{base_url}/api/v4/projects/{project_id}/members/all?per_page={PAGE_SIZE}&page={page}"
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
        try:
            with opener(request, timeout=30) as response:
                if response.status != 200:
                    raise RosterError("members_api_status")
                batch = json.loads(response.read().decode("utf-8") or "[]")
                next_page = response.headers.get("x-next-page", "")
        except RosterError:
            raise
        except (urllib.error.URLError, OSError, ValueError):
            raise RosterError("members_api_failed") from None
        if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
            raise RosterError("members_api_malformed")
        rows.extend(batch)
        if not next_page or not batch:
            return rows
        if not next_page.isdigit():
            raise RosterError("members_api_malformed")
        page = int(next_page)
    raise RosterError("members_api_incomplete")  # never act on a partial walk


def load_people(path: Path) -> dict[str, str]:
    try:
        people = _owner_only_json(path, "people file")
    except GenerateError:
        raise RosterError("people_unavailable") from None
    if not isinstance(people, dict):
        raise RosterError("people_unavailable")
    for username, pubkey in people.items():
        if not (isinstance(username, str) and USERNAME_RE.fullmatch(username)
                and isinstance(pubkey, str) and HEX64_RE.fullmatch(pubkey)):
            raise InputError("people file entries must be GitLab username -> 64 lowercase hex")
    return people


def build_roster(members: list[dict[str, Any]], people: Mapping[str, str], project_id: int,
                 now: datetime) -> dict[str, Any]:
    maintainers: dict[str, str] = {}
    for row in members:
        username, level = row.get("username"), row.get("access_level")
        if not (isinstance(username, str) and USERNAME_RE.fullmatch(username)
                and isinstance(level, int) and not isinstance(level, bool)):
            raise RosterError("members_api_malformed")
        if (level < MAINTAINER_ACCESS or row.get("state", "active") != "active" or row.get("bot") is True
                or GITLAB_BOT_USERNAME_RE.fullmatch(username)):
            continue
        pubkey = people.get(username)
        if pubkey is None:
            continue  # a Maintainer with no Buzz key cannot address the agent; the rest are unaffected
        maintainers[username] = pubkey
    if len(set(maintainers.values())) != len(maintainers):
        raise InputError("one pubkey maps to several maintainers")  # ambiguous identity: write nothing
    return {
        "version": ROSTER_VERSION, "project_id": project_id,
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "maintainers": [{"username": name, "pubkey": key} for name, key in sorted(maintainers.items())],
    }


def load_roster(path: Path, project_id: int, now: datetime, ttl_seconds: int) -> dict[str, Any]:
    try:
        roster = _owner_only_json(path, "roster")
    except GenerateError:
        raise RosterError("roster_unreadable") from None
    try:
        if roster["version"] != ROSTER_VERSION or not isinstance(roster["maintainers"], list):
            raise RosterError("roster_malformed")
        generated_at = datetime.fromisoformat(roster["generated_at"])
        if roster["project_id"] != project_id or isinstance(roster["project_id"], bool):
            raise RosterError("roster_project_mismatch")
        entries = [(item["username"], item["pubkey"]) for item in roster["maintainers"]]
    except RosterError:
        raise
    except (KeyError, TypeError, ValueError):
        raise RosterError("roster_malformed") from None
    if generated_at.tzinfo is None or not all(
            USERNAME_RE.fullmatch(str(name)) and HEX64_RE.fullmatch(str(key)) for name, key in entries):
        raise RosterError("roster_malformed")
    if len({key for _, key in entries}) != len(entries):
        raise RosterError("roster_malformed")
    if now - generated_at > timedelta(seconds=ttl_seconds) or generated_at - now > MAX_FUTURE_SKEW:
        raise RosterError("roster_stale")
    return roster


def decide(roster: Mapping[str, Any], pubkey: str) -> dict[str, Any]:
    if not HEX64_RE.fullmatch(pubkey):
        return {"admitted": False, "reason": "malformed_pubkey"}  # the raw event pubkey is lowercase hex
    for item in roster["maintainers"]:
        if item["pubkey"] == pubkey:
            return {"admitted": True, "reason": "maintainer", "username": item["username"]}
    return {"admitted": False, "reason": "not_maintainer"}


def claim_event(ledger_dir: Path, event_id: str, now: datetime) -> dict[str, Any]:
    if not HEX64_RE.fullmatch(event_id):
        raise InputError("event-id must be 64 lowercase hex")
    try:
        metadata = ledger_dir.lstat()
    except OSError:
        raise RosterError("ledger_unavailable") from None
    if (not ledger_dir.is_absolute() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077):
        raise RosterError("ledger_unavailable")
    try:
        fd = os.open(ledger_dir / event_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return {"claimed": False, "reason": "replay"}
    except OSError:
        raise RosterError("ledger_unavailable") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"claimed_at": now.astimezone(timezone.utc).isoformat(timespec="seconds")}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return {"claimed": True, "reason": "first_claim"}


def _token(env_name: str) -> str:
    token = os.environ.get(env_name, "")
    if not token or "\n" in token:
        raise RosterError("token_missing")
    return token


def main(argv: list[str] | None = None, *, opener: Callable[..., Any] = urllib.request.urlopen,
         now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project-id", type=int, required=True)
    source = argparse.ArgumentParser(add_help=False)
    source.add_argument("--base-url", help="https://host[:port] of GitLab")
    source.add_argument("--people-file", type=Path, help="absolute 0600 JSON {gitlab username: 64hex pubkey}")
    source.add_argument("--token-env", default="GITLAB_TOKEN", help="env var holding a read token")
    refresh = sub.add_parser("refresh", parents=[common, source], help="write the roster file")
    refresh.add_argument("--out", type=Path, required=True, help="absolute roster path (written 0600)")
    admit = sub.add_parser("admit", parents=[common, source], help="exit 0 admitted, 1 denied, 2 unavailable")
    admit.add_argument("--pubkey", required=True, help="the raw event pubkey, never a display name")
    where = admit.add_mutually_exclusive_group(required=True)
    where.add_argument("--roster", type=Path)
    where.add_argument("--live", action="store_true", help="re-read members now instead of the roster file")
    admit.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, help="roster max age in seconds")
    claim = sub.add_parser("claim", help="one-shot replay ledger: exit 0 first claim, 1 replay, 2 unavailable")
    claim.add_argument("--ledger-dir", type=Path, required=True, help="absolute 0700 directory")
    claim.add_argument("--event-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "claim":
            result = claim_event(args.ledger_dir, args.event_id, now())
            print(json.dumps(result))
            return 0 if result["claimed"] else 1
        needs_fetch = args.command == "refresh" or args.live
        if needs_fetch:
            if not (args.base_url and args.people_file):
                raise InputError("--base-url and --people-file are required to read GitLab")
            roster = build_roster(fetch_members(args.base_url, _token(args.token_env), args.project_id, opener),
                                  load_people(args.people_file), args.project_id, now())
        if args.command == "refresh":
            if not args.out.is_absolute():
                raise InputError("--out must be absolute")
            _atomic_write_json(args.out, roster)
            print(json.dumps({"written": True, "maintainers": len(roster["maintainers"])}))
            return 0
        if not needs_fetch:
            roster = load_roster(args.roster, args.project_id, now(), args.ttl)
        result = decide(roster, args.pubkey)
        print(json.dumps(result))
        return 0 if result["admitted"] else 1
    except InputError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except RosterError as exc:
        print(json.dumps({"admitted": False, "unavailable": True, "reason": exc.reason}))
        return 2
    except OSError as exc:
        print(json.dumps({"admitted": False, "unavailable": True, "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
