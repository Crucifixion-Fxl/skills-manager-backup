"""Contract for the real Naturehood scheduled-workflow L4 receipt."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


RECEIPT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "naturehood-workflow-live-receipt-20260915.json"
)


class NaturehoodWorkflowLiveReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    def test_receipt_is_a_non_secret_revision_bound_snapshot(self) -> None:
        self.assertEqual(1, self.receipt["schema_version"])
        self.assertEqual("naturehood_scheduled_workflows_l4", self.receipt["receipt_kind"])
        self.assertTrue(self.receipt["snapshot_only"])
        self.assertFalse(self.receipt["contains_secret"])
        self.assertRegex(self.receipt["recorded_at_utc"], r"^2026-09-15T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(
            "24165734c0f671fa17fa376e12c3ce2e2db160a6",
            self.receipt["method"]["plugin_revision"],
        )

        serialized = json.dumps(self.receipt, ensure_ascii=False).lower()
        for forbidden in ("private-token", "nsec1", "gitlab_token", "superset_password"):
            self.assertNotIn(forbidden, serialized)

        def assert_no_secret_keys(value: object, path: str = "receipt") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key != "contains_secret":
                        self.assertIsNone(
                            re.search(
                                r"(?:^|_)(?:token|password|secret|private_key)(?:$|_)",
                                key.lower(),
                            ),
                            f"secret-shaped key in receipt: {path}.{key}",
                        )
                    assert_no_secret_keys(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    assert_no_secret_keys(child, f"{path}[{index}]")

        assert_no_secret_keys(self.receipt)

    def test_both_workflows_replied_in_their_own_thread(self) -> None:
        expected = {
            "delivery_progress": {
                "workflow_id": "78cb093b-2b07-470c-8bf4-a82eef7564e1",
                "root_event_id": "496c4012c71c2d36d97d34e12625d704e0f6d83efbf0b9520712dbdaff8031fe",
                "agent_pubkey": "6cf19cd67ea1bdd015182827bec8d1d21f90a2de94ac03392b24da62212d8c49",
            },
            "data_review": {
                "workflow_id": "f324d138-ebc7-4821-a165-fdbc031342a3",
                "root_event_id": "471eb63648fa15e84a18305e0321c88f420bb9e2bdb32c63129365795730266f",
                "agent_pubkey": "0f7dd8ae94a68e2de771c5111b9e9f350f4a02c3a5655c27c3357c23cd13c9c9",
            },
        }
        self.assertEqual(set(expected), set(self.receipt["workflows"]))
        for name, values in expected.items():
            workflow = self.receipt["workflows"][name]
            for field, value in values.items():
                self.assertEqual(value, workflow[field])
            self.assertRegex(workflow["run_id"], r"^[0-9a-f-]{36}$")
            for field in ("trigger_event_id", "ack_event_id", "final_event_id", "final_content_sha256"):
                self.assertRegex(workflow[field], r"^[0-9a-f]{64}$")
            self.assertTrue(workflow["trigger_accepted"])
            self.assertTrue(workflow["ack_is_nip10_reply"])
            self.assertTrue(workflow["final_is_nip10_reply"])

    def test_delivery_report_and_runtime_parameter_remediation_passed(self) -> None:
        workflow = self.receipt["workflows"]["delivery_progress"]
        self.assertEqual(
            {
                "open_feature_items": 35,
                "open_feature_pages": 1,
                "active_milestones": 3,
                "reported_items": 12,
            },
            workflow["coverage"],
        )
        for assertion in (
            "complete_pagination",
            "evidence_links",
            "uncertainty_disclosed",
            "no_code_volume_completion_estimate",
        ):
            self.assertTrue(workflow["assertions"][assertion])
        remediation = workflow["configuration_finding"]
        self.assertEqual("missing_run_parameters", remediation["observed"])
        self.assertEqual("remediated", remediation["status"])
        self.assertRegex(remediation["workflow_update_event_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            {
                "subject",
                "period",
                "comparison_window",
                "analysis_time",
                "asset_discovery",
            },
            set(remediation["added_parameters"]),
        )

    def test_data_review_qualifies_metrics_and_fails_closed_on_writeback(self) -> None:
        workflow = self.receipt["workflows"]["data_review"]
        initial = workflow["initial_attempt"]
        self.assertEqual(
            "e3136f919b1d9559adda53d8a2856656353ef2edff9fd35343397b15117e33d6",
            initial["root_event_id"],
        )
        self.assertEqual("blocked-detailed-publication", initial["outcome"])
        self.assertRegex(initial["safe_summary_event_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            "63608c719770ca6669c218a22809b0b317fccd2851a88717b1a2427cadd25f15",
            initial["safe_summary_content_sha256"],
        )
        remediation = workflow["configuration_finding"]
        self.assertEqual("missing_same_thread_output_authorization", remediation["observed"])
        self.assertEqual("remediated", remediation["status"])
        self.assertEqual(
            "2295f0d43921ecebd647935ea00ba9db64fe36f8984557e85a227625acc1b411",
            remediation["prompt_sha256"],
        )
        self.assertEqual(
            {"experiments": {"read": 641, "total": 641, "pages": 7}},
            workflow["growthbook_inventory"],
        )
        self.assertEqual(
            {
                "dashboards": {"read": 422, "total": 422, "pages": 5},
                "charts": {"read": 5537, "total": 5537, "pages": 56},
                "datasets": {"read": 2249, "total": 2249, "pages": 23},
            },
            workflow["superset_inventory"],
        )
        for assertion in (
            "complete_pagination",
            "source_window_timezone_filter_dedup",
            "zero_vs_missing_vs_query_failure_distinguished",
            "causality_not_overclaimed",
            "same_thread_pre_authorized_publication",
            "no_raw_rows_identifiers_sql_or_secrets",
            "controlled_links_only",
            "small_cohort_suppressed",
            "environment_verified_or_degraded",
        ):
            self.assertTrue(workflow["assertions"][assertion])
        self.assertEqual("readonly", workflow["gitlab_writeback"])
        self.assertEqual(0, workflow["gitlab_write_count"])

    def test_outer_loop_replayed_after_final_skill_revision(self) -> None:
        replay = self.receipt["outer_loop_final_revision"]
        self.assertEqual(
            "00c0c9f4c47c2e87569b65db22f0b93484bb69c7",
            replay["plugin_revision"],
        )
        self.assertEqual("latest_skill_reinstalled_and_channel_reconfigured", replay["setup"])
        self.assertTrue(replay["runtime_contract_passed"])
        self.assertEqual(13, replay["runtime_contract_tests"])

        expected = {
            "delivery_progress": {
                "workflow_id": "78cb093b-2b07-470c-8bf4-a82eef7564e1",
                "run_id": "ec201b3a-b2c9-4f88-8349-5bf1e914824f",
                "root_event_id": "9f095fdbb57d99876552883eed26ea9201e1c00bf8709e976bd02b31781e3832",
                "ack_event_id": "3edfe2261cf103b33dbecc9596e6e5f2a3d4764c55d9fabe8518fc818cb94a67",
                "agent_pubkey": "6cf19cd67ea1bdd015182827bec8d1d21f90a2de94ac03392b24da62212d8c49",
            },
            "data_review": {
                "workflow_id": "f324d138-ebc7-4821-a165-fdbc031342a3",
                "run_id": "fdc351b1-7b28-4bc3-b375-f02948cdbb72",
                "root_event_id": "ac22a10fec07facbceb87354018748d44756f07434048cc23ee96cec03b22629",
                "ack_event_id": "68f5419bb8bf0056aff9ebaf559b0a420e7aea0e480673f99f6ae1b13b59348f",
                "agent_pubkey": "0f7dd8ae94a68e2de771c5111b9e9f350f4a02c3a5655c27c3357c23cd13c9c9",
            },
        }
        self.assertEqual(set(expected), set(replay["workflows"]))
        for name, values in expected.items():
            workflow = replay["workflows"][name]
            for field, value in values.items():
                self.assertEqual(value, workflow[field])
            for field in ("final_event_id", "final_content_sha256"):
                self.assertRegex(workflow[field], r"^[0-9a-f]{64}$")
            self.assertGreater(workflow["final_length_chars"], 0)
            self.assertTrue(workflow["root_has_agent_mention"])
            self.assertTrue(workflow["ack_is_nip10_reply"])
            self.assertTrue(workflow["final_is_nip10_reply"])
            self.assertTrue(workflow["effect_contract_passed"])

        delivery = replay["workflows"]["delivery_progress"]
        self.assertEqual(
            {
                "open_feature_items": 35,
                "open_feature_pages": 1,
                "branches_read": 688,
                "branch_pages": 7,
                "active_milestones": 3,
                "reported_items": 12,
            },
            delivery["coverage"],
        )
        for assertion in (
            "complete_pagination",
            "evidence_links",
            "comparison_to_prior_snapshot",
            "release_candidate_and_combined_package_acceptance",
            "uncertainty_disclosed",
            "no_code_volume_completion_estimate",
        ):
            self.assertTrue(delivery["assertions"][assertion])

        data_review = replay["workflows"]["data_review"]
        self.assertTrue(data_review["same_thread_pre_authorized_publication"])
        self.assertFalse(data_review["requested_per_run_disclosure_approval"])
        self.assertEqual("readonly", data_review["gitlab_writeback"])
        self.assertEqual(0, data_review["gitlab_write_count"])
        self.assertEqual(
            {"read": 73, "total": 73, "pages": 1},
            data_review["gitlab_inventory"]["issues"],
        )
        self.assertEqual(
            {
                "read_unique": 225,
                "advertised": 226,
                "all_advertised_pages_requested": True,
            },
            data_review["gitlab_inventory"]["notes"],
        )
        self.assertEqual(
            {"read": 641, "total": 641, "pages": 7},
            data_review["growthbook_inventory"]["experiments"],
        )
        self.assertEqual(
            {"read": 422, "total": 422, "pages": 5},
            data_review["superset_inventory"]["dashboards"],
        )
        self.assertEqual(
            "2560758514bbc751cbc09310e34b864ea965d08a7d74b14c7e9c164aec4c5e88",
            data_review["pagination_retry_request_event_id"],
        )
        self.assertEqual(
            "02883e0376723f318fdb82187f8b77337807c4c6572f90208c6f95a999122518",
            data_review["pagination_retry_ack_event_id"],
        )
        self.assertEqual(
            "cd8ee67fe6d717f95706948566b6f62acfa9a5fd9745cf8aea66502e9141d62f",
            data_review["pagination_retry_event_id"],
        )
        self.assertEqual(
            "a267281750613f930811218ccb2d1ab2a46acf346e34e3c334be978a957f27ba",
            data_review["pagination_retry_content_sha256"],
        )
        self.assertEqual(1112, data_review["pagination_retry_length_chars"])
        self.assertTrue(data_review["pagination_retry_is_nip10_reply"])
        for inventory_name in ("charts", "datasets"):
            inventory = data_review["superset_inventory"][inventory_name]
            self.assertEqual(inventory["total"], inventory["read"])
            self.assertEqual(inventory["total"], inventory["unique_ids"])
            self.assertGreater(inventory["pages"], 0)
        self.assertEqual(
            {"chart_ids": [4507], "dataset_ids": [2288, 3505]},
            data_review["asset_candidates"],
        )
        self.assertEqual(
            {
                "status": "success",
                "row_count": 0,
                "partition_days": 0,
                "interpretation": "data_coverage_gap_not_usage_zero",
            },
            data_review["aggregate_query"],
        )
        for assertion in (
            "complete_pagination_after_retry",
            "source_window_timezone_filter_partition_dedup_sample_missingness",
            "zero_vs_missing_vs_query_failure_distinguished",
            "causality_not_overclaimed",
            "no_raw_rows_identifiers_sql_secrets_or_temporary_links",
            "controlled_links_only",
            "small_cohort_suppressed",
            "coverage_statement_corrected",
            "publication_candidates_unchanged",
            "effect_conclusion_unchanged",
        ):
            self.assertTrue(data_review["assertions"][assertion])


if __name__ == "__main__":
    unittest.main(verbosity=2)
