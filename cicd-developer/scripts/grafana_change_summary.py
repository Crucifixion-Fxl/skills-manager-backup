#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render a compact, token-free summary of one Dashboard source change."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def panel_map(dashboard: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Return every top-level or nested panel keyed by its stable ID."""
    result: dict[int, dict[str, Any]] = {}
    pending = list(dashboard.get("panels", []))
    while pending:
        panel = pending.pop()
        if not isinstance(panel, dict):
            continue
        if isinstance(panel.get("id"), int):
            result[panel["id"]] = panel
        pending.extend(panel.get("panels", []))
    return result


def render_summary(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    path: str,
) -> str:
    """Describe Dashboard and panel-level source changes without credentials."""
    old = panel_map(before or {})
    new = panel_map(after)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(panel_id for panel_id in set(old) & set(new) if old[panel_id] != new[panel_id])
    lines = [
        f"# Dashboard change: {after.get('title', '<untitled>')}",
        "",
        f"- Path: `{path}`",
        f"- UID: `{after.get('uid', '<missing>')}`",
        f"- Added panels: {', '.join(map(str, added)) or 'none'}",
        f"- Modified panels: {', '.join(map(str, modified)) or 'none'}",
        f"- Possibly removed panels: {', '.join(map(str, removed)) or 'none'}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("after", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="emit summary metadata as JSON")
    args = parser.parse_args()
    try:
        after = json.loads(args.after.read_text(encoding="utf-8"))
        before = json.loads(args.before.read_text(encoding="utf-8")) if args.before else None
        rendered = render_summary(before, after, args.after.as_posix())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        elif args.json:
            print(json.dumps({"summary": rendered}, ensure_ascii=False, sort_keys=True))
        else:
            print(rendered, end="")
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
