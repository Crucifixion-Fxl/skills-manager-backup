#!/usr/bin/env python3
"""Validate complete, unambiguous route tables.

Exit codes: 0 pass, 1 contract violation, 2 usage/dependency error.
"""

from __future__ import annotations

import sys
import re
from pathlib import Path

FAILURE_PREFIX = "FAIL: "
ROUTE_MODE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

try:
    import yaml
except ImportError:
    print(f"{FAILURE_PREFIX}PyYAML not installed", file=sys.stderr)
    sys.exit(2)


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        print(f"{FAILURE_PREFIX}cannot load {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def validate_keywords(
    table_path: Path,
    route_name: object,
    keywords: object,
    seen_keywords: dict[str, str],
) -> int:
    """Validate a route's keyword list and prevent ambiguous routing."""
    if not isinstance(keywords, list) or not keywords:
        print(f"{FAILURE_PREFIX}{table_path}: {route_name}: missing non-empty keywords list")
        return 1
    bad = 0
    route_label = str(route_name)
    for keyword in keywords:
        if not isinstance(keyword, str) or not keyword.strip():
            print(f"{FAILURE_PREFIX}{table_path}: {route_name}: invalid keyword {keyword!r}")
            bad += 1
            continue
        normalized = " ".join(keyword.lower().split())
        existing = seen_keywords.get(normalized)
        if existing is not None:
            print(
                f"{FAILURE_PREFIX}{table_path}: duplicate keyword {keyword!r} "
                f"for {existing!r} and {route_name!r}"
            )
            bad += 1
            continue
        for other, other_route in seen_keywords.items():
            if other_route != route_label and (
                normalized in other or other in normalized
            ):
                print(
                    f"{FAILURE_PREFIX}{table_path}: ambiguous keyword {keyword!r} "
                    f"overlaps {other!r} for {other_route!r} and {route_name!r}"
                )
                bad += 1
        seen_keywords[normalized] = route_label
    return bad


def validate_references(
    table_path: Path,
    route_name: object,
    field_name: str,
    references: object,
    available_files: set[str],
    reference_type: str,
) -> int:
    """Validate that a route's declared implementation dependencies exist."""
    if not isinstance(references, list):
        print(f"{FAILURE_PREFIX}{table_path}: {route_name}: {field_name} must be a list")
        return 1
    bad = 0
    for reference in references:
        if not isinstance(reference, str) or reference not in available_files:
            print(
                f"{FAILURE_PREFIX}{table_path}: {route_name}: {reference_type} not found: "
                f"{reference!r}"
            )
            bad += 1
    return bad


