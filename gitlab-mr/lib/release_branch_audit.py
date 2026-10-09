"""Validate live staging acceptance bound to a branch promotion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from release_parity_branch import validate_acceptance_note
from release_parity_core import InputError


def validate_branch_acceptance(
    promotion: dict[str, Any],
    release: dict[str, Any],
    validate_observation: Callable[..., str],
    parse_raw_json: Callable[[str, str], Any],
    error_type: type[ValueError],
) -> None:
    acceptance = promotion.get("staging_acceptance")
    merge_result = promotion.get("merge_result")
    if not isinstance(acceptance, dict) or not isinstance(merge_result, dict):
        raise error_type("branch promotion is missing acceptance or merge-tree binding")
    if release.get("merge_result_tree_sha") != merge_result.get("tree_sha"):
        raise error_type("Auditor merge result tree differs from Driver state")
    evidence = release.get("staging_acceptance")
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise error_type(
            "branch promotion requires two staging acceptance observations"
        )
    actual = {item.get("name"): item for item in evidence if isinstance(item, dict)}
    if set(actual) != {"staging-postmerge-pipeline", "staging-acceptance-note"}:
        raise error_type("staging acceptance observations are missing or duplicated")
    pipeline = parse_raw_json(
        validate_observation(
            actual["staging-postmerge-pipeline"],
            expected_name="staging-postmerge-pipeline",
            expected_url=acceptance.get("pipeline_api_url"),
        ),
        "staging-postmerge-pipeline",
    )
    if not isinstance(pipeline, dict) or any(
        pipeline.get(field) != value
        for field, value in (
            ("id", acceptance.get("pipeline_id")),
            ("sha", promotion.get("canonical_verified_sha")),
            ("ref", promotion.get("staging_branch")),
            ("status", "success"),
            ("web_url", acceptance.get("pipeline_url")),
        )
    ):
        raise error_type("staging postmerge pipeline evidence changed")
    note = parse_raw_json(
        validate_observation(
            actual["staging-acceptance-note"],
            expected_name="staging-acceptance-note",
            expected_url=acceptance.get("note_api_url"),
        ),
        "staging-acceptance-note",
    )
    if not isinstance(note, dict):
        raise error_type("staging acceptance note evidence must be an object")
    try:
        binding = validate_acceptance_note(
            note,
            note_id=acceptance.get("note_id"),
            verification_mr_iid=acceptance.get("verification_mr_iid"),
            merge_sha=promotion.get("canonical_verified_sha"),
            pipeline_id=acceptance.get("pipeline_id"),
            pipeline_url=acceptance.get("pipeline_url"),
            merger_id=acceptance.get("acceptance_author_id"),
            merged_at=acceptance.get("verification_merged_at"),
        )
    except (InputError, TypeError) as exc:
        raise error_type(f"staging acceptance note evidence changed: {exc}") from exc
    if any(binding.get(field) != acceptance.get(field) for field in binding):
        raise error_type("staging acceptance note evidence changed")
