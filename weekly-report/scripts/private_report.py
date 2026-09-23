#!/usr/bin/env python3
"""Create, finalize, and expire private weekly-report files safely."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile


FINAL_RE = re.compile(r"^weekly-report-(\d{4}-\d{2}-\d{2})\.(html|approval\.json)$")
DRAFT_RE = re.compile(r"^\.weekly-report-(\d{4}-\d{2}-\d{2})\.[A-Za-z0-9_-]+\.draft$")


def _report_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError("date must be YYYY-MM-DD") from error


def _private_regular(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ValueError(f"path must not be a symlink: {path}")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError(f"path must be a current-user regular file: {path}")
    if metadata.st_mode & 0o077:
        raise ValueError(f"path must not allow group/other access: {path}")
    return metadata


def prepare(report_date: str, *, root: Path = Path("/tmp")) -> Path:
    normalized = _report_date(report_date)
    root = root.resolve()
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".weekly-report-{normalized}.", suffix=".draft", dir=root
    )
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    _private_regular(path)
    return path


def finalize(draft: Path, report_date: str, *, root: Path = Path("/tmp")) -> Path:
    normalized = _report_date(report_date)
    root = root.resolve()
    resolved_draft = draft.resolve()
    draft_match = DRAFT_RE.fullmatch(resolved_draft.name)
    if resolved_draft.parent != root or draft_match is None:
        raise ValueError("draft is outside the private weekly-report root")
    if draft_match.group(1) != normalized:
        raise ValueError("draft date does not match the requested report date")
    _private_regular(resolved_draft)
    target = root / f"weekly-report-{normalized}.html"
    if target.exists() or target.is_symlink():
        _private_regular(target)
    os.replace(resolved_draft, target)
    os.chmod(target, 0o600)
    _private_regular(target)
    return target


def expired_files(
    *,
    root: Path = Path("/tmp"),
    now: datetime | None = None,
    retention_days: int = 7,
) -> list[Path]:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = (instant.astimezone(timezone.utc) - timedelta(days=retention_days)).timestamp()
    candidates: list[Path] = []
    for path in root.resolve().iterdir():
        if FINAL_RE.fullmatch(path.name) is None and DRAFT_RE.fullmatch(path.name) is None:
            continue
        try:
            metadata = _private_regular(path)
        except (OSError, ValueError):
            continue
        if metadata.st_mtime < cutoff:
            candidates.append(path)
    return sorted(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--date", required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--date", required=True)
    finalize_parser.add_argument("--draft", required=True, type=Path)
    prune_parser = subparsers.add_parser("prune")
    prune_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            path = prepare(args.date)
            result = {"status": "prepared", "path": str(path)}
        elif args.command == "finalize":
            path = finalize(args.draft, args.date)
            result = {"status": "finalized", "path": str(path)}
        else:
            paths = expired_files()
            if args.apply:
                for path in paths:
                    path.unlink()
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
