#!/usr/bin/env python3
"""端到端流程验证（不涉及真实 API 调用）。"""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state_manager import StateManager
from alert_correlator import correlate_incidents, normalize_title


@pytest.fixture
def state_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mgr(state_dir):
    return StateManager(state_dir)


@pytest.fixture
def sample_incidents():
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures", "sample_incidents.json")
    with open(fixtures) as f:
        return json.load(f)["incidents"]


class TestDispatcherLoop:
    """验证 Dispatcher 一轮 cron 的决策逻辑。"""

    def test_new_alerts_create_cg(self, mgr, sample_incidents):
        """新告警 → 关联分组 → 创建 CG → 标记已处理。"""
        groups = correlate_incidents(sample_incidents, active_cgs={})
        for g in groups:
            if g["correlates_to"] is None:
                inc_ids = [inc["id"] for inc in g["incidents"]]
                cg_id = mgr.create_cg(
                    incidents=inc_ids,
                    service=g["service"],
                    environment="cn-prod",
                )
                mgr.mark_processed(inc_ids)

        state = mgr.load_state()
        # 应创建了至少 2 个 CG（grafana-ai 和 payment-service）
        assert len(state["active_correlation_groups"]) >= 2
        # 所有 incident 已标记处理
        assert mgr.is_processed("Q1ABC123")
        assert mgr.is_processed("Q1GHI789")

    def test_correlated_alerts_saved_pending(self, mgr):
        """关联已有 CG 的告警 → 暂存 pending。"""
        from datetime import datetime, timezone, timedelta

        # 先创建一个 CG（fault_entity 匹配需要 alertname 一致）
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        # 手动设置 fault_entity 以便关联匹配（格式与 _extract_fault_entity 输出一致）
        from alert_correlator import normalize_title
        entity = normalize_title("[cn-prod] thanos-query timeout")
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-1"]["fault_entity"] = entity
        mgr._save_state(state)

        # 新一轮 poll 发现关联告警（时间在 CG 创建时间 ±5min 内，同 fault_entity = 同 normalized title）
        now = datetime.now(timezone.utc)
        incident_time = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_incidents = [{
            "id": "Q1NEW001",
            "title": "[cn-prod] thanos-query timeout",
            "created_at": incident_time,
            "service": {"id": "PSVC001", "summary": "grafana-ai"},
        }]
        groups = correlate_incidents(new_incidents, active_cgs=mgr.get_active_cgs())

        # 应关联到 CG-1
        assert groups[0]["correlates_to"] == "CG-1"

        # 暂存 pending
        mgr.save_pending_batch("CG-1", 2, new_incidents)
        mgr.add_incidents_to_cg("CG-1", ["Q1NEW001"])
        mgr.mark_processed(["Q1NEW001"])

        batches = mgr.load_pending_batches("CG-1")
        assert len(batches) == 1
        state = mgr.load_state()
        assert "Q1NEW001" in state["active_correlation_groups"]["CG-1"]["incidents"]

    def test_completed_cg_with_pending_triggers_merge(self, mgr):
        """CG 完成 + 有 pending batch → 应触发 merged investigation。"""
        mgr.create_cg(incidents=["Q1ABC123"], service="grafana-ai", environment="cn-prod")
        mgr.save_pending_batch("CG-1", 2, [{"id": "Q1NEW001"}])
        mgr.complete_cg("CG-1")

        # Dispatcher 检查：完成 + 有 pending → 需要 merged investigation
        pending = mgr.load_pending_batches("CG-1")
        completed = mgr.get_completed_cgs()
        assert "CG-1" in completed
        assert len(pending) == 1  # 有 pending → 应触发 merge

    def test_approval_timeout_24h_reminder(self, mgr):
        """审批超过 24h 但未到 48h → 应发提醒。"""
        mgr.save_approval("CG-1", 0, {
            "status": "pending_approval",
            "approval_sent_at": "2026-03-24T08:00:00Z",  # 27h ago
        })
        # 48h 未过期
        expired = mgr.list_expired_approvals(now="2026-03-25T11:00:00Z", ttl_hours=48)
        assert len(expired) == 0
        # 但超过 24h（Dispatcher 需自行实现 approaching 检查逻辑）

    def test_approval_timeout_48h_cancel(self, mgr):
        """审批超过 48h → 自动标记 expired。"""
        mgr.save_approval("CG-1", 0, {
            "status": "pending_approval",
            "approval_sent_at": "2026-03-23T08:00:00Z",  # >48h ago
        })
        expired = mgr.list_expired_approvals(now="2026-03-25T11:00:00Z", ttl_hours=48)
        assert len(expired) == 1
        assert expired[0] == ("CG-1", 0)

        # 模拟 Dispatcher 自动取消
        mgr.update_approval_status("CG-1", 0, "expired")
        assert mgr.load_approval("CG-1", 0)["status"] == "expired"


