from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_blind_quality_packet.py"
SPEC = importlib.util.spec_from_file_location("build_blind_quality_packet", SCRIPT)
packet_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet_builder)


def _private_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_packet_hides_the_baseline_candidate_mapping_from_the_grader(tmp_path):
    snapshot = _private_file(tmp_path / "snapshot.json", '{"scene_ids":["s1"]}\n')
    before = _private_file(tmp_path / "before.md", "report one")
    after = _private_file(tmp_path / "after.md", "report two")
    claims = _private_file(
        tmp_path / "claims.json",
        '[{"id":"goal","claim":"The task goal is recoverable."},'
        '{"id":"result","claim":"The verified result is stated."}]\n',
    )
    packet = tmp_path / "quality" / "blind-input.json"
    label_map = tmp_path / "quality" / "label-map.json"

    packet_builder.build_packet(
        snapshot,
        before,
        after,
        claims,
        packet,
        label_map,
        baseline_label="B",
    )

    grader_input = json.loads(packet.read_text(encoding="utf-8"))
    mapping = json.loads(label_map.read_text(encoding="utf-8"))
    assert set(grader_input["reports"]) == {"A", "B"}
    assert grader_input["reports"]["B"]["content"] == "report one"
    assert "baseline_label" not in grader_input
    assert "candidate_label" not in grader_input
    assert mapping["baseline_label"] == "B"
    assert mapping["candidate_label"] == "A"
    assert grader_input["required_claims"][0]["claim"] == (
        "The task goal is recoverable."
    )
    assert "scoring_anchors" in grader_input["rubric"]
    assert packet.stat().st_mode & 0o777 == 0o600
    assert label_map.stat().st_mode & 0o777 == 0o600


def test_packet_rejects_a_symlinked_report(tmp_path):
    snapshot = _private_file(tmp_path / "snapshot.json", "{}")
    target = _private_file(tmp_path / "target.md", "report")
    baseline = tmp_path / "baseline.md"
    baseline.symlink_to(target)
    candidate = _private_file(tmp_path / "candidate.md", "report")
    claims = _private_file(
        tmp_path / "claims.json",
        '[{"id":"goal","claim":"The goal is recoverable."}]',
    )

    with pytest.raises(ValueError, match="symlink"):
        packet_builder.build_packet(
            snapshot,
            baseline,
            candidate,
            claims,
            tmp_path / "packet.json",
            tmp_path / "map.json",
        )


def test_packet_rejects_a_group_readable_input(tmp_path):
    snapshot = _private_file(tmp_path / "snapshot.json", "{}")
    baseline = _private_file(tmp_path / "baseline.md", "report")
    baseline.chmod(0o640)
    candidate = _private_file(tmp_path / "candidate.md", "report")
    claims = _private_file(
        tmp_path / "claims.json",
        '[{"id":"goal","claim":"The goal is recoverable."}]',
    )

    with pytest.raises(ValueError, match="must be private"):
        packet_builder.build_packet(
            snapshot,
            baseline,
            candidate,
            claims,
            tmp_path / "packet.json",
            tmp_path / "map.json",
        )


def test_packet_rejects_an_intermediate_symlink_input(tmp_path):
    snapshot = _private_file(tmp_path / "snapshot.json", "{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _private_file(outside / "secret.md", "outside report")
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    candidate = _private_file(tmp_path / "candidate.md", "report")
    claims = _private_file(
        tmp_path / "claims.json",
        '[{"id":"goal","claim":"The goal is recoverable."}]',
    )

    with pytest.raises(ValueError, match="symlink component"):
        packet_builder.build_packet(
            snapshot,
            linked / secret.name,
            candidate,
            claims,
            tmp_path / "packet.json",
            tmp_path / "map.json",
        )
