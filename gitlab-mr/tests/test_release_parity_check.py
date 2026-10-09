from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "release_parity_check.py"
ORIGIN_MR_IID = 101
VERIFICATION_MR_IID = 102
CANDIDATE_MR_IID = 103
PROJECT_PATH = "engineering/skills"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


def init_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)],
        text=True,
        capture_output=True,
        check=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "tests@example.com")
    git(repo, "config", "user.name", "Test User")
    write(repo, "README.md", "base\n")
    write(repo, "old.txt", "rename me\n")
    write(repo, ":(exclude)**", "base\n")
    commit_all(repo, "base")
    git(repo, "branch", "staging")
    git(repo, "branch", "release-base")
    git(repo, "remote", "add", "origin", str(origin))
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
    return repo


def run_check(
    repo: Path,
    *args: str,
    canonical_base: str = "staging",
    canonical_head: str = "canonical",
    candidate_base: str = "release-base",
    candidate_head: str = "candidate",
    api_candidate_target_sha: str | None = None,
    api_candidate_mr_sha: str | None = None,
) -> subprocess.CompletedProcess[str]:
    canonical_base_sha = git(repo, "rev-parse", canonical_base)
    canonical_head_sha = git(repo, "rev-parse", canonical_head)
    candidate_base_sha = git(repo, "rev-parse", candidate_base)
    candidate_head_sha = git(repo, "rev-parse", candidate_head)
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{canonical_head_sha}:refs/merge-requests/{ORIGIN_MR_IID}/head",
        f"{canonical_head_sha}:refs/merge-requests/{VERIFICATION_MR_IID}/head",
        f"{candidate_head_sha}:refs/merge-requests/{CANDIDATE_MR_IID}/head",
        f"{candidate_base_sha}:refs/heads/main",
    )
    project = quote(PROJECT_PATH, safe="")
    mr_path = f"projects/{project}/merge_requests"
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
    origin_mr = {
        "iid": ORIGIN_MR_IID,
        "state": "merged",
        "target_branch": "staging",
        "source_branch": "canonical",
        "sha": canonical_head_sha,
        "diff_refs": {"base_sha": canonical_base_sha},
    }
    verification_mr = {
        "iid": VERIFICATION_MR_IID,
        "state": "merged",
        "target_branch": "staging",
        "source_branch": "canonical",
        "sha": canonical_head_sha,
        "diff_refs": {"base_sha": canonical_base_sha},
    }
    candidate_mr = {
        "iid": CANDIDATE_MR_IID,
        "state": "opened",
        "target_branch": "main",
        "source_branch": "candidate",
        "sha": api_candidate_mr_sha or candidate_head_sha,
        "diff_refs": {"base_sha": candidate_base_sha},
    }
    responses = {
        f"{mr_path}/{ORIGIN_MR_IID}": origin_mr,
        f"{mr_path}/{VERIFICATION_MR_IID}": verification_mr,
        f"{mr_path}/{CANDIDATE_MR_IID}": candidate_mr,
        # The GitLab list endpoint omits diff_refs even when the detail
        # endpoint supplies the canonical base needed for parity.
        f"{mr_path}?{query}": [
            {key: value for key, value in origin_mr.items() if key != "diff_refs"},
            {
                key: value
                for key, value in verification_mr.items()
                if key != "diff_refs"
            },
        ],
        f"projects/{project}/repository/branches/main": {
            "name": "main",
            "commit": {"id": api_candidate_target_sha or candidate_base_sha},
        },
    }
    responses_path = repo.parent / "fake-glab-responses.json"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")
    env = os.environ.copy()
    env["FAKE_GLAB_RESPONSES"] = str(responses_path)
    env["PATH"] = f"{repo.parent / 'fake-bin'}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-path",
            PROJECT_PATH,
            "--canonical-verification-mr",
            str(VERIFICATION_MR_IID),
            "--candidate-mr",
            str(CANDIDATE_MR_IID),
            *args,
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def create_matching_branches(repo: Path) -> None:
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "service.txt", "feature enabled\n")
    commit_all(repo, "canonical feature")

    git(repo, "switch", "release-base")
    write(repo, "release-only.txt", "existing release baseline\n")
    commit_all(repo, "release baseline divergence")
    git(repo, "switch", "-c", "candidate")
    write(repo, "service.txt", "feature enabled\n")
    commit_all(repo, "promote feature")


