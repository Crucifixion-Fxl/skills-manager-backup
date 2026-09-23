#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Provision or rotate a deterministic set of per-project GitLab tokens.

This operator-only helper is intentionally separate from the runtime wrapper.
It uses the local authenticated ``glab`` session, writes only named token
values to a 0600 Agent env file, and keeps all receipts/journals secret-free.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gitlab_agent_project_tokens as token_map  # noqa: E402
import provision_gitlab_agent_token as single  # noqa: E402


ProvisionError = single.ProvisionError
AGENT_RUNTIME_MARKERS = single.AGENT_RUNTIME_MARKERS
SAFE_NAME = single.SAFE_NAME


def _token_line_pattern(env_name: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:export[ \t]+)?#?[ \t]*{re.escape(env_name)}=(.*)$")


def _read_placeholders(path: Path, mapping: token_map.ProjectTokenMap) -> tuple[str, dict[str, int]]:
    content, _ = single._read_secure_env(path)
    lines = content.splitlines(keepends=True)
    indexes: dict[str, int] = {}
    for env_name in (entry.token_env for entry in mapping.projects):
        matcher = _token_line_pattern(env_name)
        matches = [(index, match.group(1)) for index, line in enumerate(lines)
                   if (match := matcher.fullmatch(line.rstrip("\r\n")))]
        if len(matches) != 1:
            raise ProvisionError(f"env file must contain exactly one {env_name} placeholder")
        index, value = matches[0]
        if value.strip() and value.strip() not in {"<pending>", "<authorized-admin-must-issue>"}:
            raise ProvisionError(f"refusing to overwrite an existing {env_name}")
        indexes[env_name] = index
    return content, indexes


def _read_values(path: Path, mapping: token_map.ProjectTokenMap) -> tuple[str, dict[str, int]]:
    content, _ = single._read_secure_env(path)
    lines = content.splitlines(keepends=True)
    indexes: dict[str, int] = {}
    for env_name in (entry.token_env for entry in mapping.projects):
        matcher = _token_line_pattern(env_name)
        matches = [(index, match.group(1)) for index, line in enumerate(lines)
                   if (match := matcher.fullmatch(line.rstrip("\r\n")))]
        if len(matches) != 1 or not matches[0][1].strip():
            raise ProvisionError(f"env file must contain exactly one populated {env_name}")
        indexes[env_name] = matches[0][0]
    return content, indexes


def _render_values(original: str, indexes: Mapping[str, int], values: Mapping[str, str]) -> str:
    lines = original.splitlines(keepends=True)
    for env_name, index in indexes.items():
        ending = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f"{env_name}={values[env_name]}{ending}"
    return "".join(lines)


def _validate_common(args: argparse.Namespace) -> tuple[Path, Path, str]:
    if AGENT_RUNTIME_MARKERS.intersection(os.environ):
        raise ProvisionError("operator-only helper refuses to run inside a registered Buzz Agent runtime")
    if not SAFE_NAME.fullmatch(args.agent_name):
        raise ProvisionError("agent-name must use letters, digits, dot, underscore or dash")
    if not args.authorization_ref.strip() or "\n" in args.authorization_ref or len(args.authorization_ref) > 200:
        raise ProvisionError("authorization-ref must be a non-empty single line of at most 200 characters")
    if not isinstance(args.expires_at, str):
        raise ProvisionError("expires-at is required for provisioning")
    expires_at = single._validate_expiry(args.expires_at)
    env_path = Path(args.env_file).expanduser()
    receipt_path = Path(args.receipt).expanduser()
    if not receipt_path.is_absolute() or receipt_path.exists() or receipt_path.is_symlink():
        raise ProvisionError("receipt path must be absolute and must not already exist")
    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if receipt_path.parent.is_symlink():
        raise ProvisionError("receipt directory must not be a symlink")
    pending_prefix = f".{receipt_path.name}."
    if any(item.name.startswith(pending_prefix) and item.name.endswith(".pending.json")
           for item in receipt_path.parent.iterdir()):
        raise ProvisionError("an unresolved pending journal exists for this receipt; reconcile it before retrying")
    return env_path, receipt_path, expires_at


