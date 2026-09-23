"""Closed success-response validation against the bundled Audience Sync v2 OpenAPI."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .allowlist import load_bundled_document
from .operations import OPERATION_SPECS, Operation
from .project_operations import (
    PERSONAL_KEY_OPERATION_SPECS,
    PROJECT_OPERATION_SPECS,
    PersonalKeyOperation,
    ProjectOperation,
    load_project_document,
)


class ContractViolation(Exception):
    """A deliberately detail-free contract validation failure."""


_RFC3339_DATETIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def _document(operation=None) -> dict[str, Any]:
    try:
        if isinstance(operation, (ProjectOperation, PersonalKeyOperation)):
            return load_project_document()
        return load_bundled_document()
    except (OSError, UnicodeError, ValueError):
        raise ContractViolation from None


def validate_operation_request(
    operation: Operation | ProjectOperation | PersonalKeyOperation, body: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Validate a request body against the bundled OpenAPI schema."""
    spec = {**OPERATION_SPECS, **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS}[operation]
    document = _document(operation)
    try:
        operation_contract = document["paths"][spec.path][spec.method.lower()]
    except (KeyError, TypeError):
        raise ContractViolation from None
    request_body = operation_contract.get("requestBody")
    if request_body is None:
        if body not in (None, {}):
            raise ContractViolation
        return None
    try:
        schema = request_body["content"]["application/json"]["schema"]
    except (KeyError, TypeError):
        raise ContractViolation from None
    if body is None or not isinstance(schema, dict):
        raise ContractViolation
    if isinstance(operation, ProjectOperation) and "criteria" in body:
        from .project_query import validate_criteria_shape

        validate_criteria_shape(body["criteria"])
    _validate(body, schema, document)
    audience_filter = body.get("audience_filter")
    if audience_filter is not None:
        validate_audience_filter(audience_filter)
    return body


def validate_audience_filter(value: Any) -> dict[str, Any]:
    """Validate the typed filter plus its non-OpenAPI cross-field semantics."""
    document = _document()
    try:
        schema = document["components"]["schemas"]["AudienceSyncFilter"]
    except (KeyError, TypeError):
        raise ContractViolation from None
    if not isinstance(schema, dict):
        raise ContractViolation
    _validate(value, schema, document)
    if not isinstance(value, dict):
        raise ContractViolation
    _validate_filter_semantics(value)
    return value


def validate_audience_criteria(value: Any) -> dict[str, Any]:
    """Validate historical criteria without requiring today's registry version."""
    from .project_query import validate_criteria_shape

    document = _document(ProjectOperation.GET_AUDIENCE_QUERY)
    validate_criteria_shape(value)
    schema = document["components"]["schemas"]["ProjectQueryPlan"]["properties"]["criteria"]
    _validate(value, schema, document)
    return value


def validate_operation_path(
    operation: Operation | ProjectOperation | PersonalKeyOperation, values: dict[str, str]
) -> None:
    """Validate the exact path parameter set and values from bundled OpenAPI."""
    _validate_parameters(operation, "path", values)


def validate_operation_query(
    operation: Operation | ProjectOperation | PersonalKeyOperation, values: dict[str, str]
) -> None:
    """Validate required and supplied query parameters from bundled OpenAPI."""
    _validate_parameters(operation, "query", values)


def _validate_parameters(
    operation: Operation | ProjectOperation | PersonalKeyOperation,
    location: str,
    values: dict[str, str],
) -> None:
    spec = {**OPERATION_SPECS, **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS}[operation]
    document = _document(operation)
    definitions = _parameter_definitions(document, spec.path, spec.method, location)
    required = {
        name for name, parameter in definitions.items() if parameter.get("required") is True
    }
    if not required.issubset(values) or not set(values).issubset(definitions):
        raise ContractViolation
    for name, value in values.items():
        schema = definitions[name].get("schema")
        if not isinstance(schema, dict):
            raise ContractViolation
        _validate(_coerce_parameter(value, schema, document), schema, document)


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
            if not isinstance(name, str) or not name or name in definitions:
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