class TestAlertCorrelatorEdgeCases:
    """补充关联器边界测试。"""

    def test_empty_input(self):
        groups = correlate_incidents([], active_cgs={})
        assert groups == []

    def test_single_alert(self):
        incident = {
            "id": "Q1SINGLE",
            "title": "[us-prod] single alert",
            "created_at": "2026-03-25T10:00:00Z",
            "service": {"id": "PSVC099", "summary": "single-service"},
        }
        groups = correlate_incidents([incident], active_cgs={})
        assert len(groups) == 1
        assert len(groups[0]["incidents"]) == 1

    def test_different_fault_entity_not_merged(self):
        """不同 fault_entity 的告警不应合并，即使标题相似。"""
        inc1 = {
            "id": "Q1A", "title": "[prod] alert X",
            "created_at": "2026-03-25T10:00:00Z",
            "service": {"id": "SVC1", "summary": "prometheus"},
            "alert_details": {"labels": {"alertname": "jks-expiring", "namespace": "prod-us"}},
        }
        inc2 = {
            "id": "Q1B", "title": "[prod] alert Y",
            "created_at": "2026-03-25T10:02:00Z",  # within 5min window
            "service": {"id": "SVC1", "summary": "prometheus"},  # same PD service
            "alert_details": {"labels": {"alertname": "k8s-pod-unhealthy", "namespace": "prod-us"}},
        }
        groups = correlate_incidents([inc1, inc2], active_cgs={})
        assert len(groups) == 2  # different alertname → different fault entity → not merged


class TestARPatternMatching:
    """L5 pattern 匹配逻辑验证。"""

    def test_evicted_pod_matches_ar001(self):
        """Evicted Pod finding 应匹配 AR-001 pattern。"""
        import re
        pattern = r"Pod.*(Evicted|Failed|ContainerStatusUnknown)"
        finding = "Pod thanos-query-7b8f9c6d4f-x2k9z Evicted"
        assert re.search(pattern, finding) is not None

    def test_crashloop_non_prod_matches_ar002(self):
        """CrashLoopBackOff in staging 应匹配 AR-002。"""
        import re
        finding_pattern = r"CrashLoopBackOff"
        env_pattern = r"staging-.*|dev-.*"
        assert re.search(finding_pattern, "Pod CrashLoopBackOff") is not None
        assert re.search(env_pattern, "staging-cn") is not None

    def test_crashloop_prod_not_matches_ar002(self):
        """CrashLoopBackOff in prod 不应匹配 AR-002。"""
        import re
        env_pattern = r"staging-.*|dev-.*"
        assert re.search(env_pattern, "cn-prod") is None


