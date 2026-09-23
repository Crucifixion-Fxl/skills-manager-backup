#!/usr/bin/env python3
"""Phase 2 card rendering tests — validates _build_phase2_elements output structure."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher_loop import _build_phase2_elements


def _make_full_report():
    """构造含全部字段的 report fixture。"""
    return {
        "correlation_group": "CG-52",
        "title": "美国 error warn alert - marketing-service AB solutionId missing",
        "severity": "low",
        "investigated_at": "2026-03-30T10:33:53Z",
        "alert_summary": {
            "service": "marketing-service",
            "environment": "us-prod",
            "alert_count": 1,
            "triggered_at": "2026-03-30T10:12:19Z",
            "pagerduty_url": "https://addx-oncall.pagerduty.com/incidents/Q2W183",
            "duration": "~4min",
            "status": "self_healed",
        },
        "timeline": [
            {"time": "09:35", "event": "Grafana alert 首次进入 pending", "source": "Grafana"},
            {"time": "10:12", "event": "pending→alerting，触发 PD #443457", "source": "PagerDuty"},
            {"time": "10:16", "event": "自动恢复 ok，告警自愈", "source": "Grafana"},
        ],
        "trend_data": [
            {"metric": "ERROR日志数", "window": "2h", "baseline": "0", "current": "471", "change": "间歇爆发"},
        ],
        "causal_chain": {
            "chain": [
                {"node_type": "root_cause", "event": "GrowthBook AB test missing fallback"},
                {"node_type": "symptom", "event": "ERROR logs exceed threshold"},
                {"node_type": "impact", "event": "部分用户无促销展示"},
            ],
            "evaluation": {
                "complete": True,
                "criteria": [
                    {"id": "①", "name": "可行动", "passed": True, "detail": "配置 fallback"},
                ],
            },
        },
        "impact": {
            "user_facing": "部分用户无促销展示",
            "scope": "marketing-service prod-us",
            "duration": "~4min",
        },
        "risk_assessment": {
            "current_risk": "低",
            "recurrence": "高",
            "urgency": "低",
        },
        "solutions": {
            "short_term": [
                {"title": "配置 fallback", "action": "在 GrowthBook 添加 default variation",
                 "risk": "low", "expected_effect": "消除 ERROR 日志"},
            ],
            "long_term": [
                {"title": "降级日志级别", "action": "ERROR → WARN", "expected_effect": "避免非故障触发"},
            ],
        },
        "dimensions_not_available": [
            {"dimension": "sentry", "reason": "未接入"},
        ],
    }


def _get_elements_by_tag(elements, tag):
    return [e for e in elements if e.get("tag") == tag]


def _get_all_markdown(elements):
    texts = []
    for e in elements:
        if e.get("tag") == "markdown":
            texts.append(e.get("content", ""))
        elif e.get("tag") == "column_set":
            for col in e.get("columns", []):
                for el in col.get("elements", []):
                    if el.get("tag") == "markdown":
                        texts.append(el.get("content", ""))
        elif e.get("tag") == "collapsible_panel":
            texts.extend(_get_all_markdown(e.get("elements", [])))
    return "\n".join(texts)


class TestPhase2Structure:
    def test_has_alert_info_table(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        tables = _get_elements_by_tag(elements, "table")
        assert len(tables) >= 1
        # 第一个 table 是告警信息
        alert_table = tables[0]
        row_keys = [r["key"] for r in alert_table["rows"]]
        assert "告警标题" in row_keys
        assert "服务" in row_keys

    def test_alert_title_from_report(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        tables = _get_elements_by_tag(elements, "table")
        alert_table = tables[0]
        title_row = [r for r in alert_table["rows"] if r["key"] == "告警标题"][0]
        assert "marketing-service" in title_row["value"]

    def test_has_timeline_table(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "时间线" in all_md
        tables = _get_elements_by_tag(elements, "table")
        # 第二个 table 是时间线
        tl_table = tables[1]
        col_names = [c["name"] for c in tl_table["columns"]]
        assert "time" in col_names
        assert "event" in col_names

    def test_timeline_adds_day_prefix(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        tables = _get_elements_by_tag(elements, "table")
        tl_table = tables[1]
        # triggered_at 是 2026-03-30，所以 day=30
        assert "30日" in tl_table["rows"][0]["time"]

    def test_no_timeline_when_empty(self):
        report = _make_full_report()
        report["timeline"] = []
        elements = _build_phase2_elements("CG-52", report)
        all_md = _get_all_markdown(elements)
        assert "时间线" not in all_md

    def test_has_trend_data_table(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "趋势数据" in all_md

    def test_has_causal_chain_with_color_labels(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "因果链" in all_md
        assert "green" in all_md  # 完整 = green 着色
        assert "[根因]" in all_md
        assert 'color="red"' in all_md

    def test_has_impact_column_set(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "影响范围" in all_md
        assert "用户影响" in all_md

    def test_has_risk_assessment(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "风险评估" in all_md
        assert "当前风险" in all_md
        assert "🟢" in all_md

    def test_has_solution_collapsible_panels(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        panels = _get_elements_by_tag(elements, "collapsible_panel")
        assert len(panels) >= 2
        # 短期 = red border, 长期 = blue border
        borders = [p.get("border", {}).get("color") for p in panels]
        assert "red" in borders
        assert "blue" in borders

    def test_solution_details_in_collapsible(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        panels = _get_elements_by_tag(elements, "collapsible_panel")
        st_panel = [p for p in panels if p.get("border", {}).get("color") == "red"][0]
        st_md = _get_all_markdown(st_panel.get("elements", []))
        assert "配置 fallback" in st_md
        assert "操作" in st_md

    def test_has_notes_section(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "补充说明" in all_md
        assert "sentry" in all_md

    def test_has_pagerduty_link(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "查看 PagerDuty" in all_md
        assert "pagerduty.com" in all_md

    def test_no_action_tag(self):
        """Card 2.0 不支持 action 标签。"""
        elements = _build_phase2_elements("CG-52", _make_full_report())
        action_elements = _get_elements_by_tag(elements, "action")
        assert len(action_elements) == 0

    def test_has_hr_separators(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        hrs = _get_elements_by_tag(elements, "hr")
        assert len(hrs) >= 5  # 至少 5 个分割线

    def test_incomplete_chain_shows_warning(self):
        report = _make_full_report()
        report["causal_chain"]["evaluation"] = {
            "complete": False,
            "criteria": [{"id": "①", "name": "可行动", "passed": False, "detail": "无动作"}],
            "incomplete_reason": "链首为 symptom",
            "next_investigation_hint": "检查 resource limits",
        }
        elements = _build_phase2_elements("CG-52", report)
        all_md = _get_all_markdown(elements)
        assert "⚠️" in all_md
        assert "不完整" in all_md
        assert "可行动" in all_md

    def test_duration_and_status_in_alert_table(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        tables = _get_elements_by_tag(elements, "table")
        alert_table = tables[0]
        status_row = [r for r in alert_table["rows"] if r["key"] == "持续/状态"][0]
        assert "~4min" in status_row["value"]
        assert "已自愈" in status_row["value"]

    def test_solution_title_in_header(self):
        elements = _build_phase2_elements("CG-52", _make_full_report())
        all_md = _get_all_markdown(elements)
        assert "解决方案" in all_md
