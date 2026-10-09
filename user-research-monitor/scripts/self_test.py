"""Offline behavioral checks for the generic monitor engines."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import yaml

try:
    from scripts import enrich_schema, ingest_forms, quality_engine, render_report
except ModuleNotFoundError:
    import enrich_schema
    import ingest_forms
    import quality_engine
    import render_report


def _answer(*values: str) -> dict:
    return {"textAnswers": {"answers": [{"value": value} for value in values]}}


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def run() -> dict:
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="user-research-monitor-") as temp:
        project = Path(temp)
        config = project / "config"
        raw = project / "snapshot" / "raw" / "2026-01-01T00-00"
        output = project / "snapshot"
        raw.mkdir(parents=True)
        _write_yaml(config / "cohort_mapping.yaml", {
            "research_batch": "generic-fixture",
            "output_root": "snapshot",
            "cohorts": [{"cohort_key": "segment_a", "business_label": "Segment A",
                         "source": {"type": "typeform", "form_id": "fixture"}}],
        })
        assert ingest_forms.get_snapshot_root(project, "2026-01-01") == (
            project.resolve() / "snapshot" / "2026-01-01-generic-fixture"
        )
        unsafe_mapping = {
            "research_batch": "generic-fixture",
            "output_root": "../outside",
            "cohorts": [{"cohort_key": "segment_a", "business_label": "Segment A",
                         "source": {"type": "typeform", "form_id": "fixture"}}],
        }
        _write_yaml(config / "cohort_mapping.yaml", unsafe_mapping)
        try:
            ingest_forms.load_cohort_mapping(project)
            raise AssertionError("output_root traversal was accepted")
        except ValueError:
            checks.append("output_root traversal is blocked")
        unsafe_mapping["output_root"] = "snapshot"
        _write_yaml(config / "cohort_mapping.yaml", unsafe_mapping)
        _write_yaml(config / "discovered_schema.yaml", {
            "cohorts": {"segment_a": {"questions": {
                "provider_required": {"title": "Required", "logical_qid": "CORE"},
                "provider_channels": {"title": "Channels", "logical_qid": "CHANNELS"},
                "provider_top": {"title": "Top", "logical_qid": "TOP"},
                "provider_bottom": {"title": "Bottom", "logical_qid": "BOTTOM"},
            }}},
        })
        rules = {
            "version": 1,
            "policy": "flag_only",
            "rules": [
                {"id": "missing", "type": "required_missing", "label": "Missing",
                 "severity": "S0", "params": {"logical_qids": ["CORE"]}},
                {"id": "exclusive", "type": "exclusive_choice_mixed", "label": "Exclusive",
                 "severity": "S1", "params": {
                     "logical_qid": "CHANNELS", "exclusive_values": ["None"]}},
                {"id": "overlap", "type": "choice_overlap", "label": "Overlap",
                 "severity": "S2", "params": {
                     "left_logical_qid": "TOP", "right_logical_qid": "BOTTOM"}},
            ],
        }
        _write_yaml(config / "quality_rules.yaml", rules)
        (raw / "segment_a.json").write_text(json.dumps({
            "responses": [{
                "responseId": "response-1",
                "createTime": "2026-01-01T00:00:00Z",
                "lastSubmittedTime": "2026-01-01T00:02:00Z",
                "answers": {
                    "provider_channels": _answer("None", "Email"),
                    "provider_top": _answer("Concept A"),
                    "provider_bottom": _answer("Concept A"),
                },
            }],
        }), encoding="utf-8")
        result = quality_engine.run_for_snapshot(project, raw, output)
        hits = result["results"][0]["triggers"]["response-1"]
        assert {hit["rule"] for hit in hits} == {"missing", "exclusive", "overlap"}
        assert result["results"][0]["conservative_drop"] == []
        assert result["manifest"]["quality_rules_sha256"]
        checks.append("generic rules execute and flag_only never drops")

        rules["policy"] = "dual_threshold"
        _write_yaml(config / "quality_rules.yaml", rules)
        result = quality_engine.run_for_snapshot(project, raw, output)
        assert result["results"][0]["conservative_drop"] == ["response-1"]
        assert result["results"][0]["strict_drop"] == ["response-1"]
        checks.append("dual_threshold applies severity policy")
        health = render_report.render_health_report(project, output)
        assert "数据健康度报告" in health
        checks.append("health report requires and consumes the run manifest")

        bad = dict(rules)
        bad["rules"] = [{
            "id": "unknown", "type": "project_magic", "label": "Unknown",
            "severity": "S2", "params": {},
        }]
        try:
            quality_engine.validate_rules_config(bad)
            raise AssertionError("unknown rule type was accepted")
        except quality_engine.QualityConfigurationError:
            checks.append("unknown rule types fail closed")

        missing_map = dict(rules)
        missing_map["rules"] = [{
            "id": "missing-map", "type": "required_missing", "label": "Missing map",
            "severity": "S2", "params": {"logical_qids": ["NOT_MAPPED"]},
        }]
        try:
            quality_engine.evaluate_cohort(
                "segment_a", {"responses": []}, missing_map,
                {"CORE": "provider_required"}, {}, set()
            )
            raise AssertionError("unmapped logical qid was accepted")
        except quality_engine.QualityConfigurationError:
            checks.append("unmapped rule targets fail closed")

        _write_yaml(config / "schema_overrides.yaml", {
            "version": 1,
            "logical_questions": {
                "FIRST": {"exact_titles": ["Same title"]},
                "SECOND": {"exact_titles": [" same   title "]},
            },
        })
        try:
            enrich_schema.enrich(project)
            raise AssertionError("ambiguous exact title was accepted")
        except enrich_schema.SchemaMappingError:
            checks.append("ambiguous schema mappings fail closed")

    return {"status": "pass", "checks": checks, "count": len(checks)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline user-research-monitor checks")
    parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