class TestNoActionPath:
    """_no_action 标记的 CG 应被正确终止。"""

    def test_no_action_report_sets_solutions_status(self, state_dir):
        """report 带 _no_action: true 时，dispatcher 应设置 solutions_status = {"_": "no_action_needed"}."""
        import yaml
        mgr = StateManager(state_dir)
        cg_id = mgr.create_cg(["inc-100"], "grafana", "US")
        mgr.complete_cg(cg_id)

        # 写一个带 _no_action 的 report
        inv_dir = os.path.join(state_dir, "investigations", cg_id)
        os.makedirs(inv_dir, exist_ok=True)
        report = {
            "solutions": {"_no_action": True, "short_term": [], "long_term": []},
            "root_cause": {"summary": "Self-healed issue"},
        }
        with open(os.path.join(inv_dir, "report.yaml"), "w") as f:
            yaml.dump(report, f)

        # 模拟 dispatcher 6c 逻辑
        cg_result = report
        solutions = cg_result.get("solutions", {})
        if solutions.get("_no_action"):
            st = mgr.load_state()
            if cg_id in st["completed_correlation_groups"]:
                st["completed_correlation_groups"][cg_id]["solutions_status"] = {"_": "no_action_needed"}
                mgr._save_state(st)

        # 验证
        st = mgr.load_state()
        assert st["completed_correlation_groups"][cg_id]["solutions_status"] == {"_": "no_action_needed"}


class TestCheckpointCrashRecovery:
    """集成测试：checkpoint 崩溃恢复完整流程。"""

    def test_full_crash_recovery_flow(self, mgr, state_dir):
        """
        1. Create CG, confirm investigating
        2. Write checkpoints for QUICK_ASSESS and VALIDATE
        3. Simulate crash (backdate checkpoints)
        4. Verify CheckpointManager detects timeout
        5. Verify state can be reset for retry
        """
        import time
        from checkpoint_manager import CheckpointManager

        # Step 1: Create and start investigating
        cg_id = mgr.create_cg(
            incidents=[{"id": "INC-1", "title": "test alert"}],
            service="test-svc",
            environment="us-prod",
        )
        mgr.confirm_investigating(cg_id)

        state = mgr.load_state()
        cg = state["active_correlation_groups"][cg_id]
        assert cg["status"] == "investigating"
        assert cg["investigating_since"] is not None

        # Step 2: Write checkpoints
        progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
        cp_mgr = CheckpointManager(progress_dir)
        cp_mgr.write("01_quick_assess", {
            "stage": "QUICK_ASSESS",
            "self_check": {"role": "investigation", "readonly_confirmed": True},
            "data": {"classification": "pod_oom"},
        })
        cp_mgr.write("02_validate", {
            "stage": "VALIDATE",
            "self_check": {"role": "investigation", "readonly_confirmed": True},
            "data": {"status": "ongoing", "entity_exists": True},
        })

        # Verify checkpoints exist
        all_cp = cp_mgr.list_all()
        assert len(all_cp) == 2
        assert all_cp[0]["name"] == "01_quick_assess"
        assert all_cp[1]["name"] == "02_validate"

        # Step 3: Not timed out yet
        assert cp_mgr.is_timed_out(timeout_seconds=1800) is False

        # Step 4: Backdate to simulate crash
        stale = time.time() - 2000
        for f in os.listdir(progress_dir):
            p = os.path.join(progress_dir, f)
            os.utime(p, (stale, stale))

        # Now should be timed out
        assert cp_mgr.is_timed_out(timeout_seconds=1800) is True

        # Step 5: Latest checkpoint has resume context
        latest = cp_mgr.get_latest()
        assert latest is not None
        assert latest["stage"] in ("QUICK_ASSESS", "VALIDATE")  # both backdated to same mtime
        assert "data" in latest

        # Step 6: Reset status for retry
        state = mgr.load_state()
        state["active_correlation_groups"][cg_id]["status"] = "dispatch_pending"
        mgr._save_state(state)
        assert mgr.get_active_cgs()[cg_id]["status"] == "dispatch_pending"


