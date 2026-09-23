#!/usr/bin/env python3
"""
Dispatcher Loop — sre-agent 的核心调度脚本。

每轮 cron 执行一次，处理所有确定性逻辑，输出 action plan JSON。
LLM cron 只需运行这个脚本然后执行输出的 action。

Steps:
  1. 读状态
  2. Poll PagerDuty
  3. 告警关联
  4. 处理新告警组
  5. 处理关联告警
  5.5 检查 dispatch_pending 重试
  6. 检测 subagent 产出物
  7. 检查审批状态
  8. 触发 Pattern Extraction
  9. 更新 last_poll_at + 清理过期 processed IDs
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

# 让 import 找到 scripts/ 同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_manager import StateManager
from checkpoint_manager import CheckpointManager
from completion_gate import CompletionGate

MAX_INVESTIGATION_RETRIES = 2

# environment 提取 fallback 关键词映射
_ENV_KEYWORDS = {
    "prod-us": "US", "us-prod": "US", "prod-eu": "EU",
    "eu-prod": "EU", "cn-main": "CN", "cn-prod": "CN",
    "staging": "staging",
}
from alert_correlator import correlate_incidents, normalize_title
from pagerduty_api import oncall_poll, _get_token as get_pd_token
from feishu_notify import send_elements_card, make_table, _get_webhook as get_feishu_webhook
from feishu_approval import (
    get_instance_status,
    create_instance,
    cancel_instance,
    _get_token as get_approval_token,
    _get_approval_code as get_approval_code,
)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read_template(path):
    """读取模板文件，失败返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _build_phase1_elements(header_lines, incidents):
    """构造 Phase 1 飞书卡片 elements 的通用部分。"""
    elements = [{"tag": "markdown", "content": line} for line in header_lines]
    elements.append({"tag": "hr"})
    elements.append({"tag": "markdown", "content": "**告警列表:**"})

    # 构造告警 table
    columns = [
        {"name": "id", "display_name": "告警 ID", "data_type": "text", "width": "auto"},
        {"name": "title", "display_name": "标题", "data_type": "text", "width": "auto"},
        {"name": "created_at", "display_name": "触发时间", "data_type": "text", "width": "auto"},
        {"name": "urgency", "display_name": "严重级别", "data_type": "text", "width": "auto"},
    ]
    rows = []
    for inc in incidents:
        rows.append({
            "id": inc.get("id", ""),
            "title": inc.get("title", ""),
            "created_at": inc.get("created_at", ""),
            "urgency": inc.get("urgency", ""),
        })
    elements.append(make_table(columns, rows))

    # PagerDuty 链接（Card 2.0 不支持 action 标签，用 markdown 链接替代）
    pd_url = ""
    if incidents:
        pd_url = incidents[0].get("html_url", "")
    if pd_url:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": f"[查看 PagerDuty]({pd_url})",
        })
    return elements


def _build_phase1_new_alert_elements(cg_id, incidents, service, environment):
    """构造 Phase 1 新告警飞书卡片 elements。"""
    header = [
        f"**{len(incidents)}** 条告警触发",
        f"**关联告警组:** {cg_id}",
        f"**服务:** {service}",
        f"**环境:** {environment}",
        "**状态:** 调查进行中...",
    ]
    return _build_phase1_elements(header, incidents)


def _build_phase1_recurrence_elements(cg_id, incidents, service, environment, recurrence_of):
    """构造复发通知卡片元素。"""
    inc_titles = "\n".join(f"- {inc.get('title', 'N/A')}" for inc in incidents[:5])
    elements = [
        {"tag": "markdown", "content": f"**告警组 {cg_id}** 检测为 **{recurrence_of}** 的复发\n\n{inc_titles}"},
        {"tag": "markdown", "content": f"**环境:** {environment}  **服务:** {service}"},
        {"tag": "markdown", "content": f"参考原始调查报告: `investigations/{recurrence_of}/report.yaml`"},
        {"tag": "markdown", "content": f"<font color='grey'>复发告警不派发新调查，参考原报告处理</font>"},
    ]
    return elements


def _build_phase1_correlated_elements(cg_id, incidents, service, environment):
    """构造 Phase 1 关联告警飞书卡片 elements。"""
    header = [
        f"**{len(incidents)}** 条新告警关联 {cg_id}",
        f"**与已知调查 {cg_id} 为同一根因**",
        f"**服务:** {service}",
        f"**环境:** {environment}",
        "**状态:** 调查进行中...",
    ]
    return _build_phase1_elements(header, incidents)


def _get_urgency_color(incidents):
    """从 incident 列表中获取最高 urgency 对应的飞书卡片颜色。"""
    for inc in incidents:
        if inc.get("urgency") == "high":
            return "red"
    return "yellow"


def _send_feishu_notification(title, elements, color="red", debug_mode=False):
    """发送飞书通知。失败返回 error 信息，成功返回 None。"""
    try:
        webhook_cfg = get_feishu_webhook(debug_mode=debug_mode)
        result = send_elements_card(
            webhook_url=webhook_cfg[0],
            secret=webhook_cfg[1],
            title=title,
            elements=elements,
            color=color,
        )
        # 检查飞书 API 返回的业务错误码
        if isinstance(result, dict) and result.get("code", 0) != 0:
            err = f"feishu card error: code={result.get('code')}, msg={result.get('msg', '')}"
            if debug_mode:
                print(f"[DEBUG] notify FAIL [{title}]: {err}", file=sys.stderr)
            return err
        if debug_mode:
            print(f"[DEBUG] notify OK [{title}]", file=sys.stderr)
        return None
    except SystemExit:
        err = "feishu webhook env not set"
        if debug_mode:
            print(f"[DEBUG] notify FAIL [{title}]: {err}", file=sys.stderr)
        return err
    except Exception as e:
        if debug_mode:
            print(f"[DEBUG] notify FAIL [{title}]: {e}", file=sys.stderr)
        return str(e)


def _save_prompt_file(state_dir, cg_id, prompt_type, prompt, suffix="", errors=None):
    """将 prompt 写入文件，返回文件路径。写入失败返回空字符串。"""
    dispatch_dir = os.path.join(state_dir, "pending-dispatches")
    os.makedirs(dispatch_dir, exist_ok=True)
    filename = f"{cg_id}-{prompt_type}{suffix}.md"
    path = os.path.join(dispatch_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(prompt)
        return path
    except Exception as e:
        if errors is not None:
            errors.append(f"Failed to write prompt file {path}: {e}")
        return ""


def _gather_prior_cg_context(state_dir):
    """收集过去 2 小时内的告警组调查结果，返回 JSON 字符串（无则返回空字符串）。"""
    try:
        mgr = StateManager(state_dir)
        active_cgs = mgr.get_active_cgs()
        completed_cgs = mgr.get_completed_cgs()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=2)
        summaries = []

        def _parse_ts(ts_str):
            if not ts_str:
                return None
            try:
                # Handle both with and without timezone info
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                return datetime.fromisoformat(ts_str)
            except Exception:
                return None

        # Process active CGs
        for cg_id, cg in active_cgs.items():
            ts = _parse_ts(cg.get("created_at"))
            if ts is None or ts < cutoff:
                continue
            # Ensure ts is timezone-aware for comparison
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            incidents = cg.get("incidents", [])
            title = incidents[0] if incidents else cg.get("service", "unknown")
            summaries.append({
                "cg_id": cg_id,
                "title": title,
                "status": cg.get("status", "investigating"),
                "environment": cg.get("environment", ""),
                "service": cg.get("service", ""),
                "root_cause_summary": "investigation in progress",
            })

        # Process completed CGs
        for cg_id, cg in completed_cgs.items():
            ts = _parse_ts(cg.get("completed_at"))
            if ts is None or ts < cutoff:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            # Try to load root cause from report
            root_cause_summary = "investigation in progress"
            try:
                result = mgr.load_cg_result(cg_id)
                if result:
                    chain = result.get("causal_chain", {}).get("chain", [])
                    for node in chain:
                        if node.get("node_type", node.get("type")) == "root_cause":
                            root_cause_summary = node.get("event", root_cause_summary)
                            break
                    if root_cause_summary == "investigation in progress":
                        # Fallback to root_cause summary field if present
                        rc = result.get("root_cause", {})
                        if isinstance(rc, dict):
                            root_cause_summary = rc.get("summary", root_cause_summary)
                        elif isinstance(rc, str):
                            root_cause_summary = rc
            except Exception:
                pass
            incidents = cg.get("incidents", [])
            title = incidents[0] if incidents else cg.get("service", "unknown")
            summaries.append({
                "cg_id": cg_id,
                "title": title,
                "status": "completed",
                "environment": cg.get("environment", ""),
                "service": cg.get("service", ""),
                "root_cause_summary": root_cause_summary,
            })

        if not summaries:
            return ""
        return json.dumps(summaries, ensure_ascii=False, indent=2)
    except Exception:
        return ""


def _build_investigation_prompt(
    alert_data, cg_id, state_dir, skill_base_dir, errors, resume_from=None
):
    """读取 investigation prompt 模板并填充变量。"""
    template_path = os.path.join(skill_base_dir, "references", "investigation-prompt-template.md")
    template = _read_template(template_path)
    if not template:
        errors.append(f"Failed to read investigation template: {template_path}")
        return ""

    # known_issues_matches
    ki_path = os.path.join(state_dir, "knowledge", "known-issues.md")
    known_issues = _read_template(ki_path) or "无匹配的已知问题"

    # prior_cg_context
    prior_cg_context = _gather_prior_cg_context(state_dir)

    prompt = template.replace("{alert_data_json}", json.dumps(alert_data, ensure_ascii=False))
    prompt = prompt.replace("{cg_id}", cg_id)
    prompt = prompt.replace("{state_dir}", state_dir)
    prompt = prompt.replace("{known_issues_matches}", known_issues)
    prompt = prompt.replace("{skill_base_dir}", skill_base_dir)
    prompt = prompt.replace("{prior_cg_result}", prior_cg_context)

    if resume_from:
        # resume_from can be a single checkpoint (legacy) or {"latest": ..., "all_checkpoints": [...]}
        if "all_checkpoints" in resume_from:
            latest = resume_from["latest"]
            all_cps = resume_from["all_checkpoints"]
        else:
            latest = resume_from
            all_cps = [resume_from]

        resume_stage = latest.get("stage", "未知")
        prompt += f"\n\n## 恢复指令\n\n"
        prompt += f"上一次调查在 {resume_stage} 阶段中断。\n"
        prompt += f"以下是所有已完成阶段的数据，从断点继续，不要重复已完成的阶段：\n\n"
        for cp in all_cps:
            stage = cp.get("stage", "unknown")
            data = cp.get("data", {})
            if data:
                prompt += f"### {stage}\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```\n\n"

    return prompt


def _build_merged_investigation_prompt(
    alert_data, cg_id, prior_result, state_dir, skill_base_dir, errors
):
    """读取 investigation prompt 模板并填充变量（merged investigation，含 prior result）。"""
    template_path = os.path.join(skill_base_dir, "references", "investigation-prompt-template.md")
    template = _read_template(template_path)
    if not template:
        errors.append(f"Failed to read investigation template: {template_path}")
        return ""

    ki_path = os.path.join(state_dir, "knowledge", "known-issues.md")
    known_issues = _read_template(ki_path) or "无匹配的已知问题"

    prior_result_str = ""
    if prior_result:
        try:
            prior_result_str = json.dumps(prior_result, ensure_ascii=False, indent=2)
        except Exception:
            prior_result_str = str(prior_result)

    prompt = template.replace("{alert_data_json}", json.dumps(alert_data, ensure_ascii=False))
    prompt = prompt.replace("{cg_id}", cg_id)
    prompt = prompt.replace("{state_dir}", state_dir)
    prompt = prompt.replace("{known_issues_matches}", known_issues)
    prompt = prompt.replace("{skill_base_dir}", skill_base_dir)
    prompt = prompt.replace("{prior_cg_result}", prior_result_str)

    return prompt


