#!/usr/bin/env python3
"""Read-only check of the documented public inventory against its pinned source."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[1] / "references/project-query-whitelist.md"
START = "<!-- whitelist-snapshot:start -->"
END = "<!-- whitelist-snapshot:end -->"


def render_inventory(registry: dict) -> str:
    """Project only logical field IDs, types and operators; never storage metadata."""
    if set(registry["relations"]) != {"audience_profile"} or registry["edges"]:
        raise ValueError("Whitelist structure changed; review the reference before updating")
    lines = [
        f'Registry: `{registry["registry_version"]}`.',
        "Approved join edges: 0.",
        "",
        "| Logical field | Value type | Allowed operators |",
        "| --- | --- | --- |",
    ]
    fields = registry["relations"]["audience_profile"]["fields"]
    for name, field in sorted(fields.items()):
        operators = ", ".join(f"`{op}`" for op in field["operators"])
        lines.append(f'| `audience_profile.{name}` | `{field["value_type"]}` | {operators} |')
    return "\n".join(lines)


def snapshot(document: str) -> str:
    if document.count(START) != 1 or document.count(END) != 1:
        raise ValueError("Expected one public whitelist snapshot")
    return document.split(START, 1)[1].split(END, 1)[0].strip()


def check(document: str, registry: dict) -> None:
    if snapshot(document) != render_inventory(registry):
        raise ValueError("Public whitelist reference differs from pinned Platform registry")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-root", type=Path, required=True)
    args = parser.parse_args()
    document = REFERENCE.read_text(encoding="utf-8")
    commit = re.search(r"Source commit: `([0-9a-f]{40})`\.", document)
    artifact = re.search(r"Source artifact: `([^`\n]+)`\.", document)
    if commit is None or artifact is None:
        parser.error("Missing immutable source provenance")
    result = subprocess.run(
        ["git", "-C", str(args.platform_root), "show", f"{commit[1]}:{artifact[1]}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        parser.error("Pinned source is unavailable in the supplied Platform checkout")
    try:
        check(document, json.loads(result.stdout))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print("Public whitelist matches pinned Platform registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
