"""Closed Project discovery contracts through the actual canonical client."""

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import test_project_query as fixtures

from audience_sync.cli import main
from audience_sync.client import SafeApiError
from audience_sync.contract_validation import ContractViolation, validate_operation_response
from audience_sync.project_operations import ProjectOperation as Op

PROJECT, REVISION = fixtures.PROJECT, fixtures.REVISION

ASSETS = {
    "items": [
        {"asset_id": "aud_" + "a" * 26, "name": None, "source": "native_audience"},
        {"asset_id": "aqp_" + "b" * 26, "name": "September", "source": "query_plan"},
    ],
    "next_cursor": "next_page",
}
SYNC = {
    "sync_request_id": "asrq_" + "a" * 26,
    "project_id": PROJECT,
    "audience_id": None,
    "audience_name": "September",
    "materialization_request_id": None,
    "materialization_run_id": "run-fixture",
    "status": "succeeded",
    "expected_member_count": 1,
    "materialized_member_count": None,
    "added_count": 1,
    "removed_count": 0,
    "skipped_count": 0,
    "missing_alias_count": 0,
    "malformed_alias_count": 0,
    "unapproved_domain_count": 0,
    "duplicate_alias_count": 0,
    "requested_at": "2026-09-09T00:00:00Z",
    "updated_at": None,
    "completed_at": "2026-09-09T00:01:00Z",
    "safe_error_code": None,
}


