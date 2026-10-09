"""Deterministic, project-configured survey response quality checks.

The engine contains no survey question IDs, feature names, cohort meanings, or
project thresholds. Every enabled check is declared in
``<project_dir>/config/quality_rules.yaml`` and validated before any response is
classified. Unknown rule types and unresolved question mappings fail closed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_RULE_TYPES = {
    "completion_time_outlier",
    "required_missing",
    "open_text_too_short",
    "open_text_gibberish",
    "scale_uniform",
    "scale_range",
    "scale_mean_conflict",
    "choice_overlap",
    "exclusive_choice_mixed",
    "cohort_answer_mismatch",
    "duplicate_fingerprint",
}
SEVERITIES = {"S0", "S1", "S2"}
POLICIES = {"flag_only", "dual_threshold"}


class QualityConfigurationError(ValueError):
    """Raised when a project quality configuration is incomplete or unsafe."""


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise QualityConfigurationError(f"{path} must contain a YAML mapping")
    return value


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"\{[^{}]+\}|<[^<>]+>", value))
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    return False


def load_rules(project_dir: Path) -> dict:
    path = Path(project_dir) / "config" / "quality_rules.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"quality_rules.yaml not found at {path}; create it from the skill template"
        )
    config = _load_yaml(path)
    validate_rules_config(config)
    return config


def validate_rules_config(config: dict) -> None:
    if config.get("version") != 1:
        raise QualityConfigurationError("quality_rules.yaml version must be 1")
    policy = config.get("policy", "flag_only")
    if policy not in POLICIES:
        raise QualityConfigurationError(f"policy must be one of {sorted(POLICIES)}")
    rules = config.get("rules")
    if not isinstance(rules, list):
        raise QualityConfigurationError("rules must be a list")
    seen: set[str] = set()
    enabled_count = 0
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise QualityConfigurationError(f"rules[{index}] must be a mapping")
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id:
            raise QualityConfigurationError(f"rules[{index}] is missing id")
        if rule_id in seen:
            raise QualityConfigurationError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)
        rule_type = rule.get("type")
        if rule_type not in SUPPORTED_RULE_TYPES:
            raise QualityConfigurationError(
                f"rule {rule_id} has unsupported type {rule_type!r}; "
                f"supported types: {sorted(SUPPORTED_RULE_TYPES)}"
            )
        severity = rule.get("severity", "S2")
        if severity not in SEVERITIES:
            raise QualityConfigurationError(f"rule {rule_id} severity must be S0, S1, or S2")
        if not str(rule.get("label") or "").strip():
            raise QualityConfigurationError(f"rule {rule_id} is missing a user-readable label")
        if rule.get("on_missing", "error") not in {"error", "skip"}:
            raise QualityConfigurationError(
                f"rule {rule_id} on_missing must be error or skip"
            )
        params = rule.get("params") or {}
        if not isinstance(params, dict):
            raise QualityConfigurationError(f"rule {rule_id} params must be a mapping")
        if rule.get("enabled", True):
            enabled_count += 1
            if _contains_placeholder(params):
                raise QualityConfigurationError(f"rule {rule_id} still contains template placeholders")
            _validate_rule_params(rule_id, rule_type, params)
    if enabled_count == 0:
        raise QualityConfigurationError("at least one quality rule must be enabled")


def _require(params: dict, rule_id: str, *keys: str) -> None:
    missing = [key for key in keys if params.get(key) in (None, "", [])]
    if missing:
        raise QualityConfigurationError(f"rule {rule_id} is missing params: {', '.join(missing)}")


def _validate_rule_params(rule_id: str, rule_type: str, params: dict) -> None:
    if rule_type in {
        "required_missing", "open_text_too_short", "open_text_gibberish",
        "scale_uniform", "scale_range",
    }:
        _require(params, rule_id, "logical_qids")
    elif rule_type == "scale_mean_conflict":
        _require(params, rule_id, "scale_logical_qids", "comparison_logical_qid")
        modes = (
            {"mean_gte", "comparison_lte"}.issubset(params),
            {"mean_lte", "comparison_gte"}.issubset(params),
        )
        if sum(modes) != 1:
            raise QualityConfigurationError(
                f"rule {rule_id} must define exactly one high/low conflict threshold pair"
            )
    elif rule_type == "choice_overlap":
        _require(params, rule_id, "left_logical_qid", "right_logical_qid")
    elif rule_type == "exclusive_choice_mixed":
        _require(params, rule_id, "logical_qid", "exclusive_values")
    elif rule_type == "cohort_answer_mismatch":
        _require(params, rule_id, "logical_qid", "allowed_values_by_cohort")
        if not isinstance(params["allowed_values_by_cohort"], dict):
            raise QualityConfigurationError(
                f"rule {rule_id} allowed_values_by_cohort must be a mapping"
            )
    elif rule_type == "duplicate_fingerprint":
        _require(params, rule_id, "logical_qids")
    elif rule_type == "completion_time_outlier":
        if "min_seconds" not in params and "max_seconds" not in params:
            raise QualityConfigurationError(f"rule {rule_id} needs min_seconds and/or max_seconds")
        source = params.get("source", "metadata")
        if source not in {"metadata", "questions"}:
            raise QualityConfigurationError(f"rule {rule_id} source must be metadata or questions")
        if source == "questions":
            _require(params, rule_id, "start_logical_qid", "end_logical_qid")


def load_excluded_response_ids(project_dir: Path) -> set[str]:
    path = Path(project_dir) / "config" / "excluded_responses.yaml"
    if not path.exists():
        return set()
    config = _load_yaml(path)
    return {
        str(item["response_id"])
        for item in (config.get("excluded") or [])
        if isinstance(item, dict) and item.get("response_id")
    }


def load_discovered_schema(project_dir: Path) -> dict:
    path = Path(project_dir) / "config" / "discovered_schema.yaml"
    return _load_yaml(path) if path.exists() else {}


def load_logical_qmap(project_dir: Path) -> dict[str, dict[str, str]]:
    schema = load_discovered_schema(project_dir)
    qmap: dict[str, dict[str, str]] = defaultdict(dict)
    for cohort_key, section in (schema.get("cohorts") or {}).items():
        for provider_qid, question in ((section or {}).get("questions") or {}).items():
            logical_qid = (question or {}).get("logical_qid")
            if logical_qid:
                qmap[str(cohort_key)][str(logical_qid)] = str(provider_qid)
    return dict(qmap)


def load_cohort_label_map(project_dir: Path) -> dict[str, str]:
    path = Path(project_dir) / "config" / "cohort_mapping.yaml"
    if not path.exists():
        return {}
    config = _load_yaml(path)
    return {
        str(item["cohort_key"]): str(item.get("business_label") or item["cohort_key"])
        for item in (config.get("cohorts") or [])
        if isinstance(item, dict) and item.get("cohort_key")
    }


def _answer_values(response: dict, qid: str) -> list[str]:
    answer = (response.get("answers") or {}).get(qid) or {}
    return [
        str(item.get("value"))
        for item in ((answer.get("textAnswers") or {}).get("answers") or [])
        if item.get("value") is not None
    ]


def _answer_text(response: dict, qid: str) -> str:
    return "\n".join(_answer_values(response, qid))


def _number(response: dict, qid: str) -> float | None:
    text = _answer_text(response, qid).strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _parse_time(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _logical_qids(rule: dict) -> list[str]:
    params = rule.get("params") or {}
    keys_by_type = {
        "required_missing": ["logical_qids"],
        "open_text_too_short": ["logical_qids"],
        "open_text_gibberish": ["logical_qids"],
        "scale_uniform": ["logical_qids"],
        "scale_range": ["logical_qids"],
        "scale_mean_conflict": ["scale_logical_qids", "comparison_logical_qid"],
        "choice_overlap": ["left_logical_qid", "right_logical_qid"],
        "exclusive_choice_mixed": ["logical_qid"],
        "cohort_answer_mismatch": ["logical_qid"],
        "duplicate_fingerprint": ["logical_qids"],
    }
    if rule["type"] == "completion_time_outlier" and params.get("source", "metadata") == "questions":
        keys_by_type[rule["type"]] = ["start_logical_qid", "end_logical_qid"]
    values: list[str] = []
    for key in keys_by_type.get(rule["type"], []):
        value = params.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return values


def _resolve_rule(rule: dict, qmap: dict[str, str]) -> dict[str, str] | None:
    missing = [logical for logical in _logical_qids(rule) if logical not in qmap]
    if missing:
        if rule.get("on_missing") == "skip":
            return None
        raise QualityConfigurationError(
            f"rule {rule['id']} references unmapped logical_qids: {', '.join(missing)}; "
            "fix discovered_schema.yaml or set on_missing: skip explicitly"
        )
    return {logical: qmap[logical] for logical in _logical_qids(rule)}


def _is_gibberish(text: str, prompt: str = "") -> bool:
    value = text.strip()
    if not value:
        return False
    if re.fullmatch(r"[\W_]+", value) or re.search(r"(.)\1{3,}", value):
        return True
    return bool(prompt and value.casefold() == prompt.strip().casefold())


def _completion_seconds(response: dict, params: dict, resolved: dict[str, str]) -> float | None:
    if params.get("source", "metadata") == "questions":
        start = _parse_time(_answer_text(response, resolved[params["start_logical_qid"]]))
        end = _parse_time(_answer_text(response, resolved[params["end_logical_qid"]]))
    else:
        start = _parse_time(response.get(params.get("start_field", "createTime")))
        end = _parse_time(response.get(params.get("end_field", "lastSubmittedTime")))
    return None if start is None or end is None or end < start else end - start


def _has_required_data(response: dict, rule: dict, resolved: dict[str, str]) -> bool:
    """Whether this response contains enough data to evaluate a rule.

    A zero count is surfaced in the run manifest so an enabled but inapplicable
    rule cannot look as if it ran successfully.
    """
    params = rule.get("params") or {}
    rule_type = rule["type"]
    if rule_type == "required_missing":
        return True
    if rule_type == "completion_time_outlier":
        return _completion_seconds(response, params, resolved) is not None
    if rule_type in {"open_text_too_short", "open_text_gibberish"}:
        return params.get("include_blank", False) or any(
            _answer_text(response, resolved[logical]).strip()
            for logical in params["logical_qids"]
        )
    if rule_type in {"scale_uniform", "scale_range"}:
        answered = sum(
            _number(response, resolved[logical]) is not None
            for logical in params["logical_qids"]
        )
        return answered >= int(params.get("min_answered", 3))
    if rule_type == "scale_mean_conflict":
        return (
            any(
                _number(response, resolved[logical]) is not None
                for logical in params["scale_logical_qids"]
            )
            and _number(response, resolved[params["comparison_logical_qid"]]) is not None
        )
    if rule_type == "choice_overlap":
        return bool(
            _answer_values(response, resolved[params["left_logical_qid"]])
            and _answer_values(response, resolved[params["right_logical_qid"]])
        )
    if rule_type in {"exclusive_choice_mixed", "cohort_answer_mismatch"}:
        return bool(_answer_values(response, resolved[params["logical_qid"]]))
    return False


def _evaluate_response_rule(
    response: dict,
    cohort_key: str,
    rule: dict,
    resolved: dict[str, str],
    schema: dict,
) -> dict | None:
    params = rule.get("params") or {}
    rule_type = rule["type"]
    details: dict[str, Any] = {}
    if rule_type == "completion_time_outlier":
        seconds = _completion_seconds(response, params, resolved)
        if seconds is None:
            return None
        too_short = params.get("min_seconds") is not None and seconds < float(params["min_seconds"])
        too_long = params.get("max_seconds") is not None and seconds > float(params["max_seconds"])
        if not (too_short or too_long):
            return None
        details = {"seconds": round(seconds, 2), "direction": "short" if too_short else "long"}
    elif rule_type == "required_missing":
        missing = [
            logical for logical in params["logical_qids"]
            if not _answer_text(response, resolved[logical]).strip()
        ]
        if not missing:
            return None
        details = {"missing_logical_qids": missing}
    elif rule_type in {"open_text_too_short", "open_text_gibberish"}:
        hits = []
        for logical in params["logical_qids"]:
            provider_qid = resolved[logical]
            text = _answer_text(response, provider_qid).strip()
            if not text and not params.get("include_blank", False):
                continue
            if rule_type == "open_text_too_short":
                hit = len(text) < int(params.get("min_chars", 3))
            else:
                hit = _is_gibberish(text, (schema.get(provider_qid) or {}).get("title", ""))
            if hit:
                hits.append(logical)
        if len(hits) < int(params.get("min_hits", 1)):
            return None
        details = {"hit_logical_qids": hits, "hit_count": len(hits)}
    elif rule_type in {"scale_uniform", "scale_range"}:
        values = [_number(response, resolved[logical]) for logical in params["logical_qids"]]
        values = [value for value in values if value is not None]
        if len(values) < int(params.get("min_answered", 3)):
            return None
        if rule_type == "scale_uniform":
            unique = len(set(values))
            if unique > int(params.get("max_unique_values", 1)):
                return None
            details = {"answered": len(values), "unique_values": unique}
        else:
            value_range = max(values) - min(values)
            if value_range > float(params.get("max_range", 0)):
                return None
            details = {
                "answered": len(values), "range": value_range,
                "min": min(values), "max": max(values),
            }
    elif rule_type == "scale_mean_conflict":
        values = [_number(response, resolved[logical]) for logical in params["scale_logical_qids"]]
        values = [value for value in values if value is not None]
        comparison = _number(response, resolved[params["comparison_logical_qid"]])
        if not values or comparison is None:
            return None
        mean = sum(values) / len(values)
        high_conflict = (
            "mean_gte" in params and mean >= float(params["mean_gte"])
            and comparison <= float(params["comparison_lte"])
        )
        low_conflict = (
            "mean_lte" in params and mean <= float(params["mean_lte"])
            and comparison >= float(params["comparison_gte"])
        )
        if not (high_conflict or low_conflict):
            return None
        details = {"mean": round(mean, 3), "comparison": comparison}
    elif rule_type == "choice_overlap":
        left = set(_answer_values(response, resolved[params["left_logical_qid"]]))
        right = set(_answer_values(response, resolved[params["right_logical_qid"]]))
        overlap = sorted(left & right)
        if len(overlap) < int(params.get("min_overlap", 1)):
            return None
        details = {"overlap": overlap, "overlap_count": len(overlap)}
    elif rule_type == "exclusive_choice_mixed":
        choices = _answer_values(response, resolved[params["logical_qid"]])
        exclusive_values = set(map(str, params["exclusive_values"]))
        exclusive = [item for item in choices if item in exclusive_values]
        others = [item for item in choices if item not in exclusive_values]
        if not exclusive or not others:
            return None
        details = {"exclusive": exclusive, "other_choices": others}
    elif rule_type == "cohort_answer_mismatch":
        allowed_by_cohort = params["allowed_values_by_cohort"]
        if cohort_key not in allowed_by_cohort:
            if rule.get("on_missing") == "skip":
                return None
            raise QualityConfigurationError(
                f"rule {rule['id']} has no allowed values for cohort {cohort_key}"
            )
        actual = _answer_values(response, resolved[params["logical_qid"]])
        allowed = set(map(str, allowed_by_cohort[cohort_key]))
        if not actual or all(item in allowed for item in actual):
            return None
        details = {"actual": actual, "allowed": sorted(allowed)}
    else:
        return None
    return {
        "rule": rule["id"],
        "type": rule_type,
        "label": rule["label"],
        "severity": rule.get("severity", "S2"),
        "details": details,
    }


def _duplicate_ids(responses: list[dict], rule: dict, resolved: dict[str, str]) -> set[str]:
    params = rule["params"]
    minimum = int(params.get("min_non_empty", 2))
    window = float(params.get("window_seconds", 600))
    groups: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    for response in responses:
        values = [
            _answer_text(response, resolved[logical]).strip()
            for logical in params["logical_qids"]
        ]
        if sum(bool(value) for value in values) < minimum:
            continue
        digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
        groups[digest].append((
            str(response.get("responseId") or ""),
            _parse_time(response.get("lastSubmittedTime")),
        ))
    flagged: set[str] = set()
    for group in groups.values():
        if len(group) < 2 or any(item[1] is None for item in group):
            continue
        group.sort(key=lambda item: item[1])
        for previous, current in zip(group, group[1:]):
            if current[1] - previous[1] <= window:
                flagged.update({previous[0], current[0]})
    return flagged


def evaluate_cohort(
    cohort_key: str,
    raw_data: dict,
    config: dict,
    qmap: dict[str, str],
    schema: dict,
    excluded_ids: set[str] | None = None,
) -> dict:
    excluded_ids = excluded_ids or set()
    responses = [
        response for response in (raw_data.get("responses") or [])
        if response.get("responseId") not in excluded_ids
    ]
    enabled_rules = [rule for rule in config["rules"] if rule.get("enabled", True)]
    resolved_by_rule = {rule["id"]: _resolve_rule(rule, qmap) for rule in enabled_rules}
    skipped_rules = [
        rule["id"] for rule in enabled_rules if resolved_by_rule[rule["id"]] is None
    ]
    triggers: dict[str, list[dict]] = defaultdict(list)
    evaluation_counts = {rule["id"]: 0 for rule in enabled_rules}

    for rule in enabled_rules:
        resolved = resolved_by_rule[rule["id"]]
        if resolved is None or rule["type"] != "duplicate_fingerprint":
            continue
        eligible = [
            response for response in responses
            if sum(
                bool(_answer_text(response, resolved[logical]).strip())
                for logical in rule["params"]["logical_qids"]
            ) >= int(rule["params"].get("min_non_empty", 2))
        ]
        evaluation_counts[rule["id"]] = len(eligible)
        for response_id in _duplicate_ids(responses, rule, resolved):
            triggers[response_id].append({
                "rule": rule["id"], "type": rule["type"], "label": rule["label"],
                "severity": rule.get("severity", "S2"),
                "details": {"matched_fingerprint": True},
            })

    for response in responses:
        response_id = str(response.get("responseId") or "")
        if not response_id:
            continue
        for rule in enabled_rules:
            resolved = resolved_by_rule[rule["id"]]
            if resolved is None or rule["type"] == "duplicate_fingerprint":
                continue
            if _has_required_data(response, rule, resolved):
                evaluation_counts[rule["id"]] += 1
            hit = _evaluate_response_rule(response, cohort_key, rule, resolved, schema)
            if hit:
                triggers[response_id].append(hit)

    conservative_drop: set[str] = set()
    strict_drop: set[str] = set()
    if config.get("policy", "flag_only") == "dual_threshold":
        for response_id, hits in triggers.items():
            severities = {hit["severity"] for hit in hits}
            if "S0" in severities:
                conservative_drop.add(response_id)
                strict_drop.add(response_id)
            elif "S1" in severities:
                strict_drop.add(response_id)
    return {
        "cohort_key": cohort_key,
        "total_responses": len(responses),
        "triggers": dict(triggers),
        "conservative_drop": sorted(conservative_drop),
        "strict_drop": sorted(strict_drop),
        "policy": config.get("policy", "flag_only"),
        "skipped_rules": skipped_rules,
        "rule_evaluation_counts": evaluation_counts,
    }


def write_cleaned(
    cohort_key: str, raw_data: dict, drop_set: set[str], out_path: Path
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "form_meta": raw_data.get("form_meta", {}),
        "responses": [
            response for response in (raw_data.get("responses") or [])
            if response.get("responseId") not in drop_set
        ],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_review_tabs(
    results: list[dict], raw_by_cohort: dict, out_dir: Path, labels: dict[str, str]
) -> int:
    detail_rows: list[dict] = []
    summary_rows: list[dict] = []
    for result in results:
        cohort_key = result["cohort_key"]
        by_id = {
            str(response.get("responseId")): response
            for response in (raw_by_cohort.get(cohort_key, {}).get("responses") or [])
            if response.get("responseId")
        }
        for response_id, hits in result["triggers"].items():
            response = by_id.get(response_id, {})
            for hit in hits:
                action = "flag_only"
                if result["policy"] == "dual_threshold":
                    action = {
                        "S0": "drop_both", "S1": "drop_strict", "S2": "flag_only"
                    }[hit["severity"]]
                detail_rows.append({
                    "cohort_key": cohort_key,
                    "response_id": response_id,
                    "create_time": response.get("createTime", ""),
                    "rule": hit["rule"],
                    "type": hit["type"],
                    "label": hit["label"],
                    "severity": hit["severity"],
                    "details": json.dumps(
                        hit["details"], ensure_ascii=False, sort_keys=True
                    ),
                    "action": action,
                    "review_decision": "",
                })
            highest = min(
                (hit["severity"] for hit in hits), key=lambda value: int(value[1])
            )
            summary_rows.append({
                "分群": labels.get(cohort_key, cohort_key),
                "提交时间": response.get("lastSubmittedTime")
                or response.get("createTime", ""),
                "回答ID": response_id,
                "最高风险等级": highest,
                "命中规则": "；".join(hit["label"] for hit in hits),
                "证据": "；".join(
                    json.dumps(hit["details"], ensure_ascii=False, sort_keys=True)
                    for hit in hits
                ),
                "人工决定（keep/drop）": "",
            })
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_fields = [
        "cohort_key", "response_id", "create_time", "rule", "type", "label",
        "severity", "details", "action", "review_decision",
    ]
    with (out_dir / "review_tab_detail.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(detail_rows)
    summary_fields = [
        "分群", "提交时间", "回答ID", "最高风险等级", "命中规则",
        "证据", "人工决定（keep/drop）",
    ]
    with (out_dir / "review_tab.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    return len(summary_rows)


def run_for_snapshot(project_dir: Path, snapshot_dir: Path, out_root: Path) -> dict:
    config = load_rules(project_dir)
    schema_document = load_discovered_schema(project_dir)
    qmaps = load_logical_qmap(project_dir)
    excluded_ids = load_excluded_response_ids(project_dir)
    labels = load_cohort_label_map(project_dir)
    raw_by_cohort: dict[str, dict] = {}
    results: list[dict] = []
    for raw_file in sorted(snapshot_dir.glob("*.json")):
        cohort_key = raw_file.stem
        raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
        raw_by_cohort[cohort_key] = raw_data
        cohort_schema = (
            (((schema_document.get("cohorts") or {}).get(cohort_key) or {})
             .get("questions") or {})
        )
        if cohort_key not in qmaps:
            raise QualityConfigurationError(
                f"no logical question mapping found for cohort {cohort_key}"
            )
        result = evaluate_cohort(
            cohort_key, raw_data, config, qmaps[cohort_key],
            cohort_schema, excluded_ids
        )
        results.append(result)
        write_cleaned(
            cohort_key, raw_data,
            set(result["conservative_drop"]) | excluded_ids,
            out_root / "cleaned" / "conservative" / f"{cohort_key}.json",
        )
        write_cleaned(
            cohort_key, raw_data,
            set(result["strict_drop"]) | excluded_ids,
            out_root / "cleaned" / "strict" / f"{cohort_key}.json",
        )
    review_count = write_review_tabs(results, raw_by_cohort, out_root, labels)
    config_bytes = (
        Path(project_dir) / "config" / "quality_rules.yaml"
    ).read_bytes()
    manifest = {
        "engine_version": 1,
        "quality_rules_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "policy": config.get("policy", "flag_only"),
        "cohorts": [
            {
                "cohort_key": result["cohort_key"],
                "responses": result["total_responses"],
                "flagged": len(result["triggers"]),
                "skipped_rules": result["skipped_rules"],
                "rule_evaluation_counts": result["rule_evaluation_counts"],
                "unevaluable_rules": sorted(
                    rule_id
                    for rule_id, count in result["rule_evaluation_counts"].items()
                    if count == 0 and rule_id not in result["skipped_rules"]
                ),
            }
            for result in results
        ],
    }
    (out_root / "quality_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"results": results, "review_count": review_count, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run project-configured survey quality checks"
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    args = parser.parse_args()
    result = run_for_snapshot(args.project_dir, args.snapshot_dir, args.out_root)
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
