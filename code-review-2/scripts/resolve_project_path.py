"""Resolve the reviewed repository to a canonical GitLab project path."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

PROJECT_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
SCP_REMOTE_RE = re.compile(
    r"^(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)$"
)
SUPPORTED_REMOTE_SCHEMES = {"git", "http", "https", "ssh"}


def canonicalize_project_path(
    raw_value: str | None,
    *,
    gitlab_host: str | None = None,
) -> str | None:
    """Normalize a project path or Git remote URL to path_with_namespace."""
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    value = raw_value.strip()
    trusted_host = gitlab_host.strip().lower() if gitlab_host else None
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme.lower() not in SUPPORTED_REMOTE_SCHEMES:
            return None
        if not trusted_host or not parsed.hostname:
            return None
        if parsed.hostname.lower() != trusted_host:
            return None
        value = parsed.path
    else:
        scp_match = SCP_REMOTE_RE.fullmatch(value)
        if scp_match:
            if not trusted_host or scp_match.group("host").lower() != trusted_host:
                return None
            value = scp_match.group("path")

    value = value.strip("/")
    if value.lower().endswith(".git"):
        value = value[:-4]
    canonical = value.lower()
    return canonical if PROJECT_PATH_RE.fullmatch(canonical) else None


def resolve_c1_policy(
    canonical_project: str | None,
    *,
    blocking_namespaces: list[str],
    blocking_projects: list[str],
) -> dict:
    """Map a canonical project path to the configured C1 gate level."""
    if canonical_project is None or not PROJECT_PATH_RE.fullmatch(canonical_project):
        return {
            "policy": "warning",
            "reason": "project identity unavailable",
        }

    if canonical_project in blocking_projects:
        return {
            "policy": "block",
            "reason": f"exact project: {canonical_project}",
        }

    for namespace in blocking_namespaces:
        if canonical_project.startswith(f"{namespace}/"):
            return {
                "policy": "block",
                "reason": f"namespace: {namespace}",
            }

    return {
        "policy": "warning",
        "reason": "project is outside the configured blocking scope",
    }


def _meta_candidate(review_data_dir: str | None) -> tuple[str | None, str | None]:
    if not review_data_dir:
        return None, None

    meta_path = Path(review_data_dir) / "meta.json"
    if not meta_path.exists():
        return None, f"{meta_path} does not exist"

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read {meta_path}: {exc}"

    project = data.get("project") if isinstance(data, dict) else None
    candidates = [
        project.get("path_with_namespace") if isinstance(project, dict) else None,
        project.get("project_path") if isinstance(project, dict) else None,
        data.get("project_path") if isinstance(data, dict) else None,
    ]
    candidate = next((value for value in candidates if value), None)
    if candidate is None:
        return None, f"{meta_path} does not contain a reviewed project path"
    return candidate, None


def _origin_remote() -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def resolve_project_path(
    *,
    explicit_project_path: str | None,
    review_data_dir: str | None,
    ci_project_path: str | None,
    remote_url: str | None,
    gitlab_host: str | None = None,
) -> dict:
    """Resolve using reviewed-project metadata before executor CI metadata."""
    candidates: list[tuple[str, str | None]] = []
    if explicit_project_path:
        candidates.append(("explicit", explicit_project_path))
    else:
        meta_value, meta_error = _meta_candidate(review_data_dir)
        if meta_error:
            return {
                "canonical_project": None,
                "source": "review_data",
                "diagnostic": meta_error,
            }
        if meta_value:
            candidates.append(("review_data", meta_value))
        elif ci_project_path:
            candidates.append(("ci_project_path", ci_project_path))
        else:
            candidates.append(("origin", remote_url or _origin_remote()))

    source, raw_value = candidates[0]
    canonical = canonicalize_project_path(raw_value, gitlab_host=gitlab_host)
    diagnostic = (
        None if canonical else f"invalid or unavailable project path from {source}"
    )
    return {
        "canonical_project": canonical,
        "source": source,
        "diagnostic": diagnostic,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the reviewed GitLab project to path_with_namespace."
    )
    parser.add_argument("--project-path", help="Prompt-injected reviewed project path")
    parser.add_argument("--review-data-dir", help="Directory containing meta.json")
    parser.add_argument("--ci-project-path", help="Executor CI_PROJECT_PATH fallback")
    parser.add_argument("--remote-url", help="Git origin URL fallback")
    parser.add_argument(
        "--gitlab-host",
        help="Trusted GitLab host for URL inputs (defaults to CI_SERVER_HOST)",
    )
    args = parser.parse_args()

    try:
        result = resolve_project_path(
            explicit_project_path=args.project_path,
            review_data_dir=args.review_data_dir or os.getenv("REVIEW_DATA_DIR"),
            ci_project_path=args.ci_project_path or os.getenv("CI_PROJECT_PATH"),
            remote_url=args.remote_url,
            gitlab_host=args.gitlab_host or os.getenv("CI_SERVER_HOST"),
        )
    except (OSError, TypeError, ValueError) as exc:
        result = {
            "canonical_project": None,
            "source": "resolver",
            "diagnostic": str(exc),
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
