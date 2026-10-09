"""Durable, transport-independent recovery of runtime-authoritative Threads.

The caller verifies the live process/revision and authenticates relay reads.
No invite/join state, LLM output, or observer completion is an input here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "references/scripts"))
from restart_continue_message import build_continue_args
from recovery_relay import RouteBlocked, canonical_origin
from recovery_authority import runtime_policy
import recovery_inventory
from recovery_lifecycle import ready_message
from recovery_attempt import attempt_id
from recovery_schedule import NoticeBudgetExceeded
from recovery_read_budget import MAX_SOURCES, SourceReadFailure, read_budget

JOURNAL_VERSION = 10


class RecoveryStore:
    """One controller writer; FULL synchronous transactions precede all sends."""
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise ValueError("unsafe recovery database path")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        self.lock = os.open(str(self.path) + ".lock", flags, 0o600)
        self.db = None
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(self.path, flags, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ValueError("recovery database must be owner-only")
            finally:
                os.close(fd)
            self.db = sqlite3.connect(self.path)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, event TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0)")
            self.db.execute("CREATE TABLE IF NOT EXISTS covered (key TEXT PRIMARY KEY)")
            self.db.execute("CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, generation TEXT NOT NULL, tasks TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS delivery_receipts (key TEXT PRIMARY KEY, first_delivered_at INTEGER)")
            self.db.commit()
            return self
        except BaseException:
            try:
                if self.db is not None:
                    self.db.close()
            finally:
                os.close(self.lock)
            raise

    def __exit__(self, *exc):
        try:
            self.db.close()
        finally:
            os.close(self.lock)

    def covered(self, key):
        return self.db.execute("SELECT 1 FROM covered WHERE key=?", (key,)).fetchone() is not None

    def cover(self, keys):
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO covered VALUES (?)", [(key,) for key in keys])

    def delivery(self, key, generation, tasks):
        """Persist the handoff BEFORE publishing, not after a successful POST."""
        encoded = json.dumps(sorted(set(tasks)))
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?,?)", (key, generation, encoded))
            self.db.execute("INSERT OR IGNORE INTO delivery_receipts VALUES (?,NULL)", (key,))
        if self.db.execute("SELECT generation,tasks FROM deliveries WHERE key=?", (key,)).fetchone() != (generation, encoded):
            raise ValueError("recovery delivery contract changed")

    def reconcile(self, snapshot):
        """Only the target runtime's durable admission discharges old tasks.

        An active receipt transfers responsibility to that runtime's own active
        inventory. A completed receipt closes it. Relay acceptance alone does
        neither: the receiving process may crash before admitting the event.
        """
        covered = []
        for encoded, tasks in self.db.execute(
                "SELECT messages.event,deliveries.tasks FROM deliveries JOIN messages ON deliveries.key=messages.key WHERE generation=?",
                (snapshot["generation"],)).fetchall():
            if _receipt_transferred(snapshot, json.loads(encoded)):
                covered.extend(json.loads(tasks))
        # Validate the whole generation before committing any handoff. A later
        # contradictory row must not leave earlier original work discharged.
        self.cover(covered)

    def validate_receipt_targets(self, snapshots):
        """A known delivery cannot discharge work through another generation."""
        targets = {json.loads(event)["id"]: generation for event, generation in self.db.execute(
            "SELECT messages.event,deliveries.generation FROM deliveries JOIN messages ON deliveries.key=messages.key")}
        for snapshot in snapshots:
            for event_id in snapshot["receipts"]:
                if event_id in targets and targets[event_id] != snapshot["generation"]:
                    raise ValueError("recovery_receipt_inconsistent")

    def event_id(self, key):
        """The exact signed event this controller durably recorded for one outbox key."""
        row = self.db.execute("SELECT event FROM messages WHERE key=?", (key,)).fetchone()
        return json.loads(row[0])["id"] if row is not None else None

    def send(self, relay, key, content, tags, *, notice_budget=None):
        row = self.db.execute("SELECT event, acked FROM messages WHERE key=?", (key,)).fetchone()
        if row is None:
            if notice_budget is not None:
                notice_budget.claim_notice()
            event = relay.sign(content, tags)
            with self.db:
                self.db.execute("INSERT INTO messages(key,event) VALUES (?,?)", (key, json.dumps(event)))
        else:
            event, acked = json.loads(row[0]), row[1]
            if event["content"] != content or event["tags"] != tags:
                raise ValueError("recovery outbox contract changed")
            if acked:
                with self.db:
                    self._ack_delivery(key)
                return False
            if notice_budget is not None:
                notice_budget.claim_notice()
        # A previous POST may have committed even if its ACK was lost.
        readback = relay.lookup(event["id"])
        if readback is None:
            relay.publish(event)
            readback = relay.lookup(event["id"])
        if readback != event:
            raise ValueError("recovery message readback mismatch")
        with self.db:
            self.db.execute("UPDATE messages SET acked=1 WHERE key=?", (key,))
            self._ack_delivery(key)
        return True

    def _ack_delivery(self, key):
        """Start the receipt clock only after an exact, durable delivery ACK.

        Existing experimental outboxes with no clock start at this first known
        observation, never at the signed timestamp or an invented delivery time.
        This is conservative: a crash cannot produce a premature warning.
        """
        row = self.db.execute("SELECT first_delivered_at FROM delivery_receipts WHERE key=?", (key,)).fetchone()
        if row is not None and row[0] is None:
            now = int(time.time())
            if not 0 < now <= 253402300799:
                raise ValueError("recovery clock unverified")
            # With the surrounding send transaction, ACK and clock persist once.
            self.db.execute("UPDATE delivery_receipts SET first_delivered_at=? WHERE key=? AND first_delivered_at IS NULL", (now, key))

    def receipt_overdue(self, key, snapshot):
        row = self.db.execute(
            "SELECT messages.event,messages.acked,delivery_receipts.first_delivered_at "
            "FROM messages JOIN delivery_receipts USING(key) WHERE messages.key=?", (key,)).fetchone()
        if row is None or row[1] != 1 or type(row[2]) is not int or not 0 < row[2] <= 253402300799:
            raise ValueError("recovery delivery clock unverified")
        event = json.loads(row[0])
        # A known native refusal is a receipt with an actionable reason, not a
        # missing receiver. Active/terminal receipts are reconciled separately.
        if event["id"] in snapshot["receipts"]:
            return False
        return int(time.time()) - row[2] >= 120


def _key(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _receipt_transferred(snapshot, event):
    status = snapshot.get("receipts", {}).get(event["id"])
    if status not in ("active", "completed", "cancelled"):
        return False
    binding = [t for t in event["tags"] if t[0] == "recovery"]
    sources = snapshot["input_sources"].get(event["id"], [])
    if (len(binding) != 1 or len(binding[0]) < 4 or binding[0][2] != snapshot["generation"]
            or [s["event_id"] for s in sources] != binding[0][3:]
            or event["id"] not in snapshot["recovery_receipts"]):
        raise ValueError("recovery_receipt_inconsistent")
    expected = attempt_id(snapshot["relay"], snapshot["agent_pubkey"], snapshot["generation"],
                          sources[0]["route"]["channel"], sources[0]["route"]["root"], binding[0][3:])
    if binding[0][1] != expected or snapshot.get("recovery_attempts", {}).get(event["id"]) != expected:
        raise ValueError("recovery_receipt_inconsistent")
    route = sources[0]["route"]
    channel_tags = [t for t in event["tags"] if t[0] == "h"]
    agent_tags = [t for t in event["tags"] if t[0] == "p"]
    thread_tags = [t for t in event["tags"] if t[0] == "e"]
    if (event["kind"] != 9 or channel_tags != [["h", route["channel"]]]
            or agent_tags != [["p", snapshot["agent_pubkey"]]]
            or thread_tags != [["e", route["root"], "", "root"], ["e", route["root"], "", "reply"]]):
        raise ValueError("recovery_receipt_inconsistent")
    triggers = snapshot.get("triggers", {})
    if not isinstance(triggers, dict) or len(triggers) > 4096:
        raise ValueError("recovery_receipt_inconsistent")
    owners = []
    for turn, inputs in triggers.items():
        if (not isinstance(inputs, list) or len(inputs) > 256
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in inputs)):
            raise ValueError("recovery_receipt_inconsistent")
        owners.extend([turn] * inputs.count(event["id"]))
    if status in ("completed", "cancelled"):
        if owners:
            raise ValueError("recovery_receipt_inconsistent")
        return True
    if len(owners) != 1:
        raise ValueError("recovery_receipt_inconsistent")
    if route not in snapshot["active"].get(owners[0], []):
        raise ValueError("recovery_receipt_inconsistent")
    return True


def _generation(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid runtime generation")
    return value


def _authorized_sources(store, transport, sources, agent_name, agent_pubkey, policy, generation, tags, result, schedule):
    allowed, denied = [], []
    try:
        with read_budget() as budget:
            budget.sources(len(sources))
            for source in sources:
                try:
                    transport.validate_source(source, agent_pubkey, policy)
                    allowed.append(source)
                except SourceReadFailure:
                    raise
                except (OSError, ValueError, RuntimeError) as exc:
                    if not isinstance(exc, RouteBlocked):
                        exc = RouteBlocked("source_authority_unavailable", "原请求人当前的权限暂时无法核实。请 owner 检查 Relay 的成员与身份查询服务；恢复后会自动重试，任务记录会保留。", notice_allowed=True)
                    if not exc.notice_allowed:
                        # Loss of private-Thread access also invalidates the
                        # earlier subset. No feedback can be sent on this route.
                        raise exc
                    denied.append((source, exc))
                budget.remaining()
    except SourceReadFailure as exc:
        # validate_route already established writable owner access. Do not
        # turn a capacity/timeout failure into a partly authorized continuation.
        raise RouteBlocked(exc.code, exc.reason, notice_allowed=True) from exc
    # No writes or publication under the wall timer. All reads finished before
    # any denial/ready/continue can become durable or visible.
    for source, exc in denied:
        result["errors"].append(exc.code)
        key = _key(agent_pubkey, generation, source["event_id"], exc.code)
        try:
            store.send(transport, key + ":source-blocked",
                       f"{agent_name} 自动续接暂未执行原请求 {source['event_id'][:12]}：{exc.reason}", tags,
                       notice_budget=schedule)
        except NoticeBudgetExceeded:
            result["errors"].append("recovery_notice_budget_exhausted")
    return allowed


def _scheduling_operation(relay, agent, generation, route, sources):
    work_ids = sorted(source["event_id"] for source in sources)
    if len(work_ids) > MAX_SOURCES:
        # This key schedules visible failure only; it is NEVER a wire AttemptID.
        # Keep the protocol's 256-original bound and the entire pending set.
        return _key("oversized-source-set", canonical_origin(relay), agent, generation, route, work_ids)
    return attempt_id(relay, agent, generation, *route, work_ids)


def _validate(snapshot, agent_pubkey, relay):
    if type(snapshot.get("version")) is not int or snapshot["version"] != JOURNAL_VERSION:
        raise ValueError("schema_unsupported: upgrade native and controller together; preserve journals")
    if not (snapshot.get("phase") == "unavailable" and snapshot.get("runtime_policy") is None
            and snapshot.get("active") == {} and snapshot.get("channels") == []):
        runtime_policy(snapshot.get("runtime_policy"))
    if (snapshot.get("agent_pubkey") != agent_pubkey
            or canonical_origin(snapshot.get("relay", "")) != canonical_origin(relay)):
        raise ValueError("runtime identity or community mismatch")
    _generation(snapshot.get("generation"))
    if not isinstance(snapshot.get("active"), dict) or len(snapshot["active"]) > 4096:
        raise ValueError("invalid active task inventory")
    cancelled = snapshot.get("cancelled_turns")
    if not isinstance(cancelled, list) or len(cancelled) > 8192:
        raise ValueError("invalid cancellation inventory")
    if len({_generation(turn) for turn in cancelled}) != len(cancelled) or any(turn in snapshot["active"] for turn in cancelled):
        raise ValueError("invalid or contradictory cancellation tombstone")
    receipts = snapshot.get("receipts", {})
    if (not isinstance(receipts, dict) or len(receipts) > 16384
            or any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                   or value not in ("active", "completed", "cancelled", "blocked") for key, value in receipts.items())):
        raise ValueError("invalid runtime delivery receipts")


def recover(store, transport, *, agent_name, agent_pubkey, relay, current,
            snapshots, runtime_verified, schedule=None):
    """Continue each interrupted Thread once after verified runtime readiness.

    Failed Threads retain their pending records and do not block other Channels.
    `runtime_verified` must come from the production process/revision adapter,
    not from the on-disk snapshot itself. A controller restart needs no special
    resume command: it reopens the same outbox and reads remote receipts.
    """
    result = {"continued": 0, "errors": [], "continued_events": []}
    _validate(current, agent_pubkey, relay)
    if len(snapshots) > 1024:
        raise ValueError("runtime inventory exceeds recovery bound")
    valid = []
    for snapshot in [current, *snapshots]:
        try:
            _validate(snapshot, agent_pubkey, relay)
            # Validate the entire snapshot before it authorizes any sends.
            for turn, routes in snapshot["active"].items():
                _generation(turn)
                if not isinstance(routes, list) or not 1 <= len(routes) <= 256:
                    raise ValueError("invalid recovery routes")
                for route in routes:
                    channel, root = route["channel"], route["root"]
                    build_continue_args(channel=channel, root=root, agent_name=agent_name, agent_pubkey=agent_pubkey)
            recovery_inventory.validate(snapshot)
            valid.append(snapshot)
        except (ValueError, KeyError, TypeError, AttributeError):
            result["errors"].append("invalid_runtime_snapshot")
    if result["errors"]:
        # An unreadable generation could contain the handoff which already owns
        # old work. Skipping it could revive that work through another snapshot.
        return result
    try:
        groups = recovery_inventory.pending(valid, current["generation"])
    except (ValueError, KeyError, TypeError):
        result["errors"].append("invalid_original_source_history")
        return result
    # Keep existing outbox handoff audit; election now uses stable native work
    # bindings, not permanently covered old turn IDs (which die on restart).
    store.validate_receipt_targets(valid)
    for snapshot in valid:
        store.reconcile(snapshot)
    routes = sorted(groups)
    operations = {route: _scheduling_operation(relay, agent_pubkey, current["generation"], route, sources)
                  for route, sources in groups.items()}
    if schedule is not None:
        routes = schedule.select(agent_pubkey, routes, operations)
        result.update(scheduled=len(routes), deferred=len(groups) - len(routes))
    for channel, root in routes:
        sources = groups[channel, root]
        scheduling_operation = operations[channel, root]
        error_start = len(result["errors"])
        work_ids = [source["event_id"] for source in sources]
        lifecycle = _key(canonical_origin(relay), agent_pubkey, current["generation"], channel, root, "lifecycle")
        tags = [["h", channel], ["e", root, "", "root"], ["e", root, "", "reply"]]
        try:
            # Must verify the original signed root's Channel and current
            # membership/policy; missing Thread never falls back to top level.
            public_mode = transport.validate_route(channel, root, agent_pubkey)
            if runtime_verified:
                policy = current["runtime_policy"]
                if (policy["owner"] != transport.owner or policy["respond_to"] == "nobody"
                        or policy["respond_to"] != public_mode):
                    raise RouteBlocked("runtime_policy_denied",
                                       "实际运行权限与公开策略不一致，或当前不接受群内请求。请 owner 核对 Agent 的运行版本及响应策略；系统不会自动扩大权限。",
                                       notice_allowed=True)
                sources = _authorized_sources(store, transport, sources, agent_name, agent_pubkey, policy,
                                              current["generation"], tags, result, schedule)
                if not sources:
                    continue
                work_ids = [source["event_id"] for source in sources]
                operation = attempt_id(relay, agent_pubkey, current["generation"], channel, root, work_ids)
            if not runtime_verified or current.get("phase") != "ready":
                store.send(transport, lifecycle + ":not-ready", f"{agent_name} 自动续接尚未开始：启动状态尚未核实。请 owner 检查 Agent 服务、实际运行版本和订阅就绪状态；核实后会自动续接，无需手动发送 continue。", tags, notice_budget=schedule)
                result["errors"].append("runtime_not_ready")
                continue
            if channel not in current.get("channels", []):
                store.send(transport, lifecycle + ":subscription", f"{agent_name} 自动续接未完成：新进程尚未订阅本群。请检查 Agent 的频道配置与成员状态；恢复后会自动重试。", tags, notice_budget=schedule)
                result["errors"].append("agent_not_subscribed")
                continue
            store.send(transport, lifecycle + ":ready",
                       ready_message(agent_name, valid, current["generation"], channel, root, work_ids),
                       tags, notice_budget=schedule)
            store.delivery(operation + ":continue", current["generation"], work_ids)
            published = store.send(transport, operation + ":continue", f"@{agent_name} continue",
                                   tags + [["p", agent_pubkey], ["recovery", operation, current["generation"], *work_ids]])
            result["continued"] += int(published)
            if published:
                # The controller's own durable record of which event this round
                # produced, not a self-reported flag; this is what lets an L3
                # oracle bind one timer invocation to one specific continue by
                # identity instead of trusting a time-window coincidence.
                result["continued_events"].append(store.event_id(operation + ":continue"))
            if store.receipt_overdue(operation + ":continue", current):
                result["errors"].append("receipt_timeout")
                store.send(transport, operation + ":receipt-timeout",
                           f"{agent_name} 的自动续接消息已送达，但超过 120 秒仍尚未确认接收任务。可能是 Agent 忙碌、接收进程异常或恢复记录无法写入；请 owner 检查该 Agent 的服务与接收日志。原任务和投递已保留，会继续核对；无需重复发送 continue，也不会重复创建恢复消息。", tags, notice_budget=schedule)
        except NoticeBudgetExceeded:
            result["errors"].append("recovery_notice_budget_exhausted")
        except RouteBlocked as exc:
            result["errors"].append(exc.code)
            if exc.notice_allowed:
                try:
                    store.send(transport, lifecycle + ":blocked:" + exc.code,
                               f"{agent_name} 自动续接未完成：{exc.reason}", tags, notice_budget=schedule)
                except NoticeBudgetExceeded:
                    result["errors"].append("recovery_notice_budget_exhausted")
                except (OSError, ValueError, RuntimeError):
                    result["errors"].append("recovery_failure_notice_unverified")
        except SourceReadFailure as exc:
            # Route access was not established, so retain the explicit local
            # reason without sending into an unverified/private Thread.
            result["errors"].append(exc.code)
        except (OSError, ValueError, RuntimeError):
            # Do not reflect raw exception text (may include endpoints/tokens).
            result["errors"].append("recovery_delivery_unverified")
        finally:
            if schedule is not None:
                schedule.outcome(agent_pubkey, scheduling_operation, result["errors"][error_start:])
    if schedule is not None:
        result.update(schedule.status(operations))
    return result
