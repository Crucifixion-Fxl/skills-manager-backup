#!/usr/bin/env python3
"""Build a blinded A/B grader packet from two sanitized reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
QUALITY_POLICY = SKILL_ROOT / "policies" / "report-quality-v1.json"
QUALITY_POLICY_SOURCES = (QUALITY_POLICY, SKILL_ROOT / "evals" / "evals.json")
RUBRIC_VERSION = "work-method-quality-v1"
MAX_INPUT_BYTES = 5 * 1024 * 1024


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_private(path: Path, label: str) -> bytes:
    _require(path.exists() and not path.is_symlink(), f"{label} must be a regular non-symlink file")
    metadata = path.stat()
    _require(stat.S_ISREG(metadata.st_mode), f"{label} must be a regular file")
    _require(metadata.st_uid == os.getuid(), f"{label} must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, f"{label} must be private")
    _require(metadata.st_size <= MAX_INPUT_BYTES, f"{label} exceeds the 5 MiB limit")
    return path.read_bytes()


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _quality_policy_hash() -> str:
    digest = hashlib.sha256()
    for path in QUALITY_POLICY_SOURCES:
        digest.update(path.relative_to(SKILL_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


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
            raise ValueError(f"blind quality path contains a symlink component: {cursor}")


def _write_private_json(path: Path, document: dict[str, Any], *, controlled_root: Path) -> None:
    _reject_symlink_components(path)
    absolute = Path(os.path.abspath(path))
    _require(
        absolute.parent == controlled_root or controlled_root in absolute.parents,
        f"output is outside the controlled round root: {path}",
    )
    _require(not path.exists() and not path.is_symlink(), f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    serialized = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def build_packet(
    snapshot_path: Path,
    baseline_report_path: Path,
    candidate_report_path: Path,
    required_claims_path: Path,
    packet_path: Path,
    label_map_path: Path,
    *,
    baseline_label: str | None = None,
) -> None:
    controlled_root = Path(os.path.abspath(snapshot_path)).parent
    _reject_symlink_components(controlled_root)
    for path, label in (
        (snapshot_path, "frozen snapshot"),
        (baseline_report_path, "baseline report"),
        (candidate_report_path, "candidate report"),
        (required_claims_path, "required claim ledger"),
        (packet_path, "packet output"),
        (label_map_path, "label map output"),
    ):
        _reject_symlink_components(path)
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve()
        _require(
            (
                absolute.parent == controlled_root
                or controlled_root in absolute.parents
            )
            and (
                resolved.parent == controlled_root
                or controlled_root in resolved.parents
            ),
            f"{label} is outside the controlled round root",
        )
    snapshot = _read_private(snapshot_path, "frozen snapshot")
    baseline = _read_private(baseline_report_path, "baseline report")
    candidate = _read_private(candidate_report_path, "candidate report")
    claims_payload = _read_private(required_claims_path, "required claim ledger")
    try:
        claims_document = json.loads(claims_payload)
    except json.JSONDecodeError as error:
        raise ValueError("required claim ledger must be valid JSON") from error
    _require(isinstance(claims_document, list) and claims_document, "required claim ledger must be a nonempty array")
    claims: list[dict[str, str]] = []
    for claim in claims_document:
        _require(isinstance(claim, dict), "required claims must be objects")
        claim_id = claim.get("id")
        claim_text = claim.get("claim")
        _require(isinstance(claim_id, str) and claim_id.strip(), "required claim id must be nonempty")
        _require(isinstance(claim_text, str) and claim_text.strip(), "required claim text must be nonempty")
        claims.append({"id": claim_id.strip(), "claim": claim_text.strip()})
    _require(len(claims) == len({claim["id"] for claim in claims}), "required claim IDs must be unique")

    chosen = baseline_label or ("A" if secrets.randbits(1) else "B")
    _require(chosen in {"A", "B"}, "baseline label must be A or B")
    candidate_label = "B" if chosen == "A" else "A"
    report_payloads = {
        chosen: {"report_digest": _digest(baseline), "content": baseline.decode("utf-8")},
        candidate_label: {
            "report_digest": _digest(candidate),
            "content": candidate.decode("utf-8"),
        },
    }
    policy = json.loads(QUALITY_POLICY.read_text(encoding="utf-8"))
    packet = {
        "schema": "addx.work_method_blind_quality_input.v1",
        "snapshot_digest": _digest(snapshot),
        "quality_policy_hash": _quality_policy_hash(),
        "rubric_version": RUBRIC_VERSION,
        "judge_input": "reports_claim_ledger_rubric_only",
        "required_claims": claims,
        "rubric": policy,
        "reports": report_payloads,
    }
    packet_bytes = (
        json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    label_map = {
        "schema": "addx.work_method_blind_label_map.v1",
        "snapshot_digest": _digest(snapshot),
        "packet_digest": _digest(packet_bytes),
        "baseline_label": chosen,
        "candidate_label": candidate_label,
        "baseline_report_digest": _digest(baseline),
        "candidate_report_digest": _digest(candidate),
    }
    _write_private_json(packet_path, packet, controlled_root=controlled_root)
    _write_private_json(label_map_path, label_map, controlled_root=controlled_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--baseline-report", required=True, type=Path)
    parser.add_argument("--candidate-report", required=True, type=Path)
    parser.add_argument("--required-claims", required=True, type=Path)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--label-map", required=True, type=Path)
    args = parser.parse_args()
    try:
        build_packet(
            args.snapshot,
            args.baseline_report,
            args.candidate_report,
            args.required_claims,
            args.packet,
            args.label_map,
        )
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 1
    print(json.dumps({"status": "written", "packet": str(args.packet), "label_map": str(args.label_map)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
