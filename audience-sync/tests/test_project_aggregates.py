"""Candidate aggregate contracts through the real standalone client (offline)."""

import copy
import json
import unittest
from io import BytesIO
from unittest.mock import patch

import test_project_query as fixtures
from test_project_criteria_readback import plan_response

from audience_sync.client import SafeApiError, _safe_projection
from audience_sync.contract_validation import (
    ContractViolation,
    validate_operation_request,
    validate_operation_response,
)
from audience_sync.project_operations import ProjectOperation as Op
from audience_sync.project_query import check_logical_path


def capability():
    result = copy.deepcopy(fixtures.CAPABILITY)
    result["resource"]["relations"].append({
        "relation_id": "events", "datahub_urn": "urn:li:dataset:fixture_events",
        "fields": [{"field_id": "amount", "value_type": "number", "operators": ["gte"]}],
    })
    result["resource"]["edges"] = [{
        "edge_id": "profile_events", "from_relation": "audience_profile",
        "to_relation": "events", "quantifiers": ["exists", "not_exists"],
        "aggregates": [{"aggregate_id": name, "function": name,
                        "field_id": None if name == "count" else "amount",
                        "value_type": "integer" if name == "count" else "number",
                        "operators": ["eq", "gte", "in", "between", "is_null"]}
                       for name in ("count", "sum", "min", "max", "avg")],
    }]
    return result


def criteria(**node):
    return {**fixtures.CRITERIA, "where": {
        "kind": "aggregate", "edge_id": "profile_events", "aggregate_id": "count",
        "operator": "gte", "value": 1, **node,
    }}


