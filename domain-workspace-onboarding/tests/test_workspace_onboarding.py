from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
FIXTURES = SKILL / "references/fixtures"
SHA = "0123456789abcdef0123456789abcdef01234567"
REAL_STANDARDS = os.environ.get("DOMAIN_WORKSPACE_STANDARDS_ROOT", "")
REAL_STANDARDS_SHA = os.environ.get("DOMAIN_WORKSPACE_STANDARDS_SHA", "")
REAL_STANDARDS_CASES = (
    "test_security_iot_joint_assess_is_read_only_and_preserves_candidates",
    "test_assess_normalizes_equivalent_repository_urls",
    "test_assess_rejects_new_workspace_without_boundary_or_repositories",
    "test_real_standards_init_uses_official_schema_and_validator",
    "test_real_standards_init_rejects_unchanged_template",
    "test_real_standards_propose_uses_snapshot_without_authorizing_creation",
    "test_real_standards_init_negative_gates[unapproved]",
    "test_real_standards_init_negative_gates[duplicate-boundary]",
    "test_real_standards_init_negative_gates[owner]",
    "test_real_standards_init_negative_gates[reuse]",
    "test_archive_cli_exception_is_fail_closed",
)

sys.path.insert(0, str(SCRIPTS))
import archive_gate as archive_module  # noqa: E402
import assess as assess_module  # noqa: E402
import common as common_module  # noqa: E402
import gate as gate_module  # noqa: E402
import validation_gate as validation_module  # noqa: E402
import verify_standards as standards_module  # noqa: E402
from common import (  # noqa: E402
    canonical_git_url,
    materialize_git_commit,
    validate_raw_observation,
)
from gate import (  # noqa: E402
    archive_requirements,
    init_requirements,
    validate_evidence,
)
from ingest_filter import path_disposition  # noqa: E402
from validation_gate import validate_workspace_binding  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def record_real_standards_sha(
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    if REAL_STANDARDS_SHA:
        record_testsuite_property("domain_workspace_standards_sha", REAL_STANDARDS_SHA)


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def run_pep723_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", str(SCRIPTS / name), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def raw_observation(
    tmp_path: Path, name: str, status: str, observed: str | None = None
) -> dict:
    observed = observed or f"{name}:verified"
    evidence_file = tmp_path / f"{name}.txt"
    evidence_file.write_text(f"{observed}\n", encoding="utf-8")
    return {
        "status": status,
        "evidence": f"https://evidence.invalid/{name}",
        "observed_value": observed,
        "evidence_file": str(evidence_file),
        "evidence_sha256": hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
        "verified_at": datetime.now(UTC).isoformat(),
        "verifier": "test readback",
    }


def read_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def make_catalog_fixture(tmp_path: Path, lifecycle: str = "active") -> Path:
    root = tmp_path / "standards"
    write_yaml(
        root / "catalog/workspaces.yaml",
        {
            "schema_version": 1,
            "workspaces": [
                {
                    "name": "existing-domain",
                    "repository": "git@gitlab.addx.ai:domain-workspaces/existing-domain.git",
                    "domain_type": "stable-business-domain",
                    "lifecycle": lifecycle,
                    "boundary": {
                        "key": "existing-boundary",
                        "summary": "Existing durable business boundary.",
                        "includes": ["Existing outcomes"],
                        "excludes": ["Shared platform ownership"],
                    },
                }
            ],
        },
    )
    write_yaml(
        root / "catalog/repository-references.yaml",
        {
            "schema_version": 1,
            "repositories": [
                {
                    "repo": "git@gitlab.addx.ai:cloud/iot-service-unified.git",
                    "references": [
                        {
                            "workspace": "existing-domain",
                            "name": "iot-service",
                            "role": "dependency",
                            "reason": "Existing consumer contract.",
                            "boundary": "Existing domain consumption only.",
                            "taxonomy": {"field": "surface", "value": "supporting"},
                        }
                    ],
                }
            ],
        },
    )
    return root


def governance(root: Path) -> tuple[dict, dict]:
    return (
        read_yaml(root / "catalog/workspaces.yaml"),
        read_yaml(root / "catalog/repository-references.yaml"),
    )


def proposal() -> dict:
    return {
        "proposal_id": "candidate-domain-20260804",
        "workspace": {
            "name": "candidate-domain",
            "governance_owner": {
                "status": "confirmed",
                "name": "Owner",
                "gitlab_username": "owner",
                "evidence": "https://evidence.example/owners/candidate-domain",
            },
            "boundary": {
                "key": "candidate-boundary",
                "summary": "Candidate durable boundary.",
                "durable_reason": "Independent roadmap and lifecycle.",
                "includes": ["Candidate outcomes"],
                "excludes": ["Existing domain consumption"],
            },
        },
        "repositories": [
            {
                "name": "iot-service",
                "repo": "git@gitlab.addx.ai:cloud/iot-service-unified.git",
                "role": "dependency",
                "reason": "Reusable platform contract.",
                "boundary": "Candidate platform governance only.",
            }
        ],
        "repository_reuse": [
            {
                "repo": "git@gitlab.addx.ai:cloud/iot-service-unified.git",
                "existing_workspaces": ["existing-domain"],
                "reuse_reason": "Provider and consumer boundaries differ.",
                "boundary": "Candidate platform governance only.",
            }
        ],
        "decision": {
            "outcome": "new-workspace",
            "approval": {
                "status": "approved",
                "approved_by": "Group Owner",
                "reference": "https://approvals.invalid/candidate-domain",
            },
            "gitlab_creation": {
                "namespace": "domain-workspaces",
                "project_path": "candidate-domain",
                "creator": "owner",
                "creator_role": "group-owner",
                "verified_at": date.today().isoformat(),
                "evidence": "https://evidence.example/permissions/candidate-domain",
            },
        },
    }


