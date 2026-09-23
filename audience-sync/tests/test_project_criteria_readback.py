"""Real CLI readback and current-capability preflight for historical criteria."""

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_project_query as fixtures

from audience_sync.cli import main
from audience_sync.client import SafeApiError, _safe_projection
from audience_sync.contract_validation import ContractViolation, validate_operation_response
from audience_sync.project_operations import ProjectOperation as Op

ROOT = Path(__file__).resolve().parents[1]
GUIDE_ZH = json.loads((ROOT / "tests/fixtures/guide-zh-CN.json").read_text())

PLAN = "aqp_" + "a" * 26


def plan_response(**fields):
    return {"project_id": fixtures.PROJECT, "binding_revision": fixtures.REVISION,
            "resource": {"plan_id": PLAN, "name": "Fixture", "plan_hash": "a" * 64,
                         "partition_dt": "2026-09-09", "criteria_hash": "b" * 64,
                         "registry_version": "historical", "registry_digest": "c" * 64,
                         **fields}}


class CriteriaReadbackTests(unittest.TestCase):
    def test_cli_preserves_full_tree_and_normalizes_old_server(self):
        nested = copy.deepcopy(fixtures.CRITERIA)
        for _ in range(7):
            nested["where"] = {"kind": "and", "children": [nested["where"]]}
        for fields in ({}, {"criteria": None}, {"criteria": fixtures.CRITERIA},
                       {"criteria": nested}):
            response = plan_response(**fields)
            output = StringIO()
            request = {"path": {"project_id": fixtures.PROJECT, "plan_id": PLAN}}
            with (
                patch.dict("os.environ", {"AUDIENCE_PROJECT_ID": fixtures.PROJECT,
                           "AUDIENCE_SYNC_API_KEY": "awpk_v2_" + "a" * 26 + "_" + "A" * 43},
                           clear=True),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("audience_sync.client.build_opener") as opener,
                redirect_stdout(output),
            ):
                opener.return_value.open.return_value = BytesIO(json.dumps(response).encode())
                self.assertEqual(main(["get_audience_query", "--request-stdin"]), 0)
            expected = plan_response(criteria=fields.get("criteria"))
            self.assertEqual(json.loads(output.getvalue()), {"ok": True, "result": expected})
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_create_and_get_reject_unknown_or_malformed_criteria(self):
        invalid = ["raw", {**fixtures.CRITERIA, "sql": "forbidden"}]
        for update in ({"children": []}, {"value": {}}, {"values": ["VN"]}):
            value = copy.deepcopy(fixtures.CRITERIA)
            value["where"].update(update)
            invalid.append(value)
        for op in (Op.GET_AUDIENCE_QUERY, Op.CREATE_AUDIENCE_QUERY):
            valid = plan_response(criteria=fixtures.CRITERIA)
            self.assertEqual(validate_operation_response(op, valid), valid)
            for criteria in invalid:
                with self.subTest(op=op, criteria=criteria), self.assertRaises(ContractViolation):
                    validate_operation_response(op, plan_response(criteria=criteria))
            with self.assertRaises(ContractViolation):
                validate_operation_response(op, plan_response(sql="forbidden"))

    def test_stale_or_unsupported_criteria_stops_before_create(self):
        for mutation in ("version", "field", "operator", "value"):
            criteria = copy.deepcopy(fixtures.CRITERIA)
            if mutation == "version":
                criteria["registry_version"] = "historical"
            else:
                criteria["where"][{"field": "field_id", "operator": "operator",
                                   "value": "value"}[mutation]] = {
                                       "field": "unsupported", "operator": "between", "value": 1
                                   }[mutation]
            with patch("audience_sync.client.build_opener") as opener:
                opener.return_value.open.return_value = BytesIO(
                    json.dumps(fixtures.CAPABILITY).encode())
                with self.assertRaises(SafeApiError):
                    fixtures.ProjectQueryTests().client().call(
                        Op.CREATE_AUDIENCE_QUERY, path={"project_id": fixtures.PROJECT},
                        body={"name": "Rebuild", "criteria": criteria,
                              "idempotency_key": "rebuild-123"})
                for call in opener.return_value.open.call_args_list:
                    self.assertTrue(call.args[0].full_url.endswith("/query-capabilities"))

    def test_recovery_instruction_requires_semantic_review(self):
        guide = " ".join((Path(__file__).resolve().parents[1]
                          / "references/project-query.md").read_text().split())
        for requirement in (GUIDE_ZH["no_blind_registry_replacement"],
                            GUIDE_ZH["clarify_before_rebuild"],
                            (GUIDE_ZH["validate_then_create_rebuild"]),
                            GUIDE_ZH["no_old_evidence_transfer"]):
            self.assertIn(requirement, guide)

    def test_criteria_projection_rejects_unsafe_scalars_without_rewriting(self):
        for value in ("https://untrusted.test/path", "person@example.test", "awpk_secret",
                      "x\x00y", "x" * 4097, float("inf")):
            criteria = copy.deepcopy(fixtures.CRITERIA)
            criteria["where"]["value"] = value
            with self.subTest(value=value), self.assertRaises(SafeApiError):
                _safe_projection(plan_response(criteria=criteria))
