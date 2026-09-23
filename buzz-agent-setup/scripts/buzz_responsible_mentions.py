#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Resolve structured responsibility candidates to verified Buzz human members.

This helper deliberately does not read message prose.  Callers first extract
owners from approved GitLab fields, then pass
those structured candidates together with fresh profile/member snapshots.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


HEX64_RE = re.compile(r"[0-9a-f]{64}")
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
ALLOWED_SOURCES = frozenset(
    {
        "gitlab.assignees",
        "gitlab.reviewers",
        "gitlab.maintainers",
        "gitlab.author",
        "gitlab.milestone_owner",
        "gitlab.pipeline_user",
        "gitlab.deployer",
        "people.person",
    }
)
# Sources only a caller that extracts them deterministically may opt into (the
# GitLab sync's exact `@username` tokens, ADR-0018). Agents reach this helper
# through the CLI and the send gate, neither of which passes an opt-in.
OPT_IN_SOURCES = frozenset({"gitlab.comment_mention"})
# Channel roles that are people who can act on a matter (skills#136). `guest` is a restricted role and `bot` is an
# Agent, so neither is notified. The GitLab sync reads this same set, so its @ and `unmapped:` line follow it.
HUMAN_ROLES = frozenset({"owner", "admin", "member"})
RESERVED_NAMES = frozenset({"all", "everyone", "here", "channel"})


def _require_pubkey(value: Any, field: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be 64 lowercase hex")
    return value


def _normalize_candidates(candidates: Any, extra_sources: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    if not extra_sources <= OPT_IN_SOURCES:
        raise ValueError("extra_sources may only opt into the documented opt-in sources")
    allowed = ALLOWED_SOURCES | extra_sources
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    normalized: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {"username", "source"}:
            raise ValueError("candidate must contain only username and source")
        username = item.get("username")
        source = item.get("source")
        if not isinstance(source, str) or source not in allowed:
            raise ValueError("candidate source is not an approved structured field")
        if (
            not isinstance(username, str)
            or USERNAME_RE.fullmatch(username) is None
            or username.casefold() in RESERVED_NAMES
        ):
            raise ValueError("candidate username is invalid or reserved")
        normalized.append({"username": username, "source": source})
    return normalized


def resolve_mentions(
    candidates: Any,
    *,
    aliases: Any,
    profiles: Any,
    members: Any,
    limit: int = 3,
    extra_sources: frozenset[str] = frozenset(),
) -> dict[str, list[dict[str, str]]]:
    """Return verified mentions and fail-closed unresolved candidates.

    Profile matching is exact and case-sensitive.  An `aliases` entry (the
    GitLab sync's configured people map) may point directly to a pubkey, but it
    never bypasses fresh Channel membership or the human-role check.  Output preserves first-seen order,
    deduplicates by pubkey, and enforces the attention budget.
    """

    normalized = _normalize_candidates(candidates, extra_sources)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 3:
        raise ValueError("limit must be an integer from 1 to 3")
    if not isinstance(aliases, dict):
        raise ValueError("aliases must be an object")
    alias_map: dict[str, str] = {}
    for username, pubkey in aliases.items():
        if not isinstance(username, str) or USERNAME_RE.fullmatch(username) is None:
            raise ValueError("alias username is invalid")
        alias_map[username] = _require_pubkey(pubkey, "alias pubkey")

    if not isinstance(profiles, list):
        raise ValueError("profiles must be a list")
    profile_map: dict[str, list[str]] = {}
    for item in profiles:
        if not isinstance(item, dict):
            raise ValueError("profile must be an object")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError("profile name must be a string")
        pubkey = _require_pubkey(item.get("pubkey"), "profile pubkey")
        profile_map.setdefault(name, []).append(pubkey)

    if not isinstance(members, list):
        raise ValueError("members must be a list")
    member_roles: dict[str, str] = {}
    for item in members:
        if not isinstance(item, dict):
            raise ValueError("member must be an object")
        pubkey = _require_pubkey(item.get("pubkey"), "member pubkey")
        role = item.get("role")
        if not isinstance(role, str):
            raise ValueError("member role must be a string")
        if pubkey in member_roles:
            raise ValueError("duplicate Channel member pubkey")
        member_roles[pubkey] = role

    mentions: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    seen_pubkeys: set[str] = set()

    for item in normalized:
        username = item["username"]
        source = item["source"]
        if username in alias_map:
            pubkey = alias_map[username]
        else:
            matches = profile_map.get(username, [])
            unique_matches = list(dict.fromkeys(matches))
            if not unique_matches:
                unresolved.append({**item, "reason": "profile_not_found"})
                continue
            if len(unique_matches) != 1:
                unresolved.append({**item, "reason": "ambiguous_profile"})
                continue
            pubkey = unique_matches[0]

        role = member_roles.get(pubkey)
        if role is None:
            unresolved.append({**item, "reason": "not_channel_member"})
            continue
        if role not in HUMAN_ROLES:
            unresolved.append({**item, "reason": "not_human_member"})
            continue
        if pubkey in seen_pubkeys:
            continue
        if len(mentions) >= limit:
            unresolved.append({**item, "reason": "attention_budget"})
            continue
        mentions.append({**item, "pubkey": pubkey})
        seen_pubkeys.add(pubkey)

    return {"mentions": mentions, "unresolved": unresolved}


def _load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve structured GitLab owners to Buzz human p tags"
    )
    parser.add_argument("--input", required=True, help="JSON input path, or - for stdin")
    args = parser.parse_args(argv)
    try:
        data = _load_json(args.input)
        if not isinstance(data, dict):
            raise ValueError("input must be an object")
        if set(data) - {"candidates", "aliases", "profiles", "members", "limit"}:
            raise ValueError("input contains unknown keys")
        result = resolve_mentions(
            data.get("candidates"),
            aliases=data.get("aliases", {}),
            profiles=data.get("profiles", []),
            members=data.get("members", []),
            limit=data.get("limit", 3),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
