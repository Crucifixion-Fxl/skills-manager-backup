#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate the channel config's people map from the bridge's per-channel emails.

Where the emails come from (one of the two, never both):

- the bridge's signed endpoint (recommended): GET {base_url}/bind/api/channels/{channel}/people, NIP-98 signed with
  a channel owner's or admin's Buzz key (infra/buzz-deploy#77; the bridge must run with
  CHANNEL_PEOPLE_EMAILS_ENABLED). It answers for the channel's current members only, so a person who is not in
  the channel cannot be mapped, and a binding that changed is picked up on the next run. Any failure (no emails
  in the answer, a status other than 200, a timeout, a malformed or foreign answer) ends the whole run before a
  file is touched.
- the file `ops export-people` writes (fallback/offline): a 0600 JSON object mapping every directory address to
  its Buzz pubkey (ADR-0012, infra/buzz-deploy#51), for hosts that cannot reach the bridge.

Either way the tool joins the addresses with the project's GitLab usernames (username@<domain>, localpart
fallback), merges the result into the sync config's `people`, and writes the config back atomically. Manual
entries always win and are never deleted; a generated entry whose pubkey is the publisher's or an agent's is
refused, so the config the sync loads next round always validates. The signing key never leaves this process.
Nothing but usernames, channel ids and counts is printed: no address, key, header or response body.

One address bound to several keys (API mode only): that is one person with several verified keys, and @-ing any of
them reaches him, so one is written rather than dropping the person, whom nobody could then notify. Refused keys
(Desk, agents, publisher) are set aside first; among the rest the key that is a member of the most configured
channels wins and ties go to the smallest hex, so the same input always gives the same output. The warning names the
username, the chosen key's first 8 hex digits and how many were dropped, never an address or a whole key. Addresses
that merely share a localpart may belong to different people: those stay unmapped. `--export` never has several keys
for one address and behaves as before.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
DOMAIN_DEFAULT = "a4x.io"
HEX64_RE = re.compile(r"[0-9a-f]{64}")
USERNAME_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,254}")
# A project/group access token's bot user is exactly project_<id>_bot_<hex>
# (gitlab_buzz_sync's GITLAB_BOT_USERNAME_RE is a prefix match for its own
# purposes; here the full boundary matters — "project_1312_botany" is a
# person, not a bot).
GITLAB_BOT_USERNAME_RE = re.compile(r"(?:project|group)_[0-9]+_bot_[0-9a-f]+")
MEMBERS_PAGE_SIZE = 100
MEMBERS_MAX_PAGES = 50


class GenerateError(RuntimeError):
    pass


def _bridge() -> Any:
    """The feishu group sync's bridge client (NIP-98 header, URL, strict answer parser, signer-key loader, GET
    that follows no redirect and uses no proxy). Loaded only when the API mode needs it, so the `--export`
    mode does not depend on it."""
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import buzz_feishu_group_sync

    return buzz_feishu_group_sync


