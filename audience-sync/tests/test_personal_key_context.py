"""Executable bootstrap, per-key isolation and credential boundary regressions."""

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from test_project_query import CAPABILITY

from audience_sync.cli import main
from audience_sync.client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
from audience_sync.key_configuration import configured_keys, summarize_project_keys
from audience_sync.project_operations import (
    PERSONAL_KEY_OPERATION_SPECS,
    PERSONAL_KEY_PATH,
    PROJECT_OPERATION_SPECS,
    PersonalKeyOperation,
    ProjectOperation,
)

SELF = PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT
KEY_A = "awpk_v2_" + "a" * 26 + "_" + "A" * 43
KEY_B = "awpk_v2_" + "b" * 26 + "_" + "B" * 43
ORIGIN = "https://platform.example.invalid"
REVISION = "pbr_" + "a" * 64
CONTEXT = {
    "project_id": "example-a",
    "binding_revision": REVISION,
    "credential_profile": "audience_sync",
    "allowed_actions": ["audience_sync.capabilities.read", "audience_sync.preview.read"],
}
ENV = {"AUDIENCE_SYNC_API_KEY": KEY_A, "AUDIENCE_PLATFORM_BASE_URL": ORIGIN}


def response(value):
    return BytesIO(json.dumps(value).encode())


def failure(status, code=None):
    body = {"detail": {"code": code}} if code else {"detail": "Not Found"}
    return HTTPError(ORIGIN + PERSONAL_KEY_PATH, status, "failure", {}, response(body))


