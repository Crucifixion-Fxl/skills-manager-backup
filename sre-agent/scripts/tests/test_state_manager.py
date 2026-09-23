#!/usr/bin/env python3
"""state_manager.py 单元测试。"""

import json
import os
import tempfile
import pytest
import yaml

# 让 import 找到 scripts/
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state_manager import StateManager


@pytest.fixture
def state_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mgr(state_dir):
    return StateManager(state_dir)


class TestInit:
    def test_creates_directories_and_empty_state(self, mgr, state_dir):
        assert os.path.isfile(os.path.join(state_dir, "alert_state.json"))
        assert os.path.isdir(os.path.join(state_dir, "investigations"))
        assert os.path.isdir(os.path.join(state_dir, "executions"))
        assert os.path.isdir(os.path.join(state_dir, "approvals"))
        assert os.path.isdir(os.path.join(state_dir, "knowledge"))

    def test_empty_state_structure(self, mgr):
        state = mgr.load_state()
        assert state["last_poll_at"] is None
        assert state["processed_incident_ids"] == []
        assert state["active_correlation_groups"] == {}
        assert state["completed_correlation_groups"] == {}
        assert state["cg_counter"] == 1


class TestCorrelationGroup:
    def test_create_cg(self, mgr):
        cg_id = mgr.create_cg(
            incidents=["Q1ABC123"],
            service="grafana-ai",
            environment="cn-prod",
        )
        assert cg_id == "CG-1"
        state = mgr.load_state()
        cg = state["active_correlation_groups"]["CG-1"]
        assert cg["incidents"] == ["Q1ABC123"]
        assert cg["service"] == "grafana-ai"
        assert cg["status"] == "dispatch_pending"
        assert state["cg_counter"] == 2

    def test_add_incidents_to_cg(self, mgr):
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        mgr.add_incidents_to_cg("CG-1", ["Q1DEF456", "Q1GHI789"])
        state = mgr.load_state()
        assert state["active_correlation_groups"]["CG-1"]["incidents"] == [
            "Q1ABC123", "Q1DEF456", "Q1GHI789"
        ]

    def test_complete_cg(self, mgr):
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        mgr.complete_cg("CG-1")
        state = mgr.load_state()
        assert "CG-1" not in state["active_correlation_groups"]
        assert "CG-1" in state["completed_correlation_groups"]
        assert state["completed_correlation_groups"]["CG-1"]["result_path"] == "investigations/CG-1/report.yaml"


class TestProcessedIncidents:
    def test_mark_processed(self, mgr):
        mgr.mark_processed(["Q1ABC123", "Q1DEF456"])
        assert mgr.is_processed("Q1ABC123")
        assert not mgr.is_processed("Q1NEW999")

    def test_filter_new(self, mgr):
        mgr.mark_processed(["Q1ABC123"])
        new = mgr.filter_new_incidents(["Q1ABC123", "Q1DEF456", "Q1GHI789"])
        assert new == ["Q1DEF456", "Q1GHI789"]


class TestPendingBatch:
    def test_save_and_load_pending(self, mgr, state_dir):
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        incidents_data = [{"id": "Q1DEF456", "title": "test"}]
        mgr.save_pending_batch("CG-1", 2, incidents_data)

        path = os.path.join(state_dir, "investigations", "CG-1", "pending-alerts", "batch-2.json")
        assert os.path.isfile(path)

        batches = mgr.load_pending_batches("CG-1")
        assert len(batches) == 1
        assert batches[0][0]["id"] == "Q1DEF456"

    def test_clear_pending(self, mgr, state_dir):
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        mgr.save_pending_batch("CG-1", 2, [{"id": "Q1DEF456"}])
        mgr.clear_pending("CG-1")
        assert mgr.load_pending_batches("CG-1") == []


class TestCGResult:
    def test_save_and_load_result(self, mgr, state_dir):
        result = {
            "type": "incident_report",
            "alert_summary": {"entity": "thanos-cn"},
            "causal_chain": {"confidence": "high"},
            "solutions": {"short_term": [{"action": "add limits"}]},
        }
        mgr.save_cg_result("CG-1", result)

        report_path = os.path.join(state_dir, "investigations", "CG-1", "report.yaml")
        assert os.path.isfile(report_path)

        loaded = mgr.load_cg_result("CG-1")
        assert loaded["alert_summary"]["entity"] == "thanos-cn"


