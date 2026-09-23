"""Pin positive/negative recovery facts, without claiming model acceptance."""
import importlib.util
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "evaluations/fixtures/lease_recovery.py"
spec = importlib.util.spec_from_file_location("lease_recovery", SOURCE)
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
spec.loader.exec_module(fixture)


class LeaseRecoveryFixtureTest(unittest.TestCase):
    def test_expiry_does_not_invoke_scheduler_or_prove_persistence(self):
        for running, write_ok, expected in (
            (False, True, "not_invoked"), (True, False, "write_failed")
        ):
            job = fixture.Job()
            self.assertEqual(fixture.release(job, expired=True,
                             scheduler_running=running, write_ok=write_ok), expected)
            self.assertEqual(job.lease, "held")
            self.assertEqual(fixture.resubmit(job, input_available=True,
                             write_ok=True), "existing_job")
            self.assertIsNone(job.output)

    def test_release_is_not_queue_admission_or_business_output(self):
        job = fixture.Job()
        self.assertEqual(fixture.release(job, expired=True,
                         scheduler_running=True, write_ok=True), "released")
        self.assertFalse(job.queued)
        self.assertIsNone(job.output)
        self.assertEqual(fixture.resubmit(job, input_available=False,
                         write_ok=True), "input_missing")
        self.assertEqual(fixture.resubmit(job, input_available=True,
                         write_ok=False), "write_failed")
        self.assertFalse(job.queued)

    def test_admission_still_requires_execution_and_output_readback(self):
        job = fixture.Job(lease="released")
        self.assertEqual(fixture.resubmit(job, input_available=True,
                         write_ok=True), "queued")
        self.assertEqual(fixture.resubmit(job, input_available=True,
                         write_ok=True), "existing_job")
        self.assertEqual(fixture.execute(job, worker_running=False,
                         output_write_ok=True), "not_invoked")
        self.assertEqual(fixture.execute(job, worker_running=True,
                         output_write_ok=False), "write_failed")
        self.assertIsNone(job.output)
        self.assertEqual(fixture.execute(job, worker_running=True,
                         output_write_ok=True), "completed")
        self.assertEqual(job.output, "result")
        self.assertFalse(job.queued)
