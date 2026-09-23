from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "validate_manifest.py"
QUALITY_WRITER = SKILL_ROOT / "scripts" / "write_quality_gate.py"
REPORT_CONTRACT = SKILL_ROOT / "references" / "report-contract.md"
WEEKLY_SKILL = SKILL_ROOT.parent / "weekly-report" / "SKILL.md"
FILTER_SOURCES = (
    SKILL_ROOT / "filters" / "claude-session-evidence.jq",
    SKILL_ROOT / "filters" / "codex-session-evidence.jq",
    SKILL_ROOT / "scripts" / "extract_session_evidence.sh",
    SKILL_ROOT / "scripts" / "build_scene_ledger.py",
)
SELECTION_POLICY = SKILL_ROOT / "policies" / "scene-selection-v1.json"
QUALITY_POLICY = SKILL_ROOT / "policies" / "report-quality-v1.json"
QUALITY_POLICY_SOURCES = (
    SKILL_ROOT / "policies" / "report-quality-v1.json",
    SKILL_ROOT / "evals" / "evals.json",
)
BASELINE_REPORT = b"baseline report"
CANDIDATE_REPORT = b"candidate report"
WEEKLY_REPORT = b"<html>private weekly report draft</html>"
WEEKLY_REPORT_DIGEST = "sha256:" + hashlib.sha256(WEEKLY_REPORT).hexdigest()
REVIEW_TASK = (
    json.dumps(
        {
            "schema": "addx.work_method_review_task.v1",
            "round_id": "round-1",
            "ruleset_id": "rules-v1",
            "author": "jchen",
            "week": "2026-W38",
            "weekly_report_digest": WEEKLY_REPORT_DIGEST,
            "status": "awaiting_human_review",
            "title": "Review this work-method retrospective round",
            "checklist": [
                "Verify every conclusion against its scene evidence.",
                "Accept or propose a rule change for the next round.",
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    + "\n"
).encode()


def _frozen_scene(scene_id: str, observed_at: str, characters: int) -> dict:
    records = []
    remaining = characters
    index = 0
    while remaining:
        chunk_size = min(4000, remaining)
        record = {
            "kind": "ai_output",
            "provider": "codex",
            "at": observed_at,
            "session_kind": "root",
            "trust": "untrusted_evidence",
            "phase": f"chunk-{index}",
            "text": "b" * chunk_size,
            "text_truncated": chunk_size == 4000,
        }
        canonical = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        records.append(
            {
                "record_digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
                "record_line": index + 1,
                "kind": record["kind"],
                "provider": record["provider"],
                "at": record["at"],
                "session_kind": record["session_kind"],
                "session_id": None,
            }
        )
        remaining -= chunk_size
        index += 1
    return {
        "id": scene_id,
        "content": "b" * characters,
        "observed_at": observed_at,
        "weekly_report_id": "2026-W38",
        "source_records": records,
    }


FROZEN_SCENES = [
    _frozen_scene("scene-a", "2026-09-14T10:00:00Z", 200_000),
    _frozen_scene("scene-b", "2026-09-15T10:00:00Z", 100_000),
    _frozen_scene("scene-c", "2026-09-16T10:00:00Z", 100_000),
]
BASELINE_INPUT = "".join(scene["content"] for scene in FROZEN_SCENES)
CANDIDATE_INPUT = FROZEN_SCENES[0]["content"][:90_000]
FROZEN_SNAPSHOT = (
    json.dumps(
        {"schema": "addx.work_method_frozen_snapshot.v1", "scenes": FROZEN_SCENES},
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
).encode()
FROZEN_CLAIMS = (
    b'[{"id":"claim-goal","claim":"The task goal is recoverable."},'
    b'{"id":"claim-turning-point","claim":"The turning point is evidenced."},'
    b'{"id":"claim-result","claim":"The verified result is stated."}]\n'
)


def _filter_hash() -> str:
    digest = hashlib.sha256()
    for path in FILTER_SOURCES:
        digest.update(path.relative_to(SKILL_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _selection_policy_hash() -> str:
    return "sha256:" + hashlib.sha256(SELECTION_POLICY.read_bytes()).hexdigest()


def _quality_policy_hash() -> str:
    digest = hashlib.sha256()
    for path in QUALITY_POLICY_SOURCES:
        digest.update(path.relative_to(SKILL_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _load_validator(script: Path = SCRIPT):
    spec = importlib.util.spec_from_file_location("validate_manifest", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("validator module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    temporary = tempfile.TemporaryDirectory()
    # Resolve the temp prefix: macOS makes /var and /tmp OS-owned symlinks, and
    # the validator rejects symlink components anywhere in an artifact root. The
    # security check is correct; the test just must not hand it an OS-symlinked
    # path, or the whole suite is unrunnable outside Linux.
    artifact_root = Path(os.path.realpath(temporary.name))
    _write_quality_artifacts(artifact_root, _efficiency_eval())
    structural_validate = module.validate_manifest

    def validate_with_artifacts(document):
        return structural_validate(document, artifact_root=artifact_root)

    module.validate_manifest = validate_with_artifacts
    module._test_artifact_root = artifact_root
    module._test_temporary = temporary
    return module


def _load_quality_writer():
    spec = importlib.util.spec_from_file_location("write_quality_gate", QUALITY_WRITER)
    if spec is None or spec.loader is None:
        raise RuntimeError("quality gate writer could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(*, outcome: str = "extend-existing-under-review") -> dict:
    testing_skill = SKILL_ROOT.parent / "testing-strategy" / "SKILL.md"
    return {
        "id": "SP-1",
        "method": "turn evaluator gaps into regression assets",
        "outcome": outcome,
        "independent_task_ids": ["task-a", "task-b", "task-c"],
        "task_evidence": [
            {"task_id": "task-a", "evidence_refs": ["scene-a#turn-2"]},
            {"task_id": "task-b", "evidence_refs": ["scene-b#turn-4"]},
            {"task_id": "task-c", "evidence_refs": ["scene-c#turn-3"]},
        ],
        "contexts": ["software delivery", "data analysis"],
        "human_judgment": "The owner decides what real success means.",
        "state_delta_evidence": [
            "scene-a#turn-2",
            "scene-b#turn-4",
            "scene-c#turn-3",
        ],
        "existing_skill_coverage": {
            "status": "verified",
            "matches": ["testing-strategy: partial"],
        },
        "addx_skill_comparison": [
            {
                "skill": "testing-strategy",
                "path": "skills/testing-strategy/SKILL.md",
                "skill_digest": "sha256:"
                + hashlib.sha256(testing_skill.read_bytes()).hexdigest(),
                "coverage": "partial",
                "overlap": "Turns right-side software defects into lower-layer tests.",
                "gap": "Does not cover data reports or Skill rule evaluators.",
            }
        ],
        "change_decision": {
            "action": "update",
            "targets": ["testing-strategy"],
            "rationale": "The existing Skill owns the closest trigger and output.",
        },
        "trigger": "Observed reality contradicts a passing evaluator.",
        "non_trigger": "A deterministic defect already has a sufficient regression test.",
        "stable_output": "An eval-gap ledger with a new fixture and rerun evidence.",
        "stop_condition": "The old and new fixtures pass and remain traceable.",
        "fixtures": {
            "positive": ["a real journey disproves a technical success state"],
            "negative": ["many turns caused only by a broken tool"],
            "boundary": ["one isolated incident without recurrence"],
        },
    }


def _manifest() -> dict:
    return {
        "schema": "addx.work_method_retrospective.v2",
        "round_id": "round-1",
        "ruleset_id": "rules-v1",
        "input": {
            "scene_ids": ["scene-a", "scene-b", "scene-c"],
            "source_refs": ["report.md#scene-a"],
            "evidence_cutoff_at": "2026-09-15T09:30:53Z",
        },
        "tasks": {
            "eval_driven_review": {
                "findings": [
                    {
                        "id": "ED-1",
                        "claim": "The owner raises the oracle after real use.",
                        "evidence_refs": ["scene-a#turn-2"],
                        "confidence": "high",
                    }
                ],
                "recommendations": [
                    {
                        "id": "ED-R1",
                        "finding_ids": ["ED-1"],
                        "action": "Record the new fixture and its rerun evidence.",
                    }
                ],
            },
            "skill_proposal_discovery": {"candidates": [_candidate()]},
        },
        "inner_loop": {
            "rules_frozen": True,
            "checks": [
                {"id": "evidence", "status": "pass", "evidence": "all claims linked"}
            ],
            "corrections": [],
        },
        "outer_loop": {
            "status": "awaiting_human_review",
            "review_task": "review-task.md",
        },
        "publication": {"status": "local_only"},
    }


def _efficiency_eval() -> dict:
    quality = {
        "scene_reconstruction": 4,
        "evidence_traceability": 4,
        "recommendation_actionability": 4,
        "skill_decision_accuracy": 4,
        "human_readability": 4,
    }
    comparison = {
        "mode": "rerun",
        "filter_hash": _filter_hash(),
        "selection_policy_hash": _selection_policy_hash(),
        "quality_policy_hash": _quality_policy_hash(),
        "rubric_version": "work-method-quality-v1",
        "snapshot_id": "golden-scenes-v1",
        "snapshot_ref": "quality/frozen-snapshot.json",
        "snapshot_digest": "sha256:"
        + hashlib.sha256(FROZEN_SNAPSHOT).hexdigest(),
        "comparison_protocol": {
            "method": "blind_side_by_side",
            "same_snapshot": True,
            "judge_input": "reports_claim_ledger_rubric_only",
        },
        "required_claim_ids": ["claim-goal", "claim-turning-point", "claim-result"],
        "baseline": {
            "blind_label": "B",
            "input_characters": len(BASELINE_INPUT),
            "input_tokens": 100000,
            "input_ref": "quality/baseline-input.json",
            "token_receipt_ref": "quality/baseline-token-receipt.json",
            "report_digest": "sha256:" + hashlib.sha256(BASELINE_REPORT).hexdigest(),
            "report_ref": "quality/baseline-report.md",
            "supported_claim_ids": ["claim-goal", "claim-turning-point", "claim-result"],
            "unsupported_claim_ids": [],
            "privacy_violations": [],
            "critical_gates": {
                "scene_reconstruction": True,
                "evidence_traceability": True,
                "skill_boundary": True,
            },
            "quality_dimensions": quality,
            "grader_refs": ["quality-review.md#baseline"],
        },
        "candidate": {
            "blind_label": "A",
            "input_characters": len(CANDIDATE_INPUT),
            "input_tokens": 30000,
            "input_ref": "quality/candidate-input.json",
            "token_receipt_ref": "quality/candidate-token-receipt.json",
            "report_digest": "sha256:" + hashlib.sha256(CANDIDATE_REPORT).hexdigest(),
            "report_ref": "quality/candidate-report.md",
            "supported_claim_ids": ["claim-goal", "claim-turning-point", "claim-result"],
            "unsupported_claim_ids": [],
            "privacy_violations": [],
            "critical_gates": {
                "scene_reconstruction": True,
                "evidence_traceability": True,
                "skill_boundary": True,
            },
            "quality_dimensions": quality,
            "grader_refs": ["quality-review.md#candidate"],
        },
        "decision": "accept",
    }
    for name in ("baseline", "candidate"):
        input_bytes = _model_input_bytes(name, comparison)
        comparison[name]["input_digest"] = (
            "sha256:" + hashlib.sha256(input_bytes).hexdigest()
        )
        receipt_bytes = _token_receipt_bytes(name, comparison)
        comparison[name]["token_receipt_digest"] = (
            "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
        )
    packet_bytes = _blind_input_bytes(comparison)
    map_bytes = _blind_label_map_bytes(comparison, packet_bytes)
    comparison["comparison_protocol"].update(
        {
            "packet_ref": "quality/blind-quality-input.json",
            "packet_digest": "sha256:" + hashlib.sha256(packet_bytes).hexdigest(),
            "label_map_ref": "quality/blind-label-map.json",
            "label_map_digest": "sha256:" + hashlib.sha256(map_bytes).hexdigest(),
            "claim_ledger_ref": "quality/required-claims.json",
            "claim_ledger_digest": "sha256:" + hashlib.sha256(FROZEN_CLAIMS).hexdigest(),
        }
    )
    review_bytes = _blind_review_bytes(comparison)
    comparison["quality_review"] = {
        "artifact_ref": "quality/blind-quality-review.json",
        "artifact_sha256": "sha256:" + hashlib.sha256(review_bytes).hexdigest(),
    }
    return comparison


def _blind_review_bytes(comparison: dict) -> bytes:
    runs = {}
    for name in ("baseline", "candidate"):
        run = comparison[name]
        runs[run["blind_label"]] = {
            "report_digest": run["report_digest"],
            "supported_claim_ids": run["supported_claim_ids"],
            "unsupported_claim_ids": run["unsupported_claim_ids"],
            "privacy_violations": run["privacy_violations"],
            "critical_gates": run["critical_gates"],
            "quality_dimensions": run["quality_dimensions"],
        }
    review = {
        "schema": "addx.work_method_blind_quality_review.v1",
        "snapshot_digest": comparison["snapshot_digest"],
        "packet_digest": comparison["comparison_protocol"]["packet_digest"],
        "rubric_version": comparison["rubric_version"],
        "method": comparison["comparison_protocol"]["method"],
        "judge_input": comparison["comparison_protocol"]["judge_input"],
        "reviewer": {
            "provider": "litellm",
            "model": "test-grader",
            "request_id": "request-blind-review",
        },
        "runs": runs,
    }
    return (json.dumps(review, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _private_json_bytes(document: dict) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _retime_scene(scene: dict, observed_at: str) -> None:
    scene["observed_at"] = observed_at
    observed_date = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
    iso_year, iso_week, _ = observed_date.isocalendar()
    scene["weekly_report_id"] = f"{iso_year}-W{iso_week:02d}"
    for source in scene["source_records"]:
        source["at"] = observed_at


def _model_input_bytes(name: str, comparison: dict) -> bytes:
    model_input = BASELINE_INPUT if name == "baseline" else CANDIDATE_INPUT
    selection = (
        [
            {"scene_id": scene["id"], "start": 0, "end": len(scene["content"])}
            for scene in FROZEN_SCENES
        ]
        if name == "baseline"
        else [{"scene_id": "scene-a", "start": 0, "end": len(CANDIDATE_INPUT)}]
    )
    return _private_json_bytes(
        {
            "schema": "addx.work_method_model_input.v1",
            "snapshot_digest": comparison["snapshot_digest"],
            "filter_hash": comparison["filter_hash"],
            "selection_policy_hash": comparison["selection_policy_hash"],
            "transform_id": "scene-slices-v1",
            "selection": selection,
            "model_input": model_input,
        }
    )


def _token_receipt_bytes(name: str, comparison: dict) -> bytes:
    run = comparison[name]
    return _private_json_bytes(
        {
            "schema": "addx.work_method_input_token_receipt.v1",
            "source": "provider_usage",
            "snapshot_digest": comparison["snapshot_digest"],
            "input_digest": run["input_digest"],
            "provider_response": {
                "provider": "litellm",
                "model": "test-model",
                "request_id": f"request-{name}",
                "output_digest": run["report_digest"],
                "usage": {"prompt_tokens": run["input_tokens"]},
            },
            "counter_name": "model-api-usage",
            "counter_version": "v1",
        }
    )


def _blind_input_bytes(comparison: dict) -> bytes:
    reports = {}
    content = {"baseline": BASELINE_REPORT, "candidate": CANDIDATE_REPORT}
    for name in ("baseline", "candidate"):
        run = comparison[name]
        reports[run["blind_label"]] = {
            "report_digest": run["report_digest"],
            "content": content[name].decode("utf-8"),
        }
    document = {
        "schema": "addx.work_method_blind_quality_input.v1",
        "snapshot_digest": comparison["snapshot_digest"],
        "quality_policy_hash": comparison["quality_policy_hash"],
        "rubric_version": comparison["rubric_version"],
        "judge_input": comparison["comparison_protocol"]["judge_input"],
        "required_claims": json.loads(FROZEN_CLAIMS),
        "rubric": json.loads(QUALITY_POLICY.read_text(encoding="utf-8")),
        "reports": reports,
    }
    return _private_json_bytes(document)


def _blind_label_map_bytes(comparison: dict, packet_bytes: bytes) -> bytes:
    document = {
        "schema": "addx.work_method_blind_label_map.v1",
        "snapshot_digest": comparison["snapshot_digest"],
        "packet_digest": "sha256:" + hashlib.sha256(packet_bytes).hexdigest(),
        "baseline_label": comparison["baseline"]["blind_label"],
        "candidate_label": comparison["candidate"]["blind_label"],
        "baseline_report_digest": comparison["baseline"]["report_digest"],
        "candidate_report_digest": comparison["candidate"]["report_digest"],
    }
    return _private_json_bytes(document)


def _write_quality_artifacts(root: Path, comparison: dict) -> None:
    root.chmod(0o700)
    for name, payload in {
        "weekly-report.html": WEEKLY_REPORT,
        "review-task.json": REVIEW_TASK,
    }.items():
        path = root / name
        path.write_bytes(payload)
        path.chmod(0o600)
    quality = root / "quality"
    quality.mkdir(parents=True, exist_ok=True)
    quality.chmod(0o700)
    artifacts = {
        "baseline-report.md": BASELINE_REPORT,
        "candidate-report.md": CANDIDATE_REPORT,
        "frozen-snapshot.json": FROZEN_SNAPSHOT,
        "blind-quality-review.json": _blind_review_bytes(comparison),
        "blind-quality-input.json": _blind_input_bytes(comparison),
        "blind-label-map.json": _blind_label_map_bytes(
            comparison, _blind_input_bytes(comparison)
        ),
        "required-claims.json": FROZEN_CLAIMS,
        "baseline-input.json": _model_input_bytes("baseline", comparison),
        "candidate-input.json": _model_input_bytes("candidate", comparison),
    }
    for run_name in ("baseline", "candidate"):
        if "input_tokens" in comparison[run_name]:
            artifacts[f"{run_name}-token-receipt.json"] = _token_receipt_bytes(
                run_name, comparison
            )
    for name, payload in artifacts.items():
        path = quality / name
        path.write_bytes(payload)
        path.chmod(0o600)


def _prepare_state_round(tmp_path: Path) -> tuple[Path, Path]:
    # Same reason as _load_validator: realpath the pytest tmp prefix so the
    # validator's symlink-component check sees a canonical path on macOS.
    tmp_path = Path(os.path.realpath(tmp_path))
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    state_root.chmod(0o700)
    marker = state_root / ".addx-work-method-artifacts"
    marker.write_text("addx.work_method_artifacts.v1\n", encoding="utf-8")
    marker.chmod(0o600)
    round_root = state_root / "round-1"
    round_root.mkdir(mode=0o700)
    return state_root, round_root


def _write_weekly_approval(root: Path, manifest: dict) -> tuple[Path, Path, str, str]:
    author = "jchen"
    week = "2026-W38"
    weekly_report = root / "weekly-report.html"
    weekly_report.write_bytes(WEEKLY_REPORT)
    weekly_report.chmod(0o600)
    now = datetime.now(timezone.utc)
    receipt = {
        "schema": "addx.weekly_report_approval.v1",
        "approval_source": "top-level-user-message",
        "author": author,
        "week": week,
        "report_path": str(weekly_report.resolve()),
        "round_id": manifest["round_id"],
        "report_digest": "sha256:"
        + hashlib.sha256(weekly_report.read_bytes()).hexdigest(),
        "quality_review_digest": manifest["efficiency_eval"]["quality_review"][
            "artifact_sha256"
        ],
        "approved_by": "jchen",
        "accepted_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "nonce": "a" * 32,
        "consumed": False,
    }
    approval = root / "weekly-report.approval.json"
    approval.write_text(json.dumps(receipt), encoding="utf-8")
    approval.chmod(0o600)
    return approval, weekly_report, author, week


def _as_v3(manifest: dict, *, task_ids: list[str] | None = None) -> dict:
    manifest["schema"] = "addx.work_method_retrospective.v3"
    manifest["input"]["evidence_cutoff_at"] = "2026-09-16T23:59:59Z"
    manifest["input"]["scene_ledger_ref"] = "quality/frozen-snapshot.json"
    manifest["input"]["scene_ledger_digest"] = (
        "sha256:" + hashlib.sha256(FROZEN_SNAPSHOT).hexdigest()
    )
    manifest["trigger"] = {
        "source": "weekly_report",
        "eval_window": {"start": "2026-09-14", "end": "2026-09-16"},
        "proposal_window": {
            "kind": "rolling_28_days",
            "start": "2026-08-20",
            "end": "2026-09-16",
            "weekly_report_ids": ["2026-W36", "2026-W37", "2026-W38"],
            "previous_cutoff_at": "2026-09-13T23:59:59Z",
        },
    }
    manifest["input"]["scan"] = {
        "scanned_sessions": 20,
        "selected_sessions": 6,
        "skipped_cached_sessions": 14,
        "input_characters": 90000,
        "input_tokens": 30000,
        "coverage": "complete",
        "content_mode": "redacted_evidence",
        "redaction_policy": "session-evidence-v1",
    }
    review = manifest["tasks"]["eval_driven_review"]
    review["outcome"] = "review"
    review["independent_task_ids"] = task_ids or ["task-a", "task-b", "task-c"]
    review["task_evidence"] = [
        {
            "task_id": task_id,
            "evidence_refs": [f"scene-{chr(ord('a') + (index % 3))}#task"],
        }
        for index, task_id in enumerate(review["independent_task_ids"])
    ]
    review["frictions"] = [
        {
            "id": "FR-1",
            "category": "cicd",
            "status": "observed",
            "data_state": "measured",
            "claim": "The delivery loop waited repeatedly for CI feedback.",
            "evidence_refs": ["scene-b#ci-wait"],
            "signals": [
                {
                    "name": "pipeline_wait",
                    "value": 18,
                    "unit": "minutes",
                    "source_ref": "scene-b#ci-wait",
                }
            ],
            "impact": "Delayed the next verification step.",
            "evaluation_action": "Track queue and job duration separately.",
            "confidence": "high",
        }
    ]
    manifest["outer_loop"]["review_task"] = {
        "artifact_ref": "review-task.json",
        "artifact_digest": "sha256:" + hashlib.sha256(REVIEW_TASK).hexdigest(),
        "weekly_report_ref": "weekly-report.html",
        "weekly_report_digest": WEEKLY_REPORT_DIGEST,
        "author": "jchen",
        "week": "2026-W38",
    }
    manifest["efficiency_eval"] = _efficiency_eval()
    return manifest


def test_valid_manifest_keeps_the_two_review_tasks_separate():
    validator = _load_validator()

    validator.validate_manifest(_manifest())


def test_quality_gate_approval_cli_is_documented_end_to_end():
    documents = [
        SKILL_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8"),
        REPORT_CONTRACT.read_text(encoding="utf-8"),
        WEEKLY_SKILL.read_text(encoding="utf-8"),
    ]
    for argument in (
        "--manifest",
        "--report",
        "--approval-receipt",
        "--weekly-report",
        "--author",
        "--week",
        "--accepted-by",
        "--artifact-name",
    ):
        assert all(argument in document for document in documents), argument
    assert "quality_review_digest" in documents[2]


def test_v1_manifest_remains_valid_without_the_v2_addx_comparison():
    validator = _load_validator()
    manifest = _manifest()
    manifest["schema"] = "addx.work_method_retrospective.v1"
    candidate = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0]
    del candidate["addx_skill_comparison"]
    del candidate["change_decision"]

    validator.validate_manifest(manifest)


def test_v3_weekly_manifest_requires_a_bounded_incremental_scan():
    validator = _load_validator()
    manifest = _as_v3(_manifest())

    validator.validate_manifest(manifest)
    del manifest["input"]["scan"]

    with pytest.raises(validator.ContractError, match="input.scan"):
        validator.validate_manifest(manifest)


def test_v3_rejects_token_savings_that_reduce_report_quality():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["input"]["scan"]["input_characters"] = 50000
    manifest["input"]["scan"]["input_tokens"] = 15000
    manifest["efficiency_eval"]["candidate"]["input_characters"] = 50000
    manifest["efficiency_eval"]["candidate"]["input_tokens"] = 15000
    manifest["efficiency_eval"]["candidate"]["supported_claim_ids"] = [
        "claim-goal",
        "claim-result",
    ]

    with pytest.raises(validator.ContractError, match="required claims"):
        validator.validate_manifest(manifest)


def test_v3_rejects_unbound_or_fabricated_input_costs():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["baseline"]["input_characters"] = 1_000_000_000
    manifest["efficiency_eval"]["candidate"]["input_characters"] = 1
    manifest["efficiency_eval"]["candidate"]["input_tokens"] = 1
    manifest["input"]["scan"]["input_characters"] = 1
    manifest["input"]["scan"]["input_tokens"] = 1

    with pytest.raises(validator.ContractError, match="input character count does not match"):
        validator.validate_manifest(manifest)


def test_v3_efficiency_eval_binds_both_reports_and_a_blind_protocol():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    del manifest["efficiency_eval"]["candidate"]["report_digest"]

    with pytest.raises(validator.ContractError, match="candidate.report_digest"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["comparison_protocol"]["same_snapshot"] = False
    with pytest.raises(validator.ContractError, match="same frozen snapshot"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["comparison_protocol"]["judge_input"] = (
        "raw_sessions_and_reports"
    )
    with pytest.raises(validator.ContractError, match="reports, claim ledger, and rubric"):
        validator.validate_manifest(manifest)


def test_v3_efficiency_eval_requires_real_reports_snapshot_and_blind_review():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    (validator._test_artifact_root / "quality" / "candidate-report.md").unlink()
    with pytest.raises(validator.ContractError, match="candidate report is missing"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    (validator._test_artifact_root / "quality" / "blind-quality-review.json").unlink()
    with pytest.raises(validator.ContractError, match="blind quality review is missing"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["snapshot_digest"] = "sha256:" + "0" * 64
    with pytest.raises(validator.ContractError, match="validated input scene ledger"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    (validator._test_artifact_root / "quality" / "baseline-report.md").chmod(0o644)
    with pytest.raises(validator.ContractError, match="must be private"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    review_path = validator._test_artifact_root / "quality" / "blind-quality-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["packet_digest"] = "sha256:" + "0" * 64
    review_bytes = (json.dumps(review, sort_keys=True, separators=(",", ":")) + "\n").encode()
    review_path.write_bytes(review_bytes)
    review_path.chmod(0o600)
    manifest["efficiency_eval"]["quality_review"]["artifact_sha256"] = (
        "sha256:" + hashlib.sha256(review_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="packet digest does not match"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    packet_path = validator._test_artifact_root / "quality" / "blind-quality-input.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["rubric"]["scoring_anchors"]["4"] = "Always score four."
    packet_bytes = _private_json_bytes(packet)
    packet_path.write_bytes(packet_bytes)
    packet_path.chmod(0o600)
    manifest["efficiency_eval"]["comparison_protocol"]["packet_digest"] = (
        "sha256:" + hashlib.sha256(packet_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="canonical policy"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    packet_path = validator._test_artifact_root / "quality" / "blind-quality-input.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["required_claims"][0]["claim"] = "Always satisfied."
    packet_bytes = _private_json_bytes(packet)
    packet_path.write_bytes(packet_bytes)
    packet_path.chmod(0o600)
    manifest["efficiency_eval"]["comparison_protocol"]["packet_digest"] = (
        "sha256:" + hashlib.sha256(packet_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="claim ledger"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    packet_path = validator._test_artifact_root / "quality" / "blind-quality-input.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["baseline_label"] = "B"
    packet["raw_sessions"] = ["must never reach the grader"]
    packet["reports"]["A"]["role"] = "candidate"
    packet_bytes = _private_json_bytes(packet)
    packet_path.write_bytes(packet_bytes)
    packet_path.chmod(0o600)
    manifest["efficiency_eval"]["comparison_protocol"]["packet_digest"] = (
        "sha256:" + hashlib.sha256(packet_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="exact allowed fields"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    receipt_path = (
        validator._test_artifact_root / "quality" / "candidate-token-receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_response"]["output_digest"] = "sha256:" + "0" * 64
    receipt_bytes = _private_json_bytes(receipt)
    receipt_path.write_bytes(receipt_bytes)
    receipt_path.chmod(0o600)
    manifest["efficiency_eval"]["candidate"]["token_receipt_digest"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="output digest does not match"):
        validator.validate_manifest(manifest)


def test_v3_efficiency_eval_uses_the_same_model_and_distinct_provider_calls():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    receipt_path = (
        validator._test_artifact_root / "quality" / "candidate-token-receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_response"]["model"] = "different-model"
    receipt_bytes = _private_json_bytes(receipt)
    receipt_path.write_bytes(receipt_bytes)
    receipt_path.chmod(0o600)
    manifest["efficiency_eval"]["candidate"]["token_receipt_digest"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="same model"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    receipt_path = (
        validator._test_artifact_root / "quality" / "candidate-token-receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_response"]["request_id"] = "request-baseline"
    receipt_bytes = _private_json_bytes(receipt)
    receipt_path.write_bytes(receipt_bytes)
    receipt_path.chmod(0o600)
    manifest["efficiency_eval"]["candidate"]["token_receipt_digest"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="distinct provider request IDs"):
        validator.validate_manifest(manifest)


def test_v3_model_input_must_be_deterministically_derived_from_the_snapshot():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    input_path = validator._test_artifact_root / "quality" / "candidate-input.json"
    model_input = json.loads(input_path.read_text(encoding="utf-8"))
    model_input["model_input"] = "x"
    input_bytes = _private_json_bytes(model_input)
    input_path.write_bytes(input_bytes)
    input_path.chmod(0o600)
    manifest["efficiency_eval"]["candidate"]["input_digest"] = (
        "sha256:" + hashlib.sha256(input_bytes).hexdigest()
    )
    manifest["efficiency_eval"]["candidate"]["input_characters"] = 1
    manifest["input"]["scan"]["input_characters"] = 1

    with pytest.raises(validator.ContractError, match="not derived from the frozen snapshot"):
        validator.validate_manifest(manifest)


def test_v3_candidate_cannot_drop_a_baseline_quality_gate():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    del manifest["efficiency_eval"]["candidate"]["critical_gates"]["skill_boundary"]

    with pytest.raises(validator.ContractError, match="critical gate set"):
        validator.validate_manifest(manifest)


def test_v3_rejects_more_than_eight_scenes_or_selected_sessions():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["input"]["scene_ids"] = [f"scene-{i}" for i in range(9)]

    with pytest.raises(validator.ContractError, match="at most 8 scenes"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["input"]["scan"]["selected_sessions"] = 9
    manifest["input"]["scan"]["skipped_cached_sessions"] = 11
    with pytest.raises(validator.ContractError, match="at most 8 sessions"):
        validator.validate_manifest(manifest)


def test_v3_fewer_than_three_tasks_must_be_needs_evidence():
    validator = _load_validator()
    manifest = _as_v3(_manifest(), task_ids=["task-a", "task-b"])

    with pytest.raises(validator.ContractError, match="needs-evidence"):
        validator.validate_manifest(manifest)

    manifest["tasks"]["eval_driven_review"]["outcome"] = "needs-evidence"
    validator.validate_manifest(manifest)

    manifest["tasks"]["eval_driven_review"]["independent_task_ids"] = []
    manifest["tasks"]["eval_driven_review"]["task_evidence"] = []
    validator.validate_manifest(manifest)


def test_v3_complete_coverage_accounts_for_every_scanned_session():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["input"]["scan"]["skipped_cached_sessions"] = 10

    with pytest.raises(validator.ContractError, match="account for every"):
        validator.validate_manifest(manifest)

    manifest["input"]["scan"]["coverage"] = "partial"
    validator.validate_manifest(manifest)


def test_v3_validates_real_rolling_window_and_report_count():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["trigger"]["proposal_window"]["start"] = "2026-08-01"

    with pytest.raises(validator.ContractError, match="exceeds 28 days"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["trigger"]["proposal_window"]["weekly_report_ids"] = [
        "2026-W33",
        "2026-W34",
        "2026-W35",
        "2026-W36",
        "2026-W37",
    ]
    with pytest.raises(validator.ContractError, match="at most 4 weekly reports"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["trigger"]["proposal_window"]["weekly_report_ids"] = [
        "not-a-week",
        "not-a-week",
    ]
    with pytest.raises(validator.ContractError, match="YYYY-Www"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["trigger"]["proposal_window"]["previous_cutoff_at"] = "garbage"
    with pytest.raises(validator.ContractError, match="ISO timestamp"):
        validator.validate_manifest(manifest)


def test_v3_rejects_snapshot_scenes_and_evidence_outside_the_incremental_window():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    snapshot_path = validator._test_artifact_root / "quality" / "frozen-snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    _retime_scene(snapshot["scenes"][0], "2026-01-01T00:00:00Z")
    snapshot_bytes = _private_json_bytes(snapshot)
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_path.chmod(0o600)
    manifest["efficiency_eval"]["snapshot_digest"] = (
        "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
    )
    manifest["input"]["scene_ledger_digest"] = manifest["efficiency_eval"][
        "snapshot_digest"
    ]
    with pytest.raises(validator.ContractError, match="outside the proposal window"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["tasks"]["eval_driven_review"]["task_evidence"][0][
        "evidence_refs"
    ] = ["legacy-scene#turn-1"]
    with pytest.raises(validator.ContractError, match="frozen in-window scene locator"):
        validator.validate_manifest(manifest)


def test_v3_rejects_scene_observed_after_the_evidence_cutoff():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["input"]["evidence_cutoff_at"] = "2026-09-16T09:00:00Z"

    with pytest.raises(validator.ContractError, match="after the evidence cutoff"):
        validator.validate_manifest(manifest)


def test_v3_rejects_unredacted_or_unbound_scene_summary_and_timestamp():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    snapshot_path = validator._test_artifact_root / "quality" / "frozen-snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["scenes"][0]["content"] = "Authorization: Bearer sk-FAKESECRET123"
    snapshot_bytes = _private_json_bytes(snapshot)
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_path.chmod(0o600)
    manifest["input"]["scene_ledger_digest"] = (
        "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
    )

    with pytest.raises(validator.ContractError, match="unredacted sensitive data"):
        validator.validate_manifest(manifest)

    validator = _load_validator()
    manifest = _as_v3(_manifest())
    snapshot_path = validator._test_artifact_root / "quality" / "frozen-snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["scenes"][0]["observed_at"] = "2026-09-10T10:00:00Z"
    snapshot_bytes = _private_json_bytes(snapshot)
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_path.chmod(0o600)
    manifest["input"]["scene_ledger_digest"] = (
        "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
    )

    with pytest.raises(validator.ContractError, match="does not match source records"):
        validator.validate_manifest(manifest)


def test_v3_rejects_eval_evidence_outside_seven_days_but_inside_proposal_window():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["trigger"]["eval_window"]["start"] = "2026-09-15"

    with pytest.raises(validator.ContractError, match="scene inside the eval window"):
        validator.validate_manifest(manifest)


def test_v3_rejects_scene_week_id_that_disagrees_with_observed_at():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    snapshot_path = validator._test_artifact_root / "quality" / "frozen-snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["scenes"][0]["weekly_report_id"] = "2026-W37"
    snapshot_bytes = _private_json_bytes(snapshot)
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_path.chmod(0o600)
    manifest["input"]["scene_ledger_digest"] = (
        "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
    )

    with pytest.raises(validator.ContractError, match="does not match observed_at"):
        validator.validate_manifest(manifest)


def test_v3_every_independent_task_has_explicit_evidence_linkage():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["tasks"]["eval_driven_review"]["task_evidence"] = [
        {"task_id": "task-a", "evidence_refs": ["scene-a#turn-2"]},
    ]
    with pytest.raises(validator.ContractError, match="every eval-driven task"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    candidate = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0]
    candidate["task_evidence"] = [
        {"task_id": "task-a", "evidence_refs": ["scene-a#turn-2"]},
    ]
    with pytest.raises(validator.ContractError, match="every task"):
        validator.validate_manifest(manifest)


def test_v3_workflow_friction_requires_evidence_and_measurable_signals():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    friction = manifest["tasks"]["eval_driven_review"]["frictions"][0]
    friction["signals"] = []

    with pytest.raises(validator.ContractError, match="needs a measurable signal"):
        validator.validate_manifest(manifest)

    friction["status"] = "needs-evidence"
    validator.validate_manifest(manifest)

    friction["status"] = "observed"
    friction["signals"] = [
        {
            "name": "pipeline_wait",
            "value": 18,
            "unit": "minutes",
            "source_ref": "scene-a#different-evidence",
        }
    ]
    with pytest.raises(validator.ContractError, match="must use an evidence_ref"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["tasks"]["eval_driven_review"]["frictions"] = []
    with pytest.raises(validator.ContractError, match="must not be empty"):
        validator.validate_manifest(manifest)


def test_v3_workflow_friction_distinguishes_zero_missing_and_blocked_data():
    validator = _load_validator()

    manifest = _as_v3(_manifest())
    friction = manifest["tasks"]["eval_driven_review"]["frictions"][0]
    friction["category"] = "telemetry"
    friction["data_state"] = "not_applicable"
    with pytest.raises(validator.ContractError, match="must classify its data state"):
        validator.validate_manifest(manifest)

    friction["data_state"] = "unresolved"
    with pytest.raises(validator.ContractError, match="must be needs-evidence"):
        validator.validate_manifest(manifest)

    friction["data_state"] = "measured"
    friction["signals"][0]["value"] = 0
    with pytest.raises(validator.ContractError, match="needs a positive signal"):
        validator.validate_manifest(manifest)

    friction["data_state"] = "instrumentation_missing"
    validator.validate_manifest(manifest)

    friction["data_state"] = "query_blocked"
    validator.validate_manifest(manifest)


@pytest.mark.parametrize("invalid_value", [float("inf"), float("-inf"), float("nan")])
def test_v3_workflow_friction_rejects_non_finite_signals(invalid_value):
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    friction = manifest["tasks"]["eval_driven_review"]["frictions"][0]
    friction["signals"][0]["value"] = invalid_value

    with pytest.raises(validator.ContractError, match="signal value is invalid"):
        validator.validate_manifest(manifest)


def test_v3_workflow_friction_rejects_sensitive_or_unsafe_report_text():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    friction = manifest["tasks"]["eval_driven_review"]["frictions"][0]
    friction["claim"] = "Authorization: Bearer sk-FAKESECRET123456"
    with pytest.raises(validator.ContractError, match="unredacted sensitive data"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    friction = manifest["tasks"]["eval_driven_review"]["frictions"][0]
    friction["evidence_refs"] = ["scene-b#Authorization:Bearer-secret"]
    friction["signals"][0]["source_ref"] = friction["evidence_refs"][0]
    with pytest.raises(validator.ContractError, match="safe scene#locator"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["tasks"]["eval_driven_review"]["findings"][0][
        "claim"
    ] = "Authorization: Bearer sk-FAKESECRET123456"
    with pytest.raises(validator.ContractError, match="unredacted sensitive data"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["tasks"]["eval_driven_review"]["recommendations"][0][
        "action"
    ] = "Email owner@example.com and inspect /home/person/private"
    with pytest.raises(validator.ContractError, match="unredacted sensitive data"):
        validator.validate_manifest(manifest)


def test_v3_addx_comparison_binds_a_real_current_skill_file():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    comparison = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "addx_skill_comparison"
    ][0]
    comparison["path"] = "skills/nonexistent-skill/SKILL.md"
    comparison["skill"] = "nonexistent-skill"
    manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "change_decision"
    ]["targets"] = ["nonexistent-skill"]
    with pytest.raises(validator.ContractError, match="Skill file is missing"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    comparison = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "addx_skill_comparison"
    ][0]
    comparison["skill_digest"] = "sha256:" + "0" * 64
    with pytest.raises(validator.ContractError, match="Skill digest does not match"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    candidate = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0]
    comparison = candidate["addx_skill_comparison"][0]
    comparison["skill"] = "nonexistent-skill"
    candidate["change_decision"]["targets"] = ["nonexistent-skill"]
    with pytest.raises(validator.ContractError, match="name must match its Skill path"):
        validator.validate_manifest(manifest)


def test_v3_addx_comparison_uses_the_packaged_read_only_catalog(
    tmp_path, monkeypatch
):
    packager_path = REPOSITORY_ROOT / "scripts" / "package_plugin.py"
    spec = importlib.util.spec_from_file_location("package_plugin_catalog", packager_path)
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    state_root = tmp_path / "stable-user-state"
    monkeypatch.setenv("ADDX_WORK_METHOD_STATE_DIR", str(state_root))
    destination = tmp_path / "analytics-plugin-v1"
    destination_v2 = tmp_path / "analytics-plugin-v2"
    destination.mkdir()
    destination_v2.mkdir()
    packager._stage_analytics_plugin(REPOSITORY_ROOT, destination)
    packager._stage_analytics_plugin(REPOSITORY_ROOT, destination_v2)

    assert not (destination / "skills" / "testing-strategy").exists()
    staged_validator = _load_validator(
        destination
        / "skills"
        / "work-method-retrospective"
        / "scripts"
        / "validate_manifest.py"
    )
    staged_validator.validate_manifest(_as_v3(_manifest()))
    staged_validator_v2 = _load_validator(
        destination_v2
        / "skills"
        / "work-method-retrospective"
        / "scripts"
        / "validate_manifest.py"
    )
    assert staged_validator.STATE_ROOT == state_root
    assert staged_validator_v2.STATE_ROOT == state_root
    assert staged_validator.QUALITY_GATE_ROOT == staged_validator_v2.QUALITY_GATE_ROOT

def test_v3_reuses_an_accepted_quality_gate_without_rerunning_baseline(
    tmp_path, monkeypatch
):
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    digest = manifest["efficiency_eval"]["filter_hash"]
    gate_root = tmp_path / "quality-gates"
    gate_root.mkdir()
    gate_root.chmod(0o700)
    monkeypatch.setattr(validator, "QUALITY_GATE_ROOT", gate_root)
    receipt = {
        "schema": "addx.work_method_quality_gate.v1",
        "snapshot_id": "golden-scenes-v1",
        "filter_hash": digest,
        "selection_policy_hash": _selection_policy_hash(),
        "quality_policy_hash": _quality_policy_hash(),
        "rubric_version": "work-method-quality-v1",
        "decision": "accept",
        "accepted_at": "2026-09-16T12:00:00Z",
        "accepted_by": "jchen",
        "report_digest": _efficiency_eval()["quality_review"]["artifact_sha256"],
        "approval_provenance": {
            "receipt_digest": "sha256:" + "6" * 64,
            "round_id": "round-1",
            "author": "jchen",
            "week": "2026-W38",
            "weekly_report_digest": "sha256:" + "7" * 64,
            "approved_by": "jchen",
            "accepted_at": "2026-09-16T11:59:00Z",
        },
        "quality_comparison": {
            "method": "blind_side_by_side",
            "metric": "input_tokens",
            "snapshot_digest": _efficiency_eval()["snapshot_digest"],
            "quality_review_digest": _efficiency_eval()["quality_review"][
                "artifact_sha256"
            ],
            "quality_review_ref": "quality/blind-quality-review.json",
            "packet_digest": _efficiency_eval()["comparison_protocol"]["packet_digest"],
            "claim_ledger_digest": _efficiency_eval()["comparison_protocol"][
                "claim_ledger_digest"
            ],
            "label_map_digest": _efficiency_eval()["comparison_protocol"][
                "label_map_digest"
            ],
            "baseline_label": "B",
            "candidate_label": "A",
            "required_claim_ids": ["claim-goal", "claim-result", "claim-turning-point"],
            "reviewer": {
                "provider": "litellm",
                "model": "test-grader",
                "request_id": "request-blind-review",
            },
            "baseline": {
                "report_digest": "sha256:" + hashlib.sha256(BASELINE_REPORT).hexdigest(),
                "input_digest": _efficiency_eval()["baseline"]["input_digest"],
                "input_characters": 400000,
                "input_tokens": 100000,
                "token_receipt_digest": _efficiency_eval()["baseline"][
                    "token_receipt_digest"
                ],
                "token_evidence": {
                    "source": "provider_usage",
                    "provider": "litellm",
                    "model": "test-model",
                    "request_id": "request-baseline",
                    "counter_name": "model-api-usage",
                    "counter_version": "v1",
                },
                "required_claim_count": 3,
                "unsupported_claim_count": 0,
                "privacy_violation_count": 0,
                "critical_gates": _efficiency_eval()["baseline"]["critical_gates"],
                "quality_dimensions": _efficiency_eval()["baseline"][
                    "quality_dimensions"
                ],
            },
            "candidate": {
                "report_digest": "sha256:" + hashlib.sha256(CANDIDATE_REPORT).hexdigest(),
                "input_digest": _efficiency_eval()["candidate"]["input_digest"],
                "input_characters": 90000,
                "input_tokens": 30000,
                "token_receipt_digest": _efficiency_eval()["candidate"][
                    "token_receipt_digest"
                ],
                "token_evidence": {
                    "source": "provider_usage",
                    "provider": "litellm",
                    "model": "test-model",
                    "request_id": "request-candidate",
                    "counter_name": "model-api-usage",
                    "counter_version": "v1",
                },
                "required_claim_count": 3,
                "unsupported_claim_count": 0,
                "privacy_violation_count": 0,
                "critical_gates": _efficiency_eval()["candidate"]["critical_gates"],
                "quality_dimensions": _efficiency_eval()["candidate"][
                    "quality_dimensions"
                ],
            },
        },
    }
    receipt_bytes = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    (gate_root / "accepted-v1.json").write_bytes(receipt_bytes)
    (gate_root / "accepted-v1.json").chmod(0o600)
    manifest["efficiency_eval"] = {
        "mode": "reuse",
        "filter_hash": digest,
        "selection_policy_hash": _selection_policy_hash(),
        "quality_policy_hash": _quality_policy_hash(),
        "rubric_version": "work-method-quality-v1",
        "accepted_gate": {
            "artifact_ref": "accepted-v1.json",
            "artifact_sha256": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest(),
        },
        "decision": "accept",
    }

    with pytest.raises(validator.ContractError, match="reuse mode must use"):
        validator.validate_manifest(manifest)
    del manifest["input"]["scan"]["input_tokens"]
    validator.validate_manifest(manifest)

    for run_name in ("baseline", "candidate"):
        receipt["quality_comparison"][run_name]["quality_dimensions"][
            "scene_reconstruction"
        ] = 2
    receipt_bytes = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    (gate_root / "accepted-v1.json").write_bytes(receipt_bytes)
    (gate_root / "accepted-v1.json").chmod(0o600)
    manifest["efficiency_eval"]["accepted_gate"]["artifact_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="from 3 to 4"):
        validator.validate_manifest(manifest)
    for run_name in ("baseline", "candidate"):
        receipt["quality_comparison"][run_name]["quality_dimensions"][
            "scene_reconstruction"
        ] = 4
    receipt_bytes = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    (gate_root / "accepted-v1.json").write_bytes(receipt_bytes)
    (gate_root / "accepted-v1.json").chmod(0o600)
    manifest["efficiency_eval"]["accepted_gate"]["artifact_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )

    scene_ledger = validator._test_artifact_root / "quality" / "frozen-snapshot.json"
    scene_ledger_bytes = scene_ledger.read_bytes()
    scene_ledger.unlink()
    with pytest.raises(validator.ContractError, match="input scene ledger is missing"):
        validator.validate_manifest(manifest)
    scene_ledger.write_bytes(scene_ledger_bytes)
    scene_ledger.chmod(0o600)

    manifest["trigger"]["eval_window"]["start"] = "2026-09-15"
    manifest["trigger"]["proposal_window"]["start"] = "2026-09-15"
    manifest["trigger"]["proposal_window"]["weekly_report_ids"] = ["2026-W38"]
    with pytest.raises(validator.ContractError, match="outside the proposal window"):
        validator.validate_manifest(manifest)
    manifest["trigger"]["eval_window"]["start"] = "2026-09-14"
    manifest["trigger"]["proposal_window"]["start"] = "2026-08-20"
    manifest["trigger"]["proposal_window"]["weekly_report_ids"] = [
        "2026-W36",
        "2026-W37",
        "2026-W38",
    ]

    receipt["approval_provenance"]["approved_by"] = "different-user"
    receipt_bytes = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    (gate_root / "accepted-v1.json").write_bytes(receipt_bytes)
    (gate_root / "accepted-v1.json").chmod(0o600)
    manifest["efficiency_eval"]["accepted_gate"]["artifact_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="does not match accepted_by"):
        validator.validate_manifest(manifest)
    receipt["approval_provenance"]["approved_by"] = "jchen"

    receipt["quality_comparison"]["candidate"]["token_evidence"]["model"] = (
        "different-model"
    )
    receipt_bytes = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    (gate_root / "accepted-v1.json").write_bytes(receipt_bytes)
    (gate_root / "accepted-v1.json").chmod(0o600)
    manifest["efficiency_eval"]["accepted_gate"]["artifact_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="same model"):
        validator.validate_manifest(manifest)

    linked_gate_root = tmp_path / "linked-quality-gates"
    linked_gate_root.symlink_to(gate_root, target_is_directory=True)
    monkeypatch.setattr(validator, "QUALITY_GATE_ROOT", linked_gate_root)
    with pytest.raises(validator.ContractError, match="symlink component"):
        validator.validate_manifest(manifest)
    monkeypatch.setattr(validator, "QUALITY_GATE_ROOT", gate_root)

    manifest["efficiency_eval"]["accepted_gate"]["artifact_ref"] = "missing.json"
    with pytest.raises(validator.ContractError, match="artifact is missing"):
        validator.validate_manifest(manifest)


def test_rerun_receipt_writer_feeds_the_next_reuse_manifest(tmp_path, monkeypatch):
    validator = _load_validator()
    writer = _load_quality_writer()
    state_root, round_root = _prepare_state_round(tmp_path)
    gate_root = state_root / "quality-gates"
    manifest = _as_v3(_manifest())
    quality_dir = round_root / "quality"
    _write_quality_artifacts(round_root, manifest["efficiency_eval"])
    manifest_path = round_root / "rerun.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    report = quality_dir / "blind-quality-review.json"
    approval, weekly_report, author, week = _write_weekly_approval(round_root, manifest)

    reference = writer.write_quality_gate(
        manifest_path,
        report,
        approval,
        weekly_report,
        author,
        week,
        "jchen",
        "session-evidence-v1.json",
        gate_root=gate_root,
        state_root=state_root,
    )

    monkeypatch.setattr(validator, "QUALITY_GATE_ROOT", gate_root)
    reuse = _as_v3(_manifest())
    reuse["efficiency_eval"] = {
        "mode": "reuse",
        "filter_hash": _filter_hash(),
        "selection_policy_hash": _selection_policy_hash(),
        "quality_policy_hash": _quality_policy_hash(),
        "rubric_version": "work-method-quality-v1",
        "accepted_gate": reference,
        "decision": "accept",
    }
    del reuse["input"]["scan"]["input_tokens"]
    validator.validate_manifest(reuse)
    receipt_path = gate_root / reference["artifact_ref"]
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    approval_bytes = approval.read_bytes()
    approval_document = json.loads(approval_bytes)
    assert receipt["approval_provenance"] == {
        "receipt_digest": "sha256:" + hashlib.sha256(approval_bytes).hexdigest(),
        "round_id": manifest["round_id"],
        "author": author,
        "week": week,
        "weekly_report_digest": approval_document["report_digest"],
        "approved_by": "jchen",
        "accepted_at": approval_document["accepted_at"],
    }
    assert receipt["quality_comparison"]["metric"] == "input_tokens"
    assert receipt["quality_comparison"]["baseline"]["input_tokens"] == 100000
    assert receipt["quality_comparison"]["candidate"]["input_tokens"] == 30000
    assert receipt["quality_comparison"]["candidate"]["quality_dimensions"] == (
        receipt["quality_comparison"]["baseline"]["quality_dimensions"]
    )
    assert receipt["quality_comparison"]["candidate"]["required_claim_count"] == 3
    assert receipt["quality_comparison"]["reviewer"] == {
        "provider": "litellm",
        "model": "test-grader",
        "request_id": "request-blind-review",
    }
    assert receipt["quality_comparison"]["required_claim_ids"] == [
        "claim-goal",
        "claim-result",
        "claim-turning-point",
    ]
    assert {
        receipt["quality_comparison"]["baseline_label"],
        receipt["quality_comparison"]["candidate_label"],
    } == {"A", "B"}
    assert receipt["quality_comparison"]["candidate"]["critical_gates"] == {
        "scene_reconstruction": True,
        "evidence_traceability": True,
        "skill_boundary": True,
    }


def test_quality_gate_writer_rejects_a_report_digest_that_was_not_compared(tmp_path):
    writer = _load_quality_writer()
    state_root, round_root = _prepare_state_round(tmp_path)
    manifest = _as_v3(_manifest())
    quality_dir = round_root / "quality"
    _write_quality_artifacts(round_root, manifest["efficiency_eval"])
    manifest["efficiency_eval"]["candidate"]["report_digest"] = "sha256:" + "0" * 64
    manifest_path = round_root / "rerun.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    review = quality_dir / "blind-quality-review.json"
    approval, weekly_report, author, week = _write_weekly_approval(round_root, manifest)

    with pytest.raises(ValueError, match="candidate report digest does not match"):
        writer.write_quality_gate(
            manifest_path,
            review,
            approval,
            weekly_report,
            author,
            week,
            "jchen",
            "session-evidence-v1.json",
            gate_root=state_root / "quality-gates",
            state_root=state_root,
        )


def test_quality_gate_writer_requires_digest_bound_human_approval(tmp_path):
    writer = _load_quality_writer()
    state_root, round_root = _prepare_state_round(tmp_path)
    manifest = _as_v3(_manifest())
    _write_quality_artifacts(round_root, manifest["efficiency_eval"])
    manifest_path = round_root / "rerun.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    approval, weekly_report, author, week = _write_weekly_approval(round_root, manifest)
    receipt = json.loads(approval.read_text(encoding="utf-8"))
    receipt["quality_review_digest"] = "sha256:" + "0" * 64
    approval.write_text(json.dumps(receipt), encoding="utf-8")
    approval.chmod(0o600)

    with pytest.raises(ValueError, match="quality review digest does not match"):
        writer.write_quality_gate(
            manifest_path,
            round_root / "quality" / "blind-quality-review.json",
            approval,
            weekly_report,
            author,
            week,
            "jchen",
            "session-evidence-v1.json",
            gate_root=state_root / "quality-gates",
            state_root=state_root,
        )


def test_quality_gate_writer_rejects_an_intermediate_symlink_root(tmp_path):
    writer = _load_quality_writer()
    state_root, round_root = _prepare_state_round(tmp_path)
    manifest = _as_v3(_manifest())
    _write_quality_artifacts(round_root, manifest["efficiency_eval"])
    manifest_path = round_root / "rerun.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    report = round_root / "quality" / "blind-quality-review.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o700)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)
    approval, weekly_report, author, week = _write_weekly_approval(round_root, manifest)

    with pytest.raises(ValueError, match="symlink component"):
        writer.write_quality_gate(
            manifest_path,
            report,
            approval,
            weekly_report,
            author,
            week,
            "jchen",
            "session-evidence-v1.json",
            gate_root=linked_parent / "quality-gates",
            state_root=state_root,
        )


def test_v3_rejects_filter_hash_not_bound_to_current_pipeline():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["filter_hash"] = "sha256:" + "b" * 64

    with pytest.raises(validator.ContractError, match="current extractor pipeline"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["selection_policy_hash"] = "sha256:" + "b" * 64
    with pytest.raises(validator.ContractError, match="current selection policy"):
        validator.validate_manifest(manifest)


def test_v3_candidate_cannot_add_evidence_outside_frozen_claim_ledger():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["candidate"]["supported_claim_ids"].append(
        "claim-new-only"
    )

    with pytest.raises(validator.ContractError, match="outside the frozen ledger"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    manifest["efficiency_eval"]["required_claim_ids"].append("claim-goal")
    with pytest.raises(validator.ContractError, match="must be unique"):
        validator.validate_manifest(manifest)


def test_v3_weekly_report_can_record_no_new_skill_proposals():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["tasks"]["skill_proposal_discovery"]["candidates"] = []

    validator.validate_manifest(manifest)


def test_v3_uses_character_budget_only_when_tokens_are_unavailable():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    validator.validate_manifest(manifest)

    del manifest["input"]["scan"]["input_tokens"]
    for name in ("baseline", "candidate"):
        del manifest["efficiency_eval"][name]["input_tokens"]
        del manifest["efficiency_eval"][name]["token_receipt_ref"]
        del manifest["efficiency_eval"][name]["token_receipt_digest"]
    validator.validate_manifest(manifest)

    manifest["input"]["scan"]["input_characters"] = 150000
    manifest["efficiency_eval"]["candidate"]["input_characters"] = 150000
    with pytest.raises(validator.ContractError, match="120,000 character"):
        validator.validate_manifest(manifest)


def test_v3_accepts_200000_bound_tokens_and_rejects_200001():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    comparison = manifest["efficiency_eval"]
    comparison["baseline"]["input_tokens"] = 250_000
    comparison["candidate"]["input_tokens"] = 200_000
    manifest["input"]["scan"]["input_tokens"] = 200_000
    for name in ("baseline", "candidate"):
        receipt_bytes = _token_receipt_bytes(name, comparison)
        comparison[name]["token_receipt_digest"] = (
            "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
        )
    _write_quality_artifacts(validator._test_artifact_root, comparison)
    validator.validate_manifest(manifest)

    manifest["input"]["scan"]["input_tokens"] = 200_001
    with pytest.raises(validator.ContractError, match="200,000 token"):
        validator.validate_manifest(manifest)


def test_v3_accepts_120000_fallback_characters_and_rejects_120001():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    comparison = manifest["efficiency_eval"]
    for name in ("baseline", "candidate"):
        del comparison[name]["input_tokens"]
        del comparison[name]["token_receipt_ref"]
        del comparison[name]["token_receipt_digest"]
    candidate_input = _private_json_bytes(
        {
            "schema": "addx.work_method_model_input.v1",
            "snapshot_digest": comparison["snapshot_digest"],
            "filter_hash": comparison["filter_hash"],
            "selection_policy_hash": comparison["selection_policy_hash"],
            "transform_id": "scene-slices-v1",
            "selection": [{"scene_id": "scene-a", "start": 0, "end": 120_000}],
            "model_input": FROZEN_SCENES[0]["content"][:120_000],
        }
    )
    comparison["candidate"]["input_characters"] = 120_000
    comparison["candidate"]["input_digest"] = (
        "sha256:" + hashlib.sha256(candidate_input).hexdigest()
    )
    manifest["input"]["scan"]["input_characters"] = 120_000
    del manifest["input"]["scan"]["input_tokens"]
    _write_quality_artifacts(validator._test_artifact_root, comparison)
    candidate_path = validator._test_artifact_root / "quality" / "candidate-input.json"
    candidate_path.write_bytes(candidate_input)
    candidate_path.chmod(0o600)
    validator.validate_manifest(manifest)

    manifest["input"]["scan"]["input_characters"] = 120_001
    with pytest.raises(validator.ContractError, match="120,000 character"):
        validator.validate_manifest(manifest)


def test_v3_token_evidence_cannot_replace_bound_character_reduction():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["input"]["scan"]["input_characters"] = 400000
    manifest["efficiency_eval"]["candidate"]["input_characters"] = 400000

    with pytest.raises(validator.ContractError, match="bound input characters"):
        validator.validate_manifest(manifest)


def test_v3_accepted_report_requires_absolute_quality_floor():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    for name in ("baseline", "candidate"):
        manifest["efficiency_eval"][name]["quality_dimensions"][
            "scene_reconstruction"
        ] = 2

    with pytest.raises(validator.ContractError, match="from 3 to 4"):
        validator.validate_manifest(manifest)


def test_v3_token_evidence_must_be_present_on_both_sides_or_neither():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    del manifest["efficiency_eval"]["baseline"]["input_tokens"]
    del manifest["efficiency_eval"]["baseline"]["token_receipt_ref"]
    del manifest["efficiency_eval"]["baseline"]["token_receipt_digest"]

    with pytest.raises(validator.ContractError, match="both baseline and candidate"):
        validator.validate_manifest(manifest)


def test_quality_gate_writer_rejects_a_public_manifest(tmp_path):
    writer = _load_quality_writer()
    state_root, round_root = _prepare_state_round(tmp_path)
    manifest = _as_v3(_manifest())
    _write_quality_artifacts(round_root, manifest["efficiency_eval"])
    manifest_path = round_root / "rerun.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o644)
    approval, weekly_report, author, week = _write_weekly_approval(round_root, manifest)

    with pytest.raises(ValueError, match="must be private"):
        writer.write_quality_gate(
            manifest_path,
            round_root / "quality" / "blind-quality-review.json",
            approval,
            weekly_report,
            author,
            week,
            "jchen",
            "session-evidence-v1.json",
            gate_root=state_root / "quality-gates",
            state_root=state_root,
        )


def test_candidate_cannot_update_a_skill_that_already_fully_covers_it():
    validator = _load_validator()
    manifest = _manifest()
    manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "addx_skill_comparison"
    ][0]["coverage"] = "full"

    with pytest.raises(validator.ContractError, match="cannot update"):
        validator.validate_manifest(manifest)


def test_skill_proposal_needs_three_independent_tasks():
    validator = _load_validator()
    manifest = _manifest()
    manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "independent_task_ids"
    ] = ["task-a", "task-b"]

    with pytest.raises(validator.ContractError, match="independent tasks"):
        validator.validate_manifest(manifest)


def test_rejected_long_conversation_needs_a_failed_gate_not_full_proposal_fields():
    validator = _load_validator()
    manifest = _manifest()
    manifest["tasks"]["skill_proposal_discovery"]["candidates"] = [
        {
            "id": "SP-N1",
            "method": "summarize every long conversation",
            "outcome": "reject",
            "failed_gates": ["turn count alone is not reusable-method evidence"],
        }
    ]

    validator.validate_manifest(manifest)


def test_recommendations_must_trace_to_findings():
    validator = _load_validator()
    manifest = _manifest()
    manifest["tasks"]["eval_driven_review"]["recommendations"][0][
        "finding_ids"
    ] = ["missing"]

    with pytest.raises(validator.ContractError, match="unknown finding"):
        validator.validate_manifest(manifest)


def test_accepted_proposal_requires_addx_skill_comparison():
    validator = _load_validator()
    manifest = _manifest()
    del manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "addx_skill_comparison"
    ]

    with pytest.raises(validator.ContractError, match="AddX Skill comparison"):
        validator.validate_manifest(manifest)


def test_update_decision_requires_an_existing_addx_target():
    validator = _load_validator()
    manifest = _manifest()
    manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "change_decision"
    ]["targets"] = []

    with pytest.raises(validator.ContractError, match="update target"):
        validator.validate_manifest(manifest)


def test_new_skill_decision_cannot_name_an_update_target():
    validator = _load_validator()
    manifest = _manifest()
    decision = manifest["tasks"]["skill_proposal_discovery"]["candidates"][0][
        "change_decision"
    ]
    decision["action"] = "add"

    with pytest.raises(validator.ContractError, match="add decision"):
        validator.validate_manifest(manifest)


def test_final_inner_loop_cannot_keep_failed_checks():
    validator = _load_validator()
    manifest = _manifest()
    manifest["inner_loop"]["checks"][0]["status"] = "fail"

    with pytest.raises(validator.ContractError, match="inner-loop check"):
        validator.validate_manifest(manifest)


def test_default_result_must_remain_local_and_wait_for_human_review():
    validator = _load_validator()
    manifest = _manifest()
    manifest["publication"]["status"] = "published"

    with pytest.raises(validator.ContractError, match="local_only"):
        validator.validate_manifest(manifest)


def test_v3_review_task_must_exist_and_bind_round_week_author_and_report():
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    manifest["outer_loop"]["review_task"]["artifact_ref"] = "missing-task.json"
    with pytest.raises(validator.ContractError, match="review task is missing"):
        validator.validate_manifest(manifest)

    manifest = _as_v3(_manifest())
    task_path = validator._test_artifact_root / "review-task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["round_id"] = "different-round"
    task_bytes = _private_json_bytes(task)
    task_path.write_bytes(task_bytes)
    task_path.chmod(0o600)
    manifest["outer_loop"]["review_task"]["artifact_digest"] = (
        "sha256:" + hashlib.sha256(task_bytes).hexdigest()
    )
    with pytest.raises(validator.ContractError, match="round_id does not match"):
        validator.validate_manifest(manifest)


@pytest.mark.parametrize(
    ("report_bytes", "error"),
    [
        (
            b"<html>Authorization: Bearer sk-FAKESECRET123456</html>",
            "unredacted sensitive data",
        ),
        (b"<html><script>alert(1)</script></html>", "unsafe active markup"),
        (
            b'<html><a href="java&#x73;cript:alert(1)">review</a></html>',
            "unsafe active markup",
        ),
        (
            b'<html><svg><a xlink:href="java&#x73;cript:alert(1)">x</a></svg></html>',
            "unsafe active markup",
        ),
        (
            b'<html><a href="java&#x09;script:alert(1)">x</a></html>',
            "unsafe active markup",
        ),
        (
            b'<html><a href="data:text/html;base64,PHNjcmlwdD4=">x</a></html>',
            "unsafe active markup",
        ),
        (
            b'<html><svg><a><animate attributeName="href" values="javascript:alert(1)"/></a></svg></html>',
            "unsafe active markup",
        ),
        (
            b'<html><svg><a id="x"></a><set href="#x" attributeName="href" to="javascript:alert(1)"/></svg></html>',
            "unsafe active markup",
        ),
        (
            b'<html><style>body{background:u/**/rl(https://evil.example/x)}</style></html>',
            "unsafe active markup",
        ),
        (
            b"<html><body>sk-FAKE<em>SECRET123456</em></body></html>",
            "unredacted sensitive data",
        ),
        (
            b"<html><body>owner<span></span>@example.com</body></html>",
            "unredacted sensitive data",
        ),
        (
            b'<html><style>body::before{content:"sk-FAKE" "SECRET123456"}</style></html>',
            "unsafe active markup",
        ),
        (
            b'<html><style>body{background-image:image-set("https://evil.example/x" 1x)}</style></html>',
            "unsafe active markup",
        ),
        (
            b'<html><meta http-equiv="refresh" http-equiv="x" content="0;url=https://evil.example/"></html>',
            "unsafe active markup",
        ),
        (
            b'<html><body background="https://evil.example/tracker">x</body></html>',
            "unsafe active markup",
        ),
        (
            b'<html><body><table background="https://evil.example/tracker"><tr><td>x</td></tr></table></body></html>',
            "unsafe active markup",
        ),
    ],
)
def test_v3_review_task_rejects_sensitive_or_active_weekly_html(report_bytes, error):
    validator = _load_validator()
    manifest = _as_v3(_manifest())
    root = validator._test_artifact_root
    report_path = root / "weekly-report.html"
    report_path.write_bytes(report_bytes)
    report_path.chmod(0o600)
    report_digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    manifest["outer_loop"]["review_task"]["weekly_report_digest"] = report_digest

    task_path = root / "review-task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["weekly_report_digest"] = report_digest
    task_bytes = _private_json_bytes(task)
    task_path.write_bytes(task_bytes)
    task_path.chmod(0o600)
    manifest["outer_loop"]["review_task"]["artifact_digest"] = (
        "sha256:" + hashlib.sha256(task_bytes).hexdigest()
    )

    with pytest.raises(validator.ContractError, match=error):
        validator.validate_manifest(manifest)


def test_canonical_weekly_html_template_passes_the_review_gate():
    validator = _load_validator()
    source = WEEKLY_SKILL.read_text(encoding="utf-8")
    template = source.split("```html\n", 1)[1].split("\n```", 1)[0]
    rendered = template.replace("<BOT_NAME>", "Codex").encode("utf-8")

    validator._validate_weekly_report_content(rendered, "canonical weekly report")


def test_private_manifest_must_live_in_its_direct_state_round(tmp_path):
    validator = _load_validator()
    state_root, round_root = _prepare_state_round(tmp_path)
    manifest = _as_v3(_manifest())
    manifest_path = round_root / "review-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    assert (
        validator.read_private_manifest(manifest_path, state_root=state_root)["round_id"]
        == "round-1"
    )

    outside = tmp_path / "arbitrary-round"
    outside.mkdir(mode=0o700)
    outside_manifest = outside / "review-manifest.json"
    outside_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    outside_manifest.chmod(0o600)
    with pytest.raises(validator.ContractError, match="direct state-root round"):
        validator.read_private_manifest(outside_manifest, state_root=state_root)


def test_eval_suite_keeps_confirmed_outer_loop_rules_as_regressions():
    suite = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
    assertion_names = {
        assertion["name"]
        for case in suite["evals"]
        for assertion in case.get("assertions", [])
    }

    assert {
        "addx_update_or_add_decision",
        "cross_domain_new_skill_boundary",
        "outer_feedback_updates_skill_contract",
        "prior_round_immutable",
        "weekly_report_forces_eval_review",
        "insufficient_weekly_evidence_is_not_a_rating",
        "session_end_records_candidate_only",
        "review_routing_boundaries",
        "incremental_scan_budget",
        "efficiency_requires_quality_parity",
        "efficiency_binds_report_artifacts",
        "workflow_friction_requires_evidence",
        "telemetry_zero_is_not_no_friction",
        "weekly_html_mechanical_privacy_gate",
    } <= assertion_names


def test_skill_documents_the_hybrid_trigger_contract():
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "每次 `weekly-report`" in text
    assert "至少 3 个独立任务" in text
    assert "最近 4 份周报 / 28 天" in text
    assert "SessionEnd" in text
    assert "execution-review" in text
    assert "Plugin" in text
    assert "未安装" in text
    assert "200,000" in text
    assert "120,000" in text
