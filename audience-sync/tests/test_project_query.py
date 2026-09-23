import copy
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch

from audience_sync.client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
from audience_sync.contract_validation import ContractViolation, validate_operation_request
from audience_sync.operations import Operation
from audience_sync.project_operations import PROJECT_OPERATION_SPECS
from audience_sync.project_operations import ProjectOperation as Op
from audience_sync.project_query import check_logical_path, validate_response_binding

PROJECT = "kiwibit"
REVISION = "pbr_" + "a" * 64
CRITERIA = {
    "schema_version": "audience-criteria-v1",
    "registry_version": "audience-query-v1-profile",
    "where": {
        "kind": "field",
        "relation_id": "audience_profile",
        "field_id": "country",
        "operator": "eq",
        "value": "VN",
    },
}
CAPABILITY = {
    "project_id": PROJECT,
    "binding_revision": REVISION,
    "resource": {
        "schema_version": "audience-query-capability-v1",
        "registry_version": "audience-query-v1-profile",
        "registry_digest": "a" * 64,
        "structured_criteria_available": True,
        "preview_available": False,
        "materialization_available": False,
        "brevo_sync_available": False,
        "relations": [
            {
                "relation_id": "audience_profile",
                "datahub_urn": "urn:li:dataset:profile",
                "fields": [
                    {
                        "field_id": "country",
                        "value_type": "string",
                        "operators": ["eq", "in", "is_null"],
                    }
                ],
            }
        ],
        "edges": [],
    },
}


