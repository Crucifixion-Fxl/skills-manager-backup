from __future__ import annotations

import unittest

from audience_sync import Operation
from audience_sync.contract_validation import ContractViolation, validate_operation_response

AUDIENCE_ID = "aud_" + "a" * 26
AMRQ = "amrq_" + "b" * 26
ASRQ = "asrq_" + "c" * 26


class ContractValidationTests(unittest.TestCase):
    def test_all_nine_operation_success_schemas_accept_native_minimums(self) -> None:
        audience = {
            "audience_id": AUDIENCE_ID,
            "name": "sg_paid",
            "product_scope": "vicohome",
            "audience_filter": {"registered_only": True},
            "audience_filter_hash": "d" * 64,
        }
        materialization = {"status": "queued", "materialization_request_id": AMRQ}
        cases = {
            Operation.GET_SYNC_CAPABILITIES: {
                "product_scope": "vicohome",
                "contract_revision": "audience-sync-v2",
                "create_audience_available": True,
                "definition_available": True,
                "materialization_available": True,
                "brevo_configured": True,
                "brevo_effect_ready": False,
                "mailchimp_configured": True,
                "mailchimp_effect_ready": False,
                "exact_materialization_run_required": True,
                "max_members": None,
                "destinations": [
                    {
                        "destination_id": "brevo_primary",
                        "kind": "brevo",
                        "label": "Brevo staging folder",
                    },
                    {
                        "destination_id": "mailchimp_primary",
                        "kind": "mailchimp",
                        "label": "Mailchimp staging list",
                    },
                ],
            },
            Operation.SEARCH_AUDIENCES: {"items": [audience]},
            Operation.CREATE_AUDIENCE: {"audience": audience, "outcome": "created"},
            Operation.SAVE_AUDIENCE: {"audience": audience, "outcome": "updated"},
            Operation.PREVIEW_AUDIENCE: {
                "audience_id": AUDIENCE_ID,
                "product_scope": "vicohome",
                "audience_filter_hash": "d" * 64,
                "criteria_summary": "country is SG",
                "member_count": 42,
            },
            Operation.MATERIALIZE_AUDIENCE: {
                "audience_id": AUDIENCE_ID,
                "product_scope": "vicohome",
                "materialization": materialization,
                "idempotent": False,
            },
            Operation.GET_MATERIALIZATION: {
                "audience_id": AUDIENCE_ID,
                "product_scope": "vicohome",
                "materialization": materialization,
                "idempotent": True,
            },
            Operation.SYNC_AUDIENCE: {"sync_request_id": ASRQ, "status": "queued"},
            Operation.GET_AUDIENCE_SYNC: {"sync_request_id": ASRQ, "status": "running"},
        }
        self.assertEqual(len(cases), 9)
        for operation, payload in cases.items():
            with self.subTest(operation=operation):
                self.assertEqual(validate_operation_response(operation, payload), payload)

    def test_operation_schema_mismatch_and_unknown_fields_fail(self) -> None:
        for payload in (
            {"items": []},
            {"sync_request_id": ASRQ, "status": "queued", "list_id": 12},
            {"sync_request_id": AMRQ, "status": "queued"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ContractViolation):
                validate_operation_response(Operation.SYNC_AUDIENCE, payload)

    def test_old_eight_operation_capabilities_fail_the_create_gate(self) -> None:
        old_host = {
            "product_scope": "vicohome",
            "contract_revision": "audience-sync-v2",
            "definition_available": True,
            "materialization_available": True,
            "brevo_configured": False,
            "brevo_effect_ready": False,
            "mailchimp_configured": False,
            "mailchimp_effect_ready": False,
            "destinations": [],
        }
        with self.assertRaises(ContractViolation):
            validate_operation_response(Operation.GET_SYNC_CAPABILITIES, old_host)

    def test_create_response_rejects_unknown_summary_and_filter_fields(self) -> None:
        base = {
            "audience_id": AUDIENCE_ID,
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter_hash": "d" * 64,
            "audience_filter": {
                "registered_only": True,
                "paid_user": True,
                "active_within_days": 30,
            },
        }
        invalid_audiences = (
            {**base, "sql": "not-allowed"},
            {**base, "url": "https://attacker.example.test/audiences/one"},
            {**base, "audience_filter": {**base["audience_filter"], "unknown": True}},
            {key: value for key, value in base.items() if key != "audience_filter"},
            {key: value for key, value in base.items() if key != "audience_filter_hash"},
        )
        for audience in invalid_audiences:
            with self.subTest(audience=audience), self.assertRaises(ContractViolation):
                validate_operation_response(
                    Operation.CREATE_AUDIENCE,
                    {"audience": audience, "outcome": "created"},
                )

    def test_complete_filter_response_requires_timezone_aware_rfc3339(self) -> None:
        base = {
            "audience_id": AUDIENCE_ID,
            "name": "complete-profile-filter",
            "product_scope": "vicohome",
            "audience_filter_hash": "d" * 64,
        }
        audience_filter = {
            "cuids": ["cuid-001"],
            "serial_numbers": ["serial-001"],
            "registered_at_from": "2026-01-01T00:00:00Z",
            "free_trial": False,
            "refund_amount_max": 12.5,
            "materialization_limit": 1,
        }
        payload = {
            "audience": {**base, "audience_filter": audience_filter},
            "outcome": "created",
        }

        self.assertEqual(validate_operation_response(Operation.CREATE_AUDIENCE, payload), payload)

        for invalid_datetime in (
            "2026-01-01T00:00:00",
            "2026-01-01T00:00Z",
            "2026-W01-4T00:00:00Z",
            "20260101T000000Z",
        ):
            invalid = {
                "audience": {
                    **base,
                    "audience_filter": {
                        **audience_filter,
                        "registered_at_from": invalid_datetime,
                    },
                },
                "outcome": "created",
            }
            with (
                self.subTest(invalid_datetime=invalid_datetime),
                self.assertRaises(ContractViolation),
            ):
                validate_operation_response(Operation.CREATE_AUDIENCE, invalid)

        invalid_limit = {
            "audience": {
                **base,
                "audience_filter": {**audience_filter, "materialization_limit": 2},
            },
            "outcome": "created",
        }
        with self.assertRaises(ContractViolation):
            validate_operation_response(Operation.CREATE_AUDIENCE, invalid_limit)

    def test_optional_audience_detail_url_is_https_and_omittable(self) -> None:
        audience = {
            "audience_id": AUDIENCE_ID,
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter_hash": "d" * 64,
            "audience_filter": {"active_within_days": 30},
        }
        without_url = {"audience": audience, "outcome": "created"}
        self.assertEqual(
            validate_operation_response(Operation.CREATE_AUDIENCE, without_url),
            without_url,
        )
        with_url = {
            "audience": {
                **audience,
                "audience_detail_url": (
                    "https://micro-app-platform.example.test/audience-sync-admin/audiences/"
                    + AUDIENCE_ID
                ),
            },
            "outcome": "created",
        }
        self.assertEqual(
            validate_operation_response(Operation.CREATE_AUDIENCE, with_url),
            with_url,
        )
        for url in (
            "http://example.test/one",
            "https://user:password@example.test/one",
            "https://example.test/one?token=opaque",
            "https://example.test/one#fragment",
        ):
            invalid_url = {
                "audience": {**audience, "audience_detail_url": url},
                "outcome": "created",
            }
            with self.subTest(url=url), self.assertRaises(ContractViolation):
                validate_operation_response(Operation.CREATE_AUDIENCE, invalid_url)

    def test_provider_resource_url_is_bounded_safe_and_success_only(self) -> None:
        prefix = "https://provider.example.test/"
        maximum_url = prefix + "a" * (2048 - len(prefix))
        self.assertEqual(len(maximum_url), 2048)
        urls_with_at_outside_authority = (
            "https://provider.example.test/resources/@123",
            "https://provider.example.test/resources/123?view=@members",
            "https://provider.example.test/resources/123#@current",
        )
        base = {
            "sync_request_id": ASRQ,
            "status": "succeeded",
            "provider_kind": "brevo",
        }
        for payload in (
            base,
            {**base, "provider_resource_url": None},
            {
                **base,
                "provider_resource_url": (
                    "https://provider.example.test/resources/123?view=members#current"
                ),
            },
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
            with self.subTest(payload=payload):
                self.assertEqual(
                    validate_operation_response(Operation.GET_AUDIENCE_SYNC, payload),
                    payload,
                )

        for url in (
            "http://provider.example.test/resources/123",
            "https://user@provider.example.test/resources/123",
            "https://user:password@provider.example.test/resources/123",
            "https:///resources/123",
            "https://[invalid",
            "https://provider.example.test/resources/ 123",
            maximum_url + "a",
        ):
            with self.subTest(url=url), self.assertRaises(ContractViolation):
                validate_operation_response(
                    Operation.GET_AUDIENCE_SYNC,
                    {**base, "provider_resource_url": url},
                )

        with self.assertRaises(ContractViolation):
            validate_operation_response(
                Operation.GET_AUDIENCE_SYNC,
                {
                    "sync_request_id": ASRQ,
                    "status": "running",
                    "provider_resource_url": "https://provider.example.test/resources/123",
                },
            )

    def test_search_detail_url_requires_one_authoritative_result(self) -> None:
        audience = {
            "audience_id": AUDIENCE_ID,
            "name": "active-paid-30d",
            "product_scope": "vicohome",
            "audience_filter_hash": "d" * 64,
            "audience_filter": {"active_within_days": 30},
            "audience_detail_url": "https://micro-app.example.test/audiences/" + AUDIENCE_ID,
        }
        exact = {"items": [audience]}
        self.assertEqual(validate_operation_response(Operation.SEARCH_AUDIENCES, exact), exact)
        for payload in (
            {"items": [audience, {**audience, "audience_id": "aud_" + "b" * 26}]},
            {
                "items": [
                    {key: value for key, value in audience.items() if key != "audience_filter"}
                ]
            },
            {
                "items": [
                    {
                        key: value
                        for key, value in audience.items()
                        if key != "audience_filter_hash"
                    }
                ]
            },
        ):
            with self.subTest(payload=payload), self.assertRaises(ContractViolation):
                validate_operation_response(Operation.SEARCH_AUDIENCES, payload)


if __name__ == "__main__":
    unittest.main()
