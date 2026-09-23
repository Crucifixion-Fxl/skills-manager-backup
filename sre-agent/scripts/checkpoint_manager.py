#!/usr/bin/env python3
"""
CheckpointManager — subagent progress tracking and crash recovery.

Each subagent (Investigation, Execution, Review) writes checkpoint files at
each stage so that:
1. Behavioral self-checks are recorded
2. Progress is preserved if the subagent crashes (API 500, timeout)
3. The dispatcher can detect crashes and resume from the last checkpoint
"""

import json
import os
import time
from datetime import datetime, timezone


class CheckpointManager:
    """Read/write JSON checkpoint files in a progress directory."""

    def __init__(self, progress_dir: str):
        """progress_dir: path to checkpoint directory, created on first write."""
        self.progress_dir = progress_dir

    def write(self, name: str, data: dict) -> str:
        """Write checkpoint file. Adds 'timestamp' automatically. Returns file path."""
        os.makedirs(self.progress_dir, exist_ok=True)
        payload = dict(data)
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        path = os.path.join(self.progress_dir, f"{name}.json")
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    def write_heartbeat(self, name: str) -> str:
        """Update timestamp of existing checkpoint (preserving data), or create minimal one."""
        path = os.path.join(self.progress_dir, f"{name}.json")
        if os.path.isfile(path):
            with open(path) as f:
                payload = json.load(f)
        else:
            payload = {}
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(self.progress_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    def get_latest(self) -> dict | None:
        """Return most recently modified checkpoint with 'name' and 'mtime' merged in, or None."""
        entries = self._load_all()
        if not entries:
            return None
        return max(entries, key=lambda e: e["mtime"])

    def list_all(self) -> list[dict]:
        """Return all checkpoints sorted by filename ascending, each with 'name' and 'mtime'."""
        entries = self._load_all()
        entries.sort(key=lambda e: e["name"])
        return entries

    def is_timed_out(self, timeout_seconds: int = 1800) -> bool | None:
        """True if latest checkpoint older than timeout_seconds. None if no checkpoints."""
        latest = self.get_latest()
        if latest is None:
            return None
        return (time.time() - latest["mtime"]) > timeout_seconds

    # ── internal ──────────────────────────────────────────────

    def _load_all(self) -> list[dict]:
        """Load all .json checkpoint files, enriching each with 'name' and 'mtime'."""
        if not os.path.isdir(self.progress_dir):
            return []
        results = []
        for fname in os.listdir(self.progress_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.progress_dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            data["name"] = fname.removesuffix(".json")
            data["mtime"] = os.path.getmtime(path)
            results.append(data)
        return results
