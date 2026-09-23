"""Contract for the real Naturehood Agent Issue-lifecycle L4 receipt."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


RECEIPT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "naturehood-agent-issue-lifecycle-live-receipt-20260915.json"
)


class NaturehoodAgentIssueLiveReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    def test_receipt_is_a_non_secret_snapshot_bound_to_revision(self) -> None:
        self.assertEqual(1, self.receipt["schema_version"])
        self.assertEqual("naturehood_agent_issue_lifecycle_l4", self.receipt["receipt_kind"])
        self.assertTrue(self.receipt["snapshot_only"])
        self.assertFalse(self.receipt["contains_secret"])
        self.assertRegex(self.receipt["recorded_at_utc"], r"^2026-09-15T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(
            "24165734c0f671fa17fa376e12c3ce2e2db160a6",
            self.receipt["method"]["plugin_revision"],
        )
        serialized = json.dumps(self.receipt, ensure_ascii=False).lower()
        for forbidden in ("private-token", "nsec1", "gitlab_token", "superset_password"):
            self.assertNotIn(forbidden, serialized)

        def assert_no_secret_keys(value: object, path: str = "receipt") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key != "contains_secret":
                        self.assertIsNone(
                            re.search(
                                r"(?:^|_)(?:token|password|secret|private_key)(?:$|_)",
                                key.lower(),
                            ),
                            f"secret-shaped key in receipt: {path}.{key}",
                        )
                    assert_no_secret_keys(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    assert_no_secret_keys(child, f"{path}[{index}]")

        assert_no_secret_keys(self.receipt)

    def test_all_registered_agents_proved_full_issue_lifecycle(self) -> None:
        agents = self.receipt["agents"]
        self.assertEqual({"nh-feature", "nh-dev", "nh-bi"}, set(agents))
        expected = {
            "nh-feature": {"access_level": 20, "issue_iid": 253, "branch": 403},
            "nh-dev": {"access_level": 30, "issue_iid": 254, "branch": 201},
            "nh-bi": {"access_level": 20, "issue_iid": 255, "branch": 403},
        }
        for name, values in expected.items():
            agent = agents[name]
            self.assertEqual(values["access_level"], agent["access_level"])
            self.assertEqual(values["issue_iid"], agent["issue_iid"])
            self.assertEqual("closed", agent["final_state"])
            self.assertTrue(agent["self_assigned"])
            self.assertRegex(str(agent["note_id"]), r"^\d+$")
            self.assertEqual(
                {
                    "create": 201,
                    "comment": 201,
                    "update": 200,
                    "close": 200,
                    "reopen": 200,
                    "final_close": 200,
                    "readback": 200,
                },
                agent["statuses"],
            )
            self.assertEqual(values["branch"], agent["branch_create_status"])
            if name == "nh-dev":
                self.assertEqual(204, agent["branch_cleanup_status"])
            else:
                self.assertIsNone(agent["branch_cleanup_status"])
            self.assertTrue(
                re.fullmatch(
                    rf"https://gitlab\.addx\.ai/applications/naturehood/-/issues/{values['issue_iid']}",
                    agent["issue_url"],
                )
            )

    def test_runtime_receipt_matches_the_validated_agent_shape(self) -> None:
        expected = {
            "nh-feature": {"workers": 4, "prompt_sha256": "baeb55a87cfba5a14cdae3ce5e2de3462cbfc74ff77053c1eb3f8a86c5a5f3b4"},
            "nh-dev": {"workers": 2, "prompt_sha256": "50f1cb8134f7d667ab77797aa002e77ebaa4362ed9179fb3a4be552ff80a0068"},
            "nh-bi": {"workers": 1, "prompt_sha256": "d14fbe5b68773282eb17bd069272fea1961d1d054535c93cb35ec27db0f09d9e"},
        }
        runtime = self.receipt["runtime"]
        self.assertEqual("40ad7888-23a7-452c-96b5-8e062d82a06c", runtime["channel_id"])
        self.assertEqual("185a74f4609f99ca803d72eb4d0b2cfd525187ded88872140e38c5207076997f", runtime["launcher_sha256"])
        for name, values in expected.items():
            agent = runtime["agents"][name]
            self.assertTrue(agent["active"])
            self.assertGreater(agent["main_pid"], 0)
            self.assertEqual(values["workers"], agent["workers"])
            self.assertEqual(values["prompt_sha256"], agent["prompt_sha256"])
            self.assertEqual("0600", agent["env_mode"])
            self.assertEqual("0600", agent["prompt_mode"])
            self.assertTrue(agent["process_started_after_prompt"])
            self.assertTrue(agent["declared_env_only"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