def validate_build_entries(
    entries: list[object],
    table_path: Path,
    workflow_files: set[str],
) -> tuple[int, set[str], set[str]]:
    """Validate build routes and collect implemented and planned workflows."""
    bad = 0
    routed_workflows: set[str] = set()
    planned_workflows: set[str] = set()
    seen_keywords: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"{FAILURE_PREFIX}{table_path}: build entry is not a mapping")
            bad += 1
            continue
        intent = entry.get("intent", "<missing intent>")
        workflow = entry.get("workflow_file")
        bad += validate_keywords(
            table_path, intent, entry.get("keywords"), seen_keywords
        )
        mode_keywords = entry.get("mode_keywords")
        if mode_keywords is not None:
            route_keywords = {
                " ".join(keyword.lower().split())
                for keyword in entry.get("keywords", [])
                if isinstance(keyword, str)
            }
            if not isinstance(mode_keywords, dict) or not mode_keywords:
                print(
                    f"{FAILURE_PREFIX}{table_path}: {intent}: "
                    "mode_keywords must be a non-empty mapping"
                )
                bad += 1
            else:
                assigned: set[str] = set()
                for mode, keywords in mode_keywords.items():
                    if not isinstance(mode, str) or not ROUTE_MODE.fullmatch(mode):
                        print(
                            f"{FAILURE_PREFIX}{table_path}: {intent}: invalid route mode"
                        )
                        bad += 1
                        continue
                    if not isinstance(keywords, list) or not keywords:
                        print(
                            f"{FAILURE_PREFIX}{table_path}: {intent}: "
                            "route mode requires non-empty keywords"
                        )
                        bad += 1
                        continue
                    normalized = {
                        " ".join(keyword.lower().split())
                        for keyword in keywords
                        if isinstance(keyword, str) and keyword.strip()
                    }
                    if len(normalized) != len(keywords) or not normalized <= route_keywords:
                        print(
                            f"{FAILURE_PREFIX}{table_path}: {intent}: "
                            "route mode keywords must be unique route keywords"
                        )
                        bad += 1
                    if assigned & normalized:
                        print(
                            f"{FAILURE_PREFIX}{table_path}: {intent}: "
                            "route mode keywords overlap"
                        )
                        bad += 1
                    assigned.update(normalized)
        if not isinstance(workflow, str) or not workflow:
            print(f"{FAILURE_PREFIX}{table_path}: {intent}: missing workflow_file")
            bad += 1
            continue
        bad += validate_references(
            table_path,
            intent,
            "internal_calls",
            entry.get("internal_calls"),
            workflow_files,
            "internal workflow",
        )
        if entry.get("status") == "planned":
            planned_workflows.add(workflow)
            planned_stop = entry.get("planned_stop")
            if not isinstance(planned_stop, str) or "STOP" not in planned_stop:
                print(
                    f"{FAILURE_PREFIX}{table_path}: {intent}: "
                    "planned route missing planned_stop with STOP"
                )
                bad += 1
            continue
        routed_workflows.add(workflow)
        if workflow not in workflow_files:
            print(f"{FAILURE_PREFIX}{table_path}: {intent}: workflow_file not found: {workflow}")
            bad += 1
    return bad, routed_workflows, planned_workflows


def validate_troubleshoot_entries(
    entries: list[object],
    table_path: Path,
    playbook_files: set[str],
    validator_files: set[str],
) -> tuple[int, set[str]]:
    """Validate troubleshoot routes and collect their referenced playbooks."""
    bad = 0
    routed_playbooks: set[str] = set()
    seen_keywords: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"{FAILURE_PREFIX}{table_path}: troubleshoot entry is not a mapping")
            bad += 1
            continue
        symptom = entry.get("symptom", "<missing symptom>")
        playbook = entry.get("playbook_file")
        bad += validate_keywords(
            table_path, symptom, entry.get("keywords"), seen_keywords
        )
        if not isinstance(playbook, str) or not playbook:
            print(f"{FAILURE_PREFIX}{table_path}: {symptom}: missing playbook_file")
            bad += 1
            continue
        routed_playbooks.add(playbook)
        if playbook not in playbook_files:
            print(f"{FAILURE_PREFIX}{table_path}: {symptom}: playbook_file not found: {playbook}")
            bad += 1
        bad += validate_references(
            table_path,
            symptom,
            "related_validators",
            entry.get("related_validators"),
            validator_files,
            "related validator",
        )
    return bad, routed_playbooks


def report_unrouted_files(
    table_path: Path,
    available_files: set[str],
    routed_files: set[str],
    planned_files: set[str],
    file_type: str,
) -> int:
    """Report implemented files not covered by an explicit route."""
    bad = 0
    for path in sorted(available_files - routed_files - planned_files):
        print(f"{FAILURE_PREFIX}{table_path}: implemented {file_type} not routed: {path}")
        bad += 1
    return bad


def resolve_skill_root(argv: list[str]) -> Path | None:
    if len(argv) != 2:
        print("usage: check_routes.py <skill-root>", file=sys.stderr)
        return None
    root = Path(argv[1]).resolve()
    if not root.is_dir():
        print(f"{FAILURE_PREFIX}skill root does not exist: {root}", file=sys.stderr)
        return None
    return root


def load_route_list(path: Path, key: str) -> list[object] | None:
    data = load_yaml(path)
    if not isinstance(data, dict):
        print(f"{FAILURE_PREFIX}{path}: top-level YAML must be a mapping")
        return None
    entries = data.get(key)
    if not isinstance(entries, list):
        print(f"{FAILURE_PREFIX}{path}: missing {key} list")
        return None
    return entries


