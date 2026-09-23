"""Generate the fixed Audience Sync operation allowlist from bundled OpenAPI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Final

_SOURCE_CONTRACT = (
    Path(__file__).resolve().parents[2] / "contracts/audience-sync-v2.openapi.json"
)
_SUPPORTED_METHODS: Final[dict[str, str]] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
}
_OPENAPI_HTTP_METHODS: Final[frozenset[str]] = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
@dataclass(frozen=True)
class OperationSpec:
    method: str
    path: str
    path_parameters: tuple[str, ...] = ()
    query_parameters: tuple[str, ...] = ()
    required_query_parameters: frozenset[str] = frozenset()
    required_body: frozenset[str] = frozenset()
    optional_body: frozenset[str] = frozenset()


@lru_cache(maxsize=1)
def load_bundled_document() -> dict[str, Any]:
    """Load the same canonical contract from a checkout or installed wheel."""
    packaged = resources.files("audience_sync").joinpath(
        "contracts", "audience-sync-v2.openapi.json"
    )
    try:
        content = packaged.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = _SOURCE_CONTRACT.read_text(encoding="utf-8")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("invalid_bundled_contract")
    return value


def generate_operation_specs(
    document: dict[str, Any] | None = None,
) -> dict[str, OperationSpec]:
    """Bind every published operation ID to its literal OpenAPI transport."""
    contract = document if document is not None else load_bundled_document()
    paths = contract.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("invalid_bundled_contract")
    specs: dict[str, OperationSpec] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            raise ValueError("invalid_bundled_contract")
        for method_key, operation in path_item.items():
            normalized_method = str(method_key).lower()
            method = _SUPPORTED_METHODS.get(normalized_method)
            if method is None:
                if normalized_method in _OPENAPI_HTTP_METHODS:
                    raise ValueError("unsupported_operation_method")
                continue
            if not isinstance(operation, dict):
                raise ValueError("invalid_bundled_contract")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in specs:
                raise ValueError("duplicate_or_empty_operation_id")
            path_names = _parameter_names(path_item, operation, "path")
            query_names = _parameter_names(path_item, operation, "query")
            required_query_names = _required_parameter_names(
                path_item, operation, "query"
            )
            required_body, optional_body = _body_names(contract, operation)
            specs[operation_id] = OperationSpec(
                method=method,
                path=path,
                path_parameters=path_names,
                query_parameters=query_names,
                required_query_parameters=required_query_names,
                required_body=required_body,
                optional_body=optional_body,
            )
    return specs


def operation_registry_document(
    specs: dict[str, OperationSpec] | None = None,
) -> dict[str, Any]:
    """Return the deterministic review artifact for the generated allowlist."""
    generated = specs if specs is not None else generate_operation_specs()
    return {
        "contract_revision": "audience-sync-v2",
        "contract_state": "published_operations",
        "operations": [
            {
                "name": name,
                "method": spec.method,
                "path": spec.path,
                "path_parameters": list(spec.path_parameters),
                "query_parameters": list(spec.query_parameters),
                "required_query_parameters": sorted(spec.required_query_parameters),
                "required_body": sorted(spec.required_body),
                "optional_body": sorted(spec.optional_body),
            }
            for name, spec in generated.items()
        ],
        "schema_version": 2,
    }


def require_operation_inventory(
    specs: dict[str, OperationSpec], expected_names: frozenset[str]
) -> None:
    """Fail closed when bundled OpenAPI adds, removes, or renames an operation."""
    if set(specs) != set(expected_names):
        raise ValueError("unexpected_operation_inventory")


def _parameter_names(
    path_item: dict[str, Any], operation: dict[str, Any], location: str
) -> tuple[str, ...]:
    names: list[str] = []
    for source in (path_item, operation):
        parameters = source.get("parameters", [])
        if not isinstance(parameters, list):
            raise ValueError("invalid_bundled_contract")
        for parameter in parameters:
            if not isinstance(parameter, dict) or parameter.get("in") != location:
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("invalid_bundled_contract")
            names.append(name)
    return tuple(names)


def _required_parameter_names(
    path_item: dict[str, Any], operation: dict[str, Any], location: str
) -> frozenset[str]:
    required: set[str] = set()
    for source in (path_item, operation):
        parameters = source.get("parameters", [])
        if not isinstance(parameters, list):
            raise ValueError("invalid_bundled_contract")
        for parameter in parameters:
            if not isinstance(parameter, dict) or parameter.get("in") != location:
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("invalid_bundled_contract")
            if parameter.get("required") is True:
                required.add(name)
    return frozenset(required)


def _body_names(
    document: dict[str, Any], operation: dict[str, Any]
) -> tuple[frozenset[str], frozenset[str]]:
    request_body = operation.get("requestBody")
    if request_body is None:
        return frozenset(), frozenset()
    try:
        schema = request_body["content"]["application/json"]["schema"]
    except (KeyError, TypeError):
        raise ValueError("invalid_bundled_contract") from None
    resolved = _resolve_schema(schema, document)
    properties = resolved.get("properties")
    required = resolved.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ValueError("invalid_bundled_contract")
    if any(not isinstance(name, str) or name not in properties for name in required):
        raise ValueError("invalid_bundled_contract")
    return frozenset(required), frozenset(properties) - frozenset(required)


def _resolve_schema(schema: Any, document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ValueError("invalid_bundled_contract")
    reference = schema.get("$ref")
    prefix = "#/components/schemas/"
    if reference is None:
        return schema
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ValueError("invalid_bundled_contract")
    name = reference.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
    try:
        resolved = document["components"]["schemas"][name]
    except (KeyError, TypeError):
        raise ValueError("invalid_bundled_contract") from None
    if not isinstance(resolved, dict):
        raise ValueError("invalid_bundled_contract")
    return resolved
