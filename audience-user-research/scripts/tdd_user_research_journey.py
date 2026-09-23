"""Deterministic cold-start journey through the shipped Personal API client.

This is an acceptance driver, not a workflow engine. It keeps the exact IDs
returned by Platform, stops on an unknown mutation outcome, and never sends a
campaign. Ordinary CLI stdout is projected, so this harness reads the client
DTO when it must check link uid binding. The surrounding test copies the whole
Skill and runs this file from an unrelated working directory with only a
securely injected Personal key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "scripts" / "api.py"
sys.path.insert(0, str(ROOT / "src"))

from user_research import (  # noqa: E402
    AudienceClient,
    AudienceClientConfig,
    Operation,
    SafeApiError,
)
from user_research.operations import SELF_CONTEXT_OPERATION  # noqa: E402

_RUN_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$", re.ASCII)
_TERMINAL_FAILURE = frozenset({"failed", "reconcile_required", "cancelled"})


class JourneyError(Exception):
    """A code-only failure that is safe to print from this harness."""


def _required_text(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    if not value or len(value) > 128:
        raise JourneyError("invalid_test_input")
    return value


def _poll_settings() -> tuple[int, float]:
    try:
        attempts = int(os.environ.get("USER_RESEARCH_TDD_POLL_ATTEMPTS", "120"))
        interval = float(os.environ.get("USER_RESEARCH_TDD_POLL_INTERVAL_SECONDS", "5"))
    except ValueError:
        raise JourneyError("invalid_poll_settings") from None
    if not 1 <= attempts <= 240 or not 0 <= interval <= 30:
        raise JourneyError("invalid_poll_settings")
    return attempts, interval


def _trusted_resource_url(
    value: Any, expected_host: str, *, fragment_allowed: bool = False
) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == expected_host
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.query
        and (fragment_allowed or not parsed.fragment)
    )


def _invoke(
    operation: str,
    *,
    path: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        client = AudienceClient(AudienceClientConfig.from_environment())
        selected = Operation(operation)
        if selected == SELF_CONTEXT_OPERATION:
            result = client.verify_self_context()
        else:
            context = client.verify_self_context()
            supplied = (path or {}).get("project_id")
            if supplied not in (None, context["project_id"]):
                raise SafeApiError("project_binding_mismatch")
            result = client.call(
                selected,
                path=path,
                query=query,
                body=body,
            )
    except SafeApiError as exc:
        raise JourneyError(exc.code) from None
    except ValueError:
        raise JourneyError("invalid_arguments") from None
    if not isinstance(result, dict):
        raise JourneyError("cli_invalid_output")
    return result


def _capability_operations() -> frozenset[str]:
    try:
        completed = subprocess.run(
            [sys.executable, str(API), "capabilities"],
            cwd=ROOT,
            env=dict(os.environ),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise JourneyError("cli_timeout") from None
    try:
        payload = json.loads(completed.stdout)
        operations = payload.get("operations")
    except (json.JSONDecodeError, AttributeError):
        raise JourneyError("cli_invalid_output") from None
    if completed.returncode != 0 or not isinstance(operations, list):
        raise JourneyError("capability_discovery_failed")
    if any(not isinstance(item, str) for item in operations):
        raise JourneyError("capability_discovery_failed")
    return frozenset(operations)


def _one_operation(available: frozenset[str], *names: str) -> str:
    matches = [name for name in names if name in available]
    if len(matches) != 1:
        raise JourneyError("required_operation_unavailable")
    return matches[0]


def _comparable_voc_configuration(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    configuration = dict(value)
    brief_value = configuration.get("voc_brief")
    if not isinstance(brief_value, dict):
        return configuration
    brief = dict(brief_value)
    time_range_value = brief.get("time_range")
    if not isinstance(time_range_value, dict):
        return configuration
    time_range = dict(time_range_value)
    for stored_name, public_name in (("from_date", "from"), ("to_date", "to")):
        if stored_name not in time_range:
            continue
        if public_name in time_range:
            return None
        time_range[public_name] = time_range.pop(stored_name)
    brief["time_range"] = time_range
    configuration["voc_brief"] = brief
    return configuration


def _context(available: frozenset[str]) -> tuple[str, str]:
    operation = _one_operation(available, "get_project_personal_key_context")
    context = _invoke(operation)
    project_id = context.get("project_id")
    binding_revision = context.get("binding_revision")
    allowed = context.get("allowed_actions")
    required = {
        "idea.write",
        "research.prepare",
        "research.materialize",
        "forms.create",
    }
    if (
        not isinstance(project_id, str)
        or not isinstance(binding_revision, str)
        or not isinstance(allowed, list)
        or not required.issubset(set(allowed))
    ):
        raise JourneyError("insufficient_project_authority")
    return project_id, binding_revision


def _readiness(available: frozenset[str], project_id: str, binding_revision: str) -> dict[str, Any]:
    result = _invoke(
        _one_operation(available, "personal_research_readiness"),
        path={"project_id": project_id},
    )
    if (
        result.get("project_id") != project_id
        or result.get("binding_revision") != binding_revision
        or result.get("mapping") not in {"configured", "missing"}
        or result.get("warehouse") not in {"available", "no_rows", "not_checked", "query_error"}
    ):
        raise JourneyError("invalid_research_readiness")
    return result


def _create_idea(
    available: frozenset[str], project_id: str, binding_revision: str, run_token: str
) -> str:
    operation = _one_operation(available, "personal_idea_create")
    result = _invoke(
        operation,
        path={"project_id": project_id},
        body={
            "title": f"User Research TDD {run_token}",
            "description": "Disposable unsent cold-start journey",
            "idempotency_key": f"idea-{run_token}-0001",
        },
    )
    if result.get("project_id") != project_id or result.get("binding_revision") != binding_revision:
        raise JourneyError("idea_binding_mismatch")
    resource = result.get("resource")
    idea_id = resource.get("idea_id") if isinstance(resource, dict) else None
    if not isinstance(idea_id, str):
        raise JourneyError("invalid_idea_result")
    return idea_id


def _run_optional_voc(
    available: frozenset[str],
    project_id: str,
    idea_id: str,
    binding_revision: str,
    run_token: str,
) -> tuple[str, str]:
    capabilities = _invoke(
        _one_operation(available, "personal_voc_capabilities"),
        path={"project_id": project_id},
    )
    default = capabilities.get("default")
    if (
        capabilities.get("project_id") != project_id
        or capabilities.get("binding_revision") != binding_revision
        or not isinstance(default, dict)
    ):
        raise JourneyError("voc_capability_binding_mismatch")
    markets = default.get("markets")
    strategies = default.get("source_strategies")
    collection_bound = default.get("collection_bound")
    if (
        not isinstance(markets, list)
        or not markets
        or not isinstance(strategies, list)
        or not strategies
        or collection_bound not in {"focused", "standard"}
    ):
        raise JourneyError("voc_capability_invalid")

    expected_minimal_input = "Understand recent activation blockers"
    expected_configuration = {
        "decision_goal": "Choose the next activation improvement",
        "assumptions": ["Recent public discussions may reveal user language"],
        "plan": None,
        "voc_brief": {
            "schema_version": 1,
            "question": "What blocks users from reaching initial value?",
            "markets": markets,
            "source_strategies": strategies,
            "keywords": ["activation", "onboarding"],
            "time_range": {"from": "2026-01-01", "to": "2026-09-15"},
            "collection_bound": collection_bound,
        },
    }
    configuration = _invoke(
        _one_operation(available, "personal_action_configuration_get"),
        path={"project_id": project_id, "idea_id": idea_id, "action_kind": "voc"},
    )
    resource = configuration.get("resource")
    if (
        configuration.get("project_id") != project_id
        or configuration.get("binding_revision") != binding_revision
        or not isinstance(resource, dict)
        or resource.get("idea_id") != idea_id
        or resource.get("kind") != "voc"
        or isinstance(resource.get("configuration_revision"), bool)
        or not isinstance(resource.get("configuration_revision"), int)
    ):
        raise JourneyError("voc_configuration_binding_mismatch")
    current_revision = resource["configuration_revision"]
    current_hash = resource.get("content_hash")
    if current_revision == 0 and current_hash is None:
        written = _invoke(
            _one_operation(available, "personal_action_configuration_put"),
            path={"project_id": project_id, "idea_id": idea_id, "action_kind": "voc"},
            body={
                "expected_configuration_revision": current_revision,
                "expected_content_hash": current_hash,
                "minimal_input": expected_minimal_input,
                "configuration": expected_configuration,
            },
        )
        written_resource = written.get("resource")
        if (
            written.get("project_id") != project_id
            or written.get("binding_revision") != binding_revision
            or not isinstance(written_resource, dict)
            or written_resource.get("idea_id") != idea_id
            or written_resource.get("kind") != "voc"
            or written_resource.get("minimal_input") != expected_minimal_input
            or _comparable_voc_configuration(written_resource.get("configuration"))
            != expected_configuration
        ):
            raise JourneyError("voc_configuration_binding_mismatch")
    elif (
        current_revision >= 1
        and isinstance(current_hash, str)
        and resource.get("minimal_input") == expected_minimal_input
        and _comparable_voc_configuration(resource.get("configuration")) == expected_configuration
    ):
        written_resource = resource
    else:
        raise JourneyError("voc_configuration_requires_review")
    revision = written_resource.get("configuration_revision")
    content_hash = written_resource.get("content_hash")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(content_hash, str)
    ):
        raise JourneyError("voc_configuration_binding_mismatch")

    started = _invoke(
        _one_operation(available, "personal_voc_execution_start"),
        path={"project_id": project_id, "idea_id": idea_id},
        body={
            "expected_configuration_revision": revision,
            "expected_content_hash": content_hash,
            "idempotency_key": f"voc-{run_token}-0001",
        },
    )
    execution = started.get("execution")
    platform_run_id = execution.get("platform_run_id") if isinstance(execution, dict) else None
    execution_status = execution.get("status") if isinstance(execution, dict) else None
    if (
        started.get("project_id") != project_id
        or started.get("binding_revision") != binding_revision
        or started.get("idea_id") != idea_id
        or not isinstance(platform_run_id, str)
        or not isinstance(execution.get("idempotent"), bool)
    ):
        raise JourneyError("voc_execution_binding_mismatch")
    if execution_status in {"failed", "cancelled"}:
        raise JourneyError("voc_execution_failed")
    if execution_status not in {"queued", "running", "reconciling", "succeeded", "partial"}:
        raise JourneyError("voc_execution_invalid")

    attempts, interval = _poll_settings()
    for attempt in range(attempts):
        observed = _invoke(
            _one_operation(available, "personal_voc_execution_get"),
            path={
                "project_id": project_id,
                "idea_id": idea_id,
                "platform_run_id": platform_run_id,
            },
        )
        result = observed.get("result")
        if (
            observed.get("project_id") != project_id
            or observed.get("binding_revision") != binding_revision
            or observed.get("idea_id") != idea_id
            or observed.get("platform_run_id") != platform_run_id
            or not isinstance(result, dict)
        ):
            raise JourneyError("voc_execution_binding_mismatch")
        status = result.get("status")
        if status in {"succeeded", "partial"}:
            return platform_run_id, status
        if status in _TERMINAL_FAILURE:
            raise JourneyError("voc_execution_failed")
        if status not in {"queued", "running", "reconciling"}:
            raise JourneyError("voc_execution_invalid")
        if attempt + 1 < attempts and interval:
            time.sleep(interval)
    raise JourneyError("voc_execution_timeout")


def _create_research(
    available: frozenset[str], project_id: str, idea_id: str, run_token: str
) -> tuple[str, str]:
    operation = _one_operation(available, "personal_research_journey_create")
    result = _invoke(
        operation,
        path={"project_id": project_id},
        body={"idea_id": idea_id, "idempotency_key": f"research-{run_token}-0001"},
    )
    research_id = result.get("research_id")
    revision = result.get("binding_revision")
    if (
        result.get("project_id") != project_id
        or result.get("idea_id") != idea_id
        or not isinstance(research_id, str)
        or not isinstance(revision, str)
    ):
        raise JourneyError("research_binding_mismatch")
    return research_id, revision


def _create_form(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    binding_revision: str,
    run_token: str,
) -> tuple[str, str]:
    operation = _one_operation(available, "personal_research_journey_form")
    native_body = {
        "title": f"User Research TDD {run_token}",
        "type": "form",
        "settings": {"is_public": True},
        "hidden": ["uid", "research_id", "batch"],
        "fields": [
            {
                "title": "Would you recommend this product?",
                "ref": f"recommend-{run_token}",
                "type": "yes_no",
            }
        ],
    }
    result = _invoke(
        operation,
        path={"project_id": project_id, "research_id": research_id},
        body={"idempotency_key": f"form-{run_token}-0001", "body": native_body},
    )
    form_id = result.get("form_id")
    fingerprint = result.get("definition_fingerprint")
    if (
        result.get("project_id") != project_id
        or result.get("research_id") != research_id
        or result.get("binding_revision") != binding_revision
        or not isinstance(form_id, str)
        or not isinstance(fingerprint, str)
        or not _trusted_resource_url(result.get("form_url"), "form.typeform.com")
    ):
        raise JourneyError("form_binding_mismatch")
    return form_id, fingerprint


def _research_capabilities(
    available: frozenset[str], project_id: str, binding_revision: str
) -> dict[str, Any]:
    operation = _one_operation(available, "personal_research_query_capabilities")
    result = _invoke(operation, path={"project_id": project_id})
    resource = result.get("resource")
    if (
        result.get("project_id") != project_id
        or result.get("binding_revision") != binding_revision
        or result.get("enabled") is not True
        or not isinstance(resource, dict)
        or resource.get("structured_criteria_available") is not True
        or resource.get("materialization_available") is not True
    ):
        raise JourneyError("research_population_not_ready")
    return resource


def _selection(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    registry_version: str,
    relation_id: str,
    binding_revision: str,
    run_token: str,
    sample_size: int | None,
) -> tuple[str, int]:
    result = _invoke(
        _one_operation(available, "personal_research_prepare_selection"),
        path={"project_id": project_id, "research_id": research_id},
        body={
            "idempotency_key": f"selection-{run_token}-0001",
            "criteria": {
                "schema_version": "audience-criteria-v1",
                "registry_version": registry_version,
                "where": {
                    "kind": "field",
                    "relation_id": relation_id,
                    "field_id": "country",
                    "operator": "eq",
                    "value": "SG",
                },
            },
        },
    )
    selection_id = result.get("approved_selection_id")
    count = result.get("expected_count")
    if (
        result.get("project_id") != project_id
        or result.get("research_id") != research_id
        or result.get("binding_revision") != binding_revision
        or not isinstance(selection_id, str)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
        or (sample_size is None and count > 5)
    ):
        raise JourneyError("selection_binding_or_count_mismatch")
    return selection_id, count


def _country_relation_id(resource: dict[str, Any]) -> str:
    relations = resource.get("relations")
    if not isinstance(relations, list):
        raise JourneyError("research_population_not_ready")
    matches: list[str] = []
    for relation in relations:
        if not isinstance(relation, dict) or not isinstance(relation.get("fields"), list):
            continue
        relation_id = relation.get("relation_id")
        if not isinstance(relation_id, str):
            continue
        for field in relation["fields"]:
            if (
                isinstance(field, dict)
                and field.get("field_id") == "country"
                and field.get("value_type") == "string"
                and isinstance(field.get("operators"), list)
                and "eq" in field["operators"]
            ):
                matches.append(relation_id)
                break
    if len(matches) != 1:
        raise JourneyError("research_population_not_ready")
    return matches[0]


def _poll_operation(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    request_id: str,
    operation_name: str,
    binding_revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts, interval = _poll_settings()
    for attempt in range(attempts):
        status = _invoke(
            _one_operation(available, "personal_research_journey_status"),
            path={"project_id": project_id, "research_id": research_id},
        )
        if (
            status.get("project_id") != project_id
            or status.get("research_id") != research_id
            or status.get("binding_revision") != binding_revision
        ):
            raise JourneyError("journey_binding_mismatch")
        requests = [
            item
            for item in status.get("requests", [])
            if isinstance(item, dict) and item.get("request_id") == request_id
        ]
        receipts = [
            item
            for item in status.get("receipts", [])
            if isinstance(item, dict) and item.get("request_id") == request_id
        ]
        if len(requests) != 1:
            raise JourneyError("request_binding_mismatch")
        request = requests[0]
        if request.get("operation") != operation_name:
            raise JourneyError("request_binding_mismatch")
        revision = request.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise JourneyError("request_binding_mismatch")
        state = request.get("state")
        if state == "succeeded":
            if (
                len(receipts) != 1
                or receipts[0].get("state") != "succeeded"
                or receipts[0].get("operation") != operation_name
                or receipts[0].get("revision") != revision
            ):
                raise JourneyError("receipt_binding_mismatch")
            return status, receipts[0]
        if state in _TERMINAL_FAILURE:
            raise JourneyError(f"{operation_name}_failed")
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise JourneyError(f"{operation_name}_outcome_unknown")


def _request_operation(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    binding_revision: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _invoke(
        _one_operation(available, "personal_research_journey_operation"),
        path={"project_id": project_id, "research_id": research_id},
        body=body,
    )
    request_id = result.get("request_id")
    response_source_run_id = result.get("source_materialization_run_id")
    expected_source_run_id = body.get("source_materialization_run_id")
    if (
        result.get("project_id") != project_id
        or result.get("binding_revision") != binding_revision
        or result.get("operation") != body["operation"]
        or result.get("expected_count") != body.get("expected_count")
        or (expected_source_run_id is not None and response_source_run_id != expected_source_run_id)
        or (
            expected_source_run_id is None
            and response_source_run_id is not None
            and (not isinstance(response_source_run_id, str) or not response_source_run_id)
        )
        or not isinstance(request_id, str)
    ):
        raise JourneyError("request_binding_mismatch")
    status, receipt = _poll_operation(
        available,
        project_id,
        research_id,
        request_id,
        body["operation"],
        binding_revision,
    )
    if (
        expected_source_run_id is None
        and response_source_run_id is not None
        and response_source_run_id != receipt.get("data_run_id")
    ):
        raise JourneyError("request_binding_mismatch")
    return status, receipt


def _links(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    form_id: str,
    batch: str,
    binding_revision: str,
) -> int:
    result = _invoke(
        _one_operation(available, "personal_research_journey_links"),
        path={"project_id": project_id, "research_id": research_id},
        query={"form_id": form_id, "batch": batch, "offset": "0", "limit": "5"},
    )
    if (
        result.get("project_id") != project_id
        or result.get("binding_revision") != binding_revision
        or not isinstance(result.get("items"), list)
        or result.get("total") != len(result.get("items", []))
    ):
        raise JourneyError("link_binding_mismatch")
    for item in result["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise JourneyError("link_binding_mismatch")
        parsed = urlsplit(item["url"])
        values = parse_qs(parsed.fragment)
        uid = item.get("uid")
        if (
            not _trusted_resource_url(item["url"], "form.typeform.com", fragment_allowed=True)
            or parsed.path != f"/to/{form_id}"
            or not isinstance(uid, str)
            or re.fullmatch(r"[a-f0-9]{32}", uid) is None
            or values.get("uid") != [uid]
            or values.get("research_id") != [research_id]
            or values.get("batch") != [batch]
        ):
            raise JourneyError("link_binding_mismatch")
    return len(result["items"])


def _campaign(
    available: frozenset[str],
    project_id: str,
    research_id: str,
    run_id: str,
    list_id: str,
    run_token: str,
    binding_revision: str,
) -> str:
    result = _invoke(
        _one_operation(available, "personal_research_journey_campaign_draft"),
        path={"project_id": project_id, "research_id": research_id},
        body={
            "idempotency_key": f"campaign-{run_token}-0001",
            "source_materialization_run_id": run_id,
            "body": {
                "name": f"User Research TDD {run_token}",
                "subject": "Share your feedback",
                "htmlContent": '<a href="{{ contact.SURVEY_URL }}">Open questionnaire</a>',
            },
        },
    )
    campaign_id = result.get("campaign_id")
    if (
        result.get("project_id") != project_id
        or result.get("research_id") != research_id
        or result.get("binding_revision") != binding_revision
        or result.get("source_materialization_run_id") != run_id
        or result.get("list_id") != list_id
        or not isinstance(campaign_id, str)
        or not _trusted_resource_url(result.get("campaign_url"), "app.brevo.com")
    ):
        raise JourneyError("campaign_binding_mismatch")
    status = _invoke(
        _one_operation(available, "personal_research_journey_status"),
        path={"project_id": project_id, "research_id": research_id},
    )
    if status.get("binding_revision") != binding_revision:
        raise JourneyError("campaign_binding_mismatch")
    bindings = [
        item
        for item in status.get("bindings", [])
        if isinstance(item, dict) and item.get("campaign_id") == campaign_id
    ]
    if (
        len(bindings) != 1
        or bindings[0].get("source_materialization_run_id") != run_id
        or bindings[0].get("list_id") != list_id
    ):
        raise JourneyError("campaign_binding_mismatch")
    return campaign_id


def run(mode: str, sample_size: int | None = None) -> dict[str, Any]:
    if sample_size is not None and (
        sample_size < 1 or sample_size > 5 or mode == "questionnaire-only"
    ):
        raise JourneyError("invalid_sample_size")
    available = _capability_operations()
    project_id, binding_revision = _context(available)
    readiness = _readiness(available, project_id, binding_revision)
    if readiness["warehouse"] == "query_error":
        raise JourneyError("research_readiness_query_failed")
    run_token = _required_text("USER_RESEARCH_TDD_RUN_TOKEN", "coldstart")
    if _RUN_TOKEN.fullmatch(run_token) is None:
        raise JourneyError("invalid_test_input")

    idea_id = _create_idea(available, project_id, binding_revision, run_token)
    voc_run_id: str | None = None
    voc_status = "skipped_optional"
    if mode == "full":
        voc_run_id, voc_status = _run_optional_voc(
            available, project_id, idea_id, binding_revision, run_token
        )
    research_id, research_binding = _create_research(available, project_id, idea_id, run_token)
    if research_binding != binding_revision:
        raise JourneyError("research_binding_mismatch")
    form_id, _fingerprint = _create_form(
        available, project_id, research_id, binding_revision, run_token
    )
    result: dict[str, Any] = {
        "project_id": project_id,
        "research_id": research_id,
        "form_id": form_id,
        "questionnaire_created": True,
        "campaign_sent": False,
    }
    if mode == "questionnaire-only":
        return result

    if readiness["mapping"] != "configured" or readiness["warehouse"] != "available":
        raise JourneyError("research_population_not_ready")

    resource = _research_capabilities(available, project_id, binding_revision)
    registry_version = resource.get("registry_version")
    if not isinstance(registry_version, str):
        raise JourneyError("research_population_not_ready")
    relation_id = _country_relation_id(resource)
    selection_id, count = _selection(
        available,
        project_id,
        research_id,
        registry_version,
        relation_id,
        binding_revision,
        run_token,
        sample_size,
    )
    if sample_size is not None and sample_size > count:
        raise JourneyError("sample_size_exceeds_selection")
    materialization_count = sample_size if sample_size is not None else count
    materialize_body = {
        "operation": "materialize",
        "idempotency_key": f"materialize-{run_token}-0001",
        "approved_selection_id": selection_id,
        "expected_count": materialization_count,
    }
    if sample_size is not None:
        materialize_body["sample_size"] = sample_size
    materialized, materialize_receipt = _request_operation(
        available,
        project_id,
        research_id,
        binding_revision,
        materialize_body,
    )
    run_id = materialize_receipt.get("data_run_id")
    if (
        materialize_receipt.get("expected_count") != materialization_count
        or materialize_receipt.get("selected_count") != materialization_count
        or materialize_receipt.get("form_id") != form_id
        or not isinstance(run_id, str)
    ):
        raise JourneyError("materialize_binding_mismatch")
    batch = run_id
    link_count = _links(available, project_id, research_id, form_id, batch, binding_revision)
    if link_count != materialization_count:
        raise JourneyError("link_count_mismatch")

    synchronized, sync_receipt = _request_operation(
        available,
        project_id,
        research_id,
        binding_revision,
        {
            "operation": "sync_brevo",
            "idempotency_key": f"sync-{run_token}-0001",
            "source_materialization_run_id": run_id,
            "expected_count": materialization_count,
        },
    )
    list_id = sync_receipt.get("list_id")
    if (
        sync_receipt.get("source_materialization_run_id") != run_id
        or sync_receipt.get("expected_count") != materialization_count
        or sync_receipt.get("selected_count") != materialization_count
        or sync_receipt.get("form_id") != form_id
        or not isinstance(list_id, str)
    ):
        raise JourneyError("sync_binding_mismatch")
    campaign_id = _campaign(
        available,
        project_id,
        research_id,
        run_id,
        list_id,
        run_token,
        binding_revision,
    )
    result.update(
        {
            "materialization_status": "succeeded",
            "materialization_member_count": materialization_count,
            "links_count": materialization_count,
            "brevo_sync_status": "succeeded",
            "campaign_draft_created": True,
            "campaign_id": campaign_id,
            "voc_status": voc_status,
            "voc_platform_run_id": voc_run_id,
            "request_count": len(materialized.get("requests", []))
            + len(synchronized.get("requests", [])),
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("full", "full-no-voc", "questionnaire-only"), nargs="?", default="full"
    )
    parser.add_argument("--sample-size", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        payload = {"ok": True, "result": run(args.mode, args.sample_size)}
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return 0
    except JourneyError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