def load_route_conflicts(path: Path) -> list[object] | None:
    data = load_yaml(path)
    if not isinstance(data, dict):
        print(f"{FAILURE_PREFIX}{path}: top-level YAML must be a mapping")
        return None
    conflicts = data.get("route_conflicts")
    if not isinstance(conflicts, list):
        print(f"{FAILURE_PREFIX}{path}: missing route_conflicts list")
        return None
    return conflicts


def validate_route_conflicts(
    conflicts: list[object],
    table_path: Path,
    build_entries: list[object],
) -> int:
    """Validate explicit same-capability conflicts without blocking additives."""
    workflow_entries = {
        entry.get("workflow_file"): entry
        for entry in build_entries
        if isinstance(entry, dict) and isinstance(entry.get("workflow_file"), str)
    }
    seen_ids: set[str] = set()
    seen_pairs: set[frozenset[tuple[str, str]]] = set()
    bad = 0
    for conflict in conflicts:
        if not isinstance(conflict, dict) or set(conflict) != {
            "id",
            "capability",
            "participants",
            "resolution",
            "question",
        }:
            print(f"{FAILURE_PREFIX}{table_path}: route conflict must use exact schema")
            bad += 1
            continue
        conflict_id = conflict["id"]
        if not isinstance(conflict_id, str) or not conflict_id or conflict_id in seen_ids:
            print(f"{FAILURE_PREFIX}{table_path}: route conflict id is missing or duplicate")
            bad += 1
        else:
            seen_ids.add(conflict_id)
        if (
            not isinstance(conflict["capability"], str)
            or not conflict["capability"]
            or conflict["resolution"] != "ask-focused-clarification"
            or not isinstance(conflict["question"], str)
            or not conflict["question"].endswith("?")
        ):
            print(f"{FAILURE_PREFIX}{table_path}: route conflict resolution is invalid")
            bad += 1
        participants = conflict["participants"]
        if not isinstance(participants, list) or len(participants) != 2:
            print(f"{FAILURE_PREFIX}{table_path}: route conflict requires exactly two participants")
            bad += 1
            continue
        pair: set[tuple[str, str]] = set()
        for participant in participants:
            if not isinstance(participant, dict) or set(participant) != {
                "workflow_file",
                "mode",
            }:
                print(f"{FAILURE_PREFIX}{table_path}: conflict participant uses invalid schema")
                bad += 1
                continue
            workflow = participant["workflow_file"]
            mode = participant["mode"]
            entry = workflow_entries.get(workflow)
            modes = {"default"}
            if isinstance(entry, dict) and isinstance(entry.get("mode_keywords"), dict):
                modes.update(entry["mode_keywords"])
            if entry is None or mode not in modes:
                print(f"{FAILURE_PREFIX}{table_path}: conflict participant is not a routed mode")
                bad += 1
                continue
            pair.add((workflow, mode))
        frozen_pair = frozenset(pair)
        if len(frozen_pair) != 2 or frozen_pair in seen_pairs:
            print(f"{FAILURE_PREFIX}{table_path}: route conflict pair is duplicate or incomplete")
            bad += 1
        else:
            seen_pairs.add(frozen_pair)
        selected_prompt_parts: list[str] = []
        if len(pair) == 2:
            for workflow, mode in sorted(pair):
                entry = workflow_entries[workflow]
                mode_map = entry.get("mode_keywords")
                keywords = (
                    mode_map.get(mode)
                    if mode != "default" and isinstance(mode_map, dict)
                    else entry.get("keywords")
                )
                if isinstance(keywords, list) and keywords:
                    selected_prompt_parts.append(str(keywords[0]))
            matched = resolve_route_conflicts(
                " and ".join(selected_prompt_parts), build_entries, conflicts
            )
            if len(matched) != 1 or matched[0].get("id") != conflict_id:
                print(
                    f"{FAILURE_PREFIX}{table_path}: route conflict is not reachable "
                    "through executable selection"
                )
                bad += 1
    return bad


