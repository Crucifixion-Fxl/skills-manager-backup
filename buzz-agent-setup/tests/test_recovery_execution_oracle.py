"""Oracle controls only; actual native behavior is checked separately in L3."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("recovery_wire_oracle", Path(__file__).parent / "localstack/recovery_wire_oracle.py")
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


class ExecutionOracleTests(unittest.TestCase):
    def test_positive_missing_and_wrong_scopes_at_actual_capture_reader(self):
        expected = dict(event_id="1" * 64, actor="2" * 64, owner="3" * 64, channel="channel",
                        root="4" * 64, slot="selected", other_slot="unselected", agent_name="test-agent")
        row = dict(work_id=expected["event_id"], signed_author=expected["actor"],
                   effective_requester=expected["actor"], channel="channel", thread_root=expected["root"], mode="resume")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acp.jsonl"
            self.assertIsNone(ORACLE.observe(path, **expected))
            for mutation in ("none", "other-source", "owner-from", "owner-body", "manifest", "requester", "work-id"):
                item = dict(row)
                if mutation == "requester": item["effective_requester"] = expected["owner"]
                if mutation == "work-id": item["work_id"] = "9" * 64
                manifest = "" if mutation == "manifest" else "<recovery-scope>\nInstructions\n" + json.dumps([item]) + "\n</recovery-scope>"
                text = manifest + "\nThread root: " + expected["root"] + "\nFrom hex: " + expected["actor"] + "\nContent: RECOVERY-L3:selected"
                if mutation == "other-source": text += "\nRECOVERY-L3:unselected"
                if mutation == "owner-from": text += "\nFrom hex: " + expected["owner"]
                if mutation in ("owner-body", "manifest"): text += "\nContent: @test-agent continue"
                target = path.with_name(mutation + ".jsonl")
                ORACLE.capture(target, {"params": {"sessionId": "fresh", "prompt": [{"text": text}]}})
                with self.subTest(mutation=mutation):
                    if mutation == "none":
                        self.assertTrue(ORACLE.observe(target, **expected)["other_source_absent"])
                    else:
                        with self.assertRaises(AssertionError): ORACLE.observe(target, **expected)


if __name__ == "__main__":
    unittest.main()
