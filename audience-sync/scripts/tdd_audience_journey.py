"""Historical compatibility journey through the explicit legacy fixed CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "scripts" / "legacy_api.py"
_RUN_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$", re.ASCII)
_PRODUCT_SCOPE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$", re.ASCII)
class JourneyError(Exception):
    """Code-only failure safe to emit from the TDD harness."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise JourneyError("missing_test_input")
    return value


def _poll_settings() -> tuple[int, float]:
    try:
        attempts = int(os.environ.get("AUDIENCE_SYNC_TDD_POLL_ATTEMPTS", "8"))
        interval = float(os.environ.get("AUDIENCE_SYNC_TDD_POLL_INTERVAL_SECONDS", "1"))
    except ValueError:
        raise JourneyError("invalid_poll_settings") from None
    if not 1 <= attempts <= 20 or not 0 <= interval <= 30:
        raise JourneyError("invalid_poll_settings")
    return attempts, interval


def _invoke(
    operation: str,
    envelope: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    command = [sys.executable, str(API), operation]
    serialized = None
    if envelope is not None:
        command.append("--request-stdin")
        serialized = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(os.environ),
        input=serialized,
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise JourneyError("cli_invalid_output") from None
    if completed.returncode != 0:
        code = payload.get("error") if isinstance(payload, dict) else None
        raise JourneyError(code if isinstance(code, str) else "cli_request_failed")
    result = payload.get("result") if envelope is not None else payload
    if not isinstance(result, dict):
        raise JourneyError("cli_invalid_output")
    return result


def _capabilities(product_scope: str) -> dict[str, Any]:
    result = _invoke(
        "get_sync_capabilities",
        {"query": {"product_scope": product_scope}},
    )
    if (
        result.get("contract_revision") != "audience-sync-v2"
        or result.get("product_scope") != product_scope
        or result.get("create_audience_available") is not True
        or result.get("definition_available") is not True
        or result.get("materialization_available") is not True
        or result.get("exact_materialization_run_required") is not True
    ):
        raise JourneyError("incompatible_capabilities")
    return result


def _ready_destination(capabilities: dict[str, Any], provider_kind: str) -> dict[str, Any]:
    if provider_kind not in {"brevo", "mailchimp"}:
        raise JourneyError("invalid_provider_kind")
    if capabilities.get(provider_kind + "_effect_ready") is not True:
        raise JourneyError("provider_not_ready")
    destinations = [
        destination
        for destination in capabilities.get("destinations", [])
        if isinstance(destination, dict) and destination.get("kind") == provider_kind
    ]
    if len(destinations) != 1:
        raise JourneyError("destination_ambiguous")
    return destinations[0]


def _search(product_scope: str, name: str) -> list[dict[str, Any]]:
    result = _invoke(
        "search_audiences",
        {
            "body": {
                "product_scope": product_scope,
                "query": name,
                "limit": 20,
                "offset": 0,
            }
        },
    )
    items = result.get("items")
    if not isinstance(items, list):
        raise JourneyError("invalid_search_result")
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("product_scope") == product_scope
    ]


