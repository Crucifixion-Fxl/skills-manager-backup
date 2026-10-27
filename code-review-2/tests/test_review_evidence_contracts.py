"""Consumer contract tests for optional code-review evidence capabilities."""

import json
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ReviewEvidenceContractTest(unittest.TestCase):
    def test_svg_preview_contract_is_minimal_and_compatible(self):
        self.assertEqual(
            read_json(REFERENCES / "svg-preview-contract.json"),
            {
                "schema_version": 1,
                "capability": "svg-preview/v1",
                "artifact": "design-evidence.json",
            },
        )

    def test_review_evidence_checkpoint_contract_names_the_trusted_tool_and_artifact(
        self,
    ):
        self.assertEqual(
            read_json(REFERENCES / "review-evidence-checkpoint-contract.json"),
            {
                "schema_version": 1,
                "capability": "review-evidence-checkpoint/v1",
                "artifact": "evidence-coverage.json",
                "tool": "review_evidence_checkpoint",
            },
        )

    def test_skill_routes_to_the_contract_consumers(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        design = (REFERENCES / "design-deliverable-review.md").read_text(
            encoding="utf-8"
        )
        checkpoint = (REFERENCES / "review-evidence-checkpoint.md").read_text(
            encoding="utf-8"
        )
        report = (REFERENCES / "design-review-report.md").read_text(encoding="utf-8")
        index = (REFERENCES / "REFERENCE.md").read_text(encoding="utf-8")
        self.assertIn("svg-preview-contract.json", design)
        self.assertIn("review-evidence-checkpoint-contract.json", checkpoint)
        self.assertIn("CODE_REVIEW_SVG_PREVIEW_ENABLED", design)
        self.assertIn("CODE_REVIEW_EVIDENCE_CHECKPOINT_ENABLED", checkpoint)
        self.assertIn("普通 technical review", skill)
        self.assertIn("its `role` and `required_by` basis", report)
        self.assertIn("review-evidence-checkpoint.md", index)

    def test_checkpoint_contract_keeps_actual_page_coverage_and_legacy_boundaries(self):
        guide = (REFERENCES / "review-evidence-checkpoint.md").read_text(
            encoding="utf-8"
        )
        for evidence in (
            "review-evidence-plan.json",
            "evidence-coverage.json",
            "plan_sha256",
            "diff_sha256",
            "page_sha256",
            "covered_pages",
            "unresolved_items",
            "generateRawTextPages",
            "validateRawTextPages",
            "page_source",
            "raw-text-pages",
            "raw-text:<ordinal>",
            "long-line-view",
            "view:<ordinal>",
            "ordinal",
            "byte_start",
            "byte_end",
            "line_count",
            "line_fragment",
            "`bytes`",
            "24 KiB",
            "400 个换行行数",
            "1600 bytes",
            "1800 字节（含换行）",
            "字节 offset",
            "拼接还原原始 diff SHA",
            "generateView",
            "validateView",
            "批量读取",
            "未截断",
            "不强制 `long_line_bytes=1`",
            "自造路径推断",
            "默认 long-line viewer",
            "不扩大 token、模型或 900 秒预算",
            "缺页",
            "重复页",
            "本次范围内的**整组合格候选**",
            "只有某个具体问题确实要求覆盖整组候选",
            "文件大小或后缀本身都不触发调用",
            "不为使用工具扩大 scope",
            "工具不替 reviewer 判断 `required_by`",
            "FINALIZE_MS=180000",
            "reserve_ms=180000",
            "不再 Read 新页",
            "not_applicable",
            "不新增通用 JSON parser",
            "浏览器",
            "generated",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, guide)
        self.assertNotIn("在适用文件存在时调用", guide)

    def test_visual_necessity_uses_shared_diff_scoped_rule(self):
        guide = (REFERENCES / "diff-scoped-review.md").read_text(encoding="utf-8")
        design = (REFERENCES / "design-deliverable-review.md").read_text(
            encoding="utf-8"
        )
        checkpoint = (REFERENCES / "review-evidence-checkpoint.md").read_text(
            encoding="utf-8"
        )
        report = (REFERENCES / "design-review-report.md").read_text(encoding="utf-8")
        for rule in (
            "按问题判断证据与视觉必要性",
            "只读取能回答本次变更问题的最小直接源码",
            "不能单独把技术审查升级为 `design`/`mixed`",
            "`required_by`",
            "不因出现 SVG 就要求预览",
            "必要预览缺失、不可读或未实际观察",
            "不新增 code/visual 状态字段",
            "review_evidence_checkpoint",
            "不要为使用工具扩大范围",
            "不能再用 optional 标记、改 profile 或删回执绕过缺页校验",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, guide)
        self.assertIn(
            "[范围规则](diff-scoped-review.md#按问题判断证据与视觉必要性)",
            design,
        )
        self.assertIn(
            "[共享范围规则](diff-scoped-review.md#按问题判断证据与视觉必要性)",
            checkpoint,
        )
        self.assertIn(
            "[diff-scoped evidence rule](diff-scoped-review.md#按问题判断证据与视觉必要性)",
            report,
        )
        self.assertIn(
            "The checkpoint helper does not judge this semantic basis.", report
        )
        for rule in (
            "mixed code findings belong in `technical_appendix`",
            "`dimensions.visual` records only actual visual observations",
            "keep that dimension `NOT_CHECKED`",
            "`should_pass=false`",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, report)

    def test_behavior_eval_covers_the_required_adversarial_cases(self):
        eval_root = SKILL / "evals/review-evidence"
        cases = read_json(eval_root / "cases.json")
        rubric = read_json(eval_root / "rubric.json")
        expected_ids = {
            "manifest-optional-cannot-hide-required",
            "html-static-docs-no-browser-gate",
            "html-interactive-docs-still-has-a-role",
            "ready-design-plan-cannot-drop-large-candidate",
            "mixed-ui-unrelated-large-json-does-not-start-checkpoint",
            "explicit-whole-candidate-checkpoint-missing-page-is-incomplete",
            "missing-page-receipt-is-incomplete",
            "long-line-read-truncation-needs-trusted-view-pages",
            "generated-json-needs-diff-and-provenance",
            "svg-source-answers-structure-question",
            "required-ui-layout-preview-missing",
            "pcb-visual-orientation-required-after-structural-checks",
            "svg-read-return-does-not-prove-visual-review",
            "ordinary-technical-image-does-not-trigger-svg-gate",
            "capability-mixing-stays-on-supported-side",
            "no-large-diff-is-not-applicable",
            "design-no-large-diff-needs-no-model-tool-call",
            "design-review-cannot-downgrade-to-technical",
            "technical-large-json-has-no-automatic-gate",
            "deadline-stops-new-tasks",
        }
        self.assertEqual({case["id"] for case in cases}, expected_ids)
        self.assertEqual({case["id"] for case in rubric}, expected_ids)
        cases_by_id = {case["id"]: case for case in cases}
        svg_source_case = cases_by_id["svg-source-answers-structure-question"]
        svg_source_evidence = svg_source_case["available_evidence"]
        self.assertEqual(svg_source_evidence["profile"], "technical")
        self.assertIsNone(svg_source_evidence["preview"])
        self.assertIn("Do not require an image", svg_source_case["output"])
        ui_case = cases_by_id["required-ui-layout-preview-missing"]
        self.assertTrue(ui_case["available_evidence"]["visual_evidence_required"])
        self.assertIsNone(ui_case["available_evidence"]["preview"])
        self.assertIn("should_pass=false", ui_case["output"])
        pcb_case = cases_by_id[
            "pcb-visual-orientation-required-after-structural-checks"
        ]
        pcb_evidence = pcb_case["available_evidence"]
        self.assertEqual(pcb_evidence["erc"], "pass")
        self.assertEqual(pcb_evidence["drc"], "pass")
        self.assertIsNone(pcb_evidence["assembled_preview"])
        self.assertIn("overall review incomplete", pcb_case["output"])
        optional_image = cases_by_id[
            "ordinary-technical-image-does-not-trigger-svg-gate"
        ]
        self.assertIsNone(optional_image["available_evidence"]["preview"])
        self.assertIn("do not claim a visual PASS", optional_image["output"])
        technical_large = cases_by_id["technical-large-json-has-no-automatic-gate"]
        technical_large_evidence = technical_large["available_evidence"]
        self.assertEqual(technical_large_evidence["profile"], "technical")
        self.assertFalse(technical_large_evidence["explicit_checkpoint_call"])
        self.assertFalse(technical_large_evidence["trusted_plan_exists"])
        mixed_ui = cases_by_id[
            "mixed-ui-unrelated-large-json-does-not-start-checkpoint"
        ]
        mixed_ui_evidence = mixed_ui["available_evidence"]
        self.assertEqual(mixed_ui_evidence["profile"], "mixed")
        self.assertFalse(mixed_ui_evidence["whole_candidate_set_required"])
        self.assertFalse(mixed_ui_evidence["explicit_checkpoint_call"])
        self.assertFalse(mixed_ui_evidence["plan_ready"])
        self.assertIsNone(mixed_ui_evidence["signup_preview"])
        self.assertIn("overall result incomplete", mixed_ui["output"])
        explicit_whole = cases_by_id[
            "explicit-whole-candidate-checkpoint-missing-page-is-incomplete"
        ]
        explicit_evidence = explicit_whole["available_evidence"]
        self.assertTrue(explicit_evidence["explicit_checkpoint_call"])
        self.assertFalse(explicit_evidence["tool_can_filter_candidates"])
        self.assertTrue(explicit_evidence["required_by"])
        self.assertEqual(
            explicit_evidence["covered_page_ids"], ["raw-text:1", "view:1"]
        )
        self.assertEqual(explicit_evidence["missing_page_id"], "raw-text:2")
        self.assertIn("local review is insufficient", explicit_whole["output"])
        missing_page_case = next(
            case for case in cases if case["id"] == "missing-page-receipt-is-incomplete"
        )
        planned_page_ids = {
            page["page_id"]
            for task in missing_page_case["available_evidence"]["tasks"]
            for page in task["pages"]
        }
        self.assertEqual(planned_page_ids, {"raw-text:1", "raw-text:2"})
        self.assertEqual(
            {
                task["page_source"]
                for task in missing_page_case["available_evidence"]["tasks"]
            },
            {"raw-text-pages"},
        )
        missing_page_rubric = next(
            item
            for item in rubric
            if item["id"] == "missing-page-receipt-is-incomplete"
        )
        self.assertIn("raw-text:2", missing_page_rubric["criteria"])

        truncation_case = next(
            case
            for case in cases
            if case["id"] == "long-line-read-truncation-needs-trusted-view-pages"
        )
        truncation_evidence = truncation_case["available_evidence"]
        self.assertTrue(truncation_evidence["prior_raw_text_read"]["truncated"])
        self.assertEqual(
            truncation_evidence["prior_raw_text_read"]["visible_characters"], 2000
        )
        self.assertEqual(
            truncation_evidence["prior_raw_text_read"]["page_bytes"], 24576
        )
        plan = truncation_evidence["trusted_plan"]
        self.assertEqual(plan["files"][0]["page_source"], "long-line-view")
        task = plan["tasks"][0]
        self.assertEqual(task["page_source"], "long-line-view")
        self.assertEqual(
            [page["page_id"] for page in task["pages"]],
            ["view:1", "view:2"],
        )
        self.assertTrue(
            all(
                page["page_path"] == "value supplied by trusted plan"
                for page in task["pages"]
            )
        )
        self.assertTrue(truncation_evidence["batch_read_supported"])

        downgrade_case = next(
            case
            for case in cases
            if case["id"] == "design-review-cannot-downgrade-to-technical"
        )
        downgrade_evidence = downgrade_case["available_evidence"]
        self.assertEqual(downgrade_evidence["trusted_profile"], "mixed")
        self.assertEqual(downgrade_evidence["reviewer_proposed_profile"], "technical")
        self.assertEqual(
            downgrade_evidence["plan_page_ids"], ["raw-text:1", "raw-text:2"]
        )

        capability_case = next(
            case
            for case in cases
            if case["id"] == "capability-mixing-stays-on-supported-side"
        )
        capability_rows = capability_case["available_evidence"]["rows"]
        self.assertEqual(len(capability_rows), 4)
        capability_matrix = {
            (row["skill_contract"], row["template_flag"]) for row in capability_rows
        }
        self.assertEqual(
            capability_matrix,
            {(False, False), (False, True), (True, False), (True, True)},
        )