def real_proposal(root: Path) -> dict:
    value = read_yaml(root / "templates/workspace-proposal.yaml")
    value["proposal_id"] = "candidate-shared-runtime-20260804"
    value["workspace"]["name"] = "candidate-shared-runtime"
    value["workspace"]["display_name"] = "Candidate Shared Runtime"
    value["workspace"]["boundary"]["key"] = "candidate-shared-runtime-platform"
    value["workspace"]["governance_owner"] = {
        "status": "confirmed",
        "name": "Candidate Domain Owner",
        "gitlab_username": "candidate-domain-owner",
        "evidence": "https://gitlab.addx.ai/domain-workspaces/standards/-/issues/999#owner",
    }
    value["decision"]["approval"] = {
        "status": "approved",
        "approved_by": "Domain Workspaces Group Owner",
        "reference": "https://gitlab.addx.ai/domain-workspaces/standards/-/issues/999#approval",
    }
    value["decision"]["gitlab_creation"] = {
        "namespace": "domain-workspaces",
        "project_path": "candidate-shared-runtime",
        "creator": "candidate-domain-owner",
        "creator_role": "group-owner",
        "verified_at": date.today().isoformat(),
        "evidence": "https://gitlab.addx.ai/groups/domain-workspaces/-/group_members",
    }
    value["migration"]["independent_owner_agent"]["write_scope"] = [
        "domain-workspaces/candidate-shared-runtime"
    ]
    return value


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_security_iot_joint_assess_is_read_only_and_preserves_candidates(tmp_path: Path) -> None:
    root = Path(REAL_STANDARDS)
    result = run_script(
        "assess.py",
        "--standards-root",
        str(root),
        "--request",
        str(FIXTURES / "security-iot-joint-assess.yaml"),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "new-workspace"
    assert payload["creation_authorized"] is False
    assert payload["slug_or_boundary_guessed"] is False
    assert [row["domain_type_candidate"] for row in payload["candidate_boundaries"]] == [
        "stable-business-domain",
        "shared-platform-domain",
    ]
    overlap = next(row for row in payload["repository_overlaps"] if "iot-service" in row["repo"])
    assert overlap["existing_workspaces"]


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_assess_normalizes_equivalent_repository_urls(tmp_path: Path) -> None:
    root = Path(REAL_STANDARDS)
    request = read_yaml(FIXTURES / "security-iot-joint-assess.yaml")
    request["repositories"] = [
        "https://gitlab.addx.ai/cloud/iot-service-unified.git",
        "git@gitlab.addx.ai:apps/g0-flutter.git",
    ]
    path = tmp_path / "assessment.yaml"
    write_yaml(path, request)
    result = run_script("assess.py", "--standards-root", str(root), "--request", str(path))
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["repository_overlaps"][0]["existing_workspaces"]


def test_git_url_identity_keeps_non_default_ports_distinct() -> None:
    trusted = canonical_git_url("git@gitlab.addx.ai:domain-workspaces/standards.git")
    assert canonical_git_url(
        "ssh://git@gitlab.addx.ai:2222/domain-workspaces/standards.git"
    ) != trusted
    assert canonical_git_url(
        "https://gitlab.addx.ai:8443/domain-workspaces/standards.git"
    ) != trusted
    with pytest.raises(ValueError, match="must not embed"):
        canonical_git_url("https://user:token@gitlab.addx.ai/domain-workspaces/standards.git")


@pytest.mark.parametrize(
    ("duplicate", "shared", "expected"),
    [
        (False, False, "none"),
        (False, True, "partial"),
        (True, False, "duplicate"),
        (True, True, "duplicate"),
    ],
)
def test_assess_relationship_keeps_duplicate_precedence(
    duplicate: bool, shared: bool, expected: str
) -> None:
    workspace_name = "existing-domain"
    repository = "git@gitlab.addx.ai:cloud/iot-service-unified.git"
    identity = canonical_git_url(repository)
    catalog = {
        "workspaces": [
            {
                "name": workspace_name,
                "lifecycle": "active",
                "boundary": {"key": "existing-boundary"},
            }
        ]
    }
    boundary = {
        "proposed_name": workspace_name if duplicate else "candidate-domain",
        "key": "candidate-boundary",
    }
    references = [{"workspace": workspace_name}] if shared else []
    comparisons, found_duplicate = assess_module._catalog_comparisons(
        catalog,
        boundary,
        [repository],
        {repository: identity},
        {identity: references},
    )
    assert comparisons[0]["relationship"] == expected
    assert found_duplicate is duplicate


def test_validate_raw_observation_keeps_early_return_and_error_accumulation(
    tmp_path: Path,
) -> None:
    early_errors: list[str] = []
    validate_raw_observation(
        "record",
        {"status": "pending", "evidence": "http://user@example.invalid"},
        early_errors,
        "verified",
    )
    assert early_errors == ["record requires status=verified"]

    errors: list[str] = []
    validate_raw_observation(
        "record",
        {
            "status": "verified",
            "evidence": "http://user@example.invalid",
            "evidence_file": str(tmp_path / "missing"),
            "verified_at": "not-a-date",
        },
        errors,
        "verified",
        "expected-observation",
    )
    assert errors == [
        "record evidence must be an auditable HTTPS URL",
        "record.observed_value is required",
        "record.verifier is required",
        "record.observed_value must equal 'expected-observation'",
        "record.evidence_file must be an absolute regular file",
        "record.verified_at must be ISO-8601",
    ]


def write_junit_report(
    path: Path,
    skipped: int,
    reason: str,
    *,
    standards_sha: str = "",
    total: int = 77,
) -> None:
    names = [
        *REAL_STANDARDS_CASES[: min(total, len(REAL_STANDARDS_CASES))],
        *(f"unit-{index}" for index in range(max(total - 11, 0))),
    ]
    cases = "".join(
        f'<testcase name="{name}">'
        + (f'<skipped message="{reason}" />' if index < skipped else "")
        + "</testcase>"
        for index, name in enumerate(names)
    )
    properties = (
        "<properties>"
        f'<property name="domain_workspace_standards_sha" value="{standards_sha}" />'
        "</properties>"
        if standards_sha
        else ""
    )
    path.write_text(
        f'<testsuites><testsuite tests="{total}" failures="0" errors="0" '
        f'skipped="{skipped}">{properties}{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_ci_report_requires_explicit_offline_real_standards_skip_count(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    output = tmp_path / "mode.json"
    write_junit_report(report, 11, "set DOMAIN_WORKSPACE_STANDARDS_ROOT")
    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "offline",
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["degraded"] is True

    write_junit_report(report, 10, "set DOMAIN_WORKSPACE_STANDARDS_ROOT")
    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "offline",
        "--output",
        str(output),
    )
    assert result.returncode == 1
    assert "skip exactly 11" in result.stdout


def test_ci_report_online_mode_requires_sha_and_zero_skips(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    output = tmp_path / "mode.json"
    write_junit_report(report, 0, "", standards_sha="f" * 40)
    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "online",
        "--standards-sha",
        "f" * 40,
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["degraded"] is False
    assert payload["skipped"] == 0

    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "online",
        "--output",
        str(output),
    )
    assert result.returncode == 1
    assert "exact standards SHA" in result.stdout

    write_junit_report(report, 0, "", standards_sha="f" * 40, total=76)
    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "online",
        "--standards-sha",
        "f" * 40,
        "--output",
        str(output),
    )
    assert result.returncode == 1
    assert "at least 77 tests" in result.stdout


