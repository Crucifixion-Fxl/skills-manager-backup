"""Read guard ownership and exact boundaries; no network or persistent writes."""
from pathlib import Path
import signal
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_read_budget import MAX_BYTES, MAX_QUERIES, SourceReadFailure, read_budget


class ReadGuardTests(unittest.TestCase):
    def test_nested_queries_keep_the_same_budget_and_restore_the_handler(self):
        handler = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(SourceReadFailure) as exhausted:
            with read_budget() as outer:
                outer.response(MAX_BYTES - 1)
                with read_budget() as inner:
                    self.assertIs(inner, outer)
                    inner.response(1)
                with self.assertRaises(SourceReadFailure) as caught:
                    with read_budget() as inner:
                        inner.response(1)
                self.assertEqual(caught.exception.code, "source_read_capacity_exceeded")
                # A swallowed inner error cannot make the whole read succeed.
                with self.assertRaises(SourceReadFailure):
                    outer.remaining()
        self.assertEqual(exhausted.exception.code, "source_read_capacity_exceeded")
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_query_count_rejects_the_769th_read(self):
        handler = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(SourceReadFailure) as caught:
            with read_budget() as budget:
                for _ in range(MAX_QUERIES):
                    self.assertGreater(budget.request(), 0)
                budget.request()
        self.assertEqual(caught.exception.code, "source_read_capacity_exceeded")
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_existing_timer_is_not_replaced_or_cancelled(self):
        handler = signal.getsignal(signal.SIGALRM)
        signal.setitimer(signal.ITIMER_REAL, 60)
        try:
            with self.assertRaises(SourceReadFailure) as caught:
                with read_budget():
                    self.fail("stole existing timer")
            self.assertEqual(caught.exception.code, "source_read_budget_unavailable")
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 50)
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_non_main_thread_cannot_claim_a_hard_wall_deadline(self):
        outcomes = []
        def task():
            try:
                with read_budget():
                    outcomes.append("unexpected success")
            except SourceReadFailure as exc:
                outcomes.append(exc.code)
        worker = threading.Thread(target=task)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ["source_read_budget_unavailable"])
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
