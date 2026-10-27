from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_drive_audit.py"
CHECKER = Path(__file__).parents[1] / "scripts" / "check_merge_integrity.py"
sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))

from merge_integrity_audit import MergeIntegrityError, validate_merge_integrity  # noqa: E402

# Fixed identities and dates make the fixture commits (and so their SHAs) identical in every tmp_path.
GIT_ENV = {
    **os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "tests@example.com",
    "GIT_AUTHOR_DATE": "2026-10-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T00:00:00Z",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, text=True, capture_output=True,
                          check=True).stdout.strip()


def commit(work: Path, path: str, content: str, message: str) -> str:
    (work / path).parent.mkdir(parents=True, exist_ok=True)
    (work / path).write_text(content, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", message)
    return git(work, "rev-parse", "HEAD")


def build_repo(root: Path, variant: str = "clean", target_branch: str = "main") -> tuple[Path, str, str]:
    """Bare origin + clone; returns (clone, MR head, origin/<target_branch>)."""
    origin, work = root / "origin.git", root / "work"
    if work.exists():
        return work, git(work, "rev-parse", "feature"), git(work, "rev-parse", f"origin/{target_branch}")
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    git(root, "init", "-q", "-b", "main", str(work))
    git(work, "remote", "add", "origin", str(origin))
    commit(work, "app/x.txt", "x\n", "base")
    git(work, "switch", "-q", "-c", "feature")
    commit(work, "app/feature.txt", "feature\n", "feat")
    if variant == "evil":  # merge the target and drop its new file: B FAIL on app/y.txt
        git(work, "switch", "-q", "main")
        commit(work, "app/y.txt", "y\n", "main work")
        git(work, "switch", "-q", "feature")
        git(work, "merge", "-q", "--no-ff", "--no-commit", "main")
        git(work, "rm", "-q", "-f", "app/y.txt")
        git(work, "commit", "-q", "-m", "merge main, drop y")
    elif variant == "sync":  # pre-merged sync of another long-lived branch: needs a second report
        git(work, "switch", "-q", "-c", "release/x", "main")
        commit(work, "app/x.txt", "x\nrelease fix\n", "release fix")
        git(work, "push", "-q", "origin", "release/x")
        git(work, "switch", "-q", "feature")
        git(work, "merge", "-q", "--no-ff", "-m", "sync release/x", "release/x")
    elif variant == "octopus":  # UNVERIFIED: cannot be replayed
        for name in ("o1", "o2"):
            git(work, "switch", "-q", "-c", name, "main")
            commit(work, f"app/{name}.txt", f"{name}\n", name)
        git(work, "switch", "-q", "feature")
        git(work, "merge", "-q", "--no-ff", "-m", "octopus", "o1", "o2")
    git(work, "push", "-q", "origin", f"main:refs/heads/{target_branch}")
    git(work, "fetch", "-q", "origin")
    return work, git(work, "rev-parse", "feature"), git(work, "rev-parse", f"origin/{target_branch}")


with tempfile.TemporaryDirectory() as _scratch:
    _, HEAD, TARGET = build_repo(Path(_scratch))


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
        cwd=build_repo(tmp_path / "git")[0],
        env=GIT_ENV,
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
        "last_snapshot": {"head_sha": HEAD},
    }


def report_evidence(tmp_path: Path, variant: str, source: str | None, name: str) -> dict:
    """Run check_merge_integrity.py --json for real in the fixture repo and summarise it like the Auditor."""
    work, head, target = build_repo(tmp_path / ("git" if variant == "clean" else f"git-{variant}"), variant)
    source = source or head
    result = subprocess.run([sys.executable, str(CHECKER), target, source, "--json"], cwd=work, env=GIT_ENV,
                            text=True, capture_output=True, check=False)
    path = tmp_path / f"{name}-{variant}-{source[:8]}.json"
    path.write_text(result.stdout, encoding="utf-8")
    return {"exit_code": result.returncode, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "evidence_file": str(path)}


def integrity_evidence(tmp_path: Path, variant: str = "clean", name: str = "integrity") -> dict:
    _, head, target = build_repo(tmp_path / ("git" if variant == "clean" else f"git-{variant}"), variant)
    return {"applicable": True, "target_sha": target, "source_sha": head,
            **report_evidence(tmp_path, variant, None, name)}


