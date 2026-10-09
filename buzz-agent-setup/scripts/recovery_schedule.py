"""Persistent scheduling cursors, not a second work/authorization ledger.

The enclosing RecoveryStore holds the single-writer lock for the whole round.
Native journals remain the only source of pending responsibility. Advancing a
cursor before network work cannot discharge or hide a task after a crash.
"""
import json
import math
import random
import time

NETWORK_FAILURES = frozenset({"recovery_delivery_unverified", "recovery_failure_notice_unverified",
                              "source_authority_unavailable", "source_unverified", "agent_owner_unverified",
                              "source_read_timeout", "source_read_budget_unavailable"})


class NoticeBudgetExceeded(RuntimeError):
    """Defer feedback, never change authorization or discard pending work."""


def _now():
    now = time.time()
    if not math.isfinite(now) or not 0 < now <= 253402300799:
        raise ValueError("recovery scheduling clock unverified")
    return now


class RecoverySchedule:
    """At most eight Routes per round, two per Agent, with durable rotation."""
    def __init__(self, db):
        self.db = db
        self.remaining = 8
        self.notices_remaining = 8
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS scheduling_cursors (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS recovery_retries (key TEXT PRIMARY KEY, agent TEXT NOT NULL, "
                       "failures INTEGER NOT NULL, retry_at REAL NOT NULL, reasons TEXT NOT NULL)")

    def _cursor(self, key):
        row = self.db.execute("SELECT value FROM scheduling_cursors WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    @staticmethod
    def _rotate(values, cursor):
        return [v for v in values if v > cursor] + [v for v in values if v <= cursor]

    def agents(self, agents):
        by_key = {agent["pubkey"]: agent for agent in agents}
        return [by_key[key] for key in self._rotate(sorted(by_key), self._cursor("agent"))]

    def select(self, agent, routes, operations):
        now = _now()
        keys_in_scope = set(operations.values())
        # Retry metadata is not an outbox or a responsibility ledger. Remove it
        # only after the caller validated the COMPLETE pending native inventory;
        # changed generations/work sets get fresh scheduling, never old authority.
        with self.db:
            for key, in self.db.execute("SELECT key FROM recovery_retries WHERE agent=?", (agent,)).fetchall():
                if key not in keys_in_scope:
                    self.db.execute("DELETE FROM recovery_retries WHERE key=?", (key,))
        due = []
        for route in routes:
            row = self._retry(operations[route])
            if row is None or row[1] <= now:
                due.append(route)
        keys = {json.dumps(route, separators=(",", ":")): route for route in due}
        selected = self._rotate(sorted(keys), self._cursor("route:" + agent))[:min(2, self.remaining)]
        if selected:
            # Both cursors must commit together BEFORE any remote effect. A
            # reopened controller rotates even if a preceding send timed out.
            with self.db:
                self.db.executemany("INSERT INTO scheduling_cursors VALUES (?,?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    [("agent", agent), ("route:" + agent, selected[-1])])
            self.remaining -= len(selected)
        return [keys[key] for key in selected]

    def _retry(self, key):
        row = self.db.execute("SELECT failures,retry_at,reasons FROM recovery_retries WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        count, deadline, encoded = row
        reasons = json.loads(encoded)
        if (type(count) is not int or not 1 <= count <= 32 or type(deadline) not in (int, float)
                or not math.isfinite(deadline) or not 0 < deadline <= 253402300799
                or not isinstance(reasons, list) or not reasons
                or any(not isinstance(reason, str) or reason not in NETWORK_FAILURES for reason in reasons)):
            raise ValueError("recovery retry state unverified")
        return count, deadline, reasons

    def outcome(self, agent, key, errors):
        reasons = sorted(set(errors).intersection(NETWORK_FAILURES))
        with self.db:
            if not reasons:
                self.db.execute("DELETE FROM recovery_retries WHERE key=?", (key,))
                return
            previous = self._retry(key)
            failures = min(32, previous[0] + 1 if previous else 1)
            base = min(300, 5 * 2 ** min(failures - 1, 6))
            delay = max(5, min(300, base * random.uniform(0.8, 1.2)))
            self.db.execute("INSERT INTO recovery_retries VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                            "failures=excluded.failures,retry_at=excluded.retry_at,reasons=excluded.reasons",
                            (key, agent, failures, _now() + delay, json.dumps(reasons)))

    def status(self, operations):
        pending = [row for key in operations.values() if (row := self._retry(key)) is not None]
        return dict(retry_pending=len(pending), next_retry_at=min((row[1] for row in pending), default=None),
                    retry_reasons=sorted({reason for row in pending for reason in row[2]}))

    def claim_notice(self):
        if self.notices_remaining <= 0:
            raise NoticeBudgetExceeded("recovery_notice_budget_exhausted")
        self.notices_remaining -= 1
