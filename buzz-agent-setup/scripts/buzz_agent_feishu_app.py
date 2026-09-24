#!/usr/bin/env python3
"""Publish an agent's Feishu app id in the agent's kind:30177 (ADR-0019, engineering/skills#147).

The agent's owner runs this once the agent's Feishu app exists. It reads the agent's current kind:30177 head (signed by
the owner, `d` = the agent) from the relay, merges `"feishu": {"app_id": ...}` into its content and keeps every other
field and tag as they are, signs the new event with the owner's key, publishes it (POST /events) and reads it back
(POST /query): the head must be the event just sent. The group syncs of every other operator then find the agent's bot
(buzz_feishu_group_sync.py, "别人的 agent 的飞书应用（kind:30177 目录）").

- It never creates an agent's policy: no owner-signed kind:30177 for the agent means exit 2 (minting is mint-agent.py's job).
- `--mirror` instead declares a group sync's mirror identity (ADR-0020): `"feishu": {"mirror": true}` in its kind:30177, so
  other hosts' group syncs do not relay it as an agent and the join-request script takes the answers it carries. A mirror
  has no policy of its own yet, so this one may be created (name from its kind 0 or --name, parallelism 1, respond_to
  "owner-only": the most cautious value Buzz Desktop reads; nobody gets an answer from a mirror anyway). Only the owner the
  mirror's own profile names (NIP-OA) can declare it: anyone else's declaration would be ignored by every reader.
- The same app id (or the flag) already there means nothing is sent.
- The old event is saved to --backup-dir (0700, one 0600 file per event) before anything is sent; --dry-run only reads.
- The owner's key is read from a 0600 env file (BUZZ_PRIVATE_KEY, hex or nsec) and used in this process only: it is
  never in argv, a child's environment or the output. The relay address is --relay-url or the env file's BUZZ_RELAY_URL.

Exit codes: 0 published / unchanged / would_publish; 1 the relay refused, could not be reached, or the readback did not
match; 2 bad input or nothing to change (no policy, a content that is not a JSON object, an env file that is not 0600).
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import pwd
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buzz_feishu_group_sync as fgs  # noqa: E402 -- the sibling that reads this directory; shares its signer and transport

TIMEOUT = 15.0
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
LOCK_DIR = Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".local" / "state" / "buzz-agent-feishu-app" / "locks"


class Refused(Exception):
    """Bad input or nothing this tool may change: exit 2."""


class Failed(Exception):
    """The relay refused, could not be reached, or did not show what was sent: exit 1."""


def _agent_hex(value: str) -> str:
    if fgs.HEX64_RE.fullmatch(value or ""):
        return value
    if (value or "").startswith("npub1"):
        try:
            return fgs.sync.nk.bech32_decode(value, "npub").hex()
        except Exception:  # a malformed npub: whatever the decoder raised
            pass
    raise Refused("--agent must be the agent's pubkey as 64 lower-case hex digits or an npub")


def _relay_url(args: argparse.Namespace) -> str:
    if args.relay_url:
        url = args.relay_url
    else:
        url = fgs.read_env_file(Path(args.owner_env)).get("BUZZ_RELAY_URL", "")
    fgs.relay_query_url(url)  # the same check the group sync applies
    return url.rstrip("/")


def _post(http: Any, key: str, url: str, payload: Any, now: datetime) -> tuple[int, bytes]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    headers = {"Authorization": fgs.nip98_header(key, "POST", url, now, body=body), "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        return http(url, headers, TIMEOUT, body=body)
    except (OSError, fgs.GroupSyncError):
        raise Failed(f"the relay could not be reached ({url})") from None


def _head(http: Any, key: str, relay: str, owner: str, agent: str, now: datetime) -> Mapping[str, Any] | None:
    status, answer = _post(http, key, f"{relay}/query",
                           [{"kinds": [fgs.KIND_MANAGED_AGENT], "authors": [owner], "#d": [agent]}], now)
    if status != 200:
        raise Failed(f"the relay answered the query with HTTP {status}")
    try:
        events = json.loads(answer)
    except ValueError:
        raise Failed("the relay's query answer is not JSON") from None
    if not isinstance(events, list):
        raise Failed("the relay's query answer is not a list of events")
    head = None
    for event in events:
        if (fgs._nip01_event_verified(event) and event.get("kind") == fgs.KIND_MANAGED_AGENT and event["pubkey"] == owner
                and (fgs._first_tag(event, "d") or [None, None])[1] == agent and fgs._newest(event, head)):
            head = event
    return head


def _sign(key: str, kind: int, tags: list[Any], content: str, created_at: int) -> dict[str, Any]:
    pubkey = fgs._signer_pubkey(key)
    serial = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(serial.encode()).hexdigest()
    sig = fgs.sync.nk.schnorr_sign(bytes.fromhex(event_id), bytes.fromhex(key), secrets.token_bytes(32)).hex()
    return {"id": event_id, "pubkey": pubkey, "created_at": created_at, "kind": kind, "tags": tags, "content": content,
            "sig": sig}


def _backup(directory: Path, agent: str, event: Mapping[str, Any]) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{agent[:16]}-30177-{event['id'][:16]}.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return path  # the same event was saved by an earlier run
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(event, fh, ensure_ascii=False)
    return path


def _profile(http: Any, key: str, relay: str, identity: str, now: datetime) -> tuple[str, str] | None:
    """(the owner its NIP-OA auth tag names, its name) from the identity's latest kind 0, or None when it has none."""
    status, answer = _post(http, key, f"{relay}/query", [{"kinds": [0], "authors": [identity]}], now)
    if status != 200:
        raise Failed(f"the relay answered the profile query with HTTP {status}")
    try:
        events = json.loads(answer)
    except ValueError:
        raise Failed("the relay's profile answer is not JSON") from None
    latest = None
    for event in events if isinstance(events, list) else []:
        if fgs._nip01_event_verified(event) and event.get("kind") == 0 and event["pubkey"] == identity \
                and fgs._newest(event, latest):
            latest = event
    if latest is None:
        return None
    auth = fgs._first_tag(latest, "auth")
    owner = auth[1] if auth is not None and isinstance(auth[1], str) else ""
    try:
        body = json.loads(latest["content"])
    except ValueError:
        body = {}
    name = (body.get("display_name") or body.get("name")) if isinstance(body, dict) else None
    return owner, name if isinstance(name, str) else ""


