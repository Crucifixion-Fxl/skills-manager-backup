from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

SCRIPT = Path(__file__).parents[1] / "scripts" / "init_drive_state.py"
PROJECT_PATH = "engineering/skills"
ORIGIN_MR_IID = 101
VERIFICATION_MR_IID = 102
CANDIDATE_MR_IID = 103
CLEANUP_PIPELINE_ID = 9001


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def create_repo(tmp_path: Path, name: str = "driver-repo") -> Path:
    repo = tmp_path / name
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.email", "tests@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    return repo


def run_init(
    tmp_path: Path,
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    head_sha: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = cwd or create_repo(tmp_path)
    output = tmp_path / "state.json"
    head_sha = head_sha or git(repo, "rev-parse", "HEAD")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mr-iid",
            "42",
            "--project-path",
            PROJECT_PATH,
            "--branch",
            "feat/sample",
            "--head-sha",
            head_sha,
            "--output",
            str(output),
            *args,
        ],
        text=True,
        capture_output=True,
        check=False,
        cwd=repo,
        env=env,
    )
    return result, output


def setup_promotion_repo(
    tmp_path: Path,
    *,
    api_candidate_sha: str | None = None,
) -> tuple[Path, dict[str, str], str]:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    repo = create_repo(tmp_path, "promotion-repo")
    git(repo, "remote", "add", "origin", str(origin))
    base_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "-c", "canonical")
    (repo / "service.txt").write_text("feature enabled\n", encoding="utf-8")
    git(repo, "add", "service.txt")
    git(repo, "commit", "-m", "canonical feature")
    canonical_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "main")
    git(repo, "switch", "-c", "candidate")
    (repo / "service.txt").write_text("feature enabled\n", encoding="utf-8")
    git(repo, "add", "service.txt")
    git(repo, "commit", "-m", "candidate feature")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{canonical_sha}:refs/merge-requests/{ORIGIN_MR_IID}/head",
        f"{canonical_sha}:refs/merge-requests/{VERIFICATION_MR_IID}/head",
        f"{candidate_sha}:refs/merge-requests/{CANDIDATE_MR_IID}/head",
        f"{base_sha}:refs/heads/main",
    )

    project = quote(PROJECT_PATH, safe="")
    mr_path = f"projects/{project}/merge_requests"
    origin_mr = {
        "iid": ORIGIN_MR_IID,
        "state": "merged",
        "target_branch": "staging",
        "source_branch": "canonical",
        "sha": canonical_sha,
        "diff_refs": {"base_sha": base_sha},
    }
    verification_mr = {
        **origin_mr,
        "iid": VERIFICATION_MR_IID,
    }
    candidate_mr = {
        "iid": CANDIDATE_MR_IID,
        "state": "opened",
        "target_branch": "main",
        "source_branch": "candidate",
        "sha": api_candidate_sha or candidate_sha,
        "diff_refs": {"base_sha": base_sha},
    }
    query = urlencode(
        {
            "state": "merged",
            "target_branch": "staging",
            "source_branch": "canonical",
            "order_by": "created_at",
            "sort": "asc",
            "per_page": "100",
        }
    )
    responses = {
        f"{mr_path}/{VERIFICATION_MR_IID}": verification_mr,
        f"{mr_path}/{CANDIDATE_MR_IID}": candidate_mr,
        f"{mr_path}?{query}": [origin_mr, verification_mr],
        f"projects/{project}/repository/branches/main": {
            "name": "main",
            "commit": {"id": base_sha},
        },
    }
    responses_path = tmp_path / "fake-glab-responses.json"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_glab = fake_bin / "glab"
    fake_glab.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_GLAB_RESPONSES"], encoding="utf-8") as source:
    responses = json.load(source)
if len(sys.argv) != 3 or sys.argv[1] != "api" or sys.argv[2] not in responses:
    print(f"unexpected glab invocation: {sys.argv}", file=sys.stderr)
    raise SystemExit(2)
