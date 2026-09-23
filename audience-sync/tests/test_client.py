from __future__ import annotations

import copy
import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from audience_sync import (
    OPERATION_SPECS,
    AudienceSyncClient,
    AudienceSyncConfig,
    Operation,
    SafeApiError,
)
from audience_sync.allowlist import generate_operation_specs, load_bundled_document

CLIENT_CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures/client-cases.json").read_text(encoding="utf-8")
)

AUDIENCE_ID = "aud_" + "a" * 26
SYNC_REQUEST_ID = "asrq_" + "b" * 26
MATERIALIZATION_REQUEST_ID = "amrq_" + "c" * 26
SYNC_API_KEY = "awpk_v1_" + "a" * 26 + "_" + "A" * 43
EXPANDED_AUDIENCE_FILTER = {
    "active_within_days": 30,
    "app_score_max": None,
    "app_score_min": None,
    "cities": [],
    "countries": [],
    "cuids": [],
    "device_quantity_max": None,
    "device_quantity_min": 1,
    "device_share_types": [],
    "device_types": [],
    "email_domains": [],
    "feeder_device_user": None,
    "first_bind_serial_numbers": [],
    "first_bound_at_from": None,
    "first_bound_at_to": None,
    "first_purchase_at_from": None,
    "first_purchase_at_to": None,
    "free_trial": None,
    "languages": [],
    "last_purchase_at_from": None,
    "last_purchase_at_to": None,
    "materialization_limit": None,
    "paid_amount_max": None,
    "paid_amount_min": None,
    "paid_count_max": None,
    "paid_count_min": None,
    "paid_user": None,
    "platforms": [],
    "refund_amount_max": None,
    "refund_amount_min": None,
    "refund_count_max": None,
    "refund_count_min": None,
    "registered_at_from": None,
    "registered_at_to": None,
    "registered_only": True,
    "serial_numbers": [],
    "sku_ids": [],
    "sku_names": [],
    "subscription_statuses": [],
    "tenants": ["vicohome"],
    "tier_service_types": [],
    "timezones": [],
}
AUDIENCE_SUMMARY = {
    "audience_id": AUDIENCE_ID,
    "name": "sg_paid",
    "product_scope": "vicohome",
    "audience_filter": {
        "registered_only": True,
        "paid_user": True,
        "active_within_days": 30,
    },
    "audience_filter_hash": "d" * 64,
    "audience_detail_url": (
        "https://micro-app-platform.example.test/audience-sync-admin/audiences/" + AUDIENCE_ID
    ),
}
SYNC_BODY = {
    "product_scope": "vicohome",
    "audience_id": AUDIENCE_ID,
    "materialization_run_id": "mat:exact:1",
    "expected_member_count": 42,
    "destination_id": "brevo_primary",
    "idempotency_key": "sync-request-1",
}


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._payload


class _Opener:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = []

    def open(self, request, timeout: float):
        self.calls.append((request, timeout))
        return _Response(self.payload)