def run(args: argparse.Namespace, http: Any, now: datetime, *, owner_key: str | None = None) -> dict[str, Any]:
    agent = _agent_hex(args.agent)
    if bool(args.mirror) == bool(args.app_id):
        raise Refused("give exactly one of --app-id (an agent's Feishu app) and --mirror (a group sync's mirror identity)")
    if not args.mirror and not fgs.APP_ID_RE.fullmatch(args.app_id):
        raise Refused("--app-id does not look like a Feishu app id (cli_...)")
    try:
        key = owner_key or fgs.load_signer_key(Path(args.owner_env))
        relay = _relay_url(args)
    except fgs.GroupSyncError as exc:
        raise Refused(str(exc)) from None
    owner = fgs._signer_pubkey(key)
    if args.mirror:
        profile = _profile(http, key, relay, agent, now)
        if profile is None or profile[0] != owner:
            raise Refused("the mirror's profile does not name this key's owner (NIP-OA): nobody would take its declaration")
    head = _head(http, key, relay, owner, agent, now)
    if head is None and not args.mirror:
        raise Refused("the relay has no kind:30177 for this agent signed by this owner: nothing to add the app id to")
    if head is None:
        name = args.name or profile[1] or agent[:12]
        content: Any = {"name": name, "parallelism": 1, "respond_to": "owner-only"}
    else:
        try:
            content = json.loads(head["content"])
        except ValueError:
            content = None
    if not isinstance(content, dict):
        raise Refused("the agent's current kind:30177 content is not a JSON object: not merging into it")
    feishu = content.get("feishu") if isinstance(content.get("feishu"), dict) else {}
    result = {"agent": agent[:12], "previous_event": head["id"] if head else None, "created": head is None}
    change = {"mirror": True} if args.mirror else {"app_id": args.app_id}
    if head is not None and all(feishu.get(k) == v for k, v in change.items()):
        return {**result, "action": "unchanged"}
    merged = dict(content, feishu={**feishu, **change})
    if args.dry_run:
        return {**result, "action": "would_publish", "fields_kept": sorted(k for k in content if k != "feishu")}
    if head is not None:
        _backup(Path(args.backup_dir), agent, head)
    tags = copy.deepcopy(head["tags"]) if head else [["d", agent]]
    created_at = max(int(now.timestamp()), int(head["created_at"]) + 1) if head else int(now.timestamp())
    event = _sign(key, fgs.KIND_MANAGED_AGENT, tags, json.dumps(merged, ensure_ascii=False), created_at)
    status, answer = _post(http, key, f"{relay}/events", event, now)
    try:
        verdict = json.loads(answer)
    except ValueError:
        verdict = None
    if status != 200 or not isinstance(verdict, dict) or verdict.get("accepted") is not True:
        reason = verdict.get("message") if isinstance(verdict, dict) else None
        raise Failed(f"the relay did not accept the event (HTTP {status}{', ' + str(reason)[:200] if reason else ''})")
    again = _head(http, key, relay, owner, agent, now)
    if again is None or again["id"] != event["id"]:
        raise Failed("readback: the relay's current kind:30177 for this agent is not the event just sent")
    return {**result, "action": "published", "event_id": event["id"]}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--owner-env", required=True, help="0600 env file with the agent owner's BUZZ_PRIVATE_KEY")
    parser.add_argument("--agent", required=True, help="the agent's pubkey (64 hex or npub)")
    parser.add_argument("--app-id", default="", help="the agent's own Feishu app id (cli_...)")
    parser.add_argument("--mirror", action="store_true",
                        help="declare --agent a group sync's mirror identity instead (ADR-0020); creates its policy if it has none")
    parser.add_argument("--name", default="", help="--mirror only: the name of a policy created for the mirror")
    parser.add_argument("--relay-url", default="", help="https relay address (default: BUZZ_RELAY_URL in --owner-env)")
    parser.add_argument("--backup-dir", default=str(Path.home() / ".local" / "state" / "buzz-agent-feishu-app"),
                        help="where the replaced event is saved (0700 directory, 0600 files)")
    parser.add_argument("--dry-run", action="store_true", help="read and report; publish nothing")
    return parser.parse_args(argv)


