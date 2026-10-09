"""Validate imported legacy state before a durable adapter can consume it."""
import dataclasses
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import buzz_feishu_group_sync as gs


class StateDataContract(unittest.TestCase):
    def test_valid_state_is_parsed_without_filesystem_access(self):
        state = gs.State(binding="channel|chat", floor=123, buzz_since=125)
        state.b2f["a" * 64] = "om_existing"
        data = dataclasses.asdict(state)
        with mock.patch.object(gs, "_read_owner_only", side_effect=AssertionError("must not reopen a file")):
            parsed = gs.state_from_data(data)
        self.assertEqual(dataclasses.asdict(parsed), data)

    def test_corrupt_or_partial_state_never_becomes_empty(self):
        for data in ([], {}, {**dataclasses.asdict(gs.State()), "buzz_since": True},
                     {**dataclasses.asdict(gs.State()), "unexpected": "canary"}):
            with self.subTest(shape=type(data).__name__):
                with self.assertRaises(gs.GroupSyncError):
                    gs.state_from_data(data)

    def test_old_settled_ledger_migrates_using_existing_rules(self):
        data = dataclasses.asdict(gs.State(binding="channel|chat", buzz_since=125))
        for field in ("r2f", "react_since"):
            data.pop(field)
        parsed = gs.state_from_data(data)
        self.assertEqual(parsed.react_since, 125)
        self.assertEqual(parsed.r2f, {})
        self.assertNotIn("r2f", data)
