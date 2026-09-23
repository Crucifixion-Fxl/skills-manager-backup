#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fail-closed Zendesk Admin configuration planner and applier.

Only credentials from environment variables are accepted. The tool never deletes
resources, never logs credentials, and requires an exact plan SHA for writes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


RESOURCE_SPECS = {
    "groups": ("groups", "group", "name"),
    "ticket_fields": ("ticket_fields", "ticket_field", "title"),
    "ticket_forms": ("ticket_forms", "ticket_form", "name"),
    "triggers": ("triggers", "trigger", "title"),
}
MANAGED_BODY_KEYS = {
    "groups": {"name", "description"},
    "ticket_fields": {
        "title", "type", "description", "active", "required", "collapsed_for_agents",
        "regexp_for_validation", "title_in_portal", "visible_in_portal",
        "editable_in_portal", "required_in_portal", "tag", "custom_field_options",
    },
    "ticket_forms": {"name", "display_name", "active", "end_user_visible", "ticket_field_ids"},
    "triggers": {"title", "active", "category_id", "conditions", "actions", "description"},
}
META_KEYS = {"key", "enabled", "decision_required", "append_field_keys", "notes"}
# Groups, ticket fields, and ticket forms do not have resource-specific OAuth
# scopes in Zendesk. Their admin endpoints require the global read/write scopes.
DEFAULT_OAUTH_SCOPE = "read write"


class ConfigError(RuntimeError):
    pass


class ApiError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return value


def secure_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def require_safe_subdomain(value: str) -> str:
    if not value or not all(c.islower() or c.isdigit() or c == "-" for c in value):
        raise ConfigError("Zendesk subdomain must contain only lowercase letters, digits, or hyphens")
    return value


