import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_problem_artifact.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("problem_artifact_validator", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("validator module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT.parent))
    return module


def reidentify(value):
    value = copy.deepcopy(value)
    value.pop("artifact_id", None)
    value.pop("content_sha256", None)
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    value["artifact_id"] = "problem-analysis:" + digest
    value["content_sha256"] = "sha256:" + digest
    return value


def evidence(evidence_id, evidence_class="SOURCE", relation="SUPPORTS"):
    locator = {
        "SOURCE": "https://gitlab.addx.ai/issues/software/frontend/-/issues/238",
        "REPOSITORY": "repo@" + "b" * 40 + ":src/view.py:12",
        "RUNTIME": "query:ui-regression:2026-09-06T00:00:00Z",
    }[evidence_class]
    return {
        "evidence_id": evidence_id,
        "evidence_class": evidence_class,
        "claim": "该证据用于核验可观察的问题边界。",
        "result": "已读取受控来源并形成脱敏摘要。",
        "locator": locator,
        "observed_at": "2026-09-06T00:00:00Z",
        "content_sha256": "sha256:" + "1" * 64,
        "relation": relation,
        "limitations": ["仅保留脱敏摘要。"],
    }


def valid_artifact(status="ANALYZED_NO_ROOT_CAUSE"):
    value = {
        "schema_version": "problem-analysis/2.0",
        "identity": {
            "workflow_id": "issue-problem-analysis-v2",
            "node_id": "problem_analysis",
            "attempt_id": "attempt-238-1",
            "producer_agent": "buzz:" + "9" * 64,
            "produced_at": "2026-09-06T00:00:00Z",
            "source_revision": "2026-09-04T01:02:03.000Z",
        },
        "status": status,
        "evidence_depth": "SOURCE_ONLY",
        "source_baseline": {
            "kind": "GITLAB_ISSUE",
            "stable_ref": "https://gitlab.addx.ai/issues/software/frontend/-/issues/238",
            "revision": "2026-09-04T01:02:03.000Z",
            "retrieved_at": "2026-09-06T00:00:00Z",
            "content_sha256": "sha256:" + "1" * 64,
            "retrieval_status": "VERIFIED",
        },
        "context_resolution": {
            "status": "UNMAPPED",
            "explanation": "没有已审核的唯一仓库映射。",
            "candidate_contexts": [],
            "repository": None,
        },
        "problem_frame": {
            "affected_actor": "查看详情的用户。",
            "scenario": "首次打开详情并缩放图片。",
            "observed_behavior": "图片初始放大且无法缩小。",
            "expected_behavior": "图片适配窗口并允许缩放。",
            "deviation": "初始比例和交互偏离预期。",
            "reproducibility": "仅由 Issue 描述确认，尚未运行复现。",
            "known_unknowns": ["机型和资源比例未知。"],
        },
        "impact": {
            "affected_surface": "图片详情页。",
            "scope": "当前仅确认一个 Issue 报告。",
            "frequency": "未知。",
            "severity_basis": "核心浏览交互受阻。",
            "user_or_business_effect": "用户可能无法查看完整图片。",
        },
        "boundary_matrix": [{
            "dimension": "交互路径",
            "inside": ["Issue 描述路径。"],
            "outside": [],
            "unknown": ["其他入口。"],
            "evidence_ids": ["E-SOURCE"],
            "next_probe": "读取可复现证据。",
        }],
        "evidence_ledger": [evidence("E-SOURCE")],
        "methodology_proof": {
            "skill_id": "addx:root-cause-analysis",
            "mode": "ISSUE_TRIAGE",
            "package_sha256": "sha256:" + "2" * 64,
            "proof_verified": True,
            "evidence_refs": ["E-SOURCE"],
        },
        "supporting_skill_reviews": [],
        "causal_neighbors": [],
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "statement": "状态初始化可能与期望不一致。",
                "state": "OPEN",
                "falsifier": "若首帧状态符合基线则证伪。",
                "falsifier_result": "NOT_RUN",
                "evidence_for": ["E-SOURCE"],
                "evidence_against": [],
                "next_probe": "采集首帧状态。",
                "confidence_basis": "现象方向一致但证据不足。",
            },
            {
                "hypothesis_id": "H2",
                "statement": "事件竞争可能阻断交互。",
                "state": "OPEN",
                "falsifier": "若事件完整到达则证伪。",
                "falsifier_result": "NOT_RUN",
                "evidence_for": [],
                "evidence_against": [],
                "next_probe": "记录事件链。",
                "confidence_basis": "尚无直接证据。",
            },
        ],
        "code_findings": {
            "inspection_status": "NOT_INSPECTED",
            "reason": "仓库尚未唯一映射。",
            "findings": [],
        },
        "recommended_actions": [],
        "verification_matrix": [],
        "open_questions": [],
        "quality_gate": {
            "source_verified": True,
            "context_claims_bounded": True,
            "evidence_traceable": True,
            "competing_hypotheses_present": True,
            "falsifiers_present": True,
            "causal_neighbors_reconciled": False,
            "magnitude_direction_reconciled": False,
            "no_unexplained_symptoms": False,
            "methodology_proof_verified": True,
            "root_cause_gate_passed": False,
            "gate_evidence_ids": ["E-SOURCE"],
            "missing_requirements": ["需要仓库或运行时证据。"],
        },
    }
    return reidentify(value)


