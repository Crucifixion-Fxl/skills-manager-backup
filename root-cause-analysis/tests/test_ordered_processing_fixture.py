"""Protect calibration facts; passing these tests does not prove model quality."""
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "evaluations/fixtures/ordered_processing.py"
spec = importlib.util.spec_from_file_location("ordered_processing", SOURCE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class OrderedProcessingFixtureTest(unittest.TestCase):
    def test_optional_branch_preserves_prior_assignment_and_validation(self):
        self.assertEqual(fixture.effective_limit("7", None, True), 7)
        with self.assertRaises(ValueError):
            fixture.effective_limit("invalid", None, True)
        self.assertIsNone(fixture.effective_limit(None, None, True))

    def test_absent_primary_does_not_prove_absent_effective_value(self):
        self.assertEqual(fixture.effective_limit(None, "9", False), 9)
        with self.assertRaises(ValueError):
            fixture.effective_limit(None, None, False)

    def test_route_failure_requires_decode_success(self):
        self.assertEqual(fixture.process(True, False), "route_failed")
        self.assertEqual(fixture.process(False, False), "decode_failed")
        self.assertEqual(fixture.process(True, True), "completed")


if __name__ == "__main__":
    unittest.main()
