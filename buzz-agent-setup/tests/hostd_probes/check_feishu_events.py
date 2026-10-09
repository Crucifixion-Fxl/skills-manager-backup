#!/usr/bin/env python3
"""Validate Feishu probe observations against an explicit, run-scoped evidence manifest.

Legacy summary receipts cannot pass: each expected event must match the manifest's
run, time window, app, chat, and operation identifiers. This validates association,
not Feishu's cryptographic authenticity or an operator's claim that a fault was injected.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import stat
import uuid
import re
import sys
from pathlib import Path
from typing import Any

EVENTS = Path.home() / "skills/.worktree/.feishu-browser/events.jsonl"
MANIFEST = Path.home() / "skills/.worktree/.feishu-browser/manifest.json"
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "hostd" / "probes" / "feishu-events.json"
SCHEMA_VERSION = 2


class EvidenceError(ValueError):
    """The evidence contract is absent or malformed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def validate_row(row: dict[str, Any]) -> None:
    """Reject malformed nested data before it can enter matching or a redacted receipt."""
    _require(isinstance(row, dict), "event evidence contains a malformed record")
    numeric = {"schema_version", "t_recv", "create_time"}
    special = {"mentions", "operator_has_union_id", "member_app_ids", "member_ids", "callback_chat_id"}
    for key, value in row.items():
        if key not in numeric | special:
            _require(isinstance(value, str) and bool(value), "event evidence contains a malformed identifier")
    if "schema_version" in row:
        _require(type(row["schema_version"]) is int, "event schema_version must be an integer")
    if "t_recv" in row:
        _require(_finite_number(row["t_recv"]), "event receive time must be finite Unix seconds")
    if "create_time" in row:
        value = row["create_time"]
        _require(isinstance(value, (str, int, float)) and not isinstance(value, bool), "message create_time is malformed")
        try:
            valid_time = math.isfinite(float(value))
        except (ValueError, OverflowError):
            valid_time = False
        _require(valid_time, "message create_time must be finite")
    if "operator_has_union_id" in row:
        _require(type(row["operator_has_union_id"]) is bool, "card operator union-id presence must be boolean")
    for key in ("member_app_ids", "member_ids"):
        if key in row:
            _require(isinstance(row[key], list) and all(isinstance(v, str) and v for v in row[key]), "event member identifiers are malformed")
    if "mentions" in row:
        mentions = row["mentions"]
        _require(isinstance(mentions, list), "event mentions must be a list")
        for mention in mentions:
            _require(isinstance(mention, dict) and set(mention) <= {"id", "mentioned_type"}
                     and all(isinstance(value, str) and value for value in mention.values()),
                     "event mention contains malformed identifiers or unknown fields")

    if "actor_id_hash" in row or "actor_namespace" in row:
        namespace = row.get("actor_namespace")
        actor_hash = row.get("actor_id_hash", "")
        _require(re.fullmatch(r"[0-9a-f]{64}", actor_hash) is not None,
                 "reaction actor hash must be a canonical SHA-256")
        _require((namespace == "user-union" and row.get("operator_type") == "user" and "actor_app_id" not in row) or
                 (namespace == "bot-app" and row.get("operator_type") in {"app", "bot"} and bool(row.get("actor_app_id")) and
                  actor_hash == hashlib.sha256(f"bot-app\0{row['actor_app_id']}".encode("utf-8")).hexdigest()),
                 "reaction actor namespace and operator identity are inconsistent")


    provenance = {"callback_chat_id", "chat_resolution", "reaction_target_sha256"}
    if provenance & set(row):
        _require(provenance <= set(row) and row.get("chat_resolution") == "declared-target" and
                 row.get("type") in {"im.message.reaction.created_v1", "im.message.reaction.deleted_v1"} and
                 isinstance(row.get("chat_id"), str) and bool(row["chat_id"]) and
                 row.get("callback_chat_id") in ("", row["chat_id"]) and
                 re.fullmatch(r"[0-9a-f]{64}", row.get("reaction_target_sha256", "")) is not None,
                 "reaction target declaration provenance is malformed or contradictory")
        _require(row.get("actor_namespace") in {"user-union", "bot-app"} and
                 isinstance(row.get("action_time"), str) and re.fullmatch(r"[0-9]{1,16}", row["action_time"]),
                 "declared reaction requires exact actor and decimal-string action time")
    for key, namespace in (("union_id", "user-union"), ("open_id", "user-open"), ("user_id", "user-id")):
        hashed, named = "operator_" + key + "_hash", "operator_" + key + "_namespace"
        if hashed in row or named in row:
            expected = namespace if key == "union_id" else namespace + ":" + row.get("app_id", "")
            _require(row.get("operator_type") == "user" and row.get("actor_namespace") == "user-union" and
                     row.get(named) == expected and re.fullmatch(r"[0-9a-f]{64}", row.get(hashed, "")) is not None and
                     (key != "union_id" or row[hashed] == row.get("actor_id_hash")),
                     "reaction operator identifier namespace is inconsistent")
    if provenance & set(row) and row.get("operator_type") == "user":
        _require(row.get("operator_union_id_hash") == row.get("actor_id_hash"),
                 "declared user reaction requires the actual union identifier hash")


