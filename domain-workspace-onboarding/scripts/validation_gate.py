#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Evidence and live-checkout validation for the VALIDATE lifecycle mode."""

# Library module; gate.py provides argparse --help and json.dumps structured output.

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from common import (
    canonical_git_url,
    governance_data,
    isolated_git_env,
    load_yaml,
    materialize_git_commit,
    nonempty,
    record_ready,
    safe_git_command,
    trusted_validator_env,
    validate_raw_observation,
    verify_standards_checkout,
    workspace_remote_git_env,
    workspace_status_git_env,
)

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH_REF_PREFIX = "refs/heads/"
MAX_GIT_METADATA_BYTES = 100_000_000
BOOLEAN_STATUS_CONFIG = {
    "core.filemode",
    "core.ignorecase",
    "core.precomposeunicode",
    "core.symlinks",
}
ENUM_STATUS_CONFIG = {
    "core.autocrlf": {"false", "true", "input"},
    "core.eol": {"lf", "crlf", "native"},
}
CHECKS = {
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
}
LAYERS = {"source", "merge", "ci", "deploy", "runtime", "acceptance"}


def _parse_day(value: Any, label: str, errors: list[str]) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{label} must be an ISO-8601 date")
        return None


def _validate_check_records(checks: dict[str, Any], errors: list[str]) -> None:
    if set(checks) != CHECKS:
        errors.append(f"checks must contain exactly: {', '.join(sorted(CHECKS))}")
    for name in CHECKS:
        if not record_ready(checks.get(name), {"pass"}):
            errors.append(f"validation check must pass with evidence: {name}")


def _validate_representative_workflow(
    workflow: dict[str, Any], require_activation: bool, errors: list[str]
) -> None:
    if not require_activation:
        return
    if (
        not nonempty(workflow.get("command"))
        or workflow.get("exit_code") != 0
        or not FULL_SHA.fullmatch(str(workflow.get("source_sha") or ""))
    ):
        errors.append("activation requires an executable representative workflow at a full source SHA")
    bound_workflow = {**workflow, "status": "verified"}
    validate_raw_observation(
        "representative_workflow", bound_workflow, errors, "verified"
    )
    expected_observation = f"workflow-pass:{workflow.get('source_sha', '')}"
    if workflow.get("observed_value") != expected_observation:
        errors.append(
            "representative_workflow.observed_value must bind workflow-pass to source_sha"
        )
    evidence_file = Path(str(workflow.get("evidence_file") or ""))
    if evidence_file.is_absolute() and evidence_file.is_file() and not evidence_file.is_symlink():
        raw = evidence_file.read_text(encoding="utf-8", errors="replace")
        if str(workflow.get("command") or "") not in raw:
            errors.append("representative workflow raw evidence is missing the command")


def _validate_review_plan(
    review: dict[str, Any], require_activation: bool, errors: list[str]
) -> None:
    if not require_activation:
        return
    reviewed_at = _parse_day(review.get("reviewed_at"), "dated_review_plan.reviewed_at", errors)
    next_review_at = _parse_day(review.get("next_review_at"), "dated_review_plan.next_review_at", errors)
    if not nonempty(review.get("owner")):
        errors.append("dated_review_plan.owner is required")
    if reviewed_at and reviewed_at > date.today():
        errors.append("dated review plan reviewed_at cannot be in the future")
    if reviewed_at and next_review_at and (
        next_review_at <= reviewed_at or next_review_at <= date.today()
    ):
        errors.append("dated review plan must have a future next_review_at after reviewed_at")
    bound_review = {**review, "status": "verified"}
    validate_raw_observation("dated_review_plan", bound_review, errors, "verified")
    expected_observation = f"review-plan:{review.get('next_review_at', '')}"
    if review.get("observed_value") != expected_observation:
        errors.append("dated_review_plan.observed_value must bind next_review_at")
    evidence_file = Path(str(review.get("evidence_file") or ""))
    if evidence_file.is_absolute() and evidence_file.is_file() and not evidence_file.is_symlink():
        raw = evidence_file.read_text(encoding="utf-8", errors="replace")
        for required in (str(review.get("owner") or ""), str(review.get("next_review_at") or "")):
            if required and required not in raw:
                errors.append("dated review plan raw evidence is missing owner or next review date")


