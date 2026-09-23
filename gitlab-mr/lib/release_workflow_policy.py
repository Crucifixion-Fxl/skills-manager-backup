"""生产 MR 模式和 review 决议的确定性策略。"""

from __future__ import annotations


class PolicyError(ValueError):
    """流程证据不足，不能继续。"""


def is_production_target(target_branch: str) -> bool:
    return target_branch in {"main", "master"} or target_branch.startswith("release/")


def classify_mr_mode(
    target_branch: str,
    *,
    staging_flow_exists: bool | None = None,
    verified_sha_available: bool = False,
    staging_writer_cleanup_attested: bool = False,
    emergency_approved: bool = False,
) -> str:
    if not is_production_target(target_branch):
        return "ordinary"
    if staging_flow_exists is None:
        raise PolicyError("production target requires staging workflow evidence")
    if not staging_flow_exists:
        return "production-non-promotion"
    if verified_sha_available:
        return "production-promotion"
    if staging_writer_cleanup_attested:
        return "staging-writer-cleanup"
    if emergency_approved:
        return "emergency-hotfix"
    raise PolicyError(
        "repository has a staging flow; provide a verified SHA or explicit hotfix approval"
    )


def may_resolve_rejected_finding(author_type: str, disposition: str) -> bool:
    return author_type == "bot" and disposition in {
        "false-positive",
        "already-fixed",
    }
