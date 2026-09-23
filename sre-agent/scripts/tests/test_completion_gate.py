#!/usr/bin/env python3
"""completion_gate.py unit tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from completion_gate import CompletionGate


# ---------------------------------------------------------------------------
# Helpers — reusable minimal-valid dicts
# ---------------------------------------------------------------------------

def _valid_report():
    return {
        "root_cause": {"summary": "OOM kill due to memory leak in cache layer"},
        "causal_chain": {
            "chain": [
                {"node_type": "trigger", "description": "Traffic spike"},
                {"node_type": "root_cause", "description": "Memory leak"},
            ]
        },
        "conclusion": "Restart pod and apply memory limit patch",
        "severity": "high",
        "alert_summary": {"status": "resolved"},
    }


def _valid_review():
    return {
        "overall_verdict": "approved",
        "root_cause_review": {"checkpoints": [{"item": "ok", "pass": True}]},
        "timeline_review": {"checkpoints": [{"item": "ok", "pass": True}]},
        "evidence_review": {"checkpoints": [{"item": "ok", "pass": True}]},
    }


def _valid_result():
    return {"status": "success", "summary": "Fix applied successfully"}


def _valid_pattern():
    return {
        "pattern": {
            "pattern_id": "PAT-OOM-001",
            "match_conditions": [{"field": "alert_name", "op": "eq", "value": "OOMKilled"}],
            "execution_steps": [{"action": "restart_pod"}],
            "risk": "medium",
        }
    }


# ---------------------------------------------------------------------------
# validate_report
# ---------------------------------------------------------------------------

class TestValidateReport:
    def test_valid_full_report_passes(self):
        ok, errors = CompletionGate.validate_report(_valid_report())
        assert ok is True
        assert errors == []

    def test_incomplete_report_fails_with_specific_errors(self):
        """Only title + severity, no root_cause / chain / conclusion."""
        report = {"title": "Something broke", "severity": "high"}
        ok, errors = CompletionGate.validate_report(report)
        assert ok is False
        assert "missing root_cause" in errors
        assert "missing root_cause.summary" in errors
        assert "empty causal_chain" in errors
        assert "no root_cause node in causal_chain" in errors
        assert "missing conclusion" in errors
        assert "missing alert_summary.status" in errors

    def test_transient_event_report_passes(self):
        report = _valid_report()
        report["alert_summary"]["status"] = "transient_event"
        ok, errors = CompletionGate.validate_report(report)
        assert ok is True
        assert errors == []

    def test_invalid_status_value_fails(self):
        report = _valid_report()
        report["alert_summary"]["status"] = "banana"
        ok, errors = CompletionGate.validate_report(report)
        assert ok is False
        assert any("invalid alert_summary.status" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_review
# ---------------------------------------------------------------------------

class TestValidateReview:
    def test_valid_review_passes(self):
        ok, errors = CompletionGate.validate_review(_valid_review())
        assert ok is True
        assert errors == []

    def test_missing_overall_verdict_fails(self):
        review = _valid_review()
        del review["overall_verdict"]
        ok, errors = CompletionGate.validate_review(review)
        assert ok is False
        assert "missing overall_verdict" in errors

    def test_missing_core_sections_fails(self):
        review = {"overall_verdict": "approved"}
        ok, errors = CompletionGate.validate_review(review)
        assert ok is False
        assert "missing root_cause_review.checkpoints" in errors
        assert "missing timeline_review.checkpoints" in errors
        assert "missing evidence_review.checkpoints" in errors


# ---------------------------------------------------------------------------
# validate_result
# ---------------------------------------------------------------------------

class TestValidateResult:
    def test_valid_success_result_passes(self):
        ok, errors = CompletionGate.validate_result(_valid_result())
        assert ok is True
        assert errors == []

    def test_invalid_status_fails(self):
        result = {"status": "unknown", "summary": "something"}
        ok, errors = CompletionGate.validate_result(result)
        assert ok is False
        assert any("invalid status" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_pattern
# ---------------------------------------------------------------------------

class TestValidatePattern:
    def test_valid_pattern_passes(self):
        ok, errors = CompletionGate.validate_pattern(_valid_pattern())
        assert ok is True
        assert errors == []

    def test_missing_top_level_pattern_key_fails(self):
        ok, errors = CompletionGate.validate_pattern({"foo": "bar"})
        assert ok is False
        assert "missing top-level 'pattern' key" in errors

    def test_pattern_with_missing_fields_fails(self):
        data = {"pattern": {"pattern_id": "PAT-001"}}
        ok, errors = CompletionGate.validate_pattern(data)
        assert ok is False
        assert "missing pattern.match_conditions" in errors
        assert "missing pattern.execution_steps" in errors
        assert any("invalid pattern.risk" in e for e in errors)