def _audience(
    product_scope: str,
    run_token: str,
    name: str,
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(matches) > 1:
        raise JourneyError("audience_ambiguous")
    if matches:
        audience = matches[0]
    else:
        created = _invoke(
            "create_audience",
            {
                "body": {
                    "name": name,
                    "product_scope": product_scope,
                    "audience_filter": {
                        "tenants": [product_scope],
                        "materialization_limit": 1,
                    },
                    "idempotency_key": "tdd." + run_token + ".create",
                }
            },
        )
        audience = created.get("audience")
    if not isinstance(audience, dict):
        raise JourneyError("invalid_audience")
    if audience.get("product_scope") != product_scope:
        raise JourneyError("audience_binding_mismatch")
    audience_filter = audience.get("audience_filter")
    if (
        not isinstance(audience_filter, dict)
        or audience_filter.get("tenants") != [product_scope]
        or audience_filter.get("materialization_limit") != 1
    ):
        raise JourneyError("audience_smoke_bound_missing")
    audience_hash = audience.get("audience_filter_hash")
    if not isinstance(audience_hash, str) or re.fullmatch(r"[a-f0-9]{64}", audience_hash) is None:
        raise JourneyError("audience_hash_missing")
    return audience


def _poll_materialization(
    product_scope: str,
    audience_id: str,
    audience_filter_hash: str,
    initial: dict[str, Any],
) -> dict[str, Any]:
    attempts, interval = _poll_settings()
    current = initial
    retained_request_id: Optional[str] = None
    for _attempt in range(attempts):
        materialization = current.get("materialization")
        if not isinstance(materialization, dict):
            raise JourneyError("invalid_materialization")
        status = materialization.get("status")
        if status == "succeeded":
            if (
                current.get("audience_id") != audience_id
                or current.get("product_scope") != product_scope
                or materialization.get("audience_filter_hash") != audience_filter_hash
                or (
                    retained_request_id is not None
                    and materialization.get("materialization_request_id")
                    != retained_request_id
                )
                or not isinstance(materialization.get("materialization_run_id"), str)
                or not isinstance(materialization.get("member_count"), int)
                or not 0 <= materialization["member_count"] <= 1
            ):
                raise JourneyError("materialization_binding_mismatch")
            return current
        if status in {"failed", "outcome_unknown"}:
            raise JourneyError("materialization_" + str(status))
        request_id = materialization.get("materialization_request_id")
        if not isinstance(request_id, str):
            raise JourneyError("materialization_request_id_missing")
        if retained_request_id is None:
            retained_request_id = request_id
        elif request_id != retained_request_id:
            raise JourneyError("materialization_binding_mismatch")
        if interval:
            time.sleep(interval)
        current = _invoke(
            "get_materialization",
            {
                "path": {"materialization_request_id": request_id},
                "query": {"product_scope": product_scope},
            },
        )
    raise JourneyError("materialization_outcome_unknown")


def _poll_sync(
    product_scope: str,
    audience_id: str,
    materialization_run_id: str,
    expected_member_count: int,
    destination_id: str,
    initial: dict[str, Any],
) -> dict[str, Any]:
    attempts, interval = _poll_settings()
    current = initial
    retained_request_id: Optional[str] = None
    for _attempt in range(attempts):
        status = current.get("status")
        if status == "succeeded":
            if (
                current.get("audience_id") != audience_id
                or current.get("product_scope") != product_scope
                or (
                    retained_request_id is not None
                    and current.get("sync_request_id") != retained_request_id
                )
                or current.get("materialization_run_id") != materialization_run_id
                or current.get("source_count") != expected_member_count
                or current.get("destination_id") != destination_id
            ):
                raise JourneyError("sync_binding_mismatch")
            return current
        if status in {"failed", "reconcile_required", "outcome_unknown"}:
            raise JourneyError("sync_" + str(status))
        request_id = current.get("sync_request_id")
        if not isinstance(request_id, str):
            raise JourneyError("sync_request_id_missing")
        if retained_request_id is None:
            retained_request_id = request_id
        elif request_id != retained_request_id:
            raise JourneyError("sync_binding_mismatch")
        if interval:
            time.sleep(interval)
        current = _invoke(
            "get_audience_sync",
            {
                "path": {"sync_request_id": request_id},
                "query": {"product_scope": product_scope},
            },
        )
    raise JourneyError("sync_outcome_unknown")


def run(mode: str) -> dict[str, Any]:
    product_scope = _required_environment("AUDIENCE_SYNC_TDD_PRODUCT_SCOPE")
    run_token = _required_environment("AUDIENCE_SYNC_TDD_RUN_TOKEN")
    if _PRODUCT_SCOPE.fullmatch(product_scope) is None or _RUN_TOKEN.fullmatch(run_token) is None:
        raise JourneyError("invalid_test_input")
    local = _invoke("capabilities")
    if (
        local.get("contract_revision") != "audience-sync-v2"
        or local.get("contract_state") != "published_operations"
        or len(local.get("operations", [])) != 9
    ):
        raise JourneyError("incompatible_local_adapter")
    capabilities = _capabilities(product_scope)
    name = "audience_sync_tdd_" + run_token.replace("-", "_")
    matches = _search(product_scope, name)
    if mode == "inspect":
        return {
            "audience_match_count": len(matches),
            "contract_revision": capabilities["contract_revision"],
            "mode": mode,
            "operation_count": len(local["operations"]),
            "product_scope": product_scope,
            "ready_destination_count": sum(
                1
                for item in capabilities.get("destinations", [])
                if isinstance(item, dict)
                and capabilities.get(str(item.get("kind")) + "_effect_ready") is True
            ),
        }

    provider_kind = _required_environment("AUDIENCE_SYNC_TDD_PROVIDER_KIND")
    _ready_destination(capabilities, provider_kind)
    audience = _audience(product_scope, run_token, name, matches)
    audience_id = audience.get("audience_id")
    audience_hash = audience.get("audience_filter_hash")
    if not isinstance(audience_id, str) or not isinstance(audience_hash, str):
        raise JourneyError("invalid_audience")
    preview = _invoke(
        "preview_audience",
        {"path": {"audience_id": audience_id}, "body": {"product_scope": product_scope}},
    )
    if (
        preview.get("audience_id") != audience_id
        or preview.get("product_scope") != product_scope
        or preview.get("audience_filter_hash") != audience_hash
        or not isinstance(preview.get("member_count"), int)
        or not 0 <= preview["member_count"] <= 1
    ):
        raise JourneyError("preview_binding_mismatch")
    materialization_envelope = {
        "path": {"audience_id": audience_id},
        "body": {
            "product_scope": product_scope,
            "expected_audience_filter_hash": audience_hash,
            "idempotency_key": "tdd." + run_token + ".materialize",
        },
    }
    materialization = _poll_materialization(
        product_scope,
        audience_id,
        audience_hash,
        _invoke("materialize_audience", materialization_envelope),
    )
    materialization_state = materialization["materialization"]
    fresh_capabilities = _capabilities(product_scope)
    destination = _ready_destination(fresh_capabilities, provider_kind)
    sync_envelope = {
        "body": {
            "product_scope": product_scope,
            "audience_id": audience_id,
            "materialization_run_id": materialization_state["materialization_run_id"],
            "expected_member_count": materialization_state["member_count"],
            "destination_id": destination["destination_id"],
            "idempotency_key": "tdd." + run_token + ".sync",
        }
    }
    synchronized = _poll_sync(
        product_scope,
        audience_id,
        materialization_state["materialization_run_id"],
        materialization_state["member_count"],
        destination["destination_id"],
        _invoke("sync_audience", sync_envelope),
    )
    return {
        "materialization_member_count": materialization_state["member_count"],
        "materialization_status": materialization_state["status"],
        "mode": mode,
        "operation_count": len(local["operations"]),
        "product_scope": product_scope,
        "provider_kind": synchronized.get("provider_kind"),
        "provider_resource_url": synchronized.get("provider_resource_url"),
        "sync_applied_count": synchronized.get("applied_count"),
        "sync_status": synchronized["status"],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inspect", "full"))
    arguments = parser.parse_args(argv)
    try:
        result = run(arguments.mode)
    except (JourneyError, subprocess.TimeoutExpired) as exc:
        code = str(exc) if isinstance(exc, JourneyError) else "cli_timeout"
        print(json.dumps({"error": code, "ok": False}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
