from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from user_research.contract_validation import ContractViolation, validate_operation_response
from user_research.operations import OPERATION_SPECS, Operation

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_fixed_inventory_contains_coldstart_chain_and_no_send(self) -> None:
        operations = {operation.value: spec for operation, spec in OPERATION_SPECS.items()}
        required = {
            "get_project_personal_key_context",
            "personal_audience_sync_capabilities",
            "personal_idea_list",
            "personal_idea_create",
            "personal_idea_get",
            "personal_action_configuration_get",
            "personal_action_configuration_put",
            "personal_voc_capabilities",
            "personal_voc_execution_start",
            "personal_voc_execution_get",
            "personal_research_journey_create",
            "personal_research_journey_rename",
            "personal_voc_rename",
            "project_rename_idea",
            "project_rename_research",
            "project_rename_research_audience",
            "project_get_report_source",
            "project_publish_report",
            "project_get_current_report",
            "personal_research_readiness",
            "personal_research_query_capabilities",
            "personal_research_prepare_selection",
            "personal_research_journey_form",
            "personal_research_journey_form_publish",
            "personal_research_journey_operation",
            "personal_research_journey_status",
            "personal_research_journey_links",
            "personal_research_journey_campaign_draft",
            "personal_research_journey_responses",
            "personal_research_journey_responses_csv",
            "personal_research_journey_aggregate",
            "personal_research_journey_idea_summary",
            "personal_research_journey_idea_summary_csv",
        }
        self.assertTrue(required.issubset(operations))
        platform_api = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        for marker in (
            "PATCH /api/platform/v3/projects/{project_id}/ideas/{idea_id}/title",
            "PATCH /api/platform/v3/projects/{project_id}/research/{research_id}/title",
            "PATCH /api/platform/v3/projects/{project_id}/ideas/{idea_id}/voc/{voc_id}/title",
            (
                "PATCH /api/platform/v3/projects/{project_id}"
                "/research-audience-assets/{asset_id}/title"
            ),
            '{"title":"..."}',
        ):
            self.assertIn(marker, platform_api)
        self.assertTrue(all(not name.startswith("send_") for name in operations))
        self.assertTrue(
            all(not spec.path.rstrip("/").endswith("/send") for spec in operations.values())
        )

    def test_project_routes_are_literal_personal_api_only(self) -> None:
        for operation, spec in OPERATION_SPECS.items():
            with self.subTest(operation=operation.value):
                self.assertTrue(spec.path.startswith("/api/platform/"))
                self.assertNotIn("/api/admin/", spec.path)
                self.assertNotIn("http://", spec.path)
                self.assertNotIn("https://", spec.path)
                self.assertNotIn("/send", spec.path.lower())
                self.assertNotIn("/schedule", spec.path.lower())

    def test_provider_effects_remain_project_scoped_audience_bridges(self) -> None:
        paths = [spec.path for spec in OPERATION_SPECS.values()]
        self.assertIn(
            "/api/platform/v3/projects/{project_id}/ideas/{idea_id}/actions/voc/executions",
            paths,
        )
        self.assertFalse(any("{product_scope}" in path for path in paths))
        self.assertTrue(
            all(
                path.startswith("/api/platform/v3/projects/{project_id}/")
                for path in paths
                if "/providers/apify/" in path
            )
        )
        source = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "SKILL.md",
                "references/platform-api.md",
                "references/host-configuration.md",
            )
        ).lower()
        for forbidden in ("api.typeform.com", "api.brevo.com", "app.nocodb.com"):
            self.assertNotIn(forbidden, source)

    def test_typeform_form_and_brevo_draft_are_separate_native_payloads(self) -> None:
        document = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        root = "/api/platform/v3/projects/{project_id}/research/{research_id}/journey"
        for suffix in ("/form", "/campaign-draft"):
            operation = document["paths"][root + suffix]["post"]
            schema = operation["requestBody"]["content"]["application/json"]["schema"]
            self.assertEqual(schema, {"$ref": "#/components/schemas/ProviderCreate"})
        provider_create = document["components"]["schemas"]["ProviderCreate"]
        body = provider_create["properties"]["body"]
        self.assertEqual(body["anyOf"][0]["additionalProperties"], True)
        self.assertEqual(body["anyOf"][1], {"type": "null"})
        self.assertIn("source_research_id", provider_create["properties"])
        self.assertEqual(provider_create["required"], ["idempotency_key"])
        self.assertNotIn(root + "/send", document["paths"])

    def test_research_operation_contract_has_exact_async_evidence(self) -> None:
        document = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        request = document["components"]["schemas"]["OperationRequest"]
        self.assertEqual(
            request["properties"]["operation"]["enum"],
            ["materialize", "prepare_personalized_links", "sync_brevo"],
        )
        for field in (
            "approved_selection_id",
            "expected_count",
            "sample_size",
            "source_materialization_run_id",
            "destination_id",
            "idempotency_key",
        ):
            self.assertIn(field, request["properties"])
        sample_size = request["properties"]["sample_size"]
        self.assertEqual(sample_size["anyOf"][0], {"minimum": 1.0, "type": "integer"})
        self.assertEqual(sample_size["anyOf"][1], {"type": "null"})
        journey = document["components"]["schemas"]["JourneyStatus"]
        self.assertTrue({"requests", "receipts", "bindings"}.issubset(journey["required"]))

    def test_native_voc_publish_requires_voices_and_chinese_report_not_overview(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        for marker in (
            "project_publish_report",
            "representative_voices",
            "不要提交 `source_coverage`",
            "has_more",
            "简体中文",
        ):
            self.assertIn(marker, skill)
        self.assertIn("text` 原文", skill)
        self.assertIn("证据概览由平台从已绑定", skill)
        self.assertNotIn("sample_content_hash", skill)
        self.assertNotIn("一次发布同时交付三件事：提交 `source_coverage`", skill)
        for marker in (
            "Do not submit `source_coverage`",
            "Simplified Chinese",
            "voices_status=not_provided",
            "A VOC publication is therefore one call that delivers two Human-visible",
            "bound VOC run",
            "the platform to hydrate excerpts",
        ):
            self.assertIn(marker, platform)
        self.assertNotIn(
            "A VOC publication is therefore one call that delivers three Human-visible",
            platform,
        )
        self.assertIn("| VOC | `personal_voc_rename` |", platform)
        self.assertIn("do not pass that id to the CLI", platform)
        self.assertIn('"text": "<原文摘录，1–1000 chars>"', platform)
        self.assertNotIn("Copy counts from `project_get_report_source`", platform)
        outcome = (ROOT / "references/outcome-writing.md").read_text(encoding="utf-8")
        self.assertIn("`project_publish_report`", outcome)
        self.assertNotIn("`publish_report`", outcome)
        guidance = (ROOT / "references/runtime-guidance.md").read_text(encoding="utf-8")
        self.assertIn("project_publish_report", guidance)
        self.assertIn("Simplified Chinese Markdown", guidance)
        self.assertIn("Do not submit `source_coverage`", guidance)
        self.assertNotIn("VOC publications may include", platform)
        errors = (ROOT / "references/errors-and-recovery.md").read_text(encoding="utf-8")
        self.assertIn("Native VOC report missing original voice text", errors)
        self.assertNotIn("Native VOC report missing coverage or original voice text", errors)

    def test_hermes_cron_pages_owned_dataset_items_before_native_publish(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        errors = (ROOT / "references/errors-and-recovery.md").read_text(encoding="utf-8")
        self.assertIn("trusted report-request context", skill)
        self.assertIn("project_cron_voc_dataset", skill)
        self.assertNotIn("cron report 工具。", skill)
        self.assertIn("project_cron_voc_dataset_items", journey)
        self.assertIn("empty report-source is expected", journey)
        self.assertNotIn("cron publication tools only", journey)
        self.assertIn("project_cron_voc_dataset_*", platform)
        self.assertIn("Empty native report-source context is expected", platform)
        self.assertIn("project_cron_voc_dataset_*", errors)
        self.assertIn("Do not publish a 0/0/0 placeholder", errors)
        self.assertIn("分页读取原始 items 即可分析并写报告", skill)
        self.assertIn("CSV/JSONL 导出是可选附件", skill)
        self.assertNotIn("下载 owned Dataset 后在本地分析", skill)
        self.assertIn("Those JSON pages are enough to analyze and publish", journey)
        self.assertIn("JSON items are bounded reads and are sufficient", platform)
        self.assertNotIn("authored from the downloaded Dataset export", platform)
        self.assertNotIn("from the local Dataset export", platform)
        self.assertIn("Export download is optional", errors)

    def test_project_shared_collaboration_and_reuse_guidance(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        errors = (ROOT / "references/errors-and-recovery.md").read_text(encoding="utf-8")
        for marker in (
            "Project 协作",
            "source_research_id",
            "form_url#uid=...&research_id=current&batch=current",
        ):
            self.assertIn(marker, skill)
        for marker in (
            "Send exactly one of provider-native",
            "source_research_id",
            "created_by_fingerprint",
        ):
            self.assertIn(marker, platform)
        self.assertIn("must not POST Typeform", journey)
        self.assertIn("exactly one of a native `body`", journey)
        self.assertIn("Do not clone a source form JSON into", journey)
        self.assertNotIn(
            "Create a short decision-linked questionnaire using a Typeform-native body.",
            journey,
        )
        self.assertIn("Campaign reuse leftover source URL", errors)
        self.assertIn("exactly one of", errors)

    def test_idea_summary_includes_audience_coverage_and_idea_report(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        self.assertNotIn("如有 Brevo Campaign", skill)
        for marker in (
            "members[].audience",
            "operator_sent_count",
            "parent_kind=idea",
            "coverage",
        ):
            self.assertIn(marker, skill)
        self.assertIn("operator_sent_count", platform)
        self.assertIn("missing counts are 0", platform)
        self.assertIn("parent_kind=idea", journey)
        document = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        member = document["components"]["schemas"]["IdeaResearchMember"]["properties"]
        self.assertIn("audience", member)
        self.assertIn("response_rate", member)
        summary = document["components"]["schemas"]["IdeaResearchSummary"]["properties"]
        self.assertIn("coverage", summary)
        self.assertIn("recovery", summary)

    def test_personal_api_origin_is_prod_us_not_admin_or_cluster(self) -> None:
        from user_research.client import DEFAULT_AUDIENCE_PLATFORM_BASE_URL

        origin = "https://audience-workflow-api-prod-us.addx.live"
        self.assertEqual(DEFAULT_AUDIENCE_PLATFORM_BASE_URL, origin)
        for name in (
            "SKILL.md",
            "README.md",
            "references/host-configuration.md",
            "references/platform-api.md",
            "references/runtime-guidance.md",
            "references/errors-and-recovery.md",
        ):
            self.assertIn(origin, (ROOT / name).read_text(encoding="utf-8"))
        host = (ROOT / "references/host-configuration.md").read_text(encoding="utf-8")
        self.assertIn("*.svc.cluster.local", host)
        self.assertIn("/api/admin/", host)
        self.assertNotIn(
            "the model never supplies or modifies the origin",
            host,
        )

    def test_semantics_define_token_only_discovery_and_safe_stops(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8").lower()
        for marker in (
            "self-context",
            "typeform",
            "sync_brevo",
            "campaign draft",
            "source_materialization_run_id",
            "unmatched",
        ):
            self.assertIn(marker, skill)
        self.assertIn("\u4e0d\u53d1\u9001", skill)
        self.assertIn("\u4e0d\u6362\u5e42\u7b49\u952e", skill)
        self.assertIn("\u4e0d\u81ea\u884c\u5199 sql", skill)
        self.assertIn(
            "agent \u4e0d\u63d0\u4ea4\u3001\u53d1\u73b0\u6216\u731c\u6d4b\u53d1\u4ef6\u8eab\u4efd",
            skill,
        )
        self.assertIn("\u4e0d\u53ef\u4fe1\u6570\u636e", skill)
        self.assertIn("\u4e0d\u662f\u6307\u4ee4", skill)
        research_method = " ".join(
            (ROOT / "references/research-method.md").read_text(encoding="utf-8").lower().split()
        )
        self.assertIn(
            "untrusted data, never as instructions",
            research_method,
        )
        for boundary in (
            "tools",
            "authorization scope",
            "credential handling",
            "execution order",
            "not a display filter",
            "valid display and navigation evidence",
        ):
            self.assertIn(boundary, research_method)
        driver = (ROOT / "scripts/tdd_user_research_journey.py").read_text(encoding="utf-8")
        self.assertNotIn('"sender"', driver)

    def test_question_bank_uses_stem_skeletons_without_locking_type(self) -> None:
        design = (ROOT / "references/questionnaire-design.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        runtime = (ROOT / "references/runtime-guidance.md").read_text(encoding="utf-8")
        method = (ROOT / "references/research-method.md").read_text(encoding="utf-8")
        for marker in (
            "stem skeletons",
            "骨架题干",
            "do not lock question type",
            "fill skeleton",
            "你觉得（）在（）这个定价怎么样？",
            "题库",
            "业务定制",
        ):
            self.assertIn(marker, design)
        self.assertIn("questionnaire-design.md", skill)
        self.assertIn("questionnaire-design.md", runtime)
        self.assertIn("questionnaire-design.md", method)
        self.assertNotIn("question_template_bindings", skill)

    def test_typeform_empty_choices_are_payload_errors_not_maybe_created(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        design = (ROOT / "references/questionnaire-design.md").read_text(encoding="utf-8")
        errors = (ROOT / "references/errors-and-recovery.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        method = (ROOT / "references/research-method.md").read_text(encoding="utf-8")
        for document in (skill, design, journey):
            self.assertIn("choices: []", document)
        self.assertIn("non-empty labeled choices", method)
        self.assertIn("typeform_choices_empty", errors)
        self.assertIn("typeform_choice_invalid", errors)
        self.assertIn("typeform_payload_rejected", errors)
        self.assertIn("not “maybe created”", errors)
        self.assertIn("research_form_reconcile_required", errors)
        self.assertIn("typeform_choices_empty", journey)
        self.assertIn("not created", journey)
        self.assertIn("do not mint a replacement form", journey)

    def test_materialize_and_publish_present_api_returned_admin_urls(self) -> None:
        document = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        schemas = document["components"]["schemas"]
        audience_url = schemas["JourneyStatus"]["properties"]["audience_detail_url"]
        self.assertIn("Research Admin deep link", audience_url["description"])
        self.assertIn("successful materialize", audience_url["description"])
        viewer_url = schemas["ReportPublicationProjection"]["properties"]["viewer_url"]
        self.assertIn("Research Admin deep link", viewer_url["description"])
        self.assertIn("never construct a frontend path", viewer_url["description"])
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        results = (ROOT / "references/result-output.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        outcome = (ROOT / "references/outcome-writing.md").read_text(encoding="utf-8")
        for marker in ("audience_detail_url", "viewer_url"):
            self.assertIn(marker, skill)
            self.assertIn(marker, results)
            self.assertIn(marker, platform)
        self.assertIn("物化成功后把 API 返回的 `audience_detail_url` 原样给用户", skill)
        self.assertIn("写回成功后把 API 返回的 `viewer_url` 原样给用户", skill)
        self.assertIn("never construct `?aw_target=`", journey)
        self.assertIn("do not assemble", platform)
        self.assertIn("Research Admin deep link", outcome)
        self.assertNotIn("the Admin shell", outcome)

    def test_idea_research_voc_present_api_returned_admin_urls(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        results = (ROOT / "references/result-output.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        platform = (ROOT / "references/platform-api.md").read_text(encoding="utf-8")
        outcome = (ROOT / "references/outcome-writing.md").read_text(encoding="utf-8")
        for marker in ("idea_detail_url", "research_detail_url", "voc_detail_url"):
            self.assertIn(marker, skill)
            self.assertIn(marker, results)
            self.assertIn(marker, platform)
            self.assertIn(marker, journey)
            self.assertIn(marker, outcome)
        self.assertIn(
            "列表或详情若返回 `idea_detail_url` / "
            "`research_detail_url` / `voc_detail_url`，原样给用户",
            skill,
        )
        self.assertIn(
            "创建或读取 Idea / Research / VOC 后，把 API 返回的 "
            "`idea_detail_url`、`research_detail_url`、`voc_detail_url` 原样给用户",
            skill,
        )
        document = json.loads((ROOT / "contracts/project-control-plane.openapi.json").read_text())
        schemas = document["components"]["schemas"]
        self.assertIn("research_detail_url", schemas["ResearchBinding"]["properties"])
        self.assertIn("research_detail_url", schemas["IdeaResearchMember"]["properties"])
        self.assertIn("idea_detail_url", schemas["IdeaResearchSummary"]["properties"])
        self.assertIn("voc_detail_url", schemas["ProjectVocDiscoveryItem"]["properties"])
        self.assertIn("voc_detail_url", schemas["ProjectNativeVocRun"]["properties"])

    def test_research_list_and_idea_summary_accept_admin_detail_urls(self) -> None:
        idea_id = "idea_" + "b" * 24
        research_id = "research_" + "a" * 26
        binding_revision = "pbr_4c4cda060be60b9b8ff5ed58d0fb73d15617abddae5b9e2ce231887925d7b96b"
        research_url = "https://admin.example.test/research"
        idea_url = "https://admin.example.test/idea"
        list_payload = {
            "project_id": "kiwibit",
            "binding_revision": binding_revision,
            "enabled": True,
            "has_more": False,
            "items": [
                {
                    "research_id": research_id,
                    "project_id": "kiwibit",
                    "revision": 1,
                    "research_detail_url": research_url,
                }
            ],
        }
        summary_payload = {
            "project_id": "kiwibit",
            "binding_revision": binding_revision,
            "idea_id": idea_id,
            "kind": "idea_research_summary",
            "research_count": 1,
            "response_count": 1,
            "associated_count": 1,
            "unmatched_count": 0,
            "source_fingerprint": "b" * 64,
            "source_revision_id": "isum_" + "b" * 26,
            "idea_detail_url": idea_url,
            "members": [
                {
                    "research_id": research_id,
                    "idea_id": idea_id,
                    "response_count": 1,
                    "associated_count": 1,
                    "unmatched_count": 0,
                    "research_detail_url": research_url,
                }
            ],
        }
        self.assertEqual(
            validate_operation_response(Operation("personal_research_journey_list"), list_payload),
            list_payload,
        )
        self.assertEqual(
            validate_operation_response(
                Operation("personal_research_journey_idea_summary"), summary_payload
            ),
            summary_payload,
        )
        rejected = dict(list_payload)
        rejected["unexpected_field"] = True
        with self.assertRaises(ContractViolation):
            validate_operation_response(Operation("personal_research_journey_list"), rejected)

    def test_uncovered_capabilities_name_sibling_skills(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("未覆盖、需其他子 Skill", skill)
        for sibling in (
            "user-research-coding",
            "user-research-report",
            "user-research-monitor",
            "survey-research-workflow",
        ):
            self.assertIn(sibling, skill)
        self.assertIn("不建立开放文本 codebook", skill)
        self.assertIn("不是同一件事", skill)

    def test_side_effect_writes_ask_confirmation_without_required_extras(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        method = (ROOT / "references/research-method.md").read_text(encoding="utf-8")
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8")
        runtime = (ROOT / "references/runtime-guidance.md").read_text(encoding="utf-8")
        for marker in (
            "VOC 采集、物化 audience、创建 Typeform、同步 Brevo",
            "不是必填",
            "不得因缺渠道/关键词拒绝执行",
        ):
            self.assertIn(marker, skill)
        self.assertIn("Do not refuse because channel or keywords are missing", method)
        self.assertIn("Do not refuse because channel or keywords are missing", journey)
        self.assertIn("do not refuse because they are missing", runtime)
        self.assertNotIn(
            "proceed without an extra confirmation turn",
            method,
        )

    def test_questionnaire_only_and_response_analysis_are_explicit(self) -> None:
        journey = (ROOT / "references/typeform-research.md").read_text(encoding="utf-8").lower()
        for marker in (
            "generic questionnaire branch",
            "unmatched",
            "responses",
            "aggregate",
            "csv",
            "question",
            "profile",
        ):
            self.assertIn(marker, journey)

    def test_operation_registry_is_current_and_literal(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/update_operation_registry.py", "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        registry = json.loads((ROOT / "contracts/operation-registry.json").read_text())
        actual = {
            (operation.value, spec.method, spec.path) for operation, spec in OPERATION_SPECS.items()
        }
        recorded = {(item["name"], item["method"], item["path"]) for item in registry["operations"]}
        self.assertEqual(recorded, actual)

    def test_source_lock_hashes_the_distribution_and_has_external_owner(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/update_source_lock.py", "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        source = json.loads((ROOT / "contracts/source.json").read_text())
        owner = json.loads((ROOT / "contracts/semantic-owner.json").read_text())
        self.assertEqual(source["semantic_owner_file"], owner)
        self.assertEqual(owner["repository"], "lli/user-research-skill")
        self.assertEqual(owner["revision"], "14dab06b962a792332da11a839e58ee209f60986")
        publication = (
            ROOT.parents[1] / "docs/04-user-stories/audience-user-research-publication.md"
        ).read_text(encoding="utf-8")
        self.assertIn(owner["revision"], publication)
        self.assertNotIn("contracts/source.json", source["files"])
        self.assertFalse(any(".egg-info/" in relative for relative in source["files"]))
        self.assertIn("references/datahub-schema-search.md", source["files"])
        self.assertNotIn(".gitlab-ci.yml", source["files"])
        self.assertNotIn("skills/datahub-schema-search/SKILL.md", source["files"])
        for relative, expected in source["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)

    def test_source_lock_fails_loud_when_packaged_reference_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "source"
            shutil.copytree(
                ROOT,
                clone,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".venv",
                    "__pycache__",
                    "*.egg-info",
                    "build",
                    "dist",
                ),
            )
            reference = clone / "references" / "datahub-schema-search.md"
            reference.write_text(
                reference.read_text(encoding="utf-8") + "\nunreviewed change\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, "scripts/update_source_lock.py", "--check"],
                cwd=clone,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout.strip(), "source_lock_drift")

    def test_eval_contract_covers_full_optional_recovery_and_results(self) -> None:
        evaluations = json.loads((ROOT / "evals/evals.json").read_text())
        self.assertEqual(evaluations["contract"]["case_count"], 19)
        self.assertTrue(evaluations["contract"]["hide_tool_tokens"])
        slugs = {item["slug"] for item in evaluations["evals"]}
        self.assertEqual(
            slugs,
            {
                "coldstart-full-authorized-journey",
                "voc-skipped-full-research",
                "questionnaire-only-no-warehouse",
                "resume-and-ambiguous-recovery",
                "response-profile-csv",
                "native-voc-dataset-report-loop",
                "historical-project-inventory",
                "monitor-existing-research",
                "historical-voc-local-dataset-analysis",
                "historical-research-results-local-csv",
                "permission-and-partial-history",
                "voc-focus-and-channel-choice",
                "questionnaire-and-campaign-copy-brief",
                "uj-nl-research-voc-dataset-report",
                "uj-questionnaire-without-warehouse",
                "uj-small-cohort-invite-draft",
                "uj-resume-history-results-matrix",
                "uj-recoverable-exceptions",
                "project-shared-research-reuse",
            },
        )
        full = next(item for item in evaluations["evals"] if item["slug"].startswith("coldstart-"))
        self.assertIn("sender identity", full["setup"])
        self.assertTrue(any("Omit sender" in item["check"] for item in full["assertions"]))
        self.assertEqual(
            evaluations["contract"]["result_card"]["required_labels"],
            ["项目与对象", "当前状态", "简述", "核验链接", "下一步"],
        )
        self.assertTrue(
            all(
                any(assertion["name"].startswith("output-") for assertion in case["assertions"])
                for case in evaluations["evals"]
            )
        )

    def test_distribution_metadata_includes_skill_and_contracts(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for marker in (
            "include SKILL.md",
            "recursive-include references *.md",
            "recursive-include contracts *.json",
            "recursive-include evals *.json",
        ):
            self.assertIn(marker, manifest)
        setup = (ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn('glob("*.json")', setup)
        self.assertIn('package_data={"user_research": ["contracts/*.json"]}', setup)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('user_research = ["contracts/*.json"]', pyproject)

    def test_native_voc_run_accepts_platform_item_count(self) -> None:
        body = {
            "project_id": "kiwibit",
            "binding_revision": "pbr_" + "a" * 64,
            "request_id": "pnvoc_" + "ab" * 13,
            "idea_id": "idea_" + "c" * 20,
            "actor_id": "actor/name",
            "build": "1.2.3",
            "input": {"productUrls": ["https://example.com/item"]},
            "voc_id": "ivoc_" + "d" * 20,
            "run": {
                "voc_id": "ivoc_" + "d" * 20,
                "run_id": "run-001",
                "dataset_id": None,
                "status": "running",
                "terminal": False,
                "run_url": "https://console.apify.com/actors/runs/run-001",
                "changed": False,
            },
        }
        for operation_name in ("project_voc_native_start", "project_voc_native_read"):
            operation = Operation(operation_name)
            for item_count in (None, 59):
                candidate = json.loads(json.dumps(body))
                candidate["run"]["item_count"] = item_count
                with self.subTest(operation=operation_name, item_count=item_count):
                    validated = validate_operation_response(operation, candidate)
                    assert isinstance(validated, dict)
                    self.assertEqual(validated["run"]["item_count"], item_count)
            rejected = json.loads(json.dumps(body))
            rejected["run"]["unexpected"] = True
            with (
                self.subTest(operation=operation_name, extra=True),
                self.assertRaises(ContractViolation),
            ):
                validate_operation_response(operation, rejected)

    def test_v2_native_run_contract_keeps_actor_and_item_count(self) -> None:
        path = ROOT / "contracts/audience-platform-public-v2.openapi.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        schema = document["components"]["schemas"]["ApifyNativeRunResult"]
        self.assertIs(schema["additionalProperties"], False)
        for name in ("actor_id", "actor_title", "item_count"):
            self.assertIn(name, schema["properties"])


if __name__ == "__main__":
    unittest.main()
