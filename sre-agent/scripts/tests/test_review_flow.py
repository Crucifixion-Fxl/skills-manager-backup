#!/usr/bin/env python3
"""Review flow unit tests."""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher_loop import _determine_revision_type, _load_investigation_checkpoints, _delete_report
from state_manager import StateManager
from checkpoint_manager import CheckpointManager


@pytest.fixture
def state_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mgr(state_dir):
    return StateManager(state_dir)


class TestDetermineRevisionType:
    """Test _determine_revision_type returns correct revision type based on failed categories."""

    def test_r_fail_returns_full(self):
        review = {
            "root_cause_review": {"verdict": "fail"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) == "full"

    def test_t_fail_returns_full(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "fail"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) == "full"

    def test_e_fail_returns_full(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "fail"},
        }
        assert _determine_revision_type(review) == "full"

    def test_only_i_fail_returns_partial(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "fail"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) == "partial"

    def test_only_k_fail_returns_partial(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "fail"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) == "partial"

    def test_only_s_fail_returns_partial(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
            "solutions_review": [{"verdict": "rejected"}],
        }
        assert _determine_revision_type(review) == "partial"

    def test_all_pass_returns_none(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) is None

    def test_empty_review_returns_none(self):
        # No sections present => rte_failed=False, iks_failed=False => None
        assert _determine_revision_type({}) is None

    def test_rte_beats_iks(self):
        # Both R and I fail — full should take precedence over partial
        review = {
            "root_cause_review": {"verdict": "fail"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "fail"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        }
        assert _determine_revision_type(review) == "full"

    def test_solutions_all_accepted_returns_none(self):
        review = {
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
            "solutions_review": [{"verdict": "accepted"}, {"verdict": "accepted"}],
        }
        assert _determine_revision_type(review) is None


class TestLoadInvestigationCheckpoints:
    def test_loads_checkpoints_with_data(self, state_dir):
        cg_id = "CG-1"
        progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
        cp = CheckpointManager(progress_dir)
        cp.write("01_quick_assess", {
            "stage": "QUICK_ASSESS",
            "data": {"classification": "pod_oom"},
        })
        cp.write("02_validate", {
            "stage": "VALIDATE",
            "data": {"status": "ongoing"},
        })
        result = _load_investigation_checkpoints(state_dir, cg_id)
        assert len(result) >= 2

    def test_returns_empty_for_nonexistent(self, state_dir):
        result = _load_investigation_checkpoints(state_dir, "CG-999")
        assert result == [] or result == {}

    def test_filters_out_heartbeat_checkpoints(self, state_dir):
        cg_id = "CG-2"
        progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
        cp = CheckpointManager(progress_dir)
        # heartbeat: no "data" key
        cp.write("00_heartbeat", {"stage": "HEARTBEAT"})
        # real checkpoint with data
        cp.write("01_quick_assess", {
            "stage": "QUICK_ASSESS",
            "data": {"classification": "disk_full"},
        })
        result = _load_investigation_checkpoints(state_dir, cg_id)
        assert len(result) == 1
        assert result[0].get("data") is not None


class TestDeleteReport:
    def test_deletes_existing_report(self, state_dir, mgr):
        mgr.save_cg_result("CG-1", {"type": "test"})
        assert mgr.load_cg_result("CG-1") is not None
        _delete_report(state_dir, "CG-1")
        assert mgr.load_cg_result("CG-1") is None

    def test_no_error_when_missing(self, state_dir):
        _delete_report(state_dir, "CG-999")  # should not raise

    def test_report_file_removed(self, state_dir, mgr):
        mgr.save_cg_result("CG-3", {"type": "check"})
        report_path = os.path.join(state_dir, "investigations", "CG-3", "report.yaml")
        assert os.path.isfile(report_path)
        _delete_report(state_dir, "CG-3")
        assert not os.path.isfile(report_path)