def test_ci_report_rejects_aggregate_skip_counts_without_testcase_nodes(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.xml"
    output = tmp_path / "mode.json"
    report.write_text(
        '<testsuites><testsuite tests="77" failures="0" errors="0" skipped="11" />'
        "</testsuites>",
        encoding="utf-8",
    )
    result = run_pep723_script(
        "verify_ci_test_report.py",
        "--report",
        str(report),
        "--mode",
        "offline",
        "--output",
        str(output),
    )
    assert result.returncode == 1
    assert "aggregate counts do not match" in result.stdout


def test_materialize_git_commit_uses_exact_regular_blobs_and_preserves_mode(
    tmp_path: Path,
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    script = root / "scripts/governance-validate"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(root), "add", "scripts/governance-validate"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    script.write_text("untrusted working tree mutation\n", encoding="utf-8")

    with materialize_git_commit(root, sha) as snapshot:
        materialized = snapshot / "scripts/governance-validate"
        assert materialized.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
        assert os.access(materialized, os.X_OK)

    assert not snapshot.exists()


def test_materialize_git_commit_rejects_symlinks_and_oversized_blobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "large.txt").write_text("1234", encoding="utf-8")
    (root / "link").symlink_to("large.txt")
    subprocess.run(["git", "-C", str(root), "add", "large.txt", "link"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "unsafe entries"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="unsupported entry"):
        with materialize_git_commit(root, sha):
            pass

    subprocess.run(["git", "-C", str(root), "rm", "link"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "regular blobs only"], check=True)
    regular_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(common_module, "MAX_SNAPSHOT_BLOB_BYTES", 3)
    with pytest.raises(ValueError, match="blob exceeds materialization limit"):
        with materialize_git_commit(root, regular_sha):
            pass


def test_materialize_git_commit_rejects_unsafe_and_normalized_duplicate_paths() -> None:
    with pytest.raises(ValueError, match="unsafe path"):
        common_module._validate_snapshot_names(
            [("100644", "a" * 40, 1, "../escape")]
        )
    with pytest.raises(ValueError, match="duplicate normalized path"):
        common_module._validate_snapshot_names(
            [
                ("100644", "a" * 40, 1, "docs/Owner.md"),
                ("100644", "b" * 40, 1, "docs/owner.md"),
            ]
        )


def test_materialize_git_commit_enforces_streamed_file_and_total_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "one.txt").write_text("12", encoding="utf-8")
    (root / "two.txt").write_text("34", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "one.txt", "two.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "two blobs"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    monkeypatch.setattr(common_module, "MAX_SNAPSHOT_FILES", 1)
    with pytest.raises(ValueError, match="file limit"):
        with materialize_git_commit(root, sha):
            pass

    monkeypatch.setattr(common_module, "MAX_SNAPSHOT_FILES", 5_000)
    monkeypatch.setattr(common_module, "MAX_SNAPSHOT_TOTAL_BYTES", 3)
    with pytest.raises(ValueError, match="byte limit"):
        with materialize_git_commit(root, sha):
            pass


@pytest.mark.parametrize("cumulative", [False, True])
def test_git_tree_stream_rejects_terminated_oversized_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cumulative: bool
) -> None:
    record = b"100644 blob " + (b"a" * 40) + b" 1\t" + (b"n" * 30)
    output = record + b"\0"
    if cumulative:
        output += record + b"\0"

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(output)

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("completed fake process must not be killed")

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(common_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    if cumulative:
        monkeypatch.setattr(common_module, "MAX_GIT_TREE_RECORD_BYTES", len(record))
        monkeypatch.setattr(
            common_module, "MAX_GIT_TREE_TOTAL_RECORD_BYTES", len(record) * 2 - 1
        )
        expected = "path metadata limit"
    else:
        monkeypatch.setattr(common_module, "MAX_GIT_TREE_RECORD_BYTES", len(record) - 1)
        expected = "path limit"
    with pytest.raises(ValueError, match=expected):
        common_module._git_tree_entries(tmp_path, "a" * 40)


@pytest.mark.parametrize(
    "raw_record",
    [
        b"malformed",
        b"100644 blob " + (b"a" * 40) + b" not-a-size\tfile.txt",
    ],
)
def test_git_tree_record_parser_keeps_invalid_entry_contract(raw_record: bytes) -> None:
    with pytest.raises(ValueError, match=common_module.INVALID_GIT_TREE_ENTRY):
        common_module._parse_git_tree_record(raw_record)


def test_materialize_git_commit_cleans_partial_output_on_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "one.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "one.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "one blob"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    created: list[Path] = []
    original_temporary_directory = common_module.tempfile.TemporaryDirectory

    class TrackingTemporaryDirectory(original_temporary_directory):
        def __enter__(self) -> str:
            directory = super().__enter__()
            created.append(Path(directory))
            return directory

    def fail_copy(_source: object, target: Path, _size: int, _name: str) -> None:
        target.write_bytes(b"partial")
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        common_module.tempfile, "TemporaryDirectory", TrackingTemporaryDirectory
    )
    monkeypatch.setattr(common_module, "_copy_exact_blob", fail_copy)
    with pytest.raises(OSError, match="simulated write failure"):
        with materialize_git_commit(root, sha):
            pass
    assert created and all(not directory.exists() for directory in created)


def test_materialize_and_verifier_reject_git_replace_refs(tmp_path: Path) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    content = root / "validator"
    content.write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "validator"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "trusted blob"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    original_blob = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{sha}:validator"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    replacement_blob = subprocess.run(
        ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
        check=True,
        capture_output=True,
        input="hostile\n",
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(root), "replace", original_blob, replacement_blob], check=True
    )

    with materialize_git_commit(root, sha) as snapshot:
        assert (snapshot / "validator").read_text(encoding="utf-8") == "trusted\n"
    errors: list[str] = []
    standards_module._validate_local_state(root, sha, errors)
    assert "standards checkout contains Git replace refs" in errors


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("core.fsmonitor", "{marker}"),
        ("credential.helper", "!touch {marker}"),
    ],
)
def test_standards_verifier_rejects_executable_local_git_config_without_running_it(
    tmp_path: Path, key: str, value: str
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    marker = tmp_path / "executed"
    subprocess.run(
        ["git", "-C", str(root), "config", key, value.format(marker=marker)],
        check=True,
    )
    errors = standards_module._local_git_config_errors(root)
    assert errors == [
        f"standards Git config contains unsafe key: {key}"
    ]
    assert not marker.exists()


def test_standards_verifier_rejects_linked_worktree_config_without_running_it(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    primary.mkdir()
    subprocess.run(["git", "-C", str(primary), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.name", "Test"], check=True
    )
    (primary / "tracked.txt").write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(primary), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-qm", "baseline"], check=True)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", "-q", "-b", "linked", str(linked)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(linked), "config", "extensions.worktreeConfig", "true"],
        check=True,
    )
    marker = tmp_path / "executed"
    subprocess.run(
        [
            "git",
            "-C",
            str(linked),
            "config",
            "--worktree",
            "filter.hostile.clean",
            f"!touch {marker}",
        ],
        check=True,
    )
    assert standards_module._local_git_config_errors(linked) == [
        "cannot validate standards Git config: standards linked worktrees are not allowed"
    ]
    assert not marker.exists()


def test_standards_verifier_never_queries_an_untrusted_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "standards"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://untrusted.invalid/domain-workspaces/standards.git",
        ],
        check=True,
    )
    errors: list[str] = []
    origin, origin_validated = standards_module._validated_origin(root, errors)

    def fail_remote(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("untrusted origin must not reach git ls-remote")

    monkeypatch.setattr(standards_module.subprocess, "run", fail_remote)
    remote_head = standards_module._validated_remote_head(
        root,
        origin,
        origin_validated,
        "a" * 40,
        True,
        errors,
    )
    assert origin_validated is False
    assert remote_head is None
    assert errors == ["standards origin does not match the trusted repository identity"]


def test_standards_result_preserves_errors_for_an_invalid_origin(
    tmp_path: Path,
) -> None:
    errors = ["unsupported Git remote URL"]
    payload = standards_module._result_payload(
        tmp_path,
        "ext::sh -c unsafe",
        False,
        SHA,
        None,
        b"",
        errors,
    )
    assert payload["compatible"] is False
    assert payload["errors"] == errors
    assert payload["origin_identity_sha256"] is None


def test_trusted_validator_environment_excludes_ci_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_READ_TOKEN", "do-not-inherit")
    monkeypatch.setenv("CI_JOB_TOKEN", "do-not-inherit")
    environment = common_module.trusted_validator_env()
    assert "GITLAB_READ_TOKEN" not in environment
    assert "CI_JOB_TOKEN" not in environment
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_workspace_remote_environment_excludes_ci_credentials_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_READ_TOKEN", "do-not-inherit")
    monkeypatch.setenv("CI_JOB_TOKEN", "do-not-inherit")
    monkeypatch.setenv("GIT_SSH_COMMAND", "touch should-not-run")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/should-not-be-inherited")
    environment = common_module.workspace_remote_git_env()
    assert "GITLAB_READ_TOKEN" not in environment
    assert "CI_JOB_TOKEN" not in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_ALLOW_PROTOCOL"] == "https:ssh"
    assert environment["GIT_SSH_COMMAND"].startswith("ssh -F /dev/null")
    assert "IdentityAgent=none" in environment["GIT_SSH_COMMAND"]
    assert "IdentityFile=none" in environment["GIT_SSH_COMMAND"]
    assert "SSH_AUTH_SOCK" not in environment
    assert "HOME" not in environment


def test_workspace_git_checks_never_runs_executable_workspace_git_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("trusted\n", encoding="utf-8")
    (workspace / ".gitattributes").write_text(
        "tracked.txt filter=hostile\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(workspace), "add", "tracked.txt", ".gitattributes"],
        check=True,
    )
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "remote",
            "add",
            "origin",
            "git@gitlab.addx.ai:domain-workspaces/example.git",
        ],
        check=True,
    )
    helper_marker = tmp_path / "credential-helper-executed"
    filter_marker = tmp_path / "filter-executed"
    ssh_marker = tmp_path / "ssh-command-executed"
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "credential.helper",
            f"!touch {helper_marker}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "filter.hostile.clean",
            f"touch {filter_marker}; cat",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "url.git@evil.invalid:.insteadOf",
            "git@gitlab.addx.ai:",
        ],
        check=True,
    )
    tracked.write_text("changed content with a different size\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "core.sshCommand",
            f"touch {ssh_marker}",
        ],
        check=True,
    )
    test_bin = tmp_path / "bin"
    test_bin.mkdir()
    fake_ssh = test_bin / "ssh"
    fake_ssh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{test_bin}:{os.environ['PATH']}")
    errors, actual = validation_module.workspace_git_checks(
        workspace,
        {"head_sha": sha, "remote_head_sha": sha},
        "git@gitlab.addx.ai:domain-workspaces/example.git",
    )
    assert errors == [
        "actual workspace Git working tree must be clean",
        "cannot read actual workspace remote branch",
        "declared Git evidence does not match actual local/remote heads",
    ]
    assert actual["remote_head_sha"] == ""
    assert actual["origin"] == "git@gitlab.addx.ai:domain-workspaces/example.git"
    assert not helper_marker.exists()
    assert not filter_marker.exists()
    assert not ssh_marker.exists()