class TestExecution:
    def test_get_execution_dir(self, mgr, state_dir):
        d = mgr.get_execution_dir("CG-1", 0)
        assert d == os.path.join(state_dir, "executions", "CG-1-solution-0")
        assert os.path.isdir(d)
        assert os.path.isdir(os.path.join(d, "backup"))

    def test_get_execution_dir_multiple(self, mgr, state_dir):
        d0 = mgr.get_execution_dir("CG-1", 0)
        d1 = mgr.get_execution_dir("CG-1", 1)
        assert d0 != d1
        assert os.path.isdir(os.path.join(d1, "backup"))


class TestApproval:
    def test_save_and_load_approval(self, mgr):
        mgr.save_approval("CG-1", 0, {
            "status": "pending_approval",
            "approval_sent_at": "2026-03-25T10:55:00Z",
            "solution_summary": "add resource limits",
        })
        approval = mgr.load_approval("CG-1", 0)
        assert approval["status"] == "pending_approval"

    def test_update_approval_status(self, mgr):
        mgr.save_approval("CG-1", 0, {"status": "pending_approval"})
        mgr.update_approval_status("CG-1", 0, "approved")
        assert mgr.load_approval("CG-1", 0)["status"] == "approved"

    def test_list_expired_approvals(self, mgr):
        mgr.save_approval("CG-1", 0, {
            "status": "pending_approval",
            "approval_sent_at": "2026-03-23T10:00:00Z",  # >48h ago
        })
        mgr.save_approval("CG-1", 1, {
            "status": "pending_approval",
            "approval_sent_at": "2026-03-25T10:00:00Z",  # recent
        })
        expired = mgr.list_expired_approvals(now="2026-03-25T11:00:00Z", ttl_hours=48)
        assert len(expired) == 1
        assert expired[0] == ("CG-1", 0)


class TestInvestigatingSince:
    def test_create_cg_has_no_investigating_since(self, mgr):
        cg_id = mgr.create_cg(["Q1ABC"], "grafana", "US")
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["investigating_since"] is None

    def test_confirm_investigating_sets_investigating_since(self, mgr):
        cg_id = mgr.create_cg(["Q1ABC"], "grafana", "US")
        mgr.confirm_investigating(cg_id)
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["status"] == "investigating"
        assert cg["investigating_since"] is not None
        from datetime import datetime
        datetime.fromisoformat(cg["investigating_since"].replace("Z", "+00:00"))


class TestCreateCGExtendedFields:
    def test_create_cg_with_recurrence_fields(self, mgr):
        cg_id = mgr.create_cg(
            incidents=["INC-1"], service="svc", environment="us-prod",
            is_recurrence=True, recurrence_of="CG-5", fault_entity="fe", service_entity="se"
        )
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["is_recurrence"] is True
        assert cg["recurrence_of"] == "CG-5"
        assert cg["fault_entity"] == "fe"
        assert cg["service_entity"] == "se"

    def test_create_cg_default_recurrence_fields(self, mgr):
        cg_id = mgr.create_cg(incidents=["INC-1"], service="svc", environment="us-prod")
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["is_recurrence"] is False
        assert cg["recurrence_of"] is None
        assert cg["fault_entity"] is None
        assert cg["service_entity"] is None


class TestConfirmInvestigating:
    def test_dispatch_pending_to_investigating(self, mgr):
        cg_id = mgr.create_cg(["Q1ABC"], "grafana", "US")
        assert mgr.get_active_cgs()[cg_id]["status"] == "dispatch_pending"

        mgr.confirm_investigating(cg_id)
        assert mgr.get_active_cgs()[cg_id]["status"] == "investigating"

    def test_rejects_non_dispatch_pending(self, mgr):
        cg_id = mgr.create_cg(["Q1ABC"], "grafana", "US")
        mgr.confirm_investigating(cg_id)  # now "investigating"
        with pytest.raises(ValueError, match="expected 'dispatch_pending'"):
            mgr.confirm_investigating(cg_id)

    def test_rejects_unknown_cg(self, mgr):
        with pytest.raises(ValueError, match="not found"):
            mgr.confirm_investigating("CG-999")


