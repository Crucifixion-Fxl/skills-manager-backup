#!/usr/bin/env python3
"""
ReportBuilder — Investigation subagent 用于构建 report.yaml 的 Builder 类。
方法签名即 schema，save() 时校验并写入文件。
"""

import os
from datetime import datetime, timezone

import yaml


_VALID_SEVERITIES = {"critical", "high", "medium", "low"}
_VALID_STATUSES = {"transient_event", "self_healed", "ongoing", "resolved"}
_VALID_NODE_TYPES = {
    "root_cause", "contributing_factor", "intermediate",
    "symptom", "amplifier", "impact",
}
_VALID_TERMS = {"short", "long"}
_VALID_RISKS = {"critical", "high", "medium", "low"}
_VALID_RELATION_MODES = {"parallel", "exclusive"}

_MIN_ROOT_CAUSE_SUMMARY = 50
_MIN_EVIDENCE_LENGTH = 20


def _parse_iso_timestamp(value):
    """Parse an ISO 8601 timestamp, tolerating trailing 'Z'.

    Returns a timezone-aware datetime, or None on parse failure / empty value.
    Used by timeline/causal-chain ordering validation; invalid timestamps are
    skipped (not fatal) so old reports without timestamps still validate.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ─── Solution field schema (canonical, single source of truth) ───
#
# 这是 sre-agent solution 字段的唯一定义。schema 文档、prompt template、
# completion_gate、dispatcher 渲染都引用本表,禁止在其他地方重复定义字段。
#
# 字段维度:
#   short_term (executable):  Execution subagent 实际执行的修复操作
#   long_term (recommendation): 长期改进建议,不会被自动执行
#
# 修改字段需同步更新:
#   1. SOLUTION_SCHEMA (本表)
#   2. add_solution() 签名
#   3. references/schemas/incident-report-schema.md
#   4. references/investigation-prompt-template.md (FINALIZE 部分)
#   5. dispatcher_loop._build_approval_detail() 的渲染逻辑
#   6. tests/test_report_builder.py
#
# 任何 drift 由 test_report_builder.py 守护。
SOLUTION_SCHEMA = {
    # 字段名: (required_for_short, required_for_long, type, description)
    "title":           (True,  True,  str,  "方案简短标题"),
    "action":          (True,  True,  str,  "操作描述(一句话)"),
    "addresses":       (True,  True,  str,  "解决的因果链节点引用,如 'causal_chain[0]'"),
    "expected_effect": (True,  True,  str,  "预期效果(一句话,执行后世界会变成什么样)"),
    "risk":            (True,  False, str,  "操作风险等级 (critical/high/medium/low)"),
    "reversible":      (True,  False, bool, "是否可回滚"),
    "blast_radius":    (True,  False, str,  "操作影响的实例/服务/资源数量描述"),
    "verify_cmd":      (True,  False, str,  "执行前验证问题仍存在的命令"),
    "prompt":          (True,  False, str,  "Zero-context 完整执行步骤"),
    "relation":        (False, False, dict, "互斥/并行关系: {mode: parallel|exclusive, group: id}"),
}


def get_required_solution_fields(term):
    """返回指定 term 必填字段名列表。"""
    idx = 0 if term == "short" else 1
    return [name for name, spec in SOLUTION_SCHEMA.items() if spec[idx]]


def get_optional_solution_fields(term):
    """返回指定 term 可选字段名列表。"""
    idx = 0 if term == "short" else 1
    return [name for name, spec in SOLUTION_SCHEMA.items() if not spec[idx]]


class ReportBuilder:
    """Builder for investigation report.yaml files."""

    def __init__(self, cg_id, output_dir):
        self._cg_id = cg_id
        self._output_dir = output_dir
        self._metadata = None
        self._root_cause = None
        self._chain_nodes = []
        self._solutions_short = []
        self._solutions_long = []
        self._conclusion = None
        self._impact = None
        self._dimensions_not_available = []
        self._timeline = []
        self._trend_data = []
        self._risk_assessment = None
        self._evaluation = None

    def set_metadata(self, title, severity, environment, service, alert_count=1,
                     triggered_at=None, pagerduty_url=None,
                     duration=None, status=None):
        """Set alert metadata. Required.

        Args:
            duration: 告警实际持续时间，如 "~4min"、"25min"、"持续中"
            status: 告警当前状态，如 "self_healed"、"ongoing"、"resolved"
        """
        if severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(_VALID_SEVERITIES)}, got '{severity}'"
            )
        if status is not None and status not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUSES)} or None, got '{status}'"
            )
        self._metadata = {
            "title": title,
            "severity": severity,
            "environment": environment,
            "service": service,
            "alert_count": alert_count,
            "triggered_at": triggered_at,
            "pagerduty_url": pagerduty_url,
            "duration": duration,
            "status": status,
        }

    def set_root_cause(self, summary, category, direct_cause=None, is_ongoing=False):
        """Set root cause analysis. Required."""
        self._root_cause = {
            "summary": summary,
            "category": category,
            "direct_cause": direct_cause,
            "is_ongoing": is_ongoing,
        }

    def add_chain_node(self, node_type, event, evidence, timestamp=None, caused_by=None):
        """Add a causal chain node. At least one root_cause node required."""
        if node_type not in _VALID_NODE_TYPES:
            raise ValueError(
                f"node_type must be one of {sorted(_VALID_NODE_TYPES)}, got '{node_type}'"
            )
        node = {
            "node_type": node_type,
            "event": event,
            "evidence": evidence,
        }
        if timestamp:
            node["timestamp"] = timestamp
        if caused_by is not None:
            node["caused_by"] = caused_by
        self._chain_nodes.append(node)

    def set_conclusion(self, text):
        """Set conclusion text. Required."""
        self._conclusion = text

    def add_solution(self, term, *, title, action, addresses, expected_effect,
                     risk=None, reversible=None, blast_radius=None,
                     verify_cmd=None, prompt=None, relation=None):
        """添加修复方案,字段定义见 SOLUTION_SCHEMA。

        Args (term="short"/"long" 都需要):
            title: 方案简短标题
            action: 操作描述(一句话)
            addresses: 解决的因果链节点引用,如 "causal_chain[0]"
            expected_effect: 预期效果(执行后状态)

        Args (仅 term="short" 需要):
            risk: 操作风险等级 (critical/high/medium/low)
            reversible: 是否可回滚 (bool)
            blast_radius: 操作影响范围描述
            verify_cmd: 执行前验证问题仍存在的命令
            prompt: Zero-context 完整执行步骤

        Args (可选,仅 term="short" 适用):
            relation: 互斥/并行关系 {mode: parallel|exclusive, group: id}
                      未指定时默认 {mode: parallel}
        """
        if term not in _VALID_TERMS:
            raise ValueError(
                f"term must be one of {sorted(_VALID_TERMS)}, got '{term}'"
            )

        # 收集所有字段值
        sol = {
            "title": title,
            "action": action,
            "addresses": addresses,
            "expected_effect": expected_effect,
        }
        if risk is not None:
            sol["risk"] = risk
        if reversible is not None:
            sol["reversible"] = reversible
        if blast_radius is not None:
            sol["blast_radius"] = blast_radius
        if verify_cmd is not None:
            sol["verify_cmd"] = verify_cmd
        if prompt is not None:
            sol["prompt"] = prompt
        if relation is not None:
            sol["relation"] = relation
        elif term == "short":
            sol["relation"] = {"mode": "parallel"}  # default

        # 按 SOLUTION_SCHEMA 校验必填字段
        required = get_required_solution_fields(term)
        missing = [f for f in required if sol.get(f) is None]
        if missing:
            raise ValueError(
                f"add_solution(term={term!r}): missing required fields {missing}. "
                f"See SOLUTION_SCHEMA in report_builder.py."
            )

        # 字段类型 + 取值校验
        if "risk" in sol and sol["risk"] not in _VALID_RISKS:
            raise ValueError(
                f"risk must be one of {sorted(_VALID_RISKS)}, got '{sol['risk']}'"
            )
        if "reversible" in sol and not isinstance(sol["reversible"], bool):
            raise ValueError(
                f"reversible must be bool, got {type(sol['reversible']).__name__}"
            )
        if "relation" in sol:
            rel = sol["relation"]
            if not isinstance(rel, dict):
                raise ValueError(f"relation must be dict, got {type(rel).__name__}")
            mode = rel.get("mode")
            if mode not in _VALID_RELATION_MODES:
                raise ValueError(
                    f"relation.mode must be one of {sorted(_VALID_RELATION_MODES)}, "
                    f"got {mode!r}"
                )
            if mode == "exclusive" and not rel.get("group"):
                raise ValueError(
                    "relation.mode='exclusive' requires non-empty 'group' identifier"
                )

        if term == "short":
            self._solutions_short.append(sol)
        else:
            self._solutions_long.append(sol)

    def set_impact(self, user_facing=None, scope=None, duration=None):
        """Set impact assessment. Optional."""
        self._impact = {}
        if user_facing:
            self._impact["user_facing"] = user_facing
        if scope:
            self._impact["scope"] = scope
        if duration:
            self._impact["duration"] = duration

    def add_dimension_not_available(self, dimension, reason):
        """Record a data dimension that was not available during investigation."""
        self._dimensions_not_available.append({
            "dimension": dimension,
            "reason": reason,
        })

    def add_timeline_event(self, time, event, source):
        """Add a timeline event. Optional but recommended."""
        self._timeline.append({"time": time, "event": event, "source": source})

    def add_trend_data(self, metric, window, baseline, current, change):
        """Add a trend data entry. Optional."""
        self._trend_data.append({
            "metric": metric, "window": window,
            "baseline": baseline, "current": current, "change": change,
        })

    def set_risk_assessment(self, current_risk, recurrence, urgency):
        """Set risk assessment. Optional."""
        self._risk_assessment = {
            "current_risk": current_risk,
            "recurrence": recurrence,
            "urgency": urgency,
        }

    def set_evaluation(self, complete, criteria, incomplete_reason=None,
                       next_investigation_hint=None):
        """Set causal chain evaluation. Optional."""
        # Normalize criteria: must be list of dicts
        if isinstance(criteria, str):
            criteria = [{"id": "?", "name": "unknown", "passed": True, "detail": criteria}]
        elif isinstance(criteria, list):
            criteria = [c if isinstance(c, dict) else {"id": "?", "name": "unknown", "passed": True, "detail": str(c)} for c in criteria]
        else:
            criteria = []
        self._evaluation = {
            "complete": complete,
            "criteria": criteria,
        }
        if incomplete_reason:
            self._evaluation["incomplete_reason"] = incomplete_reason
        if next_investigation_hint:
            self._evaluation["next_investigation_hint"] = next_investigation_hint

    def set_depth_limit_reached(self, round_count):
        """标记已达到 DEEP_DIVE 深度上限。必须先调用 set_evaluation()。"""
        if self._evaluation:
            self._evaluation["depth_limit_reached"] = True
            self._evaluation["investigation_rounds"] = round_count

    def _validate(self):
        """Validate all required fields and content minimums. Returns list of errors."""
        errors = []

        # Required methods
        if not self._metadata:
            errors.append("Missing: call set_metadata() before save()")
        if not self._root_cause:
            errors.append("Missing: call set_root_cause() before save()")
        if not self._chain_nodes:
            errors.append("Missing: call add_chain_node() at least once before save()")
        if self._conclusion is None:
            errors.append("Missing: call set_conclusion() before save()")

        # Content minimums
        if self._root_cause:
            summary_len = len(self._root_cause.get("summary", ""))
            if summary_len < _MIN_ROOT_CAUSE_SUMMARY:
                errors.append(
                    f"root_cause.summary too short (min {_MIN_ROOT_CAUSE_SUMMARY} chars), "
                    f"got {summary_len} chars"
                )

        # Chain node validation
        has_root_cause_node = False
        for i, node in enumerate(self._chain_nodes):
            if node["node_type"] == "root_cause":
                has_root_cause_node = True
            evidence_len = len(node.get("evidence", ""))
            if evidence_len < _MIN_EVIDENCE_LENGTH:
                errors.append(
                    f"chain_node[{i}].evidence too short (min {_MIN_EVIDENCE_LENGTH} chars), "
                    f"got {evidence_len} chars"
                )

        if self._chain_nodes and not has_root_cause_node:
            errors.append("Must have at least one chain_node with node_type='root_cause'")

        if self._evaluation and not self._evaluation.get("complete", True):
            if not self._evaluation.get("incomplete_reason"):
                errors.append("evaluation.complete=False requires incomplete_reason")

            # DEEP_DIVE 强制检查点：incomplete 报告必须有 depth_limit_reached 或 dimensions_not_available
            depth_reached = self._evaluation.get("depth_limit_reached", False)
            has_unavailable = len(self._dimensions_not_available) > 0
            if not depth_reached and not has_unavailable:
                errors.append(
                    "DEEP_DIVE required: evaluation incomplete but no depth_limit_reached "
                    "and no dimensions_not_available. Must perform DEEP_DIVE iterations "
                    "or document which data sources were tried and failed."
                )

        # Timeline 严格升序（对应 Review T1：相邻事件时间递增）
        # 拦住 Investigation LLM 写错 timestamp 顺序的低级错误，避免转到 Review 才发现。
        prev_ts = None
        prev_idx = -1
        for i, event in enumerate(self._timeline):
            ts = _parse_iso_timestamp(event.get("time"))
            if ts is None:
                continue  # 无法解析的条目跳过（旧 report 兼容）
            if prev_ts is not None and ts < prev_ts:
                errors.append(
                    f"timeline[{i}] not in ascending order: "
                    f"{event.get('time')} < timeline[{prev_idx}] {self._timeline[prev_idx].get('time')}"
                )
            prev_ts = ts
            prev_idx = i

        # Causal chain 因果时序（对应 Review T2：父节点时间 ≤ 子节点时间）
        # 父节点是被 caused_by 引用的节点，如果父节点 timestamp 晚于当前节点，说明
        # Investigation 把"结果"标成了"原因"（反向因果）。
        for i, node in enumerate(self._chain_nodes):
            cur_ts = _parse_iso_timestamp(node.get("timestamp"))
            if cur_ts is None:
                continue  # 本节点无 timestamp，无法校验
            caused_by = node.get("caused_by")
            if not caused_by:
                continue
            for pidx in caused_by:
                if not isinstance(pidx, int) or pidx < 0 or pidx >= len(self._chain_nodes):
                    continue  # 越界引用由其它校验覆盖
                parent = self._chain_nodes[pidx]
                parent_ts = _parse_iso_timestamp(parent.get("timestamp"))
                if parent_ts is None:
                    continue
                if parent_ts > cur_ts:
                    errors.append(
                        f"causal_chain[{i}].caused_by[{pidx}]: parent timestamp "
                        f"{parent.get('timestamp')} is later than child "
                        f"{node.get('timestamp')} — cause must precede effect"
                    )

        return errors

    def _build_dict(self):
        """Assemble the report dict."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        solutions = {
            "short_term": self._solutions_short,
            "long_term": self._solutions_long,
        }
        if not self._solutions_short:
            solutions["_no_action"] = True

        report = {
            "correlation_group": self._cg_id,
            "title": self._metadata["title"],
            "severity": self._metadata["severity"],
            "investigated_at": now,
            "alert_summary": {
                "service": self._metadata["service"],
                "environment": self._metadata["environment"],
                "alert_count": self._metadata.get("alert_count", 1),
                "triggered_at": self._metadata.get("triggered_at"),
                "pagerduty_url": self._metadata.get("pagerduty_url"),
                "duration": self._metadata.get("duration"),
                "status": self._metadata.get("status"),
            },
            "root_cause": self._root_cause,
            "causal_chain": {
                "chain": self._chain_nodes,
                **({"evaluation": self._evaluation} if self._evaluation else {}),
            },
            "timeline": self._timeline,
            "trend_data": self._trend_data,
            "solutions": solutions,
            "conclusion": self._conclusion,
            "dimensions_not_available": self._dimensions_not_available,
        }
        if self._impact:
            report["impact"] = self._impact
        if self._risk_assessment:
            report["risk_assessment"] = self._risk_assessment

        return report

    def save(self):
        """Validate and write report.yaml. Returns True on success, False on validation failure."""
        errors = self._validate()
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print(f"  - {e}")
            return False

        os.makedirs(self._output_dir, exist_ok=True)
        report = self._build_dict()
        path = os.path.join(self._output_dir, "report.yaml")
        with open(path, "w") as f:
            yaml.dump(report, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"Report saved to {path}")
        return True