def _safe_truncate_prompt(prompt_text, max_chars):
    """仅在 prompt 超出 Feishu textarea 安全长度时才截断,默认保留完整内容。

    审批人需要看完整 prompt 才能判断变更是否合理,这是审批的核心信息源。
    只有触达 textarea 长度上限才截断,并在末尾明确标注。
    """
    if not prompt_text:
        return "(无执行步骤)"
    if len(prompt_text) <= max_chars:
        return prompt_text
    return prompt_text[:max_chars] + "\n\n... (达到 Feishu textarea 长度上限被截断,完整内容见 .sre-agent/.../report.yaml 的 solutions[].prompt 字段)"


def _build_approval_detail(cg_id, solution_idx, solution, cg_result):
    """构造审批表单 detail textarea 内容,从 report.yaml 提取关键决策信息。

    用于飞书审批卡片的「变更详情」字段,审批人据此判断是否批准。
    """
    if not cg_result:
        return f"(无 report.yaml,降级展示)\n方案: {solution.get('title', '')}"

    alert = cg_result.get("alert_summary", {}) or {}
    rc = cg_result.get("root_cause", {}) or {}
    impact = cg_result.get("impact", {}) or {}
    risk = cg_result.get("risk_assessment", {}) or {}
    title = cg_result.get("title", "")

    parts = []
    parts.append("━━━ 告警 ━━━")
    parts.append(f"标题: {title}")
    parts.append(f"环境: {alert.get('environment', '?')} / 服务: {alert.get('service', '?')}")
    parts.append(f"状态: {alert.get('status', '?')} / 触发: {alert.get('triggered_at', '?')}")
    pd_url = alert.get("pagerduty_url", "")
    if pd_url:
        parts.append(f"PD: {pd_url}")
    parts.append("")

    parts.append("━━━ 根因 ━━━")
    rc_summary = rc.get("summary", "")
    if rc_summary:
        parts.append(rc_summary)
    direct = rc.get("direct_cause", "")
    if direct:
        parts.append(f"直接原因: {direct}")
    parts.append("")

    parts.append("━━━ 影响 ━━━")
    scope = impact.get("scope", "")
    user_facing = impact.get("user_facing", "")
    if scope:
        parts.append(f"范围: {scope}")
    if user_facing:
        parts.append(f"用户面: {user_facing}")
    parts.append("")

    parts.append("━━━ 风险评估 ━━━")
    parts.append(
        f"当前风险={risk.get('current_risk', '?')} / "
        f"复发={risk.get('recurrence', '?')} / "
        f"紧迫={risk.get('urgency', '?')}"
    )
    parts.append("")

    parts.append(f"━━━ 方案 ST-{solution_idx} ━━━")
    parts.append(f"标题: {solution.get('title', '')}")
    parts.append(f"风险等级: {solution.get('risk', 'unknown')}")
    reversible = solution.get("reversible")
    if reversible is True:
        parts.append("可回滚: 是")
    elif reversible is False:
        parts.append("可回滚: 否")
    blast = solution.get("blast_radius", "")
    if blast:
        parts.append(f"影响范围: {blast}")
    addresses = solution.get("addresses", "")
    if addresses:
        parts.append(f"针对根因: {addresses}")
    relation = solution.get("relation") or {}
    if relation.get("mode") == "exclusive":
        group = relation.get("group", "")
        parts.append(f"互斥分组: {group} (批准本方案将自动撤销同组其他)")
    expected = solution.get("expected_effect", "")
    if expected:
        parts.append(f"预期效果: {expected}")
    verify = solution.get("verify_cmd", "")
    if verify:
        parts.append(f"验证命令: {verify}")
    parts.append("")

    parts.append("━━━ 执行步骤(完整 prompt) ━━━")
    # 元数据部分约 600~800 字符,飞书 textarea 安全上限按 ~10000 估算,
    # 给 prompt 留 ~9000 字符空间。超出才截断。
    metadata_so_far = "\n".join(parts)
    remaining = 9000 - len(metadata_so_far)
    if remaining < 1000:
        remaining = 1000  # 至少给 1000 字符
    parts.append(_safe_truncate_prompt(solution.get("prompt", ""), remaining))

    return "\n".join(parts)


# ─── 审批卡片辅助 ───

_RISK_EMOJI_MAP = {
    "critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢",
    "高": "🔴", "中": "🟡", "低": "🟢",
}


def _risk_label(risk):
    """'high' → '🔴 high'"""
    if not risk:
        return "unknown"
    emoji = _RISK_EMOJI_MAP.get(str(risk).lower(), "")
    return f"{emoji} {risk}".strip()


def _reversible_label(reversible):
    """True → '✅ yes', False → '❌ no', None → 'unknown'"""
    if reversible is True:
        return "✅ yes"
    if reversible is False:
        return "❌ no"
    return "unknown"


def _find_exclusive_siblings(cg_result, solution_idx):
    """返回同 relation.group 内的其他 short_term 方案 [(idx, solution), ...]。

    当前方案 relation.mode != 'exclusive' 或缺 group 时返回 []。
    """
    if not cg_result:
        return []
    short_term = (cg_result.get("solutions") or {}).get("short_term", []) or []
    if solution_idx < 0 or solution_idx >= len(short_term):
        return []
    current = short_term[solution_idx] or {}
    relation = current.get("relation") or {}
    if relation.get("mode") != "exclusive":
        return []
    group = relation.get("group")
    if not group:
        return []
    siblings = []
    for i, sib in enumerate(short_term):
        if i == solution_idx:
            continue
        sib_rel = (sib or {}).get("relation") or {}
        if sib_rel.get("mode") == "exclusive" and sib_rel.get("group") == group:
            siblings.append((i, sib))
    return siblings


def _cancel_exclusive_siblings(
    mgr, cg_id, approved_idx, cg_result, approval_token, submitter_open_id, errors,
):
    """批准某方案后,撤销同 relation.group 内其他 pending 兄弟方案。

    Returns:
        list of (sibling_idx, sibling_title) tuples — 成功撤销的兄弟方案,
        供调用方构造通知。未成功撤销的（包括 API 报错、sibling 已非 pending、
        缺 instance_code 等情况）不会出现在返回值里。

    Side effects:
      * 对每个成功撤销的 sibling 调用 cancel_instance() 并把 approval 状态
        改为 'exclusive_cancelled'
      * 撤销失败时追加到 errors 列表,不 raise

    关键不变量: 这个函数绝不会导致 Execution subagent 被派发去跑已撤销的方案,
    因为后续 Step 6d/6e/8 都只处理 auto_executed/approved/executing/executed_success
    状态,不包含 exclusive_cancelled。
    """
    siblings = _find_exclusive_siblings(cg_result, approved_idx)
    if not siblings:
        return []

    cancelled = []
    for sib_idx, sib_sol in siblings:
        sib_approval = mgr.load_approval(cg_id, sib_idx)
        if not sib_approval:
            # sibling 还没创建审批实例,无需撤销
            continue
        if sib_approval.get("status") != "pending_approval":
            # sibling 已是终态（approved/rejected/exclusive_cancelled/expired）,不动
            continue
        sib_instance = sib_approval.get("instance_code") or sib_approval.get("instance_id")
        if not sib_instance:
            # 数据异常: pending 但无 instance_code,不能撤销
            errors.append(
                f"Cannot cancel {cg_id} ST-{sib_idx}: missing instance_code"
            )
            continue

        try:
            cancel_instance(approval_token, sib_instance, submitter_open_id)
        except Exception as e:
            errors.append(
                f"Failed to cancel exclusive sibling {cg_id} ST-{sib_idx} "
                f"(instance_code={sib_instance}): {e}"
            )
            continue

        mgr.update_approval_status(cg_id, sib_idx, "exclusive_cancelled")
        cancelled.append((sib_idx, sib_sol.get("title", "")))

    return cancelled


def _build_approval_notify_elements(cg_id, solution_idx, solution, cg_result, instance_code=None):
    """构造审批通知卡片 elements —— ST-centric,方案信息为主,告警背景为辅。

    结构:
      1. ST-centric header (markdown) — 大标题显示 CG-ST-solution.title
      2. 方案信息表 (table) — 方案 ID/风险/可回滚/影响范围/针对根因/互斥/预期效果
      3. 验证命令 (markdown + inline code)
      4. 关联背景表 (table) — CG/告警标题/服务环境/PD/根因/影响/风险评估
      5. (可选) 互斥方案子表 (table) — relation.mode=exclusive 时列出同组兄弟方案
      6. collapsible_panel — 完整执行 prompt
      7. button — 跳转飞书审批 App
    """
    cg_result = cg_result or {}
    alert = cg_result.get("alert_summary", {}) or {}
    rc = cg_result.get("root_cause", {}) or {}
    impact = cg_result.get("impact", {}) or {}
    risk_assess = cg_result.get("risk_assessment", {}) or {}

    sol_title = solution.get("title", "")
    sol_risk = solution.get("risk", "unknown")
    sol_reversible = solution.get("reversible")
    sol_blast = solution.get("blast_radius", "")
    sol_addresses = solution.get("addresses", "")
    sol_expected = solution.get("expected_effect", "")
    sol_verify = solution.get("verify_cmd", "")
    sol_prompt = solution.get("prompt", "")

    siblings = _find_exclusive_siblings(cg_result, solution_idx)

    elements = []

    # ━━━ 1. ST-centric 大标题 ━━━
    elements.append({
        "tag": "markdown",
        "content": f"## 🛠 {cg_id} ST-{solution_idx} · {sol_title}",
    })

    # ━━━ 2. 方案信息表 ━━━
    sol_rows = [
        {"key": "方案 ID", "value": f"ST-{solution_idx}"},
        {"key": "风险等级", "value": _risk_label(sol_risk)},
        {"key": "可回滚", "value": _reversible_label(sol_reversible)},
        {"key": "影响范围", "value": sol_blast or "未指定"},
        {"key": "针对根因", "value": sol_addresses or "未指定"},
    ]
    if siblings:
        sibling_ids = ", ".join(f"ST-{i}" for i, _ in siblings)
        sol_rows.append({"key": "互斥方案", "value": f"{sibling_ids}（二选一,批准本方案将自动撤销其他）"})
    sol_rows.append({"key": "预期效果", "value": sol_expected or "未指定"})

    elements.append({"tag": "markdown", "content": "**📋 方案信息**"})
    elements.append(make_table(
        columns=[
            {"name": "key", "display_name": "项目", "data_type": "text", "width": "120px"},
            {"name": "value", "display_name": "详情", "data_type": "text", "width": "auto"},
        ],
        rows=sol_rows,
        row_height="low",
    ))

    # ━━━ 3. 验证命令 (code block) ━━━
    if sol_verify:
        elements.append({
            "tag": "markdown",
            "content": f"**🔍 验证命令:** `{sol_verify}`",
        })

    elements.append({"tag": "hr"})

    # ━━━ 4. 关联背景表 ━━━
    alert_title = cg_result.get("title", "")
    service_env = " / ".join(filter(None, [alert.get("service", ""), alert.get("environment", "")])) or "?"
    pd_url = alert.get("pagerduty_url", "")
    rc_text = rc.get("summary", "") or rc.get("direct_cause", "") or "未提供"
    impact_parts = []
    if impact.get("scope"):
        impact_parts.append(f"范围: {impact['scope']}")
    if impact.get("user_facing"):
        impact_parts.append(f"用户面: {impact['user_facing']}")
    impact_text = " / ".join(impact_parts) or "未提供"
    risk_text = (
        f"当前={risk_assess.get('current_risk', '?')} / "
        f"复发={risk_assess.get('recurrence', '?')} / "
        f"紧迫={risk_assess.get('urgency', '?')}"
    )

    ctx_rows = [
        {"key": "关联 CG", "value": cg_id},
        {"key": "告警标题", "value": alert_title or "?"},
        {"key": "服务/环境", "value": service_env},
    ]
    if pd_url:
        ctx_rows.append({"key": "PD 链接", "value": pd_url})
    ctx_rows.append({"key": "根因", "value": rc_text})
    ctx_rows.append({"key": "影响", "value": impact_text})
    ctx_rows.append({"key": "风险评估", "value": risk_text})

    elements.append({"tag": "markdown", "content": "**📎 关联背景**"})
    elements.append(make_table(
        columns=[
            {"name": "key", "display_name": "项目", "data_type": "text", "width": "120px"},
            {"name": "value", "display_name": "详情", "data_type": "text", "width": "auto"},
        ],
        rows=ctx_rows,
        row_height="low",
    ))

    # ━━━ 5. 互斥方案子表（可选）━━━
    if siblings:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "**⚠️ 互斥方案（批准本方案将自动撤销其他）**",
        })
        sib_rows = []
        for i, sib in siblings:
            sib_rows.append({
                "id": f"ST-{i}",
                "title": sib.get("title", ""),
                "risk": _risk_label(sib.get("risk", "")),
                "reversible": _reversible_label(sib.get("reversible")),
            })
        elements.append(make_table(
            columns=[
                {"name": "id", "display_name": "#", "data_type": "text", "width": "80px"},
                {"name": "title", "display_name": "标题", "data_type": "text", "width": "auto"},
                {"name": "risk", "display_name": "风险", "data_type": "text", "width": "100px"},
                {"name": "reversible", "display_name": "可回滚", "data_type": "text", "width": "100px"},
            ],
            rows=sib_rows,
            row_height="low",
        ))

    # ━━━ 6. 完整执行步骤（collapsible_panel）━━━
    if sol_prompt:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
                "title": {
                    "tag": "markdown",
                    "content": "**📋 完整执行步骤（点击展开）**",
                },
                "background_color": "grey-100",
                "vertical_align": "center",
                "padding": "4px 0px 4px 8px",
                "icon": {
                    "tag": "standard_icon",
                    "token": "down-small-ccm_outlined",
                    "color": "grey",
                    "size": "16px 16px",
                },
                "icon_position": "right",
                "icon_expanded_angle": -180,
            },
            "elements": [
                {"tag": "markdown", "content": f"```\n{sol_prompt}\n```"},
            ],
        })

    # ━━━ 7. 跳转按钮 ━━━
    if instance_code:
        elements.append({"tag": "hr"})
        approval_url = (
            f"https://applink.feishu.cn/client/mini_program/open"
            f"?appId=cli_9cb844403dbb9108"
            f"&path=pages%2Fdetail%2Findex%3FinstanceCode%3D{instance_code}"
        )
        elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "📲 前往飞书审批"},
            "type": "primary",
            "width": "default",
            "size": "medium",
            "behaviors": [
                {"type": "open_url", "default_url": approval_url},
            ],
        })

    return elements