def _verify_token(
    *,
    host: str,
    entry: token_map.ProjectTokenEntry,
    token: str,
    bot_user_id: int,
    require_internal_isolation: bool,
    token_client_factory: Callable[[str, str], Any],
) -> tuple[dict[str, Any], list[int], list[int] | None]:
    client = token_client_factory(host, token)
    token_user = client.get_object("/user")
    target = client.get_object(f"/projects/{entry.project_id}")
    memberships = client.get_all("/projects", {"membership": "true", "simple": "true"})
    membership_ids = sorted(item.get("id") for item in memberships if isinstance(item.get("id"), int))
    if require_internal_isolation:
        internal = client.get_all("/projects", {"visibility": "internal", "simple": "true"})
        unexpected_internal: list[int] | None = sorted(
            item.get("id") for item in internal
            if isinstance(item.get("id"), int) and item.get("id") != entry.project_id
        )
    else:
        unexpected_internal = None  # non-external Developer bots can read Internal CI config by design
    if (token_user.get("id") != bot_user_id or target.get("id") != entry.project_id
            or target.get("path_with_namespace") != entry.project_path):
        raise ProvisionError("new token identity or target project verification failed")
    if membership_ids != [entry.project_id] or (unexpected_internal is not None and unexpected_internal):
        raise ProvisionError("new token is not isolated to exactly the target project membership")
    return token_user, membership_ids, unexpected_internal


def _operator(glab: Any, authorized_admin: str) -> dict[str, Any]:
    operator = glab.request("/user")
    if operator.get("username") != authorized_admin or operator.get("is_admin") is not True:
        raise ProvisionError("local glab identity must match --authorized-admin and be a GitLab instance admin")
    return operator


