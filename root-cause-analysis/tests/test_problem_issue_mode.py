import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]


class ProblemIssueModeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.contract = (ROOT / "references/problem-analysis-contract.md").read_text(
            encoding="utf-8"
        )
        cls.routing = (ROOT / "references/issue-analysis-routing.md").read_text(
            encoding="utf-8"
        )

    def section(self, document: str, heading: str) -> str:
        match = re.search(
            rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", document
        )
        self.assertIsNotNone(match, f"missing section: {heading}")
        return match.group(1)

    def test_existing_skill_is_the_only_analysis_methodology(self):
        self.assertIn("name: root-cause-analysis", self.skill)
        self.assertIn("ISSUE_TRIAGE", self.skill)
        self.assertIn("ROOT_CAUSE_INVESTIGATION", self.skill)
        self.assertFalse((REPO_ROOT / "skills/problem-analysis-agent").exists())

    def test_issue_mode_routes_to_the_machine_contract(self):
        for value in (
            "problem-analysis/2.0",
            "TRIAGED",
            "ANALYZED_NO_ROOT_CAUSE",
            "ROOT_CAUSE_CONFIRMED",
            "BLOCKED",
            "SOURCE_ONLY",
            "REPOSITORY_READ",
            "RUNTIME_READ",
            "NEEDS_INPUT",
        ):
            self.assertIn(value, self.skill + self.contract)
        self.assertIn("references/problem-analysis-contract.md", self.skill)
        self.assertIn("references/issue-analysis-routing.md", self.skill)

    def test_methodology_proof_is_not_a_self_review(self):
        provenance = self.section(self.contract, "Methodology provenance")
        for token in (
            "`methodology_proof`",
            "`addx:root-cause-analysis`",
            "`mode`",
            "`package_sha256`",
            "`evidence_refs`",
        ):
            self.assertIn(token, provenance)
        self.assertIn("不是二次调用或自我 review", provenance)
        self.assertNotIn("review_only=true", self.contract + self.routing)
        self.assertNotIn("mandatory RCA record", self.contract + self.routing)

    def test_triage_and_investigation_have_distinct_gates(self):
        modes = self.section(self.contract, "Modes")
        gate = self.section(self.contract, "Root-cause gate")
        self.assertIn("ISSUE_TRIAGE", modes)
        self.assertIn("ROOT_CAUSE_INVESTIGATION", modes)
        self.assertIn("ROOT_CAUSE_CONFIRMED", gate)
        for rule in (
            "at least two competing hypotheses",
            "at least one KILLED hypothesis",
            "at least one CONFIRMED hypothesis",
            "magnitude and direction",
            "no material competing hypothesis remains OPEN",
        ):
            self.assertIn(rule, gate)

    def test_needs_input_is_a_zero_write_control_outcome(self):
        control = self.section(self.contract, "`NEEDS_INPUT` control outcome")
        self.assertIn("write_policy: ZERO_WRITE", control)
        self.assertIn("不生成 Artifact", control)
        self.assertIn("不写 GitLab", control)

    def test_ci_validates_the_existing_skill_path(self):
        ci = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("root-cause-analysis:unit:", ci)
        self.assertIn(
            "python -m unittest discover -s skills/root-cause-analysis/tests -v", ci
        )
        self.assertIn(
            "uv run python scripts/validate.py --skill skills/root-cause-analysis", ci
        )
        self.assertIn('"skills/root-cause-analysis/**/*"', ci)
        self.assertNotIn("problem-analysis-agent:unit:", ci)


if __name__ == "__main__":
    unittest.main()
