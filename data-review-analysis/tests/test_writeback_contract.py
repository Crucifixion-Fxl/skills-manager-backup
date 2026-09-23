from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESOLVER_PATH = ROOT / "scripts" / "resolve_issue_target.py"
PROGRESS_SKILL_PATH = ROOT.parent / "delivery-progress-analysis" / "SKILL.md"


def load_resolver():
    spec = importlib.util.spec_from_file_location("resolve_issue_target", RESOLVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load resolver from {RESOLVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DataReviewWritebackContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.payload = json.loads(
            (ROOT / "evals" / "evals.json").read_text(encoding="utf-8")
        )
        cls.cases = {case["mode"]: case for case in cls.payload["evals"]}
        cls.resolver = load_resolver()

    def test_default_policy_is_target_driven_not_approval_driven(self) -> None:
        self.assertIn("目标驱动自动回流", self.skill)
        self.assertIn("不要求逐次人工批准", self.skill)
        self.assertNotIn("issue_writeback=allowed", self.skill)

    def test_eval_covers_unique_zero_and_ambiguous_target_outcomes(self) -> None:
        expected = {
            "offline_auto_unique_target": "write_count=1",
            "offline_auto_no_target": "write_count=0",
            "offline_auto_ambiguous_target": "write_count=0",
        }
        for mode, oracle in expected.items():
            with self.subTest(mode=mode):
                case = self.cases[mode]
                self.assertIn(oracle, case["expected_output"])

    def test_target_resolution_eval_requires_structural_evidence(self) -> None:
        for mode in (
            "offline_auto_unique_target",
            "offline_auto_no_target",
            "offline_auto_ambiguous_target",
        ):
            checks = "\n".join(
                assertion["check"] for assertion in self.cases[mode]["assertions"]
            )
            self.assertIn("结构化关联", checks)

    def test_classifier_only_promotes_adapter_verified_receipt_kinds(self) -> None:
        result = self.resolver.resolve(
            {
                "allowed_project": "applications/naturehood",
                "allowed_destination_project_ids": ["applications/naturehood"],
                "readonly": False,
                "candidates": [
                    {
                        "project": "applications/naturehood",
                        "iid": 167,
                        "evidence": [
                            {
                                "kind": "protected_repo_mapping",
                                "ref": "release/viconature/header-image/2026-09-09",
                            }
                        ],
                    },
                    {
                        "project": "applications/naturehood",
                        "iid": 198,
                        "evidence": [
                            {"kind": "gitlab_related", "ref": "mr:88"},
                            {"kind": "canonical_key", "ref": "header-image"},
                        ],
                    },
                ],
            }
        )
        self.assertEqual("ready", result["status"])
        self.assertEqual(1, result["write_count"])
        self.assertEqual(167, result["target"]["iid"])
        self.assertEqual([198], [item["iid"] for item in result["untrusted_candidates"]])

    def test_resolver_fails_closed_for_zero_ambiguous_readonly_and_scope(self) -> None:
        trusted = {
            "project": "applications/naturehood",
            "iid": 167,
            "evidence": [{"kind": "trusted_event_issue", "ref": "binding:167"}],
        }
        cases = (
            (
                {
                    "allowed_project": "applications/naturehood",
                    "allowed_destination_project_ids": ["applications/naturehood"],
                    "candidates": [],
                },
                "skipped-no-target",
                0,
            ),
            (
                {
                    "allowed_project": "applications/naturehood",
                    "allowed_destination_project_ids": ["applications/naturehood"],
                    "candidates": [
                        trusted,
                        {
                            "project": "applications/naturehood",
                            "iid": 198,
                            "evidence": [
                                {
                                    "kind": "protected_repo_mapping",
                                    "ref": "release:legacy-conversion",
                                }
                            ],
                        },
                    ],
                },
                "skipped-ambiguous",
                0,
            ),
            (
                {
                    "allowed_project": "applications/naturehood",
                    "allowed_destination_project_ids": ["applications/naturehood"],
                    "readonly": True,
                    "candidates": [trusted],
                },
                "readonly",
                0,
            ),
            (
                {
                    "allowed_project": "applications/naturehood",
                    "allowed_destination_project_ids": ["applications/naturehood"],
                    "candidates": [
                        {
                            **trusted,
                            "project": "applications/another-project",
                        }
                    ],
                },
                "skipped-no-target",
                0,
            ),
        )
        for payload, status, write_count in cases:
            with self.subTest(status=status):
                result = self.resolver.resolve(payload)
                self.assertEqual(status, result["status"])
                self.assertEqual(write_count, result["write_count"])

    def test_resolver_deduplicates_one_issue_and_rejects_unknown_provenance(self) -> None:
        duplicate = {
            "project": "applications/naturehood",
            "iid": 167,
            "evidence": [
                {"kind": "protected_repo_mapping", "ref": "release:header-image"}
            ],
        }
        result = self.resolver.resolve(
            {
                "allowed_project": "applications/naturehood",
                "allowed_destination_project_ids": ["applications/naturehood"],
                "candidates": [duplicate, duplicate],
            }
        )
        self.assertEqual("ready", result["status"])
        self.assertEqual(1, len(result["targets"]))

        invalid = {
            **duplicate,
            "evidence": [{"kind": "model_inference", "ref": "looks similar"}],
        }
        with self.assertRaises(self.resolver.ResolutionError):
            self.resolver.resolve(
                {
                    "allowed_project": "applications/naturehood",
                    "allowed_destination_project_ids": ["applications/naturehood"],
                    "candidates": [invalid],
                }
            )

    def test_classifier_requires_and_enforces_destination_allowlist(self) -> None:
        candidate = {
            "project": "applications/naturehood",
            "iid": 167,
            "evidence": [{"kind": "trusted_event_issue", "ref": "receipt:binding:167"}],
        }
        with self.assertRaises(self.resolver.ResolutionError):
            self.resolver.resolve(
                {
                    "allowed_project": "applications/naturehood",
                    "candidates": [candidate],
                }
            )

        rejected = self.resolver.resolve(
            {
                "allowed_project": "applications/naturehood",
                "allowed_destination_project_ids": ["applications/another-project"],
                "candidates": [candidate],
            }
        )
        self.assertEqual("skipped-no-target", rejected["status"])
        self.assertEqual(0, rejected["write_count"])
        self.assertFalse(rejected["destination_allowed"])

    def test_upsert_decision_covers_create_update_and_noop(self) -> None:
        marker = "<!-- data-review-evidence-index:v1 -->"
        base = {
            "single_writer": True,
            "writer_author_id": 42,
            "desired_body": f"{marker}\n## 数据证据索引\n- metric: 10%",
        }
        create = self.resolver.decide_upsert({**base, "notes": []})
        self.assertEqual(("create", 1), (create["status"], create["write_count"]))

        existing = {
            "id": 1001,
            "author_id": 42,
            "editable": True,
            "body": f"{marker}\n## 数据证据索引\n- metric: 8%",
        }
        update = self.resolver.decide_upsert({**base, "notes": [existing]})
        self.assertEqual(("update", 1), (update["status"], update["write_count"]))
        self.assertEqual(1001, update["note_id"])

        noop = self.resolver.decide_upsert(
            {
                **base,
                "desired_body": f"{base['desired_body']}\n本次运行时间：2026-09-15 08:00 UTC",
                "notes": [
                    {
                        **existing,
                        "body": f"{base['desired_body']}\n本次运行时间：2026-09-14 08:00 UTC",
                    }
                ],
            }
        )
        self.assertEqual(("no-op", 0), (noop["status"], noop["write_count"]))

    def test_upsert_decision_fails_closed_for_writer_and_marker_conflicts(self) -> None:
        marker = "<!-- data-review-evidence-index:v1 -->"
        base = {
            "single_writer": True,
            "writer_author_id": "bot-42",
            "desired_body": f"{marker}\n## 数据证据索引",
        }
        marked = {
            "id": 1001,
            "author_id": "bot-42",
            "editable": True,
            "body": f"{marker}\nold",
        }
        cases = (
            ({**base, "single_writer": False, "notes": []}, "single-writer-required"),
            ({**base, "notes": [marked, {**marked, "id": 1002}]}, "duplicate-marker"),
            (
                {
                    **base,
                    "notes": [{**marked, "author_id": "other", "editable": True}],
                },
                "marker-not-owned",
            ),
            (
                {**base, "notes": [{**marked, "editable": False}]},
                "marker-not-editable",
            ),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                result = self.resolver.decide_upsert(payload)
                self.assertEqual("conflict", result["status"])
                self.assertEqual(reason, result["reason"])
                self.assertEqual(0, result["write_count"])

    def test_eval_inputs_are_executable_and_cover_independent_conflicts(self) -> None:
        required_modes = {
            "offline_auto_unique_target",
            "offline_auto_no_target",
            "offline_auto_ambiguous_target",
            "offline_explicit_readonly",
            "offline_no_single_writer_conflict",
            "offline_duplicate_marker_conflict",
            "offline_uneditable_marker_conflict",
            "offline_platform_independent_superset_success",
        }
        self.assertTrue(required_modes.issubset(self.cases))
        for case in self.payload["evals"]:
            with self.subTest(mode=case["mode"]):
                for relative_path in case.get("files", []):
                    self.assertTrue((ROOT / relative_path).is_file(), relative_path)
        for mode in (
            "offline_explicit_readonly",
            "offline_partial_platform_failure",
            "offline_channel_context_with_fresh_aggregate_data",
        ):
            with self.subTest(mode=mode):
                self.assertIn(
                    "Issue 回流=readonly, write_count=0",
                    self.cases[mode]["expected_output"],
                )

        self.assertIn(
            "write_count=0", self.cases["offline_repeat_noop"]["expected_output"]
        )

    def test_issue_comment_example_starts_with_exact_marker(self) -> None:
        example = self.skill.split("### Issue 证据索引", 1)[1]
        fenced = example.split("```markdown", 1)[1].split("```", 1)[0].lstrip("\n")
        self.assertTrue(
            fenced.startswith("<!-- data-review-evidence-index:v1 -->\n"),
            fenced.splitlines()[0],
        )

    def test_workflow_contract_separates_skill_canvas_and_run_context(self) -> None:
        section = self.skill.split("## Buzz Workflow 调用", 1)[1]
        self.assertIn("@<agent> 使用 $addx:data-review-analysis。", section)
        self.assertIn("复盘周期：最近 3 个月", section)
        self.assertIn("分析时点：触发时", section)
        self.assertIn("复盘对象：最近发布的需求", section)
        self.assertIn("Workflow 专属", section)
        self.assertIn("Canvas", section)
        self.assertIn("通用方法", section)
        self.assertNotIn("channel_id=", section)
        self.assertNotIn("project=", section)
        self.assertNotIn("trusted_hosts=", section)
        self.assertNotIn("issue_writeback=", section)
        self.assertIn("可信 host", section)
        self.assertIn("owner 控制的 Agent prompt", section)

    def test_same_thread_publication_authority_stays_in_owner_prompt(self) -> None:
        for required in (
            "数据出口 allowlist",
            "同 Thread",
            "不需要逐次披露审批",
            "Workflow 不能授予披露权限",
            "Channel ACL",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_traceability_and_output_security_gates_are_explicit(self) -> None:
        for required in (
            "报告阈值和决策阈值",
            "未设定",
            "来源元数据不完整",
            "exact host allowlist",
            "重定向目标",
            "userinfo",
            "非默认端口",
            "project + issue iid + index version + note ID",
            "updated_at",
            "write verification failed",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_output_contract_uses_decision_tables_without_hiding_metric_provenance(self) -> None:
        output = self.skill.split("## 输出契约", 1)[1].split("## Examples", 1)[0]
        for required in (
            "功能／需求结论表",
            "指标对比表",
            "指标口径表",
            "实验状态表",
            "覆盖与缺口表",
            "口径 ID",
            "同一事实不要同时在段落和表格重复展开",
        ):
            with self.subTest(required=required):
                self.assertIn(required, output)
        self.assertIn("| 功能／需求 | 发布证据 | 核心结论 | 结论强度 | 下一动作 |", output)
        self.assertIn("| 口径 ID | 来源 | 窗口／时区 | 分子／分母 | 过滤／去重 | 样本／新鲜度 |", output)

        case = self.cases["offline_multi_feature_tabular_report"]
        checks = "\n".join(item["check"] for item in case["assertions"])
        self.assertIn("表格", checks)
        self.assertIn("口径 ID", checks)
        self.assertIn("不重复", checks)

    def test_responsible_people_mentions_are_verified_and_not_workflow_owned(self) -> None:
        for required in (
            "GitLab `assignee.username`",
            "唯一精确匹配",
            "当前 Channel member",
            "显式 `p` tag",
            "不得只凭 display_name",
            "不得 `@all`",
            "Workflow 不保存责任人名单",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

        case = self.cases["offline_responsible_people_not_channel_members"]
        checks = "\n".join(item["check"] for item in case["assertions"])
        self.assertIn("cfang", checks)
        self.assertIn("xcao", checks)
        self.assertIn("不发送真实 mention", checks)

    def test_eval_covers_traceability_and_output_security_failures(self) -> None:
        required_modes = {
            "offline_missing_business_thresholds",
            "offline_incomplete_discussion_provenance",
            "offline_missing_trusted_host_allowlist",
            "offline_writeback_readback_failure",
        }
        self.assertTrue(required_modes.issubset(self.cases))
        self.assertIn(
            "write_count=0",
            self.cases["offline_missing_trusted_host_allowlist"]["expected_output"],
        )
        self.assertIn(
            "write verification failed",
            self.cases["offline_writeback_readback_failure"]["expected_output"],
        )

    def test_progress_skill_uses_the_same_configuration_ownership(self) -> None:
        progress = PROGRESS_SKILL_PATH.read_text(encoding="utf-8")
        section = progress.split("## Buzz Workflow 调用", 1)[1]
        for required in (
            "Skill",
            "Canvas",
            "Workflow 专属",
            "Agent prompt",
            "汇报周期",
            "分析时点",
        ):
            with self.subTest(required=required):
                self.assertIn(required, progress)
        self.assertNotIn("channel_id=", section)
        self.assertNotIn("project=", section)

    def test_baseline_resolution_and_platform_results_are_independent(self) -> None:
        self.assertIn("基线选择顺序", self.skill)
        self.assertIn("同一 phase 的 control", self.skill)
        self.assertIn("无需 Canvas 重复指定", self.skill)
        self.assertIn("等长前置窗口", self.skill)
        self.assertIn("当前窗口的使用量、漏斗和质量事实", self.skill)
        self.assertIn("数据源彼此独立降级", self.skill)

        case = self.cases["offline_platform_independent_superset_success"]
        checks = "\n".join(assertion["check"] for assertion in case["assertions"])
        self.assertIn("Superset", checks)
        self.assertIn("GrowthBook", checks)
        self.assertIn("不能把整份复盘降级", checks)

    def test_ci_runs_the_data_review_contract(self) -> None:
        ci = (ROOT.parents[1] / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("data-review-analysis:unit:", ci)
        self.assertIn(
            "python3 -m unittest discover -s skills/data-review-analysis/tests",
            ci,
        )
        self.assertIn('"skills/data-review-analysis/**/*"', ci)


if __name__ == "__main__":
    unittest.main(verbosity=2)
