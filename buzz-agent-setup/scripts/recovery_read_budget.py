"""Hard read-only deadlines for the Linux, single-threaded recovery controller.

Socket timeouts alone permit an endless trickle of bytes. The wall timer covers
HTTP, JSON and signature checks, but NEVER an outbox/journal write or a POST.
Nested queries share the complete original-set budget, not a fresh deadline.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import signal
import threading
import time

MAX_SOURCES = 256
MAX_BYTES = 1024 * 1024
MAX_QUERIES = MAX_SOURCES * 3  # membership, original, optional owner attestation
READ_SECONDS = 10
_ACTIVE = ContextVar("recovery_read_budget", default=None)


class SourceReadFailure(RuntimeError):
    """Fixed, public-safe reason; never substitute partially verified work."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.reason = {
            "source_read_timeout": "原请求核验超过 10 秒总时限，本话题本轮未自动续接，全部原任务已保留。请 owner 检查 Relay 响应；恢复后会自动重试，无需手动发送 continue。",
            "source_read_capacity_exceeded": "原请求核验超过单话题容量上限（256 条原请求、合计 1 MiB 响应或 768 次查询），本轮未自动续接，全部原任务已保留。请 owner 检查积压与异常大消息，并联系维护者处理恢复容量；系统不会截断任务后当作成功。",
            "source_read_budget_unavailable": "当前恢复进程无法安全启用原请求核验时限，本轮未自动续接，原任务已保留。请 owner 检查恢复控制器是否通过标准单线程服务启动，以及是否存在冲突的进程定时器。",
        }[code]


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + READ_SECONDS
        self.bytes = self.queries = 0
        self.failure = None

    def fail(self, code):
        self.failure = code
        raise SourceReadFailure(code)

    def remaining(self):
        if self.failure is not None:
            raise SourceReadFailure(self.failure)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.fail("source_read_timeout")
        return min(READ_SECONDS, remaining)

    def sources(self, count):
        self.remaining()
        if count > MAX_SOURCES:
            self.fail("source_read_capacity_exceeded")

    def request(self):
        self.queries += 1
        if self.queries > MAX_QUERIES:
            self.fail("source_read_capacity_exceeded")
        return self.remaining()

    def response(self, size):
        self.bytes += size
        if self.bytes > MAX_BYTES:
            self.fail("source_read_capacity_exceeded")
        self.remaining()


@contextmanager
def read_budget():
    """Reuse an enclosing source read; otherwise own and restore a wall timer.

Fail closed if embedded in an incompatible executor. Never steal an existing
timer, run asynchronous workers, or leave reads running after timeout.
"""
    budget = _ACTIVE.get()
    if budget is not None:
        budget.remaining()
        yield budget
        budget.remaining()
        return
    if (threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
            or signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, set())):
        raise SourceReadFailure("source_read_budget_unavailable")
    budget = _Budget()
    previous = signal.getsignal(signal.SIGALRM)
    token = _ACTIVE.set(budget)
    def expired(_signum, _frame):
        budget.fail("source_read_timeout")
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, READ_SECONDS)
        yield budget
        budget.remaining()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _ACTIVE.reset(token)