class AggregateTests(unittest.TestCase):
    def test_all_functions_and_optional_row_filter_preserve_readback(self):
        for name in ("count", "sum", "min", "max", "avg"):
            for filtered in (False, True):
                value = criteria(aggregate_id=name)
                if filtered:
                    value["where"]["where"] = {
                        "kind": "field", "relation_id": "events", "field_id": "amount",
                        "operator": "gte", "value": 5,
                    }
                with self.subTest(name=name, filtered=filtered):
                    validate_operation_request(Op.CREATE_AUDIENCE_QUERY, {
                        "name": "Fixture", "criteria": value, "idempotency_key": "fixture-123",
                    })
                    check_logical_path(value, capability()["resource"])
                    response = plan_response(criteria=value)
                    self.assertEqual(validate_operation_response(Op.GET_AUDIENCE_QUERY, response),
                                     response)
                    self.assertEqual(_safe_projection(response), response)

    def test_client_capability_and_create_roundtrip(self):
        client = fixtures.ProjectQueryTests().client()
        response = plan_response(criteria=criteria())
        with patch.object(client._opener, "open", side_effect=[
            BytesIO(json.dumps(capability()).encode()), BytesIO(json.dumps(response).encode()),
        ]) as send:
            actual = client.call(Op.CREATE_AUDIENCE_QUERY, path={"project_id": fixtures.PROJECT},
                                 body={"name": "Fixture", "criteria": criteria(),
                                       "idempotency_key": "fixture-123"})
            self.assertEqual(actual, response)
            self.assertEqual(send.call_count, 2)
        self.assertEqual(validate_operation_response(Op.GET_QUERY_CAPABILITIES, capability()),
                         capability())

    def test_typed_operators_and_null_shape(self):
        for operator, values in (("in", [0, 2]), ("between", [0, 2]), ("is_null", None)):
            value = criteria(operator=operator)
            del value["where"]["value"]
            if values is not None:
                value["where"]["values"] = values
            check_logical_path(value, capability()["resource"])
        for bad in (True, "1", None, 1.2):
            with self.subTest(value=bad), self.assertRaises(ContractViolation):
                check_logical_path(criteria(value=bad), capability()["resource"])

    def test_count_column_and_non_numeric_metric_types(self):
        for metric in ("count", "avg"):
            check_logical_path(criteria(aggregate_id=metric, value=10 ** 400),
                               capability()["resource"])
        for function, field_id, value_type, value in (
            ("count", "amount", "integer", 0),
            ("min", "amount", "number", 1.5),
            ("max", "label", "string", "trial"),
            ("max", "ended_at", "timestamp", "2026-09-14T00:00:00Z"),
        ):
            cap = capability()
            cap["resource"]["edges"][0]["aggregates"] = [{
                "aggregate_id": "metric", "function": function, "field_id": field_id,
                "value_type": value_type, "operators": ["eq"],
            }]
            validate_operation_response(Op.GET_QUERY_CAPABILITIES, cap)
            check_logical_path(criteria(aggregate_id="metric", operator="eq", value=value),
                               cap["resource"])
        for bad in (float("inf"), float("nan"), True, "1.5"):
            with self.assertRaises(ContractViolation):
                check_logical_path(criteria(aggregate_id="avg", value=bad),
                                   capability()["resource"])

    def test_unadvertised_stale_wrong_project_stop_before_create(self):
        cases = []
        for updates in ({"aggregate_id": "unknown"}, {"edge_id": "unknown"},
                        {"operator": "lte"}, {"value": "1"}):
            cases.append((criteria(**updates), capability()))
        stale = criteria()
        stale["registry_version"] = "stale"
        cases.append((stale, capability()))
        old = capability()
        del old["resource"]["edges"][0]["aggregates"]
        cases.append((criteria(), old))
        foreign = capability()
        foreign["project_id"] = "other-project"
        cases.append((criteria(), foreign))
        for value, cap in cases:
            client = fixtures.ProjectQueryTests().client()
            with patch.object(client._opener, "open", return_value=BytesIO(
                json.dumps(cap).encode()
            )) as send, self.assertRaises(SafeApiError):
                client.call(Op.CREATE_AUDIENCE_QUERY, path={"project_id": fixtures.PROJECT},
                            body={"name": "Fixture", "criteria": value,
                                  "idempotency_key": "fixture-123"})
            self.assertEqual(send.call_count, 1)
            self.assertTrue(send.call_args[0][0].full_url.endswith("/query-capabilities"))

    def test_timestamp_between_orders_instants_not_offset_strings(self):
        cap = capability()
        metric = cap["resource"]["edges"][0]["aggregates"][0]
        metric.update(function="max", field_id="ended_at", value_type="timestamp")
        value = criteria(operator="between")
        del value["where"]["value"]
        value["where"]["values"] = ["2026-09-14T08:00:00+08:00", "2026-09-14T01:00:00Z"]
        check_logical_path(value, cap["resource"])
        value["where"]["values"].reverse()
        with self.assertRaises(ContractViolation):
            check_logical_path(value, cap["resource"])
        # Lexical and numeric predicates retain their ordinary ordering.
        for value_type, bounds in (("string", ["a", "z"]), ("number", [2, 10])):
            metric["value_type"] = value_type
            value["where"]["values"] = bounds
            check_logical_path(value, cap["resource"])
            value["where"]["values"] = list(reversed(bounds))
            with self.assertRaises(ContractViolation):
                check_logical_path(value, cap["resource"])

    def test_nested_paths_scope_and_sql_injection_rejected(self):
        for where in (criteria()["where"], {"kind": "relationship", "edge_id": "profile_events",
                                           "quantifier": "exists", "where": criteria()["where"]}):
            with self.assertRaises(ContractViolation):
                check_logical_path(criteria(where=where), capability()["resource"])
        for key in ("sql", "tenant_id", "bundle_id", "function", "field_id"):
            with self.subTest(key=key), self.assertRaises(ContractViolation):
                check_logical_path(criteria(**{key: "forbidden"}), capability()["resource"])
        wrong = capability()
        wrong["resource"]["edges"][0]["from_relation"] = "events"
        with self.assertRaises(ContractViolation):
            check_logical_path(criteria(), wrong["resource"])
