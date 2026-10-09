"""Deterministic scheduling tests: no network, writes, or live database."""
import asyncio
from collections import deque
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd.onboarding_runtime import OnboardingRuntime


class RetrySchedulingTests(unittest.IsolatedAsyncioTestCase):
    def service(self, count=14):
        self.now = 100.0
        self.rows = [dict(request_id=f'JOIN-{i:03d}', status='approved',
                          callback_app_id='cli_test') for i in range(count)]
        s = OnboardingRuntime.__new__(OnboardingRuntime)
        s._closed = False
        s._approval_retry_at = 0.0
        s._approval_retry_cursor = ''
        s._approval_retry_due = {}
        s._notice_retry_at = 10000.0
        s._retry_clock = lambda: self.now
        s.clock = lambda: 1000
        s._active = {}
        s._queue = deque()
        s._lock = asyncio.Lock()
        s._wake = asyncio.Event()
        s.last_notice = ''
        s.store = SimpleNamespace(join_requests=lambda: self.rows,
            join_request=lambda key: next((r for r in self.rows if r['request_id']==key), None),
            fallback_provenance=lambda key: None)
        self.calls = []
        async def tick(**kw): self.calls.append((kw['request_id'], self.now))
        s.coordinator = SimpleNamespace(tick=mock.AsyncMock(side_effect=tick),
            retry_notices=mock.AsyncMock(), drain=mock.AsyncMock(), close=mock.Mock(), last_notice='')
        for name in ('_recover_own', '_restore_own_request', '_recover_fallback'):
            setattr(s, name, mock.AsyncMock())
        return s

    async def test_fourteen_requests_first_pass_has_five_second_slots(self):
        s = self.service()
        for i in range(14):
            self.now = 100 + 5*i
            await s._retry_approved()
            await s._retry_approved()  # unrelated foreground calls cannot burst
        self.assertEqual(self.calls, [(r['request_id'], 100+5*i) for i,r in enumerate(self.rows)])

    async def test_failure_rotates_and_each_request_cools_for_sixty_seconds(self):
        s = self.service(2)
        async def tick(**kw):
            self.calls.append((kw['request_id'],self.now))
            raise RuntimeError('offline failure')
        s.coordinator.tick.side_effect = tick
        for stamp in (100, 104, 105, 110, 159, 160):
            self.now=stamp
            await s._retry_approved()
        self.assertEqual(self.calls, [('JOIN-000',100), ('JOIN-001',105), ('JOIN-000',160)])

    async def test_slow_attempt_reserves_slot_and_does_not_add_sixty_seconds_for_next(self):
        s = self.service(2)
        async def tick(**kw):
            self.calls.append((kw['request_id'],self.now))
            self.now += 40
        s.coordinator.tick.side_effect = tick
        await s._retry_approved()
        await s._retry_approved()
        self.assertEqual(self.calls,[('JOIN-000',100),('JOIN-001',140)])
        self.assertGreaterEqual(s._approval_retry_due['JOIN-000'],200)

    async def test_applied_is_recovered_but_requested_terminal_and_fallback_are_not(self):
        s = self.service(5)
        for row,status in zip(self.rows,('requested','done','denied','applied','approved')):row['status']=status
        s.store.fallback_provenance=lambda key: {} if key=='JOIN-004' else None
        await s._retry_approved()
        self.assertEqual(self.calls,[('JOIN-003',100)])

    async def test_full_drain_cannot_bypass_approved_cooldown(self):
        s = self.service(1)
        await s.drain()
        await s.drain()
        self.assertEqual(self.calls,[('JOIN-000',100)])
        s._recover_fallback.assert_awaited()

    async def test_actual_reconnect_discovery_cannot_bypass_approved_or_applied_cooldown(self):
        for status in ('approved','applied'):
            with self.subTest(status=status):
                s=self.service(1)
                self.rows[0].update(status=status,agent_id='a'*64,chat_id='oc_test')
                s.store.transaction=nullcontext
                s.records={'cli_test':SimpleNamespace(pubkey='a'*64,owner_pubkey='b'*64)}
                s._issuer_bindings={}
                s.chats=mock.AsyncMock(return_value=('oc_test',))
                s.discover=mock.AsyncMock(return_value=SimpleNamespace(
                    status='unbound',agent_id='a'*64,binding_id=None))
                s._queue.append(('connected','cli_test',None,None,None))
                await s.drain()  # actual _foreground AND actual _discovered
                self.assertEqual(self.calls,[('JOIN-000',100)])
                s.discover.assert_awaited_once_with('cli_test','oc_test')

    async def test_requested_recovery_remains_independent(self):
        s = self.service(2)
        self.rows[1]['status']='requested'
        await s.drain()
        self.assertEqual(self.calls,[('JOIN-000',100),('JOIN-001',100)])

    async def test_request_approved_during_foreground_cannot_use_old_requested_cursor(self):
        s=self.service(1)
        self.rows[0]['status']='requested'
        foreground=s._foreground
        passes=0
        async def changed():
            nonlocal passes
            passes+=1
            if passes==3:self.rows[0]['status']='approved'
            await foreground()
        s._foreground=changed
        await s.drain()
        self.assertEqual(self.calls,[('JOIN-000',100)])

    async def test_wall_clock_jump_does_not_waive_cooldown_and_terminal_state_is_pruned(self):
        s=self.service(1)
        await s._retry_approved()
        s.clock=lambda: 99999999
        self.now=105
        await s._retry_approved()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(s._approval_delay(),55)
        self.rows[0]['status']='done'
        self.assertEqual(s._approval_delay(),60)
        self.assertEqual(s._approval_retry_due,{})

    async def test_timer_covers_fourteen_due_requests_without_speeding_periodic_recovery(self):
        s=self.service(14)
        async def wait(awaitable, *, timeout):
            awaitable.close()
            self.now+=timeout
            if len(self.calls)>=14 or self.now>1000:s.close()
            raise asyncio.TimeoutError
        with mock.patch('hostd.onboarding_runtime.asyncio.wait_for',side_effect=wait):
            await s.run()
        self.assertEqual(self.calls,[(r['request_id'],100+5*i) for i,r in enumerate(self.rows)])
        self.assertEqual(s._recover_fallback.await_count,2)

    async def test_restart_spaces_durable_pending_rows_instead_of_bursting(self):
        s=self.service(3)
        await s._retry_approved()
        # Reconstruct only volatile scheduling state, as create() does.
        s._approval_retry_at=0.;s._approval_retry_cursor='';s._approval_retry_due={}
        self.calls.clear()
        await s._retry_approved()
        await s._retry_approved()
        self.now+=5
        await s._retry_approved()
        self.assertEqual(self.calls,[('JOIN-000',100),('JOIN-001',105)])

    async def test_due_wakes_do_not_accelerate_full_recovery_and_close_is_drained(self):
        s=self.service(3)
        full=s.drain
        s.drain=mock.AsyncMock(side_effect=full)
        async def wait(awaitable, *, timeout):
            awaitable.close()
            self.now += timeout
            if len(self.calls)>=3 or self.now>1000:s.close()
            raise asyncio.TimeoutError
        with mock.patch('hostd.onboarding_runtime.asyncio.wait_for', side_effect=wait):
            await s.run()
        self.assertEqual(self.calls,[('JOIN-000',100),('JOIN-001',105),('JOIN-002',110)])
        self.assertEqual(s.drain.await_count,1)
        self.assertEqual(s._recover_fallback.await_count,1)
        self.assertEqual(s.coordinator.retry_notices.await_count,0)
        self.assertFalse(s._lock.locked())

    async def test_close_during_tick_finishes_inflight_but_starts_no_next(self):
        s=self.service(2)
        entered=asyncio.Event();finish=asyncio.Event()
        async def tick(**kw):
            self.calls.append((kw['request_id'],self.now));entered.set();await finish.wait()
        s.coordinator.tick.side_effect=tick
        task=asyncio.create_task(s.drain())
        await entered.wait();s.close()
        self.assertFalse(task.done())
        finish.set();await task
        self.now+=100;await s._retry_approved()
        self.assertEqual(self.calls,[('JOIN-000',100)])


if __name__=='__main__':unittest.main()