def _validate_declared_git(git: dict[str, Any], errors: list[str]) -> None:
    if git.get("clean") is not True:
        errors.append("Git working tree must be clean")
    if not FULL_SHA.fullmatch(str(git.get("head_sha") or "")):
        errors.append("git.head_sha must be a full SHA")
    if git.get("head_sha") != git.get("remote_head_sha"):
        errors.append("local and remote Git heads must match")


def _validate_evidence_layers(
    layers: dict[str, Any], require_activation: bool, errors: list[str]
) -> None:
    if set(layers) != LAYERS:
        errors.append(f"evidence_boundaries must contain exactly: {', '.join(sorted(LAYERS))}")
    permitted = {"verified", "unverified", "blocked", "not-applicable"}
    for name in LAYERS:
        record = layers.get(name) or {}
        if record.get("status") not in permitted or not nonempty(record.get("evidence")):
            errors.append(f"evidence layer must have explicit status and evidence: {name}")
        if require_activation:
            validate_raw_observation(name, record, errors, "verified")


def validate_evidence(
    evidence: dict[str, Any], require_activation: bool
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if evidence.get("schema_version") != 1:
        errors.append("validation evidence schema_version must be 1")
    checks = evidence.get("checks") or {}
    _validate_check_records(checks, errors)
    _validate_representative_workflow(
        checks.get("representative_workflow") or {}, require_activation, errors
    )
    _validate_review_plan(checks.get("dated_review_plan") or {}, require_activation, errors)
    _validate_declared_git(evidence.get("git") or {}, errors)
    layers = evidence.get("evidence_boundaries") or {}
    _validate_evidence_layers(layers, require_activation, errors)
    return errors, {"activation_requested": require_activation, "evidence_boundaries": layers}


def _regular_git_file(path: Path, label: str, maximum: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"workspace {label} must be a regular file")
    if path.stat().st_size > maximum:
        raise ValueError(f"workspace {label} exceeds {maximum} bytes")
    return path


def _valid_branch_ref(ref: str) -> bool:
    if not ref.startswith(BRANCH_REF_PREFIX):
        return False
    branch = ref.removeprefix(BRANCH_REF_PREFIX)
    parts = branch.split("/")
    invalid_characters = set(" ~^:?*[\\")
    return bool(
        branch
        and all(part not in {"", ".", ".."} for part in parts)
        and not any(part.startswith(".") or part.endswith(".lock") for part in parts)
        and not branch.endswith((".", "/"))
        and "@{" not in branch
        and not any(character in invalid_characters or ord(character) < 32 for character in branch)
    )


def _packed_ref(git_dir: Path, ref: str) -> str:
    packed_refs = git_dir / "packed-refs"
    if not packed_refs.exists():
        return ""
    raw = _regular_git_file(
        packed_refs, "packed-refs", MAX_GIT_METADATA_BYTES
    ).read_text(encoding="utf-8", errors="strict")
    for line in raw.splitlines():
        if line.startswith(("#", "^")):
            continue
        fields = line.split(" ", 1)
        if len(fields) == 2 and fields[1] == ref and FULL_SHA.fullmatch(fields[0]):
            return fields[0]
    return ""


def _workspace_head(git_dir: Path) -> tuple[str, str]:
    head_value = _regular_git_file(git_dir / "HEAD", "HEAD", 4_096).read_text(
        encoding="ascii", errors="strict"
    ).strip()
    if not head_value.startswith("ref: "):
        raise ValueError("workspace must be on a named Git branch")
    ref = head_value.removeprefix("ref: ")
    if not _valid_branch_ref(ref):
        raise ValueError("workspace HEAD contains an invalid branch ref")
    branch = ref.removeprefix(BRANCH_REF_PREFIX)
    loose_ref = git_dir.joinpath(*ref.split("/"))
    if loose_ref.exists():
        head = _regular_git_file(loose_ref, "branch ref", 4_096).read_text(
            encoding="ascii", errors="strict"
        ).strip()
    else:
        head = _packed_ref(git_dir, ref)
    if not FULL_SHA.fullmatch(head):
        raise ValueError("workspace branch does not resolve to a full SHA")
    return head, branch


def _git_config_values(
    config: Path, key: str, value_type: str | None = None
) -> list[str]:
    arguments = [
        "config",
        "--file",
        str(config),
        "--no-includes",
        "--null",
    ]
    if value_type:
        arguments.extend(["--type", value_type])
    arguments.extend(["--get-all", key])
    with tempfile.TemporaryDirectory(prefix="domain-workspace-config-") as clean_cwd:
        result = subprocess.run(
            safe_git_command(None, *arguments),
            check=False,
            capture_output=True,
            env=isolated_git_env(),
            cwd=clean_cwd,
            timeout=10,
        )
    if result.returncode == 1:
        return []
    if result.returncode:
        raise ValueError(f"cannot read workspace Git config key: {key}")
    try:
        raw_values = result.stdout.split(b"\0")
        if raw_values and raw_values[-1] == b"":
            raw_values.pop()
        return [
            value.decode("utf-8", errors="strict")
            for value in raw_values
        ]
    except UnicodeDecodeError as exc:
        raise ValueError(f"workspace Git config key is not UTF-8: {key}") from exc


def _workspace_origin(git_dir: Path) -> str:
    config = _regular_git_file(git_dir / "config", "Git config", 5_000_000)
    values = _git_config_values(config, "remote.origin.url")
    if len(values) > 1:
        raise ValueError("workspace origin must contain exactly one URL")
    return values[0].strip() if values else ""


def _workspace_status_config(git_dir: Path) -> dict[str, str]:
    config = _regular_git_file(git_dir / "config", "Git config", 5_000_000)
    normalized: dict[str, str] = {}
    for key in sorted(BOOLEAN_STATUS_CONFIG | set(ENUM_STATUS_CONFIG)):
        values = _git_config_values(config, key)
        if len(values) > 1:
            raise ValueError(f"workspace Git config repeats status key: {key}")
        if not values:
            continue
        if key in BOOLEAN_STATUS_CONFIG:
            typed_values = _git_config_values(config, key, "bool")
            if len(typed_values) != 1:
                raise ValueError(f"workspace Git config repeats status key: {key}")
            normalized[key] = typed_values[0]
            continue
        value = values[0].strip().lower()
        if key == "core.autocrlf" and value != "input":
            typed_values = _git_config_values(config, key, "bool")
            if len(typed_values) != 1:
                raise ValueError(f"workspace Git config repeats status key: {key}")
            normalized[key] = typed_values[0]
            continue
        if value not in ENUM_STATUS_CONFIG[key]:
            raise ValueError(f"workspace Git config has invalid value: {key}")
        normalized[key] = value
    return normalized


def _copy_shared_indexes(git_dir: Path, sanitized_git: Path) -> None:
    total_bytes = 0
    for entry in git_dir.iterdir():
        if not re.fullmatch(r"sharedindex\.[0-9a-f]{40}", entry.name):
            continue
        shared_index = _regular_git_file(
            entry, "shared Git index", MAX_GIT_METADATA_BYTES
        )
        total_bytes += shared_index.stat().st_size
        if total_bytes > MAX_GIT_METADATA_BYTES:
            raise ValueError("workspace shared Git indexes exceed the size limit")
        shutil.copyfile(shared_index, sanitized_git / entry.name)


def _sanitized_workspace_status(
    workspace_root: Path,
    git_dir: Path,
    head: str,
    status_config: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    index = _regular_git_file(git_dir / "index", "Git index", MAX_GIT_METADATA_BYTES)
    objects = git_dir / "objects"
    if objects.is_symlink() or not objects.is_dir():
        raise ValueError("workspace Git objects must be a directory")
    with tempfile.TemporaryDirectory(prefix="domain-workspace-status-") as temporary:
        sanitized_git = Path(temporary) / "git"
        (sanitized_git / "objects").mkdir(parents=True)
        (sanitized_git / "refs").mkdir()
        (sanitized_git / "HEAD").write_text(f"{head}\n", encoding="ascii")
        config_lines = [
            "[core]",
            "\trepositoryformatversion = 0",
            "\tbare = false",
            *(
                f"\t{key.removeprefix('core.')} = {value}"
                for key, value in sorted(status_config.items())
            ),
        ]
        (sanitized_git / "config").write_text(
            "\n".join(config_lines) + "\n", encoding="ascii"
        )
        candidate_index = Path(temporary) / "candidate-index"
        shutil.copyfile(index, candidate_index)
        _copy_shared_indexes(git_dir, sanitized_git)
        git_environment = workspace_status_git_env(objects)
        candidate_environment = {
            **git_environment,
            "GIT_INDEX_FILE": str(candidate_index),
        }
        shared_index = subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                "rev-parse",
                "--shared-index-path",
            ),
            check=False,
            capture_output=True,
            text=True,
            env=candidate_environment,
            cwd=temporary,
            timeout=30,
        )
        if shared_index.returncode:
            raise ValueError("cannot inspect workspace split-index state")
        if shared_index.stdout.strip():
            raise ValueError("workspace split index is not supported")
        index_entries = subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                "ls-files",
                "--stage",
                "-z",
            ),
            check=False,
            capture_output=True,
            env=candidate_environment,
            cwd=temporary,
            timeout=30,
        )
        if index_entries.returncode:
            raise ValueError("cannot inspect sanitized workspace Git index")
        for record in index_entries.stdout.split(b"\0"):
            if not record:
                continue
            metadata = record.split(b"\t", 1)[0].split()
            if len(metadata) != 3:
                raise ValueError("workspace Git index contains an invalid entry")
            mode, _object_id, stage = metadata
            if mode == b"160000":
                raise ValueError("workspace Git index must not contain submodules")
            if stage != b"0":
                raise ValueError("workspace Git index must not contain unmerged entries")
        index_flags = subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                "ls-files",
                "-v",
                "-z",
            ),
            check=False,
            capture_output=True,
            env=candidate_environment,
            cwd=temporary,
            timeout=30,
        )
        if index_flags.returncode:
            raise ValueError("cannot inspect workspace Git index flags")
        if any(
            record and not record.startswith(b"H ")
            for record in index_flags.stdout.split(b"\0")
        ):
            raise ValueError(
                "workspace Git index must not contain special path flags"
            )
        cached_diff = subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                "diff-index",
                "--cached",
                "--quiet",
                head,
                "--",
            ),
            check=False,
            capture_output=True,
            env=candidate_environment,
            cwd=temporary,
            timeout=30,
        )
        if cached_diff.returncode:
            raise ValueError("workspace Git index must match HEAD")
        fresh_index = subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                "read-tree",
                head,
            ),
            check=False,
            capture_output=True,
            env=git_environment,
            cwd=temporary,
            timeout=30,
        )
        if fresh_index.returncode:
            raise ValueError("cannot create sanitized workspace Git index")
        return subprocess.run(
            safe_git_command(
                None,
                f"--git-dir={sanitized_git}",
                f"--work-tree={workspace_root}",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=all",
            ),
            check=False,
            capture_output=True,
            text=True,
            env=git_environment,
            cwd=temporary,
            timeout=30,
        )


