"""Closed response validation against the OpenAPI snapshots bundled with the Skill."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Any

from .allowlist import load_bundled_document
from .operations import OPERATION_SPECS, Operation


class ContractViolation(Exception):
    """A deliberately detail-free contract validation failure."""


_SAFE_ATTACHMENT_CONTENT_TYPES = frozenset({"application/x-ndjson", "text/csv", "text/markdown"})


def _contract_version(path: str) -> str:
    for version in ("v1", "v2", "v3"):
        if path.startswith(f"/api/platform/{version}/"):
            return version
    raise ContractViolation


@lru_cache(maxsize=3)
def _document(version: str) -> dict[str, Any]:
    try:
        value = load_bundled_document(version)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ContractViolation from None
    return value


def validate_operation_response(
    operation: Operation, response: dict[str, Any] | None
) -> dict[str, Any] | None:
    spec = OPERATION_SPECS[operation]
    version = _contract_version(spec.path)
    document = _document(version)
    try:
        responses = document["paths"][spec.path][spec.method.lower()]["responses"]
    except (KeyError, TypeError):
        raise ContractViolation from None

    schemas: list[dict[str, Any]] = []
    empty_success = False
    for status, definition in responses.items():
        if not str(status).startswith("2") or not isinstance(definition, dict):
            continue
        content = definition.get("content")
        if not isinstance(content, dict) or "application/json" not in content:
            empty_success = True
            continue
        schema = content["application/json"].get("schema")
        if isinstance(schema, dict):
            schemas.append(schema)

    if response is None:
        if empty_success:
            return None
        raise ContractViolation
    if not schemas or not any(_matches(response, schema, document) for schema in schemas):
        raise ContractViolation
    return response


def response_declares_property(operation: Operation, name: str) -> bool:
    """True when a success JSON schema can carry this property on the object or its resource."""
    spec = OPERATION_SPECS[operation]
    document = _document(_contract_version(spec.path))
    try:
        responses = document["paths"][spec.path][spec.method.lower()]["responses"]
    except (KeyError, TypeError):
        return False
    for status, definition in responses.items():
        if not str(status).startswith("2") or not isinstance(definition, dict):
            continue
        content = definition.get("content")
        if not isinstance(content, dict):
            continue
        media = content.get("application/json")
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if isinstance(schema, dict) and _response_schema_declares(schema, name, document, set()):
            return True
    return False


def _deref(schema: dict[str, Any], document: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    if reference in seen:
        return {}
    seen.add(reference)
    return _deref(_resolve(reference, document), document, seen)


def _object_declares(
    schema: dict[str, Any], name: str, document: dict[str, Any], seen: set[str]
) -> bool:
    schema = _deref(schema, document, seen)
    properties = schema.get("properties")
    if isinstance(properties, dict) and name in properties:
        return True
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if isinstance(branch, dict) and _object_declares(branch, name, document, seen):
                return True
    return False


def _response_schema_declares(
    schema: dict[str, Any], name: str, document: dict[str, Any], seen: set[str]
) -> bool:
    schema = _deref(schema, document, seen)
    if _object_declares(schema, name, document, set(seen)):
        return True
    properties = schema.get("properties")
    resource = properties.get("resource") if isinstance(properties, dict) else None
    if not isinstance(resource, dict):
        return False
    resource = _deref(resource, document, set(seen))
    if resource.get("type") == "array":
        return False
    return _object_declares(resource, name, document, set(seen))


def attachment_content_types(operation: Operation) -> frozenset[str]:
    """Return reviewed binary response media types for one attachment operation."""
    spec = OPERATION_SPECS[operation]
    version = _contract_version(spec.path)
    document = _document(version)
    try:
        responses = document["paths"][spec.path][spec.method.lower()]["responses"]
    except (KeyError, TypeError):
        raise ContractViolation from None
    content_types: set[str] = set()
    for status, definition in responses.items():
        if not str(status).startswith("2") or not isinstance(definition, dict):
            continue
        content = definition.get("content")
        if isinstance(content, dict):
            content_types.update(name for name in content if name in _SAFE_ATTACHMENT_CONTENT_TYPES)
    if not content_types:
        raise ContractViolation
    return frozenset(content_types)


def validate_operation_request(
    operation: Operation, body: dict[str, Any] | None
) -> dict[str, Any] | None:
    spec = OPERATION_SPECS[operation]
    version = _contract_version(spec.path)
    document = _document(version)
    try:
        operation_contract = document["paths"][spec.path][spec.method.lower()]
    except (KeyError, TypeError):
        raise ContractViolation from None
    request_body = operation_contract.get("requestBody")
    if request_body is None:
        if body is not None:
            raise ContractViolation
        return None
    try:
        schema = request_body["content"]["application/json"]["schema"]
    except (KeyError, TypeError):
        raise ContractViolation from None
    if body is None or not isinstance(schema, dict):
        raise ContractViolation
    _validate(body, schema, document)
    return body


def validate_operation_query(operation: Operation, query: dict[str, str]) -> None:
    """Validate required and supplied optional query values from bundled OpenAPI."""
    spec = OPERATION_SPECS[operation]
    version = _contract_version(spec.path)
    document = _document(version)
    query_parameters = _parameter_definitions(document, spec.path, spec.method, "query")
    required = {
        name for name, parameter in query_parameters.items() if parameter.get("required") is True
    }
    if not required.issubset(query) or not set(query).issubset(query_parameters):
        raise ContractViolation
    if "research_id" in query and "idea_id" in query_parameters and "idea_id" not in query:
        # Exact provider attachment is a parent-child binding. The public
        # contract keeps both parameters optional for backward-compatible
        # direct and Idea-only calls, but an exact Research target is never
        # valid without its Idea parent.
        raise ContractViolation
    for name, value in query.items():
        schema = query_parameters[name].get("schema")
        if not isinstance(schema, dict):
            raise ContractViolation
        _validate(_coerce_parameter(value, schema, document), schema, document)


def validate_operation_path(operation: Operation, path: dict[str, str]) -> None:
    """Validate the exact path parameter set and values from bundled OpenAPI."""
    spec = OPERATION_SPECS[operation]
    version = _contract_version(spec.path)
    _validate_parameters(version, spec.path, spec.method, "path", path)


def validate_contract_parameters(
    version: str,
    contract_path: str,
    method: str,
    location: str,
    values: dict[str, str],
) -> None:
    """Validate host-only fixed-route parameters against a bundled contract."""
    _validate_parameters(version, contract_path, method, location, values)


def _validate_parameters(
    version: str,
    contract_path: str,
    method: str,
    location: str,
    values: dict[str, str],
) -> None:
    document = _document(version)
    definitions = _parameter_definitions(document, contract_path, method, location)
    required = {
        name for name, parameter in definitions.items() if parameter.get("required") is True
    }
    if set(values) != required:
        raise ContractViolation
    for name, value in values.items():
        schema = definitions[name].get("schema")
        if not isinstance(schema, dict):
            raise ContractViolation
        _validate(value, schema, document)


def _parameter_definitions(
    document: dict[str, Any], contract_path: str, method: str, location: str
) -> dict[str, dict[str, Any]]:
    try:
        path_item = document["paths"][contract_path]
        operation = path_item[method.lower()]
    except (KeyError, TypeError):
        raise ContractViolation from None
    definitions: dict[str, dict[str, Any]] = {}
    for source in (path_item, operation):
        parameters = source.get("parameters", [])
        if not isinstance(parameters, list):
            raise ContractViolation
        for parameter in parameters:
            if not isinstance(parameter, dict) or parameter.get("in") != location:
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name:
                raise ContractViolation
            definitions[name] = parameter
    return definitions


def _coerce_parameter(value: str, schema: dict[str, Any], document: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        raise ContractViolation
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return _coerce_parameter(value, _resolve(reference, document), document)
    for keyword in ("anyOf", "oneOf"):
        if keyword not in schema:
            continue
        for branch in _schema_list(schema[keyword]):
            try:
                candidate = _coerce_parameter(value, branch, document)
                _validate(candidate, branch, document)
                return candidate
            except ContractViolation:
                continue
        raise ContractViolation
    expected_type = schema.get("type")
    if expected_type in (None, "string"):
        return value
    if expected_type == "integer":
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value) is None:
            raise ContractViolation
        return int(value)
    if expected_type == "number":
        try:
            number = float(value)
        except ValueError:
            raise ContractViolation from None
        if not math.isfinite(number):
            raise ContractViolation
        return number
    if expected_type == "boolean":
        if value == "true":
            return True
        if value == "false":
            return False
    raise ContractViolation


def _matches(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> bool:
    try:
        _validate(value, schema, document)
    except ContractViolation:
        return False
    return True


def _validate(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        _validate(value, _resolve(reference, document), document)
        return
    if "const" in schema and value != schema["const"]:
        raise ContractViolation
    if "enum" in schema and value not in schema["enum"]:
        raise ContractViolation
    if "allOf" in schema:
        for branch in _schema_list(schema["allOf"]):
            _validate(value, branch, document)
    if "anyOf" in schema and not any(
        _matches(value, branch, document) for branch in _schema_list(schema["anyOf"])
    ):
        raise ContractViolation
    if (
        "oneOf" in schema
        and sum(_matches(value, branch, document) for branch in _schema_list(schema["oneOf"])) != 1
    ):
        raise ContractViolation

    expected_type = schema.get("type")
    if expected_type == "object":
        _validate_object(value, schema, document)
    elif expected_type == "array":
        _validate_array(value, schema, document)
    elif expected_type == "string":
        _validate_string(value, schema)
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractViolation
        _validate_number(value, schema)
    elif expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractViolation
        _validate_number(value, schema)
    elif expected_type == "boolean" and not isinstance(value, bool):
        raise ContractViolation
    elif expected_type == "null" and value is not None:
        raise ContractViolation


def _validate_object(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolation
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ContractViolation
    required = schema.get("required", [])
    if not isinstance(required, list) or any(item not in value for item in required):
        raise ContractViolation
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        child = properties.get(key)
        if isinstance(child, dict):
            _validate(item, child, document)
        elif additional is False:
            raise ContractViolation
        elif isinstance(additional, dict):
            _validate(item, additional, document)


def _validate_array(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> None:
    if not isinstance(value, list):
        raise ContractViolation
    if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
        raise ContractViolation
    if schema.get("uniqueItems") and len(
        {json.dumps(item, sort_keys=True) for item in value}
    ) != len(value):
        raise ContractViolation
    items = schema.get("items")
    if isinstance(items, dict):
        for item in value:
            _validate(item, items, document)


def _validate_string(value: Any, schema: dict[str, Any]) -> None:
    if not isinstance(value, str):
        raise ContractViolation
    if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
        raise ContractViolation
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
        raise ContractViolation


def _validate_number(value: int | float, schema: dict[str, Any]) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise ContractViolation
    if "maximum" in schema and value > schema["maximum"]:
        raise ContractViolation
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise ContractViolation
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise ContractViolation


def _resolve(reference: str, document: dict[str, Any]) -> dict[str, Any]:
    if not reference.startswith("#/components/schemas/"):
        raise ContractViolation
    name = reference.removeprefix("#/components/schemas/").replace("~1", "/").replace("~0", "~")
    try:
        schema = document["components"]["schemas"][name]
    except (KeyError, TypeError):
        raise ContractViolation from None
    if not isinstance(schema, dict):
        raise ContractViolation
    return schema


def _schema_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContractViolation
    return value