def rewrite_report(evidence: dict, change) -> None:
    path = Path(evidence["evidence_file"])
    report = json.loads(path.read_text(encoding="utf-8"))
    change(report)
    path.write_text(json.dumps(report), encoding="utf-8")
    evidence["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


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
        "head_sha": HEAD,
        "pipeline_status": "success",
        "unresolved_discussions": 0,
        "mergeable": True,
        "audited_at_utc": now,
        "merge_integrity": integrity_evidence(tmp_path),
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
        "last_snapshot": {"head_sha": HEAD},
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
        "head_sha": HEAD,
        "pipeline_status": "success",
        "unresolved_discussions": 0,
        "mergeable": True,
        "audited_at_utc": now,
        "merge_integrity": integrity_evidence(tmp_path),
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


def check_integrity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit: dict, state: dict | None = None,
                    variant: str = "clean", target_branch: str = "main") -> None:
    work, head, _ = build_repo(tmp_path / ("git" if variant == "clean" else f"git-{variant}"), variant, target_branch)
    monkeypatch.chdir(work)
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    validate_merge_integrity({"target_branch": target_branch, **(state or {})}, {"head_sha": head, **audit})


def rejects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit: dict, message: str, **kwargs) -> None:
    with pytest.raises(MergeIntegrityError, match=message):
        check_integrity(tmp_path, monkeypatch, audit, **kwargs)


def test_merge_integrity_evidence_is_required(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    del audit["merge_integrity"]
    result = run_audit(tmp_path, promotion_state(), audit)
    assert result.returncode == 1
    assert "audit.merge_integrity must be an object" in result.stderr


@pytest.mark.parametrize("branch", ["main", "master", "staging", "release/20261022", "hotfix/fix-1"])
def test_merge_integrity_cannot_be_skipped_for_long_lived_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    rejects(tmp_path, monkeypatch, {"merge_integrity": {"applicable": False}}, "required for this target branch",
            target_branch=branch)


def test_merge_integrity_skip_is_recomputed_from_merges_in_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip = {"merge_integrity": {"applicable": False}}
    check_integrity(tmp_path, monkeypatch, skip, target_branch="dev")
    rejects(tmp_path, monkeypatch, skip, "contains merge commits", variant="evil", target_branch="dev")


def test_merge_integrity_clean_report_passes_end_to_end(tmp_path: Path) -> None:
    result = run_audit(tmp_path, promotion_state(), valid_audit(tmp_path))
    assert result.returncode == 0, result.stderr


def test_merge_integrity_report_must_match_hash_head_and_live_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = integrity_evidence(tmp_path)
    rejects(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "report_sha256": "0" * 64}}, "hash does not match")
    rejects(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "source_sha": "d" * 40}}, "source_sha does not match")
    rejects(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "target_sha": "d" * 40}}, "not the current origin/main")
    forged = integrity_evidence(tmp_path, name="forged")
    rewrite_report(forged, lambda r: r.update(target_sha="d" * 40))
    rejects(tmp_path, monkeypatch, {"merge_integrity": forged}, "not bound to the audited head/target")
    work = build_repo(tmp_path / "git")[0]
    git(work, "switch", "-q", "main")
    commit(work, "app/later.txt", "later\n", "target moved")
    git(work, "push", "-q", "origin", "main")
    git(work, "switch", "-q", "feature")
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, "not the current origin/main")


def test_merge_integrity_commits_must_exist_locally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = integrity_evidence(tmp_path)
    work = build_repo(tmp_path / "git")[0]
    git(work, "switch", "-q", "main")
    git(work, "branch", "-q", "-D", "feature")
    git(work, "reflog", "expire", "--expire=now", "--all")
    git(work, "gc", "-q", "--prune=now")
    monkeypatch.chdir(work)
    with pytest.raises(MergeIntegrityError, match="not available locally"):
        validate_merge_integrity({"target_branch": "main"}, {"head_sha": HEAD, "merge_integrity": evidence})


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda r: r["findings"].pop(), "checks A-E"),
        (lambda r: r.update(exit_code=1, failed=True), "do not match the findings"),
        (lambda r: r["findings"].append({"check": "C", "level": "FAIL", "message": "x", "files": ["a"]}),
         "do not match the findings"),
        (lambda r: r.update(findings="none"), "no findings list"),
    ],
)
def test_merge_integrity_report_must_be_self_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change, message: str
) -> None:
    evidence = integrity_evidence(tmp_path)
    rewrite_report(evidence, change)
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, message)


