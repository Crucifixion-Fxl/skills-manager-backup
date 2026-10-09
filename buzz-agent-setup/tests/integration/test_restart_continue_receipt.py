"""L4-ARC-001: black-box oracle for restart, reaction, and recovery.

Set BUZZ_RESTART_CONTINUE_RECEIPT to a JSON file collected from a real Buzz
canary. The source events and the local checkpoint must be read back after the
run; this oracle does not manufacture them.
"""

import json
import os
import unittest
from pathlib import Path


def _tags(event, key):
    return [tag[1] for tag in event["tags"] if len(tag) > 1 and tag[0] == key]


def assert_restart_continue_receipt(receipt):
    agent = receipt["agent_pubkey"]
    root = receipt["root"]
    continuation = receipt["continue_event"]
    reactions_while_running = receipt["reactions_while_running"]["reactions"]
    completion = receipt["completion_event"]

    assert receipt["old_pid"] != receipt["new_pid"], "runtime did not restart"
    assert receipt["pre_restart_agent_mentions"] == 1, "extra pre-restart wakeup"
    assert receipt["notice_event"]["created_at"] < receipt["ready_at"]
    assert receipt["ready_at"] <= continuation["created_at"]
    assert continuation["content"] == f'@{receipt["agent_name"]} continue', "mention not visible in body"
    assert _tags(continuation, "p") == [agent], "real Agent p tag missing or duplicated"
    assert _tags(continuation, "e") == [root], "continue not in original Thread"
    assert _tags(continuation, "h") == [receipt["channel"]]
    assert receipt["reaction_target_event_id"] == continuation["id"], "reaction is not on continue"
    assert any(
        reaction["emoji"] == "👀" and agent in reaction["pubkeys"]
        for reaction in reactions_while_running
    ), "Agent acknowledgement reaction absent while processing continue"
    assert receipt["checkpoint_before_stop_lines"] == ["A"]
    assert receipt["checkpoint_after_restart_before_continue_lines"] == ["A"]
    assert receipt["checkpoint_while_running_lines"] == ["A"]
    assert completion["pubkey"] == agent
    assert completion["content"] == "DONE"
    assert _tags(completion, "e") == [root], "completion not in original Thread"
    assert completion["created_at"] - continuation["created_at"] >= 90, "recovery skipped required wait"
    assert receipt["checkpoint_lines"] == ["A", "B"], "task did not resume exactly once"


class RestartContinueReceiptTests(unittest.TestCase):
    def test_rejects_old_p_tag_only_continue(self):
        receipt = {
            "agent_name": "probe", "agent_pubkey": "a" * 64,
            "channel": "613a9560-d423-4d14-ab3d-f4fc29aecb7a", "root": "b" * 64,
            "old_pid": 1, "new_pid": 2, "pre_restart_agent_mentions": 1,
            "notice_event": {"created_at": 1}, "ready_at": 2,
            "continue_event": {"id": "c" * 64, "created_at": 3, "content": "continue", "tags": [["h", "613a9560-d423-4d14-ab3d-f4fc29aecb7a"], ["e", "b" * 64], ["p", "a" * 64]]},
            "reaction_target_event_id": "c" * 64,
            "reactions_while_running": {"reactions": [{"emoji": "👀", "pubkeys": ["a" * 64]}]},
            "completion_event": {"pubkey": "a" * 64, "content": "DONE", "created_at": 95, "tags": [["e", "b" * 64]]},
            "checkpoint_before_stop_lines": ["A"],
            "checkpoint_after_restart_before_continue_lines": ["A"],
            "checkpoint_while_running_lines": ["A"],
            "checkpoint_lines": ["A", "B"],
        }
        with self.assertRaisesRegex(AssertionError, "mention not visible"):
            assert_restart_continue_receipt(receipt)

    def test_rejects_no_reaction_or_duplicate_checkpoint(self):
        receipt = json.loads((Path(__file__).parent / "../fixtures/restart_continue_live_20260924.json").resolve().read_text())
        without_reaction = receipt | {"reactions_while_running": {"reactions": []}}
        with self.assertRaisesRegex(AssertionError, "reaction absent"):
            assert_restart_continue_receipt(without_reaction)
        duplicate = receipt | {"checkpoint_lines": ["A", "A", "B"]}
        with self.assertRaisesRegex(AssertionError, "exactly once"):
            assert_restart_continue_receipt(duplicate)

    def test_historical_canary_receipt(self):
        path = Path(__file__).parent / "../fixtures/restart_continue_live_20260924.json"
        assert_restart_continue_receipt(json.loads(path.resolve().read_text()))

    @unittest.skipUnless(os.getenv("BUZZ_RESTART_CONTINUE_RECEIPT"), "real Buzz receipt not supplied")
    def test_real_restart_reaction_and_recovery(self):
        path = Path(os.environ["BUZZ_RESTART_CONTINUE_RECEIPT"])
        assert_restart_continue_receipt(json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
