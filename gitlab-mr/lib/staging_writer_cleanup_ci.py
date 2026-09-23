"""验证 staging writer cleanup 的 GitLab CI 结构转换。"""

from __future__ import annotations

from typing import Any

from release_parity_core import InputError
from staging_writer_cleanup_contract import load_yaml_blob, string_list


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    return []


def validate_ci_transition(
    contract: dict[str, Any],
    target_ref: str,
    candidate_ref: str,
    target_branch: str,
    verification_paths: list[str],
) -> tuple[list[str], list[str]]:
    before, _ = load_yaml_blob(target_ref, ".gitlab-ci.yml", "target CI config")
    after, _ = load_yaml_blob(candidate_ref, ".gitlab-ci.yml", "candidate CI config")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise InputError("GitLab CI config must be a YAML mapping")
    writers = string_list(contract.get("writer_jobs"), "writer_jobs")
    gates = string_list(contract.get("contract_gate_jobs"), "contract_gate_jobs")
    if set(writers) & set(gates):
        raise InputError("writer_jobs and contract_gate_jobs must be disjoint")
    removed = set(before) - set(after)
    added = set(after) - set(before)
    if removed != set(writers):
        raise InputError(
            f"removed CI keys do not match writer_jobs: {sorted(removed)!r}"
        )
    if added != set(gates):
        raise InputError(
            f"added CI keys do not match contract_gate_jobs: {sorted(added)!r}"
        )
    changed_common = sorted(
        key for key in set(before) & set(after) if before[key] != after[key]
    )
    if changed_common:
        raise InputError(
            "cleanup changed retained CI objects: " + ", ".join(changed_common)
        )
    for name in writers:
        job = before[name]
        text = "\n".join(_strings(job))
        variables = job.get("variables") if isinstance(job, dict) else None
        image_path = variables.get("IMAGE_PATH") if isinstance(variables, dict) else None
        if (
            not isinstance(job, dict)
            or not name.startswith(("build:staging-", "manifest:staging-"))
            or not isinstance(image_path, str)
            or "/staging-" not in image_path
            or target_branch not in text
        ):
            raise InputError(
                f"declared writer job is not a {target_branch}-side staging writer: {name}"
            )
    for name in gates:
        job = after[name]
        if not isinstance(job, dict) or job.get("stage") != "test":
            raise InputError(f"cleanup contract gate must be a test-stage job: {name}")
        text = "\n".join(_strings(job))
        for marker in (
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
            "CI_COMMIT_BRANCH",
            target_branch,
            *verification_paths,
        ):
            if marker not in text:
                raise InputError(f"cleanup contract gate {name} is missing {marker!r}")
    return writers, gates