@dataclass
class ZendeskClient:
    subdomain: str
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    scope: str = DEFAULT_OAUTH_SCOPE

    @classmethod
    def from_environment(cls, configured_subdomain: str | None = None) -> "ZendeskClient":
        configured = require_safe_subdomain(configured_subdomain or "")
        environment = os.environ.get("ZENDESK_SUBDOMAIN", "").strip()
        if environment and require_safe_subdomain(environment) != configured:
            raise ConfigError("ZENDESK_SUBDOMAIN does not match the tenant pinned by the desired config")
        subdomain = configured
        access_token = os.environ.get("ZENDESK_OAUTH_ACCESS_TOKEN", "")
        client_id = os.environ.get("ZENDESK_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.environ.get("ZENDESK_OAUTH_CLIENT_SECRET", "")
        scope = os.environ.get("ZENDESK_OAUTH_SCOPE", "").strip() or DEFAULT_OAUTH_SCOPE
        if not access_token and (not client_id or not client_secret):
            raise ConfigError(
                "Set ZENDESK_OAUTH_CLIENT_ID and ZENDESK_OAUTH_CLIENT_SECRET "
                "or a short-lived ZENDESK_OAUTH_ACCESS_TOKEN; never paste them into chat"
            )
        return cls(
            subdomain=subdomain,
            client_id=client_id,
            client_secret=client_secret,
            access_token=access_token,
            scope=scope,
        )

    @property
    def base_url(self) -> str:
        return f"https://{self.subdomain}.zendesk.com/api/v2"

    def _bearer_token(self) -> str:
        if self.access_token:
            return self.access_token
        data = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
        }).encode("utf-8")
        url = f"https://{self.subdomain}.zendesk.com/oauth/tokens"
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "addx-zendesk-admin-config/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            error.read()
            raise ApiError(f"Zendesk OAuth token endpoint returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ApiError(f"Zendesk OAuth token request failed: {error.reason}") from error
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise ApiError("Zendesk OAuth token response did not contain access_token")
        self.access_token = token
        return token

    def _request(self, method: str, path_or_url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("https://") else f"{self.base_url}/{path_or_url.lstrip('/')}"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != f"{self.subdomain}.zendesk.com":
            raise ApiError("Refusing Zendesk pagination URL outside the configured tenant")
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._bearer_token(),
            "User-Agent": "addx-zendesk-admin-config/1",
        }
        data = None
        if payload is not None:
            data = canonical(payload)
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            error.read()
            raise ApiError(f"Zendesk API returned HTTP {error.code} for {method} {parsed.path}") from error
        except urllib.error.URLError as error:
            raise ApiError(f"Zendesk API request failed for {method} {parsed.path}: {error.reason}") from error
        if not body:
            return {}
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ApiError("Zendesk API returned a non-object response")
        return result

    def get(self, path_or_url: str) -> dict[str, Any]:
        return self._request("GET", path_or_url)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", path, payload)

    def list_all(self, resource: str) -> list[dict[str, Any]]:
        plural, _, _ = RESOURCE_SPECS[resource]
        url: str | None = f"{plural}?per_page=100"
        result: list[dict[str, Any]] = []
        while url:
            page = self.get(url)
            items = page.get(plural, [])
            if not isinstance(items, list):
                raise ApiError(f"Zendesk response field {plural} is not a list")
            result.extend(item for item in items if isinstance(item, dict))
            next_page = page.get("next_page")
            url = next_page if isinstance(next_page, str) and next_page else None
        return result


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ConfigError("Only schema_version 1 is supported")
    require_safe_subdomain(str(config.get("tenant", {}).get("subdomain", "")))
    unresolved = config.get("unresolved_decisions", [])
    if not isinstance(unresolved, list):
        raise ConfigError("unresolved_decisions must be a list")
    seen_keys: set[str] = set()
    enabled_field_keys: set[str] = set()
    for resource in RESOURCE_SPECS:
        entries = config.get(resource, [])
        if not isinstance(entries, list):
            raise ConfigError(f"{resource} must be a list")
        identity_key = RESOURCE_SPECS[resource][2]
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigError(f"Every {resource} entry must be an object")
            key = entry.get("key")
            if not isinstance(key, str) or not key:
                raise ConfigError(f"Every {resource} entry needs a non-empty key")
            if key in seen_keys:
                raise ConfigError(f"Duplicate resource key: {key}")
            seen_keys.add(key)
            if entry.get("enabled", True):
                if entry.get("decision_required"):
                    raise ConfigError(f"Enabled resource {key} still requires a business decision")
                if not entry.get(identity_key):
                    raise ConfigError(f"Enabled resource {key} needs {identity_key}")
                if "__REQUIRED__" in json.dumps(entry, ensure_ascii=False):
                    raise ConfigError(f"Enabled resource {key} contains __REQUIRED__")
                if resource == "ticket_fields":
                    enabled_field_keys.add(key)
    for form in config.get("ticket_forms", []):
        if not form.get("enabled", True):
            continue
        unknown = set(form.get("append_field_keys", [])).difference(enabled_field_keys)
        if unknown:
            raise ConfigError(f"Form {form['key']} references disabled or unknown field keys: {sorted(unknown)}")


def snapshot(client: ZendeskClient) -> dict[str, Any]:
    return {
        "tenant": client.subdomain,
        "resources": {resource: client.list_all(resource) for resource in RESOURCE_SPECS},
    }


def desired_body(resource: str, entry: dict[str, Any]) -> dict[str, Any]:
    allowed = MANAGED_BODY_KEYS[resource]
    return copy.deepcopy({key: value for key, value in entry.items() if key in allowed and key not in META_KEYS})


def comparable(resource: str, body: dict[str, Any]) -> dict[str, Any]:
    keys = MANAGED_BODY_KEYS[resource]
    result = {key: copy.deepcopy(body[key]) for key in keys if key in body}
    if resource == "ticket_fields" and "custom_field_options" in result:
        normalized_options = []
        for option in result["custom_field_options"]:
            normalized = {key: option[key] for key in ("name", "value") if key in option}
            # Zendesk adds `default: false` to every tagger/multiselect option.
            # An omitted value and explicit false are semantically equivalent.
            if option.get("default") is True:
                normalized["default"] = True
            normalized_options.append(normalized)
        result["custom_field_options"] = normalized_options
    return result


def indexes(live: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {}
    resources = live["resources"]
    for resource, (_, _, identity_key) in RESOURCE_SPECS.items():
        output[resource] = {}
        for item in resources.get(resource, []):
            identity = str(item.get(identity_key, ""))
            if not identity:
                continue
            if identity in output[resource]:
                raise ConfigError(f"Multiple live {resource} resources share identity {identity!r}")
            output[resource][identity] = item
    return output


def build_plan(config: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    validate_config(config)
    live_indexes = indexes(live)
    field_key_to_title = {
        entry["key"]: entry["title"]
        for entry in config.get("ticket_fields", [])
        if entry.get("enabled", True) and entry.get("title")
    }
    operations: list[dict[str, Any]] = []
    for resource in RESOURCE_SPECS:
        _, _, identity_key = RESOURCE_SPECS[resource]
        for entry in config.get(resource, []):
            if not entry.get("enabled", True):
                continue
            name = str(entry[identity_key])
            existing = live_indexes[resource].get(name)
            after = desired_body(resource, entry)
            if resource == "ticket_forms":
                after["append_field_keys"] = copy.deepcopy(entry.get("append_field_keys", []))
            if existing is None:
                action = "create"
                before = None
            else:
                if resource == "ticket_fields" and existing.get("type") != after.get("type"):
                    raise ConfigError(
                        f"Ticket field {name!r} has immutable type {existing.get('type')!r}, "
                        f"not desired type {after.get('type')!r}; use a separately reviewed migration"
                    )
                before = comparable(resource, existing)
                candidate = comparable(resource, after)
                if resource == "ticket_forms":
                    requested_titles = {
                        field_key_to_title[key]
                        for key in entry.get("append_field_keys", [])
                        if key in field_key_to_title
                    }
                    requested_ids = {
                        int(live_indexes["ticket_fields"][title]["id"])
                        for title in requested_titles
                        if title in live_indexes["ticket_fields"]
                    }
                    unresolved_titles = requested_titles.difference(live_indexes["ticket_fields"])
                    current_ids = {int(value) for value in existing.get("ticket_field_ids", [])}
                    candidate.pop("append_field_keys", None)
                    changed = bool(unresolved_titles) or not requested_ids.issubset(current_ids) or any(
                        before.get(key) != value for key, value in candidate.items()
                    )
                else:
                    changed = any(before.get(key) != value for key, value in candidate.items())
                action = "update" if changed else "noop"
            operations.append({
                "action": action,
                "resource": resource,
                "key": entry["key"],
                "identity": name,
                "remote_id": existing.get("id") if existing else None,
                "before": before,
                "after": after,
            })
    plan = {
        "schema_version": 1,
        "tenant": config["tenant"]["subdomain"],
        "config_sha256": digest(config),
        "baseline_sha256": digest(live),
        "operations": operations,
        "summary": {
            action: sum(1 for operation in operations if operation["action"] == action)
            for action in ("create", "update", "noop")
        },
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def resolve_form_body(body: dict[str, Any], config: dict[str, Any], live: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    result = {key: copy.deepcopy(value) for key, value in body.items() if key != "append_field_keys"}
    key_to_title = {entry["key"]: entry["title"] for entry in config.get("ticket_fields", [])}
    title_index = indexes(live)["ticket_fields"]
    if existing:
        ids = list(existing.get("ticket_field_ids", []))
    else:
        default_form = next(
            (form for form in live["resources"]["ticket_forms"] if form.get("default") is True),
            None,
        )
        if not default_form:
            raise ConfigError("Creating a ticket form requires a live default form to preserve system fields")
        ids = list(default_form.get("ticket_field_ids", []))
    for key in body.get("append_field_keys", []):
        title = key_to_title.get(key)
        remote = title_index.get(title or "")
        if not remote:
            raise ConfigError(f"Form references ticket field key that is not available after field apply: {key}")
        field_id = int(remote["id"])
        if field_id not in ids:
            ids.append(field_id)
    result["ticket_field_ids"] = ids
    return result


def apply_plan(client: ZendeskClient, config: dict[str, Any], plan: dict[str, Any], confirmation: str, backup_dir: Path) -> None:
    stored_sha = plan.get("plan_sha256")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if not stored_sha or digest(unsigned) != stored_sha:
        raise ConfigError("Plan file integrity check failed")
    if confirmation != stored_sha:
        raise ConfigError("--confirm-plan-sha must exactly match plan_sha256")
    if plan.get("tenant") != config["tenant"]["subdomain"] or client.subdomain != plan.get("tenant"):
        raise ConfigError("Plan, desired config, and authenticated Zendesk tenant do not match")
    if digest(config) != plan.get("config_sha256"):
        raise ConfigError("Desired config changed after plan generation; generate a new plan")
    live = snapshot(client)
    if digest(live) != plan.get("baseline_sha256"):
        raise ConfigError("Zendesk state changed after plan generation; generate a new snapshot and plan")
    secure_write_json(backup_dir / f"pre-apply-{stored_sha[:12]}.json", live)
    for operation in plan["operations"]:
        if operation["action"] == "noop":
            continue
        resource = operation["resource"]
        plural, singular, _ = RESOURCE_SPECS[resource]
        body = copy.deepcopy(operation["after"])
        if resource == "ticket_forms":
            live = snapshot(client)
            existing = indexes(live)[resource].get(operation["identity"])
            body = resolve_form_body(body, config, live, existing)
        payload = {singular: body}
        if operation["action"] == "create":
            client.post(plural, payload)
        elif operation["action"] == "update":
            remote_id = operation.get("remote_id")
            if not remote_id:
                raise ConfigError(f"Update operation has no remote id: {operation['key']}")
            client.put(f"{plural}/{remote_id}", payload)
        else:
            raise ConfigError(f"Unsupported plan action: {operation['action']}")


def plan_has_drift(plan: dict[str, Any]) -> bool:
    return plan["summary"]["create"] > 0 or plan["summary"]["update"] > 0


def safe_summary(plan: dict[str, Any]) -> str:
    summary = plan["summary"]
    return (
        f"plan_sha256={plan['plan_sha256']} create={summary['create']} "
        f"update={summary['update']} noop={summary['noop']}"
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    auth = subparsers.add_parser("check-auth")
    auth.add_argument("--config", type=Path, required=True)
    snap = subparsers.add_parser("snapshot")
    snap.add_argument("--config", type=Path, required=True)
    snap.add_argument("--output", type=Path, required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--config", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--config", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--config", type=Path, required=True)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--confirm-plan-sha", required=True)
    apply.add_argument("--backup-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = read_json(args.config)
        validate_config(config)
        client = ZendeskClient.from_environment(config["tenant"]["subdomain"])
        if args.command == "check-auth":
            user = client.get("users/me").get("user", {})
            print(json.dumps({"tenant": client.subdomain, "authenticated": True, "role": user.get("role")}, sort_keys=True))
        elif args.command == "snapshot":
            live = snapshot(client)
            secure_write_json(args.output, live)
            print(f"snapshot_sha256={digest(live)} output={args.output}")
        elif args.command in {"plan", "verify"}:
            live = snapshot(client)
            plan = build_plan(config, live)
            if args.command == "plan":
                secure_write_json(args.output, plan)
                print(safe_summary(plan))
            else:
                print(safe_summary(plan))
                return 2 if plan_has_drift(plan) else 0
        elif args.command == "apply":
            plan = read_json(args.plan)
            apply_plan(client, config, plan, args.confirm_plan_sha, args.backup_dir)
            print(f"apply_complete plan_sha256={plan['plan_sha256']}")
        return 0
    except (ConfigError, ApiError, json.JSONDecodeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