class ProjectQueryTests(unittest.TestCase):
    def test_environment_timeout_has_project_preview_headroom_and_legacy_bounds(self):
        project_env = {
            "AUDIENCE_PROJECT_ID": "example-project",
            "AUDIENCE_SYNC_API_KEY": "awpk_v2_" + "a" * 26 + "_" + "A" * 43,
        }
        for environment, expected in (
            (project_env, 45.0),
            ({"AUDIENCE_SYNC_API_KEY": project_env["AUDIENCE_SYNC_API_KEY"].replace(
                "awpk_v2_", "awpk_v1_")}, 10.0),
            ({**project_env, "AUDIENCE_PLATFORM_TIMEOUT_SECONDS": "30"}, 30.0),
        ):
            with patch.dict("os.environ", environment, clear=True):
                config = AudienceSyncConfig.from_environment()
                self.assertEqual(config.timeout_seconds, expected)
                AudienceSyncClient(config)
        for timeout in ("0", "-1", "nan", "inf", "45.1", "bad"):
            with self.subTest(timeout=timeout), patch.dict(
                "os.environ", {**project_env, "AUDIENCE_PLATFORM_TIMEOUT_SECONDS": timeout},
                clear=True,
            ), self.assertRaisesRegex(SafeApiError, "invalid_timeout"):
                AudienceSyncClient(AudienceSyncConfig.from_environment())
        with patch.dict("os.environ", {
            "AUDIENCE_SYNC_API_KEY": project_env["AUDIENCE_SYNC_API_KEY"].replace(
                "awpk_v2_", "awpk_v1_"),
            "AUDIENCE_PLATFORM_TIMEOUT_SECONDS": "30.1",
        }, clear=True), self.assertRaisesRegex(SafeApiError, "invalid_timeout"):
            AudienceSyncClient(AudienceSyncConfig.from_environment())

    def test_project_cli_stdin_and_host_binding(self):
        from audience_sync.cli import main

        request = {"path": {"project_id": PROJECT}}
        output = StringIO()
        environ = {
            "AUDIENCE_PROJECT_ID": PROJECT,
            "AUDIENCE_SYNC_API_KEY": "awpk_v2_" + "a" * 26 + "_" + "A" * 43,
        }
        with (
            patch.dict("os.environ", environ, clear=True),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("audience_sync.client.build_opener") as opener,
            redirect_stdout(output),
        ):
            opener.return_value.open.return_value = BytesIO(json.dumps(CAPABILITY).encode())
            self.assertEqual(main(["get_query_capabilities", "--request-stdin"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "result": CAPABILITY})
        self.assertNotIn("awpk", output.getvalue())

    def test_project_cli_forbids_arbitrary_transport_input(self):
        from audience_sync.cli import main

        output = StringIO()
        request = {"path": {"project_id": PROJECT}, "url": "https://evil.test"}
        with (
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("audience_sync.client.build_opener") as opener,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["get_query_capabilities", "--request-stdin"]), 2)
        opener.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["error"], "invalid_arguments")

    def client(self, **overrides):
        values = dict(
            base_url="https://example.test",
            sync_api_key="awpk_v2_" + "a" * 26 + "_" + "A" * 43,
            contract_revision="audience-project-v3",
            project_id=PROJECT,
        )
        return AudienceSyncClient(AudienceSyncConfig(**{**values, **overrides}))

    @staticmethod
    def successful_sync_response(**changes):
        resource = {
            "plan_id": "aqp_" + "a" * 26,
            "sync_request_id": "asrq_" + "b" * 26,
            "status": "succeeded",
            "materialization_run_id": "run-123",
            "destination_id": "brevo-test",
            "destination_revision": "pdr_" + "c" * 64,
            "added_count": 16968,
            "removed_count": 0,
            "missing_alias_count": 30,
            "malformed_alias_count": 1,
            "unapproved_domain_count": 5,
            "duplicate_alias_count": 10,
            "skipped_count": 46,
            "safe_error_code": None,
        }
        resource.update(changes)
        return {
            "project_id": PROJECT,
            "binding_revision": REVISION,
            "resource": resource,
        }

    @staticmethod
    def failed_materialization_response(**changes):
        resource = {
            "plan_id": "aqp_" + "a" * 26,
            "materialization_request_id": "amrq_" + "d" * 26,
            "status": "failed",
            "materialization_run_id": None,
            "member_count": None,
            "safe_error_code": "project_query_materialization_terminal_failure",
        }
        resource.update(changes)
        return {
            "project_id": PROJECT,
            "binding_revision": REVISION,
            "resource": resource,
        }

    @staticmethod
    def project_sync_capabilities(target_type):
        destination = {
            "destination_id": "brevo-test",
            "destination_revision": "pdr_" + "b" * 64,
            "kind": "brevo",
            "label": "Staging destination",
            "is_default": False,
        }
        if target_type != "missing":
            destination["target_type"] = target_type
        return {
            "project_id": PROJECT,
            "binding_revision": REVISION,
            "definition_available": True,
            "materialization_available": True,
            "destinations": [destination],
        }

    def test_exact_additive_inventory_and_no_admin_routes(self):
        self.assertEqual(len(PROJECT_OPERATION_SPECS), 13)
        self.assertTrue(
            all(
                spec.path.startswith("/api/platform/v3/projects/{project_id}/audience-sync/")
                for spec in PROJECT_OPERATION_SPECS.values()
            )
        )
        self.assertEqual(len(Operation), 9)

    def test_real_contract_capability_roundtrip_and_request_path(self):
        client = self.client()
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(CAPABILITY).encode())
        ) as send:
            self.assertEqual(
                client.call(Op.GET_QUERY_CAPABILITIES, path={"project_id": PROJECT}), CAPABILITY
            )
        self.assertTrue(
            send.call_args[0][0].full_url.endswith("/kiwibit/audience-sync/query-capabilities")
        )

    def test_cross_project_and_untrusted_operation_reject_before_transport(self):
        for operation, path in (
            (Op.GET_QUERY_CAPABILITIES, {"project_id": "vicohome"}),
            ("get_query_capabilities", {"project_id": PROJECT}),
        ):
            client = self.client()
            with patch.object(client._opener, "open") as send, self.assertRaises(SafeApiError):
                client.call(operation, path=path)
            send.assert_not_called()

    def test_key_revision_boundaries(self):
        for client, operation in (
            (self.client(), Operation.GET_SYNC_CAPABILITIES),
            (self.client(contract_revision="audience-sync-v2"), Op.GET_QUERY_CAPABILITIES),
            (
                self.client(sync_api_key="awpk_v1_" + "a" * 26 + "_" + "A" * 43),
                Op.GET_QUERY_CAPABILITIES,
            ),
        ):
            with patch.object(client._opener, "open") as send, self.assertRaises(SafeApiError):
                client.call(operation, path={"project_id": PROJECT})
            send.assert_not_called()

    def test_unknown_discovery_path_only_reads_capabilities(self):
        criteria = copy.deepcopy(CRITERIA)
        criteria["where"]["field_id"] = "cancelled_at"
        client = self.client()
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(CAPABILITY).encode())
        ) as send:
            with self.assertRaisesRegex(SafeApiError, "query_path_onboarding_required"):
                client.call(
                    Op.CREATE_AUDIENCE_QUERY,
                    path={"project_id": PROJECT},
                    body={"name": "VN", "criteria": criteria, "idempotency_key": "create-123"},
                )
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][0].method, "GET")

    def test_scope_sql_and_closed_node_shape_denied(self):
        for field in ("tenant_id", "bundle_id", "sql", "product_scope", "projection"):
            body = {"criteria": CRITERIA, field: "forbidden"}
            with self.assertRaises(ContractViolation):
                validate_operation_request(Op.VALIDATE_AUDIENCE_QUERY, body)
        criteria = copy.deepcopy(CRITERIA)
        criteria["where"]["children"] = []
        with self.assertRaises(ContractViolation):
            validate_operation_request(Op.VALIDATE_AUDIENCE_QUERY, {"criteria": criteria})

    def test_complexity_types_and_edge_fail_closed(self):
        cases = []
        for key, value in (("relation_id", "subscription"), ("operator", "gte"), ("value", 1)):
            criteria = copy.deepcopy(CRITERIA)
            criteria["where"][key] = value
            cases.append(criteria)
        criteria = copy.deepcopy(CRITERIA)
        criteria["where"] = {
            "kind": "relationship",
            "edge_id": "latest_subscription",
            "quantifier": "exists",
            "where": criteria["where"],
        }
        cases.append(criteria)
        criteria = copy.deepcopy(CRITERIA)
        for _ in range(8):
            criteria["where"] = {"kind": "not", "child": criteria["where"]}
        cases.append(criteria)
        for criteria in cases:
            with self.assertRaises(ContractViolation):
                check_logical_path(criteria, CAPABILITY["resource"])

    def test_strict_confirmation_and_required_preview(self):
        body = {
            "materialization_request_id": "amrq_" + "a" * 26,
            "materialization_run_id": "run-123",
            "expected_member_count": 2,
            "destination_id": "brevo-test",
            "destination_revision": "pdr_" + "b" * 64,
            "idempotency_key": "sync-1234",
        }
        for confirmed in (False, 1, 1.0, "true", None):
            with self.assertRaises(ContractViolation):
                validate_operation_request(Op.SYNC_AUDIENCE_QUERY, {**body, "confirmed": confirmed})
        validate_operation_request(Op.SYNC_AUDIENCE_QUERY, {**body, "confirmed": True})
        with self.assertRaises(ContractViolation):
            validate_operation_request(
                Op.MATERIALIZE_AUDIENCE_QUERY,
                {"expected_member_count": 2, "idempotency_key": "material-123"},
            )

    def test_disabled_preview_no_post(self):
        client = self.client()
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(CAPABILITY).encode())
        ) as send:
            with self.assertRaisesRegex(SafeApiError, "project_query_unavailable"):
                client.call(
                    Op.PREVIEW_AUDIENCE_QUERY,
                    path={"project_id": PROJECT, "plan_id": "aqp_" + "a" * 26},
                )
        self.assertEqual(send.call_count, 1)

    def test_sync_requires_explicit_folder_target_semantic(self):
        query_capability = copy.deepcopy(CAPABILITY)
        query_capability["resource"]["brevo_sync_available"] = True
        body = {
            "materialization_request_id": "amrq_" + "a" * 26,
            "materialization_run_id": "run-123",
            "expected_member_count": 2,
            "destination_id": "brevo-test",
            "destination_revision": "pdr_" + "b" * 64,
            "confirmed": True,
            "idempotency_key": "sync-target-123",
        }
        for target_type in ("list", None, "missing"):
            client = self.client()
            responses = iter(
                (query_capability, self.project_sync_capabilities(target_type))
            )
            with patch.object(
                client._opener,
                "open",
                side_effect=lambda *_args, **_kwargs: BytesIO(json.dumps(next(responses)).encode()),
            ) as send:
                with self.assertRaisesRegex(SafeApiError, "project_destination_unavailable"):
                    client.call(
                        Op.SYNC_AUDIENCE_QUERY,
                        path={"project_id": PROJECT, "plan_id": "aqp_" + "a" * 26},
                        body=body,
                    )
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all(call.args[0].method == "GET" for call in send.call_args_list))

    def test_sync_readback_transports_closed_terminal_audit_projection(self):
        client = self.client()
        response = self.successful_sync_response()
        path = {
            "project_id": PROJECT,
            "plan_id": response["resource"]["plan_id"],
            "request_id": response["resource"]["sync_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
        ) as send:
            self.assertEqual(client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path), response)
        self.assertTrue(send.call_args[0][0].full_url.endswith("/syncs/" + path["request_id"]))

    def test_sync_readback_allows_legacy_all_null_reason_tuple(self):
        client = self.client()
        response = self.successful_sync_response(
            missing_alias_count=None,
            malformed_alias_count=None,
            unapproved_domain_count=None,
            duplicate_alias_count=None,
        )
        path = {
            "project_id": PROJECT,
            "plan_id": response["resource"]["plan_id"],
            "request_id": response["resource"]["sync_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
        ):
            self.assertEqual(client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path), response)

    def test_new_client_normalizes_missing_safe_error_code_from_old_platform(self):
        client = self.client()
        old_platform_response = self.successful_sync_response()
        del old_platform_response["resource"]["safe_error_code"]
        path = {
            "project_id": PROJECT,
            "plan_id": old_platform_response["resource"]["plan_id"],
            "request_id": old_platform_response["resource"]["sync_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(old_platform_response).encode())
        ):
            result = client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path)
        self.assertIsNone(result["resource"]["safe_error_code"])

    def test_missing_safe_error_normalization_keeps_old_response_closed(self):
        client = self.client()
        old_platform_response = self.successful_sync_response(recipient_alias="never-project")
        del old_platform_response["resource"]["safe_error_code"]
        path = {
            "project_id": PROJECT,
            "plan_id": old_platform_response["resource"]["plan_id"],
            "request_id": old_platform_response["resource"]["sync_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(old_platform_response).encode())
        ):
            with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path)

    def test_sync_readback_rejects_partial_or_mismatched_reason_tuple(self):
        for response in (
            self.successful_sync_response(duplicate_alias_count=None),
            self.successful_sync_response(skipped_count=45),
        ):
            client = self.client()
            path = {
                "project_id": PROJECT,
                "plan_id": response["resource"]["plan_id"],
                "request_id": response["resource"]["sync_request_id"],
            }
            with patch.object(
                client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
            ):
                with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                    client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path)

    def test_sync_readback_preserves_only_declared_safe_failure_code(self):
        client = self.client()
        response = self.successful_sync_response(
            status="failed",
            added_count=None,
            removed_count=None,
            skipped_count=None,
            missing_alias_count=None,
            malformed_alias_count=None,
            unapproved_domain_count=None,
            duplicate_alias_count=None,
            safe_error_code="project_query_sync_provider_failed",
        )
        path = {
            "project_id": PROJECT,
            "plan_id": response["resource"]["plan_id"],
            "request_id": response["resource"]["sync_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
        ):
            self.assertEqual(client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path), response)

    def test_materialization_readback_preserves_only_declared_safe_failure_code(self):
        client = self.client()
        response = self.failed_materialization_response()
        path = {
            "project_id": PROJECT,
            "plan_id": response["resource"]["plan_id"],
            "request_id": response["resource"]["materialization_request_id"],
        }
        with patch.object(
            client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
        ):
            self.assertEqual(
                client.call(Op.GET_AUDIENCE_QUERY_MATERIALIZATION, path=path), response
            )

    def test_sync_readback_rejects_undeclared_response_fields(self):
        for response in (
            self.successful_sync_response(recipient_alias="never-project"),
            self.successful_sync_response(
                status="failed",
                added_count=None,
                removed_count=None,
                skipped_count=None,
                missing_alias_count=None,
                malformed_alias_count=None,
                unapproved_domain_count=None,
                duplicate_alias_count=None,
                safe_error_code="raw provider detail",
            ),
        ):
            client = self.client()
            path = {
                "project_id": PROJECT,
                "plan_id": response["resource"]["plan_id"],
                "request_id": response["resource"]["sync_request_id"],
            }
            with patch.object(
                client._opener, "open", return_value=BytesIO(json.dumps(response).encode())
            ):
                with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                    client.call(Op.GET_AUDIENCE_QUERY_SYNC, path=path)

    def test_wrong_response_project_and_exact_request_rejected(self):
        response = {
            "project_id": PROJECT,
            "resource": {"plan_id": "plan", "materialization_request_id": "wrong"},
        }
        with self.assertRaises(ContractViolation):
            validate_response_binding(
                Op.GET_AUDIENCE_QUERY_MATERIALIZATION,
                response,
                {"project_id": PROJECT, "plan_id": "plan", "request_id": "right"},
                {},
            )
        response["project_id"] = "vicohome"
        with self.assertRaises(ContractViolation):
            validate_response_binding(
                Op.GET_QUERY_CAPABILITIES, response, {"project_id": PROJECT}, {}
            )