def _build_execution_prompt(cg_id, solution_idx, solution, state_dir, skill_base_dir, errors):
    """读取 execution prompt 模板并填充变量。"""
    template_path = os.path.join(skill_base_dir, "references", "execution-prompt-template.md")
    template = _read_template(template_path)
    if not template:
        errors.append(f"Failed to read execution template: {template_path}")
        return ""

    solution_prompt = solution.get("prompt", "")
    change_desc = solution.get("change_desc", f"{cg_id}-solution-{solution_idx}")

    prompt = template.replace("{solution_prompt}", solution_prompt)
    prompt = prompt.replace("{cg_id}", cg_id)
    prompt = prompt.replace("{solution_idx}", str(solution_idx))
    prompt = prompt.replace("{change_desc}", change_desc)
    prompt = prompt.replace("{state_dir}", state_dir)
    prompt = prompt.replace("{skill_base_dir}", skill_base_dir)

    return prompt


def _build_pattern_extraction_prompt(cg_id, solution_idx, state_dir, skill_base_dir, errors):
    """读取 pattern extraction prompt 模板并填充变量。"""
    template_path = os.path.join(skill_base_dir, "references", "pattern-extraction-prompt-template.md")
    template = _read_template(template_path)
    if not template:
        errors.append(f"Failed to read pattern extraction template: {template_path}")
        return ""

    cg_result_path = os.path.join(state_dir, "investigations", cg_id, "report.yaml")

    # 读执行结果
    exec_result_path = os.path.join(
        state_dir, "executions", f"{cg_id}-solution-{solution_idx}", "result.json"
    )
    exec_result = ""
    try:
        with open(exec_result_path, "r") as f:
            exec_result = f.read()
    except Exception:
        pass

    prompt = template.replace("{cg_id}", cg_id)
    prompt = prompt.replace("{cg_result_path}", cg_result_path)
    prompt = prompt.replace("{execution_result}", exec_result)
    prompt = prompt.replace("{state_dir}", state_dir)
    prompt = prompt.replace("{solution_idx}", str(solution_idx))
    prompt = prompt.replace("{skill_base_dir}", skill_base_dir)

    return prompt


def _build_review_prompt(
    cg_id, report, checkpoint_data, state_dir, skill_base_dir, errors,
    revision_feedback=""
):
    """读取 review prompt 模板并填充变量。"""
    template_path = os.path.join(skill_base_dir, "references", "review-prompt-template.md")
    template = _read_template(template_path)
    if not template:
        errors.append(f"Failed to read review template: {template_path}")
        return ""

    # Serialize report
    try:
        import yaml as _yaml
        report_yaml_str = _yaml.dump(report, default_flow_style=False, allow_unicode=True)
    except Exception:
        report_yaml_str = json.dumps(report, indent=2, ensure_ascii=False)

    # Serialize checkpoint data
    checkpoint_data_str = ""
    if checkpoint_data:
        try:
            checkpoint_data_str = json.dumps(checkpoint_data, indent=2, ensure_ascii=False)
        except Exception:
            checkpoint_data_str = str(checkpoint_data)

    prompt = template.replace("{report_yaml}", report_yaml_str)
    prompt = prompt.replace("{checkpoint_data}", checkpoint_data_str or "无 checkpoint 数据")
    prompt = prompt.replace("{cg_id}", cg_id)
    prompt = prompt.replace("{state_dir}", state_dir)
    prompt = prompt.replace("{skill_base_dir}", skill_base_dir)
    prompt = prompt.replace("{revision_feedback}", revision_feedback or "无（首次审查）")

    return prompt


def _load_investigation_checkpoints(state_dir, cg_id):
    """加载 investigation subagent 的所有 checkpoint 数据。"""
    progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
    cp_mgr = CheckpointManager(progress_dir)
    all_cps = cp_mgr.list_all()
    # 只返回包含 data 的 checkpoint（过滤心跳）
    return [cp for cp in all_cps if cp.get("data")]


def _determine_revision_type(review_result):
    """根据 review 结果判断修订类型。

    Returns:
        "full" — R/T/E fail，需要完整重新 investigation
        "partial" — 仅 I/K/S fail，保留根因/时间线，仅修订影响/风险/方案
        None — all pass（不需要修订）
    """
    rte_failed = False
    iks_failed = False

    for section_key in ("root_cause_review", "timeline_review", "evidence_review"):
        section = review_result.get(section_key, {})
        if section.get("verdict") == "fail":
            rte_failed = True

    for section_key in ("impact_review", "risk_review"):
        section = review_result.get(section_key, {})
        if section.get("verdict") == "fail":
            iks_failed = True

    # solutions_review: check if any solution rejected
    solutions_review = review_result.get("solutions_review", [])
    for sol in solutions_review:
        if sol.get("verdict") == "rejected":
            iks_failed = True

    if rte_failed:
        return "full"
    if iks_failed:
        return "partial"
    return None


def _delete_report(state_dir, cg_id):
    """删除 investigations/{cg_id}/report.yaml（如存在）。"""
    report_path = os.path.join(state_dir, "investigations", cg_id, "report.yaml")
    if os.path.isfile(report_path):
        os.remove(report_path)


def _delete_progress(state_dir, cg_id):
    """Delete investigations/{cg_id}/progress/ directory (if exists)."""
    import shutil
    progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
    if os.path.isdir(progress_dir):
        shutil.rmtree(progress_dir)


def _get_investigation_timeout(state_dir, cg_id):
    """Get timeout based on investigation path (from VALIDATE_EXISTENCE checkpoint).

    After crash recovery clears checkpoints, falls back to default (30min)
    — intentional safe-direction fallback.
    """
    TIMEOUTS_INV = {
        "default": 30 * 60,           # 30min
        "transient_event": 8 * 60,    # 8min
        "self_healed": 15 * 60,       # 15min
    }
    cp_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
    cp_mgr = CheckpointManager(cp_dir)
    all_cps = cp_mgr.list_all()
    validate_cp = None
    for cp in all_cps:
        if cp.get("name") == "02_validate":
            validate_cp = cp
            break
    if validate_cp:
        status = validate_cp.get("data", {}).get("status")
        if status in TIMEOUTS_INV:
            return TIMEOUTS_INV[status]
    return TIMEOUTS_INV["default"]


# ━━━ 因果链节点类型 → 标签 / 颜色 ━━━
_NODE_LABELS = {
    "root_cause": "根因", "contributing_factor": "根因",
    "intermediate": "传播", "symptom": "传播",
    "amplifier": "放大", "impact": "影响",
}
_NODE_COLORS = {
    "root_cause": "red", "contributing_factor": "red",
    "intermediate": "orange", "symptom": "orange", "amplifier": "orange",
    "impact": "grey",
}
_STATUS_TEXT = {"self_healed": "已自愈", "ongoing": "持续中", "resolved": "已恢复", "transient_event": "瞬态事件"}
_RISK_EMOJI = {"低": "🟢", "中": "🟡", "高": "🔴"}


def _make_kv_row(key, value, key_weight=1, value_weight=4):
    """构造标签-值 column_set 行。"""
    return {
        "tag": "column_set", "flex_mode": "none",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": key_weight,
             "vertical_align": "top",
             "elements": [{"tag": "markdown", "content": f"**{key}**"}]},
            {"tag": "column", "width": "weighted", "weight": value_weight,
             "vertical_align": "top",
             "elements": [{"tag": "markdown", "content": value}]},
        ]
    }