def _workspace_metadata(
    workspace_root: Path,
) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
    if workspace_root.is_symlink() or not workspace_root.is_dir():
        raise ValueError("workspace root must be a regular directory")
    git_dir = workspace_root / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ValueError("workspace must be a primary Git checkout")
    head, branch = _workspace_head(git_dir)
    origin = _workspace_origin(git_dir)
    status_config = _workspace_status_config(git_dir)
    return (
        _sanitized_workspace_status(
            workspace_root, git_dir, head, status_config
        ),
        head,
        branch,
        origin,
    )


def _run_workspace_remote(origin: str, branch: str) -> subprocess.CompletedProcess[str]:
    credential_helper = os.environ.get("DOMAIN_WORKSPACE_GIT_CREDENTIAL_HELPER")
    with tempfile.TemporaryDirectory(prefix="domain-workspace-git-") as clean_cwd:
        return subprocess.run(
            safe_git_command(
                None,
                "ls-remote",
                "--heads",
                origin,
                f"refs/heads/{branch}",
                credential_helper=credential_helper,
            ),
            check=False,
            capture_output=True,
            text=True,
            env=workspace_remote_git_env(),
            cwd=clean_cwd,
            timeout=30,
        )


def workspace_git_checks(
    workspace_root: Path,
    declared: dict[str, Any],
    expected_origin: str | None,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    status, head, branch, origin = _workspace_metadata(workspace_root)
    if status.returncode or status.stdout.strip():
        errors.append("actual workspace Git working tree must be clean")
    origin_validated = False
    if not origin:
        errors.append("actual workspace origin is missing")
    elif not expected_origin:
        errors.append("central catalog workspace origin is missing")
    else:
        try:
            origin_validated = canonical_git_url(origin) == canonical_git_url(expected_origin)
            if not origin_validated:
                errors.append("actual workspace origin does not match central catalog repository")
        except ValueError:
            errors.append("actual workspace origin must be a supported SSH or HTTPS Git URL")
    remote_head = ""
    if origin_validated:
        remote = _run_workspace_remote(origin, branch)
        rows = remote.stdout.strip().split()
        if remote.returncode or not rows:
            errors.append("cannot read actual workspace remote branch")
        else:
            remote_head = rows[0]
    if declared.get("head_sha") != head or declared.get("remote_head_sha") != remote_head:
        errors.append("declared Git evidence does not match actual local/remote heads")
    return errors, {
        "head_sha": head,
        "remote_head_sha": remote_head,
        "branch": branch,
        "origin": origin,
    }


def validate_workspace_binding(
    evidence: dict[str, Any],
    workspace_manifest: dict[str, Any],
    actual_git: dict[str, Any],
    require_activation: bool,
) -> list[str]:
    errors = []
    if evidence.get("workspace") != workspace_manifest.get("workspace"):
        errors.append("validation evidence workspace does not match actual workspace manifest")
    if require_activation:
        workflow = (evidence.get("checks") or {}).get("representative_workflow") or {}
        if workflow.get("source_sha") != actual_git.get("head_sha"):
            errors.append("representative workflow source_sha does not match actual workspace HEAD")
    return errors


def validate_gate(
    standards_root: Path,
    workspace_root: Path,
    evidence_path: Path,
    require_activation: bool,
) -> tuple[list[str], dict[str, Any]]:
    evidence = load_yaml(evidence_path)
    errors, details = validate_evidence(evidence, require_activation)
    catalog_repository: str | None = None
    verification, verification_errors = verify_standards_checkout(
        standards_root, Path(__file__).with_name("verify_standards.py")
    )
    errors.extend(verification_errors)
    details["standards_verification"] = verification
    if not verification_errors:
        verified_sha = str(verification["local_head"])
        with materialize_git_commit(standards_root, verified_sha) as snapshot:
            catalog, _ = governance_data(snapshot)
            catalog_rows = catalog.get("workspaces") or []
            catalog_row = next(
                (
                    row
                    for row in catalog_rows
                    if isinstance(row, dict)
                    and row.get("name") == evidence.get("workspace")
                ),
                None,
            )
            if catalog_row:
                catalog_repository = str(catalog_row.get("repository") or "")
            else:
                errors.append("validation workspace is not present in the central catalog")
            result = subprocess.run(
                [str(snapshot / "scripts/workspace-validate"), str(workspace_root)],
                check=False,
                capture_output=True,
                text=True,
                env=trusted_validator_env(),
            )
        details.update(
            {
                "official_validator_source_sha": verified_sha,
                "official_workspace_validator_exit": result.returncode,
                "official_workspace_validator_stdout": result.stdout.strip(),
                "official_workspace_validator_stderr": result.stderr.strip(),
            }
        )
        if result.returncode:
            errors.append("official standards workspace validator failed")
    git_errors, git_details = workspace_git_checks(
        workspace_root,
        evidence.get("git") or {},
        catalog_repository,
    )
    errors.extend(git_errors)
    details["actual_git"] = git_details
    workspace_manifest = load_yaml(workspace_root / "workspace.yaml")
    errors.extend(
        validate_workspace_binding(evidence, workspace_manifest, git_details, require_activation)
    )
    if require_activation:
        lifecycle = workspace_manifest.get("lifecycle")
        if lifecycle != "incubating":
            errors.append("activation decision requires current lifecycle=incubating")
    return errors, details