class TestReviewTracking:
    def test_create_cg_has_review_fields(self, mgr):
        cg_id = mgr.create_cg(["INC-1"], "svc", "us-prod")
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["review_dispatched"] is False
        assert cg["revision_count"] == 0

    def test_mark_review_dispatched(self, mgr):
        cg_id = mgr.create_cg(["INC-1"], "svc", "us-prod")
        mgr.mark_review_dispatched(cg_id)
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["review_dispatched"] is True

    def test_reset_for_revision(self, mgr):
        cg_id = mgr.create_cg(["INC-1"], "svc", "us-prod")
        mgr.confirm_investigating(cg_id)
        mgr.mark_review_dispatched(cg_id)
        mgr.reset_for_revision(cg_id)
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["status"] == "dispatch_pending"
        assert cg["review_dispatched"] is False
        assert cg["revision_count"] == 1
        assert cg["investigating_since"] is None

    def test_reset_for_revision_increments_count(self, mgr):
        cg_id = mgr.create_cg(["INC-1"], "svc", "us-prod")
        mgr.confirm_investigating(cg_id)
        mgr.reset_for_revision(cg_id)  # revision_count → 1
        mgr.confirm_investigating(cg_id)
        mgr.reset_for_revision(cg_id)  # revision_count → 2
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["revision_count"] == 2

    def test_save_and_load_review_result(self, mgr):
        review = {"cg_id": "CG-1", "overall_verdict": "approved"}
        mgr.save_review_result("CG-1", review)
        loaded = mgr.load_review_result("CG-1")
        assert loaded["overall_verdict"] == "approved"

    def test_load_review_result_nonexistent(self, mgr):
        assert mgr.load_review_result("CG-999") is None

    def test_delete_review_result(self, mgr):
        mgr.save_review_result("CG-1", {"verdict": "test"})
        mgr.delete_review_result("CG-1")
        assert mgr.load_review_result("CG-1") is None


class TestPublicStateHelpers:
    """Tests for update_active_cg, set_solutions_status, trim_processed_ids."""

    def test_update_active_cg(self, mgr):
        cg_id = mgr.create_cg([{"id": "P1"}], "svc", "env")
        result = mgr.update_active_cg(cg_id, {"status": "investigation_failed", "custom_field": 42})
        assert result is True
        state = mgr.load_state()
        assert state["active_correlation_groups"][cg_id]["status"] == "investigation_failed"
        assert state["active_correlation_groups"][cg_id]["custom_field"] == 42

    def test_update_active_cg_nonexistent_returns_false(self, mgr):
        result = mgr.update_active_cg("CG-999", {"status": "test"})
        assert result is False
        state = mgr.load_state()
        assert "CG-999" not in state["active_correlation_groups"]

    def test_set_solutions_status(self, mgr):
        cg_id = mgr.create_cg([{"id": "P1"}], "svc", "env")
        mgr.complete_cg(cg_id)
        result = mgr.set_solutions_status(cg_id, {"0": "approved", "1": "rejected"})
        assert result is True
        state = mgr.load_state()
        assert state["completed_correlation_groups"][cg_id]["solutions_status"] == {"0": "approved", "1": "rejected"}

    def test_set_solutions_status_nonexistent_returns_false(self, mgr):
        result = mgr.set_solutions_status("CG-999", {"0": "test"})
        assert result is False
        state = mgr.load_state()
        assert "CG-999" not in state.get("completed_correlation_groups", {})

    def test_trim_processed_ids_under_limit(self, mgr):
        state = mgr.load_state()
        state["processed_incident_ids"] = ["a", "b", "c"]
        mgr._save_state(state)
        mgr.trim_processed_ids(max_ids=10)
        assert mgr.load_state()["processed_incident_ids"] == ["a", "b", "c"]

    def test_trim_processed_ids_over_limit(self, mgr):
        state = mgr.load_state()
        state["processed_incident_ids"] = list(range(20))
        mgr._save_state(state)
        mgr.trim_processed_ids(max_ids=5)
        result = mgr.load_state()["processed_incident_ids"]
        assert len(result) == 5
        assert result == [15, 16, 17, 18, 19]
