"""Client-first optional human navigation mapping, without weakening closed schemas."""

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import test_project_query as fixtures

from audience_sync.cli import main
from audience_sync.client import SafeApiError, _safe_projection
from audience_sync.contract_validation import ContractViolation, validate_operation_response
from audience_sync.project_operations import ProjectOperation as Op

PLAN = "aqp_" + "a" * 26
VIEWER = "https://micro-app-platform-us.addx.live/audience-sync-us"
LINK = VIEWER + "?" + urlencode({"aw_target": "/projects/kiwibit/audiences/" + PLAN})
PROVIDER = "https://app.brevo.com/contact/list/id/456"
ROOT = Path(__file__).resolve().parents[1]
GUIDE_ZH = json.loads((ROOT / "tests/fixtures/guide-zh-CN.json").read_text())


class CompletionLinkInstructionTests(unittest.TestCase):
    """Guard the shared instruction contract, not simulated model obedience."""

    def setUp(self):
        self.guide = " ".join((ROOT / "references/project-query.md").read_text().split())

    def test_materialization_reply_reuses_bound_link_or_reads_once(self):
        for requirement in (
            GUIDE_ZH["successful_exact_materialization"],
            GUIDE_ZH["prior_bound_plan_url"],
            GUIDE_ZH["same_plan_project_key"],
            GUIDE_ZH["one_exact_plan_read"],
            GUIDE_ZH["optional_link_failures"],
            GUIDE_ZH["no_link_retry_or_fabrication"],
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, self.guide)

    def test_sync_reply_preserves_status_and_never_starts_extra_work(self):
        for requirement in (
            GUIDE_ZH["terminal_exact_sync"],
            GUIDE_ZH["independently_optional_bound_links"],
            GUIDE_ZH["native_status_counts_error"],
            GUIDE_ZH["actual_list_not_folder"],
            GUIDE_ZH["no_added_effects_or_notifications"],
            GUIDE_ZH["native_and_bundled_completion"],
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, self.guide)

    def test_skill_entry_routes_completion_replies_to_shared_rule(self):
        skill = " ".join((ROOT / "SKILL.md").read_text().split())
        self.assertIn("references/project-query.md#completion-replies", skill)
        self.assertIn(GUIDE_ZH["completion_rule_entry"], skill)


class ProjectHumanLinkTests(unittest.TestCase):
    def test_old_null_and_linked_sync_payloads_keep_closed_validation(self):
        for fields in ({}, {"audience_detail_url": None, "provider_resource_url": None},
                       {"audience_detail_url": LINK, "provider_resource_url": PROVIDER}):
            response = fixtures.ProjectQueryTests.successful_sync_response(**fields)
            self.assertEqual(validate_operation_response(Op.GET_AUDIENCE_QUERY_SYNC, response),
                             response)
            self.assertEqual(_safe_projection(response), response)
            invalid = copy.deepcopy(response)
            invalid["resource"]["unrecognized_link"] = PROVIDER
            with self.assertRaises(ContractViolation):
                validate_operation_response(Op.GET_AUDIENCE_QUERY_SYNC, invalid)

    def test_plan_response_accepts_optional_link_without_requiring_it(self):
        resource = {"plan_id": PLAN, "name": "Fixture", "plan_hash": "a" * 64,
                    "partition_dt": "2026-09-09", "criteria_hash": "b" * 64,
                    "registry_version": "fixture", "registry_digest": "c" * 64}
        for fields in ({}, {"audience_detail_url": None}, {"audience_detail_url": LINK}):
            response = {"project_id": "kiwibit", "binding_revision": "pbr_" + "a" * 64,
                        "resource": {**resource, **fields}}
            for operation in (Op.CREATE_AUDIENCE_QUERY, Op.GET_AUDIENCE_QUERY):
                expected = {**response, "resource": {"criteria": None, **response["resource"]}}
                self.assertEqual(validate_operation_response(operation, response), expected)
                self.assertEqual(_safe_projection(response), response)

    def test_cli_preserves_both_links_in_exact_sync_readback(self):
        response = fixtures.ProjectQueryTests.successful_sync_response(
            audience_detail_url=LINK, provider_resource_url=PROVIDER)
        request = {"path": {"project_id": "kiwibit", "plan_id": PLAN,
                            "request_id": response["resource"]["sync_request_id"]}}
        output = StringIO()
        with (
            patch.dict("os.environ", {"AUDIENCE_PROJECT_ID": "kiwibit",
                       "AUDIENCE_SYNC_API_KEY": "awpk_v2_" + "a" * 26 + "_" + "A" * 43},
                       clear=True),
            patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))),
            patch("audience_sync.client.build_opener") as opener,
            redirect_stdout(output),
        ):
            opener.return_value.open.return_value = BytesIO(json.dumps(response).encode())
            self.assertEqual(main(["get_audience_query_sync", "--request-stdin"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "result": response})
        self.assertNotIn("awpk", output.getvalue())

    def test_only_known_project_target_query_is_accepted(self):
        for value in (
            VIEWER + "/audiences/" + PLAN,
            LINK,
            LINK.replace("micro-app-platform-us", "micro-app-platform-staging-us")
                .replace("/audience-sync-us?", "/audience-sync-admin-staging-us?"),
        ):
            self.assertEqual(_safe_projection({"audience_detail_url": value}),
                             {"audience_detail_url": value})
        for value in (
            "javascript:alert(1)", "//evil.invalid", "not-a-url",
            LINK + "&token=not-allowed", LINK + "&aw_target=/projects/vicohome",
            LINK + "#fragment", VIEWER + "?aw_target=%E0%A4%A",
            VIEWER + "?" + urlencode({"aw_target": "https://evil.invalid"}),
            VIEWER + "?" + urlencode({"aw_target": "/projects/kiwibit/../admin"}),
            LINK.replace("https://", "http://"),
            LINK.replace("https://", "https://user:pass@"),
        ):
            with self.subTest(value=value), self.assertRaises(SafeApiError):
                _safe_projection({"audience_detail_url": value})

    def test_raw_control_and_backslash_url_text_is_rejected(self):
        for field, url in (("audience_detail_url", LINK),
                           ("provider_resource_url", PROVIDER)):
            for value in ("\x00" + url, url + "\x01", url.replace("https://", "https://host\\")):
                with self.subTest(field=field, value=repr(value)), self.assertRaises(SafeApiError):
                    _safe_projection({field: value})


if __name__ == "__main__":
    unittest.main()