def write_contract(repo: Path, contract: dict) -> str:
    relative = "release-contracts/sample.yaml"
    write(repo, relative, yaml.safe_dump(contract))
    commit_all(repo, "add release contract")
    return relative


def test_matching_exact_change_passes_with_diverged_baselines(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)

    result = run_check(repo)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "release code parity: PASS" in result.stdout
    assert "matched paths: 1" in result.stdout


def test_missing_candidate_path_blocks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "service.txt", "feature enabled\n")
    write(repo, "config/application-staging.yml", "enabled: true\n")
    commit_all(repo, "canonical feature")

    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "service.txt", "feature enabled\n")
    commit_all(repo, "incomplete promotion")

    result = run_check(repo)

    assert result.returncode == 1
    assert "missing-from-candidate: config/application-staging.yml" in result.stdout


def test_different_feature_change_blocks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "service.txt", "policyEnabled: true\n")
    commit_all(repo, "canonical feature")

    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "service.txt", "policyEnabled: false\n")
    commit_all(repo, "incorrect promotion")

    result = run_check(repo)

    assert result.returncode == 1
    assert "change-differs: service.txt" in result.stdout


def test_declared_environment_difference_and_external_gate_passes_code_check(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "service.txt", "feature enabled\n")
    write(repo, "config/application-staging.yml", "policyEnabled: true\n")
    commit_all(repo, "canonical feature")

    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "service.txt", "feature enabled\n")
    write(repo, "config/application-prod.yml", "policyEnabled: true\n")
    commit_all(repo, "release promotion")

    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application-*.yml"],
                "reason": "environment-specific profile names",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application-staging.yml",
                        "absent": True,
                    },
                    {
                        "ref": "candidate",
                        "path": "config/application-prod.yml",
                        "contains": ["policyEnabled: true"],
                        "sha256": hashlib.sha256(b"policyEnabled: true\n").hexdigest(),
                    },
                ],
            }
        ],
        "external_gates": [
            {
                "name": "production-policy",
                "required": True,
                "evidence": "https://example.test/policy",
                "expected_contains": ['"runtimeEnabled": true'],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 0, result.stderr + result.stdout
    assert "allowed differences: 2" in result.stdout
    assert "LIVE VERIFY external gate 'production-policy'" in result.stdout
    assert "contract: release-contracts/sample.yaml" in result.stdout

    json_result = run_check(repo, "--contract", contract_path, "--json")
    report = json.loads(json_result.stdout)
    assert json_result.returncode == 0
    assert report["code_status"] == "pass"
    assert report["status"] == "live-audit-required"


def test_external_gate_cannot_self_declare_ready_status(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract = {
        "version": 1,
        "feature": "sample",
        "external_gates": [
            {
                "name": "production-policy",
                "required": True,
                "status": "ready",
                "evidence": "https://example.test/policy",
                "expected_contains": ['"runtimeEnabled": true'],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 2
    assert "contains unknown keys: status" in result.stderr


def test_required_external_gate_needs_expected_value(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = write_contract(
        repo,
        {
            "version": 1,
            "feature": "sample",
            "external_gates": [
                {
                    "name": "production-policy",
                    "required": True,
                    "evidence": "https://example.test/policy",
                }
            ],
        },
    )

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "expected_contains is required" in result.stderr


def test_allowed_difference_without_value_assertion_is_rejected(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config/application-staging.yml", "policyEnabled: true\n")
    commit_all(repo, "canonical feature")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "config/application-prod.yml", "policyEnabled: true\n")
    commit_all(repo, "release promotion")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application-*.yml"],
                "reason": "environment-specific profile names",
                "owner": "sample-team",
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 2
    assert "required_content must be a non-empty list" in result.stderr


def test_unbounded_allow_pattern_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["**/*"],
                "reason": "hide everything",
                "owner": "sample-team",
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 2
    assert "literal top-level path segment" in result.stderr


def test_whitespace_sensitive_yaml_change_blocks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config.yml", "feature:\n  enabled: true\n")
    commit_all(repo, "canonical feature")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "config.yml", "feature:\nenabled: true\n")
    commit_all(repo, "incorrect indentation")

    result = run_check(repo)

    assert result.returncode == 1
    assert "change-differs: config.yml" in result.stdout


def test_rename_cannot_be_replaced_by_copy_that_keeps_old_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    git(repo, "mv", "old.txt", "new.txt")
    commit_all(repo, "rename canonical file")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "new.txt", "rename me\n")
    commit_all(repo, "copy without deletion")

    result = run_check(repo)

    assert result.returncode == 1
    assert "missing-from-candidate: old.txt" in result.stdout


def test_base_must_be_ancestor_of_head(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    git(repo, "switch", "-c", "wrong-base", "staging")
    write(repo, "unrelated.txt", "wrong base\n")
    commit_all(repo, "wrong baseline")

    result = run_check(repo, canonical_base="wrong-base")

    assert result.returncode == 2
    assert "is not an ancestor" in result.stderr


def test_literal_path_cannot_act_as_git_pathspec(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, ":(exclude)**", "canonical\n")
    commit_all(repo, "canonical special path")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, ":(exclude)**", "candidate\n")
    commit_all(repo, "candidate special path")

    result = run_check(repo)

    assert result.returncode == 1
    assert "change-differs: :(exclude)**" in result.stdout


def test_single_segment_wildcard_does_not_cross_directories(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config/secrets/private.yml", "enabled: true\n")
    commit_all(repo, "canonical nested config")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "config/secrets/private.yml", "enabled: false\n")
    commit_all(repo, "incorrect nested config")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/*.yml"],
                "reason": "top-level environment configs only",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application.yml",
                        "absent": True,
                    }
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 1
    assert "config/secrets/private.yml" in result.stdout


def test_every_allowed_candidate_path_needs_exact_assertion(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config/application-staging.yml", "policyEnabled: true\n")
    commit_all(repo, "canonical config")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "config/application-prod-us.yml", "policyEnabled: true\n")
    write(repo, "config/application-prod-eu.yml", "policyEnabled: false\n")
    commit_all(repo, "incomplete release configs")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application-*.yml"],
                "reason": "environment profile names",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application-staging.yml",
                        "absent": True,
                    },
                    {
                        "ref": "candidate",
                        "path": "config/application-prod-us.yml",
                        "contains": ["policyEnabled: true"],
                        "sha256": hashlib.sha256(b"policyEnabled: true\n").hexdigest(),
                    },
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 1
    assert "application-prod-eu.yml" in result.stdout
    assert "needs an exact candidate required_content check" in result.stdout


def test_contains_text_cannot_replace_expected_file_hash(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config/application-staging.yml", "policyEnabled: true\n")
    commit_all(repo, "canonical config")
    git(repo, "switch", "-c", "candidate", "release-base")
    write(repo, "config/application-prod.yml", "# policyEnabled: true\n")
    commit_all(repo, "commented release config")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application-*.yml"],
                "reason": "environment profile names",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application-staging.yml",
                        "absent": True,
                    },
                    {
                        "ref": "candidate",
                        "path": "config/application-prod.yml",
                        "contains": ["policyEnabled: true"],
                        "sha256": hashlib.sha256(b"policyEnabled: true\n").hexdigest(),
                    },
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 1
    assert "sha256 is" in result.stdout


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = "release-contracts/sample.yaml"
    write(
        repo,
        contract_path,
        "version: 1\nversion: 1\nfeature: sample\n",
    )
    commit_all(repo, "add invalid release contract")

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "duplicate key" in result.stderr


def test_boolean_contract_version_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = "release-contracts/sample.yaml"
    write(
        repo,
        contract_path,
        "version: true\nfeature: sample\n",
    )
    commit_all(repo, "add invalid release contract")

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "version must be integer 1" in result.stderr


def test_non_boolean_temporary_flag_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["service.txt"],
                "reason": "sample",
                "owner": "sample-team",
                "temporary": "true",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "service.txt",
                        "contains": ["feature enabled"],
                    }
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 2
    assert "temporary must be boolean" in result.stderr


def test_empty_content_assertion_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract = {
        "version": 1,
        "feature": "sample",
        "required_content": [
            {
                "ref": "candidate",
                "path": "service.txt",
                "contains": [""],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", str(contract_path))

    assert result.returncode == 2
    assert "must contain non-empty strings" in result.stderr


def test_contract_must_exist_in_candidate_commit(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = "release-contracts/sample.yaml"
    write(
        repo,
        contract_path,
        yaml.safe_dump({"version": 1, "feature": "sample"}),
    )

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "is absent from candidate commit" in result.stderr


def test_candidate_target_ref_must_match_gitlab_branch_api(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)

    result = run_check(
        repo,
        api_candidate_target_sha=git(repo, "rev-parse", "staging"),
    )

    assert result.returncode == 2
    assert "candidate target branch" in result.stderr
    assert "expected" in result.stderr


def test_candidate_source_ref_must_match_mr_sha(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)

    result = run_check(
        repo,
        api_candidate_mr_sha=git(repo, "rev-parse", "staging"),
    )

    assert result.returncode == 2
    assert "candidate MR" in result.stderr
    assert "expected" in result.stderr


def test_symlink_cannot_satisfy_regular_file_assertion(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "config/application.yml", "policyEnabled: true\n")
    commit_all(repo, "canonical config")
    git(repo, "switch", "-c", "candidate", "release-base")
    (repo / "config").mkdir()
    os.symlink("policyEnabled: true\n", repo / "config" / "application.yml")
    commit_all(repo, "symlinked candidate config")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application.yml"],
                "reason": "environment-specific config",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application.yml",
                        "sha256": hashlib.sha256(b"policyEnabled: true\n").hexdigest(),
                    }
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 1
    assert "mode is 120000, expected 100644" in result.stdout


def test_allowed_candidate_deletion_uses_final_tree_state(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "canonical", "staging")
    write(repo, "service.txt", "feature enabled\n")
    commit_all(repo, "canonical feature")
    git(repo, "switch", "release-base")
    write(repo, "config/obsolete-prod.yml", "enabled: false\n")
    commit_all(repo, "release baseline config")
    git(repo, "switch", "-c", "candidate")
    write(repo, "service.txt", "feature enabled\n")
    (repo / "config" / "obsolete-prod.yml").unlink()
    commit_all(repo, "promote and remove obsolete config")
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/obsolete-prod.yml"],
                "reason": "obsolete production profile",
                "owner": "sample-team",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/obsolete-prod.yml",
                        "absent": True,
                    }
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 0, result.stderr + result.stdout


def test_expired_temporary_rule_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract = {
        "version": 1,
        "feature": "sample",
        "allowed_differences": [
            {
                "paths": ["config/application.yml"],
                "reason": "temporary profile",
                "owner": "sample-team",
                "temporary": True,
                "expires": "2000-01-01",
                "required_content": [
                    {
                        "ref": "candidate",
                        "path": "config/application.yml",
                        "absent": True,
                    }
                ],
            }
        ],
    }
    contract_path = write_contract(repo, contract)

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "is not later than current UTC date" in result.stderr


def test_temporary_rule_expiry_requires_iso_date(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = write_contract(
        repo,
        {
            "version": 1,
            "feature": "sample",
            "allowed_differences": [
                {
                    "paths": ["config/application.yml"],
                    "reason": "temporary profile",
                    "owner": "sample-team",
                    "temporary": True,
                    "expires": "next Friday",
                    "required_content": [
                        {
                            "ref": "candidate",
                            "path": "config/application.yml",
                            "absent": True,
                        }
                    ],
                }
            ],
        },
    )

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "expires must use YYYY-MM-DD" in result.stderr


def test_absent_assertion_cannot_hide_mode_constraint(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = write_contract(
        repo,
        {
            "version": 1,
            "feature": "sample",
            "required_content": [
                {
                    "ref": "candidate",
                    "path": "not-present.txt",
                    "absent": True,
                    "mode": "",
                }
            ],
        },
    )

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "cannot mix absent with content assertions" in result.stderr


def test_external_gate_evidence_must_be_auditable_url(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    create_matching_branches(repo)
    contract_path = write_contract(
        repo,
        {
            "version": 1,
            "feature": "sample",
            "external_gates": [
                {
                    "name": "production-policy",
                    "required": True,
                    "evidence": "x",
                    "expected_contains": ['"runtimeEnabled": true'],
                }
            ],
        },
    )

    result = run_check(repo, "--contract", contract_path)

    assert result.returncode == 2
    assert "must be an auditable HTTPS URL" in result.stderr


def run_branch_promotion(
    tmp_path: Path,
    *,
    candidate_extra: bool = False,
    staging_extra: bool = False,
    missing_merge_sha: bool = False,
    main_only_change: bool = False,
    pipeline_success: bool = True,
    unauthorized_note: bool = False,
    rejected_note: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo = init_repo(tmp_path)
    git(repo, "switch", "-c", "develop", "staging")
    write(repo, "service.txt", "verified feature\n")
    commit_all(repo, "develop feature")
    develop_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "staging")
    git(repo, "merge", "--no-ff", "develop", "-m", "verified staging merge")
    verified_merge_sha = git(repo, "rev-parse", "HEAD")
    if staging_extra:
        write(repo, "later.txt", "unverified staging change\n")
        commit_all(repo, "later staging change")
    staging_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "main")
    if main_only_change:
        write(repo, "production-only.txt", "existing production change\n")
        commit_all(repo, "diverged production baseline")
    else:
        git(repo, "commit", "--allow-empty", "-m", "diverged production history")
    target_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "candidate", verified_merge_sha)
    if candidate_extra:
        write(repo, "candidate-only.txt", "unverified candidate change\n")
        commit_all(repo, "candidate-only change")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--force",
        "origin",
        f"{candidate_sha}:refs/merge-requests/{CANDIDATE_MR_IID}/head",
        f"{staging_sha}:refs/heads/staging",
        f"{target_sha}:refs/heads/main",
    )

    project = quote(PROJECT_PATH, safe="")
    mr_path = f"projects/{project}/merge_requests"
    pipeline_url = "https://gitlab.example.test/group/project/-/pipelines/12345"
    note_body = (
        f"<!-- staging-acceptance:PASS:v1:{VERIFICATION_MR_IID}:{verified_merge_sha}:12345 -->\n"
        f"{'验收未通过' if rejected_note else '验收通过：'} staging pipeline: {pipeline_url}"
    )
    responses = {
        f"{mr_path}/{VERIFICATION_MR_IID}": {
            "iid": VERIFICATION_MR_IID,
            "state": "merged",
            "source_branch": "develop",
            "target_branch": "staging",
            "sha": develop_sha,
            "merge_commit_sha": None if missing_merge_sha else verified_merge_sha,
            "merged_at": "2026-10-08T01:00:00Z",
            "web_url": "https://gitlab.example.test/group/project/-/merge_requests/102",
            "merged_by": {"id": 277, "username": "qlv"},
        },
        f"{mr_path}/{CANDIDATE_MR_IID}": {
            "iid": CANDIDATE_MR_IID,
            "state": "opened",
            "source_branch": "candidate",
            "target_branch": "main",
            "sha": candidate_sha,
        },
        f"projects/{project}/repository/branches/staging": {
            "commit": {"id": staging_sha}
        },
        f"projects/{project}/repository/branches/main": {"commit": {"id": target_sha}},
        f"projects/{project}/pipelines/12345": {
            "id": 12345,
            "sha": verified_merge_sha,
            "ref": "staging",
            "status": "success" if pipeline_success else "failed",
            "web_url": pipeline_url,
        },
        f"{mr_path}/{VERIFICATION_MR_IID}/notes/67890": {
            "id": 67890,
            "system": False,
            "noteable_iid": VERIFICATION_MR_IID,
            "created_at": "2026-10-08T02:00:00Z",
            "body": note_body,
            "author": {"id": 278 if unauthorized_note else 277},
        },
    }
    responses_path = repo.parent / "fake-glab-responses.json"
    responses_path.write_text(json.dumps(responses), encoding="utf-8")
    env = os.environ.copy()
    env["FAKE_GLAB_RESPONSES"] = str(responses_path)
    env["PATH"] = f"{repo.parent / 'fake-bin'}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-path",
            PROJECT_PATH,
            "--canonical-verification-mr",
            str(VERIFICATION_MR_IID),
            "--candidate-mr",
            str(CANDIDATE_MR_IID),
            "--branch-promotion",
            "--staging-pipeline-id",
            "12345",
            "--staging-acceptance-note-id",
            "67890",
            "--json",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_branch_promotion_passes_with_diverged_main_and_no_old_mr_ref(
    tmp_path: Path,
) -> None:
    result = run_branch_promotion(tmp_path)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["provenance"]["mode"] == "branch-promotion"
    assert report["refs"]["canonical"] == report["refs"]["candidate"]
    assert report["refs"]["canonical_base"] == report["refs"]["candidate_base"]
    assert report["matched_paths"] == ["service.txt"]
    assert report["provenance"]["merge_result"]["tree_sha"]


def test_branch_promotion_rejects_main_only_merge_result(tmp_path: Path) -> None:
    result = run_branch_promotion(tmp_path, main_only_change=True)

    assert result.returncode == 2
    assert "production merge result differs" in result.stderr


def test_branch_promotion_requires_successful_postmerge_pipeline(
    tmp_path: Path,
) -> None:
    result = run_branch_promotion(tmp_path, pipeline_success=False)

    assert result.returncode == 2
    assert "staging postmerge pipeline" in result.stderr


def test_branch_promotion_rejects_unauthorized_acceptance_note(tmp_path: Path) -> None:
    result = run_branch_promotion(tmp_path, unauthorized_note=True)

    assert result.returncode == 2
    assert "authored by the staging merger" in result.stderr


def test_branch_promotion_rejects_negative_acceptance_note(tmp_path: Path) -> None:
    result = run_branch_promotion(tmp_path, rejected_note=True)

    assert result.returncode == 2
    assert "not bound to the merge and pipeline" in result.stderr


def test_branch_promotion_rejects_candidate_only_commit(tmp_path: Path) -> None:
    result = run_branch_promotion(tmp_path, candidate_extra=True)

    assert result.returncode == 2
    assert "is not verified staging merge" in result.stderr


def test_branch_promotion_rejects_staging_change_after_verification(
    tmp_path: Path,
) -> None:
    result = run_branch_promotion(tmp_path, staging_extra=True)

    assert result.returncode == 2
    assert "staging branch SHA" in result.stderr


def test_branch_promotion_requires_merge_commit_sha(tmp_path: Path) -> None:
    result = run_branch_promotion(tmp_path, missing_merge_sha=True)

    assert result.returncode == 2
    assert "canonical verification MR.merge_commit_sha" in result.stderr