@contextmanager
def owned_output_parent(path: Path, *, private: bool):
    """Pin each directory without following symlinks, including every ancestor."""
    path = Path(path)
    _require(".." not in path.parts and bool(path.name), "unsafe probe output path")
    if not path.is_absolute():
        path = Path.cwd() / path
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in path.parts[1:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        meta = os.fstat(directory)
        _require(meta.st_uid == os.geteuid() and (not private or stat.S_IMODE(meta.st_mode) == 0o700),
                 "probe output directory must be owned by the current user and private when recording")
        yield directory, path.name
    finally:
        os.close(directory)


def write_owned(path: Path, payload: bytes, *, append: bool = False, private_parent: bool = True) -> None:
    """Write an owner-only artifact through pinned directory FDs; refuse symlinks and hardlinks."""
    with owned_output_parent(path, private=private_parent) as (directory, name):
        if append:
            fd = os.open(name, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                meta = os.fstat(fd)
                _require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid() and
                         stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1,
                         "probe append file must be an owner-only regular file without hardlinks")
                fcntl.flock(fd, fcntl.LOCK_EX)
                with os.fdopen(os.dup(fd), "ab") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                os.close(fd)
            return
        try:
            existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        _require(existing is None or (stat.S_ISREG(existing.st_mode) and existing.st_uid == os.geteuid() and existing.st_nlink == 1),
                 "probe output must be a regular file owned by the current user without links")
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

def _event_id(row: dict[str, Any]) -> str | None:
    return row.get("event_id") or (row.get("header") or {}).get("event_id")


def _validate_manifest(doc: dict[str, Any]) -> tuple[str, float, float, str, dict[str, str], list[dict], dict]:
    _require(isinstance(doc, dict), "manifest must be a JSON object")
    _require(doc.get("schema_version") == SCHEMA_VERSION,
             "unsupported evidence receipt; run the current probe and provide a schema_version 2 manifest")
    allowed = {"schema_version", "run_id", "window", "target", "required_events", "isolation", "max_connection_start_skew_s"}
    _require(set(doc) <= allowed, "manifest has unknown fields; regenerate it with the current probe")
    run_id = doc.get("run_id")
    window = doc.get("window")
    target = doc.get("target")
    _require(isinstance(window, dict) and isinstance(target, dict), "manifest window and target must be JSON objects")
    apps = target.get("apps")
    required = doc.get("required_events")
    isolation = doc.get("isolation")
    _require(isinstance(run_id, str) and bool(run_id), "manifest run_id is required")
    _require(set(window) == {"start", "end"} and isinstance(target, dict) and set(target) == {"chat_id", "apps"},
             "manifest window and target must use only the documented fields")
    _require(isinstance(window.get("start"), (int, float)) and not isinstance(window.get("start"), bool)
             and isinstance(window.get("end"), (int, float)) and not isinstance(window.get("end"), bool)
             and _finite_number(window["start"]) and _finite_number(window["end"]) and window["start"] < window["end"], "manifest requires a valid start/end window")
    _require(isinstance(target.get("chat_id"), str) and bool(target["chat_id"]), "manifest target chat_id is required")
    _require(isinstance(apps, dict) and len(apps) >= 2 and all(isinstance(k, str) and isinstance(v, str) and v for k, v in apps.items()),
             "manifest requires at least two named bot/app_id pairs")
    _require(isinstance(required, list) and required, "manifest required_events must list exact expected observations")
    _require(isinstance(isolation, dict) and set(isolation) == {"driver", "fault_id", "fault_bot", "survivor_bots", "post_fault_message_id"},
             "manifest isolation has missing or unknown fields")
    driver = isolation.get("driver")
    _require(isinstance(driver, dict) and set(driver) == {"driver_id", "readback_id", "receipt_path", "receipt_sha256"}
             and isinstance(driver.get("driver_id"), str) and driver["driver_id"]
             and isinstance(driver.get("readback_id"), str) and driver["readback_id"]
             and isinstance(driver.get("receipt_path"), str) and driver["receipt_path"]
             and isinstance(driver.get("receipt_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", driver["receipt_sha256"]),
             "manifest isolation requires a driver id, readback id, private receipt path, and receipt SHA-256")
    skew = doc.get("max_connection_start_skew_s", 5)
    _require(_finite_number(skew) and skew >= 0, "connection start skew must be finite and nonnegative")
    return run_id, float(window["start"]), float(window["end"]), target["chat_id"], apps, required, isolation



def validate_recorder_manifest(doc: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    """Validate the pre-run fields needed to record events; the final checker needs full expectations."""
    _require(isinstance(doc, dict), "manifest must be a JSON object")
    allowed = {"schema_version", "run_id", "window", "target", "required_events", "isolation", "max_connection_start_skew_s"}
    _require(set(doc) <= allowed, "manifest has unknown fields; use the current probe manifest format")
    _require(doc.get("schema_version") == SCHEMA_VERSION and isinstance(doc.get("run_id"), str) and doc["run_id"],
             "recorder requires a schema_version 2 manifest with run_id")
    window, target = doc.get("window"), doc.get("target")
    _require(isinstance(window, dict) and set(window) == {"start", "end"} and
             isinstance(window.get("start"), (int, float)) and not isinstance(window.get("start"), bool) and
             isinstance(window.get("end"), (int, float)) and not isinstance(window.get("end"), bool) and
             _finite_number(window["start"]) and _finite_number(window["end"]) and window["start"] < window["end"], "recorder manifest requires a planned start/end window")
    _require(isinstance(target, dict) and set(target) == {"chat_id", "apps"} and
             isinstance(target.get("chat_id"), str) and target["chat_id"] and isinstance(target.get("apps"), dict) and
             len(target["apps"]) >= 2 and all(isinstance(bot, str) and isinstance(app, str) and app for bot, app in target["apps"].items()),
             "recorder manifest requires target chat_id and bot/app_id pairs")
    return doc["run_id"], target["chat_id"], target["apps"]

def _in_scope(row: dict[str, Any], run_id: str, start: float, end: float) -> bool:
    try:
        timestamp = float(row["t_recv"])
    except (KeyError, TypeError, ValueError):
        return False
    return row.get("schema_version") == SCHEMA_VERSION and row.get("run_id") == run_id and start <= timestamp <= end


def evaluate(manifest: dict[str, Any], rows: list[dict[str, Any]], driver_receipt_bytes: bytes | None = None) -> dict[str, Any]:
    """Return a redacted receipt; fault isolation also requires a digest-bound driver artifact."""
    run_id, start, end, chat_id, apps, required, isolation = _validate_manifest(manifest)
    _require(isinstance(rows, list), "event evidence must be a JSON array of records")
    _require(all(isinstance(r, dict) for r in rows), "event evidence contains a malformed record; regenerate the run log")
    allowed_row_fields = {"schema_version", "run_id", "t_recv", "bot", "app_id", "session_id", "connection_id", "type",
                          "event_id", "chat_id", "callback_chat_id", "chat_resolution", "reaction_target_sha256",
                          "operator_union_id_hash", "operator_open_id_hash", "operator_user_id_hash",
                          "operator_union_id_namespace", "operator_open_id_namespace", "operator_user_id_namespace",
                          "message_id", "create_time", "mentions", "reaction_type", "actor_id_hash", "actor_namespace", "action_time", "operator_type", "actor_app_id",
                          "card_id", "action_id", "probe_marker", "operator_has_union_id", "target_app_id", "member_app_ids",
                          "member_ids", "fault_id", "driver_id", "readback_id", "receipt_sha256", "result"}
    _require(all(set(r) <= allowed_row_fields for r in rows), "event evidence contains unknown fields; rerun the current sanitized recorder")
    for row in rows:
        validate_row(row)
    scoped = [dict(r) for r in rows if _in_scope(r, run_id, start, end)]
    starts_for_match = {r.get("bot"): r for r in scoped if r.get("type") == "_connection_started" and r.get("app_id") == apps.get(r.get("bot"))}
    session_ends = {bot: min((float(r["t_recv"]) for r in scoped if r.get("type") == "_connection_closed"
                              and r.get("bot") == bot and r.get("app_id") == apps[bot]
                              and r.get("session_id") == started.get("session_id")), default=end)
                    for bot, started in starts_for_match.items()}

    def in_session(row: dict[str, Any]) -> bool:
        started = starts_for_match.get(row.get("bot"))
        return bool(started and row.get("app_id") == started.get("app_id")
                    and row.get("session_id") == started.get("session_id")
                    and float(started["t_recv"]) <= float(row["t_recv"]) < session_ends[row["bot"]])

    # SDK reactions contain no chat_id. Resolve it only from an earlier received
    # message on the same authenticated app, run and still-active session.
    for reaction in scoped:
        if reaction.get("chat_resolution") == "declared-target":
            # The Root-pinned manifest below must explicitly match this grant.
            # This generic checker does not certify the Source owner admission.
            continue
        if reaction.get("type") != "im.message.reaction.created_v1":
            continue
        associations = [r for r in scoped if r.get("type") == "im.message.receive_v1" and in_session(r)
                        and r.get("bot") == reaction.get("bot") and r.get("app_id") == reaction.get("app_id")
                        and r.get("session_id") == reaction.get("session_id")
                        and r.get("message_id") == reaction.get("message_id") and r.get("event_id")
                        and float(r["t_recv"]) <= float(reaction["t_recv"]) and r.get("chat_id")]
        associated_chats = {r["chat_id"] for r in associations}
        claimed_chat = reaction.pop("chat_id", None)
        if len(associated_chats) == 1 and (claimed_chat is None or claimed_chat in associated_chats):
            reaction["chat_id"] = next(iter(associated_chats))
    checks: dict[str, bool] = {
        "recorder_accepted_all_callbacks": not any(r.get("type") in {"_event_rejected", "_recorder_failed"} for r in scoped)
    }
    evidence: list[dict[str, Any]] = []

    def matching(expected: dict[str, Any]) -> dict[str, Any] | None:
        bot = expected.get("bot")
        app_id = expected.get("app_id")
        for row in scoped:
            if row.get("type") != expected.get("type") or row.get("bot") != bot or row.get("app_id") != app_id:
                continue
            if not in_session(row):
                continue
            if row.get("chat_id") != expected.get("chat_id", chat_id):
                continue
            if row.get("chat_resolution") == "declared-target" and expected.get("reaction_target_sha256") != row.get("reaction_target_sha256"):
                continue
            if expected.get("event_id") != _event_id(row):
                continue
            fields = ("reaction_target_sha256", "message_id", "card_id", "action_id", "probe_marker", "reaction_type", "actor_id_hash", "actor_namespace", "action_time", "operator_type", "actor_app_id", "target_app_id", "fault_id", "session_id", "operator_has_union_id")
            if any(field in expected and row.get(field) != expected[field] for field in fields):
                continue
            if "mentioned_type" in expected or "mention_id" in expected:
                mentions = row.get("mentions") or []
                if not any(("mentioned_type" not in expected or mention.get("mentioned_type") == expected["mentioned_type"])
                           and ("mention_id" not in expected or mention.get("id") == expected["mention_id"])
                           for mention in mentions if isinstance(mention, dict)):
                    continue
            return row
        return None

    failed_events = []
    expected_fields = {"scenario", "bot", "app_id", "type", "event_id", "chat_id", "message_id", "card_id",
                       "action_id", "probe_marker", "reaction_operation_id", "reaction_target_sha256", "reaction_type", "actor_id_hash", "actor_namespace", "action_time", "operator_type", "actor_app_id", "target_app_id", "operator_has_union_id", "mentioned_type", "mention_id"}
    for i, expected in enumerate(required):
        _require(isinstance(expected, dict), "manifest required_events contains an invalid entry")
        _require(set(expected) <= expected_fields, "manifest required event has unknown fields; regenerate it with the current probe")
        _require(all((type(value) is bool if key == "operator_has_union_id" else isinstance(value, str) and bool(value))
                     for key, value in expected.items()), "manifest required event contains malformed identifiers")
        _require(isinstance(expected.get("type"), str) and expected["type"], "required event type is missing")
        _require(expected.get("bot") in apps and apps[expected.get("bot")] == expected.get("app_id"),
                 "manifest required event does not map to a declared bot/app_id")
        _require(expected.get("chat_id", chat_id) == chat_id, "manifest event targets a chat other than target.chat_id")
        _require(isinstance(expected.get("event_id"), str) and expected["event_id"], "every required event must name its exact event_id")
        etype = expected.get("type")
        id_field = {"im.message.receive_v1": "message_id", "im.message.reaction.created_v1": "reaction_operation_id",
                    "card.action.trigger": "action_id", "im.chat.member.bot.added_v1": "target_app_id"}.get(etype)
        _require(id_field is None or (isinstance(expected.get(id_field), str) and expected[id_field]),
                 f"required {etype} event must name its exact {id_field}")
        if etype == "card.action.trigger":
            _require(isinstance(expected.get("card_id"), str) and expected["card_id"] and
                     isinstance(expected.get("message_id"), str) and expected["message_id"] and
                     isinstance(expected.get("probe_marker"), str) and expected["probe_marker"],
                     "required card callback must name card_id, message_id, and the card value marker")
        if etype == "im.message.reaction.created_v1":
            _require(all(isinstance(expected.get(k), str) and expected[k] for k in
                         ("reaction_operation_id", "message_id", "reaction_type", "actor_id_hash", "actor_namespace", "operator_type", "action_time")),
                     "required reaction must bind operation, message, emoji, canonical actor namespace/hash, operator type, and action time")
            namespace = expected["actor_namespace"]
            _require(re.fullmatch(r"[0-9a-f]{64}", expected["actor_id_hash"]) is not None and
                     ((namespace == "user-union" and expected["operator_type"] == "user" and "actor_app_id" not in expected) or
                      (namespace == "bot-app" and expected["operator_type"] in {"app", "bot"} and bool(expected.get("actor_app_id")))),
                     "required reaction actor must use user-union or bot-app canonical identity")
        if etype == "im.chat.member.bot.added_v1":
            _require(isinstance(expected.get("target_app_id"), str) and expected["target_app_id"],
                     "required bot-added event must name its exact target_app_id")
        if etype == "im.message.receive_v1" and expected.get("mentioned_type") == "bot":
            _require(isinstance(expected.get("mention_id"), str) and expected["mention_id"],
                     "required bot mention must name its exact mention_id")
        observed = matching(expected)
        checks[f"required_event_{i + 1}_matched"] = observed is not None
        if observed is None:
            failed_events.append({k: expected.get(k) for k in ("bot", "app_id", "type", "event_id", "chat_id", "message_id", "card_id", "action_id") if k in expected})
        else:
            projection = {k: observed.get(k) for k in ("type", "bot", "app_id", "event_id", "chat_id", "message_id", "card_id", "action_id", "probe_marker", "reaction_type", "actor_id_hash", "actor_namespace", "operator_type", "actor_app_id", "action_time", "target_app_id", "operator_has_union_id", "t_recv") if observed.get(k) is not None}
            if expected.get("scenario"):
                projection["scenario"] = expected["scenario"]
            if "reaction_operation_id" in expected:
                projection["reaction_operation_id"] = expected["reaction_operation_id"]
            if observed.get("mentions"):
                projection["mentions"] = [{"id": m.get("id"), "mentioned_type": m.get("mentioned_type")}
                                           for m in observed["mentions"] if isinstance(m, dict)]
            evidence.append(projection)

    start_rows = [r for r in scoped if r.get("type") == "_connection_started" and r.get("app_id") == apps.get(r.get("bot"))]
    starts = {r.get("bot"): r for r in start_rows}
    intervals = {}
    sessions_valid = (len(start_rows) == len(apps) and set(starts) == set(apps) and
                      len({r.get("session_id") for r in start_rows}) == len(apps))
    for bot, row in starts.items():
        session_id = row.get("session_id")
        end_rows = [r for r in scoped if r.get("type") == "_connection_closed" and r.get("bot") == bot
                    and r.get("app_id") == apps.get(bot) and r.get("session_id") == session_id]
        required_times = [float(r["t_recv"]) for r in scoped if r.get("bot") == bot and
                          any(e.get("bot") == bot and e.get("event_id") == _event_id(r) and e.get("type") == r.get("type") for e in required)]
        session_end = min((float(r["t_recv"]) for r in end_rows), default=end)
        intervals[bot] = (float(row["t_recv"]), session_end)
        sessions_valid = sessions_valid and isinstance(session_id, str) and bool(session_id)
        sessions_valid = sessions_valid and all(float(row["t_recv"]) <= t < session_end for t in required_times)
    all_apps = set(apps)
    overlap = max((v[0] for v in intervals.values()), default=end + 1) < min((v[1] for v in intervals.values()), default=start)
    starts_within_skew = (max((v[0] for v in intervals.values()), default=end + 1) -
                          min((v[0] for v in intervals.values()), default=start)) <= float(manifest.get("max_connection_start_skew_s", 5))
    checks["simultaneous_app_connections"] = sessions_valid and overlap and starts_within_skew

    fault_id = isolation.get("fault_id")
    fault_bot = isolation.get("fault_bot")
    survivors = isolation.get("survivor_bots")
    post_fault_message = isolation.get("post_fault_message_id")
    _require(isinstance(fault_id, str) and fault_bot in apps and isinstance(survivors, list)
             and survivors and fault_bot not in survivors and set(survivors) <= all_apps
             and isinstance(post_fault_message, str) and post_fault_message,
             "manifest isolation must name fault_id, fault_bot, survivor_bots, and post_fault_message_id")
    driver = isolation["driver"]
    _require(isinstance(driver_receipt_bytes, bytes) and hashlib.sha256(driver_receipt_bytes).hexdigest() == driver["receipt_sha256"],
             "fault driver receipt is missing or its SHA-256 does not match the manifest")
    try:
        driver_receipt = json.loads(driver_receipt_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise EvidenceError("fault driver receipt is malformed; rerun the isolated driver and preserve its owner-only readback") from None
    expected_driver_receipt = {"schema_version": 1, "run_id": run_id, "fault_id": isolation["fault_id"],
                               "driver_id": driver["driver_id"], "readback_id": driver["readback_id"], "result": "failed"}
    _require(driver_receipt == expected_driver_receipt, "fault driver receipt identifiers or failure result do not match the manifest")
    injection = next((r for r in scoped if r.get("type") == "_fault_injected" and r.get("fault_id") == fault_id
                      and r.get("bot") == fault_bot and r.get("app_id") == apps[fault_bot]
                      and r.get("driver_id") == driver["driver_id"]), None)
    driver_readback = next((r for r in scoped if r.get("type") == "_fault_driver_readback" and r.get("fault_id") == fault_id
                            and r.get("bot") == fault_bot and r.get("app_id") == apps[fault_bot]
                    and r.get("driver_id") == driver["driver_id"] and r.get("readback_id") == driver["readback_id"]
                    and r.get("receipt_sha256") == driver["receipt_sha256"]
                            and injection is not None and float(r["t_recv"]) >= float(injection["t_recv"])
                            and r.get("result") == "failed"), None)
    failure = next((r for r in scoped if r.get("type") == "_app_failed" and r.get("fault_id") == fault_id
                    and r.get("bot") == fault_bot and r.get("app_id") == apps[fault_bot]
                    and r.get("driver_id") == driver["driver_id"] and r.get("readback_id") == driver["readback_id"]
                    and r.get("receipt_sha256") == driver["receipt_sha256"]
                    and injection is not None and driver_readback is not None and
                    float(r["t_recv"]) >= max(float(injection["t_recv"]), float(driver_readback["t_recv"]))), None)
    survivors_delivered = bool(injection and failure and driver_readback)
    survivor_ids = []
    if injection and failure:
        for bot in survivors:
            session_start = starts_for_match.get(bot)
            row = next((r for r in scoped if r.get("type") == "im.message.receive_v1" and r.get("bot") == bot
                        and r.get("app_id") == apps[bot] and r.get("chat_id") == chat_id
                        and session_start is not None and r.get("session_id") == session_start.get("session_id")
                        and bot in intervals and intervals[bot][0] <= float(r["t_recv"]) < intervals[bot][1]
                        and isinstance(r.get("event_id"), str) and bool(r["event_id"])
                        and r.get("message_id") == post_fault_message and float(r["t_recv"]) > float(failure["t_recv"])), None)
            if row is None:
                survivors_delivered = False
            else:
                survivor_ids.append(_event_id(row))
                evidence.append({k: row.get(k) for k in ("type", "bot", "app_id", "event_id", "chat_id", "message_id", "t_recv") if row.get(k) is not None})
    checks["fault_isolated_to_one_app"] = survivors_delivered and len(survivor_ids) == len(survivors)
    if injection:
        evidence.append({"type": "_fault_injected", "bot": fault_bot, "app_id": apps[fault_bot], "fault_id": fault_id, "t_recv": injection.get("t_recv")})
    if driver_readback:
        evidence.append({"type": "_fault_driver_readback", "bot": fault_bot, "app_id": apps[fault_bot],
                         "fault_id": fault_id, "driver_id": driver["driver_id"],
                         "readback_id": driver["readback_id"], "receipt_sha256": driver["receipt_sha256"],
                         "result": "failed", "t_recv": driver_readback.get("t_recv")})
    if failure:
        evidence.append({"type": "_app_failed", "bot": fault_bot, "app_id": apps[fault_bot], "fault_id": fault_id,
                         "driver_id": driver["driver_id"], "readback_id": driver["readback_id"], "t_recv": failure.get("t_recv")})

    expected_receives = [e for e in required if e.get("type") == "im.message.receive_v1" and e.get("scenario") == "P0-1"]
    checks["P0-1_every_declared_app_received_exact_message"] = (set(e.get("bot") for e in expected_receives) == set(apps) and
        len({e.get("message_id") for e in expected_receives}) == 1 and
        all(any(r.get("type") == "im.message.receive_v1" and r.get("bot") == e.get("bot") and
                r.get("app_id") == e.get("app_id") and r.get("message_id") == e.get("message_id") and
                r.get("event_id") == e.get("event_id") and r.get("chat_id") == chat_id for r in scoped) for e in expected_receives))
    card_events = [e for e in required if e.get("type") == "card.action.trigger" and e.get("scenario") == "P0-3"]
    checks["P0-3_exact_card_action_callback"] = bool(card_events) and all(checks.get(f"required_event_{required.index(e) + 1}_matched", False) for e in card_events)
    checks["P0-3_card_value_roundtrip"] = bool(card_events) and all(e.get("probe_marker") and checks.get(f"required_event_{required.index(e) + 1}_matched", False) for e in card_events)
    checks["P0-3_card_operator_has_union_id"] = bool(card_events) and all(e.get("operator_has_union_id") is True for e in card_events)
    added = [e for e in required if e.get("type") == "im.chat.member.bot.added_v1" and e.get("scenario") == "P0-4"]
    checks["P0-4_only_added_bot_received_event"] = bool(added) and all(
        checks.get(f"required_event_{required.index(e) + 1}_matched", False) and
        e.get("target_app_id") == apps.get(e.get("bot")) and
        not any(r.get("type") == e.get("type") and r.get("chat_id") == chat_id and r.get("bot") != e.get("bot")
                and r.get("event_id") == e.get("event_id") for r in scoped) for e in added)
    reactions = [e for e in required if e.get("type") == "im.message.reaction.created_v1" and e.get("scenario") == "P0-5"]
    reaction_operations = {tuple(e.get(k) for k in ("reaction_operation_id", "message_id", "reaction_type", "actor_id_hash", "actor_namespace", "operator_type", "actor_app_id", "action_time")) for e in reactions}
    checks["P0-5_same_reaction_seen_by_every_app"] = bool(reactions) and set(e.get("bot") for e in reactions) == set(apps) and len(reaction_operations) == 1 and all(checks.get(f"required_event_{required.index(e) + 1}_matched", False) for e in reactions)
    mentions = [e for e in required if e.get("type") == "im.message.receive_v1" and e.get("scenario") == "P0-6"]
    checks["P0-6_message_names_bot_mention"] = bool(mentions) and all(e.get("mentioned_type") == "bot" and checks.get(f"required_event_{required.index(e) + 1}_matched", False) for e in mentions)

    lags = []
    latency_valid = bool(expected_receives)
    for expected in expected_receives:
        row = matching(expected)
        if row is None or "create_time" not in row:
            latency_valid = False
            continue
        try:
            created = float(row["create_time"])
            created = created / (1e6 if created > 1e14 else 1e3 if created > 1e11 else 1)
            lag = float(row["t_recv"]) - created
            if not math.isfinite(lag) or not 0 <= lag < 2:
                latency_valid = False
            if math.isfinite(lag):
                lags.append(lag)
        except (TypeError, ValueError, OverflowError):
            latency_valid = False
    checks["P0-1_receive_lag_under_2s"] = latency_valid and len(lags) == len(expected_receives)
    failed_checks = sorted(name for name, ok in checks.items() if not ok)

    return {
        "schema_version": SCHEMA_VERSION,
        "probe": "hostd-feishu-events",
        "run_id": run_id,
        "window": {"start": start, "end": end},
        "target_chat_id": chat_id,
        "target_apps": apps,
        "receive_lag_s_max": round(max(lags), 3) if lags else None,
        "evidence": evidence,
        "matched_required_events": len(required) - len(failed_events),
        "missing_required_events": failed_events,
        "checks": checks,
        "failed_checks": failed_checks,
        "ok": not failed_checks,
        "provenance": "event_recorder schema v2; identifiers retained; bodies and secret fields omitted",
        "guidance": None if not failed_checks else {
            "remedy": "Create a fresh manifest for the intended test chat, repeat the missing probe action during its window, and rerun this checker.",
            "copy_to_ai": "Help me rerun the hostd Feishu probe for this manifest, verify each listed event ID in the target test chat, and diagnose the failed checks without exposing message bodies or credentials.",
        },
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        scripts = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from hostd.safety import read_owned
        return [json.loads(line) for line in read_owned(path, max_bytes=32 * 1024 * 1024).decode("utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise EvidenceError("cannot safely read current probe event file; verify the owner-only run output and retry") from exc


def _write_receipt(receipt: dict[str, Any], output: Path) -> None:
    payload = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    write_owned(output, payload, private_parent=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check run-scoped Feishu probe evidence")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--events", type=Path, default=EVENTS)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--driver-receipt", type=Path, help="owner-only driver readback JSON bound by manifest SHA-256")
    args = parser.parse_args(argv)
    try:
        scripts = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from hostd.safety import read_owned
        manifest = json.loads(read_owned(args.manifest).decode("utf-8"))
        _validate_manifest(manifest)
        driver_path = args.driver_receipt or Path(manifest["isolation"]["driver"]["receipt_path"])
        driver_bytes = read_owned(driver_path)
        receipt = evaluate(manifest, _load_rows(args.events), driver_bytes)
    except EvidenceError as exc:
        print(f"Probe evidence rejected: {exc}. Remedy: create a fresh schema_version 2 run manifest and sanitized event log.\n"
              "Copy to AI: Help me create a current hostd probe manifest and rerun its event recorder and checker without exposing message bodies or credentials.", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
        print("Probe evidence rejected: manifest, event log, or private fault readback is missing or unreadable. Remedy: recreate the current run artifacts and verify owner-only permissions.\n"
              "Copy to AI: Help me create a current hostd probe manifest and rerun the sanitized recorder and checker without exposing credentials.", file=sys.stderr)
        return 2
    try:
        _write_receipt(receipt, args.output)
    except (OSError, ValueError):
        print("Probe receipt output rejected: verify its owner and remove symlinks from the output path. Remedy: use a fresh owner-only output file.\nCopy to AI: Help me safely save the sanitized hostd probe receipt.", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