def _owner_only_json(path: Path, what: str) -> Any:
    """Load a JSON value from an owner-only regular file, never a symlink
    (the sync's load_config contract; the export carries the same PII)."""
    if not path.is_absolute():
        raise GenerateError(f"{what} path must be absolute")
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
            raise GenerateError(f"{what} must be an owner-only regular file, not a symlink")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return json.load(handle)
    except GenerateError:
        raise
    except OSError as exc:
        if path.is_symlink():
            raise GenerateError(f"{what} must be an owner-only regular file, not a symlink") from None
        raise GenerateError(f"cannot read {what} {path.name}: {type(exc).__name__}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GenerateError(f"cannot read {what} {path.name}: {type(exc).__name__}") from None
    finally:
        if fd >= 0:
            os.close(fd)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """The sync's atomic_write_json semantics: O_EXCL|O_NOFOLLOW 0600 tmp,
    os.replace, directory fsync."""
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


def _gitlab_members(base_url: str, token: str, project_id: Any, token_env: str) -> set[str]:
    """Every member username of the project (members/all, all pages)."""
    usernames: set[str] = set()
    page = 1
    for _ in range(MEMBERS_MAX_PAGES):
        url = f"{base_url}/api/v4/projects/{project_id}/members/all?per_page={MEMBERS_PAGE_SIZE}&page={page}"
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise GenerateError(f"project {project_id}: members/all answered HTTP {response.status}")
                rows = json.loads(response.read().decode("utf-8") or "[]")
        except urllib.error.HTTPError as exc:
            raise GenerateError(
                f"project {project_id}: members/all answered HTTP {exc.code} (check {token_env})"
            ) from None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise GenerateError(f"project {project_id}: members/all failed: {type(exc).__name__}") from None
        if not isinstance(rows, list):
            raise GenerateError(f"project {project_id}: members/all answered a non-list body")
        for row in rows:
            name = row.get("username") if isinstance(row, dict) else None
            if isinstance(name, str) and USERNAME_RE.fullmatch(name):
                usernames.add(name)
        next_page = response.headers.get("x-next-page", "")
        if not next_page or not rows:
            return usernames
        if not next_page.isdigit():
            raise GenerateError(f"project {project_id}: members/all gave a malformed x-next-page")
        page = int(next_page)
    raise GenerateError(f"project {project_id}: members/all exceeded {MEMBERS_MAX_PAGES} pages")


def _validate_people_entry(username: str, pubkey: str, forbidden: set[str]) -> None:
    if not USERNAME_RE.fullmatch(username):
        raise GenerateError("people keys must be GitLab usernames")
    if not (isinstance(pubkey, str) and HEX64_RE.fullmatch(pubkey)):
        raise GenerateError(f"people[{username}] must be a 64-hex Buzz pubkey")
    if pubkey in forbidden:
        raise GenerateError("people must list humans only; Desk and agent pubkeys are not allowed")


def _localpart(address: str) -> str:
    return address.rsplit("@", 1)[0].lower() if "@" in address else ""


def run(
    config_path: Path,
    export_path: Path | None,
    domain: str,
    dry_run: bool,
    *,
    api: Mapping[str, Any] | None = None,
    http: Callable[..., tuple[int, bytes]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Merge the addresses of `export_path` (a file) or of the bridge's people API (`api`: {base_url,
    signer_env_file}; `http` and `now` are for tests) into the config's inline people."""
    _one_source(export_path, api)
    # One writer at a time: the read-merge-replace would silently lose the
    # entries another process added between our read and our replace (the
    # atomic rename only prevents torn writes, not lost updates). The lock
    # sits next to the config and covers the whole run, members fetch
    # included — runs are manual and rare.
    lock_path = config_path.with_name(config_path.name + ".people.lock")
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        raise GenerateError("another people-generate run holds the lock") from None
    try:
        return _run_locked(config_path, export_path, domain, dry_run, api, http, now)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _gitlab_settings(config: dict[str, Any]) -> tuple[str, str, list[Any], str]:
    """(token_env, base_url, projects, token) of a channel config; the token is read from the environment, never printed."""
    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise GenerateError("config has no gitlab section")
    token_env = gitlab.get("token_env")
    base_url = gitlab.get("base_url")
    projects = gitlab.get("projects")
    if not (isinstance(token_env, str) and token_env):
        raise GenerateError("gitlab.token_env is required")
    if not isinstance(base_url, str) or not (
        base_url.startswith("https://")
        or (
            base_url.startswith("http://")
            and urllib.parse.urlsplit(base_url).hostname in ("localhost", "127.0.0.1", "::1")
        )
    ):
        raise GenerateError("gitlab.base_url must be an https origin (http on loopback only)")
    if not isinstance(projects, list) or not projects:
        raise GenerateError("gitlab.projects must be a non-empty list of ids")
    token = os.environ.get(token_env, "")
    if not token:
        raise GenerateError(f"environment variable {token_env} is required (never printed)")
    return token_env, base_url, projects, token


def _forbidden_keys(config: dict[str, Any]) -> set[str]:
    agent_pubkeys = config.get("agent_pubkeys") or []
    publisher = config.get("publisher_pubkey")
    if not isinstance(agent_pubkeys, list) or not isinstance(publisher, str):
        raise GenerateError("agent_pubkeys and publisher_pubkey are required for the humans-only check")
    return {pubkey for pubkey in agent_pubkeys if isinstance(pubkey, str)} | {publisher}


def _one_source(export_path: Path | None, api: Mapping[str, Any] | None) -> None:
    if (export_path is None) == (api is None):
        raise GenerateError("give exactly one email source: the bridge API (--people-api-base-url and "
                            "--signer-env-file) or an export file (--export)")


def _load_export(export_path: Path) -> dict[str, str]:
    export = _owner_only_json(export_path, "export")
    if not isinstance(export, dict) or not export:
        raise GenerateError("export must be a non-empty JSON object of email -> pubkey")
    export_lower: dict[str, str] = {}
    for address, pubkey in export.items():
        if not isinstance(address, str) or "@" not in address:
            raise GenerateError("export keys must be email addresses")
        if not (isinstance(pubkey, str) and HEX64_RE.fullmatch(pubkey)):
            raise GenerateError("export values must be 64-hex Buzz pubkeys")
        export_lower[address.strip().lower()] = pubkey
    return export_lower


def _channel_emails(bridge: Any, api: Mapping[str, Any], key: str, channel: str,
                    http: Callable[..., tuple[int, bytes]], now: Callable[[], datetime]) -> dict[str, list[str]]:
    """{pubkey: [address, ...]} of one channel's current members, from the bridge. Every failure names the channel
    (a channel id is not a secret) and never quotes the answer, an address or a key."""
    url = bridge.people_url(api["base_url"], channel)
    try:
        header = bridge.nip98_header(key, "GET", url, now())
        status, body = http(url, {"Authorization": header, "Accept": "application/json"}, bridge.PEOPLE_API_TIMEOUT)
    except bridge.GroupSyncError as exc:  # its messages never quote the key or the answer
        raise GenerateError(f"channel {channel}: {exc}") from None
    except OSError:
        raise GenerateError(f"channel {channel}: the people API could not be reached") from None
    if status != 200:
        hint = ""
        if status == 401:
            hint = (" (signature refused: check this host's clock, and that --people-api-base-url is exactly "
                    "the bridge's BIND_PUBLIC_ORIGIN)")
        elif status == 404:
            hint = " (the signer must be an owner or admin of this channel)"
        raise GenerateError(f"channel {channel}: the people API answered HTTP {status}{hint}")
    try:
        bridge.parse_people_response(body, channel)
    except bridge.GroupSyncError as exc:
        raise GenerateError(f"channel {channel}: {exc}") from None
    doc = json.loads(body)
    if "emails" not in doc:
        raise GenerateError(
            f"channel {channel}: the bridge answered without emails: turn on CHANNEL_PEOPLE_EMAILS_ENABLED on the "
            "bridge (infra/buzz-deploy#77), or use --export"
        )
    emails = doc["emails"]
    valid = isinstance(emails, dict)
    for pubkey, addresses in (emails.items() if valid else ()):
        valid = valid and isinstance(pubkey, str) and bool(HEX64_RE.fullmatch(pubkey)) and isinstance(addresses, list)
        for address in addresses if valid else ():
            local, _, domain = address.rpartition("@") if isinstance(address, str) else ("", "", "")
            valid = valid and bool(local.strip()) and bool(domain.strip())
    if not valid:
        raise GenerateError(f"channel {channel}: the people API's emails object is malformed")
    return {pubkey: list(addresses) for pubkey, addresses in emails.items()}


def _bridge_emails(configs: list[dict[str, Any]], api: Mapping[str, Any],
                   http: Callable[..., tuple[int, bytes]] | None,
                   now: Callable[[], datetime] | None) -> tuple[dict[str, set[str]], int, dict[str, int]]:
    """The addresses of every distinct channel the configs name, each asked once: {address: {pubkeys}}, the number of
    channels, and {pubkey: how many of those channels list it as a member}. If any channel cannot be read, the whole
    run fails."""
    bridge = _bridge()
    try:
        bridge._check_people_api({"base_url": api.get("base_url"), "signer_env_file": str(api.get("signer_env_file"))})
    except bridge.GroupSyncError:
        raise GenerateError(
            "--people-api-base-url must be exactly https://host[:port] (no path, query, userinfo or trailing "
            "slash) and --signer-env-file an absolute path"
        ) from None
    channels: list[str] = []
    for config in configs:
        channel = config.get("channel_id")
        if not (isinstance(channel, str) and bridge.UUID_RE.fullmatch(channel)):
            raise GenerateError("config channel_id must be a lower-case uuid (the API mode asks the bridge per channel)")
        if channel not in channels:
            channels.append(channel)
    try:
        key = bridge.load_signer_key(Path(str(api["signer_env_file"])))
    except bridge.GroupSyncError as exc:  # never quotes the key
        raise GenerateError(str(exc)) from None
    http = http or bridge._http_get
    now = now or (lambda: datetime.now(timezone.utc))
    emails: dict[str, set[str]] = {}
    member_channels: dict[str, int] = {}
    for channel in channels:
        for pubkey, addresses in _channel_emails(bridge, api, key, channel, http, now).items():
            member_channels[pubkey] = member_channels.get(pubkey, 0) + 1  # once per channel: it is a dict key there
            for address in addresses:
                emails.setdefault(address.strip().lower(), set()).add(pubkey)
    return emails, len(channels), member_channels


def _collect_emails(configs: list[dict[str, Any]], export_path: Path | None, api: Mapping[str, Any] | None,
                    http: Callable[..., tuple[int, bytes]] | None,
                    now: Callable[[], datetime] | None
                    ) -> tuple[dict[str, set[str]], str, dict[str, Any], dict[str, int] | None]:
    """({address: {pubkeys}}, how warnings call the source, extra result fields, {pubkey: channels it is a member of}) from
    the source the run was given. The export has no channels (and one key per address): None, so nothing is chosen there."""
    if export_path is not None:
        return {address: {pubkey} for address, pubkey in _load_export(export_path).items()}, "export", {"source": "export"}, None
    assert api is not None
    emails, channels, member_channels = _bridge_emails(configs, api, http, now)
    return emails, "bridge", {"source": "api", "channels": channels}, member_channels


def _match_usernames(
    usernames: set[str],
    emails: dict[str, set[str]],
    existing: dict[str, str],
    forbidden: set[str],
    domain: str,
    origin: str = "export",
    member_channels: Mapping[str, int] | None = None,
) -> tuple[dict[str, str], int, int, list[str], list[str]]:
    """Join GitLab usernames with the addresses; `origin` ("export" or "bridge") only words the warnings. Existing
    entries win and are never touched.

    One address bound to several keys is one person with several verified keys, and @-ing any of them reaches him:
    when `member_channels` is given (API mode: {pubkey: how many channels list it}) one key is written, the one that
    is a member of the most channels, ties to the smallest hex; keys that would be refused (`forbidden`) are set aside
    before that choice. Addresses that only share a localpart may be different people, so those stay unmapped, and so
    does everything when there are no `member_channels` (the export never has several keys for one address)."""
    added: dict[str, str] = {}
    kept_manual = 0
    rejected_agent_key = 0
    unmapped: list[str] = []
    warn: list[str] = []
    for username in sorted(usernames):
        if GITLAB_BOT_USERNAME_RE.fullmatch(username):
            continue  # project/group access token bots are never people
        candidates = set(emails.get(f"{username.lower()}@{domain}", ()))
        exact = bool(candidates)
        one_address = True
        if not exact:
            by_address = {
                address: pubkeys for address, pubkeys in emails.items() if _localpart(address) == username.lower()
            }
            candidates = {pubkey for pubkeys in by_address.values() for pubkey in pubkeys}
            one_address = len(by_address) == 1
        if not candidates:
            unmapped.append(username)
            continue
        if len(candidates) > 1 and not (member_channels is not None and one_address):
            unmapped.append(username)  # several addresses (maybe several people), or no channel data: never guess
            warn.append(f"{username}: address maps to several keys, kept unmapped" if exact
                        else f"{username}: ambiguous localpart, kept unmapped")
            continue
        if username in existing:
            kept_manual += 1
            if existing[username] not in candidates:
                warn.append(f"{username}: manual entry kept (differs from the {origin})")
            continue
        eligible = candidates - forbidden
        if not eligible:
            rejected_agent_key += 1
            warn.append(f"{username}: {origin} maps to a Desk or agent pubkey, refused")
            continue
        pubkey = min(eligible, key=lambda key: (-(member_channels or {}).get(key, 0), key))
        if len(candidates) > 1:
            warn.append(f"{username}: address maps to several keys, chose {pubkey[:8]}, dropped {len(candidates) - 1}")
        added[username] = pubkey
    return added, kept_manual, rejected_agent_key, unmapped, warn


def _run_locked(config_path: Path, export_path: Path | None, domain: str, dry_run: bool,
                api: Mapping[str, Any] | None = None, http: Callable[..., tuple[int, bytes]] | None = None,
                now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    config = _owner_only_json(config_path, "config")
    if not isinstance(config, dict):
        raise GenerateError("config must be a JSON object")

    token_env, base_url, projects, token = _gitlab_settings(config)

    people = config.get("people") or {}
    if not isinstance(people, dict):
        raise GenerateError("people must map GitLab usernames to Buzz pubkeys")
    forbidden = _forbidden_keys(config)
    for username, pubkey in people.items():
        _validate_people_entry(username, pubkey, forbidden)

    emails, origin, source, member_channels = _collect_emails([config], export_path, api, http, now)

    usernames: set[str] = set()
    for project_id in projects:
        usernames |= _gitlab_members(base_url.rstrip("/"), token, project_id, token_env)

    added, kept_manual, rejected_agent_key, unmapped, warn = _match_usernames(
        usernames, emails, people, forbidden, domain, origin, member_channels
    )

    merged = {**people, **added}
    for username, pubkey in merged.items():
        _validate_people_entry(username, pubkey, forbidden)

    changed = bool(added)
    written = False
    if changed and not dry_run:
        config["people"] = merged
        _atomic_write_json(config_path, config)
        written = True

    return {
        "added": len(added),
        "kept_manual": kept_manual,
        "rejected_agent_key": rejected_agent_key,
        "unmapped": sorted(unmapped),
        "warn": warn,
        "changed": changed,
        "written": written,
        "dry_run": dry_run,
        **source,
    }


def run_shared(
    config_paths: list[Path],
    export_path: Path | None,
    people_file: Path,
    domain: str,
    dry_run: bool,
    *,
    api: Mapping[str, Any] | None = None,
    http: Callable[..., tuple[int, bytes]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Shared-file mode: the union of every given channel's GitLab members, joined with the addresses (the export
    file, or the people API of every distinct channel the configs name), merged into one people file that the
    channel configs point at with `people_file`. The configs themselves are never written. Existing entries win
    and are never deleted; a pubkey that is a Desk or agent key in *any* of the given configs is refused."""
    _one_source(export_path, api)
    people_file = Path(people_file)
    if not people_file.is_absolute():
        raise GenerateError("--people-file must be an absolute path")
    if not people_file.parent.is_dir():
        raise GenerateError("--people-file directory must exist")
    if dry_run:
        # A dry run writes nothing, so there is no read-merge-replace to serialize: it must not even create the
        # lock file (the documented "--dry-run creates and changes no file" contract).
        return _run_shared_locked(config_paths, export_path, people_file, domain, dry_run, api, http, now)
    lock_path = people_file.with_name(people_file.name + ".people.lock")
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        raise GenerateError("another people-generate run holds the lock") from None
    try:
        return _run_shared_locked(config_paths, export_path, people_file, domain, dry_run, api, http, now)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _run_shared_locked(
    config_paths: list[Path], export_path: Path | None, people_file: Path, domain: str, dry_run: bool,
    api: Mapping[str, Any] | None = None, http: Callable[..., tuple[int, bytes]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    configs: list[dict[str, Any]] = []
    for path in config_paths:
        config = _owner_only_json(path, "config")
        if not isinstance(config, dict):
            raise GenerateError("config must be a JSON object")
        configs.append(config)
    if not configs:
        raise GenerateError("at least one --config is required")

    forbidden: set[str] = set()
    for config in configs:
        forbidden |= _forbidden_keys(config)

    existing: dict[str, str] = {}
    if os.path.lexists(people_file):
        loaded = _owner_only_json(people_file, "people file")
        if not isinstance(loaded, dict):
            raise GenerateError("people file must be a JSON object")
        existing = loaded
    for username, pubkey in existing.items():
        _validate_people_entry(username, pubkey, forbidden)

    settings = [_gitlab_settings(config) for config in configs]
    emails, origin, source, member_channels = _collect_emails(configs, export_path, api, http, now)

    usernames: set[str] = set()
    fetched: set[tuple[str, Any]] = set()
    for token_env, base_url, projects, token in settings:
        for project_id in projects:
            key = (base_url.rstrip("/"), project_id)
            if key in fetched:
                continue
            fetched.add(key)
            usernames |= _gitlab_members(base_url.rstrip("/"), token, project_id, token_env)

    added, kept_manual, rejected_agent_key, unmapped, warn = _match_usernames(
        usernames, emails, existing, forbidden, domain, origin, member_channels
    )

    merged = {**existing, **added}
    for username, pubkey in merged.items():
        _validate_people_entry(username, pubkey, forbidden)

    changed = bool(added)
    written = False
    if changed and not dry_run:
        _atomic_write_json(people_file, merged)
        written = True

    return {
        "added": len(added),
        "kept_manual": kept_manual,
        "rejected_agent_key": rejected_agent_key,
        "unmapped": sorted(unmapped),
        "warn": warn,
        "changed": changed,
        "written": written,
        "dry_run": dry_run,
        "configs": len(configs),
        "projects": len(fetched),
        "usernames": len(usernames),
        **source,
    }


def main(argv: list[str] | None = None, *, http: Callable[..., tuple[int, bytes]] | None = None,
         now: Callable[[], datetime] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge the people the bridge knows per channel into the sync config's people map "
        "(manual entries win, nothing is ever deleted). "
        "Recommended source: the bridge's signed people API (--people-api-base-url and --signer-env-file): "
        "the signer must be an owner or admin of every channel the configs name, and the bridge must run with "
        "CHANNEL_PEOPLE_EMAILS_ENABLED; any failure ends the run before a file is touched. "
        "Fallback/offline source: --export, the file ops export-people wrote. The two are mutually exclusive. "
        "When one address is bound to several keys (API mode), the key that is a member of the most configured "
        "channels is written (ties: the smallest hex) and a warning gives the username, the chosen key's first 8 hex "
        "digits and how many were dropped; --export never has that case. "
        "With --people-file, write one shared people file for several channel configs "
        "instead of editing a config's inline people.",
    )
    parser.add_argument("--config", required=True, type=Path, action="append",
                        help="the channel config (owner-only 0600); repeat with --people-file for several channels")
    parser.add_argument("--people-api-base-url", metavar="URL",
                        help="the bridge's public origin, exactly https://host[:port] (its BIND_PUBLIC_ORIGIN); "
                        "each config's channel_id is asked once; needs --signer-env-file")
    parser.add_argument("--signer-env-file", type=Path,
                        help="absolute path of the 0600 env file whose BUZZ_PRIVATE_KEY (hex or nsec) signs the "
                        "requests; used in this process only; needs --people-api-base-url")
    parser.add_argument("--export", type=Path,
                        help="fallback/offline: ops export-people's 0600 JSON file; excludes the API arguments")
    parser.add_argument("--people-file", type=Path,
                        help="absolute path of the shared people file (0600) that channel configs reference "
                        "with `people_file`; the configs themselves are not modified")
    parser.add_argument("--domain", default=DOMAIN_DEFAULT, help=f"corporate mail domain (default {DOMAIN_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = parser.parse_args(argv)
    try:
        api = None
        if args.export is not None and (args.people_api_base_url is not None or args.signer_env_file is not None):
            raise GenerateError("--export and --people-api-base-url/--signer-env-file are mutually exclusive: "
                                "use the bridge API (recommended) or the export file (fallback), not both")
        if args.export is None:
            if args.people_api_base_url is None and args.signer_env_file is None:
                raise GenerateError("no email source: give --people-api-base-url and --signer-env-file "
                                    "(recommended) or --export (fallback/offline)")
            if args.signer_env_file is None:
                raise GenerateError("--people-api-base-url needs --signer-env-file")
            if args.people_api_base_url is None:
                raise GenerateError("--signer-env-file needs --people-api-base-url")
            api = {"base_url": args.people_api_base_url, "signer_env_file": str(args.signer_env_file)}
        if args.people_file is not None:
            result = run_shared(args.config, args.export, args.people_file, args.domain, args.dry_run,
                                api=api, http=http, now=now)
        elif len(args.config) != 1:
            raise GenerateError("several --config need --people-file (one shared file for all of them)")
        else:
            result = run(args.config[0], args.export, args.domain, args.dry_run, api=api, http=http, now=now)
    except GenerateError as exc:
        print(f"people-generate: {exc}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
