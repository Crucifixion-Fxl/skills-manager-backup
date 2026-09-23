#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Validate an AI paper figure HTML artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


REQUIRED_BRIEF_FIELDS = (
    "story_spine",
    "figure_role",
    "selected_context",
    "panels",
    "caption_suggestion",
    "caption_outside_figure",
    "open_assumptions",
)


class FigureHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.svg_count = 0
        self.panel_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "svg":
            self.svg_count += 1
        if "data-figure-panel" in attr_map:
            value = attr_map.get("data-figure-panel") or ""
            self.panel_ids.append(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a self-contained AI paper figure HTML artifact.")
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--brief", required=True, type=Path, help="Separate visual brief JSON file.")
    parser.add_argument("--allow-no-svg", action="store_true", help="Allow screenshot-only artifacts without inline SVG.")
    return parser.parse_args()


def _validate_top_level_contract(brief: dict, errors: list[str]) -> tuple[object, object]:
    for key in REQUIRED_BRIEF_FIELDS:
        if key not in brief:
            errors.append(f"visual brief missing required field: {key}")
    extra_keys = sorted(set(brief) - set(REQUIRED_BRIEF_FIELDS))
    if extra_keys:
        errors.append(f"visual brief has fields outside the seven-field contract: {', '.join(extra_keys)}")
    selected_context = brief.get("selected_context", [])
    panels = brief.get("panels", [])
    if not isinstance(selected_context, list) or not selected_context:
        errors.append("visual brief selected_context must be a non-empty list")
    if not isinstance(panels, list) or not panels:
        errors.append("visual brief panels must be a non-empty list")
    if brief.get("caption_outside_figure") is not True:
        errors.append("visual brief caption_outside_figure must be true")
    if not isinstance(brief.get("open_assumptions", []), list):
        errors.append("visual brief open_assumptions must be a list")
    return selected_context, panels


def _collect_context_ids(selected_context: object, errors: list[str]) -> set[str]:
    context_ids: set[str] = set()
    if isinstance(selected_context, list):
        for index, item in enumerate(selected_context, start=1):
            if not isinstance(item, dict):
                errors.append(f"visual brief selected_context item {index} must be an object with id")
                continue
            context_id = str(item.get("id", "")).strip()
            if not context_id:
                errors.append(f"visual brief selected_context item {index} missing id")
            else:
                context_ids.add(context_id)
    return context_ids


def _extract_panel_refs(key: str, value: object, panel_id: str, errors: list[str]) -> list[str]:
    if key == "context_ids":
        if not isinstance(value, list):
            errors.append(f"visual brief panel {panel_id} context_ids must be a list")
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    if not isinstance(value, list):
        errors.append(f"visual brief panel {panel_id} claims must be a list")
        return []

    refs: list[str] = []
    for claim_index, claim in enumerate(value, start=1):
        if not isinstance(claim, dict):
            continue
        claim_refs = claim.get("context_ids")
        if claim_refs is None:
            continue
        if not isinstance(claim_refs, list):
            errors.append(f"visual brief panel {panel_id} claim {claim_index} context_ids must be a list")
            continue
        refs.extend(str(item).strip() for item in claim_refs if str(item).strip())
    return refs


def _validate_panel(panel: object, index: int, context_ids: set[str], errors: list[str]) -> None:
    if not isinstance(panel, dict):
        errors.append(f"visual brief panel {index} must be an object")
        return

    raw_panel_id = str(panel.get("id", "")).strip()
    if not raw_panel_id:
        errors.append(f"visual brief panel {index} missing id")
    panel_id = raw_panel_id or f"<panel {index}>"
    panel_refs: set[str] = set()
    for key in ("context_ids", "claims"):
        value = panel.get(key)
        if value is None:
            continue
        for ref in _extract_panel_refs(key, value, panel_id, errors):
            if ref not in context_ids:
                errors.append(f"visual brief panel {panel_id} references unknown context id: {ref}")
            panel_refs.add(ref)
    if not panel_refs:
        errors.append(f"visual brief panel {panel_id} must reference selected_context by id")


def _validate_panels(panels: object, context_ids: set[str], errors: list[str]) -> None:
    if isinstance(panels, list):
        for index, panel in enumerate(panels, start=1):
            _validate_panel(panel, index, context_ids, errors)


def load_brief(brief_file: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not brief_file.is_file():
        return {}, [f"visual brief file not found: {brief_file}"]
    raw = brief_file.read_text(encoding="utf-8")
    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, [f"visual brief JSON is invalid: {exc}"]

    selected_context, panels = _validate_top_level_contract(brief, errors)
    context_ids = _collect_context_ids(selected_context, errors)
    _validate_panels(panels, context_ids, errors)
    return brief, errors


def main() -> int:
    args = parse_args()
    html_file = args.html_file.expanduser().resolve()
    brief_file = args.brief.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not html_file.is_file():
        print(f"HTML file not found: {html_file}", file=sys.stderr)
        return 1

    content = html_file.read_text(encoding="utf-8")
    parser = FigureHTMLParser()
    try:
        parser.feed(content)
    except Exception as exc:
        errors.append(f"HTML parse failed: {exc}")

    brief, brief_errors = load_brief(brief_file)
    errors.extend(brief_errors)

    if parser.svg_count == 0 and not args.allow_no_svg:
        errors.append("no inline SVG found; pass --allow-no-svg only for screenshot-only evidence panels")
    if not parser.panel_ids:
        errors.append("no data-figure-panel sections found")

    panel_ids_in_brief = [str(panel.get("id", "")) for panel in brief.get("panels", []) if isinstance(panel, dict)]
    for panel_id in parser.panel_ids:
        if panel_id and panel_id not in panel_ids_in_brief:
            errors.append(f"panel {panel_id} appears in HTML but not in visual brief")
    for panel_id in panel_ids_in_brief:
        if panel_id and panel_id not in parser.panel_ids:
            errors.append(f"panel {panel_id} appears in visual brief but not in HTML")

    forbidden_patterns = [
        r"\bTODO\b",
        r"lorem ipsum",
        r"<script[^>]*src=",
    ]
    for pattern in forbidden_patterns:
        if re.search(pattern, content, flags=re.IGNORECASE):
            errors.append(f"forbidden placeholder or external dependency matched: {pattern}")

    report = {"file": str(html_file), "brief": str(brief_file), "errors": errors, "warnings": warnings}
    print(json.dumps(report, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
