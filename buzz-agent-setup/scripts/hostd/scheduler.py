"""Thread-safe, shared Feishu request admission (design section 04).

One instance belongs to the hostd process and is shared by readers, workers,
outlets and onboarding. Token buckets pace chat/app traffic; rolling guards
also enforce the documented 5/s per chat and 50/s + 1000/min per app ceilings.
Default capacity one is deliberately conservative, not an endpoint-specific
burst allowance. App credentials, token caching and refresh remain owned by
the native CLI and its isolated per-app profile; no token enters this module.

The callable runs once in its calling thread, outside the scheduler lock.
Timeout/cancellation apply only BEFORE admission. The callable must bound its
own execution (the existing CLI does), and its delivery ledger owns retries.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time


class AdmissionError(RuntimeError):
    """No business callable was dispatched; retry may be safely reconsidered."""
    dispatched = False

    def __init__(self):
        super().__init__('请求尚未开始，调度等待已取消、超时或暂时无法接纳。\n'
                         '怎么解决：检查服务状态与积压，在原投递账本下重新排队；不要更换发送身份。\n'
                         '复制给 AI：帮我检查 hostd 共享调度器的队列、限流和取消状态；不要输出凭据或消息正文。')


@dataclass(frozen=True)
class RateLimits:
    chat_per_second: int = 5
    app_per_second: int = 50
    app_per_minute: int = 1000
    burst: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in
               (self.chat_per_second, self.app_per_second, self.app_per_minute, self.burst)):
            raise AdmissionError()


class _Budget:
    def __init__(self, limit, seconds, burst, now):
        self.limit, self.seconds = limit, seconds
        self.rate, self.capacity = limit / seconds, min(limit, burst)
        self.tokens, self.updated = float(self.capacity), now
        self.starts = deque()

    def delay(self, now):
        self.tokens = min(self.capacity, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = max(now, self.updated)
        while self.starts and self.starts[0] <= now - self.seconds:
            self.starts.popleft()
        rolling = max(0, self.starts[0] + self.seconds - now) if len(self.starts) >= self.limit else 0
        pacing = max(0, (1 - self.tokens) / self.rate)
        return max(rolling, pacing)

    def spend(self, now):
        self.tokens = max(0, self.tokens - 1)
        self.starts.append(now)


@dataclass(eq=False)
class _Ticket:
    app: str
    chat: str | None
    priority: str

    @property
    def pair(self): return self.app, self.chat


class Scheduler:
    """Shared admission without a background thread or an executor pool.

    run(callable, app_id=..., chat_id=..., priority='normal'|'backfill',
        timeout=90, cancel=threading.Event|None)

    A chat_id of None means a real app-wide operation; it consumes only app
    budgets. Normal work overtakes backfill, with at most normal_burst normal
    starts while eligible backfill waits. FIFO applies within each priority
    among eligible lanes. Busy/rate-blocked lanes never block independent
    app/chat lanes. In-flight calls sharing the exact app/chat pair serialize.
    close cancels queued requests, leaving already-dispatched calls alone.
    clock/wait injection is solely for deterministic offline tests.
    """
    def __init__(self, *, limits=None, normal_burst=8, max_pending=1024,
                 clock=time.monotonic, wait=None):
        self.limits = limits or RateLimits()
        if (not isinstance(self.limits, RateLimits) or type(normal_burst) is not int or normal_burst < 1
                or type(max_pending) is not int or max_pending < 1):
            raise AdmissionError()
        self.normal_burst, self.max_pending = normal_burst, max_pending
        self.clock, self.wait = clock, wait or (lambda condition, seconds: condition.wait(seconds))
        self.condition = threading.Condition()
        self.queue, self.active = [], {}
        self.apps, self.chats = {}, {}
        self.normal_streak, self.closed = 0, False

    @property
    def pending_count(self):
        with self.condition: return len(self.queue)

    def wake(self):
        """Notify after explicit cancellation or advancing an injected clock."""
        with self.condition: self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def _budgets(self, ticket, now):
        if ticket.app not in self.apps:
            limits = self.limits
            self.apps[ticket.app] = (_Budget(limits.app_per_second, 1, limits.burst, now),
                                     _Budget(limits.app_per_minute, 60, limits.burst, now))
        budgets = list(self.apps[ticket.app])
        if ticket.chat is not None:
            if ticket.chat not in self.chats:
                self.chats[ticket.chat] = _Budget(self.limits.chat_per_second, 1, self.limits.burst, now)
            budgets.append(self.chats[ticket.chat])
        return budgets

    def _choose(self, now):
        eligible, delay = [], .25
        for ticket in self.queue:
            if ticket.pair in self.active:
                continue
            ready_in = max(budget.delay(now) for budget in self._budgets(ticket, now))
            if ready_in <= 0:
                eligible.append(ticket)
            else:
                delay = min(delay, ready_in)
        normal = next((t for t in eligible if t.priority == 'normal'), None)
        backfill = next((t for t in eligible if t.priority == 'backfill'), None)
        if normal is not None and (backfill is None or self.normal_streak < self.normal_burst):
            return normal, backfill is not None, delay
        return backfill, False, delay

    def run(self, callable_, *, app_id, chat_id=None, priority='normal', timeout=90, cancel=None):
        def key(value):
            return isinstance(value, str) and 0 < len(value) <= 256 and not any(c.isspace() or ord(c) < 32 for c in value)
        if (not callable(callable_) or not key(app_id) or (chat_id is not None and not key(chat_id))
                or priority not in ('normal', 'backfill') or isinstance(timeout, bool)
                or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0
                or (cancel is not None and not isinstance(cancel, threading.Event))):
            raise AdmissionError()
        ticket = _Ticket(app_id, chat_id, priority)
        ident = threading.get_ident()
        with self.condition:
            if self.closed or len(self.queue) >= self.max_pending or self.active.get(ticket.pair) == ident:
                raise AdmissionError()
            deadline = self.clock() + timeout
            self.queue.append(ticket)
            self.condition.notify_all()
            admitted = False
            try:
                while True:
                    now = self.clock()
                    if self.closed or (cancel is not None and cancel.is_set()) or now >= deadline:
                        raise AdmissionError()
                    chosen, contested, delay = self._choose(now)
                    if chosen is ticket:
                        for budget in self._budgets(ticket, now): budget.spend(now)
                        self.queue.remove(ticket)
                        self.active[ticket.pair] = ident
                        if ticket.priority == 'backfill':
                            self.normal_streak = 0
                        elif contested:
                            self.normal_streak += 1
                        admitted = True
                        self.condition.notify_all()
                        break
                    self.wait(self.condition, max(1e-9, min(delay, deadline - now)))
            finally:
                if not admitted:
                    self.queue.remove(ticket)
                    self.condition.notify_all()
        try:
            return callable_()
        finally:
            with self.condition:
                del self.active[ticket.pair]
                self.condition.notify_all()
