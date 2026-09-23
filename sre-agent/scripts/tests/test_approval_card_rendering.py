#!/usr/bin/env python3
"""Approval card rendering tests — validates ST-centric table layout of
_build_approval_notify_elements() + _build_approval_detail()."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher_loop import (
    _build_approval_notify_elements,
    _build_approval_detail,
    _find_exclusive_siblings,
)


def _make_solution(**overrides):
    base = {
        "title": "续费域名 dzeesja.com",
        "action": "通过 GoDaddy 控制台续费 dzeesja.com 一年",
        "addresses": "causal_chain[0]",
        "expected_effect": "domain_expiry_days 跳升至 ~365",
        "risk": "high",
        "reversible": False,
        "blast_radius": "单域名,无云资源变更",
        "verify_cmd": "dig dzeesja.com +short",
        "prompt": "Step 1: 登录 GoDaddy\nStep 2: 找到 dzeesja.com\nStep 3: 点 Renew\nStep 4: 验证",
        "relation": {"mode": "parallel"},
    }
    base.update(overrides)
    return base


def _make_cg_result(**overrides):
    base = {
        "correlation_group": "CG-1",
        "title": "[US][Prod][critical][domain_dzeesja.com] domain-expiring",
        "severity": "critical",
        "alert_summary": {
            "service": "prometheus",
            "environment": "US",
            "status": "ongoing",
            "triggered_at": "2026-04-14T03:00:00Z",
            "pagerduty_url": "https://addx-oncall.pagerduty.com/incidents/Q443969",
        },
        "root_cause": {
            "summary": "域名 dzeesja.com 未完成续费,即将过期",
            "direct_cause": "域名注册商未收到续费付款",
        },
        "impact": {
            "user_facing": "若域名过期,相关服务 DNS 解析失败",
            "scope": "单域名 dzeesja.com",
        },
        "risk_assessment": {
            "current_risk": "高",
            "recurrence": "低",
            "urgency": "高",
        },
        "solutions": {
            "short_term": [_make_solution()],
        },
    }
    base.update(overrides)
    return base


def _get_tables(elements):
    return [e for e in elements if e.get("tag") == "table"]


def _get_markdown_texts(elements):
    texts = []
    for e in elements:
        if e.get("tag") == "markdown":
            texts.append(e.get("content", ""))
        elif e.get("tag") == "collapsible_panel":
            for sub in e.get("elements", []):
                if sub.get("tag") == "markdown":
                    texts.append(sub.get("content", ""))
    return texts


def _table_rows_by_key(table):
    return {r.get("key"): r.get("value") for r in table.get("rows", [])}


# ─── _find_exclusive_siblings ───

class TestFindExclusiveSiblings:
    def test_parallel_returns_empty(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(relation={"mode": "parallel"}),
            _make_solution(relation={"mode": "parallel"}),
        ]
        assert _find_exclusive_siblings(cg, 0) == []

    def test_exclusive_returns_sibling_in_same_group(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(title="续费", relation={"mode": "exclusive", "group": "dns-fix"}),
            _make_solution(title="弃用", relation={"mode": "exclusive", "group": "dns-fix"}),
            _make_solution(title="无关", relation={"mode": "parallel"}),
        ]
        siblings = _find_exclusive_siblings(cg, 0)
        assert len(siblings) == 1
        idx, sol = siblings[0]
        assert idx == 1
        assert sol["title"] == "弃用"

    def test_exclusive_different_group_not_sibling(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(relation={"mode": "exclusive", "group": "A"}),
            _make_solution(relation={"mode": "exclusive", "group": "B"}),
        ]
        assert _find_exclusive_siblings(cg, 0) == []

    def test_missing_group_returns_empty(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(relation={"mode": "exclusive"}),
            _make_solution(relation={"mode": "exclusive"}),
        ]
        assert _find_exclusive_siblings(cg, 0) == []


# ─── _build_approval_notify_elements ───

class TestApprovalCardStructure:
    def test_first_element_is_st_centric_header(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        first = elements[0]
        assert first.get("tag") == "markdown"
        content = first.get("content", "")
        assert "ST-0" in content
        assert "续费域名 dzeesja.com" in content

    def test_has_two_primary_tables(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        tables = _get_tables(elements)
        assert len(tables) >= 2

    def test_solution_info_table_has_required_rows(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        tables = _get_tables(elements)
        rows = _table_rows_by_key(tables[0])
        assert "方案 ID" in rows
        assert rows["方案 ID"] == "ST-0"
        assert "风险等级" in rows
        assert "🔴" in rows["风险等级"] or "high" in rows["风险等级"]
        assert "可回滚" in rows
        assert "❌" in rows["可回滚"]
        assert "影响范围" in rows
        assert "单域名" in rows["影响范围"]
        assert "针对根因" in rows
        assert "causal_chain[0]" in rows["针对根因"]
        assert "预期效果" in rows

    def test_context_table_has_alert_info(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        tables = _get_tables(elements)
        rows = _table_rows_by_key(tables[1])
        assert "关联 CG" in rows
        assert rows["关联 CG"] == "CG-1"
        assert "告警标题" in rows
        assert "domain-expiring" in rows["告警标题"]
        assert "服务/环境" in rows
        assert "prometheus" in rows["服务/环境"]
        assert "US" in rows["服务/环境"]
        assert "根因" in rows
        assert "dzeesja.com" in rows["根因"]
        assert "风险评估" in rows

    def test_reversible_true_shows_checkmark(self):
        sol = _make_solution(reversible=True)
        elements = _build_approval_notify_elements("CG-1", 0, sol, _make_cg_result())
        rows = _table_rows_by_key(_get_tables(elements)[0])
        assert "✅" in rows["可回滚"]

    def test_verify_cmd_rendered_as_code_block(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        md_texts = _get_markdown_texts(elements)
        joined = "\n".join(md_texts)
        assert "验证命令" in joined
        assert "`dig dzeesja.com +short`" in joined

    def test_collapsible_panel_contains_prompt(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        panels = [e for e in elements if e.get("tag") == "collapsible_panel"]
        assert len(panels) == 1
        panel_md = _get_markdown_texts(panels[0].get("elements", []))
        joined = "\n".join(panel_md)
        assert "Step 1: 登录 GoDaddy" in joined

    def test_button_with_instance_code(self):
        elements = _build_approval_notify_elements(
            "CG-1", 0, _make_solution(), _make_cg_result(), instance_code="INST-ABC"
        )
        buttons = [e for e in elements if e.get("tag") == "button"]
        assert len(buttons) == 1
        url = buttons[0].get("behaviors", [{}])[0].get("default_url", "")
        assert "INST-ABC" in url

    def test_no_button_when_instance_code_missing(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        buttons = [e for e in elements if e.get("tag") == "button"]
        assert len(buttons) == 0

    def test_parallel_relation_no_exclusive_row(self):
        elements = _build_approval_notify_elements("CG-1", 0, _make_solution(), _make_cg_result())
        rows = _table_rows_by_key(_get_tables(elements)[0])
        assert "互斥方案" not in rows

    def test_exclusive_relation_shows_sibling_in_table1(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(title="续费域名", relation={"mode": "exclusive", "group": "dns-fix"}),
            _make_solution(title="弃用域名并迁移", relation={"mode": "exclusive", "group": "dns-fix"},
                           risk="medium", reversible=True),
        ]
        elements = _build_approval_notify_elements(
            "CG-1", 0, cg["solutions"]["short_term"][0], cg
        )
        rows = _table_rows_by_key(_get_tables(elements)[0])
        assert "互斥方案" in rows
        assert "ST-1" in rows["互斥方案"]

    def test_exclusive_relation_appends_sibling_subtable(self):
        cg = _make_cg_result()
        cg["solutions"]["short_term"] = [
            _make_solution(title="续费域名", relation={"mode": "exclusive", "group": "dns-fix"}),
            _make_solution(title="弃用域名并迁移",
                           relation={"mode": "exclusive", "group": "dns-fix"},
                           risk="medium", reversible=True),
        ]
        elements = _build_approval_notify_elements(
            "CG-1", 0, cg["solutions"]["short_term"][0], cg
        )
        tables = _get_tables(elements)
        # 方案信息表 + 关联背景表 + 互斥方案子表 = 3
        assert len(tables) == 3
        sibling_table = tables[2]
        col_names = [c["name"] for c in sibling_table["columns"]]
        assert "id" in col_names
        assert "title" in col_names
        row_titles = [r.get("title", "") for r in sibling_table["rows"]]
        assert any("弃用" in t for t in row_titles)


# ─── _build_approval_detail (textarea) ───

class TestApprovalDetail:
    def test_includes_new_schema_fields(self):
        detail = _build_approval_detail("CG-1", 0, _make_solution(), _make_cg_result())
        assert "可回滚" in detail
        assert "影响范围" in detail
        assert "针对根因" in detail
        assert "causal_chain[0]" in detail

    def test_reversible_boolean_rendered_as_text(self):
        detail = _build_approval_detail("CG-1", 0, _make_solution(reversible=False), _make_cg_result())
        assert "否" in detail or "no" in detail.lower()

    def test_prompt_included(self):
        detail = _build_approval_detail("CG-1", 0, _make_solution(), _make_cg_result())
        assert "Step 1: 登录 GoDaddy" in detail
