from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_drive_audit.py"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def run_audit(
    tmp_path: Path,
    state: dict,
    audit: dict,
) -> subprocess.CompletedProcess[str]:
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.json"
    write_json(state_path, state)
    write_json(audit_path, audit)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--state",
            str(state_path),
            "--audit-result",
            str(audit_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def promotion_state() -> dict:
    return {
        "mr_iid": 42,
        "target_branch": "main",
        "mr_mode": "production-promotion",
        "promotion": {
            "parity_report_sha256": "b" * 64,
            "external_gates": [
                {
                    "name": "production-policy",
                    "required": True,
                    "evidence": "https://example.test/policy",
                    "expected_contains": ['"runtimeEnabled": true'],
                }
            ],
        },
        "emergency": {"approved": False},
        "last_snapshot": {"head_sha": "a" * 40},
    }


def valid_audit(tmp_path: Path) -> dict:
    now = datetime.now(UTC).isoformat()
    evidence = tmp_path / "production-policy.json"
    evidence.write_text(
        '{"runtimeEnabled": true, "environment": "production"}\n',
        encoding="utf-8",
    )
    return {
        "status": "verified",
        "mr_iid": 42,
        "target_branch": "main",
        "head_sha": "a" * 40,
        "pipeline_status": "success",
        "unresolved_discussions": 0,
        "mergeable": True,
        "audited_at_utc": now,
        "release": {
            "code_parity_status": "pass",
            "parity_report_sha256": "b" * 64,
            "external_gates": [
                {
                    "name": "production-policy",
                    "evidence_url": "https://example.test/policy",
                    "target_environment": "production",
                    "observed_value": '"runtimeEnabled": true',
                    "evidence_file": str(evidence),
                    "verifier_tool": "growthbook API",
                    "verified_at_utc": now,
                }
            ],
        },
    }


def test_complete_live_gate_evidence_passes(tmp_path: Path) -> None:
    result = run_audit(tmp_path, promotion_state(), valid_audit(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "AUDIT PASS" in result.stdout


def test_contract_url_without_live_observation_cannot_complete(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    audit["release"]["external_gates"] = []

    result = run_audit(tmp_path, promotion_state(), audit)

    assert result.returncode == 1
    assert "do not match required contract gates" in result.stderr


def test_raw_evidence_must_contain_contract_expected_value(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    evidence = Path(audit["release"]["external_gates"][0]["evidence_file"])
    evidence.write_text('{"runtimeEnabled": false}\n', encoding="utf-8")
    audit["release"]["external_gates"][0]["observed_value"] = '"runtimeEnabled": false'

    result = run_audit(tmp_path, promotion_state(), audit)

    assert result.returncode == 1
    assert "missing expected value" in result.stderr


def test_audit_must_match_current_candidate_head(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    audit["head_sha"] = "d" * 40

    result = run_audit(tmp_path, promotion_state(), audit)

    assert result.returncode == 1
    assert "head_sha does not match latest Driver snapshot" in result.stderr


def cleanup_state() -> dict:
    return {
        "mr_iid": 42,
        "target_branch": "main",
        "mr_mode": "staging-writer-cleanup",
        "promotion": {"enabled": False},
        "cleanup": {
            "enabled": True,
            "contract": {"sha256": "c" * 64},
            "accepted_staging_sha": "b" * 40,
            "accepted_pipeline": "https://example.test/pipelines/9001",
            "accepted_pipeline_id": 9001,
            "accepted_pipeline_api_url": "https://example.test/api/pipelines/9001",
            "accepted_pipeline_jobs_url": "https://example.test/api/pipelines/9001/jobs",
            "accepted_jobs": [
                "build:staging-us:backend",
                "test:staging-branch-contract",
            ],
            "removed_writer_jobs": ["build:staging-us:backend"],
            "staging_contract_gate_job": "test:staging-branch-contract",
            "staging_branch": "staging",
            "staging_branch_policy_url": "https://example.test/staging-policy",
        },
        "emergency": {"approved": False},
        "last_snapshot": {"head_sha": "a" * 40},
    }


def valid_cleanup_audit(tmp_path: Path) -> dict:
    now = datetime.now(UTC).isoformat()
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "id": 9001,
                "ref": "staging",
                "sha": "b" * 40,
                "status": "success",
                "web_url": "https://example.test/pipelines/9001",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps(
            [
                {"name": "build:staging-us:backend", "status": "success"},
                {"name": "test:staging-branch-contract", "status": "success"},
            ],
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.json"
    policy.write_text(
        '{"name":"staging","allow_force_push":false}\n',
        encoding="utf-8",
    )
    return {
        "status": "verified",
        "mr_iid": 42,
        "target_branch": "main",
        "head_sha": "a" * 40,
        "pipeline_status": "success",
        "unresolved_discussions": 0,
        "mergeable": True,
        "audited_at_utc": now,
        "release": {
            "cleanup_contract_sha256": "c" * 64,
            "cleanup_evidence": [
                {
                    "name": "accepted-staging-pipeline",
                    "evidence_url": "https://example.test/api/pipelines/9001",
                    "target_environment": "staging",
                    "observed_value": "b" * 40,
                    "evidence_file": str(pipeline),
                    "verifier_tool": "GitLab API",
                    "verified_at_utc": now,
                },
                {
                    "name": "accepted-staging-jobs",
                    "evidence_url": "https://example.test/api/pipelines/9001/jobs",
                    "target_environment": "staging",
                    "observed_value": "build:staging-us:backend",
                    "evidence_file": str(jobs),
                    "verifier_tool": "GitLab API",
                    "verified_at_utc": now,
                },
                {
                    "name": "staging-branch-policy",
                    "evidence_url": "https://example.test/staging-policy",
                    "target_environment": "staging",
                    "observed_value": "staging",
                    "evidence_file": str(policy),
                    "verifier_tool": "GitLab API",
                    "verified_at_utc": now,
                },
            ],
            "external_gates": [],
            "workflow_evidence": None,
        },
    }


def test_cleanup_audit_rechecks_contract_pipeline_and_branch_policy(
    tmp_path: Path,
) -> None:
    result = run_audit(tmp_path, cleanup_state(), valid_cleanup_audit(tmp_path))

    assert result.returncode == 0, result.stderr


def test_cleanup_audit_rejects_unrelated_accepted_test_job(tmp_path: Path) -> None:
    state = cleanup_state()
    state["cleanup"]["accepted_jobs"][1] = "test:unrelated"
    audit = valid_cleanup_audit(tmp_path)
    jobs_observation = audit["release"]["cleanup_evidence"][1]
    jobs = Path(jobs_observation["evidence_file"])
    jobs.write_text(
        '[{"name":"build:staging-us:backend","status":"success"},'
        '{"name":"test:unrelated","status":"success"}]\n',
        encoding="utf-8",
    )

    result = run_audit(tmp_path, state, audit)

    assert result.returncode == 1
    assert (
        "cleanup accepted jobs do not bind the staging contract gate" in result.stderr
    )


def test_cleanup_audit_rejects_missing_branch_policy_evidence(
    tmp_path: Path,
) -> None:
    audit = valid_cleanup_audit(tmp_path)
    audit["release"]["cleanup_evidence"] = audit["release"]["cleanup_evidence"][:2]

    result = run_audit(tmp_path, cleanup_state(), audit)

    assert result.returncode == 1
    assert "cleanup evidence does not match required observations" in result.stderr


def branch_promotion_evidence(tmp_path: Path) -> tuple[dict, dict]:
    import hashlib

    state = promotion_state()
    state["promotion"].update(
        {
            "parity_mode": "branch-promotion",
            "canonical_verified_sha": "a" * 40,
            "staging_branch": "staging",
            "merge_result": {"tree_sha": "c" * 40},
            "staging_acceptance": {
                "pipeline_id": 12345,
                "pipeline_api_url": "projects/group%2Fproject/pipelines/12345",
                "pipeline_url": "https://gitlab.example.test/pipelines/12345",
                "note_id": 67890,
                "verification_mr_iid": 102,
                "verification_merged_at": "2026-10-08T01:00:00Z",
                "acceptance_author_id": 277,
                "acceptance_marker": f"<!-- staging-acceptance:PASS:v1:102:{'a' * 40}:12345 -->",
                "note_api_url": "projects/group%2Fproject/merge_requests/102/notes/67890",
                "note_body_sha256": hashlib.sha256(
                    b"<!-- staging-acceptance:PASS:v1:102:"
                    + b"a" * 40
                    + b":12345 -->\n"
                    + "验收通过：".encode()
                    + b" https://gitlab.example.test/pipelines/12345"
                ).hexdigest(),
            },
        }
    )
    audit = valid_audit(tmp_path)
    audit["release"]["merge_result_tree_sha"] = "c" * 40
    now = datetime.now(UTC).isoformat()
    body = f"<!-- staging-acceptance:PASS:v1:102:{'a' * 40}:12345 -->\n验收通过： https://gitlab.example.test/pipelines/12345"
    pipeline = tmp_path / "staging-pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "id": 12345,
                "sha": "a" * 40,
                "ref": "staging",
                "status": "success",
                "web_url": "https://gitlab.example.test/pipelines/12345",
            }
        ),
        encoding="utf-8",
    )
    note = tmp_path / "staging-note.json"
    note.write_text(
        json.dumps(
            {
                "id": 67890,
                "system": False,
                "noteable_iid": 102,
                "created_at": "2026-10-08T02:00:00Z",
                "body": body,
                "author": {"id": 277},
            }
        ),
        encoding="utf-8",
    )
    audit["release"]["staging_acceptance"] = [
        {
            "name": "staging-postmerge-pipeline",
            "evidence_url": state["promotion"]["staging_acceptance"][
                "pipeline_api_url"
            ],
            "target_environment": "staging",
            "observed_value": "a" * 40,
            "evidence_file": str(pipeline),
            "verifier_tool": "GitLab API",
            "verified_at_utc": now,
        },
        {
            "name": "staging-acceptance-note",
            "evidence_url": state["promotion"]["staging_acceptance"]["note_api_url"],
            "target_environment": "staging",
            "observed_value": "staging-acceptance:PASS:v1:102:",
            "evidence_file": str(note),
            "verifier_tool": "GitLab API",
            "verified_at_utc": now,
        },
    ]
    return state, audit


def test_branch_promotion_acceptance_evidence_passes(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 0, result.stderr


def test_branch_promotion_rejects_changed_staging_pipeline(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][0]["evidence_file"])
    raw = json.loads(evidence.read_text())
    raw["status"] = "failed"
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1
    assert "staging postmerge pipeline evidence changed" in result.stderr


def test_branch_promotion_rejects_changed_merge_tree(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    audit["release"]["merge_result_tree_sha"] = "d" * 40
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1
    assert "merge result tree differs" in result.stderr


def test_branch_promotion_rejects_wrong_note_mr(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][1]["evidence_file"])
    raw = json.loads(evidence.read_text())
    raw["noteable_iid"] = 999
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1


def test_branch_promotion_rejects_missing_note_mr(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][1]["evidence_file"])
    raw = json.loads(evidence.read_text())
    del raw["noteable_iid"]
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1


def test_branch_promotion_rejects_premerge_note(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][1]["evidence_file"])
    raw = json.loads(evidence.read_text())
    raw["created_at"] = "2026-10-08T00:00:00Z"
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1


def test_branch_promotion_rejects_missing_note_time(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][1]["evidence_file"])
    raw = json.loads(evidence.read_text())
    del raw["created_at"]
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1


def test_branch_promotion_rejects_naive_note_time(tmp_path: Path) -> None:
    state, audit = branch_promotion_evidence(tmp_path)
    evidence = Path(audit["release"]["staging_acceptance"][1]["evidence_file"])
    raw = json.loads(evidence.read_text())
    raw["created_at"] = "2026-10-08T02:00:00"
    evidence.write_text(json.dumps(raw))
    result = run_audit(tmp_path, state, audit)
    assert result.returncode == 1
