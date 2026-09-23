"""Logical discovery preflight and exact public response bindings, never SQL policy."""

import math
from datetime import datetime

from .contract_validation import ContractViolation, _is_rfc3339_datetime
from .project_operations import ProjectOperation as Op

_SYNC_REASON_COUNT_FIELDS = (
    "missing_alias_count",
    "malformed_alias_count",
    "unapproved_domain_count",
    "duplicate_alias_count",
)


def _is_nonnegative_int(value):
    return type(value) is int and value >= 0


def _validate_sync_reason_counts(resource):
    """Keep terminal skip-audit aggregates closed and internally consistent."""
    values = tuple(resource.get(field) for field in _SYNC_REASON_COUNT_FIELDS)
    # Old ledgers predate the four reason columns. They remain readable only as
    # one unambiguous all-null tuple; a partial tuple has no safe interpretation.
    if all(value is None for value in values):
        return
    if any(not _is_nonnegative_int(value) for value in values):
        raise ContractViolation
    skipped_count = resource.get("skipped_count")
    if not _is_nonnegative_int(skipped_count) or sum(values) != skipped_count:
        raise ContractViolation


def validate_criteria_shape(criteria):
    if not isinstance(criteria, dict) or "where" not in criteria:
        raise ContractViolation
    pending = [(criteria["where"], 1)]
    count = 0
    allowed = {
        "and": {"kind", "children"},
        "or": {"kind", "children"},
        "not": {"kind", "child"},
        "field": {"kind", "relation_id", "field_id", "operator", "value", "values"},
        "relationship": {"kind", "edge_id", "quantifier", "where"},
        "aggregate": {"kind", "edge_id", "aggregate_id", "where", "operator", "value", "values"},
    }
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 8 or count > 100 or not isinstance(node, dict):
            raise ContractViolation
        kind = node.get("kind")
        if not isinstance(kind, str) or kind not in allowed or set(node) - allowed[kind]:
            raise ContractViolation
        if kind in {"and", "or"}:
            children = node.get("children")
            if not isinstance(children, list) or not 1 <= len(children) <= 20:
                raise ContractViolation
            pending.extend((child, depth + 1) for child in children)
        elif kind == "not":
            pending.append((node.get("child"), depth + 1))
        elif kind == "relationship":
            if (
                not isinstance(node.get("edge_id"), str)
                or not isinstance(node.get("quantifier"), str)
                or node.get("quantifier") not in {"exists", "not_exists"}
            ):
                raise ContractViolation
            pending.append((node.get("where"), depth + 1))
        else:
            identifiers = (("edge_id", "aggregate_id", "operator") if kind == "aggregate"
                           else ("relation_id", "field_id", "operator"))
            if any(
                not isinstance(node.get(field), str) or not node[field]
                for field in identifiers
            ):
                raise ContractViolation
            if kind == "aggregate" and node.get("where") is not None:
                pending.append((node["where"], depth + 1))
            operator = node["operator"]
            if operator in {"in", "between"}:
                values = node.get("values")
                if (
                    "value" in node
                    or not isinstance(values, list)
                    or not 1 <= len(values) <= 50
                    or (operator == "between" and len(values) != 2)
                ):
                    raise ContractViolation
            elif operator == "is_null":
                if "value" in node or "values" in node:
                    raise ContractViolation
            elif "value" not in node or "values" in node:
                raise ContractViolation


def check_logical_path(criteria, capability):
    """Only advertised paths are candidates; DataHub URNs confer no authority."""
    validate_criteria_shape(criteria)
    if criteria.get("registry_version") != capability["registry_version"]:
        raise ContractViolation
    relations = {item["relation_id"]: item for item in capability["relations"]}
    edges = {item["edge_id"]: item for item in capability["edges"]}
    if len(relations) != len(capability["relations"]) or len(edges) != len(capability["edges"]):
        raise ContractViolation
    pending = [(criteria["where"], "audience_profile")]
    while pending:
        node, relation = pending.pop()
        kind = node["kind"]
        if kind in {"and", "or"}:
            pending.extend((child, relation) for child in node["children"])
        elif kind == "not":
            pending.append((node["child"], relation))
        elif kind in {"relationship", "aggregate"}:
            edge = edges.get(node["edge_id"])
            if (
                edge is None
                or relation != "audience_profile"
                or edge["from_relation"] != relation
                or edge["to_relation"] not in relations
                or edge["to_relation"] == "audience_profile"
                or (kind == "relationship" and node["quantifier"] not in edge["quantifiers"])
            ):
                raise ContractViolation
            if kind == "aggregate":
                metrics = {item["aggregate_id"]: item for item in edge.get("aggregates", [])}
                if len(metrics) != len(edge.get("aggregates", [])):
                    raise ContractViolation
                metric = metrics.get(node["aggregate_id"])
                if metric is None:
                    raise ContractViolation
                _check_typed_predicate(node, metric)
            if node.get("where") is not None:
                pending.append((node["where"], edge["to_relation"]))
        else:
            if node["relation_id"] != relation or relation not in relations:
                raise ContractViolation
            fields = {item["field_id"]: item for item in relations[relation]["fields"]}
            field = fields.get(node["field_id"])
            _check_typed_predicate(node, field)