def _build_phase2_elements(cg_id, cg_result):
    """构造 Phase 2 飞书卡片 elements（Card 2.0 格式）。

    对齐 spec §6.2.1 的最终卡片结构：
    告警信息(table) → 时间线(table) → 趋势数据(table) → 因果链(column_set) →
    影响范围(column_set) → 风险评估(table) → 解决方案(collapsible_panel) →
    补充说明(markdown) → 底部(markdown)
    """
    elements = []
    summary = cg_result.get("alert_summary", {})

    # ━━━ 1. 告警信息 — table ━━━
    elements.append({"tag": "markdown", "content": "**📋 告警信息**"})
    duration = summary.get("duration", "")
    status = _STATUS_TEXT.get(summary.get("status", ""), "")
    duration_status = " | ".join(filter(None, [duration, status])) or "未知"
    elements.append(make_table(
        columns=[
            {"name": "key", "display_name": "项目", "data_type": "text", "width": "auto"},
            {"name": "value", "display_name": "详情", "data_type": "text", "width": "auto"},
        ],
        rows=[
            {"key": "告警标题", "value": cg_result.get("title", "")},
            {"key": "服务", "value": summary.get("service", "")},
            {"key": "环境", "value": summary.get("environment", "")},
            {"key": "告警数", "value": str(summary.get("alert_count", 0))},
            {"key": "首次触发", "value": (summary.get("triggered_at") or "")[:16]},
            {"key": "持续/状态", "value": duration_status},
        ], row_height="low",
    ))
    elements.append({"tag": "hr"})

    # ━━━ 2. 时间线 — table（可选）━━━
    timeline = cg_result.get("timeline", [])
    if timeline:
        elements.append({"tag": "markdown", "content": "**🕐 时间线**"})
        tl_rows = []
        for ev in timeline:
            t = ev.get("time", "")
            # 统一时间格式为 "dd日 HH:MM"
            if t and "T" in t and len(t) >= 16:
                # ISO 8601: "2026-03-30T14:25:00Z" → "30日 14:25"
                t = f"{t[8:10]}日 {t[11:16]}"
            elif t and len(t) <= 5:
                # "HH:MM" → 从 triggered_at 推断 day
                triggered = summary.get("triggered_at", "")
                if triggered and len(triggered) >= 10:
                    t = f"{triggered[8:10]}日 {t}"
            tl_rows.append({"time": t, "event": ev.get("event", ""), "source": ev.get("source", "")})
        elements.append(make_table(
            columns=[
                {"name": "time", "display_name": "时间", "data_type": "text", "width": "120px"},
                {"name": "event", "display_name": "事件", "data_type": "text", "width": "auto"},
                {"name": "source", "display_name": "来源", "data_type": "text", "width": "100px"},
            ],
            rows=tl_rows, row_height="low",
        ))
        elements.append({"tag": "hr"})

    # ━━━ 3. 趋势数据 — table（可选）━━━
    trend_data = cg_result.get("trend_data", [])
    if trend_data:
        elements.append({"tag": "markdown", "content": "**📊 趋势数据**"})
        elements.append(make_table(
            columns=[
                {"name": "metric", "display_name": "指标", "data_type": "text", "width": "auto"},
                {"name": "window", "display_name": "窗口", "data_type": "text", "width": "100px"},
                {"name": "baseline", "display_name": "基线", "data_type": "text", "width": "80px"},
                {"name": "current", "display_name": "当前", "data_type": "text", "width": "80px"},
                {"name": "change", "display_name": "变化", "data_type": "text", "width": "100px"},
            ],
            rows=[{
                "metric": td.get("metric", ""), "window": td.get("window", ""),
                "baseline": str(td.get("baseline", "")), "current": str(td.get("current", "")),
                "change": td.get("change", ""),
            } for td in trend_data],
            row_height="low",
        ))
        elements.append({"tag": "hr"})

    # ━━━ 4. 因果链 — column_set 模拟表格 ━━━
    causal_chain = cg_result.get("causal_chain", {})
    chain_nodes = causal_chain.get("chain", [])
    evaluation = causal_chain.get("evaluation", {})
    is_complete = evaluation.get("complete", True) if evaluation else True

    if is_complete:
        completeness_marker = '<font color="green">完整</font>'
    else:
        completeness_marker = '<font color="red">不完整</font>'
    elements.append({"tag": "markdown", "content": f"**🔗 因果链** {completeness_marker}"})

    # 表头
    elements.append(_make_kv_row("类型", "**描述**"))
    for node in chain_nodes:
        node_type = node.get("node_type", node.get("type", ""))
        desc = node.get("event", node.get("description", ""))
        label = _NODE_LABELS.get(node_type, "传播")
        color = _NODE_COLORS.get(node_type, "orange")
        elements.append({
            "tag": "column_set", "flex_mode": "none",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": 1,
                 "vertical_align": "top",
                 "elements": [{"tag": "markdown",
                               "content": f'<font color="{color}">**[{label}]**</font>'}]},
                {"tag": "column", "width": "weighted", "weight": 4,
                 "vertical_align": "top",
                 "elements": [{"tag": "markdown", "content": desc}]},
            ]
        })

    # 不完整时：判据 + 建议
    if not is_complete and evaluation:
        criteria = evaluation.get("criteria", [])
        if not isinstance(criteria, list):
            criteria = []
        failed = [c for c in criteria if isinstance(c, dict) and not c.get("passed", True)]
        if failed:
            criteria_text = "**判据不满足:**\n"
            for c in failed:
                criteria_text += f"  {c.get('id', '')} {c.get('name', '')} ❌ {c.get('detail', '')}\n"
            elements.append({"tag": "markdown", "content": criteria_text})
        incomplete_reason = evaluation.get("incomplete_reason", "")
        hint = evaluation.get("next_investigation_hint", "")
        if incomplete_reason or hint:
            hint_text = ""
            if incomplete_reason:
                hint_text += f"⚠️ {incomplete_reason}\n"
            if hint:
                hint_text += f"**人工排查建议:** {hint}\n"
            elements.append({"tag": "markdown", "content": hint_text})

    elements.append({"tag": "hr"})

    # ━━━ 5. 影响范围 — column_set ━━━
    impact = cg_result.get("impact")
    if impact:
        elements.append({"tag": "markdown", "content": "**💥 影响范围**"})
        if impact.get("user_facing"):
            elements.append(_make_kv_row("用户影响", impact["user_facing"]))
        if impact.get("scope"):
            elements.append(_make_kv_row("范围", impact["scope"]))
        if impact.get("duration"):
            elements.append(_make_kv_row("持续时间", impact["duration"]))
        elements.append({"tag": "hr"})

    # ━━━ 6. 风险评估 — column_set + emoji（避免 table 超限）━━━
    risk = cg_result.get("risk_assessment")
    if risk:
        elements.append({"tag": "markdown", "content": "**⚖️ 风险评估**"})
        risk_cols = []
        for dim, key in [("当前风险", "current_risk"), ("重现概率", "recurrence"), ("修复紧迫性", "urgency")]:
            val = risk.get(key, "未知")
            level_char = val[0] if val else ""
            emoji = _RISK_EMOJI.get(level_char, "")
            display = f"{emoji} {val}" if emoji else val
            risk_cols.append({
                "tag": "column", "width": "weighted", "weight": 1,
                "elements": [{"tag": "markdown", "content": f"**{dim}**\n{display}"}],
            })
        elements.append({"tag": "column_set", "flex_mode": "trisect", "columns": risk_cols})
        elements.append({"tag": "hr"})

    # ━━━ 7. 解决方案 — collapsible_panel ━━━
    solutions = cg_result.get("solutions", {})
    short_term = solutions.get("short_term", [])
    long_term = solutions.get("long_term", [])
    has_solutions = short_term or long_term

    if has_solutions:
        elements.append({"tag": "markdown", "content": "**🔧 解决方案**"})

    if short_term:
        confidence = "（⚠️ 可信度较低）" if not is_complete else ""
        st_elements = []
        for i, sol in enumerate(short_term):
            title = sol.get("title", f"方案 {i}")
            st_elements.append({"tag": "markdown", "content": f"{i + 1}. **{title}**"})
            for k in ["action", "expected_effect", "risk", "prompt"]:
                v = sol.get(k)
                if v and k != "prompt":
                    label = {"action": "操作", "expected_effect": "预期效果", "risk": "风险"}.get(k, k)
                    st_elements.append(_make_kv_row(label, v))
        elements.append({
            "tag": "collapsible_panel", "expanded": True,
            "header": {"title": {"tag": "markdown",
                                 "content": f"**短期方案（立即执行）{confidence}**"}},
            "border": {"color": "red", "corner_radius": "6px"},
            "padding": "8px 12px 8px 12px",
            "elements": st_elements,
        })

    if long_term:
        lt_elements = []
        for i, sol in enumerate(long_term):
            title = sol.get("title", sol.get("description", f"方案 {i}"))
            lt_elements.append({"tag": "markdown", "content": f"{i + 1}. **{title}**"})
            for k in ["action", "expected_effect"]:
                v = sol.get(k)
                if v:
                    label = {"action": "操作", "expected_effect": "预期效果"}.get(k, k)
                    lt_elements.append(_make_kv_row(label, v))
        elements.append({
            "tag": "collapsible_panel", "expanded": True,
            "header": {"title": {"tag": "markdown", "content": "**长期方案（后续跟进）**"}},
            "border": {"color": "blue", "corner_radius": "6px"},
            "padding": "8px 12px 8px 12px",
            "elements": lt_elements,
        })

    if has_solutions:
        elements.append({"tag": "hr"})

    # ━━━ 8. 补充说明 ━━━
    notes_parts = []
    dims_na = cg_result.get("dimensions_not_available", [])
    if dims_na:
        dims_text = "**以下数据维度采集失败（结论基于现有数据）:**\n"
        for d in dims_na:
            dims_text += f"- {d.get('dimension', '')}: {d.get('reason', '')}\n"
        notes_parts.append(dims_text)
    # 可扩展其他补充说明类型

    if notes_parts:
        elements.append({"tag": "markdown", "content": "**⚠️ 补充说明**"})
        for part in notes_parts:
            elements.append({"tag": "markdown", "content": part})
        elements.append({"tag": "hr"})

    # ━━━ 9. 底部 ━━━
    pd_url = summary.get("pagerduty_url", "")
    footer_parts = []
    if pd_url:
        footer_parts.append(f"[查看 PagerDuty]({pd_url})")
    footer_parts.append('<font color="grey">由 sre-agent 自动生成</font>')
    elements.append({"tag": "markdown", "content": " | ".join(footer_parts)})

    return elements


def _load_ar_patterns(skill_base_dir):
    """加载 auto-remediation patterns。"""
    path = os.path.join(skill_base_dir, "references", "auto-remediation-patterns.yaml")
    if not os.path.isfile(path):
        return []
    try:
        import yaml
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else data.get("patterns", [])
    except Exception:
        return []


def _match_ar_pattern(solution, patterns):
    """检查 solution 是否匹配任何 AR pattern。返回 pattern 或 None。"""
    if not patterns:
        return None
    # 简单匹配：检查 solution 的 findings/title 是否命中 pattern 的 match
    sol_title = (solution.get("title", "") + " " + solution.get("prompt", "")).lower()
    for p in patterns:
        match_config = p.get("match", {})
        finding_pattern = match_config.get("finding_pattern", "")
        if finding_pattern and finding_pattern.lower() in sol_title:
            if p.get("risk") == "low" and p.get("reversible", False):
                return p
    return None