def _lock(owner: str) -> Any:
    """One run at a time per owner, independent of the caller's chosen backup directory.

    Two runs can read the same replaceable-event head and then overwrite each other's merged content.  A path controlled by
    `--backup-dir` is therefore not a lock identity; the public owner key names a stable, per-account lock instead.
    """
    try:
        LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(LOCK_DIR, 0o700)
        fd = os.open(LOCK_DIR / f"{owner}.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise Failed("the owner policy lock could not be opened") from None
    handle = os.fdopen(fd, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Failed("another run is updating this owner's policies: try again after it") from None
    return handle


def main(argv: list[str] | None = None, *, http: Any = None, now: datetime | None = None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    args = parse_args(argv)
    try:
        try:
            owner_key = fgs.load_signer_key(Path(args.owner_env))
        except fgs.GroupSyncError as exc:
            raise Refused(str(exc)) from None
        lock = _lock(fgs._signer_pubkey(owner_key))
        try:
            result = run(args, http or fgs._http_get, now or datetime.now(timezone.utc), owner_key=owner_key)
        finally:
            lock.close()
    except Refused as exc:
        print(f"refused: {exc}", file=stderr)
        return EXIT_REFUSED
    except Failed as exc:
        print(f"failed: {exc}", file=stderr)
        return EXIT_FAILED
    print(json.dumps(result, ensure_ascii=False), file=stdout)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
