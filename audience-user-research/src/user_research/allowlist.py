"""Build the closed User Research operation registry from bundled OpenAPI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Final, Literal

HttpMethod = Literal["GET", "POST", "PUT", "PATCH"]
_SOURCE_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
_CONTRACT_NAMES: Final = {
    "v1": "audience-platform-public-v1.openapi.json",
    "v2": "audience-platform-public-v2.openapi.json",
    "v3": "project-control-plane.openapi.json",
}


@dataclass(frozen=True)
class ReviewedRoute:
    version: str
    method: HttpMethod
    path: str
    operation_id: str
    label: str
    required_action: str | None = None
    response_mode: str = "json"


def _route(version, method, path, operation_id, label=None, action=None, response_mode="json"):
    return ReviewedRoute(
        version, method, path, operation_id, label or operation_id, action, response_mode
    )


V3 = "/api/platform/v3/projects/{project_id}"
RESEARCH = V3 + "/research"
JOURNEY = RESEARCH + "/{research_id}/journey"

# Exact route + operationId pairs are reviewed. A new OpenAPI operation never
# enters the Agent inventory merely because the bundled snapshot changed.
REVIEWED_ROUTES: Final = (
    _route("v3", "GET", "/api/platform/v3/personal-key", "get_project_personal_key_context"),
    _route(
        "v3",
        "GET",
        V3 + "/ideas",
        "list_ideas_api_platform_v3_projects__project_id__ideas_get",
        "personal_idea_list",
        "idea.read",
    ),
    _route(
        "v3",
        "POST",
        V3 + "/ideas",
        "create_idea_api_platform_v3_projects__project_id__ideas_post",
        "personal_idea_create",
        "idea.write",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}",
        "get_idea_api_platform_v3_projects__project_id__ideas__idea_id__get",
        "personal_idea_get",
        "idea.read",
    ),
    _route(
        "v3",
        "PATCH",
        V3 + "/ideas/{idea_id}",
        "patch_idea_api_platform_v3_projects__project_id__ideas__idea_id__patch",
        "personal_idea_update",
        "idea.write",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/actions/{action_kind}/configuration",
        "personal_get_configuration_api_platform_v3_projects__project_id__ideas__idea_id__actions__action_kind__configuration_get",
        "personal_action_configuration_get",
        "idea.read",
    ),
    _route(
        "v3",
        "PUT",
        V3 + "/ideas/{idea_id}/actions/{action_kind}/configuration",
        "personal_put_configuration_api_platform_v3_projects__project_id__ideas__idea_id__actions__action_kind__configuration_put",
        "personal_action_configuration_put",
        "research.prepare",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/actions/voc/capabilities",
        "get_project_voc_capabilities_api_platform_v3_projects__project_id__actions_voc_capabilities_get",
        "personal_voc_capabilities",
        "voc.collect",
    ),
    _route(
        "v3",
        "POST",
        V3 + "/ideas/{idea_id}/actions/voc/executions",
        "start_project_voc_execution_api_platform_v3_projects__project_id__ideas__idea_id__actions_voc_executions_post",
        "personal_voc_execution_start",
        "voc.collect",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/actions/voc/executions/{platform_run_id}",
        "get_project_voc_execution_api_platform_v3_projects__project_id__ideas__idea_id__actions_voc_executions__platform_run_id__get",
        "personal_voc_execution_get",
        "voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/providers/apify/actors",
        "project_apify_actor_search",
        action="voc.collect",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/providers/apify/actors/{actor_id}",
        "project_apify_actor_detail",
        action="voc.collect",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/providers/apify/actors/{actor_id}/input-schema",
        "project_apify_actor_schema",
        action="voc.collect",
    ),
    _route(
        "v3",
        "POST",
        V3 + "/ideas/{idea_id}/voc/runs",
        "project_voc_native_start",
        action="voc.collect",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc",
        "project_voc_discovery",
        action="voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc/runs/{request_id}",
        "project_voc_native_read",
        action="voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc/{voc_id}/datasets",
        "project_voc_dataset_list",
        action="voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc/{voc_id}/datasets/{dataset_id}",
        "project_voc_dataset_metadata",
        action="voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc/{voc_id}/datasets/{dataset_id}/items",
        "project_voc_dataset_items",
        action="voc.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/voc/{voc_id}/datasets/{dataset_id}/export",
        "project_voc_dataset_export",
        action="voc.results.read",
        response_mode="attachment",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/audience-sync/capabilities",
        "capabilities_api_platform_v3_projects__project_id__audience_sync_capabilities_get",
        "personal_audience_sync_capabilities",
        "audience_sync.capabilities.read",
    ),
    _route("v3", "GET", RESEARCH, "personal_research_journey_list", action="research.results.read"),
    _route("v3", "POST", RESEARCH, "personal_research_journey_create", action="research.prepare"),
    _route(
        "v3",
        "PATCH",
        RESEARCH + "/{research_id}",
        "personal_research_journey_rename",
        action="research.prepare",
    ),
    _route(
        "v3",
        "PATCH",
        V3 + "/ideas/{idea_id}/title",
        "project_rename_idea",
        action="idea.write",
    ),
    _route(
        "v3",
        "PATCH",
        RESEARCH + "/{research_id}/title",
        "project_rename_research",
        action="research.prepare",
    ),
    _route(
        "v3",
        "PATCH",
        V3 + "/research-audience-assets/{asset_id}/title",
        "project_rename_research_audience",
        action="research.prepare",
    ),
    _route(
        "v3",
        "PATCH",
        V3 + "/ideas/{idea_id}/voc/{voc_id}/title",
        # OpenAPI operationId. The Agent-facing label is the next argument.
        "project_rename_voc",
        "personal_voc_rename",
        "voc.collect",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/report-sources/current",
        "current_project_report_source_api_platform_v3_projects__project_id__report_sources_current_get",
        "project_get_report_source",
        "report.publish",
    ),
    _route(
        "v3",
        "POST",
        V3 + "/reports",
        "publish_project_report_api_platform_v3_projects__project_id__reports_post",
        "project_publish_report",
        "report.publish",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/reports/current",
        "current_public_project_report_api_platform_v3_projects__project_id__reports_current_get",
        "project_get_current_report",
        "report.publish",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/reports/current/download",
        "project_report_download",
        action="report.publish",
        response_mode="attachment",
    ),
    _route(
        "v3",
        "GET",
        RESEARCH + "/readiness",
        "personal_research_readiness",
        action="research.prepare",
    ),
    _route(
        "v3",
        "GET",
        RESEARCH + "/query-capabilities",
        "personal_research_query_capabilities",
        action="research.prepare",
    ),
    _route(
        "v3",
        "POST",
        RESEARCH + "/{research_id}/selections",
        "personal_research_prepare_selection",
        action="research.prepare",
    ),
    _route(
        "v3", "GET", JOURNEY, "personal_research_journey_status", action="research.results.read"
    ),
    _route(
        "v3",
        "POST",
        JOURNEY + "/operations",
        "personal_research_journey_operation",
        action="research.materialize",
    ),
    _route(
        "v3", "POST", JOURNEY + "/form", "personal_research_journey_form", action="forms.create"
    ),
    _route(
        "v3",
        "POST",
        JOURNEY + "/form/publish",
        "personal_research_journey_form_publish",
        action="forms.create",
    ),
    _route(
        "v3",
        "POST",
        JOURNEY + "/campaign-draft",
        "personal_research_journey_campaign_draft",
        action="research.prepare",
    ),
    _route(
        "v3",
        "GET",
        JOURNEY + "/links",
        "personal_research_journey_links",
        action="research.results.read",
    ),
    _route(
        "v3",
        "GET",
        JOURNEY + "/responses",
        "personal_research_journey_responses",
        action="research.results.read",
    ),
    _route(
        "v3",
        "GET",
        JOURNEY + "/responses.csv",
        "personal_research_journey_responses_csv",
        action="research.results.read",
        response_mode="attachment",
    ),
    _route(
        "v3",
        "GET",
        JOURNEY + "/aggregate",
        "personal_research_journey_aggregate",
        action="research.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/research-summary",
        "personal_research_journey_idea_summary",
        action="research.results.read",
    ),
    _route(
        "v3",
        "GET",
        V3 + "/ideas/{idea_id}/research-summary/responses.csv",
        "personal_research_journey_idea_summary_csv",
        action="research.results.read",
        response_mode="attachment",
    ),
    _route(
        "v3",
        "GET",
        JOURNEY + "/response-rate",
        "personal_research_journey_response_rate",
        action="research.results.read",
    ),
)
_ROUTE_INDEX = {
    (route.version, route.method, route.path, route.operation_id): route
    for route in REVIEWED_ROUTES
}


@dataclass(frozen=True)
class OperationSpec:
    method: HttpMethod
    path: str
    contract_version: str
    operation_id: str
    query: tuple[str, ...] = ()
    required_action: str | None = None
    mode: str = "interactive"
    response_mode: str = "json"
    effect: bool = False


def generate_operation_specs(
    documents: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, OperationSpec]:
    sources = (
        tuple((version, load_bundled_document(version)) for version in _CONTRACT_NAMES)
        if documents is None
        else tuple(("v3" if _contains_v3(document) else "v2", document) for document in documents)
    )
    specs: dict[str, OperationSpec] = {}
    for version, document in sources:
        paths = document.get("paths")
        if not isinstance(paths, dict):
            raise ValueError("invalid_bundled_contract")
        for path, item in paths.items():
            if not isinstance(path, str) or not isinstance(item, dict):
                raise ValueError("invalid_bundled_contract")
            for method_key, operation in item.items():
                method = str(method_key).upper()
                if method not in {"GET", "POST", "PUT", "PATCH"} or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str):
                    continue
                reviewed = _ROUTE_INDEX.get((version, method, path, operation_id))
                if reviewed is None:
                    continue
                spec = OperationSpec(
                    reviewed.method,
                    reviewed.path,
                    reviewed.version,
                    reviewed.operation_id,
                    _query_names(item, operation),
                    reviewed.required_action,
                    response_mode=reviewed.response_mode,
                )
                previous = specs.get(reviewed.label)
                if previous is not None and previous != spec:
                    raise ValueError("duplicate_operation_label")
                specs[reviewed.label] = spec
    if documents is None and set(specs) != {route.label for route in REVIEWED_ROUTES}:
        raise ValueError("unexpected_operation_inventory")
    return specs


def operation_registry_document(specs: dict[str, OperationSpec] | None = None) -> dict[str, Any]:
    generated = specs if specs is not None else generate_operation_specs()
    return {
        "schema_version": 1,
        "operations": [
            {
                "name": name,
                "contract_version": spec.contract_version,
                "operation_id": spec.operation_id,
                "method": spec.method,
                "path": spec.path,
                "query": list(spec.query),
                "required_action": spec.required_action,
                "response_mode": spec.response_mode,
            }
            for name, spec in generated.items()
        ],
    }


def load_bundled_document(version: str) -> dict[str, Any]:
    try:
        name = _CONTRACT_NAMES[version]
    except KeyError:
        raise ValueError("unknown_contract_version") from None
    packaged = resources.files("user_research").joinpath("contracts", name)
    try:
        content = packaged.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = (_SOURCE_CONTRACTS / name).read_text(encoding="utf-8")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("invalid_bundled_contract")
    return value


def _contains_v3(document: dict[str, Any]) -> bool:
    paths = document.get("paths", {})
    return isinstance(paths, dict) and any(
        isinstance(path, str) and path.startswith("/api/platform/v3/") for path in paths
    )


def _query_names(path_item: dict[str, Any], operation: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for source in (path_item, operation):
        parameters = source.get("parameters", [])
        if not isinstance(parameters, list):
            raise ValueError("invalid_bundled_contract")
        for parameter in parameters:
            if not isinstance(parameter, dict) or parameter.get("in") != "query":
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("invalid_bundled_contract")
            names.append(name)
    return tuple(names)