print(json.dumps(responses[sys.argv[2]]))
""",
        encoding="utf-8",
    )
    fake_glab.chmod(0o755)
    env = os.environ.copy()
    env["FAKE_GLAB_RESPONSES"] = str(responses_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return repo, env, candidate_sha


def setup_cleanup_repo(
    tmp_path: Path,
    *,
    extra_candidate_path: bool = False,
    staging_protected: bool = True,
    allow_force_push: bool = False,
    pipeline_status: str = "success",
) -> tuple[Path, dict[str, str], str, str]:
    origin = tmp_path / "cleanup-origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    repo = create_repo(tmp_path, "cleanup-repo")
    git(repo, "remote", "add", "origin", str(origin))
    base_ci = """stages:\n  - test\n  - build\n\nbuild:staging-us:backend:\n  stage: build\n  variables:\n    IMAGE_PATH: registry.example.test/staging-us/backend\n  rules:\n    - if: '$CI_COMMIT_BRANCH == \"main\"'\n\nbuild:prod-us:backend:\n  stage: build\n  variables:\n    IMAGE_PATH: registry.example.test/prod-us/backend\n  rules:\n    - if: '$CI_COMMIT_BRANCH == \"main\"'\n"""
    (repo / ".gitlab-ci.yml").write_text(base_ci, encoding="utf-8")
    git(repo, "add", ".gitlab-ci.yml")
    git(repo, "commit", "-m", "add delivery jobs")
    base_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "-c", "staging")
    (repo / "STAGING.md").write_text("accepted artifact\n", encoding="utf-8")
    git(repo, "add", "STAGING.md")
    git(repo, "commit", "-m", "publish accepted staging artifact")
    accepted_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "main")
    git(repo, "switch", "-c", "cleanup")
    candidate_ci = """stages:\n  - test\n  - build\n\ntest:master-delivery-contract:\n  stage: test\n  rules:\n    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == \"main\"'\n    - if: '$CI_COMMIT_BRANCH == \"main\"'\n  script:\n    - python3 tests/test_master_delivery_contract.py\n    - python3 scripts/verify_master_delivery_contract.py\n\nbuild:prod-us:backend:\n  stage: build\n  variables:\n    IMAGE_PATH: registry.example.test/prod-us/backend\n  rules:\n    - if: '$CI_COMMIT_BRANCH == \"main\"'\n"""
    (repo / ".gitlab-ci.yml").write_text(candidate_ci, encoding="utf-8")
    for path, content in (
        ("scripts/verify_master_delivery_contract.py", "print('verified')\n"),
        ("tests/test_master_delivery_contract.py", "def test_contract(): pass\n"),
        ("docs/delivery.md", "# Staging writer handoff\n"),
    ):
        destination = repo / path
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    contract = repo / "release-contracts/staging-writer-cleanup.yaml"
    contract.parent.mkdir()
    contract.write_text(
        f"""version: 1
workflow: staging-writer-cleanup
staging_branch: staging
accepted_staging_sha: {accepted_sha}
accepted_pipeline: https://gitlab.addx.ai/engineering/skills/-/pipelines/{CLEANUP_PIPELINE_ID}
writer_jobs:
  - build:staging-us:backend
contract_gate_jobs:
  - test:master-delivery-contract
staging_contract_gate_job: test:staging-branch-contract
accepted_jobs:
  - build:staging-us:backend
  - test:staging-branch-contract
verification_paths:
  - scripts/verify_master_delivery_contract.py
  - tests/test_master_delivery_contract.py
documentation_paths:
  - docs/delivery.md
""",
        encoding="utf-8",
    )
    if extra_candidate_path:
        (repo / "service.py").write_text("print('business change')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "retire main staging writer")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{base_sha}:refs/heads/main",
        f"{accepted_sha}:refs/heads/staging",
        f"{candidate_sha}:refs/merge-requests/42/head",
    )

    project = quote(PROJECT_PATH, safe="")
    responses = {
        f"projects/{project}/merge_requests/42": {
            "iid": 42,
            "state": "opened",
            "source_branch": "feat/sample",
            "target_branch": "main",
            "sha": candidate_sha,
        },
        f"projects/{project}/repository/branches/main": {
            "name": "main",
            "protected": True,
            "commit": {"id": base_sha},
        },
        f"projects/{project}/repository/branches/staging": {
            "name": "staging",
            "protected": staging_protected,
            "commit": {"id": accepted_sha},
        },
        f"projects/{project}/protected_branches/staging": {
            "name": "staging",
            "allow_force_push": allow_force_push,
        },
        f"projects/{project}/pipelines/{CLEANUP_PIPELINE_ID}": {
            "id": CLEANUP_PIPELINE_ID,
            "ref": "staging",
            "sha": accepted_sha,
            "status": pipeline_status,
            "web_url": (
                "https://gitlab.addx.ai/engineering/skills/-/pipelines/"
                f"{CLEANUP_PIPELINE_ID}"
            ),
        },
        f"projects/{project}/pipelines/{CLEANUP_PIPELINE_ID}/jobs?per_page=100": [
            {
                "id": 7001,
                "name": "build:staging-us:backend",
                "status": "success",
            },
            {
                "id": 7002,
                "name": "test:staging-branch-contract",
                "status": "success",
            },
        ],
    }
    responses_path = tmp_path / "fake-cleanup-glab-responses.json"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")
    fake_bin = tmp_path / "fake-cleanup-bin"
    fake_bin.mkdir()
    fake_glab = fake_bin / "glab"
    fake_glab.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_GLAB_RESPONSES"], encoding="utf-8") as source:
    responses = json.load(source)
