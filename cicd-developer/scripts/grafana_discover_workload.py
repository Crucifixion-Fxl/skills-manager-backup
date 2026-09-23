#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Discover a Kubernetes Rollout or Deployment without reading Secret data."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


SKIP_PARTS = {".git", ".terraform", "node_modules", "secret", "secrets", "vendor"}
DOCUMENT_BOUNDARY = re.compile(r"(?m)^---\s*(?:#.*)?$")
TOP_LEVEL = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_-]*):\s*(?P<value>.*?)\s*$")
NESTED = re.compile(r"^\s+(?P<key>[A-Za-z][A-Za-z0-9_-]*):\s*(?P<value>.*?)\s*$")


@dataclass(frozen=True)
class Workload:
    kind: str
    name: str
    namespace: str | None
    path: Path


def _scalar(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _document_workload(document: str, path: Path) -> Workload | None:
    kind: str | None = None
    metadata: dict[str, str] = {}
    in_metadata = False
    for line in document.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = TOP_LEVEL.match(line)
        if top:
            key, value = top.group("key"), _scalar(top.group("value"))
            in_metadata = key == "metadata" and not value
            if key == "kind":
                kind = value
            continue
        if in_metadata:
            nested = NESTED.match(line)
            if nested and nested.group("key") in {"name", "namespace"}:
                metadata[nested.group("key")] = _scalar(nested.group("value"))
    if kind not in {"Rollout", "Deployment"} or not metadata.get("name"):
        return None
    return Workload(kind, metadata["name"], metadata.get("namespace"), path)


def discover_candidates(application_root: Path) -> list[Workload]:
    """Read ordinary workload manifests only; skipped paths can contain Secrets."""
    root = application_root.resolve()
    candidates: list[Workload] = []
    for path in sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"))):
        if set(path.relative_to(root).parts[:-1]) & SKIP_PARTS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read workload manifest {path}: {exc}") from exc
        for document in DOCUMENT_BOUNDARY.split(text):
            workload = _document_workload(document, path)
            if workload:
                candidates.append(workload)
    return candidates


def discover_workload(application_root: Path, workload_name: str | None = None) -> Workload:
    """Choose explicit service, then Rollout, then Deployment; never guess ties."""
    candidates = discover_candidates(application_root)
    if workload_name:
        candidates = [item for item in candidates if item.name == workload_name]
        if not candidates:
            raise ValueError(f"workload {workload_name!r} was not found")
    rollouts = [item for item in candidates if item.kind == "Rollout"]
    preferred = rollouts or [item for item in candidates if item.kind == "Deployment"]
    unique = {(item.kind, item.name, item.namespace) for item in preferred}
    if not preferred:
        raise ValueError("no Kubernetes Rollout or Deployment manifest was found")
    if len(unique) != 1:
        choices = ", ".join(sorted(f"{item.kind}/{item.name}" for item in preferred))
        raise ValueError(f"multiple preferred workloads found ({choices}); pass --workload-name")
    return preferred[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_root", type=Path)
    parser.add_argument("--workload-name")
    args = parser.parse_args()
    try:
        workload = discover_workload(args.application_root, args.workload_name)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        json.dumps(
            {
                "kind": workload.kind,
                "name": workload.name,
                "namespace": workload.namespace,
                "path": str(workload.path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
