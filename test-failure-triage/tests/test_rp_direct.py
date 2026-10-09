import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import rp_direct as R


class Fake:
    def get(self, path, params):
        if path.endswith("launch"):
            items = [{"id": 1, "uuid": "launch-uuid"}]
        elif path.endswith("item"):
            items = [{"id": 2, "uuid": "scenario-uuid", "path": "2", "status": "FAILED"},
                     {"id": 3, "uuid": "child", "path": "2.3"},
                     {"id": 4, "uuid": "unrelated", "path": "4"}]
        else:
            item = params["filter.eq.item"]
            items = [{"id": item * 10, "time": "2026-10-01T00:00:00Z", "message": "DEV_DIAG_EVENT {}",
                      "binaryContent": {"id": "file-1", "contentType": "image/png"}}]
        return {"content": items, "page": {"totalElements": len(items)}}


def bundle(uuid="scenario-uuid"):
    return {"coverage": {"logs_truncated": False}, "incidents": [{"job": {"reportPortalUuid": "launch-uuid"},
        "scenario": {"reportPortalUuid": uuid}, "logs": [], "missing_evidence": []}]}


class DirectTests(unittest.TestCase):
    def test_exact_uuid_mapping_and_descendant_logs(self):
        result = R.augment(bundle(), Fake())
        incident = result["incidents"][0]
        self.assertEqual([20, 30], [x["id"] for x in incident["logs"]])
        self.assertTrue(incident["direct_rp_observation"]["logs_complete"])
        self.assertEqual(2, incident["direct_rp_observation"]["raw_timeline_events"])
        self.assertIsNone(incident.get("timeline"))
        self.assertTrue(all(a["source"] == "rp_read_proxy" for a in incident["evidence"]["attachments"]))

    def test_missing_uuid_never_uses_names_or_another_item(self):
        incident = R.augment(bundle("unmapped"), Fake())["incidents"][0]
        self.assertEqual([], incident["logs"])
        self.assertTrue(incident["missing_evidence"])

    def test_repeat_pagination_is_partial(self):
        class Repeating(Fake):
            def get(self, path, params):
                result = super().get(path, params)
                result["page"]["totalElements"] = 999
                return result
        _, complete = R.pages(Repeating(), "item", {"filter.eq.launchId": 1})
        self.assertFalse(complete)

    def test_incident_budget_keeps_failures_but_declares_sampling(self):
        data = bundle()
        data["incidents"].append(bundle()["incidents"][0])
        result = R.augment(data, Fake(), max_incidents=1)
        self.assertEqual(2, len(result["incidents"]))
        self.assertTrue(result["coverage"]["sampled"])
        self.assertEqual(1, result["coverage"]["diagnostic_incidents_obtained"])
        self.assertIn("budget exhausted", result["incidents"][1]["missing_evidence"][0])


if __name__ == "__main__":
    unittest.main()