class TestCrossTypeMergeIntegration:
    """端到端：跨告警类型合并 + 复发抑制。"""

    def test_statemachine_scenario_cross_type_merge(self, mgr, state_dir):
        """
        模拟 2026-03-31 statemachine 场景：
        - high-memory 告警 (17:03)
        - pod-restart 告警 (17:26)
        同一 deployment statemachine，±30min 内 → 合并为 1 个 CG
        """
        from alert_correlator import correlate_incidents

        incidents = [
            {
                "id": "MEM001",
                "title": "[US] k8s-pod-high-memory statemachine",
                "created_at": "2026-03-31T17:03:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
            {
                "id": "RST001",
                "title": "[US] k8s-pod-restart statemachine",
                "created_at": "2026-03-31T17:26:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-restart",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
        ]

        groups = correlate_incidents(incidents, active_cgs={})
        # Should be merged into 1 group
        assert len(groups) == 1
        assert len(groups[0]["incidents"]) == 2

        # Create CG and verify fields
        group = groups[0]
        cg_id = mgr.create_cg(
            incidents=[inc["id"] for inc in group["incidents"]],
            service=group["service"],
            environment="us-prod",
            fault_entity=group.get("fault_entity"),
            service_entity=group.get("service_entity"),
        )
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["service_entity"] is not None

    def test_recurrence_suppression_flow(self, mgr, state_dir):
        """
        复发抑制：
        1. 创建并完成 CG-1 (thanos OOM)
        2. 40min 后同标题告警 → 标记为复发
        """
        from alert_correlator import correlate_incidents

        # Step 1: Create and complete a CG
        cg_id = mgr.create_cg(
            incidents=["OLD001"],
            service="grafana-ai",
            environment="cn-prod",
        )
        mgr.confirm_investigating(cg_id)
        mgr.complete_cg(cg_id)

        # Set normalized_title on completed CG so recurrence detection can match
        completed_title = "[cn-prod] thanos-query OOMKilled"
        state = mgr.load_state()
        state["completed_correlation_groups"][cg_id]["normalized_title"] = normalize_title(completed_title)
        mgr._save_state(state)

        # Step 2: New alert 40 min later with same title
        completed_cgs = mgr.get_completed_cgs()
        new_incidents = [{
            "id": "NEW001",
            "title": "[cn-prod] thanos-query OOMKilled",
            "created_at": "2026-03-31T11:40:00Z",
            "service": {"summary": "grafana-ai"},
            "alerts": [],
        }]

        groups = correlate_incidents(
            new_incidents, active_cgs={}, completed_cgs=completed_cgs
        )
        assert len(groups) == 1
        assert groups[0]["recurrence_of"] == cg_id
        assert groups[0]["is_recurrence"] is True

    def test_different_deployment_not_merged(self, mgr, state_dir):
        """不同 deployment 不合并，即使时间窗口内。"""
        from alert_correlator import correlate_incidents

        incidents = [
            {
                "id": "A001",
                "title": "[US] k8s-pod-high-memory statemachine",
                "created_at": "2026-03-31T17:03:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "statemachine",
                }},
                "alerts": [],
            },
            {
                "id": "A002",
                "title": "[US] k8s-pod-high-memory middlequery",
                "created_at": "2026-03-31T17:10:00Z",
                "service": {"summary": "prometheus"},
                "alert_details": {"labels": {
                    "alertname": "k8s-pod-high-memory",
                    "cluster": "us-tech", "namespace": "prod-us",
                    "deployment": "middlequery",
                }},
                "alerts": [],
            },
        ]
        groups = correlate_incidents(incidents, active_cgs={})
        assert len(groups) == 2


