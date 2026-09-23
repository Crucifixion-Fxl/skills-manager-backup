#!/usr/bin/env python3
"""
CompletionGate — Dispatcher-side output validation (Layer 3).
Validates subagent output files (report.yaml, review.yaml, result.json, pattern_candidate.yaml)
for structural completeness. Complements Builder validation (Layer 2).
"""

from report_builder import get_required_solution_fields


class CompletionGate:

    _VALID_STATUSES = {"transient_event", "self_healed", "ongoing", "resolved"}

    @staticmethod
    def validate_report(report: dict) -> tuple:
        """Validate report.yaml completeness."""
        errors = []
        if not report.get("root_cause"):
            errors.append("missing root_cause")
        summary = report.get("root_cause", {}).get("summary", "")
        if not summary:
            errors.append("missing root_cause.summary")
        chain = report.get("causal_chain", {}).get("chain", [])
        if not chain:
            errors.append("empty causal_chain")
        if not any(n.get("node_type") == "root_cause" for n in chain):
            errors.append("no root_cause node in causal_chain")
        if not report.get("conclusion"):
            errors.append("missing conclusion")
        if report.get("severity") not in ("critical", "high", "medium", "low"):
            errors.append(f"invalid severity: {report.get('severity')}")
        status = report.get("alert_summary", {}).get("status")
        if not status:
            errors.append("missing alert_summary.status")
        elif status not in CompletionGate._VALID_STATUSES:
            errors.append(f"invalid alert_summary.status: {status}, "
                          f"must be one of {sorted(CompletionGate._VALID_STATUSES)}")

        # Solution 字段完整性校验(SOLUTION_SCHEMA 是唯一事实来源)
        solutions = report.get("solutions") or {}
        if not solutions.get("_no_action"):
            short_term = solutions.get("short_term") or []
            short_required = get_required_solution_fields("short")
            for i, sol in enumerate(short_term):
                missing = [f for f in short_required if sol.get(f) is None]
                if missing:
                    errors.append(
                        f"solutions.short_term[{i}] missing required fields {missing}"
                    )
            long_term = solutions.get("long_term") or []
            long_required = get_required_solution_fields("long")
            for i, sol in enumerate(long_term):
                missing = [f for f in long_required if sol.get(f) is None]
                if missing:
                    errors.append(
                        f"solutions.long_term[{i}] missing required fields {missing}"
                    )

        return (len(errors) == 0, errors)

    @staticmethod
    def validate_review(review: dict) -> tuple:
        """Validate review.yaml completeness. Checks 3 core sections (L3 is more lenient than L2 ReviewBuilder which checks 5)."""
        errors = []
        if not review.get("overall_verdict"):
            errors.append("missing overall_verdict")
        if review.get("overall_verdict") not in ("approved", "needs_revision"):
            errors.append(f"invalid overall_verdict: {review.get('overall_verdict')}")
        for section in ("root_cause_review", "timeline_review", "evidence_review"):
            if not review.get(section, {}).get("checkpoints"):
                errors.append(f"missing {section}.checkpoints")
        return (len(errors) == 0, errors)

    @staticmethod
    def validate_result(result: dict) -> tuple:
        """Validate result.json completeness."""
        errors = []
        if result.get("status") not in ("success", "failure", "skipped_self_healed"):
            errors.append(f"invalid status: {result.get('status')}")
        if not result.get("summary"):
            errors.append("missing summary")
        return (len(errors) == 0, errors)

    @staticmethod
    def validate_pattern(data: dict) -> tuple:
        """Validate pattern_candidate.yaml completeness.
        Note: PatternBuilder nests pattern fields under top-level 'pattern' key.
        """
        errors = []
        pattern = data.get("pattern")
        if not pattern:
            errors.append("missing top-level 'pattern' key")
            return (False, errors)
        if not pattern.get("pattern_id"):
            errors.append("missing pattern.pattern_id")
        if not pattern.get("match_conditions"):
            errors.append("missing pattern.match_conditions")
        if not pattern.get("execution_steps"):
            errors.append("missing pattern.execution_steps")
        if pattern.get("risk") not in ("critical", "high", "medium", "low"):
            errors.append(f"invalid pattern.risk: {pattern.get('risk')}")
        return (len(errors) == 0, errors)
