#!/usr/bin/env python3
"""report_builder.py unit tests."""

import os
import sys
import tempfile
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_builder import (
    ReportBuilder,
    SOLUTION_SCHEMA,
    get_required_solution_fields,
    get_optional_solution_fields,
)


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_valid_builder(output_dir):
    """Helper: create a ReportBuilder with all required fields filled."""
    r = ReportBuilder(cg_id="CG-1", output_dir=output_dir)
    r.set_metadata(
        title="Test alert investigation",
        severity="medium",
        environment="us-prod",
        service="grafana",
    )
    r.set_root_cause(
        summary="A" * 50,  # min 50 chars
        category="configuration",
    )
    r.add_chain_node(
        node_type="root_cause",
        event="Something happened that caused the issue",
        evidence="B" * 20,  # min 20 chars
    )
    r.set_conclusion("This was caused by a configuration error and has self-healed.")
    return r


class TestRequiredFields:
    def test_save_fails_without_metadata(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="root_cause", event="test", evidence="B" * 20)
        r.set_conclusion("test conclusion")
        assert r.save() is False

    def test_save_fails_without_root_cause(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.add_chain_node(node_type="root_cause", event="test", evidence="B" * 20)
        r.set_conclusion("test conclusion")
        assert r.save() is False

    def test_save_fails_without_chain_nodes(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.set_conclusion("test conclusion")
        assert r.save() is False

    def test_save_fails_without_conclusion(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="root_cause", event="test", evidence="B" * 20)
        assert r.save() is False

    def test_save_succeeds_with_all_required(self, output_dir):
        r = _make_valid_builder(output_dir)
        assert r.save() is True
        assert os.path.isfile(os.path.join(output_dir, "report.yaml"))


class TestEnumValidation:
    def test_invalid_severity(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        with pytest.raises(ValueError, match="severity"):
            r.set_metadata(title="t", severity="INVALID", environment="us", service="s")

    def test_invalid_node_type(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        with pytest.raises(ValueError, match="node_type"):
            r.add_chain_node(node_type="INVALID", event="test", evidence="B" * 20)

    def test_invalid_solution_term(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        with pytest.raises(ValueError, match="term"):
            r.add_solution(
                term="INVALID",
                title="t", action="a", addresses="causal_chain[0]",
                expected_effect="e", risk="low", reversible=True,
                blast_radius="1 pod", verify_cmd="c", prompt="p",
            )

    def test_invalid_solution_risk(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        with pytest.raises(ValueError, match="risk"):
            r.add_solution(
                term="short",
                title="t", action="a", addresses="causal_chain[0]",
                expected_effect="e", risk="INVALID", reversible=True,
                blast_radius="1 pod", verify_cmd="c", prompt="p",
            )


class TestContentMinimums:
    def test_root_cause_summary_too_short(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.set_root_cause(summary="short", category="configuration")
        assert r.save() is False

    def test_evidence_too_short(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="root_cause", event="test", evidence="short")
        r.set_conclusion("test conclusion")
        assert r.save() is False

    def test_must_have_root_cause_node(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="symptom", event="test", evidence="B" * 20)
        r.set_conclusion("test conclusion")
        assert r.save() is False


class TestTimelineOrdering:
    """Timeline 必须严格升序：LLM 常见错误是追加顺序写反，应在 save() 就拦住。"""

    def test_ascending_timeline_passes(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_timeline_event(time="2026-04-14T22:10:00Z", event="first", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:15:00Z", event="second", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:20:00Z", event="third", source="k8s")
        assert r.save() is True

    def test_descending_timeline_fails(self, output_dir, capsys):
        r = _make_valid_builder(output_dir)
        r.add_timeline_event(time="2026-04-14T22:20:00Z", event="first_wrong", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:15:00Z", event="second_earlier", source="k8s")
        assert r.save() is False
        captured = capsys.readouterr()
        assert "timeline[1] not in ascending order" in captured.out

    def test_out_of_order_middle_event_fails(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_timeline_event(time="2026-04-14T22:10:00Z", event="first", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:20:00Z", event="second", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:15:00Z", event="third_wrong", source="k8s")
        assert r.save() is False

    def test_timeline_with_Z_and_offset_suffix_parses(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_timeline_event(time="2026-04-14T22:10:00Z", event="first", source="k8s")
        r.add_timeline_event(time="2026-04-14T22:15:00+00:00", event="second", source="k8s")
        assert r.save() is True

    def test_unparsable_timestamps_skipped(self, output_dir):
        """不能解析的时间戳跳过，不影响校验（旧 report 兼容）。"""
        r = _make_valid_builder(output_dir)
        r.add_timeline_event(time="unknown", event="first", source="k8s")
        r.add_timeline_event(time="still-unknown", event="second", source="k8s")
        assert r.save() is True


class TestCausalChainOrdering:
    """Causal chain: caused_by 引用的父节点 timestamp 必须 ≤ 当前节点 timestamp。"""

    def test_valid_causal_ordering_passes(self, output_dir):
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(
            node_type="root_cause", event="cause", evidence="B" * 20,
            timestamp="2026-04-14T22:10:00Z",
        )
        r.add_chain_node(
            node_type="symptom", event="effect", evidence="C" * 20,
            timestamp="2026-04-14T22:15:00Z", caused_by=[0],
        )
        r.set_conclusion("conclusion text here")
        assert r.save() is True

    def test_parent_later_than_child_fails(self, output_dir, capsys):
        """父节点时间晚于子节点 → 反向因果，应拒绝。"""
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        # 父节点 [0] 时间戳 22:22 晚于子节点 [1] 的 22:15 — CG-1 T2 回归场景
        r.add_chain_node(
            node_type="contributing_factor", event="late cause",
            evidence="B" * 20, timestamp="2026-04-14T22:22:45Z",
        )
        r.add_chain_node(
            node_type="root_cause", event="earlier event",
            evidence="C" * 20, timestamp="2026-04-14T22:15:00Z", caused_by=[0],
        )
        r.set_conclusion("conclusion text here")
        assert r.save() is False
        captured = capsys.readouterr()
        assert "causal_chain[1].caused_by[0]" in captured.out
        assert "cause must precede effect" in captured.out

    def test_multiple_parents_all_checked(self, output_dir, capsys):
        """caused_by 可能有多个父节点，任一个时间晚于子节点都应拒绝。"""
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(
            node_type="root_cause", event="legit early cause",
            evidence="B" * 20, timestamp="2026-04-14T22:10:00Z",
        )
        r.add_chain_node(
            node_type="contributing_factor", event="late cause (bad)",
            evidence="C" * 20, timestamp="2026-04-14T22:30:00Z",
        )
        r.add_chain_node(
            node_type="symptom", event="child",
            evidence="D" * 20, timestamp="2026-04-14T22:20:00Z", caused_by=[0, 1],
        )
        r.set_conclusion("conclusion text here")
        assert r.save() is False
        captured = capsys.readouterr()
        # 应报告 parent [1] 晚于 child，不报告 [0]（[0] 时间更早，合法）
        assert "caused_by[1]" in captured.out

    def test_node_without_timestamp_skipped(self, output_dir):
        """没有 timestamp 的节点无法校验，跳过。"""
        r = ReportBuilder("CG-1", output_dir)
        r.set_metadata(title="t", severity="medium", environment="us", service="svc")
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="root_cause", event="cause", evidence="B" * 20)
        r.add_chain_node(node_type="symptom", event="effect", evidence="C" * 20, caused_by=[0])
        r.set_conclusion("conclusion text here")
        assert r.save() is True


class TestOutputStructure:
    def test_yaml_structure_matches_dispatcher_expectations(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_solution(
            term="short",
            title="Fix the config",
            action="Change setting X to Y",
            addresses="causal_chain[0]",
            expected_effect="Config applied,health check 返回 200",
            risk="low",
            reversible=True,
            blast_radius="single deployment,1 pod rolling restart",
            verify_cmd="curl http://example.com/health",
            prompt="kubectl apply ...",
        )
        r.add_solution(
            term="long",
            title="Refactor the module",
            action="Rewrite module Z",
            addresses="causal_chain[0]",
            expected_effect="Permanent fix via module refactor",
        )
        r.set_impact(user_facing="No user impact", scope="single pod", duration="2 min")
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        # Structure checks matching _build_phase2_elements() expectations
        assert report["correlation_group"] == "CG-1"
        assert report["severity"] == "medium"
        assert "investigated_at" in report

        # alert_summary
        assert report["alert_summary"]["service"] == "grafana"
        assert report["alert_summary"]["environment"] == "us-prod"

        # root_cause
        assert len(report["root_cause"]["summary"]) >= 50
        assert report["root_cause"]["category"] == "configuration"

        # causal_chain — dict with "chain" key
        assert isinstance(report["causal_chain"], dict)
        assert "chain" in report["causal_chain"]
        chain = report["causal_chain"]["chain"]
        assert len(chain) >= 1
        assert chain[0]["node_type"] == "root_cause"
        assert "event" in chain[0]
        assert "evidence" in chain[0]

        # solutions
        assert len(report["solutions"]["short_term"]) == 1
        assert report["solutions"]["short_term"][0]["title"] == "Fix the config"
        assert report["solutions"]["short_term"][0]["risk"] == "low"
        assert len(report["solutions"]["long_term"]) == 1

        # impact
        assert report["impact"]["user_facing"] == "No user impact"

        # conclusion
        assert len(report["conclusion"]) > 0

    def test_no_action_marker_when_no_solutions(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert report["solutions"]["_no_action"] is True
        assert report["solutions"]["short_term"] == []

    def test_dimensions_not_available(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_dimension_not_available("sentry", "API key not configured")
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert len(report["dimensions_not_available"]) == 1
        assert report["dimensions_not_available"][0]["dimension"] == "sentry"


class TestNewFields:
    def test_timeline_in_output(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_timeline_event("2024-01-01T00:00:00Z", "Alert fired", "PagerDuty")
        r.add_timeline_event("2024-01-01T00:05:00Z", "Pod restarted", "Kubernetes")
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert len(report["timeline"]) == 2
        assert report["timeline"][0]["time"] == "2024-01-01T00:00:00Z"
        assert report["timeline"][0]["event"] == "Alert fired"
        assert report["timeline"][0]["source"] == "PagerDuty"
        assert report["timeline"][1]["source"] == "Kubernetes"

    def test_trend_data_in_output(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_trend_data(
            metric="error_rate",
            window="1h",
            baseline=0.01,
            current=0.15,
            change="+1400%",
        )
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert len(report["trend_data"]) == 1
        assert report["trend_data"][0]["metric"] == "error_rate"
        assert report["trend_data"][0]["window"] == "1h"
        assert report["trend_data"][0]["baseline"] == 0.01
        assert report["trend_data"][0]["current"] == 0.15
        assert report["trend_data"][0]["change"] == "+1400%"

    def test_risk_assessment_in_output(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.set_risk_assessment(
            current_risk="high",
            recurrence="likely",
            urgency="immediate",
        )
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert report["risk_assessment"]["current_risk"] == "high"
        assert report["risk_assessment"]["recurrence"] == "likely"
        assert report["risk_assessment"]["urgency"] == "immediate"

    def test_alert_summary_full_fields(self, output_dir):
        r = ReportBuilder(cg_id="CG-2", output_dir=output_dir)
        r.set_metadata(
            title="Full metadata test",
            severity="high",
            environment="eu-prod",
            service="api-gateway",
            alert_count=3,
            triggered_at="2024-01-01T00:00:00Z",
            pagerduty_url="https://pagerduty.com/incidents/ABC123",
        )
        r.set_root_cause(summary="A" * 50, category="configuration")
        r.add_chain_node(node_type="root_cause", event="test event", evidence="B" * 20)
        r.set_conclusion("Test conclusion.")
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert report["alert_summary"]["alert_count"] == 3
        assert report["alert_summary"]["triggered_at"] == "2024-01-01T00:00:00Z"
        assert report["alert_summary"]["pagerduty_url"] == "https://pagerduty.com/incidents/ABC123"

    def test_empty_optional_fields_have_defaults(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert report["timeline"] == []
        assert report["trend_data"] == []
        assert "risk_assessment" not in report

    def test_evaluation_in_output(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.set_evaluation(
            complete=True,
            criteria=["logs checked", "metrics reviewed", "config audited", "timeline built"],
        )
        r.save()

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        eval_data = report["causal_chain"]["evaluation"]
        assert eval_data["complete"] is True
        assert len(eval_data["criteria"]) == 4
        assert eval_data["criteria"][0]["detail"] == "logs checked"

    def test_evaluation_incomplete_requires_reason(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.set_evaluation(complete=False, criteria=["logs checked"])
        assert r.save() is False

    def test_evaluation_incomplete_with_reason(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.set_evaluation(
            complete=False,
            criteria=["logs checked"],
            incomplete_reason="Could not access database logs",
            next_investigation_hint="Check DB audit logs for slow queries",
        )
        r.add_dimension_not_available("database_logs", "Access denied")
        assert r.save() is True

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        eval_data = report["causal_chain"]["evaluation"]
        assert eval_data["complete"] is False
        assert eval_data["incomplete_reason"] == "Could not access database logs"
        assert eval_data["next_investigation_hint"] == "Check DB audit logs for slow queries"


class TestDeepDiveEnforcement:
    """DEEP_DIVE 强制检查点测试。"""

    def test_incomplete_without_deep_dive_rejected(self, output_dir):
        """complete=false + 无 depth_limit + 无 dims_unavailable → save() 返回 False"""
        r = _make_valid_builder(output_dir)
        r.set_evaluation(
            complete=False,
            criteria=["logs checked"],
            incomplete_reason="Chain is incomplete",
        )
        assert r.save() is False

    def test_incomplete_with_depth_limit_accepted(self, output_dir):
        """complete=false + depth_limit_reached=true → save() 成功"""
        r = _make_valid_builder(output_dir)
        r.set_evaluation(
            complete=False,
            criteria=["logs checked"],
            incomplete_reason="Could not find root cause after 3 rounds",
        )
        r.set_depth_limit_reached(round_count=3)
        assert r.save() is True

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        eval_data = report["causal_chain"]["evaluation"]
        assert eval_data["depth_limit_reached"] is True
        assert eval_data["investigation_rounds"] == 3

    def test_incomplete_with_dims_unavailable_accepted(self, output_dir):
        """complete=false + dims_unavailable 非空 → save() 成功"""
        r = _make_valid_builder(output_dir)
        r.set_evaluation(
            complete=False,
            criteria=["logs checked"],
            incomplete_reason="GCP logs not accessible",
        )
        r.add_dimension_not_available("gcp_vertex_ai", "API key not configured")
        assert r.save() is True

        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)

        assert len(report["dimensions_not_available"]) == 1
        assert report["dimensions_not_available"][0]["dimension"] == "gcp_vertex_ai"


# ─────────────────────────────────────────────────────────────────────────
# SOLUTION_SCHEMA 守门员测试
#
# 本类守护 ST/LT 字段规划的单一事实来源,防止任何字段在
# schema/builder/prompt/dispatcher 四处再次 drift。
#
# 如果这些测试因为 SOLUTION_SCHEMA 变更而失败,同时检查:
#   - references/schemas/incident-report-schema.md 的字段表
#   - references/investigation-prompt-template.md 的 FINALIZE 示例
#   - dispatcher_loop._build_approval_detail() 的渲染逻辑
#   - completion_gate.validate_report() 的字段列表
# ─────────────────────────────────────────────────────────────────────────
class TestSolutionSchema:
    """守护 SOLUTION_SCHEMA 的稳定性和正确性。"""

    EXPECTED_FIELDS = {
        # name: (required_short, required_long)
        "title":           (True,  True),
        "action":          (True,  True),
        "addresses":       (True,  True),
        "expected_effect": (True,  True),
        "risk":            (True,  False),
        "reversible":      (True,  False),
        "blast_radius":    (True,  False),
        "verify_cmd":      (True,  False),
        "prompt":          (True,  False),
        "relation":        (False, False),
    }

    def test_schema_has_exactly_expected_fields(self):
        """禁止字段悄悄增减。新增字段需同步更新本测试 + 所有消费方。"""
        actual = set(SOLUTION_SCHEMA.keys())
        expected = set(self.EXPECTED_FIELDS.keys())
        assert actual == expected, (
            f"SOLUTION_SCHEMA fields drift: "
            f"added={actual - expected}, removed={expected - actual}"
        )

    def test_schema_required_matrix(self):
        for name, (short_req, long_req) in self.EXPECTED_FIELDS.items():
            spec = SOLUTION_SCHEMA[name]
            assert spec[0] == short_req, f"{name}: short required mismatch"
            assert spec[1] == long_req, f"{name}: long required mismatch"

    def test_required_helpers(self):
        short_required = get_required_solution_fields("short")
        assert set(short_required) == {
            "title", "action", "addresses", "expected_effect",
            "risk", "reversible", "blast_radius", "verify_cmd", "prompt",
        }
        long_required = get_required_solution_fields("long")
        assert set(long_required) == {"title", "action", "addresses", "expected_effect"}

    def test_optional_helpers(self):
        short_optional = get_optional_solution_fields("short")
        assert "relation" in short_optional


class TestAddSolutionValidation:
    """add_solution() 字段校验行为测试。"""

    def _complete_short_kwargs(self, **overrides):
        base = {
            "term": "short",
            "title": "Renew domain",
            "action": "Login GoDaddy and renew",
            "addresses": "causal_chain[0]",
            "expected_effect": "domain_expiry_days jumps to ~365",
            "risk": "low",
            "reversible": False,
            "blast_radius": "single domain, no infra change",
            "verify_cmd": "whois dzeesja.com",
            "prompt": "Step 1: ...",
        }
        base.update(overrides)
        return base

    def test_complete_short_solution_accepted(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_solution(**self._complete_short_kwargs())
        assert r.save() is True

    def test_missing_required_short_field_rejected(self, output_dir):
        r = _make_valid_builder(output_dir)
        kwargs = self._complete_short_kwargs()
        del kwargs["reversible"]
        with pytest.raises(ValueError, match="missing required fields.*reversible"):
            r.add_solution(**kwargs)

    def test_missing_blast_radius_raises(self, output_dir):
        r = _make_valid_builder(output_dir)
        with pytest.raises(ValueError, match="missing required fields"):
            # blast_radius 在 add_solution 签名中是 default None
            r.add_solution(
                term="short",
                title="t", action="a", addresses="causal_chain[0]",
                expected_effect="e", risk="low", reversible=True,
                verify_cmd="c", prompt="p",
                # blast_radius 未传
            )

    def test_reversible_must_be_bool(self, output_dir):
        r = _make_valid_builder(output_dir)
        with pytest.raises(ValueError, match="reversible must be bool"):
            r.add_solution(**self._complete_short_kwargs(reversible="yes"))

    def test_relation_default_is_parallel(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_solution(**self._complete_short_kwargs())
        r.save()
        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)
        sol = report["solutions"]["short_term"][0]
        assert sol["relation"]["mode"] == "parallel"

    def test_relation_exclusive_requires_group(self, output_dir):
        r = _make_valid_builder(output_dir)
        with pytest.raises(ValueError, match="exclusive.*requires.*group"):
            r.add_solution(**self._complete_short_kwargs(
                relation={"mode": "exclusive"}
            ))

    def test_relation_exclusive_with_group_accepted(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_solution(**self._complete_short_kwargs(
            title="Scale horizontally",
            relation={"mode": "exclusive", "group": "scaling-fix"},
        ))
        r.add_solution(**self._complete_short_kwargs(
            title="Scale vertically",
            relation={"mode": "exclusive", "group": "scaling-fix"},
        ))
        assert r.save() is True
        with open(os.path.join(output_dir, "report.yaml")) as f:
            report = yaml.safe_load(f)
        sols = report["solutions"]["short_term"]
        assert sols[0]["relation"]["group"] == "scaling-fix"
        assert sols[1]["relation"]["group"] == "scaling-fix"

    def test_invalid_relation_mode(self, output_dir):
        r = _make_valid_builder(output_dir)
        with pytest.raises(ValueError, match="relation.mode"):
            r.add_solution(**self._complete_short_kwargs(
                relation={"mode": "bogus", "group": "x"}
            ))

    def test_long_term_minimal_fields(self, output_dir):
        r = _make_valid_builder(output_dir)
        r.add_solution(
            term="long",
            title="Enable auto-renew",
            action="Configure auto-renew for all critical domains",
            addresses="causal_chain[0]",
            expected_effect="Eliminates manual renewal toil",
        )
        assert r.save() is True

    def test_long_term_missing_expected_effect(self, output_dir):
        r = _make_valid_builder(output_dir)
        with pytest.raises(ValueError, match="missing required fields"):
            r.add_solution(
                term="long",
                title="Enable auto-renew",
                action="Configure auto-renew",
                addresses="causal_chain[0]",
                # expected_effect 未传,但 add_solution 签名中也是 required kwarg
                # 这里 expected_effect=None 会通过 _complete 校验拦截
                expected_effect=None,
            )