def test_workspace_remote_probe_uses_clean_cwd_and_safe_git_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_remote(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["env"] = kwargs.get("env")
        observed["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(command, 0, f"{SHA}\trefs/heads/main\n", "")

    monkeypatch.setenv("DOMAIN_WORKSPACE_GIT_CREDENTIAL_HELPER", "osxkeychain")
    monkeypatch.setattr(validation_module.subprocess, "run", fake_remote)
    result = validation_module._run_workspace_remote(
        "https://gitlab.addx.ai/domain-workspaces/example.git", "main"
    )
    assert result.returncode == 0
    command = observed["command"]
    assert isinstance(command, list)
    assert "credential.helper=" in command
    assert "credential.helper=osxkeychain" in command
    assert "origin" not in command
    assert Path(str(observed["cwd"])) != tmp_path
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "CI_JOB_TOKEN" not in environment
    assert "DOMAIN_WORKSPACE_GIT_CREDENTIAL_HELPER" not in environment


def test_workspace_metadata_rejects_gitlinks_before_status(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
    )
    (workspace / "tracked.txt").write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{head},vendor/submodule",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="must not contain submodules"):
        validation_module._workspace_metadata(workspace)


def test_workspace_origin_rejects_multiple_urls(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    first = "git@untrusted.invalid:domain-workspaces/example.git"
    second = "git@gitlab.addx.ai:domain-workspaces/example.git"
    subprocess.run(
        ["git", "-C", str(workspace), "remote", "add", "origin", first],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "remote", "set-url", "--add", "origin", second],
        check=True,
    )
    native_origin = subprocess.run(
        ["git", "-C", str(workspace), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert native_origin == first
    with pytest.raises(ValueError, match="must contain exactly one URL"):
        validation_module._workspace_origin(workspace / ".git")


def test_sanitized_status_preserves_safe_filemode_semantics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
    )
    tracked = workspace / "tracked.sh"
    tracked.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tracked.chmod(0o755)
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.sh"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.filemode", ""],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.autocrlf", "yes"],
        check=True,
    )
    tracked.chmod(0o644)
    native_status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    git_dir = workspace / ".git"
    head, _branch = validation_module._workspace_head(git_dir)
    status_config = validation_module._workspace_status_config(git_dir)
    sanitized = validation_module._sanitized_workspace_status(
        workspace, git_dir, head, status_config
    )
    assert native_status == ""
    assert status_config["core.filemode"] == "false"
    assert status_config["core.autocrlf"] == "true"
    assert sanitized.returncode == 0
    assert sanitized.stdout == ""

    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.autocrlf", ""],
        check=True,
    )
    tracked.write_bytes(b"#!/bin/sh\r\nexit 0\r\n")
    native_dirty = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    false_config = validation_module._workspace_status_config(git_dir)
    sanitized_dirty = validation_module._sanitized_workspace_status(
        workspace, git_dir, head, false_config
    )
    assert native_dirty == " M tracked.sh\n"
    assert false_config["core.autocrlf"] == "false"
    assert sanitized_dirty.stdout == native_dirty