def provision(
    args: argparse.Namespace,
    *,
    glab: Any | None = None,
    token_client_factory: Callable[[str, str], Any] = single.AgentTokenClient,
) -> dict[str, Any]:
    mapping = token_map.load_mapping(args.mapping)
    env_path, receipt_path, expires_at = _validate_common(args)
    glab = glab or single.GlabClient(mapping.host, glab_path=getattr(args, "glab", None))
    with single._exclusive_env_lock(env_path):
        with single._exclusive_receipt_lock(receipt_path):
            original_env, indexes = _read_placeholders(env_path, mapping)
            operator = _operator(glab, args.authorized_admin)
            operation_id = secrets.token_hex(16)
            projects: list[dict[str, Any]] = []
            for entry in mapping.projects:
                encoded = urllib.parse.quote(entry.project_path, safe="")
                project = glab.request(f"/projects/{encoded}")
                if project.get("id") != entry.project_id or project.get("path_with_namespace") != entry.project_path:
                    raise ProvisionError(f"project lookup did not match mapping for {entry.project_path}")
                server_name = f"{args.agent_name}--{entry.project_id}--op-{operation_id}"
                if len(server_name) > 128 or not SAFE_NAME.fullmatch(server_name):
                    raise ProvisionError("generated token name is invalid or too long")
                if any(item.get("name") == server_name and item.get("revoked") is not True
                       for item in single._list_project_tokens(glab, entry.project_id)):
                    raise ProvisionError(f"generated operation token name already exists for {entry.project_path}")
                projects.append({
                    "entry": entry, "server_name": server_name, "project": project,
                    "profile": single.PROFILES[entry.profile],
                })
            journal_path = receipt_path.with_name(f".{receipt_path.name}.{operation_id}.pending.json")
            journal: dict[str, Any] = {
                "schema_version": "2.0", "state": "PENDING", "operation_id": operation_id,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "authorization_evidence_ref": args.authorization_ref, "secret_material_in_journal": False,
                "mapping": token_map.mapping_to_public_dict(mapping),
                "requested": {"agent": args.agent_name, "expires_at": expires_at,
                               "env_file": str(env_path), "receipt": str(receipt_path),
                               "original_env_sha256": hashlib.sha256(original_env.encode()).hexdigest()},
                "projects": [{"project_id": item["entry"].project_id, "project_path": item["entry"].project_path,
                              "token_env": item["entry"].token_env, "server_token_name": item["server_name"]}
                             for item in projects],
            }
            single._write_receipt(journal_path, journal)
            created: list[dict[str, Any]] = []
            secrets_by_env: dict[str, str] = {}
            injected_env: str | None = None
            env_written = False
            receipt_write_attempted = False
            try:
                for item in projects:
                    entry = item["entry"]
                    profile = item["profile"]
                    want_external = single.resolve_external(profile, "auto")
                    project_id = entry.project_id
                    # Register the operation name before the POST.  If the
                    # response is lost after GitLab accepted the request, the
                    # catch path can still reconcile the exact name instead of
                    # silently leaving an orphan token behind.
                    operation_record = {"entry": entry, "server_name": item["server_name"],
                                        "token_id": None, "bot_user_id": None}
                    created.append(operation_record)
                    single._replace_json(journal_path, {**journal, "created_tokens": [
                        {"project_id": value["entry"].project_id, "token_id": value["token_id"],
                         "bot_user_id": value["bot_user_id"]} for value in created
                    ]})
                    created_response = glab.request(
                        f"/projects/{project_id}/access_tokens", method="POST",
                        payload={"name": item["server_name"], "scopes": list(profile.scopes),
                                 "access_level": profile.access_level, "expires_at": expires_at},
                    )
                    token_id = created_response.get("id")
                    bot_user_id = created_response.get("user_id")
                    token_value = created_response.get("token")
                    operation_record.update({"token_id": token_id if isinstance(token_id, int) else None,
                                             "bot_user_id": bot_user_id if isinstance(bot_user_id, int) else None})
                    journal["created_tokens"] = [
                        {"project_id": value["entry"].project_id, "token_id": value["token_id"],
                         "bot_user_id": value["bot_user_id"],
                         "token_sha256": hashlib.sha256(token_value.encode()).hexdigest()
                         if isinstance(token_value, str) else None}
                        for value in created
                    ]
                    single._replace_json(journal_path, journal)
                    if (not isinstance(token_id, int) or not isinstance(bot_user_id, int)
                            or not isinstance(token_value, str) or not token_value):
                        raise ProvisionError(f"GitLab token response is missing id, user_id or token for {entry.project_path}")
                    if (created_response.get("name") != item["server_name"]
                            or created_response.get("expires_at") != expires_at
                            or created_response.get("access_level") != profile.access_level
                            or set(created_response.get("scopes", [])) != set(profile.scopes)):
                        raise ProvisionError(f"GitLab created a token outside authorized parameters for {entry.project_path}")
                    glab.request(f"/users/{bot_user_id}", method="PUT", payload={"external": want_external})
                    bot = glab.request(f"/users/{bot_user_id}")
                    if bot.get("external") is not want_external or bot.get("state") != "active":
                        raise ProvisionError(
                            f"project token bot was not verified as active and "
                            f"{'external' if want_external else 'non-external'} for {entry.project_path}")
                    _, membership_ids, unexpected_internal = _verify_token(
                        host=mapping.host, entry=entry, token=token_value, bot_user_id=bot_user_id,
                        require_internal_isolation=want_external,
                        token_client_factory=token_client_factory,
                    )
                    secrets_by_env[entry.token_env] = token_value
                    item.update({"token_id": token_id, "bot_user_id": bot_user_id, "bot": bot,
                                 "bot_external": want_external, "internal_isolation_checked": want_external,
                                 "membership_ids": membership_ids, "unexpected_internal": unexpected_internal,
                                 "created": created_response})
                current_env, _ = single._read_secure_env(env_path)
                if current_env != original_env:
                    raise ProvisionError("env file changed after preflight; refusing to overwrite it")
                injected_env = _render_values(original_env, indexes, secrets_by_env)
                single._atomic_replace(env_path, injected_env)
                env_written = True
                current_env, metadata = single._read_secure_env(env_path)
                if current_env != injected_env or metadata.st_uid != os.getuid() or (metadata.st_mode & 0o777) != 0o600:
                    raise ProvisionError("env file token injection readback failed")
                receipt = {
                    "schema_version": "2.0", "operation_id": operation_id,
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "authorization": {"admin_id": operator.get("id"), "admin_username": operator.get("username"),
                                       "reference": args.authorization_ref},
                    "agent": args.agent_name, "mapping": token_map.mapping_to_public_dict(mapping),
                    "tokens": [{
                        "project_id": item["entry"].project_id, "project_path": item["entry"].project_path,
                        "token_env": item["entry"].token_env, "profile": item["entry"].profile,
                        "token_id": item["token_id"], "token_name": item["created"].get("name"),
                        "bot_user_id": item["bot_user_id"], "bot_username": item["bot"].get("username"),
                        "bot_external": item["bot_external"],
                        "internal_isolation_checked": item["internal_isolation_checked"],
                        "access_level": item["profile"].access_level,
                        "scopes": list(item["profile"].scopes), "expires_at": item["created"].get("expires_at"),
                        "membership_project_ids": item["membership_ids"],
                        "unexpected_internal_project_ids": item["unexpected_internal"],
                    } for item in projects],
                    "secret_material_in_receipt": False,
                    "ordinary_agent_gitlab_writes_enabled": False,
                    "l4_canary": "pending",
                }
                receipt_write_attempted = True
                single._write_receipt(receipt_path, receipt)
                journal_path.unlink()
                return receipt
            except BaseException as exc:
                cleanup_errors: list[str] = []
                if env_written and injected_env is not None:
                    try:
                        current, _ = single._read_secure_env(env_path)
                        if current != injected_env:
                            raise ProvisionError("env no longer matches this operation; refusing destructive rollback")
                        single._atomic_replace(env_path, original_env)
                        env_written = False
                    except BaseException as rollback_exc:
                        cleanup_errors.append(f"env rollback failed: {rollback_exc}")
                if receipt_write_attempted:
                    try:
                        # A receipt is a success claim. If anything fails
                        # after it is written, remove only this operation's
                        # receipt before rolling back remote state. The
                        # helper performs an operation-id CAS check.
                        single._unlink_receipt_if_owned(receipt_path, operation_id)
                    except BaseException as receipt_exc:
                        cleanup_errors.append(f"receipt rollback failed: {receipt_exc}")
                for item in reversed(created):
                    try:
                        if isinstance(item.get("token_id"), int):
                            glab.request(f"/projects/{item['entry'].project_id}/access_tokens/{item['token_id']}",
                                         method="DELETE", allow_empty=True)
                        else:
                            single._revoke_unique_token_named(glab, item["entry"].project_id, item["server_name"])
                    except BaseException as revoke_exc:
                        cleanup_errors.append(f"token revocation failed for {item['entry'].project_path}: {revoke_exc}")
                if not cleanup_errors and not env_written:
                    try:
                        journal_path.unlink()
                    except BaseException as journal_exc:
                        cleanup_errors.append(f"pending journal cleanup failed: {journal_exc}")
                else:
                    journal["state"] = "MANUAL_RECONCILIATION_REQUIRED"
                    journal["cleanup_errors"] = cleanup_errors
                    try:
                        single._replace_json(journal_path, journal)
                    except BaseException as journal_exc:
                        cleanup_errors.append(f"pending journal update failed: {journal_exc}")
                message = str(exc) if isinstance(exc, ProvisionError) else "unexpected provisioning failure"
                if cleanup_errors:
                    message += "; " + "; ".join(cleanup_errors)
                if not isinstance(exc, Exception):
                    raise
                raise ProvisionError(message) from exc