def run_loop(state_dir: str, skill_base_dir: str = ".") -> dict:
    """执行一轮 dispatcher loop，返回 action plan dict。"""
    # 把所有目录参数立刻解析为绝对路径,确保 prompt 模板里注入到 subagent 的
    # {state_dir} / {skill_base_dir} 占位符是 CWD-independent 的——subagent 的
    # 工作目录继承自主进程,和 dispatcher 的 CWD 可能完全不同,用相对路径会导致
    # StateManager/ReportBuilder/CheckpointManager 读写到错的目录,出现"dispatcher
    # 和 subagent 看到两套状态"的诡异故障。
    state_dir = os.path.abspath(os.path.expanduser(state_dir))
    skill_base_dir = os.path.abspath(os.path.expanduser(skill_base_dir))

    now = _now_iso()
    debug_mode = os.environ.get("SRE_AGENT_DEBUG", "").lower() == "true"
    actions = []
    errors = []
    summary = {
        "new_alerts": 0,
        "cgs_created": 0,
        "investigations_completed": 0,
        "reviews_dispatched": 0,
        "executions_completed": 0,
        "approvals_pending": 0,
        "notifications_sent": 0,
        "notifications_failed": 0,
    }

    # ─── Step 1: 读状态 ───
    try:
        mgr = StateManager(state_dir)
        state = mgr.load_state()
    except json.JSONDecodeError as e:
        return {
            "timestamp": now,
            "poll_succeeded": False,
            "errors": [f"State file JSON corrupted: {e}"],
            "actions": [],
            "summary": summary,
        }
    except Exception as e:
        return {
            "timestamp": now,
            "poll_succeeded": False,
            "errors": [f"State file IO error: {e}"],
            "actions": [],
            "summary": summary,
        }

    poll_succeeded = False
    new_incidents = []
    new_cg_ids = set()  # 本轮新创建的 CG IDs

    # ─── Step 2: Poll PagerDuty ───
    try:
        pd_token = get_pd_token()
        since = state.get("last_poll_at")
        if not since:
            # 首次 poll，使用当前时间减 10 分钟
            ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
            since = ten_min_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

        if debug_mode:
            poll_result = oncall_poll(pd_token, since=None, limit=1,
                                      statuses=["triggered", "acknowledged"])
            print(f"[DEBUG] PD poll: {poll_result['new_count']} alerts fetched", file=sys.stderr)
        else:
            poll_result = oncall_poll(pd_token, since=since)
        poll_succeeded = True

        # 过滤已处理的 incidents
        all_incidents = poll_result.get("new_incidents", [])
        all_ids = [inc["id"] for inc in all_incidents]
        new_ids = mgr.filter_new_incidents(all_ids)
        new_id_set = set(new_ids)
        new_incidents = [inc for inc in all_incidents if inc["id"] in new_id_set]
        summary["new_alerts"] = len(new_incidents)

    except SystemExit:
        errors.append("PagerDuty API token not set (PAGERDUTY_API_TOKEN)")
        poll_succeeded = False
    except Exception as e:
        errors.append(f"PagerDuty poll failed: {e}")
        poll_succeeded = False

    # ─── Step 3: 告警关联 ───
    correlation_groups = []
    if poll_succeeded and new_incidents:
        active_cgs = mgr.get_active_cgs()
        completed_cgs = mgr.get_completed_cgs()

        try:
            correlation_groups = correlate_incidents(
                new_incidents, active_cgs=active_cgs, completed_cgs=completed_cgs
            )
        except Exception as e:
            # 整体关联失败 → 每个 incident 降级为独立新告警组
            errors.append(f"Correlation failed, falling back to individual groups: {e}")
            correlation_groups = []
            for inc in new_incidents:
                try:
                    correlation_groups.append({
                        "group_id": f"fallback-{len(correlation_groups) + 1}",
                        "correlates_to": None,
                        "recurrence_of": None,
                        "incidents": [inc],
                        "fault_entity": normalize_title(inc.get("title", "")),
                        "service": inc.get("service", {}).get("summary", ""),
                    })
                except Exception as inner_e:
                    errors.append(f"Fallback grouping failed for incident {inc.get('id', '?')}: {inner_e}")

    # ─── Step 4: 处理新告警组 ───
    if poll_succeeded:
        for group in correlation_groups:
            if group.get("correlates_to") is not None:
                continue  # 关联告警在 Step 5 处理

            incidents = group.get("incidents", [])
            service = group.get("service", "")
            environment = ""
            # 尝试从 incidents 中提取 environment
            for inc in incidents:
                title = inc.get("title", "")
                if "[" in title and "]" in title:
                    environment = title.split("[")[1].split("]")[0]
                    break
            if not environment:
                # fallback: 从标题中搜索已知环境关键词
                env_keywords = _ENV_KEYWORDS
                for inc in incidents:
                    title_lower = inc.get("title", "").lower()
                    for keyword, env in env_keywords.items():
                        if keyword in title_lower:
                            environment = env
                            break
                    if environment:
                        break

            incident_ids = [inc["id"] for inc in incidents]

            is_recurrence = group.get("is_recurrence", False)
            recurrence_of = group.get("recurrence_of")

            # create_cg
            try:
                cg_id = mgr.create_cg(
                    incident_ids, service, environment,
                    is_recurrence=is_recurrence,
                    recurrence_of=recurrence_of,
                    fault_entity=group.get("fault_entity"),
                    service_entity=group.get("service_entity"),
                )
                new_cg_ids.add(cg_id)

                # 保存完整 incidents 数据用于 dispatch_pending 重试
                incidents_json_dir = os.path.join(state_dir, "investigations", cg_id)
                os.makedirs(incidents_json_dir, exist_ok=True)
                incidents_json_path = os.path.join(incidents_json_dir, "incidents.json")
                with open(incidents_json_path, "w") as f:
                    json.dump(incidents, f, indent=2, ensure_ascii=False)

                summary["cgs_created"] += 1

                # mark processed
                mgr.mark_processed(incident_ids)

            except Exception as e:
                # state 写入失败 → 中断
                errors.append(f"State write failed during CG creation: {e}")
                return {
                    "timestamp": now,
                    "poll_succeeded": poll_succeeded,
                    "errors": errors,
                    "actions": actions,
                    "summary": summary,
                }

            if is_recurrence:
                # 复发告警：发送复发通知，不派发调查，立即完成 CG
                feishu_elements = _build_phase1_recurrence_elements(
                    cg_id, incidents, service, environment, recurrence_of
                )
                urgency_color = _get_urgency_color(incidents)
                notify_err = _send_feishu_notification(
                    "On-Call 告警（复发）", feishu_elements, urgency_color,
                    debug_mode=debug_mode,
                )
                if notify_err:
                    summary["notifications_failed"] += 1
                else:
                    summary["notifications_sent"] += 1
                # 立即完成 CG，不派发调查
                try:
                    mgr.complete_cg(cg_id)
                except Exception as e:
                    errors.append(f"Failed to complete recurrence CG {cg_id}: {e}")
            else:
                # Phase 1 飞书通知
                feishu_elements = _build_phase1_new_alert_elements(
                    cg_id, incidents, service, environment
                )
                urgency_color = _get_urgency_color(incidents)
                notify_err = _send_feishu_notification(
                    "On-Call 告警（调查中）", feishu_elements, urgency_color,
                    debug_mode=debug_mode,
                )
                if notify_err:
                    summary["notifications_failed"] += 1
                else:
                    summary["notifications_sent"] += 1

                # 构建 investigation prompt → 写入文件
                prompt = _build_investigation_prompt(
                    incidents, cg_id, state_dir, skill_base_dir, errors
                )
                if prompt:
                    prompt_file = _save_prompt_file(state_dir, cg_id, "investigation", prompt, errors=errors)
                    if prompt_file:
                        actions.append({
                            "type": "dispatch_investigation",
                            "cg_id": cg_id,
                            "prompt_file": prompt_file,
                        })
                else:
                    errors.append(f"Empty prompt for {cg_id}, skipping dispatch")

    # ─── Step 5: 处理关联告警 ───
    if poll_succeeded:
        for group in correlation_groups:
            cg_id = group.get("correlates_to")
            if cg_id is None:
                continue

            incidents = group.get("incidents", [])
            incident_ids = [inc["id"] for inc in incidents]
            service = group.get("service", "")
            environment = ""
            for inc in incidents:
                title = inc.get("title", "")
                if "[" in title and "]" in title:
                    environment = title.split("[")[1].split("]")[0]
                    break
            if not environment:
                env_keywords = _ENV_KEYWORDS
                for inc in incidents:
                    title_lower = inc.get("title", "").lower()
                    for keyword, env in env_keywords.items():
                        if keyword in title_lower:
                            environment = env
                            break
                    if environment:
                        break

            try:
                mgr.mark_processed(incident_ids)

                # 计算 batch_num
                existing_batches = mgr.load_pending_batches(cg_id)
                batch_num = len(existing_batches) + 1
                mgr.save_pending_batch(cg_id, batch_num, incidents)
                mgr.add_incidents_to_cg(cg_id, incident_ids)
            except Exception as e:
                errors.append(f"State write failed for correlated group {cg_id}: {e}")
                continue

            # 关联通知
            feishu_elements = _build_phase1_correlated_elements(
                cg_id, incidents, service, environment
            )
            urgency_color = _get_urgency_color(incidents)
            notify_err = _send_feishu_notification(
                "On-Call 告警更新", feishu_elements, urgency_color,
                debug_mode=debug_mode,
            )
            if notify_err:
                summary["notifications_failed"] += 1
            else:
                summary["notifications_sent"] += 1

    # ─── Step 5.5: 检查 dispatch_pending 重试 ───
    try:
        active_cgs = mgr.get_active_cgs()
        for cg_id, cg in active_cgs.items():
            if cg.get("status") == "dispatch_pending" and cg_id not in new_cg_ids:
                retry_count = cg.get("investigation_retry_count", 0)
                if retry_count > MAX_INVESTIGATION_RETRIES:
                    errors.append(f"Investigation failed after {retry_count} attempts for {cg_id}")
                    mgr.update_active_cg(cg_id, {"status": "investigation_failed"})
                    mgr.complete_cg(cg_id)
                    # TODO: send feishu notification for investigation_failed
                    summary["investigations_completed"] += 1
                    continue
                # 重新输出 dispatch_investigation action
                # 从 incidents.json 读取完整 incident 数据
                incidents_json_path = os.path.join(
                    state_dir, "investigations", cg_id, "incidents.json"
                )
                try:
                    with open(incidents_json_path, "r") as f:
                        alert_data = json.load(f)
                except Exception:
                    # fallback: 用 incident IDs
                    alert_data = cg.get("incidents", [])
                prompt = _build_investigation_prompt(
                    alert_data, cg_id, state_dir, skill_base_dir, errors
                )
                if prompt:
                    prompt_file = _save_prompt_file(state_dir, cg_id, "investigation", prompt, "-retry", errors=errors)
                    if prompt_file:
                        actions.append({
                            "type": "dispatch_investigation",
                            "cg_id": cg_id,
                            "prompt_file": prompt_file,
                        })
    except Exception as e:
        errors.append(f"Error checking dispatch_pending CGs: {e}")

    # ─── Step 5.6: Crash Recovery Detection ───
    TIMEOUTS = {
        "investigation": {
            "default": 30 * 60,           # 30min
            "transient_event": 8 * 60,    # 8min
            "self_healed": 15 * 60,       # 15min
        },
        "review": 10 * 60,                # 10min
        "execution": 15 * 60,             # 15min
        "pattern_extraction": 5 * 60,     # 5min
    }
    INITIAL_TIMEOUT = TIMEOUTS["investigation"]["default"]

    try:
        active_cgs = mgr.get_active_cgs()
        for cg_id, cg in active_cgs.items():
            if cg.get("status") != "investigating":
                continue
            if cg_id in new_cg_ids:
                continue  # just dispatched this round

            # Check for report.yaml first (handled by Step 6a)
            report = mgr.load_cg_result(cg_id)
            if report is not None:
                valid, gate_errors = CompletionGate.validate_report(report)
                if not valid:
                    errors.append(f"CompletionGate rejected report for {cg_id}: {gate_errors}")
                    report = None  # treat as if report doesn't exist
            if report is not None:
                continue

            progress_dir = os.path.join(state_dir, "investigations", cg_id, "progress")
            cp_mgr = CheckpointManager(progress_dir)
            latest = cp_mgr.get_latest()

            investigating_since = cg.get("investigating_since") or cg.get("created_at")
            since_ts = _parse_iso(investigating_since).timestamp()
            now_ts = time.time()

            should_retry = False

            if latest is None:
                if now_ts - since_ts > INITIAL_TIMEOUT:
                    should_retry = True
                    errors.append(f"Crash recovery: {cg_id} has no checkpoints after {INITIAL_TIMEOUT}s")
            else:
                inv_timeout = _get_investigation_timeout(state_dir, cg_id)
                if cp_mgr.is_timed_out(timeout_seconds=inv_timeout):
                    should_retry = True
                    errors.append(f"Crash recovery: {cg_id} last checkpoint stale (>{inv_timeout}s)")

            if should_retry:
                # Reset status to dispatch_pending for retry
                mgr.update_active_cg(cg_id, {
                    "status": "dispatch_pending",
                    "investigation_retry_count": cg.get("investigation_retry_count", 0) + 1,
                })
                _delete_report(state_dir, cg_id)
                _delete_progress(state_dir, cg_id)

                # Build retry prompt with checkpoint context
                incidents_json_path = os.path.join(
                    state_dir, "investigations", cg_id, "incidents.json"
                )
                try:
                    with open(incidents_json_path, "r") as f:
                        alert_data = json.load(f)
                except Exception:
                    alert_data = cg.get("incidents", [])

                resume_ctx = None
                if latest is not None:
                    all_checkpoints = cp_mgr.list_all()
                    resume_ctx = {"latest": latest, "all_checkpoints": all_checkpoints}
                prompt = _build_investigation_prompt(
                    alert_data, cg_id, state_dir, skill_base_dir, errors,
                    resume_from=resume_ctx
                )
                if prompt:
                    prompt_file = _save_prompt_file(
                        state_dir, cg_id, "investigation", prompt,
                        "-crash-recovery", errors=errors
                    )
                    if prompt_file:
                        actions.append({
                            "type": "dispatch_investigation",
                            "cg_id": cg_id,
                            "prompt_file": prompt_file,
                        })
    except Exception as e:
        errors.append(f"Error in Step 5.6 crash recovery: {e}")

    # ─── Step 6: 检测 subagent 产出物 ───

    # 6a: active CGs 中 status=="investigating" → report 检测 → review 派发 → review 检测 → verdict 处理
    MAX_REVISION_COUNT = 2

    try:
        active_cgs = mgr.get_active_cgs()
        for cg_id, cg in active_cgs.items():
            if cg.get("status") != "investigating":
                continue

            report = mgr.load_cg_result(cg_id)
            if report is not None:
                valid, gate_errors = CompletionGate.validate_report(report)
                if not valid:
                    errors.append(f"CompletionGate rejected report for {cg_id}: {gate_errors}")
                    report = None  # treat as if report doesn't exist
            if debug_mode:
                print(f"[DEBUG] Step 6a: {cg_id} status={cg.get('status')}, "
                      f"report={'found' if report is not None else 'not found'}, "
                      f"review_dispatched={cg.get('review_dispatched', False)}, "
                      f"revision_count={cg.get('revision_count', 0)}", file=sys.stderr)

            if report is None:
                continue  # 还没出 report，跳过

            # ─── 6a-i: report.yaml 存在，检查是否需要派发 review ───
            review_dispatched = cg.get("review_dispatched", False)

            # Transient-event fast path: skip review, go straight to complete + Phase 2.
            # 瞬态告警（API 注入 / 规则未真实 firing / 指标正常）跑 review 几乎零收益，
            # 直接结案可省掉一个 review subagent 往返，~3-5 min。
            report_status = (report.get("alert_summary") or {}).get("status")
            if not review_dispatched and report_status == "transient_event":
                try:
                    mgr.complete_cg(cg_id)
                    summary["investigations_completed"] += 1
                    phase2_elements = _build_phase2_elements(cg_id, report)
                    phase2_elements.insert(-1, {
                        "tag": "markdown",
                        "content": (
                            'ℹ️ <font color="grey">**瞬态事件快路径**</font>：'
                            "告警判定为 transient_event，已跳过 Review 直接结案。"
                        ),
                    })
                    notify_err = _send_feishu_notification(
                        f"On-Call 告警诊断 | {cg_id}", phase2_elements, "red",
                        debug_mode=debug_mode,
                    )
                    if notify_err:
                        summary["notifications_failed"] += 1
                    else:
                        summary["notifications_sent"] += 1
                except Exception as e:
                    errors.append(f"Error completing transient CG {cg_id}: {e}")
                continue  # 跳过 review 派发

            if not review_dispatched:
                # 派发 Review subagent
                checkpoint_data = _load_investigation_checkpoints(state_dir, cg_id)

                # 检查是否有之前的 revision feedback
                revision_feedback = ""
                old_review = mgr.load_review_result(cg_id)
                if old_review and old_review.get("overall_verdict") == "needs_revision":
                    revision_feedback = old_review.get("revision_notes", "")
                    # 清理旧 review.yaml
                    mgr.delete_review_result(cg_id)

                prompt = _build_review_prompt(
                    cg_id, report, checkpoint_data,
                    state_dir, skill_base_dir, errors,
                    revision_feedback=revision_feedback,
                )
                if prompt:
                    revision_count = cg.get("revision_count", 0)
                    suffix = f"-rev{revision_count}" if revision_count > 0 else ""
                    prompt_file = _save_prompt_file(
                        state_dir, cg_id, "review", prompt, suffix, errors=errors
                    )
                    if prompt_file:
                        actions.append({
                            "type": "dispatch_review",
                            "cg_id": cg_id,
                            "prompt_file": prompt_file,
                        })
                        mgr.mark_review_dispatched(cg_id)
                        summary["reviews_dispatched"] += 1
                else:
                    errors.append(f"Empty review prompt for {cg_id}, falling back to no-review flow")
                    # fallback: 跳过 review，直接走原始流程
                    try:
                        mgr.complete_cg(cg_id)
                        summary["investigations_completed"] += 1
                        phase2_elements = _build_phase2_elements(cg_id, report)
                        notify_err = _send_feishu_notification(
                            f"On-Call 告警诊断 | {cg_id}", phase2_elements, "red",
                            debug_mode=debug_mode,
                        )
                        if notify_err:
                            summary["notifications_failed"] += 1
                        else:
                            summary["notifications_sent"] += 1
                    except Exception as e:
                        errors.append(f"Error completing CG {cg_id} (no-review fallback): {e}")
                continue  # 等下一轮检测 review.yaml

            # ─── 6a-ii: review 已派发，检查 review.yaml ───
            review_result = mgr.load_review_result(cg_id)
            if review_result is None:
                # review.yaml 还没出，检查 review subagent 是否崩溃
                review_progress_dir = os.path.join(
                    state_dir, "investigations", cg_id, "review_progress"
                )
                review_cp_mgr = CheckpointManager(review_progress_dir)
                review_timed_out = review_cp_mgr.is_timed_out(timeout_seconds=TIMEOUTS["review"])

                if review_timed_out is True:
                    review_retry = cg.get("review_retry_count", 0)
                    if review_retry < 1:
                        # 第一次超时 → 重试一次
                        errors.append(f"Review subagent timeout for {cg_id}, retrying (attempt {review_retry + 1})")
                        mgr.update_active_cg(cg_id, {"review_dispatched": False, "review_retry_count": review_retry + 1})
                    else:
                        # 重试仍失败 → 跳过 review，按无 review 流程继续
                        errors.append(f"Review subagent failed after {review_retry + 1} attempts for {cg_id}, skipping review")
                        try:
                            mgr.complete_cg(cg_id)
                            summary["investigations_completed"] += 1
                            phase2_elements = _build_phase2_elements(cg_id, report)
                            phase2_elements.insert(-1, {
                                "tag": "markdown",
                                "content": '⚠️ <font color="orange">Review 未完成（subagent 崩溃），结论仅供参考</font>',
                            })
                            notify_err = _send_feishu_notification(
                                f"On-Call 告警诊断 | {cg_id}", phase2_elements, "red",
                                debug_mode=debug_mode,
                            )
                            if notify_err:
                                summary["notifications_failed"] += 1
                            else:
                                summary["notifications_sent"] += 1
                        except Exception as e:
                            errors.append(f"Error completing CG {cg_id} (review crash skip): {e}")
                elif review_timed_out is None:
                    # 无任何 checkpoint，检查是否超过初始超时
                    review_dispatch_time = cg.get("investigating_since") or cg.get("created_at")
                    if review_dispatch_time:
                        since_ts = _parse_iso(review_dispatch_time).timestamp()
                        if time.time() - since_ts > INITIAL_TIMEOUT:
                            errors.append(f"Review subagent never started for {cg_id}, skipping review")
                            # 跳过 review，标记 "review 未完成"
                            try:
                                mgr.complete_cg(cg_id)
                                summary["investigations_completed"] += 1
                                phase2_elements = _build_phase2_elements(cg_id, report)
                                # 在卡片底部追加 review 未完成标记
                                phase2_elements.insert(-1, {
                                    "tag": "markdown",
                                    "content": '⚠️ <font color="orange">Review 未完成，结论仅供参考</font>',
                                })
                                notify_err = _send_feishu_notification(
                                    f"On-Call 告警诊断 | {cg_id}", phase2_elements, "red",
                                    debug_mode=debug_mode,
                                )
                                if notify_err:
                                    summary["notifications_failed"] += 1
                                else:
                                    summary["notifications_sent"] += 1
                            except Exception as e:
                                errors.append(f"Error completing CG {cg_id} (review skip): {e}")
                continue  # 等 review.yaml

            # ─── 6a-iii: review.yaml 存在，处理 verdict ───
            valid, gate_errors = CompletionGate.validate_review(review_result)
            if not valid:
                errors.append(f"CompletionGate rejected review for {cg_id}: {gate_errors}")
                review_result = None  # treat as not ready
            if review_result is None:
                continue
            overall_verdict = review_result.get("overall_verdict", "")

            if overall_verdict == "approved":
                # approved → complete_cg + Phase 2 通知 + solution 路由
                try:
                    mgr.complete_cg(cg_id)
                    summary["investigations_completed"] += 1

                    phase2_elements = _build_phase2_elements(cg_id, report)
                    notify_err = _send_feishu_notification(
                        f"On-Call 告警诊断 | {cg_id}", phase2_elements, "red",
                        debug_mode=debug_mode,
                    )
                    if notify_err:
                        summary["notifications_failed"] += 1
                    else:
                        summary["notifications_sent"] += 1
                except Exception as e:
                    errors.append(f"Error completing CG {cg_id}: {e}")

            elif overall_verdict == "needs_revision":
                revision_count = cg.get("revision_count", 0)

                if revision_count < MAX_REVISION_COUNT:
                    # 静默重新 investigation
                    revision_type = _determine_revision_type(review_result)
                    revision_notes = review_result.get("revision_notes", "")

                    # 重置 review 状态
                    mgr.reset_for_revision(cg_id)

                    # 删除旧 report.yaml 以便 investigation 产出新的
                    _delete_report(state_dir, cg_id)

                    # 构建带 review 反馈的 investigation prompt
                    incidents_json_path = os.path.join(
                        state_dir, "investigations", cg_id, "incidents.json"
                    )
                    try:
                        with open(incidents_json_path, "r") as f:
                            alert_data = json.load(f)
                    except Exception:
                        alert_data = cg.get("incidents", [])

                    prompt = _build_investigation_prompt(
                        alert_data, cg_id, state_dir, skill_base_dir, errors,
                    )

                    # 附加 review 反馈到 prompt
                    if prompt:
                        prompt += "\n\n## Review 反馈（修订指令）\n\n"
                        prompt += f"这是第 {revision_count + 1} 次修订。Review subagent 的反馈如下：\n\n"
                        prompt += f"```\n{revision_notes}\n```\n\n"

                        if revision_type == "full":
                            prompt += (
                                "**修订范围：完整重新调查。**\n"
                                "根因分析（R）、时间线（T）或证据（E）存在问题，"
                                "需要完整重新执行 INVESTIGATE → BUILD_CHAIN → EVALUATE → FINALIZE。\n"
                                "不要复用之前的分析结论。\n"
                            )
                        elif revision_type == "partial":
                            prompt += (
                                "**修订范围：保留根因和时间线，仅修订影响/风险/方案。**\n"
                                "根因分析和时间线已通过审查，仅影响范围（I）、风险评估（K）或方案（S）存在问题。\n"
                                "保留原有的根因分析和时间线，从 EVALUATE 阶段开始重新评估影响、风险和方案。\n"
                            )

                        prompt_file = _save_prompt_file(
                            state_dir, cg_id, "investigation", prompt,
                            f"-revision-{revision_count + 1}", errors=errors
                        )
                        if prompt_file:
                            actions.append({
                                "type": "dispatch_investigation",
                                "cg_id": cg_id,
                                "prompt_file": prompt_file,
                            })
                    else:
                        errors.append(f"Empty revision prompt for {cg_id}")

                else:
                    # revision_count >= MAX_REVISION_COUNT → 停止循环，发 Phase 2 标记"分析存疑"
                    try:
                        mgr.complete_cg(cg_id)
                        summary["investigations_completed"] += 1

                        phase2_elements = _build_phase2_elements(cg_id, report)
                        # 在卡片顶部插入存疑标记
                        phase2_elements.insert(0, {
                            "tag": "markdown",
                            "content": (
                                '⚠️ <font color="red">**分析存疑**</font>\n'
                                f"经 {revision_count} 次修订仍未通过审查，"
                                "以下结论由 investigation 和 review 双方意见组成，请人工判断。"
                            ),
                        })
                        phase2_elements.insert(1, {"tag": "hr"})

                        # 附加 review 反馈摘要
                        revision_notes = review_result.get("revision_notes", "")
                        if revision_notes:
                            # 在底部 footer 前插入 review 意见
                            phase2_elements.insert(-1, {"tag": "hr"})
                            phase2_elements.insert(-1, {
                                "tag": "markdown",
                                "content": f"**Review 意见:**\n{revision_notes}",
                            })

                        notify_err = _send_feishu_notification(
                            f"On-Call 告警诊断（存疑）| {cg_id}", phase2_elements, "red",
                            debug_mode=debug_mode,
                        )
                        if notify_err:
                            summary["notifications_failed"] += 1
                        else:
                            summary["notifications_sent"] += 1
                    except Exception as e:
                        errors.append(f"Error completing CG {cg_id} (max revision): {e}")

            else:
                errors.append(f"Unknown review verdict for {cg_id}: {overall_verdict}")

    except Exception as e:
        errors.append(f"Error in Step 6a: {e}")

    # 6b: completed CGs 中 solutions_status=={} → 检查 pending batch
    try:
        completed_cgs = mgr.get_completed_cgs()
        for cg_id, cg in completed_cgs.items():
            solutions_status = cg.get("solutions_status", {})
            # 检查是否有需要重试的 routing_failed solutions
            has_routing_failed = any(
                v == "routing_failed" for v in solutions_status.values()
            ) if solutions_status else False

            if solutions_status and not has_routing_failed:
                continue  # 所有 solutions 已正常路由，跳过

            pending = mgr.load_pending_batches(cg_id)
            if pending:
                # 有 pending batch → 输出 merged investigation action
                prior_result = mgr.load_cg_result(cg_id)
                # 合并所有 pending batches
                all_pending_incidents = []
                for batch in pending:
                    if isinstance(batch, list):
                        all_pending_incidents.extend(batch)
                    else:
                        all_pending_incidents.append(batch)

                prompt = _build_merged_investigation_prompt(
                    all_pending_incidents, cg_id, prior_result,
                    state_dir, skill_base_dir, errors,
                )

                # 重新设为 dispatch_pending（在 completed 中不能设 status，
                # 需要将 CG 移回 active 或直接 dispatch）
                # 根据 spec: "有则设 dispatch_pending + 输出 merged investigation action"
                # 由于 CG 已在 completed 中，我们直接输出 action
                if prompt:
                    prompt_file = _save_prompt_file(state_dir, cg_id, "investigation", prompt, "-merged", errors=errors)
                    if prompt_file:
                        actions.append({
                            "type": "dispatch_investigation",
                            "cg_id": cg_id,
                            "prompt_file": prompt_file,
                        })

                mgr.clear_pending(cg_id)
                # 标记为 merged investigating 防止下轮重复
                mgr.set_solutions_status(cg_id, {"merged": "investigating"})
                continue

            # 6c: 无 pending → 路由 solutions
            cg_result = mgr.load_cg_result(cg_id)
            if not cg_result:
                continue

            solutions = cg_result.get("solutions", {})

            # 检测 _no_action 标记（自愈 / 无需操作的 CG）
            if solutions.get("_no_action"):
                mgr.set_solutions_status(cg_id, {"_": "no_action_needed"})
                continue
            short_term = solutions.get("short_term", [])
            ar_patterns = _load_ar_patterns(skill_base_dir)

            for idx, solution in enumerate(short_term):
                solution_key = str(idx)
                # 检查是否已有 approval（跳过已成功路由的）
                existing_approval = mgr.load_approval(cg_id, idx)
                if existing_approval and existing_approval.get("status") != "routing_failed":
                    continue  # 已处理过且非失败

                # 检查 AR pattern 匹配
                matched_pattern = _match_ar_pattern(solution, ar_patterns)
                if matched_pattern:
                    # L5 自动执行
                    exec_prompt = _build_execution_prompt(
                        cg_id, idx, solution, state_dir, skill_base_dir, errors
                    )
                    exec_prompt_file = _save_prompt_file(state_dir, cg_id, "execution", exec_prompt, f"-s{idx}", errors=errors)
                    if exec_prompt_file:
                        actions.append({
                            "type": "dispatch_execution",
                            "cg_id": cg_id,
                            "solution_idx": idx,
                            "prompt_file": exec_prompt_file,
                        })
                    mgr.save_approval(cg_id, idx, {
                        "status": "auto_executed",
                        "pattern_id": matched_pattern.get("pattern_id", ""),
                        "created_at": now,
                    })

                    # 自动修复通知
                    notify_elements = [
                        {"tag": "markdown", "content": (
                            f"**关联 CG:** {cg_id}\n"
                            f"**匹配 Pattern:** {matched_pattern.get('pattern_id', '')} "
                            f"({matched_pattern.get('name', '')})"
                        )},
                        {"tag": "hr"},
                        {"tag": "markdown", "content": (
                            f"**操作摘要:** {solution.get('title', '')}"
                        )},
                    ]
                    notify_err = _send_feishu_notification(
                        "自动修复已执行", notify_elements, "blue",
                        debug_mode=debug_mode,
                    )
                    if notify_err:
                        summary["notifications_failed"] += 1
                    else:
                        summary["notifications_sent"] += 1

                else:
                    # L4 需审批
                    try:
                        approval_token = get_approval_token()
                        approval_code = get_approval_code()
                        title = f"{cg_id} ST-{idx}: {solution.get('title', '')}"
                        # 富信息 detail：从 cg_result 提取根因/影响/风险/方案细节
                        detail = _build_approval_detail(cg_id, idx, solution, cg_result)
                        # 审批人 open_id（从 FEISHU_APPROVER_OPEN_ID 读取，形如 ou_xxx）
                        approver_open_id = os.environ.get("FEISHU_APPROVER_OPEN_ID", "")
                        if not approver_open_id:
                            raise RuntimeError("FEISHU_APPROVER_OPEN_ID not set")
                        instance_code = create_instance(
                            approval_token, approval_code, title, detail, approver_open_id
                        )

                        mgr.save_approval(cg_id, idx, {
                            "status": "pending_approval",
                            "approval_sent_at": now,
                            "instance_code": instance_code,
                            "approver_open_id": approver_open_id,
                        })
                        summary["approvals_pending"] += 1

                        # 审批通知（富信息卡片）
                        notify_elements = _build_approval_notify_elements(
                            cg_id, idx, solution, cg_result, instance_code=instance_code
                        )
                        # ST-centric header: 把方案标题带进去,让审批人零点击识别变更内容
                        raw_sol_title = solution.get("title", "")
                        sol_title_trunc = raw_sol_title if len(raw_sol_title) <= 40 else raw_sol_title[:39] + "…"
                        notify_title = f"变更审批 | {cg_id} ST-{idx}: {sol_title_trunc}" if sol_title_trunc else f"变更审批 | {cg_id} ST-{idx}"
                        notify_err = _send_feishu_notification(
                            notify_title, notify_elements, "blue",
                            debug_mode=debug_mode,
                        )
                        if notify_err:
                            summary["notifications_failed"] += 1
                        else:
                            summary["notifications_sent"] += 1

                    except SystemExit:
                        errors.append(f"Feishu approval token/code not set for {cg_id} ST-{idx}")
                    except Exception as e:
                        errors.append(f"Failed to create approval for {cg_id} ST-{idx}: {e}")

            # 标记 solutions_status 为已处理（避免下轮重复）
            try:
                cg_state = mgr.get_completed_cgs().get(cg_id)
                if cg_state is not None:
                    new_solutions_status = {}
                    existing_solutions_status = cg_state.get("solutions_status", {})
                    for i in range(len(short_term)):
                        approval = mgr.load_approval(cg_id, i)
                        if approval:
                            new_solutions_status[str(i)] = approval.get("status", "pending_approval")
                        else:
                            # 保留已有的 routing_failed 状态（而非覆盖）
                            prev_status = existing_solutions_status.get(str(i), "")
                            new_solutions_status[str(i)] = prev_status if prev_status == "routing_failed" else "routing_failed"
                    mgr.set_solutions_status(cg_id, new_solutions_status)
            except Exception as e:
                errors.append(f"Failed to update solutions_status for {cg_id}: {e}")

    except Exception as e:
        errors.append(f"Error in Step 6b/6c: {e}")

    # 6d: 检查执行中的 approval → result.json 存在
    try:
        completed_cgs = mgr.get_completed_cgs()
        for cg_id, cg in completed_cgs.items():
            solutions_status = cg.get("solutions_status", {})
            for idx_str, status in solutions_status.items():
                try:
                    idx = int(idx_str)
                except ValueError:
                    continue  # Skip non-numeric keys
                approval = mgr.load_approval(cg_id, idx)
                if not approval:
                    continue
                if approval.get("status") not in ("auto_executed", "approved", "executing"):
                    continue

                # 检查 result.json
                result_path = os.path.join(
                    state_dir, "executions", f"{cg_id}-solution-{idx}", "result.json"
                )
                if os.path.isfile(result_path):
                    try:
                        with open(result_path, "r") as f:
                            exec_result = json.load(f)
                        valid, gate_errors = CompletionGate.validate_result(exec_result)
                        if not valid:
                            errors.append(f"CompletionGate rejected result for {cg_id} ST-{idx}: {gate_errors}")
                            continue  # skip this result
                        exec_status = exec_result.get("status", "")

                        if exec_status == "success":
                            mgr.update_approval_status(cg_id, idx, "executed_success")
                            summary["executions_completed"] += 1

                            # 成功通知
                            notify_elements = [
                                {"tag": "markdown", "content": (
                                    f"**变更执行成功**\n"
                                    f"**关联告警:** {cg_id}\n"
                                    f"**方案:** ST-{idx}"
                                )},
                            ]
                            notify_err = _send_feishu_notification(
                                "变更已执行", notify_elements, "green",
                                debug_mode=debug_mode,
                            )
                            if notify_err:
                                summary["notifications_failed"] += 1
                            else:
                                summary["notifications_sent"] += 1

                        elif exec_status == "failure":
                            mgr.update_approval_status(cg_id, idx, "executed_failure")
                            summary["executions_completed"] += 1

                            notify_elements = [
                                {"tag": "markdown", "content": (
                                    f"**变更执行失败，需要人工介入**\n"
                                    f"**关联告警:** {cg_id}\n"
                                    f"**方案:** ST-{idx}\n"
                                    f"**错误:** {exec_result.get('error_detail', 'unknown')}"
                                )},
                            ]
                            notify_err = _send_feishu_notification(
                                "变更执行失败", notify_elements, "red",
                                debug_mode=debug_mode,
                            )
                            if notify_err:
                                summary["notifications_failed"] += 1
                            else:
                                summary["notifications_sent"] += 1

                        elif exec_status == "skipped_self_healed":
                            mgr.update_approval_status(cg_id, idx, "skipped_self_healed")
                            summary["executions_completed"] += 1

                    except Exception as e:
                        errors.append(f"Error reading result.json for {cg_id} ST-{idx}: {e}")
    except Exception as e:
        errors.append(f"Error in Step 6d: {e}")

    # 6e: status=="extracting_pattern" → 检查 pattern_candidate.yaml
    try:
        completed_cgs = mgr.get_completed_cgs()
        for cg_id, cg in completed_cgs.items():
            solutions_status = cg.get("solutions_status", {})
            for idx_str in solutions_status:
                try:
                    idx = int(idx_str)
                except ValueError:
                    continue  # Skip non-numeric keys
                approval = mgr.load_approval(cg_id, idx)
                if not approval:
                    continue
                if approval.get("status") != "extracting_pattern":
                    continue

                pattern_path = os.path.join(
                    state_dir, "executions", f"{cg_id}-solution-{idx}", "pattern_candidate.yaml"
                )
                if os.path.isfile(pattern_path):
                    try:
                        import yaml as _yaml_pe
                        with open(pattern_path, "r") as f:
                            pattern_data = _yaml_pe.safe_load(f)
                        valid, gate_errors = CompletionGate.validate_pattern(pattern_data)
                        if not valid:
                            errors.append(f"CompletionGate rejected pattern for {cg_id} ST-{idx}: {gate_errors}")
                            continue  # skip this pattern
                    except Exception as e:
                        errors.append(f"Error reading pattern_candidate.yaml for {cg_id} ST-{idx}: {e}")
                        continue
                    mgr.update_approval_status(cg_id, idx, "pattern_extracted")

                    # 通知有新 pattern 候选
                    notify_elements = [
                        {"tag": "markdown", "content": (
                            f"**新 Auto-Remediation Pattern 候选**\n"
                            f"**关联:** {cg_id} ST-{idx}\n"
                            f"**位置:** {pattern_path}\n"
                            f"请审批后纳入 auto-remediation-patterns.yaml"
                        )},
                    ]
                    notify_err = _send_feishu_notification(
                        f"Pattern 候选审批 {cg_id} ST-{idx}", notify_elements, "blue",
                        debug_mode=debug_mode,
                    )
                    if notify_err:
                        summary["notifications_failed"] += 1
                    else:
                        summary["notifications_sent"] += 1
    except Exception as e:
        errors.append(f"Error in Step 6e: {e}")

    # ─── Step 7: 检查审批状态 ───

    # 7a: 审批状态轮询
    try:
        approval_dir = os.path.join(state_dir, "approvals")
        if os.path.isdir(approval_dir):
            import glob as glob_mod
            for path in glob_mod.glob(os.path.join(approval_dir, "CG-*-solution-*.json")):
                try:
                    with open(path, "r") as f:
                        approval_data = json.load(f)
                except Exception:
                    continue

                if approval_data.get("status") != "pending_approval":
                    continue

                instance_code = approval_data.get("instance_code") or approval_data.get("instance_id")
                if not instance_code:
                    continue

                try:
                    approval_token = get_approval_token()
                    feishu_status = get_instance_status(approval_token, instance_code)

                    # 解析文件名获取 cg_id 和 solution_idx
                    fname = os.path.basename(path).replace(".json", "")
                    parts = fname.rsplit("-solution-", 1)
                    ap_cg_id = parts[0]
                    ap_idx = int(parts[1])

                    if feishu_status == "APPROVED":
                        mgr.update_approval_status(ap_cg_id, ap_idx, "approved")

                        # 获取 solution 信息构建 execution prompt
                        cg_result = mgr.load_cg_result(ap_cg_id)

                        # 互斥方案联动: 批准一个就撤销同组其他 pending 兄弟方案
                        submitter_oid = os.environ.get("FEISHU_APPROVER_OPEN_ID", "")
                        cancelled_siblings = []
                        if cg_result and submitter_oid:
                            cancelled_siblings = _cancel_exclusive_siblings(
                                mgr, ap_cg_id, ap_idx, cg_result,
                                approval_token, submitter_oid, errors,
                            )
                        if cancelled_siblings:
                            # 获取本方案标题用于通知文案
                            approved_title = ""
                            _sl = (cg_result.get("solutions") or {}).get("short_term") or []
                            if ap_idx < len(_sl):
                                approved_title = _sl[ap_idx].get("title", "")
                            cancelled_lines = "\n".join(
                                f"- ST-{sib_idx}: {sib_title}" for sib_idx, sib_title in cancelled_siblings
                            )
                            notify_elements = [
                                {"tag": "markdown", "content": (
                                    f"**已批准方案:** ST-{ap_idx} {approved_title}\n"
                                    f"**关联告警:** {ap_cg_id}"
                                )},
                                {"tag": "hr"},
                                {"tag": "markdown", "content": (
                                    f"**因互斥已自动撤销:**\n{cancelled_lines}"
                                )},
                            ]
                            notify_err = _send_feishu_notification(
                                f"互斥方案已自动撤销 {ap_cg_id}",
                                notify_elements, "grey",
                                debug_mode=debug_mode,
                            )
                            if notify_err:
                                summary["notifications_failed"] += 1
                            else:
                                summary["notifications_sent"] += 1

                        if cg_result:
                            sol_list = cg_result.get("solutions", {}).get("short_term", [])
                            if ap_idx < len(sol_list):
                                exec_prompt = _build_execution_prompt(
                                    ap_cg_id, ap_idx, sol_list[ap_idx],
                                    state_dir, skill_base_dir, errors,
                                )
                                actions.append({
                                    "type": "dispatch_execution",
                                    "cg_id": ap_cg_id,
                                    "solution_idx": ap_idx,
                                    "prompt_file": _save_prompt_file(state_dir, ap_cg_id, "execution", exec_prompt, f"-s{ap_idx}", errors=errors),
                                })

                    elif feishu_status == "REJECTED":
                        mgr.update_approval_status(ap_cg_id, ap_idx, "rejected")

                        notify_elements = [
                            {"tag": "markdown", "content": (
                                f"**审批已拒绝**\n"
                                f"**关联告警:** {ap_cg_id}\n"
                                f"**方案:** ST-{ap_idx}"
                            )},
                        ]
                        notify_err = _send_feishu_notification(
                            f"审批已取消 {ap_cg_id} ST-{ap_idx}", notify_elements, "grey",
                            debug_mode=debug_mode,
                        )
                        if notify_err:
                            summary["notifications_failed"] += 1
                        else:
                            summary["notifications_sent"] += 1

                    # PENDING → skip

                except SystemExit:
                    errors.append("Feishu approval token not set for status check")
                except Exception as e:
                    errors.append(f"Error checking approval status for {path}: {e}")
    except Exception as e:
        errors.append(f"Error in Step 7a: {e}")

    # 7b: 超时检查
    try:
        expired = mgr.list_expired_approvals(now=now, ttl_hours=48)
        for (exp_cg_id, exp_idx) in expired:
            mgr.update_approval_status(exp_cg_id, exp_idx, "expired")
            notify_elements = [
                {"tag": "markdown", "content": (
                    f"**审批已超时（48h）**\n"
                    f"**关联告警:** {exp_cg_id}\n"
                    f"**方案:** ST-{exp_idx}"
                )},
            ]
            notify_err = _send_feishu_notification(
                f"审批已过期 {exp_cg_id} ST-{exp_idx}", notify_elements, "grey",
                debug_mode=debug_mode,
            )
            if notify_err:
                summary["notifications_failed"] += 1
            else:
                summary["notifications_sent"] += 1

        approaching = mgr.list_approaching_expiry(now=now, warn_hours=24, ttl_hours=48)
        for (ap_cg_id, ap_idx) in approaching:
            approval = mgr.load_approval(ap_cg_id, ap_idx)
            if approval and not approval.get("expiry_warned"):
                # 计算剩余时间
                sent_at = approval.get("approval_sent_at", "")
                remaining_h = 48
                if sent_at:
                    elapsed = _parse_iso(now) - _parse_iso(sent_at)
                    remaining_h = max(0, 48 - elapsed.total_seconds() / 3600)

                notify_elements = [
                    {"tag": "markdown", "content": (
                        f"**审批即将过期**\n"
                        f"**关联告警:** {ap_cg_id}\n"
                        f"**方案:** ST-{ap_idx}\n"
                        f"**剩余:** {remaining_h:.0f}h"
                    )},
                ]
                notify_err = _send_feishu_notification(
                    f"审批即将过期 {ap_cg_id} ST-{ap_idx}", notify_elements, "yellow",
                    debug_mode=debug_mode,
                )
                if notify_err:
                    summary["notifications_failed"] += 1
                else:
                    summary["notifications_sent"] += 1

                # 标记已提醒
                try:
                    approval["expiry_warned"] = True
                    mgr.save_approval(ap_cg_id, ap_idx, approval)
                except Exception:
                    pass
    except Exception as e:
        errors.append(f"Error in Step 7b: {e}")

    # ─── Step 8: 触发 Pattern Extraction ───
    try:
        completed_cgs = mgr.get_completed_cgs()
        for cg_id, cg in completed_cgs.items():
            solutions_status = cg.get("solutions_status", {})
            for idx_str in solutions_status:
                try:
                    idx = int(idx_str)
                except ValueError:
                    continue  # Skip non-numeric keys
                approval = mgr.load_approval(cg_id, idx)
                if not approval:
                    continue
                if approval.get("status") != "executed_success":
                    continue
                # 检查是否已提取
                pattern_path = os.path.join(
                    state_dir, "executions", f"{cg_id}-solution-{idx}", "pattern_candidate.yaml"
                )
                if os.path.isfile(pattern_path):
                    continue  # 已提取

                # 输出 dispatch_pattern_extraction action
                prompt = _build_pattern_extraction_prompt(
                    cg_id, idx, state_dir, skill_base_dir, errors
                )
                actions.append({
                    "type": "dispatch_pattern_extraction",
                    "cg_id": cg_id,
                    "solution_idx": idx,
                    "prompt_file": _save_prompt_file(state_dir, cg_id, "pattern_extraction", prompt, f"-s{idx}", errors=errors),
                })

                # 更新 status
                mgr.update_approval_status(cg_id, idx, "extracting_pattern")
    except Exception as e:
        errors.append(f"Error in Step 8: {e}")

    # ─── Step 9: 更新 last_poll_at + 清理 ───
    if poll_succeeded:
        try:
            mgr.update_last_poll(now)

            # 清理超过 24h 的 processed_incident_ids
            # 只保留最近的 IDs（简单策略：如果列表过长则截断）
            mgr.trim_processed_ids(max_ids=1000)
        except Exception as e:
            errors.append(f"Error updating last_poll: {e}")

    # pending-dispatches/ 下的 prompt 文件保留不清理，作为每个 CG 的完整调查留痕

    return {
        "timestamp": now,
        "poll_succeeded": poll_succeeded,
        "errors": errors,
        "actions": actions,
        "summary": summary,
    }


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="sre-agent dispatcher loop")
    parser.add_argument("--state-dir", default=".sre-agent")
    parser.add_argument("--skill-base-dir", default=".")
    args = parser.parse_args()

    result = run_loop(args.state_dir, args.skill_base_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