def confirmed_artifact():
    value = valid_artifact("ROOT_CAUSE_CONFIRMED")
    value["evidence_depth"] = "RUNTIME_READ"
    value["context_resolution"] = {
        "status": "UNIQUE",
        "explanation": "受审核映射唯一确认仓库。",
        "candidate_contexts": [],
        "repository": {"exact_head_sha": "b" * 40},
    }
    value["evidence_ledger"] += [
        evidence("E-REPO", "REPOSITORY"),
        evidence("E-RUNTIME", "RUNTIME"),
        evidence("E-REFUTE", "RUNTIME", "REFUTES"),
    ]
    value["methodology_proof"].update(
        mode="ROOT_CAUSE_INVESTIGATION",
        evidence_refs=["E-REPO", "E-RUNTIME"],
    )
    value["causal_neighbors"] = [{
        "neighbor_id": "N-UPSTREAM",
        "relationship": "UPSTREAM",
        "mechanism": "资源元数据可能改变布局输入。",
        "check_status": "CHECKED",
        "evidence_ids": ["E-REFUTE"],
        "remaining_uncertainty": "当前证据已排除该解释。",
    }]
    value["hypotheses"][0].update(
        state="KILLED", falsifier_result="FALSIFIED",
        evidence_for=[], evidence_against=["E-REFUTE"], next_probe=None,
    )
    value["hypotheses"][1].update(
        state="CONFIRMED", falsifier_result="SURVIVED",
        evidence_for=["E-RUNTIME"], next_probe=None,
    )
    for field, current in value["quality_gate"].items():
        if isinstance(current, bool):
            value["quality_gate"][field] = True
    value["quality_gate"]["gate_evidence_ids"] = [
        "E-SOURCE", "E-REPO", "E-RUNTIME", "E-REFUTE"
    ]
    value["quality_gate"]["missing_requirements"] = []
    return reidentify(value)


class ProblemArtifactValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def assertRejected(self, value, fragment):
        errors = self.validator.validate_artifact(value)
        self.assertTrue(errors)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_accepts_analyzed_triage_artifact(self):
        self.assertEqual([], self.validator.validate_artifact(valid_artifact()))

    def test_rejects_empty_artifact_and_control_objects(self):
        self.assertTrue(self.validator.validate_artifact({}))
        self.assertTrue(self.validator.validate_control_outcome({}))

    def test_rejects_extra_top_level_fields(self):
        value = valid_artifact()
        value["uncontracted"] = "must be rejected"
        self.assertRejected(reidentify(value), "top-level fields")

    def test_rejects_duplicate_methodology_self_review(self):
        value = valid_artifact()
        value["supporting_skill_reviews"] = [{"skill_id": "addx:root-cause-analysis"}]
        self.assertRejected(reidentify(value), "self review")

    def test_rejects_repository_depth_without_repository_evidence(self):
        value = valid_artifact()
        value["evidence_depth"] = "REPOSITORY_READ"
        self.assertRejected(reidentify(value), "REPOSITORY")

    def test_rejects_root_cause_under_triage_mode(self):
        value = valid_artifact("ROOT_CAUSE_CONFIRMED")
        self.assertRejected(reidentify(value), "ROOT_CAUSE_INVESTIGATION")

    def test_rejects_triaged_outcome_under_investigation_mode(self):
        value = valid_artifact("TRIAGED")
        value["hypotheses"] = []
        value["methodology_proof"]["mode"] = "ROOT_CAUSE_INVESTIGATION"
        self.assertRejected(reidentify(value), "TRIAGED requires ISSUE_TRIAGE")

    def test_rejects_non_root_status_with_passing_root_cause_gate(self):
        value = valid_artifact()
        value["quality_gate"]["root_cause_gate_passed"] = True
        self.assertRejected(reidentify(value), "root_cause_gate_passed")

    def test_rejects_verified_methodology_without_evidence_binding(self):
        value = valid_artifact()
        value["methodology_proof"]["evidence_refs"] = []
        self.assertRejected(reidentify(value), "methodology_proof.evidence_refs")

    def test_accepts_root_cause_after_full_discrimination(self):
        self.assertEqual([], self.validator.validate_artifact(confirmed_artifact()))

    def test_rejects_root_cause_with_uninspected_causal_neighbor(self):
        value = confirmed_artifact()
        value["causal_neighbors"][0]["check_status"] = "NOT_CHECKED"
        value["causal_neighbors"][0]["evidence_ids"] = []
        self.assertRejected(reidentify(value), "causal neighbor")

    def test_rejects_incomplete_supporting_skill_review(self):
        value = valid_artifact()
        value["supporting_skill_reviews"] = [{"skill_id": "addx:sentry"}]
        self.assertRejected(reidentify(value), "supporting_skill_reviews")

    def test_accepts_zero_write_needs_input(self):
        value = {
            "schema_version": "problem-analysis-control/1.0",
            "outcome_kind": "NEEDS_INPUT",
            "write_policy": "ZERO_WRITE",
            "question": "该 Issue 对应哪个实现仓库？",
            "reason": "缺少唯一 context mapping。",
        }
        self.assertEqual([], self.validator.validate_control_outcome(value))

    def test_rejects_artifact_fields_in_needs_input(self):
        value = {
            "schema_version": "problem-analysis-control/1.0",
            "outcome_kind": "NEEDS_INPUT",
            "write_policy": "ZERO_WRITE",
            "question": "该 Issue 对应哪个实现仓库？",
            "reason": "缺少唯一 context mapping。",
            "artifact_id": "not-allowed",
        }
        errors = self.validator.validate_control_outcome(value)
        self.assertTrue(any("artifact_id" in error for error in errors))

    def test_cli_rejects_non_object_json_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("[]", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = self.validator.main([str(path)])
        self.assertEqual(2, result)
        self.assertFalse(json.loads(output.getvalue())["valid"])


if __name__ == "__main__":
    unittest.main()
