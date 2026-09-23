#!/usr/bin/env python3
"""dispatcher_loop.py 单元测试。"""

import json
import os
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# 让 import 找到 scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher_loop import run_loop
from state_manager import StateManager


@pytest.fixture
def state_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def skill_base_dir():
    """返回 sre-agent skill 的根目录（含 references/）。"""
    return os.path.join(os.path.dirname(__file__), "..", "..")


@pytest.fixture
def mgr(state_dir):
    return StateManager(state_dir)


def _make_incident(id, title="Test Alert", service="test-svc", urgency="high", created_at=None):
    """构造一个模拟 PD incident。"""
    return {
        "id": id,
        "title": title,
        "service": {"summary": service},
        "urgency": urgency,
        "created_at": created_at or "2026-03-27T10:00:00Z",
        "html_url": f"https://pagerduty.com/incidents/{id}",
        "incident_number": 12345,
        "status": "triggered",
    }


# ─── 测试用例 ───


class TestEmptyPoll:
    """test_empty_poll_returns_no_actions — 无新告警时 actions 为空。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-03-27T10:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_empty_poll_returns_no_actions(
        self, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir
    ):
        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True
        assert result["actions"] == []
        assert result["summary"]["new_alerts"] == 0
        assert result["summary"]["cgs_created"] == 0


class TestNewAlertCreatesCG:
    """test_new_alert_creates_cg_and_action — 新告警创建 CG + 输出 dispatch_investigation action。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll")
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_new_alert_creates_cg_and_action(
        self, mock_send, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        incident = _make_incident("INC-001", title="[cn-prod] test OOM")
        mock_poll.return_value = {
            "total_triggered": 1,
            "new_count": 1,
            "new_incidents": [incident],
            "last_poll": "2026-03-27T10:01:00Z",
        }

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True
        assert result["summary"]["new_alerts"] == 1
        assert result["summary"]["cgs_created"] == 1

        # 应有一个 dispatch_investigation action
        investigation_actions = [
            a for a in result["actions"] if a["type"] == "dispatch_investigation"
        ]
        assert len(investigation_actions) == 1
        assert investigation_actions[0]["cg_id"] == "CG-1"
        prompt_file = investigation_actions[0]["prompt_file"]
        assert prompt_file != ""
        assert os.path.isfile(prompt_file)
        with open(prompt_file, "r") as f:
            assert len(f.read()) > 0

        # CG 应存在于 active_correlation_groups
        state = mgr.load_state()
        assert "CG-1" in state["active_correlation_groups"]
        assert state["active_correlation_groups"]["CG-1"]["status"] == "dispatch_pending"

        # incident 应已标记为 processed
        assert "INC-001" in state["processed_incident_ids"]


class TestDispatchPendingRetry:
    """test_dispatch_pending_retry — 已有 dispatch_pending CG 自动重输出 action。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-03-27T10:02:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_dispatch_pending_retry(
        self, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        # 预设一个 dispatch_pending 的 CG
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-99"] = {
            "created_at": "2026-03-27T09:50:00Z",
            "incidents": ["INC-X"],
            "service": "test-svc",
            "environment": "cn-prod",
            "status": "dispatch_pending",
            "investigation_agent_id": None,
            "phase1_sent": True,
        }
        state["processed_incident_ids"] = ["INC-X"]
        mgr._save_state(state)

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True

        # 应有一个针对 CG-99 的 dispatch_investigation action
        investigation_actions = [
            a for a in result["actions"]
            if a["type"] == "dispatch_investigation" and a["cg_id"] == "CG-99"
        ]
        assert len(investigation_actions) == 1


class TestReportDetectedCompletesCG:
    """test_report_detected_completes_cg — report.yaml 存在时触发 complete_cg。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-03-27T10:03:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_report_detected_completes_cg(
        self, mock_send, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        # 预设一个 investigating 状态的 CG（review 已派发且已 approved）
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-10"] = {
            "created_at": "2026-03-27T09:40:00Z",
            "incidents": ["INC-A"],
            "service": "thanos-query",
            "environment": "cn-prod",
            "status": "investigating",
            "investigation_agent_id": None,
            "phase1_sent": True,
            "review_dispatched": True,
            "revision_count": 0,
        }
        state["processed_incident_ids"] = ["INC-A"]
        mgr._save_state(state)

        # 写 report.yaml
        report = {
            "severity": "high",
            "conclusion": "Test conclusion text.",
            "root_cause": {"summary": "Test root cause summary text."},
            "alert_summary": {
                "service": "thanos-query",
                "environment": "cn-prod",
                "alert_count": 1,
                "status": "resolved",
            },
            "causal_chain": {"chain": [{"node_type": "root_cause", "event": "x", "evidence": "y"}]},
            "solutions": {
                "short_term": [],
                "long_term": [],
            },
        }
        mgr.save_cg_result("CG-10", report)

        # 写 approved review.yaml
        mgr.save_review_result("CG-10", {
            "cg_id": "CG-10",
            "overall_verdict": "approved",
            "reviewed_at": "2026-03-27T10:00:00Z",
            "root_cause_review": {"checkpoints": ["root cause verified"]},
            "timeline_review": {"checkpoints": ["timeline verified"]},
            "evidence_review": {"checkpoints": ["evidence verified"]},
        })

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True
        assert result["summary"]["investigations_completed"] == 1

        # CG-10 应从 active 移到 completed
        state = mgr.load_state()
        assert "CG-10" not in state["active_correlation_groups"]
        assert "CG-10" in state["completed_correlation_groups"]


class TestTransientEventFastPath:
    """test_transient_event_skips_review — status=transient_event 直接结案不过 review。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-04-14T23:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_transient_event_skips_review(
        self, mock_send, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        # 预设一个 investigating 状态的 CG，review 尚未派发
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-T1"] = {
            "created_at": "2026-04-14T22:14:00Z",
            "incidents": ["INC-T1"],
            "service": "prometheus",
            "environment": "US",
            "status": "investigating",
            "investigation_agent_id": None,
            "phase1_sent": True,
            "review_dispatched": False,
            "revision_count": 0,
        }
        state["processed_incident_ids"] = ["INC-T1"]
        mgr._save_state(state)

        # 写 transient_event report.yaml（status="transient_event"）
        report = {
            "severity": "critical",
            "conclusion": "API-injected alert, not a real firing rule.",
            "root_cause": {
                "summary": (
                    "Alert created via PagerDuty Events API injection, "
                    "not a real Prometheus rule firing."
                ),
            },
            "alert_summary": {
                "service": "prometheus",
                "environment": "US",
                "alert_count": 1,
                "status": "transient_event",
            },
            "causal_chain": {"chain": [{
                "node_type": "root_cause",
                "event": "transient API injection",
                "evidence": "first_trigger_log_entry='Triggered through the API.'",
            }]},
            "solutions": {
                "short_term": [],
                "long_term": [],
                "_no_action": True,
            },
        }
        mgr.save_cg_result("CG-T1", report)

        result = run_loop(state_dir, skill_base_dir)

        # 快路径应：跳过 review 派发，直接 complete_cg 并发 Phase 2
        assert result["poll_succeeded"] is True
        assert result["summary"]["investigations_completed"] == 1
        assert result["summary"]["reviews_dispatched"] == 0
        # 不应出现 dispatch_review action
        assert all(a["type"] != "dispatch_review" for a in result["actions"])

        # CG-T1 应已从 active 移到 completed
        state = mgr.load_state()
        assert "CG-T1" not in state["active_correlation_groups"]
        assert "CG-T1" in state["completed_correlation_groups"]

        # 应发过一次飞书通知（Phase 2 - 告警诊断卡片）
        titles = [
            call.kwargs.get("title") or (call.args[2] if len(call.args) > 2 else None)
            for call in mock_send.call_args_list
        ]
        assert any("告警诊断 | CG-T1" in (t or "") for t in titles)

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-04-14T23:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_ongoing_status_still_dispatches_review(
        self, mock_send, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        """Regression guard: non-transient status 仍需派发 review。"""
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-O1"] = {
            "created_at": "2026-04-14T22:14:00Z",
            "incidents": ["INC-O1"],
            "service": "marketing-service",
            "environment": "prod-us",
            "status": "investigating",
            "investigation_agent_id": None,
            "phase1_sent": True,
            "review_dispatched": False,
            "revision_count": 0,
        }
        state["processed_incident_ids"] = ["INC-O1"]
        mgr._save_state(state)

        report = {
            "severity": "critical",
            "conclusion": "Deployment down, OOMKill loop in progress.",
            "root_cause": {
                "summary": (
                    "marketing-service memory limit 4Gi exhausted due to JVM heap "
                    "leak driving OOM + liveness failures."
                ),
            },
            "alert_summary": {
                "service": "marketing-service",
                "environment": "prod-us",
                "alert_count": 1,
                "status": "ongoing",
            },
            "causal_chain": {"chain": [{
                "node_type": "root_cause",
                "event": "memory_limit_exhausted",
                "evidence": "container_memory_working_set hit 4094Mi",
            }]},
            "solutions": {"short_term": [], "long_term": []},
        }
        mgr.save_cg_result("CG-O1", report)

        result = run_loop(state_dir, skill_base_dir)
        # ongoing 必须走常规 review，不可被 fast path 误拦
        assert result["summary"]["reviews_dispatched"] == 1
        assert result["summary"]["investigations_completed"] == 0
        assert any(a["type"] == "dispatch_review" and a["cg_id"] == "CG-O1"
                   for a in result["actions"])


class TestPollFailure:
    """PD API 异常时的行为。"""

    @patch("dispatcher_loop.get_pd_token", side_effect=SystemExit(1))
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_poll_failure_skips_to_step6(
        self, mock_webhook, mock_token, state_dir, skill_base_dir, mgr
    ):
        """test_poll_failure_skips_to_step6 — PD API 异常时无新 CG 但有其他步骤的处理。"""
        # 预设一个 investigating CG 和 report.yaml（review 已派发且已 approved）
        state = mgr.load_state()
        state["active_correlation_groups"]["CG-20"] = {
            "created_at": "2026-03-27T09:30:00Z",
            "incidents": ["INC-B"],
            "service": "grafana",
            "environment": "us-prod",
            "status": "investigating",
            "investigation_agent_id": None,
            "phase1_sent": True,
            "review_dispatched": True,
            "revision_count": 0,
        }
        mgr._save_state(state)

        report = {
            "severity": "high",
            "conclusion": "Test conclusion text.",
            "root_cause": {"summary": "Test root cause summary text."},
            "alert_summary": {"service": "grafana", "environment": "us-prod", "alert_count": 1, "status": "resolved"},
            "causal_chain": {"chain": [{"node_type": "root_cause", "event": "x", "evidence": "y"}]},
            "solutions": {"short_term": [], "long_term": []},
        }
        mgr.save_cg_result("CG-20", report)

        # 写 approved review.yaml
        mgr.save_review_result("CG-20", {
            "cg_id": "CG-20",
            "overall_verdict": "approved",
            "reviewed_at": "2026-03-27T10:00:00Z",
            "root_cause_review": {"checkpoints": ["root cause verified"]},
            "timeline_review": {"checkpoints": ["timeline verified"]},
            "evidence_review": {"checkpoints": ["evidence verified"]},
        })

        with patch("dispatcher_loop.send_elements_card"):
            result = run_loop(state_dir, skill_base_dir)

        # PD 失败
        assert result["poll_succeeded"] is False
        assert any("PagerDuty" in e or "token" in e.lower() for e in result["errors"])

        # 无新 CG
        assert result["summary"]["cgs_created"] == 0

        # 但 Step 6 仍执行：report.yaml + approved review → complete_cg
        assert result["summary"]["investigations_completed"] == 1

    @patch("dispatcher_loop.get_pd_token", side_effect=Exception("Connection refused"))
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_poll_failure_no_last_poll_update(
        self, mock_webhook, mock_token, state_dir, skill_base_dir, mgr
    ):
        """test_poll_failure_no_last_poll_update — PD 失败时 poll_succeeded=False，不更新 last_poll。"""
        # 设置一个初始 last_poll_at
        mgr.update_last_poll("2026-03-27T09:00:00Z")

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is False

        # last_poll_at 不应被更新
        state = mgr.load_state()
        assert state["last_poll_at"] == "2026-03-27T09:00:00Z"


class TestCorrelationFailureFallback:
    """test_correlation_failure_fallback — correlate_incidents 抛异常 → 降级为独立组。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll")
    @patch("dispatcher_loop.correlate_incidents", side_effect=Exception("correlation bug"))
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_correlation_failure_fallback(
        self, mock_send, mock_webhook, mock_correlate, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        inc1 = _make_incident("INC-C1", title="[cn-prod] alert 1")
        inc2 = _make_incident("INC-C2", title="[cn-prod] alert 2")
        mock_poll.return_value = {
            "total_triggered": 2,
            "new_count": 2,
            "new_incidents": [inc1, inc2],
            "last_poll": "2026-03-27T10:05:00Z",
        }

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True

        # 应有 correlation 失败的错误记录
        assert any("correlation" in e.lower() or "fallback" in e.lower() for e in result["errors"])

        # 每个 incident 降级为独立组 → 2 个新 CG
        assert result["summary"]["cgs_created"] == 2

        # 应有 2 个 dispatch_investigation actions
        investigation_actions = [
            a for a in result["actions"] if a["type"] == "dispatch_investigation"
        ]
        assert len(investigation_actions) == 2


class TestResultJsonDetectedUpdatesApproval:
    """test_result_json_detected_updates_approval — result.json 存在时更新 approval status。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0,
        "new_count": 0,
        "new_incidents": [],
        "last_poll": "2026-03-27T10:06:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_result_json_detected_updates_approval(
        self, mock_send, mock_webhook, mock_poll, mock_token,
        state_dir, skill_base_dir, mgr
    ):
        # 预设 completed CG
        state = mgr.load_state()
        state["completed_correlation_groups"]["CG-30"] = {
            "completed_at": "2026-03-27T09:50:00Z",
            "result_path": "investigations/CG-30/report.yaml",
            "incidents": ["INC-D"],
            "service": "redis",
            "environment": "us-prod",
            "solutions_status": {"0": "routed"},
        }
        mgr._save_state(state)

        # 保存 approval (auto_executed 或 approved 状态)
        mgr.save_approval("CG-30", 0, {
            "status": "auto_executed",
            "created_at": "2026-03-27T09:55:00Z",
        })

        # 写 result.json
        exec_dir = os.path.join(state_dir, "executions", "CG-30-solution-0")
        os.makedirs(exec_dir, exist_ok=True)
        result_data = {
            "status": "success",
            "summary": "变更执行成功，服务已恢复正常",
            "verify_still_needed_result": "问题仍存在",
            "steps_executed": ["step1", "step2"],
            "verification_output": "OK",
            "backup_path": "executions/CG-30-solution-0/backup/",
            "rollback_performed": False,
            "error_detail": None,
        }
        with open(os.path.join(exec_dir, "result.json"), "w") as f:
            json.dump(result_data, f)

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True
        assert result["summary"]["executions_completed"] == 1

        # Step 6d 检测到 result.json → 设为 executed_success
        # Step 8 检测到 executed_success → 派发 pattern extraction → 设为 extracting_pattern
        # 因此最终状态是 extracting_pattern（证明 6d 的 executed_success 被 Step 8 消费了）
        approval = mgr.load_approval("CG-30", 0)
        assert approval["status"] == "extracting_pattern"

        # 应有 dispatch_pattern_extraction action
        pattern_actions = [
            a for a in result["actions"] if a["type"] == "dispatch_pattern_extraction"
        ]
        assert len(pattern_actions) == 1
        assert pattern_actions[0]["cg_id"] == "CG-30"
        assert pattern_actions[0]["solution_idx"] == 0


class TestCrashRecovery:
    """Step 5.6: crash recovery detection for stalled investigations."""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0, "new_count": 0, "new_incidents": [], "last_poll": "2026-03-27T10:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_no_checkpoint_timeout_triggers_retry(
        self, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir, mgr
    ):
        """CG investigating for >30min with no checkpoint → retry dispatch."""
        cg_id = mgr.create_cg([{"id": "INC-1", "title": "test"}], "svc", "us-prod")
        mgr.confirm_investigating(cg_id)
        # Backdate investigating_since to 35 min ago
        state = mgr.load_state()
        from datetime import timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["active_correlation_groups"][cg_id]["investigating_since"] = old_time
        mgr._save_state(state)
        # Save incidents.json so prompt build works
        inv_dir = os.path.join(state_dir, "investigations", cg_id)
        os.makedirs(inv_dir, exist_ok=True)
        with open(os.path.join(inv_dir, "incidents.json"), "w") as f:
            json.dump([{"id": "INC-1", "title": "test"}], f)

        result = run_loop(state_dir, skill_base_dir)
        retry_actions = [a for a in result["actions"] if a["type"] == "dispatch_investigation"]
        assert len(retry_actions) >= 1
        assert any(a["cg_id"] == cg_id for a in retry_actions)

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0, "new_count": 0, "new_incidents": [], "last_poll": "2026-03-27T10:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_recent_checkpoint_no_retry(
        self, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir, mgr
    ):
        """CG with recent checkpoint → no retry even if investigating_since is old."""
        cg_id = mgr.create_cg([{"id": "INC-1", "title": "test"}], "svc", "us-prod")
        mgr.confirm_investigating(cg_id)
        state = mgr.load_state()
        from datetime import timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["active_correlation_groups"][cg_id]["investigating_since"] = old_time
        mgr._save_state(state)
        # Write a RECENT checkpoint
        from checkpoint_manager import CheckpointManager
        cp_mgr = CheckpointManager(os.path.join(state_dir, "investigations", cg_id, "progress"))
        cp_mgr.write("02_validate", {"stage": "VALIDATE", "data": {}})

        result = run_loop(state_dir, skill_base_dir)
        # Should NOT have crash recovery dispatch (may have step 5.5 retry since status was reset)
        crash_actions = [a for a in result["actions"]
                        if a["type"] == "dispatch_investigation" and "crash-recovery" in a.get("prompt_file", "")]
        assert len(crash_actions) == 0

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0, "new_count": 0, "new_incidents": [], "last_poll": "2026-03-27T10:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    def test_stale_checkpoint_triggers_retry_with_resume(
        self, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir, mgr
    ):
        """CG with stale checkpoint and no report → retry with resume context."""
        import time as time_mod
        cg_id = mgr.create_cg([{"id": "INC-1", "title": "test"}], "svc", "us-prod")
        mgr.confirm_investigating(cg_id)
        # Write a stale checkpoint
        from checkpoint_manager import CheckpointManager
        cp_mgr = CheckpointManager(os.path.join(state_dir, "investigations", cg_id, "progress"))
        cp_mgr.write("02_validate", {"stage": "VALIDATE", "data": {"status": "ongoing"}})
        # Backdate checkpoint
        stale_time = time_mod.time() - 2000
        cp_path = os.path.join(state_dir, "investigations", cg_id, "progress", "02_validate.json")
        os.utime(cp_path, (stale_time, stale_time))
        # Save incidents.json
        inv_dir = os.path.join(state_dir, "investigations", cg_id)
        with open(os.path.join(inv_dir, "incidents.json"), "w") as f:
            json.dump([{"id": "INC-1", "title": "test"}], f)

        result = run_loop(state_dir, skill_base_dir)
        retry_actions = [a for a in result["actions"] if a["type"] == "dispatch_investigation"]
        assert len(retry_actions) >= 1
        # Check that the prompt file contains crash-recovery suffix
        crash_recovery_actions = [a for a in retry_actions if "crash-recovery" in a.get("prompt_file", "")]
        assert len(crash_recovery_actions) >= 1


class TestRecurrenceHandling:
    """Recurrence CGs: send notification, skip investigation."""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll")
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_recurrence_skips_investigation(
        self, mock_send, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir
    ):
        """Recurrence group → CG created + completed, no investigation dispatch."""
        mock_poll.return_value = {
            "total_triggered": 1, "new_count": 1,
            "new_incidents": [{
                "id": "REC001",
                "title": "[cn-prod] thanos-query OOMKilled",
                "created_at": "2026-03-27T12:00:00Z",
                "service": {"id": "P1", "summary": "grafana-ai"},
                "urgency": "high",
                "html_url": "https://pd/REC001",
                "incident_number": 99999,
                "status": "triggered",
                "alerts": [],
            }],
            "last_poll": "2026-03-27T12:00:00Z",
        }

        # Pre-populate a completed CG that this will be a recurrence of
        mgr = StateManager(state_dir)
        # Manually create a completed CG
        state = mgr.load_state()
        state["completed_correlation_groups"]["CG-OLD"] = {
            "completed_at": "2026-03-27T11:00:00Z",
            "title": "[cn-prod] thanos-query OOMKilled",
            "service": "grafana-ai",
            "incidents": [{"id": "OLD001", "title": "[cn-prod] thanos-query OOMKilled"}],
            "result_path": "investigations/CG-OLD/report.yaml",
            "environment": "cn-prod",
            "solutions_status": {},
        }
        mgr._save_state(state)

        with patch("dispatcher_loop.correlate_incidents") as mock_correlate:
            mock_correlate.return_value = [{
                "incidents": [{
                    "id": "REC001",
                    "title": "[cn-prod] thanos-query OOMKilled",
                    "created_at": "2026-03-27T12:00:00Z",
                    "service": {"id": "P1", "summary": "grafana-ai"},
                    "urgency": "high",
                    "html_url": "https://pd/REC001",
                    "incident_number": 99999,
                    "status": "triggered",
                    "alerts": [],
                }],
                "service": "grafana-ai",
                "correlates_to": None,
                "is_recurrence": True,
                "recurrence_of": "CG-OLD",
                "fault_entity": "thanos-query",
                "service_entity": "grafana-ai",
            }]
            result = run_loop(state_dir, skill_base_dir)

        # Should have created a CG
        assert result["summary"]["cgs_created"] >= 1

        # The recurrence CG should NOT have an investigation dispatch
        investigation_actions = [a for a in result["actions"] if a["type"] == "dispatch_investigation"]
        assert len(investigation_actions) == 0

        # Recurrence CG should be completed immediately
        final_state = mgr.load_state()
        recurrence_cg_ids = [
            cg_id for cg_id, cg in final_state["completed_correlation_groups"].items()
            if cg_id != "CG-OLD"
        ]
        assert len(recurrence_cg_ids) >= 1

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll")
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop.send_elements_card")
    def test_non_recurrence_dispatches_investigation(
        self, mock_send, mock_webhook, mock_poll, mock_token, state_dir, skill_base_dir
    ):
        """Non-recurrence group → normal Phase 1 notification + investigation dispatch."""
        mock_poll.return_value = {
            "total_triggered": 1, "new_count": 1,
            "new_incidents": [_make_incident("INC-NR1", title="[cn-prod] new alert")],
            "last_poll": "2026-03-27T12:00:00Z",
        }

        with patch("dispatcher_loop.correlate_incidents") as mock_correlate:
            mock_correlate.return_value = [{
                "incidents": [_make_incident("INC-NR1", title="[cn-prod] new alert")],
                "service": "test-svc",
                "correlates_to": None,
                "is_recurrence": False,
                "recurrence_of": None,
                "fault_entity": None,
                "service_entity": None,
            }]
            result = run_loop(state_dir, skill_base_dir)

        assert result["summary"]["cgs_created"] == 1
        investigation_actions = [a for a in result["actions"] if a["type"] == "dispatch_investigation"]
        assert len(investigation_actions) == 1


class TestExclusiveSiblingsAutoCancellation:
    """批准一个 exclusive 方案 → 同组 pending sibling 自动变 exclusive_cancelled 且不派发执行。"""

    @patch("dispatcher_loop.get_pd_token", return_value="fake-token")
    @patch("dispatcher_loop.oncall_poll", return_value={
        "total_triggered": 0, "new_count": 0, "new_incidents": [],
        "last_poll": "2026-04-15T10:00:00Z",
    })
    @patch("dispatcher_loop.get_feishu_webhook", return_value=("https://hook", "secret"))
    @patch("dispatcher_loop._send_feishu_notification", return_value=None)
    @patch("dispatcher_loop.cancel_instance", return_value={"code": 0})
    @patch("dispatcher_loop.get_instance_status")
    @patch("dispatcher_loop.get_approval_token", return_value="approval-tok")
    def test_approved_exclusive_cancels_sibling_and_blocks_dispatch(
        self, mock_tok, mock_status, mock_cancel, mock_notify,
        mock_webhook, mock_poll, mock_pd,
        state_dir, skill_base_dir, mgr, monkeypatch,
    ):
        # 只有 ST-0 在飞书侧变成 APPROVED; ST-1 仍 PENDING
        mock_status.side_effect = lambda tok, code: (
            "APPROVED" if code == "INST-0" else "PENDING"
        )
        monkeypatch.setenv("FEISHU_APPROVER_OPEN_ID", "ou_submitter")

        cg_id = "CG-EX"

        # 预设 completed CG + 两条 pending approval
        state = mgr.load_state()
        state["completed_correlation_groups"][cg_id] = {
            "completed_at": "2026-04-15T09:00:00Z",
            "incidents": ["INC-EX"],
            "service": "thanos",
            "environment": "cn-prod",
            "result_path": f"investigations/{cg_id}/report.yaml",
            "solutions_status": {
                "0": "pending_approval",
                "1": "pending_approval",
            },
        }
        mgr._save_state(state)

        def _sol(title):
            return {
                "title": title,
                "action": "mock action",
                "addresses": "causal_chain[0]",
                "expected_effect": "...",
                "risk": "high",
                "reversible": False,
                "blast_radius": "single pod",
                "verify_cmd": "kubectl get pods",
                "prompt": "mock execution prompt",
                "relation": {"mode": "exclusive", "group": "g1"},
            }

        cg_result = {
            "correlation_group": cg_id,
            "severity": "high",
            "conclusion": "...",
            "root_cause": {"summary": "..."},
            "alert_summary": {
                "service": "thanos", "environment": "cn-prod",
                "alert_count": 1, "status": "resolved",
            },
            "causal_chain": {"chain": []},
            "solutions": {
                "short_term": [_sol("续费"), _sol("弃用")],
                "long_term": [],
            },
        }
        mgr.save_cg_result(cg_id, cg_result)

        mgr.save_approval(cg_id, 0, {
            "status": "pending_approval",
            "instance_code": "INST-0",
            "approver_open_id": "ou_approver",
            "created_at": "2026-04-15T09:30:00Z",
        })
        mgr.save_approval(cg_id, 1, {
            "status": "pending_approval",
            "instance_code": "INST-1",
            "approver_open_id": "ou_approver",
            "created_at": "2026-04-15T09:30:00Z",
        })

        result = run_loop(state_dir, skill_base_dir)

        assert result["poll_succeeded"] is True

        # ST-0 批准成功, ST-1 被自动互斥撤销
        assert mgr.load_approval(cg_id, 0)["status"] == "approved"
        assert mgr.load_approval(cg_id, 1)["status"] == "exclusive_cancelled"

        # cancel_instance 应被调用, 且仅针对 ST-1 的 instance_code
        mock_cancel.assert_called_once_with("approval-tok", "INST-1", "ou_submitter")

        # 核心断言: 仅为 ST-0 派发 execution, ST-1 永远不会被派发
        exec_actions = [
            a for a in result["actions"] if a["type"] == "dispatch_execution"
        ]
        assert len(exec_actions) == 1
        assert exec_actions[0]["cg_id"] == cg_id
        assert exec_actions[0]["solution_idx"] == 0
