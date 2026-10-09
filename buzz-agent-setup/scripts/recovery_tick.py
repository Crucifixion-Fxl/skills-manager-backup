"""One bounded invocation receipt in the existing scheduler database.

The caller holds the RecoveryStore writer lease across begin, all work, and
completion. A running/failed receipt is evidence of uncertainty, not success.
No new task state, messages, credentials or execution authority is created.
"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote
import uuid

from audit_local_alignment import Auditor


KEY = "tick:last"
FIELDS = {"version", "origin", "invocation_id", "boot_id", "config_sha256", "phase", "started_ns", "finished_ns", "ok", "continued_events"}


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if str(uuid.UUID(value)) != value:
        raise ValueError("tick boot identity unverified")
    return value


def validate(record):
    if (not isinstance(record, dict) or set(record) != FIELDS
            or type(record["version"]) is not int or record["version"] != 1
            or record["origin"] not in {"systemd", "cli"}
            or not isinstance(record["invocation_id"], str)
            or re.fullmatch(r"[0-9a-f]{32}", record["invocation_id"]) is None
            or record["invocation_id"] == "0" * 32
            or not isinstance(record["config_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["config_sha256"]) is None
            or not isinstance(record["boot_id"], str) or str(uuid.UUID(record["boot_id"])) != record["boot_id"]
            or type(record["started_ns"]) is not int or not 0 < record["started_ns"] < 2 ** 63):
        raise ValueError("invalid tick receipt")
    events = record["continued_events"]
    if (not isinstance(events, list) or any(
            not isinstance(eid, str) or re.fullmatch(r"[0-9a-f]{64}", eid) is None for eid in events)
            or list(events) != sorted(set(events))):
        raise ValueError("invalid tick continued_events")
    if record["phase"] == "running":
        if record["finished_ns"] is not None or record["ok"] is not None or events:
            raise ValueError("running tick cannot claim completion")
    elif record["phase"] == "completed":
        if (type(record["finished_ns"]) is not int or not record["started_ns"] <= record["finished_ns"] < 2 ** 63
                or type(record["ok"]) is not bool):
            raise ValueError("invalid tick completion")
    else:
        raise ValueError("unknown tick phase")


def _encoded(record):
    validate(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _put(db, record):
    with db:
        db.execute("INSERT INTO scheduling_cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (KEY, _encoded(record)))


def begin_tick(db, config, invocation):
    record = dict(version=1, origin="systemd" if invocation is not None else "cli",
                  invocation_id=invocation if invocation is not None else uuid.uuid4().hex,
                  boot_id=boot_id(), config_sha256=config_digest(config), phase="running",
                  started_ns=time.monotonic_ns(), finished_ns=None, ok=None, continued_events=[])
    _put(db, record)
    return record


def complete_tick(db, record, result):
    if db.execute("SELECT value FROM scheduling_cursors WHERE key=?", (KEY,)).fetchone() != (_encoded(record),):
        raise ValueError("tick changed during controller round")
    # At most 8 routes/continues per round (RecoverySchedule), so this list is
    # always small; it is the durable proof an L3 oracle binds a continue to
    # by identity, not a time-window guess against a self-reported flag.
    continued_events = sorted({eid for agent in result["agents"].values() for eid in agent.get("continued_events", ())})
    value = {**record, "phase": "completed", "finished_ns": time.monotonic_ns(),
             "ok": not any(row["errors"] or row["retry_pending"] for row in result["agents"].values()),
             "continued_events": continued_events}
    _put(db, value)
    return value


def _metadata(fd, *, limit):
    value = os.fstat(fd)
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1 or value.st_size > limit):
        raise ValueError("tick database metadata unverified")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous tick JSON")
        result[key] = value
    return result


def read_tick(state_dir):
    """No creation, repair, reset, schema migration or writable connection."""
    db, descriptor, lock = None, None, None
    try:
        directory = Path(state_dir)
        if not directory.is_absolute() or not Auditor.trusted_private_directory(directory):
            raise ValueError("tick state directory unverified")
        path = directory / ".scheduler.sqlite3"
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        lock = os.open(str(path) + ".lock", flags)
        _metadata(lock, limit=0)
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        descriptor = os.open(path, flags)
        before = _metadata(descriptor, limit=16 * 1024 * 1024)
        db = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=1)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: 1, 20000)
        if (db.execute("PRAGMA journal_mode").fetchone() != ("delete",)
                or db.execute("SELECT type FROM sqlite_schema WHERE name='scheduling_cursors'").fetchall() != [("table",)]):
            raise ValueError("unexpected tick database schema")
        rows = db.execute("SELECT typeof(value),length(value),substr(value,1,4097) "
                          "FROM scheduling_cursors WHERE key=? LIMIT 2", (KEY,)).fetchall()
        if (len(rows) != 1 or rows[0][0] != "text" or not 1 <= rows[0][1] <= 4096
                or len(rows[0][2]) != rows[0][1]):
            raise ValueError("missing or oversized tick receipt")
        record = json.loads(rows[0][2], object_pairs_hook=_unique)
        validate(record)
        after = _metadata(descriptor, limit=16 * 1024 * 1024)
        linked = path.lstat()
        if (any(getattr(before, key) != getattr(after, key) for key in
                ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
                or (linked.st_dev, linked.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("tick database changed during readback")
        return record
    except (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error) as error:
        raise ValueError("recovery_tick_receipt_unverified") from error
    finally:
        if db is not None:
            db.close()
        for fd in (descriptor, lock):
            if fd is not None:
                os.close(fd)


def verify_tick(config, *, started_after):
    record = read_tick(config["state_dir"])
    if (type(started_after) is not int or started_after <= 0 or record["origin"] != "systemd"
            or record["phase"] != "completed" or record["ok"] is not True
            or record["config_sha256"] != config_digest(config) or record["boot_id"] != boot_id()
            or not started_after <= record["started_ns"] <= record["finished_ns"] <= time.monotonic_ns()):
        raise ValueError("recovery_tick_receipt_unverified")
    return record