def rotate(
    args: argparse.Namespace,
    *,
    glab: Any | None = None,
    token_client_factory: Callable[[str, str], Any] = single.AgentTokenClient,
) -> dict[str, Any]:
    """Rotate every mapped token while requiring the Project Access Token bot id to stay stable.

    GitLab revokes the old token as part of rotation, so a remote partial
    completion is intentionally journaled for manual reconciliation rather
    than pretending it can be rolled back.
    """
    mapping = token_map.load_mapping(args.mapping)
    env_path = Path(args.env_file).expanduser()
    receipt_path = Path(args.receipt).expanduser()
    if not receipt_path.is_absolute() or not receipt_path.exists() or receipt_path.is_symlink():
        raise ProvisionError("rotation receipt must be an absolute existing non-symlink")
    if not env_path.is_absolute():
        raise ProvisionError("env file must be absolute")
    glab = glab or single.GlabClient(mapping.host, glab_path=getattr(args, "glab", None))
    with single._exclusive_env_lock(env_path):
        with single._exclusive_receipt_lock(receipt_path):
            receipt_raw, _ = single._read_secure_env(receipt_path)
            try:
                receipt = json.loads(receipt_raw)
            except json.JSONDecodeError as exc:
                raise ProvisionError("rotation receipt contains invalid JSON") from exc
            if not isinstance(receipt, dict) or receipt.get("schema_version") != "2.0":
                raise ProvisionError("rotation requires a schema v2 multi-token receipt")
            if receipt.get("mapping") != token_map.mapping_to_public_dict(mapping):
                raise ProvisionError("rotation receipt mapping does not match the owner-controlled map")
            entries = {entry.project_id: entry for entry in mapping.projects}
            tokens = receipt.get("tokens")
            if not isinstance(tokens, list) or len(tokens) != len(entries):
                raise ProvisionError("rotation receipt does not cover the current mapping exactly")
            receipt_project_ids = [item.get("project_id") for item in tokens if isinstance(item, dict)]
            receipt_token_envs = [item.get("token_env") for item in tokens if isinstance(item, dict)]
            if (len(receipt_project_ids) != len(set(receipt_project_ids))
                    or set(receipt_project_ids) != set(entries)
                    or len(receipt_token_envs) != len(set(receipt_token_envs))):
                raise ProvisionError("rotation receipt contains duplicate or unmapped projects")
            if not getattr(args, "authorized_admin", None):
                raise ProvisionError("rotation requires --authorized-admin")
            _operator(glab, args.authorized_admin)
            original_env, indexes = _read_values(env_path, mapping)
            operation_id = secrets.token_hex(16)
            journal_path = receipt_path.with_name(f".{receipt_path.name}.{operation_id}.pending.json")
            journal: dict[str, Any] = {"schema_version": "2.0", "state": "ROTATION_PENDING",
                                      "operation_id": operation_id, "secret_material_in_journal": False,
                                      "mapping": token_map.mapping_to_public_dict(mapping), "rotated": []}
            single._write_receipt(journal_path, journal)
            rotated_values: dict[str, str] = {}
            try:
                for item in tokens:
                    project_id = item.get("project_id")
                    mapped = entries.get(project_id)
                    if mapped is None or item.get("project_path") != mapped.project_path or item.get("token_env") != mapped.token_env:
                        raise ProvisionError("rotation receipt project mapping does not match")
                    if item.get("profile") != mapped.profile:
                        raise ProvisionError(f"rotation receipt profile does not match for {mapped.project_path}")
                    old_bot = item.get("bot_user_id")
                    old_token_id = item.get("token_id")
                    if not isinstance(old_bot, int) or not isinstance(old_token_id, int):
                        raise ProvisionError("rotation receipt is missing token or bot identity")
                    bot_external = item.get("bot_external")
                    if not isinstance(bot_external, bool):
                        bot_external = single.resolve_external(single.PROFILES[mapped.profile], "auto")
                    rotation_record = {"project_id": project_id, "old_token_id": old_token_id,
                                       "new_token_id": None, "bot_user_id": old_bot,
                                       "bot_external": bot_external}
                    journal["rotated"].append(rotation_record)
                    single._replace_json(journal_path, journal)
                    payload = {}
                    if getattr(args, "expires_at", None):
                        payload["expires_at"] = single._validate_expiry(args.expires_at)
                    response = glab.request(f"/projects/{project_id}/access_tokens/{old_token_id}/rotate",
                                            method="POST", payload=payload or None)
                    new_id, new_bot, token_value = response.get("id"), response.get("user_id"), response.get("token")
                    if not isinstance(new_id, int) or not isinstance(new_bot, int) or not isinstance(token_value, str) or not token_value:
                        raise ProvisionError(f"rotation response is incomplete for {mapped.project_path}")
                    if new_bot != old_bot:
                        rotation_record.update({"new_token_id": new_id, "returned_bot_user_id": new_bot})
                        single._replace_json(journal_path, journal)
                        raise ProvisionError(f"rotation changed bot identity for {mapped.project_path}")
                    rotation_record.update({"new_token_id": new_id, "token_sha256": hashlib.sha256(token_value.encode()).hexdigest()})
                    glab.request(f"/users/{new_bot}", method="PUT", payload={"external": bot_external})
                    bot = glab.request(f"/users/{new_bot}")
                    if bot.get("external") is not bot_external or bot.get("state") != "active":
                        raise ProvisionError(
                            f"rotated bot is not active and "
                            f"{'external' if bot_external else 'non-external'} for {mapped.project_path}")
                    _, membership_ids, unexpected_internal = _verify_token(
                        host=mapping.host, entry=mapped, token=token_value, bot_user_id=new_bot,
                        require_internal_isolation=bot_external,
                        token_client_factory=token_client_factory,
                    )
                    rotated_values[mapped.token_env] = token_value
                    single._replace_json(journal_path, journal)
                current_env, _ = single._read_secure_env(env_path)
                if current_env != original_env:
                    raise ProvisionError("env file changed during rotation; refusing overwrite")
                injected = _render_values(original_env, indexes, rotated_values)
                single._atomic_replace(env_path, injected)
                current_receipt, _ = single._read_secure_env(receipt_path)
                if current_receipt != receipt_raw:
                    raise ProvisionError("receipt changed during rotation; refusing overwrite")
                for item in receipt["tokens"]:
                    update = next(value for value in journal["rotated"] if value["project_id"] == item["project_id"])
                    item["token_id"] = update["new_token_id"]
                receipt["operation_id"] = operation_id
                receipt["rotated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                receipt["secret_material_in_receipt"] = False
                single._atomic_replace(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                journal_path.unlink()
                return receipt
            except BaseException as exc:
                journal["state"] = "MANUAL_RECONCILIATION_REQUIRED"
                journal["error"] = str(exc) if isinstance(exc, Exception) else "unexpected rotation interruption"
                try:
                    single._replace_json(journal_path, journal)
                except BaseException:
                    pass
                if not isinstance(exc, Exception):
                    raise
                raise ProvisionError(str(exc)) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--agent-name")
    parser.add_argument("--expires-at")
    parser.add_argument("--authorized-admin")
    parser.add_argument("--authorization-ref")
    parser.add_argument("--rotate", action="store_true")
    parser.add_argument("--glab")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.rotate:
            receipt = rotate(args)
        else:
            required = ("agent_name", "expires_at", "authorized_admin", "authorization_ref")
            if any(not getattr(args, field) for field in required):
                raise ProvisionError("provisioning requires --agent-name, --expires-at, --authorized-admin and --authorization-ref")
            receipt = provision(args)
    except ProvisionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: processed {len(receipt['tokens'])} mapped GitLab project token(s); receipt={Path(args.receipt).expanduser()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
