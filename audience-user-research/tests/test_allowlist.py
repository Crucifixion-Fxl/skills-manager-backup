from __future__ import annotations

import unittest
from copy import deepcopy

from user_research.allowlist import (
    REVIEWED_ROUTES,
    generate_operation_specs,
    load_bundled_document,
    operation_registry_document,
)
from user_research.operations import OPERATION_SPECS, Operation


class AllowlistTests(unittest.TestCase):
    def test_exact_reviewed_inventory_is_derived_from_bundled_contracts(self) -> None:
        generated = generate_operation_specs()
        expected = {route.label for route in REVIEWED_ROUTES}
        self.assertEqual(set(generated), expected)
        self.assertEqual({operation.value for operation in OPERATION_SPECS}, expected)
        self.assertEqual({operation.value for operation in Operation}, expected)
        self.assertIn("get_project_personal_key_context", expected)
        self.assertIn("personal_audience_sync_capabilities", expected)
        self.assertIn("personal_idea_create", expected)
        self.assertEqual(
            len({name for name in expected if name.startswith("personal_research_")}), 18
        )
        self.assertEqual(
            generated["personal_research_journey_responses_csv"].response_mode,
            "attachment",
        )
        self.assertEqual(
            generated["personal_research_journey_idea_summary_csv"].response_mode,
            "attachment",
        )
        self.assertIn("personal_research_journey_idea_summary", expected)

    def test_unrelated_provider_and_delivery_operations_are_closed(self) -> None:
        generated = generate_operation_specs()
        for name in (
            "create_surveymonkey_survey",
            "create_brevo_campaign",
            "send_campaign",
            "start_campaign_review",
            "request_mailchimp_segment_sync",
            "export_apify_dataset",
        ):
            self.assertNotIn(name, generated)

    def test_contract_addition_or_operation_id_drift_is_not_granted(self) -> None:
        document = load_bundled_document("v3")
        changed = deepcopy(document)
        changed["paths"]["/api/platform/v3/personal-key"]["get"]["operationId"] = "get_all_keys"
        changed["paths"]["/api/platform/v3/arbitrary"] = {
            "post": {
                "operationId": "send_everything",
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                },
            }
        }
        specs = generate_operation_specs((changed,))
        self.assertNotIn("get_project_personal_key_context", specs)
        self.assertNotIn("send_everything", specs)

    def test_registry_artifact_is_deterministic_and_contains_literal_routes(self) -> None:
        first = operation_registry_document()
        self.assertEqual(first, operation_registry_document())
        entries = {item["name"]: item for item in first["operations"]}
        self.assertEqual(
            entries["personal_research_journey_form"]["path"],
            "/api/platform/v3/projects/{project_id}/research/{research_id}/journey/form",
        )
        self.assertEqual(
            entries["personal_research_journey_form_publish"]["path"],
            "/api/platform/v3/projects/{project_id}/research/{research_id}/journey/form/publish",
        )
        self.assertEqual(entries["personal_voc_execution_start"]["method"], "POST")
        self.assertEqual(entries["personal_voc_execution_get"]["method"], "GET")
        self.assertIn("{platform_run_id}", entries["personal_voc_execution_get"]["path"])
        self.assertEqual(entries["personal_voc_rename"]["method"], "PATCH")
        self.assertEqual(entries["personal_voc_rename"]["operation_id"], "project_rename_voc")
        self.assertNotIn("project_rename_voc", entries)
        self.assertEqual(
            entries["personal_voc_rename"]["path"],
            "/api/platform/v3/projects/{project_id}/ideas/{idea_id}/voc/{voc_id}/title",
        )
        self.assertEqual(entries["project_rename_idea"]["method"], "PATCH")
        self.assertEqual(
            entries["project_rename_idea"]["path"],
            "/api/platform/v3/projects/{project_id}/ideas/{idea_id}/title",
        )
        self.assertEqual(
            entries["project_rename_research"]["path"],
            "/api/platform/v3/projects/{project_id}/research/{research_id}/title",
        )
        self.assertEqual(
            entries["project_rename_research_audience"]["path"],
            "/api/platform/v3/projects/{project_id}/research-audience-assets/{asset_id}/title",
        )
        self.assertEqual(entries["project_get_report_source"]["method"], "GET")
        self.assertEqual(entries["project_publish_report"]["method"], "POST")
        self.assertEqual(entries["project_get_current_report"]["method"], "GET")
        self.assertEqual(
            entries["project_get_report_source"]["query"],
            ["idea_id", "parent_kind", "parent_id", "source_mode"],
        )


if __name__ == "__main__":
    unittest.main()
