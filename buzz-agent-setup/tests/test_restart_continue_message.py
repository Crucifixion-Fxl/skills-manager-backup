"""Post-restart Buzz continuation message contract (L1-ARC-001)."""

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "references" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from restart_continue_message import build_continue_args  # noqa: E402


class ContinueMessageTests(unittest.TestCase):
    def test_visible_mention_and_real_tag_target_same_agent(self):
        pubkey = "a" * 64
        root = "b" * 64
        args = build_continue_args(
            channel="613a9560-d423-4d14-ab3d-f4fc29aecb7a",
            root=root,
            agent_name="l4-restart-probe",
            agent_pubkey=pubkey,
        )
        self.assertEqual(args.count("--mention"), 1)
        self.assertEqual(args[args.index("--mention") + 1], pubkey)
        self.assertEqual(args[args.index("--reply-to") + 1], root)
        self.assertEqual(args[args.index("--content") + 1], "@l4-restart-probe continue")

    def test_rejects_unknown_thread_or_untrusted_agent_name(self):
        base = dict(
            channel="613a9560-d423-4d14-ab3d-f4fc29aecb7a",
            root="b" * 64,
            agent_name="l4-restart-probe",
            agent_pubkey="a" * 64,
        )
        for change in ({"root": ""}, {"agent_name": "agent\n--mention victim"}, {"agent_pubkey": "bad"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_continue_args(**(base | change))


if __name__ == "__main__":
    unittest.main()