def test_workspace_metadata_rejects_hidden_or_staged_index_changes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "update-index", "--assume-unchanged", "tracked.txt"],
        check=True,
    )
    tracked.write_text("hidden uncommitted content with a different size\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must not contain special path flags"):
        validation_module._workspace_metadata(workspace)

    subprocess.run(
        ["git", "-C", str(workspace), "update-index", "--no-assume-unchanged", "tracked.txt"],
        check=True,
    )
    tracked.write_text("trusted\n", encoding="utf-8")
    staged = workspace / "staged.txt"
    staged.write_text("staged but not committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "staged.txt"], check=True)
    with pytest.raises(ValueError, match="Git index must match HEAD"):
        validation_module._workspace_metadata(workspace)


def test_workspace_metadata_rejects_split_index_with_specific_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
    )
    (workspace / "tracked.txt").write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "update-index", "--split-index"], check=True
    )
    with pytest.raises(ValueError, match="split index is not supported"):
        validation_module._workspace_metadata(workspace)
    subprocess.run(
        ["git", "-C", str(workspace), "update-index", "--no-split-index"],
        check=True,
    )
    stale_shared_index = workspace / ".git" / f"sharedindex.{'f' * 40}"
    stale_shared_index.write_text("stale and inactive\n", encoding="utf-8")
    status, _head, _branch, _origin = validation_module._workspace_metadata(workspace)
    assert status.returncode == 0
    assert status.stdout == ""


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_assess_rejects_new_workspace_without_boundary_or_repositories(tmp_path: Path) -> None:
    root = Path(REAL_STANDARDS)
    path = tmp_path / "assessment.yaml"
    write_yaml(
        path,
        {
            "schema_version": 1,
            "assessment_id": "empty-evidence",
            "scope": {"durable": True, "multi_repository": True, "independent_roadmap": True},
            "boundary": {},
            "repositories": [],
            "candidate_boundaries": [],
        },
    )
    result = run_script("assess.py", "--standards-root", str(root), "--request", str(path))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["assessment_valid"] is False
    assert payload["creation_authorized"] is False