class ClientTests(unittest.TestCase):
    def config(self) -> AudienceSyncConfig:
        return AudienceSyncConfig(
            base_url="https://audience.example.test",
            sync_api_key=SYNC_API_KEY,
            contract_revision="audience-sync-v2",
        )

    def test_local_capabilities_publish_operations_without_behavior_policy(self) -> None:
        payload = AudienceSyncClient.local_capabilities()
        self.assertEqual(payload["contract_state"], "published_operations")
        self.assertTrue(payload["business_operations_available"])
        self.assertTrue(payload["create_audience_available"])
        self.assertNotIn("effects_available", payload)
        self.assertNotIn("effects_require_confirmation", payload)
        self.assertEqual(len(payload["operations"]), 9)

    def test_create_has_no_id_and_preserves_authoritative_typed_filter(self) -> None:
        body = {
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter": {
                "registered_only": True,
                "paid_user": True,
                "active_within_days": 30,
            },
            "idempotency_key": "create-audience-1",
        }
        client = AudienceSyncClient(self.config())
        opener = _Opener({"outcome": "created", "audience": AUDIENCE_SUMMARY})
        client._opener = opener

        result = client.call(Operation.CREATE_AUDIENCE, body=body)

        self.assertEqual(result["audience"]["audience_filter"], body["audience_filter"])
        self.assertEqual(
            result["audience"]["audience_detail_url"],
            AUDIENCE_SUMMARY["audience_detail_url"],
        )
        request, _timeout = opener.calls[0]
        self.assertEqual(
            request.full_url,
            "https://audience.example.test/api/platform/v2/audience-sync/audiences",
        )
        self.assertEqual(json.loads(request.data), body)
        with self.assertRaisesRegex(SafeApiError, "invalid_body_parameters"):
            client.call(Operation.CREATE_AUDIENCE, body={**body, "audience_id": AUDIENCE_ID})

        with self.assertRaisesRegex(SafeApiError, "invalid_request_schema"):
            client.call(
                Operation.CREATE_AUDIENCE,
                body={**body, "name": CLIENT_CASES["legacy_non_ascii_audience_name"]},
            )

        for url in (
            "https://user:password@example.test/audiences/one",
            "https://example.test/audiences/one?token=opaque",
            "https://example.test/audiences/one#fragment",
        ):
            unsafe_audience = {**AUDIENCE_SUMMARY, "audience_detail_url": url}
            client._opener = _Opener({"outcome": "created", "audience": unsafe_audience})
            with self.subTest(url=url), self.assertRaisesRegex(
                SafeApiError, "invalid_response"
            ):
                client.call(Operation.CREATE_AUDIENCE, body=body)

    def test_create_accepts_expanded_idempotent_authoritative_readback(self) -> None:
        body = {
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter": {
                "active_within_days": 30,
                "registered_only": True,
                "device_quantity_min": 1,
            },
            "idempotency_key": "create-audience-replay-1",
        }
        audience = {**AUDIENCE_SUMMARY, "audience_filter": EXPANDED_AUDIENCE_FILTER}
        client = AudienceSyncClient(self.config())
        client._opener = _Opener({"outcome": "idempotent", "audience": audience})

        result = client.call(Operation.CREATE_AUDIENCE, body=body)

        self.assertEqual(result["outcome"], "idempotent")
        self.assertEqual(result["audience"]["audience_filter"], EXPANDED_AUDIENCE_FILTER)
        self.assertEqual(
            result["audience"]["audience_detail_url"],
            AUDIENCE_SUMMARY["audience_detail_url"],
        )

    def test_save_is_id_bound_and_requires_current_hash(self) -> None:
        body = {
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter": {"active_within_days": 30},
            "expected_audience_filter_hash": "c" * 64,
            "idempotency_key": "update-audience-1",
        }
        client = AudienceSyncClient(self.config())
        opener = _Opener({"outcome": "updated", "audience": AUDIENCE_SUMMARY})
        client._opener = opener

        client.call(Operation.SAVE_AUDIENCE, path={"audience_id": AUDIENCE_ID}, body=body)
        request, _timeout = opener.calls[0]
        self.assertTrue(request.full_url.endswith("/audiences/" + AUDIENCE_ID))
        missing_hash_body = {
            key: value
            for key, value in body.items()
            if key != "expected_audience_filter_hash"
        }
        with self.assertRaisesRegex(SafeApiError, "invalid_body_parameters"):
            client.call(
                Operation.SAVE_AUDIENCE,
                path={"audience_id": AUDIENCE_ID},
                body=missing_hash_body,
            )
        with self.assertRaisesRegex(SafeApiError, "invalid_request_schema"):
            client.call(
                Operation.SAVE_AUDIENCE,
                path={"audience_id": AUDIENCE_ID},
                body={**body, "expected_audience_filter_hash": None},
            )

    def test_fixed_client_calls_every_operation_without_local_behavior_gate(self) -> None:
        client = AudienceSyncClient(self.config())
        definition_calls = (
            (
                Operation.SEARCH_AUDIENCES,
                {},
                {"product_scope": "vicohome", "query": "active-paid-30d"},
                {"items": []},
            ),
            (
                Operation.CREATE_AUDIENCE,
                {},
                {
                    "name": "active-paid-30d",
                    "product_scope": "vicohome",
                    "audience_filter": {"active_within_days": 30},
                    "idempotency_key": "create-audience-dark-1",
                },
                {"outcome": "created", "audience": AUDIENCE_SUMMARY},
            ),
            (
                Operation.SAVE_AUDIENCE,
                {"audience_id": AUDIENCE_ID},
                {
                    "name": "active-paid-30d",
                    "product_scope": "vicohome",
                    "audience_filter": {"active_within_days": 30},
                    "expected_audience_filter_hash": "c" * 64,
                    "idempotency_key": "update-audience-dark-1",
                },
                {"outcome": "updated", "audience": AUDIENCE_SUMMARY},
            ),
        )
        for operation, path, body, response in definition_calls:
            client._opener = _Opener(response)
            with self.subTest(operation=operation):
                self.assertIsNotNone(client.call(operation, path=path, body=body))

        processing_calls = (
            (
                Operation.MATERIALIZE_AUDIENCE,
                {"audience_id": AUDIENCE_ID},
                {
                    "product_scope": "vicohome",
                    "expected_audience_filter_hash": "a" * 64,
                    "idempotency_key": "materialize-1",
                },
                {
                    "audience_id": AUDIENCE_ID,
                    "product_scope": "vicohome",
                    "materialization": {
                        "status": "queued",
                        "materialization_request_id": MATERIALIZATION_REQUEST_ID,
                    },
                    "idempotent": False,
                },
            ),
            (
                Operation.SYNC_AUDIENCE,
                {},
                SYNC_BODY,
                {"status": "queued", "sync_request_id": SYNC_REQUEST_ID},
            ),
        )
        for operation, path, body, response in processing_calls:
            client._opener = _Opener(response)
            with self.subTest(operation=operation):
                self.assertIsNotNone(client.call(operation, path=path, body=body))

    def test_exact_sync_binding_and_provider_neutral_native_status(self) -> None:
        client = AudienceSyncClient(self.config())
        opener = _Opener(
            {
                "status": "queued",
                "sync_request_id": SYNC_REQUEST_ID,
                "provider_kind": "mailchimp",
                "destination_id": "mailchimp_primary",
            }
        )
        client._opener = opener
        result = client.call(Operation.SYNC_AUDIENCE, body=SYNC_BODY)
        self.assertEqual(result["provider_kind"], "mailchimp")
        self.assertEqual(len(opener.calls), 1)
        request, _timeout = opener.calls[0]
        self.assertEqual(
            request.full_url,
            "https://audience.example.test/api/platform/v2/audience-sync/syncs",
        )
        self.assertEqual(json.loads(request.data), SYNC_BODY)

    def test_successful_sync_readback_preserves_only_safe_provider_resource_url(self) -> None:
        client = AudienceSyncClient(self.config())
        prefix = "https://provider.example.test/"
        maximum_url = prefix + "a" * (2048 - len(prefix))
        self.assertEqual(len(maximum_url), 2048)
        resource_url = "https://provider.example.test/resources/123?view=members#current"
        urls_with_at_outside_authority = (
            "https://provider.example.test/resources/@123",
            "https://provider.example.test/resources/123?view=@members",
            "https://provider.example.test/resources/123#@current",
        )
        base = {
            "status": "succeeded",
            "sync_request_id": SYNC_REQUEST_ID,
            "provider_kind": "brevo",
        }
        for payload in (
            base,
            {**base, "provider_resource_url": None},
            {**base, "provider_resource_url": resource_url},
            *(
                {**base, "provider_resource_url": url}
                for url in urls_with_at_outside_authority
            ),
            {**base, "provider_resource_url": maximum_url},
            {
                **base,
                "provider_kind": "mailchimp",
                "provider_resource_url": "https://provider.example.test/segments/123",
            },
        ):
            client._opener = _Opener(payload)
            with self.subTest(payload=payload):
                result = client.call(
                    Operation.GET_AUDIENCE_SYNC,
                    path={"sync_request_id": SYNC_REQUEST_ID},
                    query={"product_scope": "vicohome"},
                )
                self.assertEqual(result, payload)

        effect_client = AudienceSyncClient(self.config())
        effect_client._opener = _Opener({**base, "provider_resource_url": resource_url})
        effect_result = effect_client.call(Operation.SYNC_AUDIENCE, body=SYNC_BODY)
        self.assertEqual(effect_result["provider_resource_url"], resource_url)

        for url in (
            "http://provider.example.test/resources/123",
            "https://user@provider.example.test/resources/123",
            "https://user:password@provider.example.test/resources/123",
            "https:///resources/123",
            "https://[invalid",
            "https://provider.example.test/resources/ 123",
            maximum_url + "a",
        ):
            client._opener = _Opener({**base, "provider_resource_url": url})
            with self.subTest(url=url), self.assertRaisesRegex(
                SafeApiError, "invalid_response"
            ):
                client.call(
                    Operation.GET_AUDIENCE_SYNC,
                    path={"sync_request_id": SYNC_REQUEST_ID},
                    query={"product_scope": "vicohome"},
                )

    def test_environment_and_authorization_use_only_the_restricted_sync_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUDIENCE_PLATFORM_BASE_URL": "https://audience.example.test",
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                "AUDIENCE_API_KEY": "broad-key-must-not-be-used",
                "AUDIENCE_SYNC_CONTRACT_REVISION": "audience-sync-v2",
            },
            clear=True,
        ):
            config = AudienceSyncConfig.from_environment()

        self.assertEqual(config.sync_api_key, SYNC_API_KEY)
        client = AudienceSyncClient(config)
        opener = _Opener({"items": []})
        client._opener = opener
        client.call(
            Operation.SEARCH_AUDIENCES,
            body={"product_scope": "vicohome", "query": "sg", "limit": 1, "offset": 0},
        )

        request, _timeout = opener.calls[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SYNC_API_KEY)
        self.assertNotIn("broad-key-must-not-be-used", repr(request.header_items()))

    def test_token_only_environment_uses_reviewed_non_secret_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {"AUDIENCE_SYNC_API_KEY": SYNC_API_KEY},
            clear=True,
        ):
            config = AudienceSyncConfig.from_environment()

        self.assertEqual(
            config.base_url,
            "https://audience-workflow-api-prod-us.addx.live",
        )
        self.assertEqual(config.contract_revision, "audience-sync-v2")
        AudienceSyncClient(config)

    def test_controlled_environment_overrides_non_secret_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUDIENCE_PLATFORM_BASE_URL": "https://audience.example.test",
                "AUDIENCE_PLATFORM_TIMEOUT_SECONDS": "12",
                "AUDIENCE_SYNC_API_KEY": SYNC_API_KEY,
                "AUDIENCE_SYNC_CONTRACT_REVISION": "audience-sync-v2",
            },
            clear=True,
        ):
            config = AudienceSyncConfig.from_environment()

        self.assertEqual(config.base_url, "https://audience.example.test")
        self.assertEqual(config.timeout_seconds, 12)

    def test_missing_or_malformed_token_fails_without_reflection(self) -> None:
        for token, error in (
            ("", "sync_api_key_missing"),
            ("not-a-personal-key", "invalid_sync_api_key"),
            (SYNC_API_KEY + "\n", "invalid_sync_api_key"),
        ):
            config = AudienceSyncConfig(
                base_url="https://audience.example.test",
                sync_api_key=token,
                contract_revision="audience-sync-v2",
            )
            with self.subTest(token_length=len(token)), self.assertRaisesRegex(
                SafeApiError, error
            ) as rejected:
                AudienceSyncClient(config)
            if token:
                self.assertNotIn(token, repr(rejected.exception))

    def test_http_error_code_cannot_reflect_the_restricted_token(self) -> None:
        reflected_token = "awpk_v1_" + "a" * 26 + "_" + "b" * 43
        client = AudienceSyncClient(
            AudienceSyncConfig(
                base_url="https://audience.example.test",
                sync_api_key=reflected_token,
                contract_revision="audience-sync-v2",
            )
        )
        response = BytesIO(
            json.dumps({"detail": {"code": "failure_" + reflected_token}}).encode()
        )
        error = HTTPError(
            "https://audience.example.test",
            500,
            "failure",
            hdrs=None,
            fp=response,
        )
        with patch.object(client._opener, "open", side_effect=error):
            with self.assertRaisesRegex(
                SafeApiError, "audience_sync_request_failed"
            ) as rejected:
                client.call(
                    Operation.GET_SYNC_CAPABILITIES,
                    query={"product_scope": "vicohome"},
                )
        self.assertNotIn(reflected_token, repr(rejected.exception))

    def test_optional_query_parameter_may_be_omitted(self) -> None:
        document = copy.deepcopy(load_bundled_document())
        operation_contract = document["paths"][
            "/api/platform/v2/audience-sync/capabilities"
        ]["get"]
        operation_contract["parameters"][0]["required"] = False
        spec = generate_operation_specs(document)["get_sync_capabilities"]
        response = {
            "product_scope": "vicohome",
            "contract_revision": "audience-sync-v2",
            "create_audience_available": True,
            "definition_available": True,
            "materialization_available": True,
            "brevo_configured": False,
            "brevo_effect_ready": False,
            "mailchimp_configured": False,
            "mailchimp_effect_ready": False,
            "destinations": [],
        }
        client = AudienceSyncClient(self.config())
        opener = _Opener(response)
        client._opener = opener

        with (
            patch.dict(OPERATION_SPECS, {Operation.GET_SYNC_CAPABILITIES: spec}),
            patch(
                "audience_sync.contract_validation.load_bundled_document",
                return_value=document,
            ),
        ):
            result = client.call(Operation.GET_SYNC_CAPABILITIES)

        self.assertEqual(result, response)
        self.assertEqual(
            opener.calls[0][0].full_url,
            "https://audience.example.test/api/platform/v2/audience-sync/capabilities",
        )

    def test_unknown_or_wrong_operation_response_fails_before_projection(self) -> None:
        invalid_payloads = (
            {"status": "queued", "sync_request_id": SYNC_REQUEST_ID, "list_id": 123},
            {"items": []},
            {"status": "not-a-native-status", "sync_request_id": SYNC_REQUEST_ID},
        )
        for payload in invalid_payloads:
            client = AudienceSyncClient(self.config())
            client._opener = _Opener(payload)
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(SafeApiError, "invalid_response"),
            ):
                client.call(Operation.SYNC_AUDIENCE, body=SYNC_BODY)

    def test_materialization_and_sync_request_ids_are_not_interchangeable(self) -> None:
        cases = (
            (
                Operation.GET_MATERIALIZATION,
                {"materialization_request_id": SYNC_REQUEST_ID},
            ),
            (Operation.GET_AUDIENCE_SYNC, {"sync_request_id": MATERIALIZATION_REQUEST_ID}),
        )
        for operation, path in cases:
            client = AudienceSyncClient(self.config())
            with patch.object(client._opener, "open", side_effect=AssertionError("network")):
                with (
                    self.subTest(operation=operation),
                    self.assertRaisesRegex(SafeApiError, "invalid_identifier"),
                ):
                    client.call(
                        operation,
                        path=path,
                        query={"product_scope": "vicohome"},
                    )

    def test_transport_error_is_not_retried(self) -> None:
        client = AudienceSyncClient(self.config())
        with patch.object(client._opener, "open", side_effect=URLError("timeout")) as opened:
            with self.assertRaisesRegex(SafeApiError, "transport_error"):
                client.call(Operation.SYNC_AUDIENCE, body=SYNC_BODY)
        self.assertEqual(opened.call_count, 1)

    def test_registered_only_is_strict_bool_and_paid_user_is_nullable(self) -> None:
        base = {
            "name": "sg_paid",
            "product_scope": "vicohome",
            "idempotency_key": "save-audience-1",
        }
        client = AudienceSyncClient(self.config())
        client._opener = _Opener({"outcome": "updated", "audience": AUDIENCE_SUMMARY})
        result = client.call(
            Operation.SAVE_AUDIENCE,
            path={"audience_id": AUDIENCE_ID},
            body={
                **base,
                "audience_filter": {"registered_only": True, "paid_user": None},
                "expected_audience_filter_hash": "c" * 64,
            },
        )
        self.assertEqual(result["outcome"], "updated")
        for invalid in (None, 0, 1, "true"):
            with (
                self.subTest(registered_only=invalid),
                self.assertRaisesRegex(SafeApiError, "invalid_audience_filter"),
            ):
                client.call(
                    Operation.SAVE_AUDIENCE,
                    path={"audience_id": AUDIENCE_ID},
                    body={
                        **base,
                        "audience_filter": {"registered_only": invalid},
                        "expected_audience_filter_hash": "c" * 64,
                    },
                )

    def test_complete_profile_filter_is_typed_and_bounded(self) -> None:
        audience_filter = {
            "email_domains": ["example.com"],
            "first_bind_serial_numbers": ["first-001"],
            "serial_numbers": ["device-001"],
            "cuids": ["cuid-001"],
            "feeder_device_user": True,
            "registered_at_from": "2026-01-01T00:00:00Z",
            "registered_at_to": "2026-01-31T23:59:59+00:00",
            "first_bound_at_from": "2026-01-02T00:00:00+08:00",
            "first_bound_at_to": "2026-01-30T00:00:00+08:00",
            "free_trial": False,
            "sku_ids": ["sku-001"],
            "tier_service_types": ["premium"],
            "first_purchase_at_from": "2026-01-03T00:00:00Z",
            "first_purchase_at_to": "2026-01-20T00:00:00Z",
            "last_purchase_at_from": "2026-01-04T00:00:00Z",
            "last_purchase_at_to": "2026-01-25T00:00:00Z",
            "refund_amount_min": 1.5,
            "refund_amount_max": 10,
            "materialization_limit": 1,
        }
        body = {
            "name": "complete-profile-filter",
            "product_scope": "vicohome",
            "audience_filter": audience_filter,
            "idempotency_key": "complete-filter-1",
        }
        authoritative = {**AUDIENCE_SUMMARY, "audience_filter": audience_filter}
        client = AudienceSyncClient(self.config())
        opener = _Opener({"outcome": "created", "audience": authoritative})
        client._opener = opener

        result = client.call(Operation.CREATE_AUDIENCE, body=body)

        self.assertEqual(result["audience"]["audience_filter"], audience_filter)
        self.assertEqual(json.loads(opener.calls[0][0].data)["audience_filter"], audience_filter)

    def test_exact_keyword_search_preserves_authoritative_filter_and_detail_url(self) -> None:
        audience_filter = EXPANDED_AUDIENCE_FILTER
        audience = {**AUDIENCE_SUMMARY, "audience_filter": audience_filter}
        client = AudienceSyncClient(self.config())
        opener = _Opener({"items": [audience]})
        client._opener = opener

        result = client.call(
            Operation.SEARCH_AUDIENCES,
            body={
                "product_scope": "vicohome",
                "query": "active-paid-30d",
                "limit": 1,
                "offset": 0,
            },
        )

        self.assertEqual(result["items"], [audience])
        self.assertEqual(
            result["items"][0]["audience_detail_url"],
            AUDIENCE_SUMMARY["audience_detail_url"],
        )

    def test_new_profile_filter_values_fail_closed(self) -> None:
        client = AudienceSyncClient(self.config())
        base = {
            "name": "invalid-profile-filter",
            "product_scope": "vicohome",
            "idempotency_key": "invalid-filter-1",
        }
        invalid_filters = (
            {"cuids": ["cuid"] * 51},
            {"serial_numbers": [""]},
            {"feeder_device_user": "true"},
            {"free_trial": 1},
            {"registered_at_from": "2026-01-01T00:00:00"},
            {"registered_at_from": "2026-01-01T00:00Z"},
            {"registered_at_from": "2026-W01-4T00:00:00Z"},
            {"registered_at_from": "20260101T000000Z"},
            {
                "registered_at_from": "2026-02-01T00:00:00Z",
                "registered_at_to": "2026-01-01T00:00:00Z",
            },
            {
                "first_purchase_at_from": "2026-01-02T00:00:00+08:00",
                "first_purchase_at_to": "2026-01-01T00:00:00+08:00",
            },
            {"refund_amount_min": 2, "refund_amount_max": 1},
        )
        for audience_filter in invalid_filters:
            with (
                self.subTest(audience_filter=audience_filter),
                self.assertRaisesRegex(SafeApiError, "invalid_audience_filter"),
            ):
                client.call(
                    Operation.CREATE_AUDIENCE,
                    body={**base, "audience_filter": audience_filter},
                )

    def test_filter_and_transport_inputs_fail_closed(self) -> None:
        client = AudienceSyncClient(self.config())
        for extra in (
            "url",
            "method",
            "headers",
            "provider_payload",
            "provider_resource_url",
            "folder_id",
            "list_id",
        ):
            with (
                self.subTest(extra=extra),
                self.assertRaisesRegex(SafeApiError, "invalid_body_parameters"),
            ):
                client.call(Operation.SYNC_AUDIENCE, body={**SYNC_BODY, extra: "untrusted"})
        client._opener = _Opener({"outcome": "updated", "audience": AUDIENCE_SUMMARY})
        for invalid_filter in (
            {"sql": "select 1"},
            {"countries": "SG"},
            {"countries": ["https://example.invalid"]},
            {"active_within_days": 0},
            {"paid_amount_min": float("inf")},
            {"paid_count_min": 10, "paid_count_max": 1},
        ):
            with (
                self.subTest(invalid_filter=invalid_filter),
                self.assertRaisesRegex(SafeApiError, "invalid_audience_filter"),
            ):
                client.call(
                    Operation.SAVE_AUDIENCE,
                    path={"audience_id": AUDIENCE_ID},
                    body={
                        "name": "sg_paid",
                        "product_scope": "vicohome",
                        "idempotency_key": "save-audience-1",
                        "expected_audience_filter_hash": "c" * 64,
                        "audience_filter": invalid_filter,
                    },
                )

    def test_origin_rejects_paths_credentials_and_non_https(self) -> None:
        for origin in (
            "http://audience.example.test",
            "https://user:pass@audience.example.test",
            "https://audience.example.test/api",
            "https://audience.example.test?next=evil",
        ):
            with self.subTest(origin=origin), self.assertRaises(SafeApiError):
                AudienceSyncClient(
                    AudienceSyncConfig(
                        base_url=origin,
                        sync_api_key=SYNC_API_KEY,
                        contract_revision="audience-sync-v2",
                    )
                )


if __name__ == "__main__":
    unittest.main()