def test_merge_integrity_report_must_match_a_fresh_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = integrity_evidence(tmp_path, "evil")

    def hide_failure(report: dict) -> None:
        report.update(exit_code=0, failed=False)
        for finding in report["findings"]:
            finding["level"] = "OK" if finding["level"] == "FAIL" else finding["level"]

    rewrite_report(evidence, hide_failure)
    evidence["exit_code"] = 0
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, "differs from a fresh re-run", variant="evil")


def test_merge_integrity_report_findings_must_match_rerun_even_with_same_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = integrity_evidence(tmp_path)
    rewrite_report(evidence, lambda r: r["findings"].append(
        {"check": "B", "level": "WARN", "message": "invented", "files": ["app/x.txt"]}))
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, "differs from a fresh re-run")


def test_waiver_lists_must_be_non_empty_strings() -> None:
    from merge_integrity_audit import waiver_matches

    base = {**WAIVER, "head_sha": HEAD, "target_sha": TARGET}
    assert waiver_matches(base, HEAD, TARGET, {"B"}, {"app/y.txt"})
    for change in ({"checks": []}, {"files": []}, {"files": [""]}, {"checks": [1]}):
        assert not waiver_matches({**base, **change}, HEAD, TARGET, set(), set()), change


def test_release_sync_needs_a_report_for_the_merged_long_lived_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = integrity_evidence(tmp_path, "sync")
    work = build_repo(tmp_path / "git-sync", "sync")[0]
    merged = git(work, "rev-parse", "release/x")
    listed = subprocess.run([sys.executable, str(CHECKER), evidence["target_sha"], evidence["source_sha"],
                             "--sync-parents"], cwd=work, env=GIT_ENV, text=True, capture_output=True, check=True)
    assert listed.stdout.split() == [merged]
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, "sync_reports must hold", variant="sync")
    sync = {"merged_sha": merged, **report_evidence(tmp_path, "sync", merged, "sync")}
    check_integrity(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "sync_reports": [sync]}}, variant="sync")
    for bad in ([{**sync, "merged_sha": "e" * 40}], [sync, sync], "x", [{**sync, "merged_sha": None}],
                [{**sync, "report_sha256": "0" * 64}]):
        with pytest.raises(MergeIntegrityError):
            check_integrity(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "sync_reports": bad}},
                            variant="sync")


def test_release_branch_as_source_needs_no_sync_report_for_itself(tmp_path: Path) -> None:
    work, _, target = build_repo(tmp_path / "git")
    git(work, "switch", "-q", "-c", "release/y", "main")
    commit(work, "app/r.txt", "r\n", "release work")
    git(work, "switch", "-q", "-c", "topic")
    commit(work, "app/t.txt", "t\n", "topic")
    git(work, "switch", "-q", "release/y")
    git(work, "merge", "-q", "--no-ff", "-m", "merge topic into release/y", "topic")
    git(work, "push", "-q", "origin", "release/y")
    head = git(work, "rev-parse", "release/y")
    listed = subprocess.run([sys.executable, str(CHECKER), target, head, "--sync-parents"], cwd=work, env=GIT_ENV,
                            text=True, capture_output=True, check=True)
    assert listed.stdout.split() == []


def test_release_that_merged_the_target_needs_a_report_for_its_pre_merge_tip(tmp_path: Path) -> None:
    """release -> master with conflicts: master is merged into the release itself before the MR."""
    work, _, _ = build_repo(tmp_path / "git")
    git(work, "switch", "-q", "-c", "release/z", "main")
    pre_merge = commit(work, "app/r.txt", "r\n", "release work")
    git(work, "switch", "-q", "main")
    commit(work, "app/m.txt", "m\n", "main moves on")
    git(work, "push", "-q", "origin", "main")
    git(work, "switch", "-q", "release/z")
    git(work, "merge", "-q", "--no-ff", "-m", "merge main into release/z", "main")
    git(work, "push", "-q", "origin", "release/z")
    target, head = git(work, "rev-parse", "main"), git(work, "rev-parse", "release/z")
    listed = subprocess.run([sys.executable, str(CHECKER), target, head, "--sync-parents"], cwd=work, env=GIT_ENV,
                            text=True, capture_output=True, check=True)
    assert listed.stdout.split() == [pre_merge]