def select_build_route_modes(
    prompt: str, build_entries: list[object]
) -> set[tuple[str, str]]:
    """Select routed workflow modes using the same bounded keyword contract."""
    normalized_prompt = " ".join(prompt.lower().split())
    selected: set[tuple[str, str]] = set()
    for entry in build_entries:
        if not isinstance(entry, dict):
            continue
        workflow = entry.get("workflow_file")
        keywords = entry.get("keywords")
        if not isinstance(workflow, str) or not isinstance(keywords, list):
            continue
        if not any(
            isinstance(keyword, str)
            and " ".join(keyword.lower().split()) in normalized_prompt
            for keyword in keywords
        ):
            continue
        matched_modes: set[str] = set()
        mode_keywords = entry.get("mode_keywords", {})
        if isinstance(mode_keywords, dict):
            for mode, values in mode_keywords.items():
                if isinstance(mode, str) and isinstance(values, list) and any(
                    isinstance(keyword, str)
                    and " ".join(keyword.lower().split()) in normalized_prompt
                    for keyword in values
                ):
                    matched_modes.add(mode)
        if not matched_modes:
            matched_modes.add("default")
        selected.update((workflow, mode) for mode in matched_modes)
    return selected


def resolve_route_conflicts(
    prompt: str, build_entries: list[object], conflicts: list[object]
) -> list[dict[str, object]]:
    """Return structured conflicts selected by one concrete prompt."""
    selected = select_build_route_modes(prompt, build_entries)
    matches: list[dict[str, object]] = []
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        participants = conflict.get("participants")
        if not isinstance(participants, list):
            continue
        required: set[tuple[str, str]] = set()
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            workflow = participant.get("workflow_file")
            mode = participant.get("mode")
            if isinstance(workflow, str) and isinstance(mode, str):
                required.add((workflow, mode))
        if len(required) == 2 and required <= selected:
            matches.append(conflict)
    return matches


def load_route_entries(root: Path) -> tuple[list[object], list[object]] | None:
    build_path = root / "references/data/routes-build.yaml"
    troubleshoot_path = root / "references/data/routes-troubleshoot.yaml"
    build_entries = load_route_list(build_path, "build_intents")
    if build_entries is None:
        return None
    troubleshoot_entries = load_route_list(troubleshoot_path, "troubleshoot_symptoms")
    if troubleshoot_entries is None:
        return None
    return build_entries, troubleshoot_entries


def validate_route_entries(
    root: Path,
    build_entries: list[object],
    troubleshoot_entries: list[object],
    route_conflicts: list[object] | None = None,
) -> int:
    build_path = root / "references/data/routes-build.yaml"
    troubleshoot_path = root / "references/data/routes-troubleshoot.yaml"

    workflow_files = {
        f"workflows/{path.name}"
        for path in (root / "workflows").glob("*.md")
        if path.name != "README.md"
    }
    playbook_files = {
        f"troubleshooting/{path.name}"
        for path in (root / "troubleshooting").glob("*.md")
        if path.name != "README.md"
    }
    validator_files = {
        path.name
        for path in (root / "validators").glob("*.py")
        if path.name != "__init__.py"
    }
    bad, routed_workflows, planned_workflows = validate_build_entries(
        build_entries, build_path, workflow_files
    )
    if route_conflicts is None:
        route_conflicts = load_route_conflicts(build_path)
    if route_conflicts is None:
        bad += 1
    else:
        bad += validate_route_conflicts(route_conflicts, build_path, build_entries)
    bad += report_unrouted_files(
        build_path,
        workflow_files,
        routed_workflows,
        planned_workflows,
        "workflow",
    )
    troubleshoot_bad, routed_playbooks = validate_troubleshoot_entries(
        troubleshoot_entries,
        troubleshoot_path,
        playbook_files,
        validator_files,
    )
    bad += troubleshoot_bad
    bad += report_unrouted_files(
        troubleshoot_path,
        playbook_files,
        routed_playbooks,
        set(),
        "playbook",
    )

    if bad:
        print(f"{FAILURE_PREFIX}{bad} route violation(s)")
        return 1
    print(
        "PASS: route tables match implemented workflows/playbooks "
        f"({len(routed_workflows)} build, {len(planned_workflows)} planned, "
        f"{len(routed_playbooks)} troubleshoot)"
    )
    return 0


def main(argv: list[str]) -> int:
    root = resolve_skill_root(argv)
    if root is None:
        return 2

    route_entries = load_route_entries(root)
    if route_entries is None:
        return 1

    return validate_route_entries(root, *route_entries)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
