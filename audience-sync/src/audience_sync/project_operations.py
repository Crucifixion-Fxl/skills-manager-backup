"""Published Project operations with explicit optional response extensions."""

import json
from enum import Enum
from functools import lru_cache
from importlib import resources
from pathlib import Path

from .allowlist import generate_operation_specs

PROJECT_CONTRACT_REVISION = "audience-project-v3"
PROJECT_PREFIX = "/api/platform/v3/projects/{project_id}/audience-sync"


class PersonalKeyOperation(str, Enum):
    GET_PROJECT_PERSONAL_KEY_CONTEXT = "get_project_personal_key_context"


PERSONAL_KEY_PATH = "/api/platform/v3/personal-key"


class ProjectOperation(str, Enum):
    LIST_PROJECT_AUDIENCE_ASSETS = "list_project_audience_assets"
    LIST_PROJECT_SYNCS = "list_project_syncs"
    GET_PROJECT_SYNC = "get_project_sync"
    GET_PROJECT_SYNC_CAPABILITIES = "get_project_sync_capabilities"
    GET_QUERY_CAPABILITIES = "get_query_capabilities"
    VALIDATE_AUDIENCE_QUERY = "validate_audience_query"
    CREATE_AUDIENCE_QUERY = "create_audience_query"
    GET_AUDIENCE_QUERY = "get_audience_query"
    PREVIEW_AUDIENCE_QUERY = "preview_audience_query"
    MATERIALIZE_AUDIENCE_QUERY = "materialize_audience_query"
    GET_AUDIENCE_QUERY_MATERIALIZATION = "get_audience_query_materialization"
    SYNC_AUDIENCE_QUERY = "sync_audience_query"
    GET_AUDIENCE_QUERY_SYNC = "get_audience_query_sync"


_ROUTES = (
    ("get", "/audience-assets"),
    ("get", "/syncs"),
    ("get", "/syncs/{sync_request_id}"),
    ("get", "/capabilities"),
    ("get", "/query-capabilities"),
    ("post", "/queries/validate"),
    ("post", "/queries"),
    ("get", "/queries/{plan_id}"),
    ("post", "/queries/{plan_id}/preview"),
    ("post", "/queries/{plan_id}/materializations"),
    ("get", "/queries/{plan_id}/materializations/{request_id}"),
    ("post", "/queries/{plan_id}/syncs"),
    ("get", "/queries/{plan_id}/syncs/{request_id}"),
)


@lru_cache(maxsize=1)
def load_project_document():
    name = "project-control-plane.openapi.json"
    packaged = resources.files("audience_sync").joinpath("contracts", name)
    try:
        content = packaged.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = (Path(__file__).resolve().parents[2] / "contracts" / name).read_text(
            encoding="utf-8"
        )
    document = json.loads(content)
    # Client-first rollout: retain the byte-attested upstream snapshot and only
    # recognize these documented optional navigation fields. Unknown fields
    # remain forbidden; neither requests nor operation inventory are widened.
    for model, fields in {
        "ProjectQueryPlan": ("audience_detail_url",),
        "ProjectQuerySyncResult": ("audience_detail_url", "provider_resource_url"),
    }.items():
        for field in fields:
            document["components"]["schemas"][model]["properties"].setdefault(field, {
                "anyOf": [{"type": "string", "maxLength": 2048}, {"type": "null"}],
                "default": None,
            })
    return document


def project_specs():
    document = load_project_document()
    selected = {}
    for operation, (method, suffix) in zip(ProjectOperation, _ROUTES):
        path = PROJECT_PREFIX + suffix
        contract = document["paths"][path][method]
        subset = {**document, "paths": {path: {method: contract}}}
        selected[operation] = generate_operation_specs(subset)[contract["operationId"]]
    return selected


PROJECT_OPERATION_SPECS = project_specs()


def personal_key_specs():
    document = load_project_document()
    subset = {**document, "paths": {PERSONAL_KEY_PATH: document["paths"][PERSONAL_KEY_PATH]}}
    generated = generate_operation_specs(subset)
    contract = document["paths"][PERSONAL_KEY_PATH]["get"]
    return {
        PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT: generated[contract["operationId"]]
    }


PERSONAL_KEY_OPERATION_SPECS = personal_key_specs()
