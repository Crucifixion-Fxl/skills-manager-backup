#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Classify target receipts and decide a deterministic GitLab note upsert.

The target classifier deliberately treats narrative links as candidates only.
It consumes receipts produced by trusted adapters; it does not verify event
signatures or protected-branch provenance itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


AUTHORITATIVE_EVIDENCE = frozenset(
    {
        "trusted_event_issue",
        "protected_repo_mapping",
    }
)
KNOWN_EVIDENCE = AUTHORITATIVE_EVIDENCE | frozenset(
    {
        "gitlab_closes",
        "gitlab_related",
        "canonical_key",
        "narrative_mention",
    }
)
EXACT_MARKER = "<!-- data-review-evidence-index:v1 -->"


class ResolutionError(ValueError):
    """The supplied target evidence does not match the resolver contract."""


def _candidate(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ResolutionError("each candidate must be an object")

    project = raw.get("project")
    iid = raw.get("iid")
    evidence = raw.get("evidence")
    if not isinstance(project, str) or not project.strip():
        raise ResolutionError("candidate.project must be a non-empty string")
    if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
        raise ResolutionError("candidate.iid must be a positive integer")
    if not isinstance(evidence, list):
        raise ResolutionError("candidate.evidence must be a list")

    normalized_evidence: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ResolutionError("each evidence item must be an object")
        kind = item.get("kind")
        ref = item.get("ref")
        if kind not in KNOWN_EVIDENCE:
            raise ResolutionError(f"unsupported evidence kind: {kind!r}")
        if not isinstance(ref, str) or not ref.strip():
            raise ResolutionError("evidence.ref must be a non-empty string")
        normalized_evidence.append({"kind": kind, "ref": ref.strip()})

    return {
        "project": project.strip(),
        "iid": iid,
        "evidence": normalized_evidence,
    }


def resolve(payload: object) -> dict[str, Any]:
    """Return a deterministic write-path decision for one analysis unit."""
    if not isinstance(payload, dict):
        raise ResolutionError("input must be an object")

    allowed_project = payload.get("allowed_project")
    if not isinstance(allowed_project, str) or not allowed_project.strip():
        raise ResolutionError("allowed_project must be a non-empty string")
    allowed_project = allowed_project.strip()

    raw_destinations = payload.get("allowed_destination_project_ids")
    if not isinstance(raw_destinations, list) or not raw_destinations:
        raise ResolutionError(
            "allowed_destination_project_ids must be a non-empty list"
        )
    allowed_destinations: list[str] = []
    for destination in raw_destinations:
        if not isinstance(destination, str) or not destination.strip():
            raise ResolutionError(
                "each allowed destination project id must be a non-empty string"
            )
        normalized = destination.strip()
        if normalized not in allowed_destinations:
            allowed_destinations.append(normalized)
    destination_allowed = allowed_project in allowed_destinations

    readonly = payload.get("readonly", False)
    if not isinstance(readonly, bool):
        raise ResolutionError("readonly must be a boolean")

    raw_candidates = payload.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raise ResolutionError("candidates must be a list")

    authoritative: dict[tuple[str, int], dict[str, Any]] = {}
    untrusted: dict[tuple[str, int], dict[str, Any]] = {}
    rejected_scope: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in raw_candidates:
        item = _candidate(raw)
        key = (item["project"], item["iid"])
        if item["project"] != allowed_project:
            rejected_scope[key] = item
            continue
        kinds = {entry["kind"] for entry in item["evidence"]}
        if kinds & AUTHORITATIVE_EVIDENCE:
            authoritative[key] = item
            untrusted.pop(key, None)
        elif key not in authoritative:
            untrusted[key] = item

    targets = sorted(authoritative.values(), key=lambda item: item["iid"])
    common: dict[str, Any] = {
        "allowed_project": allowed_project,
        "allowed_destination_project_ids": allowed_destinations,
        "destination_allowed": destination_allowed,
        "targets": targets,
        "untrusted_candidates": sorted(
            untrusted.values(), key=lambda item: item["iid"]
        ),
        "rejected_scope": sorted(
            rejected_scope.values(), key=lambda item: (item["project"], item["iid"])
        ),
    }

    if readonly:
        return {**common, "status": "readonly", "write_count": 0}
    if not destination_allowed:
        return {**common, "status": "skipped-no-target", "write_count": 0}
    if not targets:
        return {**common, "status": "skipped-no-target", "write_count": 0}
    if len(targets) > 1:
        return {**common, "status": "skipped-ambiguous", "write_count": 0}
    return {
        **common,
        "status": "ready",
        "write_count": 1,
        "target": targets[0],
    }


def _normalized_note_body(body: str) -> str:
    """Normalize content while ignoring explicitly volatile run timestamps."""
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    stable_lines = []
    for line in normalized.split("\n"):
        stripped = line.rstrip()
        if stripped.startswith(("本次运行时间：", "生成时间：")):
            continue
        stable_lines.append(stripped)
    return "\n".join(stable_lines).strip()


def decide_upsert(payload: object) -> dict[str, Any]:
    """Return create/update/no-op/conflict without performing a GitLab write."""
    if not isinstance(payload, dict):
        raise ResolutionError("input must be an object")

    single_writer = payload.get("single_writer")
    if not isinstance(single_writer, bool):
        raise ResolutionError("single_writer must be a boolean")
    if not single_writer:
        return {
            "status": "conflict",
            "reason": "single-writer-required",
            "write_count": 0,
        }

    writer_author_id = payload.get("writer_author_id")
    if isinstance(writer_author_id, bool) or not isinstance(
        writer_author_id, (int, str)
    ):
        raise ResolutionError("writer_author_id must be an integer or string")
    if isinstance(writer_author_id, int) and writer_author_id <= 0:
        raise ResolutionError("writer_author_id must be positive")
    if isinstance(writer_author_id, str) and not writer_author_id.strip():
        raise ResolutionError("writer_author_id must be non-empty")

    desired_body = payload.get("desired_body")
    if not isinstance(desired_body, str):
        raise ResolutionError("desired_body must be a string")
    if not desired_body.replace("\r\n", "\n").startswith(f"{EXACT_MARKER}\n"):
        raise ResolutionError("desired_body must start with the exact marker")

    raw_notes = payload.get("notes")
    if not isinstance(raw_notes, list):
        raise ResolutionError("notes must be a list")

    marked_notes: list[dict[str, Any]] = []
    for raw in raw_notes:
        if not isinstance(raw, dict):
            raise ResolutionError("each note must be an object")
        note_id = raw.get("id")
        author_id = raw.get("author_id")
        editable = raw.get("editable")
        body = raw.get("body")
        if isinstance(note_id, bool) or not isinstance(note_id, int) or note_id <= 0:
            raise ResolutionError("note.id must be a positive integer")
        if isinstance(author_id, bool) or not isinstance(author_id, (int, str)):
            raise ResolutionError("note.author_id must be an integer or string")
        if not isinstance(editable, bool):
            raise ResolutionError("note.editable must be a boolean")
        if not isinstance(body, str):
            raise ResolutionError("note.body must be a string")
        first_line = body.replace("\r\n", "\n").split("\n", 1)[0]
        if first_line == EXACT_MARKER:
            marked_notes.append(
                {
                    "id": note_id,
                    "author_id": author_id,
                    "editable": editable,
                    "body": body,
                }
            )

    if len(marked_notes) > 1:
        return {
            "status": "conflict",
            "reason": "duplicate-marker",
            "write_count": 0,
            "note_ids": sorted(note["id"] for note in marked_notes),
        }
    if not marked_notes:
        return {"status": "create", "write_count": 1}

    existing = marked_notes[0]
    if existing["author_id"] != writer_author_id:
        return {
            "status": "conflict",
            "reason": "marker-not-owned",
            "write_count": 0,
            "note_id": existing["id"],
        }
    if not existing["editable"]:
        return {
            "status": "conflict",
            "reason": "marker-not-editable",
            "write_count": 0,
            "note_id": existing["id"],
        }
    if _normalized_note_body(existing["body"]) == _normalized_note_body(desired_body):
        return {"status": "no-op", "write_count": 0, "note_id": existing["id"]}
    return {"status": "update", "write_count": 1, "note_id": existing["id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        choices=("resolve", "upsert"),
        default="resolve",
        help="decision to run (default: resolve)",
    )
    parser.add_argument("input", nargs="?", help="JSON file; omit to read stdin")
    args = parser.parse_args(argv)
    try:
        if args.input:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            payload = json.load(sys.stdin)
        decision = resolve(payload) if args.operation == "resolve" else decide_upsert(payload)
        print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    except (OSError, json.JSONDecodeError, ResolutionError) as exc:
        print(json.dumps({"status": "invalid-input", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
