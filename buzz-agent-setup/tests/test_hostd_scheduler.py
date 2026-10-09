"""Shared request admission with real competing threads and a controlled clock."""
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'hostd'))
from scheduler import Scheduler, RateLimits, AdmissionError


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def wait(self, condition, seconds): self.now += seconds


class Scheduling(unittest.TestCase):
    def paced(self, **kwargs):
        clock = Clock()
        return clock, Scheduler(clock=clock, wait=clock.wait, **kwargs)

    def wait_pending(self, scheduler, count):
        end = time.monotonic() + 2
        while scheduler.pending_count != count and time.monotonic() < end:
            time.sleep(.001)
        self.assertEqual(scheduler.pending_count, count)

    def launch(self, callback):
        result = []
        def run():
            try: result.append(callback())
            except BaseException as error: result.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(lambda: thread.join(2))
        return thread, result

    def test_callable_runs_once_result_preserved_and_failure_consumes_rate_budget(self):
        clock, scheduler = self.paced()
        calls = []
        def failed():
            calls.append(clock())
            raise ValueError('private upstream body')
        with self.assertRaises(ValueError): scheduler.run(failed, app_id='app', chat_id='chat')
        self.assertEqual(scheduler.run(lambda: (calls.append(clock()), 42)[1], app_id='app', chat_id='chat'), 42)
        self.assertEqual(calls, [0, .2])

    def test_chat_budget_is_shared_by_different_bot_apps(self):
        clock, scheduler = self.paced()
        starts = []
        for index in range(6):
            scheduler.run(lambda: starts.append(clock()), app_id=f'app{index}', chat_id='onechat')
        for actual, expected in zip(starts, [0, .2, .4, .6, .8, 1]):
            self.assertAlmostEqual(actual, expected)

    def test_app_budgets_are_shared_across_chats_with_strict_rolling_guard(self):
        clock, scheduler = self.paced(limits=RateLimits(burst=1000))
        starts = []
        for index in range(1001):
            scheduler.run(lambda: starts.append(clock()), app_id='oneapp', chat_id=f'chat{index}')
        self.assertEqual(starts[:50], [0] * 50)
        self.assertGreaterEqual(starts[50], 1)
        self.assertGreaterEqual(starts[1000], 60)
        for start in starts:
            self.assertLessEqual(sum(start - 1 < value <= start for value in starts), 50)
            self.assertLessEqual(sum(start - 60 < value <= start for value in starts), 1000)

    def test_same_pair_is_serialized_but_other_app_chat_progresses(self):
        scheduler = Scheduler()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        first, first_result = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2)), app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        second, second_result = self.launch(lambda: scheduler.run(lambda: 'same', app_id='a', chat_id='c'))
        self.wait_pending(scheduler, 1)
        self.assertEqual(scheduler.run(lambda: 'other', app_id='b', chat_id='d'), 'other')
        self.assertTrue(second.is_alive())
        release.set()
        first.join(1); second.join(1)
        self.assertEqual(second_result, ['same'])
        self.assertFalse(first.is_alive())

    def test_normal_overtakes_backfill_and_bounded_burst_gives_backfill_a_turn(self):
        scheduler = Scheduler(normal_burst=2, limits=RateLimits(chat_per_second=1000, app_per_second=1000, app_per_minute=60000))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        holder, _ = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2)), app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        order, threads = [], []
        for priority, name in [('backfill', 'b'), ('normal', 'n1'), ('normal', 'n2'), ('normal', 'n3')]:
            threads.append(self.launch(lambda p=priority, n=name: scheduler.run(lambda: order.append(n), app_id='a', chat_id='c', priority=p))[0])
            self.wait_pending(scheduler, len(threads))
        release.set(); holder.join(1)
        for thread in threads: thread.join(1)
        self.assertEqual(order, ['n1', 'n2', 'b', 'n3'])

    def test_same_app_other_chat_and_same_chat_other_app_can_progress(self):
        scheduler = Scheduler()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        holder, _ = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2)), app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        self.assertEqual(scheduler.run(lambda: 'app budget only', app_id='a', chat_id='d'), 'app budget only')
        self.assertEqual(scheduler.run(lambda: 'chat budget only', app_id='b', chat_id='c'), 'chat budget only')
        self.assertTrue(holder.is_alive())
        release.set(); holder.join(1)

    def test_uncontested_lane_cannot_reset_backfill_fairness(self):
        scheduler = Scheduler(normal_burst=2, limits=RateLimits(chat_per_second=1000, app_per_second=1000, app_per_minute=60000))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        holder, _ = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2)), app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        order, threads = [], []
        def normal(name):
            order.append(name)
            # This independent lane executes while the contended pair is busy.
            scheduler.run(lambda: None, app_id=name, chat_id='unrelated')
        for priority, name in [('backfill', 'b'), ('normal', 'n1'), ('normal', 'n2'), ('normal', 'n3')]:
            callback = (lambda n=name: normal(n)) if priority == 'normal' else lambda: order.append('b')
            threads.append(self.launch(lambda p=priority, cb=callback: scheduler.run(cb, app_id='a', chat_id='c', priority=p))[0])
            self.wait_pending(scheduler, len(threads))
        release.set(); holder.join(1)
        for thread in threads: thread.join(1)
        self.assertEqual(order, ['n1', 'n2', 'b', 'n3'])

    def test_cancelled_queued_request_never_executes_or_blocks_next_request(self):
        scheduler = Scheduler()
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        holder, _ = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2)), app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        calls = []
        waiter, result = self.launch(lambda: scheduler.run(lambda: calls.append('bad'), app_id='a', chat_id='c', cancel=cancel))
        self.wait_pending(scheduler, 1)
        cancel.set(); scheduler.wake(); waiter.join(1)
        self.assertIsInstance(result[0], AdmissionError)
        self.assertFalse(result[0].dispatched)
        self.assertEqual(calls, [])
        release.set(); holder.join(1)
        self.assertEqual(scheduler.run(lambda: 'next', app_id='a', chat_id='c'), 'next')

    def test_rate_wait_timeout_cannot_dispatch_and_retry_is_explicit(self):
        clock, scheduler = self.paced()
        scheduler.run(lambda: None, app_id='a', chat_id='c')
        calls = []
        with self.assertRaises(AdmissionError) as caught:
            scheduler.run(lambda: calls.append(clock()), app_id='b', chat_id='c', timeout=.1)
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(calls, [])
        self.assertEqual(scheduler.pending_count, 0)
        scheduler.run(lambda: calls.append(clock()), app_id='b', chat_id='c')
        self.assertAlmostEqual(calls[0], .2)

    def test_queue_bound_and_shutdown_leave_inflight_result_unchanged(self):
        scheduler = Scheduler(max_pending=1)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        holder, result = self.launch(lambda: scheduler.run(lambda: (entered.set(), release.wait(2), 'accepted')[-1], app_id='a', chat_id='c'))
        self.assertTrue(entered.wait(1))
        waiter, rejected = self.launch(lambda: scheduler.run(lambda: 'queued', app_id='a', chat_id='c'))
        self.wait_pending(scheduler, 1)
        with self.assertRaises(AdmissionError): scheduler.run(lambda: 'overflow', app_id='b', chat_id='d')
        scheduler.close(); waiter.join(1)
        self.assertIsInstance(rejected[0], AdmissionError)
        with self.assertRaises(AdmissionError): scheduler.run(lambda: 'after close', app_id='b')
        release.set(); holder.join(1)
        self.assertEqual(result, ['accepted'])

    def test_reentrant_same_thread_rejected_before_deadlock(self):
        clock, scheduler = self.paced()
        with self.assertRaises(AdmissionError) as caught:
            scheduler.run(lambda: scheduler.run(lambda: 'inner', app_id='a', chat_id='c'), app_id='a', chat_id='c')
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(scheduler.pending_count, 0)

    def test_validation_and_wait_errors_have_fixed_remedy_without_identifiers(self):
        scheduler = Scheduler()
        for kwargs in ({'app_id': 'private app\nbody'}, {'app_id': 'a', 'priority': 'wrong'},
                       {'app_id': 'a', 'timeout': float('nan')}, {'app_id': 'a', 'timeout': 0}):
            with self.assertRaises(AdmissionError) as caught: scheduler.run(lambda: None, **kwargs)
            self.assertIn('怎么解决', str(caught.exception))
            self.assertIn('复制给 AI', str(caught.exception))
            self.assertNotIn('private', str(caught.exception))

    def test_app_only_calls_have_budget_without_using_a_fake_shared_chat(self):
        clock, scheduler = self.paced()
        scheduler.run(lambda: None, app_id='a')
        scheduler.run(lambda: None, app_id='b')
        self.assertEqual(clock(), 0)
        scheduler.run(lambda: None, app_id='a')
        self.assertAlmostEqual(clock(), .06)


if __name__ == '__main__': unittest.main()