class ProjectDiscoveryTests(unittest.TestCase):
    def test_cli_query_type_errors_are_actionable_before_bootstrap(self):
        for value in (100, True, {"private": "do-not-echo"}, ["do-not-echo"], None):
            request = {"query": {"limit": value}}
            output = StringIO()
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("audience_sync.client.build_opener") as opener,
                redirect_stdout(output),
            ):
                self.assertEqual(main(["list_project_syncs", "--request-stdin"]), 1)
            opener.assert_not_called()
            error = json.loads(output.getvalue())
            self.assertEqual(error["error"], "invalid_query_parameters")
            self.assertEqual(error["hint"],
                             'CLI query values must be strings, e.g. {"limit":"100"}. '
                             'Keep body values in their schema-defined JSON types.')
            self.assertNotIn("do-not-echo", output.getvalue())

    def test_cli_string_limit_is_validated_without_misleading_type_hint(self):
        response = {"project_id": PROJECT, "binding_revision": REVISION,
                    "resource": {"items": [], "next_cursor": None}}
        for limit, code in (("100", 0), ("101", 1), ("0", 1)):
            request = {"path": {"project_id": PROJECT}, "query": {"limit": limit}}
            output = StringIO()
            with (
                patch.dict("os.environ", {"AUDIENCE_PROJECT_ID": PROJECT,
                           "AUDIENCE_SYNC_API_KEY": "awpk_v2_" + "a" * 26 + "_" + "A" * 43},
                           clear=True),
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                patch("audience_sync.client.build_opener") as opener,
                redirect_stdout(output),
            ):
                opener.return_value.open.return_value = BytesIO(json.dumps(response).encode())
                self.assertEqual(main(["list_project_syncs", "--request-stdin"]), code)
            result = json.loads(output.getvalue())
            if code == 0:
                self.assertEqual(result, {"ok": True, "result": response})
                self.assertEqual(parse_qs(urlsplit(
                    opener.return_value.open.call_args.args[0].full_url).query), {"limit": ["100"]})
            else:
                self.assertEqual(result, {"ok": False, "error": "invalid_query_parameters"})
                opener.return_value.open.assert_not_called()

    def call(self, operation, resource, query=None, **envelope):
        client = fixtures.ProjectQueryTests().client()
        client._verified_binding_revision = REVISION
        payload = {
            "project_id": PROJECT,
            "binding_revision": REVISION,
            "resource": resource,
            **envelope,
        }
        path = {"project_id": PROJECT}
        if operation == Op.GET_PROJECT_SYNC:
            path["sync_request_id"] = SYNC["sync_request_id"]
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(payload).encode())
        ) as send:
            result = client.call(operation, path=path, query=query)
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(send.call_args.args[0].data)
        return result, send.call_args.args[0]

    def test_mixed_assets_bodyless_encoded_query_and_pagination(self):
        query = {"query": "paid & \u4e5d\u6708?", "limit": "2", "cursor": "next_page"}
        result, request = self.call(Op.LIST_PROJECT_AUDIENCE_ASSETS, ASSETS, query)
        self.assertEqual(result["resource"], ASSETS)
        self.assertTrue(urlsplit(request.full_url).path.endswith("/audience-assets"))
        self.assertEqual(
            parse_qs(urlsplit(request.full_url).query), {k: [str(v)] for k, v in query.items()}
        )
        for operation in (Op.LIST_PROJECT_AUDIENCE_ASSETS, Op.LIST_PROJECT_SYNCS):
            for cursor in (None, "next_page"):
                page = {"items": [], "next_cursor": cursor}
                self.assertEqual(self.call(operation, page)[0]["resource"], page)

    def test_all_owner_sync_list_and_exact_read_preserve_aggregates(self):
        page = {"items": [SYNC], "next_cursor": None}
        self.assertEqual(
            self.call(Op.LIST_PROJECT_SYNCS, page, {"status": "succeeded"})[0]["resource"], page
        )
        result, request = self.call(Op.GET_PROJECT_SYNC, SYNC)
        self.assertEqual(result["resource"], SYNC)
        self.assertTrue(request.full_url.endswith("/syncs/" + SYNC["sync_request_id"]))
        legacy = {
            **SYNC,
            **{
                key: None
                for key in (
                    "missing_alias_count",
                    "malformed_alias_count",
                    "unapproved_domain_count",
                    "duplicate_alias_count",
                )
            },
        }
        self.assertEqual(self.call(Op.GET_PROJECT_SYNC, legacy)[0]["resource"], legacy)

    def test_json_contract_enforces_asset_source_name_and_id_union(self):
        # Exercise only the emitted JSON contract, before semantic binding checks.
        def payload(source, prefix, name_length):
            return {
                "project_id": PROJECT,
                "binding_revision": REVISION,
                "resource": {
                    "items": [{"asset_id": prefix + "a" * 26,
                               "name": "n" * name_length, "source": source}],
                    "next_cursor": None,
                },
            }

        operation = Op.LIST_PROJECT_AUDIENCE_ASSETS
        for source, prefix, maximum in (
            ("native_audience", "aud_", 128), ("query_plan", "aqp_", 100)
        ):
            valid = payload(source, prefix, maximum)
            self.assertEqual(validate_operation_response(operation, valid), valid)
            with self.subTest(source=source, case="name_overflow"):
                with self.assertRaises(ContractViolation):
                    validate_operation_response(operation, payload(source, prefix, maximum + 1))
            other_prefix = "aqp_" if prefix == "aud_" else "aud_"
            with self.subTest(source=source, case="source_id_mismatch"):
                with self.assertRaises(ContractViolation):
                    validate_operation_response(operation, payload(source, other_prefix, maximum))

    def test_closed_asset_response_and_source_identity_binding(self):
        for changes in ({"source": "query_plan"}, {"asset_id": "bad"}, {"owner": "private"}):
            page = copy.deepcopy(ASSETS)
            page["items"][0].update(changes)
            with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                self.call(Op.LIST_PROJECT_AUDIENCE_ASSETS, page)
        for envelope in (
            {"project_id": "other"},
            {"binding_revision": "pbr_" + "b" * 64},
            {"extra": True},
        ):
            with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                self.call(Op.LIST_PROJECT_AUDIENCE_ASSETS, ASSETS, **envelope)
        with self.assertRaisesRegex(SafeApiError, "invalid_response"):
            self.call(Op.LIST_PROJECT_AUDIENCE_ASSETS, ASSETS, {"limit": "1"})

    def test_sync_scope_conservation_timestamps_and_closed_metadata(self):
        for changes in (
            {"project_id": "other"},
            {"sync_request_id": "asrq_" + "b" * 26},
            {"skipped_count": 99},
            {"missing_alias_count": None},
            {"status": "running"},
            {"requested_at": "2026-09-09"},
            {"completed_at": "2026-09-08T00:00:00Z"},
            {"added_count": True},
            {"destination": {}},
            {"safe_error_code": "raw_error"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(SafeApiError, "invalid_response"),
            ):
                self.call(Op.GET_PROJECT_SYNC, {**SYNC, **changes})
        with self.assertRaisesRegex(SafeApiError, "invalid_response"):
            self.call(Op.LIST_PROJECT_SYNCS, {"items": [SYNC, SYNC], "next_cursor": None})

    def test_missing_endpoint_and_transport_failure_never_empty_or_fallback(self):
        client = fixtures.ProjectQueryTests().client()
        for error in (URLError("fixture"), HTTPError("url", 404, "missing", {}, BytesIO(b"{}"))):
            with patch.object(client._opener, "open", side_effect=error) as send:
                with self.assertRaises(SafeApiError):
                    client.call(Op.LIST_PROJECT_AUDIENCE_ASSETS, path={"project_id": PROJECT})
                self.assertEqual(send.call_count, 1)
        with patch.object(client._opener, "open") as send:
            with self.assertRaisesRegex(SafeApiError, "project_binding_mismatch"):
                client.call(Op.LIST_PROJECT_SYNCS, path={"project_id": "other"})
            send.assert_not_called()