@pytest.mark.parametrize("variant, message", [("evil", "without a matching signed waiver"), ("octopus", "did not complete")])
def test_failing_or_unfinished_sync_report_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str, message: str
) -> None:
    """The MR head report is clean; only the merged long-lived commit's report fails."""
    import merge_integrity_audit

    evidence = integrity_evidence(tmp_path)
    work, head, _ = build_repo(tmp_path / "git")
    _, bad, _ = build_repo(tmp_path / f"git-{variant}", variant)
    git(work, "fetch", "-q", str(tmp_path / f"git-{variant}" / "work"), "feature:refs/heads/bad")
    result = subprocess.run([sys.executable, str(CHECKER), evidence["target_sha"], bad, "--json"], cwd=work,
                            env=GIT_ENV, text=True, capture_output=True, check=False)
    path = tmp_path / f"bad-{variant}.json"
    path.write_text(result.stdout, encoding="utf-8")
    sync = {"merged_sha": bad, "exit_code": result.returncode, "evidence_file": str(path),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(merge_integrity_audit, "sync_parents", lambda *_: [bad])
    audit = {"merge_integrity": {**evidence, "sync_reports": [sync]}}
    rejects(tmp_path, monkeypatch, audit, message)
    if variant == "evil":
        failing = json.loads(Path(sync["evidence_file"]).read_text(encoding="utf-8"))["findings"]
        waiver = {**WAIVER, "head_sha": head, "target_sha": evidence["target_sha"],
                  "checks": sorted({f["check"] for f in failing if f["level"] == "FAIL"}),
                  "files": sorted({p for f in failing if f["level"] == "FAIL" for p in f["files"]})}
        check_integrity(tmp_path, monkeypatch, audit, {"merge_integrity_waivers": [waiver]})


def test_tampered_sync_report_with_valid_hash_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = integrity_evidence(tmp_path, "sync")
    work = build_repo(tmp_path / "git-sync", "sync")[0]
    merged = git(work, "rev-parse", "release/x")
    sync = {"merged_sha": merged, **report_evidence(tmp_path, "sync", merged, "sync")}
    rewrite_report(sync, lambda r: r["findings"].append(
        {"check": "B", "level": "WARN", "message": "invented", "files": ["app/x.txt"]}))
    rejects(tmp_path, monkeypatch, {"merge_integrity": {**evidence, "sync_reports": [sync]}},
            "differs from a fresh re-run", variant="sync")


WAIVER = {"checks": ["B", "C", "D"], "files": ["app/y.txt"], "approved_by": "qwang",
          "approved_at_utc": "2026-10-27T03:00:00Z", "reason": "intended removal"}


def test_merge_integrity_fail_needs_matching_signed_waiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = integrity_evidence(tmp_path, "evil")
    assert evidence["exit_code"] == 1
    audit = {"merge_integrity": evidence}
    rejects(tmp_path, monkeypatch, audit, "without a matching signed waiver", variant="evil")
    waiver = {**WAIVER, "head_sha": evidence["source_sha"], "target_sha": evidence["target_sha"]}
    check_integrity(tmp_path, monkeypatch, audit, {"merge_integrity_waivers": [waiver]}, variant="evil")
    for change in (
        {"head_sha": "e" * 40}, {"target_sha": "e" * 40}, {"checks": []}, {"checks": ["B", "D"]}, {"checks": "BCD"}, {"files": []},
        {"files": ["other.txt"]}, {"approved_by": ""}, {"reason": " "}, {"approved_at_utc": "yesterday"},
        {"approved_at_utc": "2026-10-27T11:00:00+08:00"}, {"approved_at_utc": "2026-10-27T03:00:00"},
    ):
        rejects(tmp_path, monkeypatch, audit, "without a matching signed waiver",
                state={"merge_integrity_waivers": [{**waiver, **change}]}, variant="evil")


def test_merge_integrity_unfinished_check_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = integrity_evidence(tmp_path, "octopus")
    assert evidence["exit_code"] == 2
    rejects(tmp_path, monkeypatch, {"merge_integrity": evidence}, "did not complete", variant="octopus")