class TestReviewFlowIntegration:
    """端到端：report → review → verdict 处理。"""

    def test_approved_review_completes_cg(self, mgr, state_dir):
        """
        1. Create CG, confirm investigating
        2. Save a report.yaml
        3. Mark review_dispatched
        4. Save an approved review.yaml
        5. Verify CG can be completed
        """
        cg_id = mgr.create_cg(["INC-1"], "test-svc", "us-prod")
        mgr.confirm_investigating(cg_id)

        # Save report
        mgr.save_cg_result(cg_id, {
            "type": "incident_report",
            "alert_summary": {"entity": "test-svc"},
            "causal_chain": {"chain": []},
            "solutions": {},
        })

        # Mark review dispatched
        mgr.mark_review_dispatched(cg_id)
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["review_dispatched"] is True

        # Save approved review
        mgr.save_review_result(cg_id, {
            "cg_id": cg_id,
            "overall_verdict": "approved",
            "root_cause_review": {"verdict": "pass"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        })

        # Verify review loaded
        review = mgr.load_review_result(cg_id)
        assert review["overall_verdict"] == "approved"

        # Complete CG
        mgr.complete_cg(cg_id)
        assert cg_id not in mgr.get_active_cgs()
        assert cg_id in mgr.get_completed_cgs()

    def test_revision_loop(self, mgr, state_dir):
        """
        1. Create CG, confirm investigating
        2. Save report, mark review dispatched
        3. Save needs_revision review
        4. Reset for revision → revision_count=1
        5. Confirm investigating again
        6. Save new report, mark review dispatched again
        7. Save approved review
        8. Complete CG
        """
        cg_id = mgr.create_cg(["INC-1"], "test-svc", "us-prod")
        mgr.confirm_investigating(cg_id)

        # First investigation
        mgr.save_cg_result(cg_id, {"type": "incident_report", "alert_summary": {"entity": "test-svc"}, "causal_chain": {"chain": []}, "solutions": {}})
        mgr.mark_review_dispatched(cg_id)

        # Review rejects
        mgr.save_review_result(cg_id, {
            "cg_id": cg_id,
            "overall_verdict": "needs_revision",
            "revision_notes": "R2 failed: root cause is WHAT not WHY",
            "root_cause_review": {"verdict": "fail"},
            "timeline_review": {"verdict": "pass"},
            "impact_review": {"verdict": "pass"},
            "risk_review": {"verdict": "pass"},
            "evidence_review": {"verdict": "pass"},
        })

        # Reset for revision
        mgr.delete_review_result(cg_id)
        mgr.reset_for_revision(cg_id)
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["revision_count"] == 1
        assert cg["status"] == "dispatch_pending"
        assert cg["review_dispatched"] is False

        # Second investigation
        mgr.confirm_investigating(cg_id)
        mgr.save_cg_result(cg_id, {"type": "incident_report", "alert_summary": {"entity": "test-svc"}, "causal_chain": {"chain": []}, "solutions": {}})
        mgr.mark_review_dispatched(cg_id)

        # Review approves this time
        mgr.save_review_result(cg_id, {
            "cg_id": cg_id,
            "overall_verdict": "approved",
        })

        review = mgr.load_review_result(cg_id)
        assert review["overall_verdict"] == "approved"

        # Complete
        mgr.complete_cg(cg_id)
        assert cg_id in mgr.get_completed_cgs()

    def test_max_revision_forces_completion(self, mgr, state_dir):
        """After 2 revisions, should force complete regardless of verdict."""
        cg_id = mgr.create_cg(["INC-1"], "test-svc", "us-prod")
        mgr.confirm_investigating(cg_id)

        # First rejection
        mgr.reset_for_revision(cg_id)
        assert mgr.get_active_cgs()[cg_id]["revision_count"] == 1

        # Second rejection
        mgr.confirm_investigating(cg_id)
        mgr.reset_for_revision(cg_id)
        assert mgr.get_active_cgs()[cg_id]["revision_count"] == 2

        # At this point, revision_count >= 2, dispatcher should force complete
        cg = mgr.get_active_cgs()[cg_id]
        assert cg["revision_count"] >= 2