def test_init_requirement_checks_accept_complete_unique_proposal(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    errors, _ = init_requirements(proposal(), *governance(root))
    assert errors == []


def test_init_rejects_unapproved_decision(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    value["decision"]["approval"]["status"] = "pending"
    errors, _ = init_requirements(value, *governance(root))
    assert "INIT requires approved decision evidence" in errors


def test_init_rejects_duplicate_boundary(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    value["workspace"]["boundary"]["key"] = "existing-boundary"
    errors, _ = init_requirements(value, *governance(root))
    assert "workspace boundary key is already cataloged" in errors


def test_init_rejects_template_identity_outside_owner_fields(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    value["workspace"]["display_name"] = "Example Candidate"
    value["workspace"]["boundary"]["key"] = "example-candidate-boundary"
    value["decision"]["gitlab_creation"]["project_path"] = "example-candidate"
    value["migration"] = {
        "independent_owner_agent": {
            "write_scope": ["domain-workspaces/example-candidate"]
        }
    }
    errors, _ = init_requirements(value, *governance(root))
    assert any("standards template placeholders" in error for error in errors)


def test_init_preserves_placeholder_before_approval_url_error_order(
    tmp_path: Path,
) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    value["workspace"]["display_name"] = "Example Candidate"
    value["decision"]["approval"]["reference"] = "not-an-auditable-url"
    errors, _ = init_requirements(value, *governance(root))
    assert errors == [
        "INIT rejects standards template placeholders: workspace.display_name",
        "INIT approval reference must be an auditable HTTPS URL",
    ]


@pytest.mark.parametrize("missing", ["owner", "reuse"])
def test_init_rejects_missing_owner_or_reuse_proof(tmp_path: Path, missing: str) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    if missing == "owner":
        value["workspace"]["governance_owner"] = {"status": "nominated", "evidence": ""}
    else:
        value["repository_reuse"] = []
    errors, _ = init_requirements(value, *governance(root))
    expected = "confirmed governance Owner" if missing == "owner" else "reuse proof"
    assert any(expected in error for error in errors)


def test_init_equivalent_url_cannot_bypass_reuse_proof(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    value = proposal()
    value["repositories"][0]["repo"] = "https://gitlab.addx.ai/cloud/iot-service-unified.git"
    value["repository_reuse"] = []
    errors, details = init_requirements(value, *governance(root))
    assert "INIT reuse proof must cover exactly every overlapping repository" in errors
    assert "gitlab.addx.ai/cloud/iot-service-unified" in details["overlapping_repositories"]


def test_init_equivalent_catalog_url_cannot_bypass_repository_uniqueness(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    catalog, registry = governance(root)
    catalog["workspaces"][0]["repository"] = (
        "https://gitlab.addx.ai/domain-workspaces/existing-domain.git"
    )
    value = proposal()
    value["decision"]["gitlab_creation"] = {
        "namespace": "domain-workspaces",
        "project_path": "existing-domain",
    }
    errors, _ = init_requirements(value, catalog, registry)
    assert "workspace repository is already cataloged" in errors


def test_untrusted_standards_validator_is_never_executed(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path)
    marker = tmp_path / "executed"
    validator = root / "scripts/governance-validate"
    validator.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    validator.chmod(0o755)
    proposal_path = tmp_path / "proposal.yaml"
    write_yaml(proposal_path, proposal())
    result = run_script(
        "gate.py",
        "init",
        "--standards-root",
        str(root),
        "--proposal",
        str(proposal_path),
    )
    assert result.returncode == 1
    assert not marker.exists()
    assert json.loads(result.stdout)["details"]["official_validator_executed"] is False


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_real_standards_init_uses_official_schema_and_validator(tmp_path: Path) -> None:
    root = Path(REAL_STANDARDS)
    proposal_path = tmp_path / "approved-proposal.yaml"
    write_yaml(proposal_path, real_proposal(root))
    result = run_script(
        "gate.py",
        "init",
        "--standards-root",
        str(root),
        "--proposal",
        str(proposal_path),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["authorized"] is True
    assert payload["details"]["official_validator_source_sha"] == REAL_STANDARDS_SHA


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_real_standards_init_rejects_unchanged_template() -> None:
    root = Path(REAL_STANDARDS)
    result = run_script(
        "gate.py",
        "init",
        "--standards-root",
        str(root),
        "--proposal",
        str(root / "templates/workspace-proposal.yaml"),
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["authorized"] is False
    assert any("unchanged standards proposal template" in error for error in payload["errors"])


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_real_standards_propose_uses_snapshot_without_authorizing_creation(tmp_path: Path) -> None:
    root = Path(REAL_STANDARDS)
    proposal_path = tmp_path / "proposal.yaml"
    write_yaml(proposal_path, real_proposal(root))
    result = run_script(
        "gate.py",
        "propose",
        "--standards-root",
        str(root),
        "--proposal",
        str(proposal_path),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["proposal_valid"] is True
    assert payload["authorized"] is False
    assert payload["creation_authorized"] is False
    assert payload["details"]["official_validator_source_sha"] == REAL_STANDARDS_SHA


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
@pytest.mark.parametrize("failure", ["unapproved", "duplicate-boundary", "owner", "reuse"])
def test_real_standards_init_negative_gates(tmp_path: Path, failure: str) -> None:
    root = Path(REAL_STANDARDS)
    value = real_proposal(root)
    if failure == "unapproved":
        value["decision"]["approval"]["status"] = "pending"
    elif failure == "duplicate-boundary":
        catalog = read_yaml(root / "catalog/workspaces.yaml")
        value["workspace"]["boundary"]["key"] = catalog["workspaces"][0]["boundary"]["key"]
    elif failure == "owner":
        value["workspace"]["governance_owner"] = {"status": "pending"}
    else:
        value["repository_reuse"] = []
    proposal_path = tmp_path / f"{failure}.yaml"
    write_yaml(proposal_path, value)
    result = run_script(
        "gate.py",
        "init",
        "--standards-root",
        str(root),
        "--proposal",
        str(proposal_path),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["authorized"] is False


def validation_evidence() -> dict:
    checks = {
        name: {"status": "pass", "evidence": f"{name} readback"}
        for name in [
            "catalog",
            "proposal",
            "yaml",
            "html_links",
            "adr",
            "taxonomy_boundary",
            "unmanaged_siblings",
            "sha_snapshot_freeze",
            "git_state",
            "representative_workflow",
            "dated_review_plan",
        ]
    }
    checks["representative_workflow"].update(
        {"command": "./scripts/representative-workflow", "exit_code": 0, "source_sha": SHA}
    )
    checks["dated_review_plan"].update(
        {"owner": "owner", "reviewed_at": "2026-08-04", "next_review_at": "2099-01-01"}
    )
    return {
        "schema_version": 1,
        "workspace": "candidate-domain",
        "checks": checks,
        "git": {"clean": True, "head_sha": SHA, "remote_head_sha": SHA},
        "evidence_boundaries": {
            name: {"status": "verified", "evidence": f"https://evidence.example/{name}"}
            for name in ["source", "merge", "ci", "deploy", "runtime", "acceptance"]
        },
    }


def test_validate_evidence_requires_representative_workflow_and_review_plan() -> None:
    errors, _ = validate_evidence(validation_evidence(), require_activation=False)
    assert errors == []
    value = validation_evidence()
    value["checks"]["representative_workflow"].pop("command")
    value["checks"]["dated_review_plan"].pop("owner")
    errors, _ = validate_evidence(value, require_activation=True)
    assert "activation requires an executable representative workflow at a full source SHA" in errors
    assert "dated_review_plan.owner is required" in errors
    future = validation_evidence()
    future["checks"]["dated_review_plan"].update(
        {"reviewed_at": "2098-01-01", "next_review_at": "2099-01-01"}
    )
    errors, _ = validate_evidence(future, require_activation=True)
    assert "dated review plan reviewed_at cannot be in the future" in errors


def test_validate_activation_rejects_unbound_self_report() -> None:
    errors, _ = validate_evidence(validation_evidence(), require_activation=True)
    assert any("evidence_file must be an absolute regular file" in error for error in errors)
    value = validation_evidence()
    value["evidence_boundaries"]["runtime"] = {
        "status": "unverified",
        "evidence": "https://evidence.example/runtime",
    }
    errors, _ = validate_evidence(value, require_activation=True)
    assert "runtime requires status=verified" in errors


def test_validate_binds_workspace_identity_and_workflow_to_actual_head() -> None:
    value = validation_evidence()
    errors = validate_workspace_binding(
        value,
        {"workspace": "different-workspace", "lifecycle": "incubating"},
        {"head_sha": "f" * 40},
        require_activation=True,
    )
    assert "validation evidence workspace does not match actual workspace manifest" in errors
    assert "representative workflow source_sha does not match actual workspace HEAD" in errors


def test_archive_requires_deprecated_catalog_and_migrated_consumers(tmp_path: Path) -> None:
    root = make_catalog_fixture(tmp_path, lifecycle="deprecated")
    catalog, _ = governance(root)
    workspace = "existing-domain"
    value = {
        "schema_version": 1,
        "phase": "preflight",
        "workspace": workspace,
        "current_lifecycle": "deprecated",
        "target_lifecycle": "archived",
        "owner": raw_observation(
            tmp_path, "owner", "confirmed", f"workspace-owner-confirmed:{workspace}"
        ),
        "consumer_migrations": [
            {
                "consumer": "consumer-a",
                **raw_observation(
                    tmp_path,
                    "consumer",
                    "migrated",
                    f"consumer-migrated:{workspace}:consumer-a",
                ),
            }
        ],
        "redirect": {
            "target": "successor",
            **raw_observation(
                tmp_path,
                "redirect",
                "ready",
                f"redirect-ready:{workspace}:successor",
            ),
        },
        "source_pointer": {
            "target": "source docs",
            **raw_observation(
                tmp_path,
                "source-pointer",
                "ready",
                f"source-pointer-ready:{workspace}:source docs",
            ),
        },
        "catalog_update": raw_observation(
            tmp_path,
            "catalog-update",
            "reviewed",
            f"catalog-transition-reviewed:{workspace}:archived",
        ),
        "archive_commit": SHA,
        "repository_read_only": raw_observation(
            tmp_path,
            "read-only",
            "reviewed",
            f"repository-read-only-reviewed:{workspace}",
        ),
        "git": {"clean": True, "head_sha": SHA, "remote_head_sha": SHA},
    }
    errors, _ = archive_requirements(value, catalog)
    assert errors == []
    broken = copy.deepcopy(value)
    broken["consumer_migrations"] = []
    errors, _ = archive_requirements(broken, catalog)
    assert "consumer_migrations must be non-empty" in errors
    malformed = copy.deepcopy(value)
    malformed["consumer_migrations"] = ["not-a-map"]
    errors, _ = archive_requirements(malformed, catalog)
    assert "consumer_migrations[0] must be a map" in errors
    self_reported = copy.deepcopy(value)
    self_reported["owner"] = {"status": "confirmed", "evidence": "x"}
    self_reported["repository_read_only"]["status"] = "planned"
    errors, _ = archive_requirements(self_reported, catalog)
    assert "owner.evidence_file must be an absolute regular file" in errors
    assert "repository_read_only requires status=reviewed" in errors
    unrelated = copy.deepcopy(value)
    unrelated["owner"] = raw_observation(tmp_path, "unrelated", "confirmed", "localhost")
    errors, _ = archive_requirements(unrelated, catalog)
    assert (
        "owner.observed_value must equal 'workspace-owner-confirmed:existing-domain'"
        in errors
    )

    post_catalog = copy.deepcopy(catalog)
    post_catalog["workspaces"][0]["lifecycle"] = "archived"
    post = copy.deepcopy(value)
    post.update({"phase": "post-transition", "current_lifecycle": "archived"})
    post["catalog_update"] = raw_observation(
        tmp_path,
        "catalog-complete",
        "complete",
        f"catalog-transition-complete:{workspace}:archived",
    )
    post["repository_read_only"] = raw_observation(
        tmp_path,
        "read-only-complete",
        "complete",
        f"repository-read-only-complete:{workspace}",
    )
    post["transition_approval"] = raw_observation(
        tmp_path,
        "transition-approval",
        "approved",
        f"archive-transition-approved:{workspace}:{SHA}",
    )
    errors, _ = archive_requirements(post, post_catalog)
    assert errors == []


def test_archive_invalid_phase_remains_fail_closed_with_preflight_expectation(
    tmp_path: Path,
) -> None:
    root = make_catalog_fixture(tmp_path, lifecycle="deprecated")
    catalog, _ = governance(root)
    errors, details = archive_requirements(
        {
            "schema_version": 1,
            "phase": "unexpected",
            "workspace": "existing-domain",
            "current_lifecycle": "deprecated",
            "target_lifecycle": "archived",
            "git": {"clean": True, "head_sha": SHA, "remote_head_sha": SHA},
        },
        catalog,
    )
    assert errors[0] == "archive phase must be preflight or post-transition"
    assert details["expected_lifecycle"] == "deprecated"
    assert "transition_approval requires status=approved" not in errors


def test_archive_gate_runs_official_validator_and_binds_catalog_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    standards = tmp_path / "standards"
    snapshot = tmp_path / "snapshot"
    workspace_root = tmp_path / "workspace"
    (snapshot / "scripts").mkdir(parents=True)
    workspace_root.mkdir()
    write_yaml(
        workspace_root / "workspace.yaml",
        {"workspace": "existing-domain", "lifecycle": "archived"},
    )
    evidence_path = tmp_path / "archive.yaml"
    write_yaml(
        evidence_path,
        {
            "phase": "post-transition",
            "workspace": "existing-domain",
            "archive_commit": SHA,
            "git": {"clean": True, "head_sha": SHA, "remote_head_sha": SHA},
        },
    )
    catalog = {
        "workspaces": [
            {
                "name": "existing-domain",
                "lifecycle": "archived",
                "repository": "git@gitlab.addx.ai:domain-workspaces/existing-domain.git",
            }
        ]
    }

    @contextmanager
    def fake_snapshot(*_args):
        yield snapshot

    monkeypatch.setattr(
        archive_module,
        "verify_standards_checkout",
        lambda *_args: ({"local_head": SHA}, []),
    )
    monkeypatch.setattr(archive_module, "materialize_git_commit", fake_snapshot)
    monkeypatch.setattr(archive_module, "governance_data", lambda *_args: (catalog, {}))
    monkeypatch.setattr(
        archive_module,
        "archive_requirements",
        lambda *_args: (
            [],
            {
                "phase": "post-transition",
                "expected_lifecycle": "archived",
                "catalog_repository": catalog["workspaces"][0]["repository"],
                "catalog_archive": {
                    "archive_commit": "f" * 40,
                    "redirect": "successor",
                },
            },
        ),
    )
    def fake_workspace_git_checks(
        _workspace_root: Path, _declared: dict, expected_origin: str
    ) -> tuple[list[str], dict[str, str]]:
        assert expected_origin == catalog["workspaces"][0]["repository"]
        return (
            ["actual workspace origin does not match central catalog repository"],
            {
                "head_sha": SHA,
                "remote_head_sha": SHA,
                "origin": "git@gitlab.addx.ai:domain-workspaces/not-this-workspace.git",
            },
        )

    monkeypatch.setattr(
        archive_module, "workspace_git_checks", fake_workspace_git_checks
    )
    monkeypatch.setattr(
        archive_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "invalid workspace"),
    )
    errors, details = archive_module.archive_gate(standards, workspace_root, evidence_path)
    assert "official standards workspace validator failed" in errors
    assert "official standards governance validator failed" in errors
    assert "actual workspace origin does not match central catalog repository" in errors
    assert "catalog archive.archive_commit does not match actual workspace HEAD" in errors
    assert details["official_validator_source_sha"] == SHA


def test_archive_cli_never_authorizes_even_when_candidate_is_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        gate_module,
        "archive_gate",
        lambda *_args: ([], {"checked": True, "phase": "preflight"}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gate.py",
            "archive",
            "--standards-root",
            str(tmp_path),
            "--workspace-root",
            str(tmp_path),
            "--evidence",
            str(tmp_path / "archive.yaml"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        gate_module.main()
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_candidate_ready"] is True
    assert payload["archive_transition_verified"] is False
    assert payload["archive_authorized"] is False
    assert payload["authorized"] is False


@pytest.mark.skipif(not REAL_STANDARDS, reason="set DOMAIN_WORKSPACE_STANDARDS_ROOT")
def test_archive_cli_exception_is_fail_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "archive.yaml"
    write_yaml(evidence, {"schema_version": 1, "phase": "preflight"})
    result = run_script(
        "gate.py",
        "archive",
        "--standards-root",
        REAL_STANDARDS,
        "--workspace-root",
        str(tmp_path / "missing-workspace"),
        "--evidence",
        str(evidence),
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["mode"] == "ARCHIVE"
    assert payload["archive_authorized"] is False
    assert payload["archive_candidate_ready"] is False
    assert payload["archive_transition_verified"] is False


def test_one_time_and_personal_material_do_not_create_workspace(tmp_path: Path) -> None:
    result = run_script(
        "ingest_filter.py",
        "--inventory",
        str(FIXTURES / "one-time-project-ingest.yaml"),
        "--source-root",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["workspace_eligible"] is False
    assert payload["included"] == []
    assert payload["bulk_copy_authorized"] is False
    assert {row["category"] for row in payload["excluded"]} >= {
        "temporary-project",
        "personal-material",
        "issue-mr-worktree",
        "sql",
        "log",
    }


def test_ingest_rejects_secret_path_even_when_category_is_allowed(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.yaml"
    write_yaml(
        inventory,
        {
            "schema_version": 1,
            "workspace_candidate": "candidate",
            "owner_agent": {"conversation_id": "owner-agent", "independent": True},
            "items": [
                {"path": ".env.production", "category": "durable-domain-context"},
                {"path": "secrets/prod.yaml", "category": "durable-domain-context"},
            ],
        },
    )
    result = run_script(
        "ingest_filter.py",
        "--inventory",
        str(inventory),
        "--source-root",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["included"] == []
    assert payload["workspace_eligible"] is False
    assert len(payload["excluded"]) == 2
    assert {item["reason"] for item in payload["excluded"]} == {
        "sensitive-or-transient-path"
    }


@pytest.mark.parametrize(
    "candidate",
    [
        "docs/privatekeys.txt",
        "docs/api-key.txt",
        "docs/credential.notes",
        "issue-42/context.html",
        ".worktrees/topic/context.html",
        "logs/output.txt",
    ],
)
def test_ingest_path_filter_preserves_sensitive_and_transient_matching(candidate: str) -> None:
    assert path_disposition(candidate) == "sensitive-or-transient-path"


@pytest.mark.parametrize(
    "candidate",
    [
        "docs/tokenization-guide.html",
        "docs/issues-overview.html",
        "catalog/worktree-policy.html",
        "docs/private.key.note",
        "docs/private--key.note",
        "docs/api._key.note",
    ],
)
def test_ingest_path_filter_does_not_match_partial_safe_words(candidate: str) -> None:
    assert path_disposition(candidate) is None


def test_ingest_path_filter_preserves_unicode_decimal_issue_matching() -> None:
    assert path_disposition("mr_٤٢/context.html") == "sensitive-or-transient-path"


def test_ingest_rejects_secret_content_hidden_in_allowed_path(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    (source / "docs").mkdir(parents=True)
    (source / "docs/context.html").write_text(
        "<p>durable context</p>\n-----BEGIN RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.html"
    outside.write_text("<p>safe-looking external content</p>\n", encoding="utf-8")
    (source / "docs/link.html").symlink_to(outside)
    inventory = tmp_path / "inventory.yaml"
    write_yaml(
        inventory,
        {
            "schema_version": 1,
            "workspace_candidate": "candidate",
            "owner_agent": {"conversation_id": "owner-agent", "independent": True},
            "items": [
                {"path": "docs/context.html", "category": "durable-domain-context"},
                {"path": "docs/link.html", "category": "durable-domain-context"},
            ],
        },
    )
    result = run_script(
        "ingest_filter.py",
        "--inventory",
        str(inventory),
        "--source-root",
        str(source),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["workspace_eligible"] is False
    assert {item["reason"] for item in payload["excluded"]} == {
        "secret-like-content",
        "symlink-content-not-ingestable",
    }
