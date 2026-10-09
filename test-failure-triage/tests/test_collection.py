import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("triage_collect", Path(__file__).parents[1] / "scripts/collect_failures.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
START, END = C.timestamp("2026-09-28T00:00:00Z"), C.timestamp("2026-10-05T00:00:00Z")


def failed_job(id="1", **overrides):
    value = {"id": id, "status": "COMPLETE", "createdAt": "2026-09-01T00:00:00Z",
        "completedAt": "2026-10-01T00:00:00Z", "testPlanId": "9",
        "metadata": {"mode": "execution", "env": {"API_KEY": "private-key"}},
        "features": [{"id": "2", "name": "Test", "filename": "f.feature", "status": "failed",
            "scenarios": [{"id": "3", "name": "Scenario", "status": "FAILED", "metadata": {}}]}]}
    value.update(overrides)
    return value


class Fake:
    def __init__(self, pages, logs=None, missing_report=False):
        self.pages, self.logs, self.missing_report = pages, logs, missing_report
        self.calls = []

    def query(self, query, variables):
        self.calls.append((query, variables))
        if query == C.JOBS:
            return {"jobs": self.pages[variables["page"] - 1]}
        if query == C.PLAN:
            return {"testPlan": {"id": "9", "metadata": {"env": {"TOKEN": "do-not-leak"}}}}
        if query == C.REPORT:
            if self.missing_report:
                raise C.CollectionError("GraphQL query rejected")
            return {"jobDiagnosticReport": {"state": "AVAILABLE", "features": [
                {"scenarios": [{"id": "3", "reportPortalItemUuid": "rp-3"}]}]}}
        if query == C.LOGS:
            return {"jobDiagnosticLogs": self.logs or {"entries": [{"id": "10", "message": "first business failure"}],
                "hasNextPage": False, "truncated": False}}
        if query == C.EVIDENCE:
            return {"jobDiagnosticEvidence": {"state": "AVAILABLE", "attachments": [], "truncated": False}}
        if query == C.TIMELINE:
            return {"jobDiagnosticTimeline": {"state": "NOT_INSTRUMENTED", "attempts": [], "truncated": False}}
        raise AssertionError("unexpected query")


class CollectionTests(unittest.TestCase):
    def test_complete_job_with_failed_scenario_and_old_plan_is_included(self):
        client = Fake([{"total": 1, "records": [failed_job()]}])
        result = C.collect(client, START, END)
        self.assertEqual(1, len(result["incidents"]))
        self.assertTrue(result["coverage"]["jobs_complete"])
        self.assertEqual("COMPLETE", result["incidents"][0]["job"]["status"])
        self.assertNotIn("env", result["incidents"][0]["job"]["metadata"])
        self.assertNotIn("env", result["plans"]["9"]["metadata"])
        self.assertTrue(all(q.lstrip().startswith("query ") for q, _ in client.calls))

    def test_scan_is_complete_before_claiming_no_failure(self):
        client = Fake([{"total": 2, "records": [failed_job("1", completedAt="2026-09-01T00:00:00Z")]},
                       {"total": 2, "records": [failed_job("2")]}])
        result = C.collect(client, START, END, page_size=1)
        self.assertTrue(result["coverage"]["jobs_complete"])
        self.assertEqual("2", result["incidents"][0]["job"]["id"])

    def test_page_budget_never_reports_complete_zero_failures(self):
        result = C.collect(Fake([{"total": 5, "records": []}]), START, END, max_pages=1)
        self.assertFalse(result["coverage"]["jobs_complete"])
        self.assertTrue(result["coverage"]["missing_evidence"])

    def test_resource_failure_before_scenario_is_retained(self):
        job = failed_job(status="FAILED_RESOURCE_NOT_AVAILABLE", features=[])
        result = C.collect(Fake([{"total": 1, "records": [job]}]), START, END)
        self.assertIsNone(result["incidents"][0]["scenario"])
        self.assertTrue(result["incidents"][0]["missing_evidence"])

    def test_repeated_log_cursor_is_bounded_and_partial(self):
        client = Fake([{"total": 1, "records": [failed_job()]}],
                      logs={"entries": [], "hasNextPage": True, "endCursor": "same", "truncated": False})
        result = C.collect(client, START, END)
        self.assertTrue(result["coverage"]["logs_truncated"])
        self.assertEqual(2, len([q for q, _ in client.calls if q == C.LOGS]))

    def test_schema_or_rp_failure_preserves_platform_failure(self):
        result = C.collect(Fake([{"total": 1, "records": [failed_job()]}], missing_report=True), START, END)
        self.assertEqual(1, len(result["incidents"]))
        self.assertEqual([], result["incidents"][0]["logs"])
        self.assertTrue(result["incidents"][0]["missing_evidence"])

    def test_missing_mode_is_not_inferred_from_job_name_or_tags(self):
        job = failed_job(name="AI generation", metadata={"env": {"MODE": "implement"}})
        result = C.collect(Fake([{"total": 1, "records": [job]}]), START, END)
        self.assertEqual({}, result["incidents"][0]["job"]["metadata"])

    def test_token_material_is_redacted_in_nested_logs(self):
        value = C.redact({"password": "secret-value", "logs": ["Bearer private-token password=abc dcpat_random_secret"]})
        self.assertNotIn("secret-value", str(value))
        self.assertNotIn("private-token", str(value))
        self.assertNotIn("password=abc", str(value))
        self.assertNotIn("dcpat_random_secret", str(value))

    def test_evidence_window_is_required_bounded_and_deduplicated(self):
        client = Fake([])
        timeline = {"attempts": [{"startedAt": "2026-10-01T00:00:00Z",
                                   "completedAt": "2026-10-01T02:30:00Z"}]}
        value = C.collect_evidence(client, {"id": "1"}, "rp-3", timeline, [])
        self.assertEqual(3, value["time_windows"])
        for query, variables in client.calls:
            self.assertEqual(C.EVIDENCE, query)
            self.assertLessEqual((C.timestamp(variables["to"]) - C.timestamp(variables["from"])).total_seconds(), 3600)
        with self.assertRaises(C.CollectionError):
            C.collect_evidence(client, {"id": "1"}, "rp-3", None, [])

    def test_private_output_and_no_symlink_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            C.write_private(output, {"incidents": []})
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            link = Path(tmp) / "link.json"
            link.symlink_to(output)
            with self.assertRaises(OSError):
                C.write_private(link, {"changed": True})


if __name__ == "__main__":
    unittest.main()