def validate_operation_response(
    operation: Operation | ProjectOperation | PersonalKeyOperation, response: Any
) -> dict[str, Any] | None:
    """Validate one successful response before any safety projection."""
    try:
        spec = {
            **OPERATION_SPECS, **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS
        }[operation]
        document = _document(operation)
        responses = document["paths"][spec.path][spec.method.lower()]["responses"]
    except (KeyError, TypeError):
        raise ContractViolation from None

    schemas: list[dict[str, Any]] = []
    empty_success = False
    if not isinstance(responses, dict):
        raise ContractViolation
    for status, definition in responses.items():
        if not str(status).startswith("2") or not isinstance(definition, dict):
            continue
        content = definition.get("content")
        if not isinstance(content, dict) or "application/json" not in content:
            empty_success = True
            continue
        media_type = content["application/json"]
        schema = media_type.get("schema") if isinstance(media_type, dict) else None
        if isinstance(schema, dict):
            schemas.append(schema)

    if response is None:
        if empty_success:
            return None
        raise ContractViolation
    if not isinstance(response, dict):
        raise ContractViolation
    if operation in {
        ProjectOperation.CREATE_AUDIENCE_QUERY, ProjectOperation.GET_AUDIENCE_QUERY
    } and isinstance(response.get("resource"), dict):
        resource = {"criteria": None, **response["resource"]}
        if resource["criteria"] is not None:
            validate_audience_criteria(resource["criteria"])
        response = {**response, "resource": resource}
    if not schemas or not any(_matches(response, schema, document) for schema in schemas):
        raise ContractViolation
    if operation is Operation.SEARCH_AUDIENCES:
        _validate_search_detail_link(response)
    if operation in {Operation.SYNC_AUDIENCE, Operation.GET_AUDIENCE_SYNC}:
        _validate_provider_resource_link(response)
    return response


def _validate_search_detail_link(response: dict[str, Any]) -> None:
    items = response.get("items")
    if not isinstance(items, list):
        raise ContractViolation
    linked = [
        item
        for item in items
        if isinstance(item, dict) and item.get("audience_detail_url") is not None
    ]
    if not linked:
        return
    if len(items) != 1 or len(linked) != 1:
        raise ContractViolation
    audience = linked[0]
    if not isinstance(audience.get("audience_filter"), dict) or not isinstance(
        audience.get("audience_filter_hash"), str
    ):
        raise ContractViolation


def _validate_provider_resource_link(response: dict[str, Any]) -> None:
    if response.get("provider_resource_url") is not None and response.get("status") != "succeeded":
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
    if (
        schema.get("format") == "uri"
        and value is not None
        and not (isinstance(value, str) and _is_safe_https_uri(value))
    ):
        raise ContractViolation
    if (
        schema.get("format") == "date-time"
        and value is not None
        and not (isinstance(value, str) and _is_rfc3339_datetime(value))
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
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ContractViolation
    if any(item not in value for item in required):
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


def _validate_filter_semantics(value: dict[str, Any]) -> None:
    """Enforce cross-field and content rules OpenAPI cannot express."""
    for item in value.values():
        if isinstance(item, list) and any(isinstance(part, str) and "://" in part for part in item):
            raise ContractViolation
    for lower_name, upper_name in (
        ("device_quantity_min", "device_quantity_max"),
        ("paid_count_min", "paid_count_max"),
        ("paid_amount_min", "paid_amount_max"),
        ("refund_count_min", "refund_count_max"),
        ("refund_amount_min", "refund_amount_max"),
        ("app_score_min", "app_score_max"),
    ):
        lower, upper = value.get(lower_name), value.get(upper_name)
        if lower is not None and upper is not None and lower > upper:
            raise ContractViolation
    for lower_name, upper_name in (
        ("registered_at_from", "registered_at_to"),
        ("first_bound_at_from", "first_bound_at_to"),
        ("first_purchase_at_from", "first_purchase_at_to"),
        ("last_purchase_at_from", "last_purchase_at_to"),
    ):
        lower, upper = value.get(lower_name), value.get(upper_name)
        if lower is not None and upper is not None:
            if _parse_rfc3339_datetime(lower) > _parse_rfc3339_datetime(upper):
                raise ContractViolation


def _is_safe_https_uri(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


def _is_rfc3339_datetime(value: str) -> bool:
    if not 1 <= len(value) <= 40 or _RFC3339_DATETIME.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _parse_rfc3339_datetime(value: str) -> datetime:
    if not _is_rfc3339_datetime(value):
        raise ContractViolation
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ContractViolation from None


def _validate_number(value: int | float, schema: dict[str, Any]) -> None:
    if not math.isfinite(value):
        raise ContractViolation
    if "minimum" in schema and value < schema["minimum"]:
        raise ContractViolation
    if "maximum" in schema and value > schema["maximum"]:
        raise ContractViolation
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise ContractViolation
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise ContractViolation


def _resolve(reference: str, document: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/components/schemas/"
    if not reference.startswith(prefix):
        raise ContractViolation
    name = reference.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
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