class PersonalKeyContextTests(unittest.TestCase):
    def test_cli_missing_key_returns_only_static_url_before_network(self):
        output = StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(b"{}"))),
            patch("audience_sync.client.build_opener") as opener,
            redirect_stdout(output),
        ):
            self.assertEqual(main([SELF.value, "--request-stdin"]), 1)
        opener.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload, {"ok": False, "error": "sync_api_key_missing",
                                  "hint": "https://micro-app-platform-us.addx.live/audience-sync-us"})

    def test_cli_summary_url_only_when_all_credentials_definitively_invalid(self):
        for effects, should_hint in (
            ([failure(401, "personal_key_invalid"), failure(401, "personal_key_invalid")], True),
            ([failure(401, "personal_key_invalid"), response(CONTEXT)], False),
            ([failure(401, "personal_key_invalid"), failure(503)], False),
            ([failure(401, "personal_key_invalid"), failure(403)], False),
            ([failure(401, "personal_key_invalid"), URLError("unavailable")], False),
        ):
            output = StringIO()
            with (
                patch.dict("os.environ", {
                    "AUDIENCE_SYNC_API_KEYS": json.dumps({"first": KEY_A, "second": KEY_B})
                }, clear=True),
                patch("audience_sync.client.build_opener") as opener,
                redirect_stdout(output),
            ):
                opener.return_value.open.side_effect = effects
                self.assertEqual(main(["summarize_project_keys"]), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual("hint" in payload, should_hint)
            if should_hint:
                self.assertEqual(payload["hint"],
                                 "https://micro-app-platform-us.addx.live/audience-sync-us")
            self.assertNotIn(KEY_A, output.getvalue())
            self.assertNotIn(KEY_B, output.getvalue())

    def client(self, project=""):
        return AudienceSyncClient(
            AudienceSyncConfig(
                base_url=ORIGIN,
                sync_api_key=KEY_A,
                contract_revision="audience-project-v3",
                project_id=project,
            )
        )

    def test_key_only_bootstrap_targets_production_without_origin_or_project_input(self):
        with (
            patch.dict("os.environ", {"AUDIENCE_SYNC_API_KEY": KEY_A}, clear=True),
            patch("audience_sync.client.build_opener") as opener,
        ):
            opener.return_value.open.return_value = response(CONTEXT)
            config = AudienceSyncConfig.from_environment(project_operation=True)
            self.assertEqual(AudienceSyncClient(config).call(SELF), CONTEXT)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(
                request.full_url,
                "https://audience-workflow-api-prod-us.addx.live" + PERSONAL_KEY_PATH,
            )

    def test_cli_preserves_http_status_without_reflecting_server_body(self):
        for status, server_code, safe_code in (
            (404, None, "audience_sync_request_failed"),
            (401, "personal_key_invalid", "personal_key_invalid"),
            (503, None, "audience_sync_request_failed"),
            (None, None, "transport_error"),
        ):
            output = StringIO()
            error = (
                HTTPError(ORIGIN, status, "private diagnostic", {}, BytesIO(
                    json.dumps({"detail": {"code": server_code}, "token": KEY_A}).encode()
                    if server_code else ("private response " + KEY_A).encode()
                )) if status else URLError("private transport " + KEY_A)
            )
            with (
                self.subTest(status=status),
                patch.dict("os.environ", {**ENV, "AUDIENCE_PROJECT_ID": "example-a"}, clear=True),
                patch("audience_sync.client.build_opener") as opener,
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(
                    b'{"path":{"project_id":"example-a"}}'
                ))),
                redirect_stdout(output),
            ):
                opener.return_value.open.side_effect = error
                self.assertEqual(main(["get_query_capabilities", "--request-stdin"]), 1)
                expected = {"ok": False, "error": safe_code}
                if status:
                    expected["http_status"] = status
                if status == 401:
                    expected["hint"] = "https://micro-app-platform-us.addx.live/audience-sync-us"
                self.assertEqual(json.loads(output.getvalue()), expected)
                self.assertNotIn(KEY_A, output.getvalue())
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_one_unscoped_operation_does_not_expand_the_ten_project_operations(self):
        self.assertEqual(len(PROJECT_OPERATION_SPECS), 13)
        self.assertEqual(set(PERSONAL_KEY_OPERATION_SPECS), {SELF})
        spec = PERSONAL_KEY_OPERATION_SPECS[SELF]
        self.assertEqual((spec.method, spec.path), ("GET", PERSONAL_KEY_PATH))
        self.assertEqual(spec.path_parameters, ())
        self.assertEqual(spec.query_parameters, ())
        self.assertFalse(spec.required_body | spec.optional_body)

    def test_authenticated_response_binds_only_this_client_then_preserves_guards(self):
        client = self.client()
        capability = copy.deepcopy(CAPABILITY)
        capability["project_id"] = CONTEXT["project_id"]
        with patch.object(
            client._opener, "open", side_effect=[response(CONTEXT), response(capability)]
        ) as open_request:
            self.assertEqual(client.call(SELF), CONTEXT)
            self.assertEqual(
                client.call(
                    ProjectOperation.GET_QUERY_CAPABILITIES,
                    path={"project_id": CONTEXT["project_id"]},
                ),
                capability,
            )
        first = open_request.call_args_list[0].args[0]
        self.assertEqual(first.full_url, ORIGIN + PERSONAL_KEY_PATH)
        self.assertIsNone(first.data)
        self.assertEqual(first.get_header("Authorization"), "Bearer " + KEY_A)
        other = self.client()
        with patch.object(other._opener, "open") as untouched:
            with self.assertRaisesRegex(SafeApiError, "project_binding_missing"):
                other.call(
                    ProjectOperation.GET_QUERY_CAPABILITIES,
                    path={"project_id": CONTEXT["project_id"]},
                )
            untouched.assert_not_called()
        with self.assertRaisesRegex(SafeApiError, "project_binding_mismatch"):
            client.call(ProjectOperation.GET_QUERY_CAPABILITIES, path={"project_id": "other"})

    def test_self_rejects_selectors_wrong_project_extra_fields_and_unknown_actions(self):
        for part in ("path", "query", "body"):
            client = self.client()
            with patch.object(client._opener, "open") as untouched:
                with self.assertRaises(SafeApiError):
                    client.call(SELF, **{part: {"project_id": "other"}})
                untouched.assert_not_called()
        client = self.client("configured-other")
        with patch.object(client._opener, "open", return_value=response(CONTEXT)):
            with self.assertRaisesRegex(SafeApiError, "project_binding_mismatch"):
                client.call(SELF)
        for payload in (
            {**CONTEXT, "token": KEY_A},
            {**CONTEXT, "credential_profile": "admin"},
            {**CONTEXT, "allowed_actions": ["admin.everything"]},
            {**CONTEXT, "allowed_actions": CONTEXT["allowed_actions"] * 2},
            {**CONTEXT, "allowed_actions": list(reversed(CONTEXT["allowed_actions"]))},
        ):
            client = self.client()
            with patch.object(client._opener, "open", return_value=response(payload)):
                with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                    client.call(SELF)
            self.assertEqual(client._config.project_id, "")

    def test_verified_binding_revision_must_match_subsequent_capability_response(self):
        client = self.client()
        capability = copy.deepcopy(CAPABILITY)
        capability.update(project_id=CONTEXT["project_id"], binding_revision="pbr_" + "b" * 64)
        with patch.object(
            client._opener, "open", side_effect=[response(CONTEXT), response(capability)]
        ):
            client.call(SELF)
            with self.assertRaisesRegex(SafeApiError, "invalid_response"):
                client.call(
                    ProjectOperation.GET_QUERY_CAPABILITIES,
                    path={"project_id": CONTEXT["project_id"]},
                )

    def test_mixed_keys_keep_profile_permissions_and_failures_separate(self):
        research = {
            **CONTEXT,
            "project_id": "example-b",
            "credential_profile": "user_research",
            "allowed_actions": ["audience_sync.capabilities.read", "idea.read"],
        }
        environment = {
            "AUDIENCE_PLATFORM_BASE_URL": ORIGIN,
            "AUDIENCE_SYNC_API_KEYS": json.dumps(
                {
                    "sync": KEY_A,
                    "research": KEY_B,
                    "revoked": KEY_A,
                    "outage": KEY_B,
                }
            ),
        }
        outcomes = {"research": research, "sync": CONTEXT}
        # Sorted aliases yield outage, research, revoked, sync; no failure hides a valid key.
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("audience_sync.client.build_opener") as opener,
        ):
            opener.return_value.open.side_effect = [
                failure(503, "project_registry_unavailable"),
                response(research),
                failure(401, "invalid_project_personal_api_key"),
                response(CONTEXT),
            ]
            summary = summarize_project_keys()
        entries = {entry["alias"]: entry for entry in summary["keys"]}
        for alias, expected in outcomes.items():
            self.assertEqual(
                entries[alias], {"alias": alias, "status": "verified", "context": expected}
            )
        self.assertEqual(entries["revoked"]["status"], "invalid")
        self.assertEqual(entries["outage"]["status"], "unavailable")
        for alias in ("revoked", "outage"):
            self.assertNotIn("context", entries[alias])
        self.assertNotIn(KEY_A, json.dumps(summary))
        self.assertNotIn(KEY_B, json.dumps(summary))
        for call in opener.return_value.open.call_args_list:
            self.assertEqual(call.args[0].full_url, ORIGIN + PERSONAL_KEY_PATH)
            self.assertIsNone(call.args[0].data)

    def test_summary_distinguishes_old_server_transport_and_scoped_failures(self):
        for error, status, code in (
            (failure(404), "unavailable", "key_context_unavailable"),
            (URLError("offline"), "unavailable", "transport_error"),
            (
                failure(409, "project_binding_revision_stale"),
                "error",
                "project_binding_revision_stale",
            ),
            (failure(404, "project_not_found"), "error", "project_not_found"),
        ):
            with (
                self.subTest(status=status, code=code),
                patch.dict("os.environ", ENV, clear=True),
                patch("audience_sync.client.build_opener") as opener,
            ):
                opener.return_value.open.side_effect = error
                self.assertEqual(
                    summarize_project_keys(),
                    {"keys": [{
                        "alias": "default", "status": status, "error": code,
                        **({"http_status": error.code} if isinstance(error, HTTPError) else {}),
                    }]},
                )
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_bounded_explicit_alias_map_and_secret_free_config_repr(self):
        for raw in (
            "[]",
            "{}",
            '{"x":1}',
            '{"x":"a","x":"b"}',
            json.dumps({"bad alias": KEY_A}),
            json.dumps({"awpk_bad": KEY_A}),
            json.dumps({"a" + str(i): KEY_A for i in range(9)}),
            "x" * (16384 + 1),
        ):
            with patch.dict("os.environ", {"AUDIENCE_SYNC_API_KEYS": raw}, clear=True):
                with self.assertRaisesRegex(SafeApiError, "invalid_sync_api_keys"):
                    configured_keys()
        with patch.dict("os.environ", {**ENV, "AUDIENCE_SYNC_API_KEYS": '{"a":"b"}'}, clear=True):
            with self.assertRaisesRegex(SafeApiError, "ambiguous_sync_api_keys"):
                configured_keys()
        environment = {"AUDIENCE_SYNC_API_KEYS": json.dumps({"selected": KEY_A})}
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(SafeApiError, "key_alias_required"):
                AudienceSyncConfig.from_environment()
            with self.assertRaisesRegex(SafeApiError, "unknown_key_alias"):
                AudienceSyncConfig.from_environment(key_alias="missing")
            config = AudienceSyncConfig.from_environment(
                key_alias="selected", project_operation=True
            )
            self.assertNotIn(KEY_A, repr(config))

    def test_cli_alias_bootstraps_then_calls_only_selected_key_without_sending_alias(self):
        capability = copy.deepcopy(CAPABILITY)
        capability["project_id"] = CONTEXT["project_id"]
        environment = {
            "AUDIENCE_PLATFORM_BASE_URL": ORIGIN,
            "AUDIENCE_SYNC_API_KEYS": json.dumps({"selected": KEY_A, "other": KEY_B}),
        }
        output = StringIO()
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("audience_sync.client.build_opener") as opener,
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(b"{}"))),
            redirect_stdout(output),
        ):
            opener.return_value.open.side_effect = [response(CONTEXT), response(capability)]
            self.assertEqual(
                main(["get_query_capabilities", "--key-alias", "selected", "--request-stdin"]), 0
            )
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "result": capability})
        self.assertEqual(opener.return_value.open.call_count, 2)
        for call in opener.return_value.open.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY_A)
            self.assertNotIn("selected", request.full_url)
            self.assertNotIn("other", request.full_url)
            self.assertIsNone(request.data)
        self.assertNotIn(KEY_A, output.getvalue())

    def test_cli_does_not_fallback_after_selected_key_denial_or_accept_wrong_project(self):
        environment = {"AUDIENCE_SYNC_API_KEYS": json.dumps({"selected": KEY_A, "other": KEY_B})}
        for outcome, path, expected in (
            (
                failure(401, "invalid_project_personal_api_key"),
                {},
                "invalid_project_personal_api_key",
            ),
            (response(CONTEXT), {"project_id": "wrong"}, "project_binding_mismatch"),
        ):
            output = StringIO()
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("audience_sync.client.build_opener") as opener,
                patch(
                    "sys.stdin",
                    SimpleNamespace(buffer=BytesIO(json.dumps({"path": path}).encode())),
                ),
                redirect_stdout(output),
            ):
                opener.return_value.open.side_effect = [outcome]
                self.assertEqual(
                    main(["get_query_capabilities", "--key-alias", "selected", "--request-stdin"]),
                    1,
                )
                self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertEqual(json.loads(output.getvalue()), {
                "ok": False, "error": expected,
                **({"http_status": outcome.code} if isinstance(outcome, HTTPError) else {}),
                **({"hint": "https://micro-app-platform-us.addx.live/audience-sync-us"}
                   if isinstance(outcome, HTTPError) and outcome.code == 401 else {}),
            })

    def test_same_project_profiles_remain_separate_and_local_summary_is_not_inventory(self):
        research = {
            **CONTEXT,
            "credential_profile": "user_research",
            "allowed_actions": ["audience_sync.capabilities.read", "idea.read"],
        }
        environment = {"AUDIENCE_SYNC_API_KEYS": json.dumps({"research": KEY_B, "sync": KEY_A})}
        output = StringIO()
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("audience_sync.client.build_opener") as opener,
            redirect_stdout(output),
        ):
            opener.return_value.open.side_effect = [response(research), response(CONTEXT)]
            self.assertEqual(main(["summarize_project_keys"]), 0)
        entries = json.loads(output.getvalue())["result"]["keys"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["context"]["project_id"], entries[1]["context"]["project_id"])
        self.assertIn("idea.read", entries[0]["context"]["allowed_actions"])
        self.assertNotIn("idea.read", entries[1]["context"]["allowed_actions"])
        self.assertNotIn("idea.read", AudienceSyncClient.local_capabilities()["project_operations"])

    def test_summary_preserves_valid_key_after_invalid_local_key_and_configured_mismatch(self):
        environment = {"AUDIENCE_SYNC_API_KEYS": json.dumps({"broken": "bad", "valid": KEY_A})}
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("audience_sync.client.build_opener") as opener,
        ):
            opener.return_value.open.return_value = response(CONTEXT)
            entries = summarize_project_keys()["keys"]
            self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(entries[0]["status"], "error")
        self.assertNotIn("context", entries[0])
        self.assertEqual(entries[1]["status"], "verified")
        with (
            patch.dict("os.environ", {**ENV, "AUDIENCE_PROJECT_ID": "wrong"}, clear=True),
            patch("audience_sync.client.build_opener") as opener,
        ):
            opener.return_value.open.return_value = response(CONTEXT)
            self.assertEqual(
                summarize_project_keys()["keys"],
                [{"alias": "default", "status": "error", "error": "project_binding_mismatch"}],
            )

    def test_single_key_cli_bootstrap_and_explicit_binding_compatibility(self):
        capability = copy.deepcopy(CAPABILITY)
        capability["project_id"] = CONTEXT["project_id"]
        for environment, request, outcomes in (
            (ENV, {}, [CONTEXT, capability]),
            (
                {**ENV, "AUDIENCE_PROJECT_ID": CONTEXT["project_id"]},
                {"path": {"project_id": CONTEXT["project_id"]}},
                [capability],
            ),
        ):
            output = StringIO()
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("audience_sync.client.build_opener") as opener,
                patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
                redirect_stdout(output),
            ):
                opener.return_value.open.side_effect = [response(item) for item in outcomes]
                self.assertEqual(main(["get_query_capabilities", "--request-stdin"]), 0)
                self.assertEqual(opener.return_value.open.call_count, len(outcomes))
            self.assertEqual(json.loads(output.getvalue())["result"], capability)

    def test_legacy_self_context_rejected_locally_without_network(self):
        with (
            patch.dict(
                "os.environ",
                {**ENV, "AUDIENCE_SYNC_API_KEY": KEY_A.replace("awpk_v2_", "awpk_v1_")},
                clear=True,
            ),
            patch("audience_sync.client.build_opener") as opener,
        ):
            result = summarize_project_keys()
            opener.return_value.open.assert_not_called()
        self.assertEqual(result["keys"][0]["error"], "invalid_sync_api_key")
        self.assertNotIn("context", result["keys"][0])