def _check_typed_predicate(node, field):
    if field is None or node["operator"] not in field["operators"]:
        raise ContractViolation
    values = node.get("values", [node.get("value")])
    if node["operator"] == "is_null":
        return
    for value in values:
        checks = {
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) is int or (type(value) is float and math.isfinite(value)),
            "boolean": type(value) is bool,
            "timestamp": isinstance(value, str) and _is_rfc3339_datetime(value),
        }
        if not checks.get(field["value_type"], False):
            raise ContractViolation
    if node["operator"] == "between":
        ordered_values = values
        if field["value_type"] == "timestamp":
            ordered_values = [datetime.fromisoformat(value.replace("Z", "+00:00"))
                              for value in values]
        if ordered_values[0] > ordered_values[1]:
            raise ContractViolation


def validate_response_binding(operation, response, path, body, query=None):
    if not isinstance(response, dict) or response.get("project_id") != path["project_id"]:
        raise ContractViolation
    resource = response.get("resource", response)
    if operation in {Op.LIST_PROJECT_AUDIENCE_ASSETS, Op.LIST_PROJECT_SYNCS, Op.GET_PROJECT_SYNC}:
        _validate_discovery(operation, resource, path, query or {})
    if "plan_id" in path and resource.get("plan_id") != path["plan_id"]:
        raise ContractViolation
    request_field = {
        Op.GET_AUDIENCE_QUERY_MATERIALIZATION: "materialization_request_id",
        Op.GET_AUDIENCE_QUERY_SYNC: "sync_request_id",
    }.get(operation)
    if request_field and resource.get(request_field) != path["request_id"]:
        raise ContractViolation
    if operation == Op.SYNC_AUDIENCE_QUERY:
        for field in ("materialization_run_id", "destination_id", "destination_revision"):
            if resource.get(field) != body[field]:
                raise ContractViolation
    if operation in {Op.MATERIALIZE_AUDIENCE_QUERY, Op.GET_AUDIENCE_QUERY_MATERIALIZATION}:
        if resource["status"] == "succeeded" and (
            not resource.get("materialization_run_id")
            or type(resource.get("member_count")) is not int
            or resource["member_count"] < 0
        ):
            raise ContractViolation
        if (
            operation == Op.MATERIALIZE_AUDIENCE_QUERY
            and resource["status"] == "succeeded"
            and resource["member_count"] != body["expected_member_count"]
        ):
            raise ContractViolation
    if operation in {Op.SYNC_AUDIENCE_QUERY, Op.GET_AUDIENCE_QUERY_SYNC}:
        if resource["status"] == "succeeded" and any(
            not _is_nonnegative_int(resource.get(field))
            for field in ("added_count", "removed_count", "skipped_count")
        ):
            raise ContractViolation
        _validate_sync_reason_counts(resource)


def _validate_discovery(operation, resource, path, query):
    if operation == Op.GET_PROJECT_SYNC:
        if resource["sync_request_id"] != path["sync_request_id"]:
            raise ContractViolation
        items = [resource]
    else:
        items = resource["items"]
        if len(items) > int(query.get("limit", 20)):
            raise ContractViolation
    identities = set()
    for item in items:
        if operation == Op.LIST_PROJECT_AUDIENCE_ASSETS:
            identity = item["asset_id"]
            prefix, maximum = (("aud_", 128) if item["source"] == "native_audience"
                               else ("aqp_", 100))
            if not identity.startswith(prefix) or (
                item["name"] is not None and len(item["name"]) > maximum
            ):
                raise ContractViolation
        else:
            identity = item["sync_request_id"]
            if item["project_id"] != path["project_id"]:
                raise ContractViolation
            if "status" in query and item["status"] != query["status"]:
                raise ContractViolation
            _validate_sync_reason_counts(item)
            if item["status"] in {"queued", "running"} and any(
                item[field] is not None for field in _SYNC_REASON_COUNT_FIELDS
            ):
                raise ContractViolation
            requested = datetime.fromisoformat(item["requested_at"].replace("Z", "+00:00"))
            completed = item["completed_at"]
            if completed and datetime.fromisoformat(completed.replace("Z", "+00:00")) < requested:
                raise ContractViolation
        if identity in identities:
            raise ContractViolation
        identities.add(identity)
