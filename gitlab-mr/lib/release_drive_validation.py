"""共享的 Driver state 输入校验。"""

from __future__ import annotations

import re
import subprocess
from typing import Any
from urllib.parse import urlsplit

from release_workflow_policy import PolicyError


def cleanup_job_binding_matches(cleanup: Any) -> bool:
    if not isinstance(cleanup, dict):
        return False
    accepted = cleanup.get("accepted_jobs")
    writers = cleanup.get("removed_writer_jobs")
    gate = cleanup.get("staging_contract_gate_job")
    lists = (accepted, writers)
    return (
        all(
            isinstance(items, list)
            and items
            and all(isinstance(item, str) and item for item in items)
            and len(set(items)) == len(items)
            for items in lists
        )
        and isinstance(gate, str)
        and bool(gate)
        and gate not in writers
        and set(accepted) == {*writers, gate}
    )


def require_text(value: str, label: str) -> None:
    if not value.strip():
        raise PolicyError(f"{label} is required for this MR mode")


def require_sha(value: str, label: str) -> None:
    require_text(value, label)
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise PolicyError(f"{label} must be a full lowercase commit SHA")


def require_https_url(value: str, label: str) -> None:
    require_text(value, label)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise PolicyError(f"{label} must be an auditable HTTPS URL")


def require_current_git_head(expected_sha: str) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PolicyError(f"cannot read current Git HEAD: {result.stderr.strip()}")
    actual = result.stdout.strip()
    if actual != expected_sha:
        raise PolicyError(f"head_sha {expected_sha} does not match Git HEAD {actual}")


def require_no_remote_staging_branch(branch: str) -> None:
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PolicyError(
            f"cannot verify remote staging branch absence: {result.stderr.strip()}"
        )
    if result.stdout.strip():
        raise PolicyError(
            f"origin/{branch} exists; production-non-promotion is not allowed"
        )
