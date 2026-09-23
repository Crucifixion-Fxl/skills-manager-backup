#!/usr/bin/env python3
"""Expire private work-method artifacts from the stable user state root."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat


SCRIPT_DIR = Path(__file__).resolve().parent
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "work_method_validate_manifest_for_retention",
    SCRIPT_DIR / "validate_manifest.py",
)
if VALIDATOR_SPEC is None or VALIDATOR_SPEC.loader is None:
    raise RuntimeError("manifest validator could not be loaded")
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


ARTIFACT_ROOT = VALIDATOR.STATE_ROOT
MARKER = VALIDATOR.STATE_MARKER
MARKER_CONTENT = VALIDATOR.STATE_MARKER_CONTENT
MAX_SCAN_ENTRIES = 4096
QUALITY_GATE_FIELDS = {
    "schema",
    "snapshot_id",
    "filter_hash",
    "selection_policy_hash",
    "quality_policy_hash",
    "rubric_version",
    "decision",
    "accepted_at",
    "accepted_by",
    "report_digest",
    "approval_provenance",
    "quality_comparison",
}
APPROVAL_PROVENANCE_FIELDS = {
    "receipt_digest",
    "round_id",
    "author",
    "week",
    "weekly_report_digest",
    "approved_by",
    "accepted_at",
}
QUALITY_COMPARISON_FIELDS = {
    "method",
    "metric",
    "snapshot_digest",
    "quality_review_digest",
    "quality_review_ref",
    "packet_digest",
    "claim_ledger_digest",
    "label_map_digest",
    "baseline_label",
    "candidate_label",
    "required_claim_ids",
    "reviewer",
    "baseline",
    "candidate",
}
QUALITY_RUN_FIELDS = {
    "report_digest",
    "input_digest",
    "input_characters",
    "quality_dimensions",
    "required_claim_count",
    "unsupported_claim_count",
    "privacy_violation_count",
    "critical_gates",
}
QUALITY_TOKEN_FIELDS = {"input_tokens", "token_receipt_digest", "token_evidence"}


def _walk_entries(root: Path):
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                yield path
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)


def _is_durable_quality_gate(
    path: Path, relative_parts: tuple[str, ...], metadata: os.stat_result
) -> bool:
    if not (
        len(relative_parts) == 2
        and relative_parts[0] == "quality-gates"
        and path.suffix == ".json"
        and metadata.st_uid == os.getuid()
        and metadata.st_mode & 0o077 == 0
        and metadata.st_size <= 1024 * 1024
    ):
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not (
        isinstance(document, dict)
        and set(document) == QUALITY_GATE_FIELDS
        and document.get("schema") == "addx.work_method_quality_gate.v1"
        and document.get("decision") == "accept"
    ):
        return False
    comparison = document.get("quality_comparison")
    if not isinstance(comparison, dict) or set(comparison) != QUALITY_COMPARISON_FIELDS:
        return False
    approval = document.get("approval_provenance")
    if not isinstance(approval, dict) or set(approval) != APPROVAL_PROVENANCE_FIELDS:
        return False
    if set(comparison.get("reviewer", {})) != {"provider", "model", "request_id"}:
        return False
    if {comparison.get("baseline_label"), comparison.get("candidate_label")} != {"A", "B"}:
        return False
    for name in ("baseline", "candidate"):
        run = comparison.get(name)
        if not isinstance(run, dict):
            return False
        expected = QUALITY_RUN_FIELDS | (
            QUALITY_TOKEN_FIELDS if "input_tokens" in run else set()
        )
        if set(run) != expected:
            return False
    try:
        VALIDATOR.validate_quality_gate_document(
            document, require_current_policy=True
        )
    except VALIDATOR.ContractError:
        return False
    return True


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"artifact path contains a symlink component: {cursor}")


def prepare_root(root: Path = ARTIFACT_ROOT) -> Path:
    _reject_symlink_components(root)
    if root.exists():
        metadata = root.stat()
        if metadata.st_uid != os.getuid() or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("artifact root must be a current-user directory")
        marker = root / MARKER
        if marker.is_symlink():
            raise ValueError("artifact marker must not be a symlink")
        if not marker.exists():
            if any(root.iterdir()):
                raise ValueError("refusing to adopt a nonempty unmarked artifact root")
        elif marker.read_text(encoding="utf-8") != MARKER_CONTENT:
            raise ValueError("artifact marker is invalid")
    else:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    _reject_symlink_components(root)
    metadata = root.stat()
    if metadata.st_uid != os.getuid() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("artifact root must be a current-user directory")
    root.chmod(0o700)
    marker = root / MARKER
    if not marker.exists():
        marker.write_text(MARKER_CONTENT, encoding="utf-8")
    marker.chmod(0o600)
    return root.resolve()


def expired_artifacts(
    root: Path,
    *,
    now: datetime | None = None,
) -> list[Path]:
    controlled = prepare_root(root)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    candidates: list[Path] = []
    for entry_count, path in enumerate(_walk_entries(controlled), start=1):
        if entry_count > MAX_SCAN_ENTRIES:
            raise ValueError(f"artifact root exceeds the {MAX_SCAN_ENTRIES}-entry scan limit")
        if path.name == MARKER or path.is_symlink() or not path.is_file():
            continue
        try:
            metadata = path.stat()
        except OSError:
            continue
        relative_parts = path.relative_to(controlled).parts
        if metadata.st_uid != os.getuid() or _is_durable_quality_gate(
            path, relative_parts, metadata
        ):
            continue
        days = 28 if "ledger" in relative_parts else 35
        cutoff = (instant.astimezone(timezone.utc) - timedelta(days=days)).timestamp()
        if metadata.st_mtime < cutoff:
            candidates.append(path)
    return sorted(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        controlled = prepare_root(ARTIFACT_ROOT)
        paths = expired_artifacts(controlled)
        if args.apply:
            print(
                json.dumps(
                    {
                        "status": "plan",
                        "count": len(paths),
                        "paths": [str(path) for path in paths],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            for path in paths:
                resolved = path.resolve()
                if not (resolved.parent == controlled or controlled in resolved.parents):
                    raise ValueError(f"refusing to unlink outside artifact root: {path}")
                path.unlink()
            for directory in sorted(
                (path for path in controlled.rglob("*") if path.is_dir() and not path.is_symlink()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        result = {
            "status": "pruned" if args.apply else "dry-run",
            "count": len(paths),
            "paths": [str(path) for path in paths],
        }
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
