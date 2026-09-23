#!/usr/bin/env python3
"""Build a private scene ledger deterministically from redacted extractor JSONL."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "work_method_validate_manifest_for_builder",
    SCRIPT_DIR / "validate_manifest.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("manifest validator could not be loaded")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
ARTIFACT_ROOT = VALIDATOR.STATE_ROOT


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _private_input(path: Path, label: str, maximum_bytes: int) -> None:
    VALIDATOR._reject_symlink_components(path, label)
    _require(path.exists(), f"{label} is missing")
    metadata = path.lstat()
    _require(stat.S_ISREG(metadata.st_mode), f"{label} must be a regular file")
    _require(metadata.st_uid == os.getuid(), f"{label} must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, f"{label} must be private")
    _require(metadata.st_size <= maximum_bytes, f"{label} exceeds the size limit")


def _read_records(path: Path) -> list[dict[str, Any]]:
    _private_input(path, "evidence", 64 * 1024 * 1024)
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            _require(line_number <= 50_000, "evidence exceeds 50,000 records")
            _require(bool(line.strip()), f"evidence line {line_number} must not be blank")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"evidence line {line_number} is not valid JSON") from error
            _require(isinstance(record, dict), f"evidence line {line_number} must be an object")
            records.append(record)
    _require(bool(records), "evidence is empty")
    return records


def build_ledger(evidence_path: Path, selection_path: Path) -> dict[str, Any]:
    records = _read_records(evidence_path)
    _private_input(selection_path, "selection", 64 * 1024)
    try:
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("selection must be valid JSON") from error
    _require(isinstance(selection, dict) and set(selection) == {"scenes"}, "selection schema is invalid")
    rows = selection["scenes"]
    _require(isinstance(rows, list) and 1 <= len(rows) <= 8, "selection must contain 1..8 scenes")
    scenes: list[dict[str, Any]] = []
    scene_ids: set[str] = set()
    selected_lines: set[int] = set()
    for index, value in enumerate(rows):
        _require(isinstance(value, dict), f"selection scene {index} must be an object")
        _require(
            set(value) == {"id", "summary", "record_lines"},
            f"selection scene {index} fields are invalid",
        )
        scene_id = value["id"]
        _require(isinstance(scene_id, str) and scene_id not in scene_ids, "scene IDs must be unique strings")
        scene_ids.add(scene_id)
        line_numbers = value["record_lines"]
        _require(
            isinstance(line_numbers, list) and 1 <= len(line_numbers) <= 64,
            f"selection scene {scene_id} must contain 1..64 record lines",
        )
        _require(
            all(isinstance(number, int) and not isinstance(number, bool) for number in line_numbers),
            f"selection scene {scene_id} record lines must be integers",
        )
        _require(
            line_numbers == sorted(set(line_numbers)),
            f"selection scene {scene_id} record lines must be unique and sorted",
        )
        _require(
            all(1 <= number <= len(records) for number in line_numbers),
            f"selection scene {scene_id} references a missing record line",
        )
        _require(
            not selected_lines.intersection(line_numbers),
            "one extractor record cannot be reused across scenes",
        )
        selected_lines.update(line_numbers)
        scenes.append(
            VALIDATOR.build_scene_from_records(
                scene_id,
                value["summary"],
                [records[number - 1] for number in line_numbers],
                line_numbers,
            )
        )
    return {"schema": "addx.work_method_frozen_snapshot.v1", "scenes": scenes}


def write_ledger(
    document: dict[str, Any],
    output: Path,
    *,
    artifact_root: Path = ARTIFACT_ROOT,
) -> None:
    VALIDATOR._reject_symlink_components(artifact_root, "scene ledger artifact root")
    _require(artifact_root.exists(), "scene ledger artifact root is missing")
    root = artifact_root.resolve()
    root_metadata = root.lstat()
    _require(stat.S_ISDIR(root_metadata.st_mode), "scene ledger artifact root must be a directory")
    _require(root_metadata.st_uid == os.getuid(), "scene ledger artifact root must be owned by the current user")
    _require(root_metadata.st_mode & 0o077 == 0, "scene ledger artifact root must be private")
    VALIDATOR._reject_symlink_components(output, "scene ledger output")
    _require(not output.exists() and not output.is_symlink(), "output already exists")
    _require(output.suffix == ".json", "output must use a .json filename")
    output_parent = output.parent.resolve()
    _require(
        output_parent == root or root in output_parent.parents,
        "output must stay inside the fixed work-method state root",
    )
    _require(output_parent.exists(), "output parent must already exist")
    parent_metadata = output_parent.lstat()
    _require(stat.S_ISDIR(parent_metadata.st_mode), "output parent must be a directory")
    _require(parent_metadata.st_uid == os.getuid(), "output parent must be owned by the current user")
    _require(parent_metadata.st_mode & 0o077 == 0, "output parent must be private")
    payload = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        write_ledger(build_ledger(args.evidence, args.selection), args.output)
    except (OSError, UnicodeDecodeError, ValueError, VALIDATOR.ContractError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 1
    print(json.dumps({"status": "written", "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
