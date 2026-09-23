#!/usr/bin/env python3
"""Validate a two-layer user-research report package without dependencies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote


VERSION_RE = re.compile(r"(?<![\w.-])v(\d+(?:\.\d+)*)", re.IGNORECASE)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def read_text(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read {path}: {exc}")
        return ""


def first_version(text: str) -> str | None:
    head = "\n".join(text.splitlines()[:20])
    match = VERSION_RE.search(head)
    return f"v{match.group(1)}" if match else None


def local_links(source: Path, text: str) -> list[Path]:
    found: list[Path] = []
    for raw in LINK_RE.findall(text):
        target = raw.strip().strip("<>").split("#", 1)[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        found.append((source.parent / unquote(target)).resolve())
    return found


def require_marker(
    item: dict, key: str, text: str, location: str, errors: list[str]
) -> None:
    marker = item.get(key)
    if marker and marker not in text:
        errors.append(
            f"{location} missing marker for {item.get('id', 'unnamed item')}: {marker!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    main_path = args.main.resolve()
    evidence_path = args.evidence.resolve()
    errors: list[str] = []

    main_text = read_text(main_path, errors)
    evidence_text = read_text(evidence_path, errors)
    if errors:
        print("REPORT PACKAGE: FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    main_version = first_version(main_text)
    evidence_version = first_version(evidence_text)
    if not main_version or not evidence_version:
        errors.append("main and evidence must each declare a version in the first 20 lines")
    elif main_version != evidence_version:
        errors.append(
            f"version mismatch: main={main_version}, evidence={evidence_version}"
        )

    main_links = local_links(main_path, main_text)
    evidence_links = local_links(evidence_path, evidence_text)
    if evidence_path not in main_links:
        errors.append("main report does not link to the evidence file")
    if main_path not in evidence_links:
        errors.append("evidence file does not link back to the main report")

    for source, links in ((main_path, main_links), (evidence_path, evidence_links)):
        for target in links:
            if not target.exists():
                errors.append(f"broken local link in {source.name}: {target}")

    if args.manifest:
        manifest_path = args.manifest.resolve()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"cannot read manifest {manifest_path}: {exc}")
            manifest = {}

        if not isinstance(manifest, dict):
            errors.append("manifest root must be a JSON object")
            manifest = {}

        expected_version = manifest.get("version")
        if expected_version and expected_version not in {main_version, evidence_version}:
            errors.append(
                f"manifest version {expected_version} does not match report version"
            )

        goals = manifest.get("research_goals", [])
        if not isinstance(goals, list) or not goals:
            errors.append("manifest research_goals must be a non-empty list")
        else:
            for goal in goals:
                if not isinstance(goal, dict):
                    errors.append("each research_goals item must be an object")
                    continue
                require_marker(goal, "main_marker", main_text, "main", errors)
                require_marker(goal, "evidence_marker", evidence_text, "evidence", errors)

        facts = manifest.get("shared_facts", [])
        if not isinstance(facts, list):
            errors.append("manifest shared_facts must be a list")
        else:
            for fact in facts:
                if not isinstance(fact, dict):
                    errors.append("each shared_facts item must be an object")
                    continue
                common = fact.get("value")
                if common:
                    fact = {**fact, "main_marker": common, "evidence_marker": common}
                require_marker(fact, "main_marker", main_text, "main", errors)
                require_marker(fact, "evidence_marker", evidence_text, "evidence", errors)

        required_files = manifest.get("required_files", [])
        if not isinstance(required_files, list):
            errors.append("manifest required_files must be a list")
        else:
            for raw in required_files:
                if not isinstance(raw, str):
                    errors.append("each required_files item must be a string")
                    continue
                target = (manifest_path.parent / raw).resolve()
                if not target.exists():
                    errors.append(f"required file missing: {target}")

    if errors:
        print("REPORT PACKAGE: FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    print(
        f"REPORT PACKAGE: PASS ({main_version}; "
        f"{main_path.name} + {evidence_path.name})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
