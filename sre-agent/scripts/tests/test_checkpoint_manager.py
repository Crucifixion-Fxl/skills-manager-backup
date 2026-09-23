#!/usr/bin/env python3
"""Tests for CheckpointManager."""

import json
import os
import time
from datetime import datetime, timezone

import pytest

from scripts.checkpoint_manager import CheckpointManager


class TestWriteCheckpoint:
    def test_write_creates_file(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        data = {"stage": "INVESTIGATE_ROUND_1", "self_check": {"readonly_confirmed": True}}
        path = cm.write("investigate_round_1", data)
        assert os.path.isfile(path)
        assert path.endswith("investigate_round_1.json")

    def test_write_adds_timestamp(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step1", {"stage": "S1"})
        with open(tmp_path / "progress" / "step1.json") as f:
            content = json.load(f)
        assert "timestamp" in content
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(content["timestamp"])

    def test_write_creates_directory(self, tmp_path):
        progress_dir = str(tmp_path / "nested" / "progress")
        cm = CheckpointManager(progress_dir)
        cm.write("init", {"stage": "INIT"})
        assert os.path.isdir(progress_dir)

    def test_write_preserves_data(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step1", {
            "stage": "INVESTIGATE",
            "self_check": {"role": "investigation", "readonly_confirmed": True},
            "next_stage": "BUILD_CHAIN",
            "data": {"findings": ["high cpu"]},
        })
        with open(tmp_path / "progress" / "step1.json") as f:
            content = json.load(f)
        assert content["stage"] == "INVESTIGATE"
        assert content["self_check"]["role"] == "investigation"
        assert content["next_stage"] == "BUILD_CHAIN"
        assert content["data"]["findings"] == ["high cpu"]

    def test_write_overwrites_existing(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step1", {"stage": "V1"})
        cm.write("step1", {"stage": "V2"})
        with open(tmp_path / "progress" / "step1.json") as f:
            content = json.load(f)
        assert content["stage"] == "V2"


class TestWriteHeartbeat:
    def test_heartbeat_updates_timestamp(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step1", {"stage": "S1", "data": {"key": "value"}})
        with open(tmp_path / "progress" / "step1.json") as f:
            old_ts = json.load(f)["timestamp"]

        # Ensure time passes so timestamp differs
        time.sleep(0.05)
        cm.write_heartbeat("step1")

        with open(tmp_path / "progress" / "step1.json") as f:
            content = json.load(f)
        assert content["timestamp"] != old_ts
        # Data preserved
        assert content["stage"] == "S1"
        assert content["data"]["key"] == "value"

    def test_heartbeat_creates_minimal_for_nonexistent(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        path = cm.write_heartbeat("heartbeat_only")
        assert os.path.isfile(path)
        with open(path) as f:
            content = json.load(f)
        assert "timestamp" in content
        # Should have at least a timestamp, nothing else required
        datetime.fromisoformat(content["timestamp"])


class TestGetLatest:
    def test_empty_dir_returns_none(self, tmp_path):
        progress_dir = tmp_path / "progress"
        progress_dir.mkdir()
        cm = CheckpointManager(str(progress_dir))
        assert cm.get_latest() is None

    def test_nonexistent_dir_returns_none(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "does_not_exist"))
        assert cm.get_latest() is None

    def test_returns_latest_by_mtime(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step_a", {"stage": "A"})
        cm.write("step_b", {"stage": "B"})
        # Backdate step_b so step_a is newer
        old_time = time.time() - 100
        os.utime(tmp_path / "progress" / "step_b.json", (old_time, old_time))

        latest = cm.get_latest()
        assert latest is not None
        assert latest["name"] == "step_a"
        assert latest["stage"] == "A"

    def test_includes_mtime(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("step1", {"stage": "S1"})
        latest = cm.get_latest()
        assert "mtime" in latest
        assert isinstance(latest["mtime"], float)


class TestListAll:
    def test_returns_sorted_by_filename(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        # Write in non-alphabetical order
        cm.write("step_c", {"stage": "C"})
        cm.write("step_a", {"stage": "A"})
        cm.write("step_b", {"stage": "B"})

        result = cm.list_all()
        assert len(result) == 3
        assert result[0]["name"] == "step_a"
        assert result[1]["name"] == "step_b"
        assert result[2]["name"] == "step_c"

    def test_empty_dir_returns_empty_list(self, tmp_path):
        progress_dir = tmp_path / "progress"
        progress_dir.mkdir()
        cm = CheckpointManager(str(progress_dir))
        assert cm.list_all() == []

    def test_each_entry_has_name_and_mtime(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("only", {"stage": "X"})
        result = cm.list_all()
        assert len(result) == 1
        assert "name" in result[0]
        assert "mtime" in result[0]
        assert result[0]["stage"] == "X"


class TestCheckTimeout:
    def test_no_timeout_when_recent(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("recent", {"stage": "OK"})
        assert cm.is_timed_out(timeout_seconds=1800) is False

    def test_timeout_when_stale(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        cm.write("old", {"stage": "STALE"})
        # Backdate by 2 hours
        old_time = time.time() - 7200
        os.utime(tmp_path / "progress" / "old.json", (old_time, old_time))
        assert cm.is_timed_out(timeout_seconds=1800) is True

    def test_none_when_no_checkpoints(self, tmp_path):
        cm = CheckpointManager(str(tmp_path / "progress"))
        assert cm.is_timed_out() is None