if len(sys.argv) != 3 or sys.argv[1] != "api" or sys.argv[2] not in responses:
    print(f"unexpected glab invocation: {sys.argv}", file=sys.stderr)
    raise SystemExit(2)
print(json.dumps(responses[sys.argv[2]]))
""",
        encoding="utf-8",
    )
    fake_glab.chmod(0o755)
    env = os.environ.copy()
    env["FAKE_GLAB_RESPONSES"] = str(responses_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return repo, env, candidate_sha, accepted_sha


def test_ordinary_state_is_initialized_for_staging(tmp_path: Path) -> None:
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "staging",
        "--mr-mode",
        "ordinary",
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(output.read_text(encoding="utf-8"))
    assert state["mr_mode"] == "ordinary"
    assert state["promotion"]["enabled"] is False
    assert state["emergency"]["approved"] is False


def test_production_promotion_rechecks_gitlab_and_records_refs(
    tmp_path: Path,
) -> None:
    repo, env, candidate_sha = setup_promotion_repo(tmp_path)
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "production-promotion",
        "--staging-flow-exists",
        "true",
        "--canonical-verification-mr",
        str(VERIFICATION_MR_IID),
        "--candidate-mr",
        str(CANDIDATE_MR_IID),
        cwd=repo,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(output.read_text(encoding="utf-8"))
    assert state["promotion"]["enabled"] is True
    assert state["promotion"]["candidate_mr_sha"] == candidate_sha
    assert len(state["promotion"]["parity_report_sha256"]) == 64


def test_promotion_rejects_gitlab_mr_ref_mismatch(tmp_path: Path) -> None:
    repo, env, _ = setup_promotion_repo(tmp_path, api_candidate_sha="a" * 40)

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "production-promotion",
        "--staging-flow-exists",
        "true",
        "--canonical-verification-mr",
        str(VERIFICATION_MR_IID),
        "--candidate-mr",
        str(CANDIDATE_MR_IID),
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "release parity check failed" in result.stderr


def test_state_rejects_head_that_is_not_checked_out(tmp_path: Path) -> None:
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "staging",
        "--mr-mode",
        "ordinary",
        head_sha="a" * 40,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "does not match Git HEAD" in result.stderr


def test_non_promotion_cannot_bypass_existing_staging_flow(tmp_path: Path) -> None:
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "master",
        "--mr-mode",
        "production-non-promotion",
        "--staging-flow-exists",
        "true",
        "--staging-flow-evidence",
        "https://gitlab.example.test/staging",
        "--not-applicable-reason",
        "this change did not use staging",
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "provide a verified SHA or explicit hotfix approval" in result.stderr


def test_attested_staging_writer_cleanup_records_bound_evidence(
    tmp_path: Path,
) -> None:
    repo, env, candidate_sha, accepted_sha = setup_cleanup_repo(tmp_path)

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(output.read_text(encoding="utf-8"))
    assert state["mr_mode"] == "staging-writer-cleanup"
    assert state["promotion"]["enabled"] is False
    assert state["cleanup"]["enabled"] is True
    assert state["cleanup"]["candidate_mr_sha"] == candidate_sha
    assert state["cleanup"]["accepted_staging_sha"] == accepted_sha
    assert state["cleanup"]["removed_writer_jobs"] == [
        "build:staging-us:backend"
    ]
    assert state["cleanup"]["accepted_jobs"] == [
        "build:staging-us:backend",
        "test:staging-branch-contract",
    ]
    assert (
        state["cleanup"]["staging_contract_gate_job"]
        == "test:staging-branch-contract"
    )
    assert len(state["cleanup"]["contract"]["sha256"]) == 64


def test_cleanup_contract_cannot_authorize_business_source_changes(
    tmp_path: Path,
) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path, extra_candidate_path=True)

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "cleanup candidate changed undeclared paths: service.py" in result.stderr


def test_cleanup_rejects_unprotected_or_force_pushable_staging(
    tmp_path: Path,
) -> None:
    repo, env, _, _ = setup_cleanup_repo(
        tmp_path,
        staging_protected=False,
        allow_force_push=True,
    )

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "staging branch must be protected" in result.stderr


def test_cleanup_rejects_mr_source_branch_mismatch(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path)
    responses_path = Path(env["FAKE_GLAB_RESPONSES"])
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    project = quote(PROJECT_PATH, safe="")
    responses[f"projects/{project}/merge_requests/42"]["source_branch"] = "other"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "cleanup candidate MR source branch does not match state" in result.stderr


def test_cleanup_rejects_unaccepted_staging_pipeline(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path, pipeline_status="failed")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "accepted staging pipeline status must be success" in result.stderr


def test_cleanup_rejects_missing_accepted_writer_job(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path)
    responses_path = Path(env["FAKE_GLAB_RESPONSES"])
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    project = quote(PROJECT_PATH, safe="")
    responses[f"projects/{project}/pipelines/{CLEANUP_PIPELINE_ID}/jobs?per_page=100"] = [
        {
            "id": 7002,
            "name": "test:staging-branch-contract",
            "status": "success",
        }
    ]
    responses_path.write_text(json.dumps(responses), encoding="utf-8")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "accepted pipeline jobs do not match cleanup contract" in result.stderr


def test_cleanup_rejects_unrelated_accepted_test_job(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path)
    contract_path = repo / "release-contracts/staging-writer-cleanup.yaml"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            "accepted_jobs:\n"
            "  - build:staging-us:backend\n"
            "  - test:staging-branch-contract\n",
            "accepted_jobs:\n"
            "  - build:staging-us:backend\n"
            "  - test:unrelated\n",
        ),
        encoding="utf-8",
    )
    git(repo, "add", str(contract_path.relative_to(repo)))
    git(repo, "commit", "--amend", "--no-edit")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{candidate_sha}:refs/merge-requests/42/head",
    )
    responses_path = Path(env["FAKE_GLAB_RESPONSES"])
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    project = quote(PROJECT_PATH, safe="")
    responses[f"projects/{project}/merge_requests/42"]["sha"] = candidate_sha
    responses[f"projects/{project}/pipelines/{CLEANUP_PIPELINE_ID}/jobs?per_page=100"][
        1
    ]["name"] = "test:unrelated"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "accepted_jobs must bind the staging contract gate" in result.stderr


def test_cleanup_rejects_retained_ci_semantic_change(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path)
    ci_path = repo / ".gitlab-ci.yml"
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8").replace(
            "registry.example.test/prod-us/backend",
            "registry.example.test/prod-us/changed",
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".gitlab-ci.yml")
    git(repo, "commit", "--amend", "--no-edit")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{candidate_sha}:refs/merge-requests/42/head",
    )
    responses_path = Path(env["FAKE_GLAB_RESPONSES"])
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    responses[f"projects/{quote(PROJECT_PATH, safe='')}/merge_requests/42"][
        "sha"
    ] = candidate_sha
    responses_path.write_text(json.dumps(responses), encoding="utf-8")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "cleanup changed retained CI objects" in result.stderr


def test_cleanup_rejects_missing_contract_gate_invocation(tmp_path: Path) -> None:
    repo, env, _, _ = setup_cleanup_repo(tmp_path)
    ci_path = repo / ".gitlab-ci.yml"
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8").replace(
            "    - python3 scripts/verify_master_delivery_contract.py\n",
            "",
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".gitlab-ci.yml")
    git(repo, "commit", "--amend", "--no-edit")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{candidate_sha}:refs/merge-requests/42/head",
    )
    responses_path = Path(env["FAKE_GLAB_RESPONSES"])
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    responses[f"projects/{quote(PROJECT_PATH, safe='')}/merge_requests/42"][
        "sha"
    ] = candidate_sha
    responses_path.write_text(json.dumps(responses), encoding="utf-8")

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "staging-writer-cleanup",
        "--staging-flow-exists",
        "true",
        "--cleanup-contract",
        "release-contracts/staging-writer-cleanup.yaml",
        cwd=repo,
        env=env,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "is missing 'scripts/verify_master_delivery_contract.py'" in result.stderr


def test_non_promotion_rejects_unverifiable_evidence(tmp_path: Path) -> None:
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "production-non-promotion",
        "--staging-flow-exists",
        "false",
        "--staging-flow-evidence",
        "x",
        "--not-applicable-reason",
        "repository has no staging workflow",
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "must be an auditable HTTPS URL" in result.stderr


def test_non_promotion_requires_remote_without_staging_branch(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    repo = create_repo(tmp_path, "non-promotion-repo")
    git(repo, "remote", "add", "origin", str(origin))

    result, output = run_init(
        tmp_path,
        "--target-branch",
        "main",
        "--mr-mode",
        "production-non-promotion",
        "--staging-flow-exists",
        "false",
        "--staging-flow-evidence",
        "https://gitlab.example.test/project/-/blob/main/CONTRIBUTING.md",
        "--not-applicable-reason",
        "repository workflow merges feature branches directly to main",
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(output.read_text(encoding="utf-8"))
    assert state["promotion"]["staging_flow_exists"] is False


def test_emergency_hotfix_requires_complete_backport_metadata(
    tmp_path: Path,
) -> None:
    result, output = run_init(
        tmp_path,
        "--target-branch",
        "release/20260730",
        "--mr-mode",
        "emergency-hotfix",
        "--staging-flow-exists",
        "true",
        "--emergency-approved",
        "--emergency-owner",
        "service-owner",
        "--emergency-reason",
        "production outage",
    )

    assert result.returncode == 2
    assert not output.exists()
    assert "emergency_verification is required" in result.stderr
