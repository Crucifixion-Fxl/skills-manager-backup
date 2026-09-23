#!/usr/bin/env python3
"""
ReviewBuilder — Review subagent 用于构建 review.yaml 的 Builder 类。
方法签名即 schema，save() 时校验并写入文件。
"""

import os
from datetime import datetime, timezone

import yaml


_REQUIRED_SECTIONS = [
    "root_cause_review", "timeline_review", "impact_review",
    "risk_review", "evidence_review",
]
_VALID_VERDICTS = {"approved", "needs_revision"}


class ReviewBuilder:
    """Builder for review.yaml files."""

    def __init__(self, cg_id, output_dir):
        self._cg_id = cg_id
        self._output_dir = output_dir
        self._sections = {}
        self._solutions_review = []
        self._overall_verdict = None
        self._revision_notes = None

    def add_section(self, name, verdict, checkpoints):
        """Add a review section (e.g. root_cause_review)."""
        for cp in checkpoints:
            if not all(k in cp for k in ("id", "passed", "detail")):
                raise ValueError(f"Checkpoint missing required fields (id, passed, detail): {cp}")
        self._sections[name] = {
            "verdict": verdict,
            "checkpoints": checkpoints,
        }

    def add_solution_review(self, index, verdict, checkpoints, recommendation=""):
        """Add solution review result."""
        self._solutions_review.append({
            "index": index,
            "verdict": verdict,
            "checkpoints": checkpoints,
            "recommendation": recommendation,
        })

    def set_verdict(self, overall_verdict, revision_notes=""):
        """Set overall verdict."""
        if overall_verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"overall_verdict must be one of {sorted(_VALID_VERDICTS)}, got '{overall_verdict}'"
            )
        self._overall_verdict = overall_verdict
        self._revision_notes = revision_notes

    def _validate(self):
        """Validate all required fields. Returns list of errors."""
        errors = []
        for section in _REQUIRED_SECTIONS:
            if section not in self._sections:
                errors.append(f"Missing review section: {section}")
        if not self._overall_verdict:
            errors.append("Missing: call set_verdict() before save()")
        if self._overall_verdict == "needs_revision" and not self._revision_notes:
            errors.append("needs_revision requires non-empty revision_notes")
        return errors

    def _build_dict(self):
        """Assemble the review dict."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        review = {
            "cg_id": self._cg_id,
            "reviewed_at": now,
        }
        for section in _REQUIRED_SECTIONS:
            if section in self._sections:
                review[section] = self._sections[section]
        if self._solutions_review:
            review["solutions_review"] = self._solutions_review
        review["overall_verdict"] = self._overall_verdict
        if self._revision_notes:
            review["revision_notes"] = self._revision_notes
        return review

    def save(self):
        """Validate and write review.yaml. Returns True on success."""
        errors = self._validate()
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print(f"  - {e}")
            return False
        os.makedirs(self._output_dir, exist_ok=True)
        review = self._build_dict()
        path = os.path.join(self._output_dir, "review.yaml")
        with open(path, "w") as f:
            yaml.dump(review, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"Review saved to {path}")
        return True
