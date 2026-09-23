from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prune_artifacts.py"
SPEC = importlib.util.spec_from_file_location("prune_artifacts", SCRIPT)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


def _age(path: Path, *, now: datetime, days: int) -> None:
    timestamp = (now - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def _durable_gate() -> dict:
    dimensions = {
        "scene_reconstruction": 4,
        "evidence_traceability": 4,
        "recommendation_actionability": 4,
        "skill_decision_accuracy": 4,
        "human_readability": 4,
    }
    gates = {
        "scene_reconstruction": True,
        "evidence_traceability": True,
        "skill_boundary": True,
    }
    baseline = {
        "report_digest": "sha256:" + "a" * 64,
        "input_digest": "sha256:" + "b" * 64,
        "input_characters": 20,
        "quality_dimensions": dimensions,
        "required_claim_count": 1,
        "unsupported_claim_count": 0,
        "privacy_violation_count": 0,
        "critical_gates": gates,
    }
    candidate = dict(baseline)
    candidate["input_characters"] = 10
    return {
        "schema": "addx.work_method_quality_gate.v1",
        "snapshot_id": "snapshot",
        "filter_hash": retention.VALIDATOR._current_filter_hash(),
        "selection_policy_hash": retention.VALIDATOR._current_selection_policy_hash(),
        "quality_policy_hash": retention.VALIDATOR._current_quality_policy_hash(),
        "rubric_version": "v1",
        "decision": "accept",
        "accepted_at": "2026-09-16T00:00:00Z",
        "accepted_by": "jchen",
        "report_digest": "sha256:" + "f" * 64,
        "approval_provenance": {
            "receipt_digest": "sha256:" + "6" * 64,
            "round_id": "round-1",
            "author": "jchen",
            "week": "2026-W38",
            "weekly_report_digest": "sha256:" + "7" * 64,
            "approved_by": "jchen",
            "accepted_at": "2026-09-16T00:00:00Z",
        },
        "quality_comparison": {
            "method": "blind_side_by_side",
            "metric": "input_characters",
            "snapshot_digest": "sha256:" + "1" * 64,
            "quality_review_digest": "sha256:" + "f" * 64,
            "quality_review_ref": "quality/review.json",
            "packet_digest": "sha256:" + "2" * 64,
            "claim_ledger_digest": "sha256:" + "3" * 64,
            "label_map_digest": "sha256:" + "4" * 64,
            "baseline_label": "A",
            "candidate_label": "B",
            "required_claim_ids": ["claim"],
            "reviewer": {
                "provider": "litellm",
                "model": "grader",
                "request_id": "review-request",
            },
            "baseline": baseline,
            "candidate": candidate,
        },
    }


def test_artifact_retention_uses_28_day_ledger_and_35_day_round_boundaries(tmp_path):
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    root = tmp_path / "artifacts" / "work-method-retrospective"
    retention.prepare_root(root)
    ledger = root / "ledger" / "candidate.json"
    round_file = root / "round-1" / "review.md"
    old_input = root / "round-1" / "quality" / "baseline-input.txt"
    accepted_gate = root / "quality-gates" / "accepted.json"
    invalid_semantic_gate = root / "quality-gates" / "invalid-semantic.json"
    leaked_gate = root / "quality-gates" / "raw-session.txt"
    malformed_gate = root / "quality-gates" / "malformed.json"
    nested_gate_name = root / "round-1" / "quality-gates" / "must-expire.txt"
    fresh = root / "round-2" / "review.md"
    for path in (
        ledger,
        round_file,
        old_input,
        accepted_gate,
        invalid_semantic_gate,
        leaked_gate,
        malformed_gate,
        nested_gate_name,
        fresh,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")
        path.chmod(0o600)
    import json

    accepted_gate.write_text(json.dumps(_durable_gate()) + "\n", encoding="utf-8")
    accepted_gate.chmod(0o600)
    invalid_semantic = _durable_gate()
    invalid_semantic["quality_comparison"]["candidate"]["input_characters"] = 20
    invalid_semantic_gate.write_text(
        json.dumps(invalid_semantic) + "\n", encoding="utf-8"
    )
    invalid_semantic_gate.chmod(0o600)
    malformed_gate.write_text(
        '{"schema":"addx.work_method_quality_gate.v1","raw_session":"must expire"}\n',
        encoding="utf-8",
    )
    malformed_gate.chmod(0o600)
    _age(ledger, now=now, days=29)
    _age(round_file, now=now, days=36)
    _age(old_input, now=now, days=36)
    _age(accepted_gate, now=now, days=180)
    _age(invalid_semantic_gate, now=now, days=180)
    _age(leaked_gate, now=now, days=180)
    _age(malformed_gate, now=now, days=180)
    _age(nested_gate_name, now=now, days=36)
    _age(fresh, now=now, days=34)

    expired = retention.expired_artifacts(root, now=now)

    assert expired == [
        ledger,
        invalid_semantic_gate,
        malformed_gate,
        leaked_gate,
        old_input,
        nested_gate_name,
        round_file,
    ]
    assert accepted_gate not in expired
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / retention.MARKER).stat().st_mode & 0o777 == 0o600


def test_artifact_retention_rejects_an_intermediate_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "workspace" / "artifacts"
    linked_parent.parent.mkdir()
    linked_parent.symlink_to(outside, target_is_directory=True)
    root = linked_parent / "work-method-retrospective"

    with pytest.raises(ValueError, match="symlink component"):
        retention.prepare_root(root)

    assert not (outside / "work-method-retrospective" / retention.MARKER).exists()


def test_artifact_retention_refuses_to_adopt_a_nonempty_unmarked_root(tmp_path):
    root = tmp_path / "existing-project"
    root.mkdir(mode=0o755)
    sentinel = root / "important.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    original_mode = root.stat().st_mode & 0o777

    with pytest.raises(ValueError, match="nonempty unmarked"):
        retention.prepare_root(root)

    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert root.stat().st_mode & 0o777 == original_mode
    assert not (root / retention.MARKER).exists()
