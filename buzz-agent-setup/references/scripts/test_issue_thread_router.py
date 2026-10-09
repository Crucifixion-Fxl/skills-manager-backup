#!/usr/bin/env python3
"""Offline behavioral checks for issue_thread_router.py."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = spec_from_file_location("issue_thread_router", HERE / "issue_thread_router.py")
assert SPEC and SPEC.loader
ROUTER = module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTER)


class IssueThreadRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((HERE / "issue-thread-router.example.json").read_text())
        ROUTER.validate_config(cls.config)

    def target(self, issue_type: str | None, status: str | None, state: str = "opened") -> str:
        return ROUTER.resolve_target(
            self.config,
            {
                "type": issue_type,
                "status": status,
                "state": state,
                "labels_valid": issue_type is not None and status is not None,
            },
        )

    def test_route_matrix(self) -> None:
        self.assertEqual(self.target("feature", "triage"), "feature")
        self.assertEqual(self.target("bug", "backlog"), "bug")
        self.assertEqual(self.target("maintenance", "ready"), "dev")
        self.assertEqual(self.target("feature", "in-review"), "qa")
        self.assertEqual(self.target("operation", "ready"), "sre")

    def test_missing_or_ambiguous_labels_fall_back_to_desk(self) -> None:
        self.assertEqual(self.target(None, "triage"), "desk")
        self.assertEqual(self.target("feature", None), "desk")
        self.assertEqual(self.target("unknown", "ready"), "desk")

    def test_closed_always_returns_to_desk(self) -> None:
        self.assertEqual(self.target("feature", "in-review", "closed"), "desk")

    def test_same_target_does_not_remention(self) -> None:
        previous = {"target": "dev"}
        current = {"type": "feature", "status": "in-progress", "state": "opened"}
        self.assertFalse(ROUTER.route_changed(previous, current, "dev"))
        self.assertTrue(ROUTER.route_changed(previous, current, "qa"))

    def test_binding_marker_round_trip(self) -> None:
        channel = "11111111-1111-1111-1111-111111111111"
        root = "a" * 64
        notes = [{"body": ROUTER.binding_marker(channel, root)}]
        self.assertEqual(ROUTER.parse_binding(notes, channel), root)

    def test_atomic_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            ROUTER.atomic_write_json(path, {"cursor": "x"})
            self.assertEqual(json.loads(path.read_text()), {"cursor": "x"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
