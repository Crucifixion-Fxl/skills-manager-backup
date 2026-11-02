"""Shared read-modify-write protocol for the gitlab-mr Driver state file (engineering/skills#61 F5).

Every legitimate writer (initializer, Issue receipt/binding producer, Driver-return and cleanup
events, Supervisor liveness snapshot, waivers, rejections, completion) goes through ``update`` or
``create``: one exclusive ``flock`` on the sidecar ``<state>.lock``, a re-read under that lock, a
binding check (MR IID / project / HEAD where applicable), the mutation, then an fsynced temp file
and ``os.replace`` with mode 0600. Unrelated history entries and snapshot fields are preserved.
HEAD-bound writers (receipt and binding admit/retire producers, ``complete``) require a ``last_snapshot``
object whose ``head_sha`` matches; ``snapshot`` initializes a missing/null one and rejects any other
non-object; event, postmerge, waiver and rejection writers neither read nor change it.
Callers do network reads before taking the lock; the lock covers only local file I/O.

Protected history kinds (``issue_sync`` receipts and ``issue_binding`` admission/retirement
records) can only be appended through their producer APIs, never through ``append_event``.
Merged/final Issue receipts are a separate, non-review kind ``issue_sync_postmerge`` written only by
``append_postmerge``: a fixed field set, validated before the lock, that records the parent's own
native readback. The writer does not read GitLab, so the record never proves merge, closure or
business acceptance and never counts as a submitted/revised review receipt.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

PROTECTED_KINDS = ("issue_sync", "issue_binding")
POSTMERGE_KIND = "issue_sync_postmerge"
POSTMERGE_SCHEMA = "gitlab-mr-issue-sync-postmerge/v1"
POSTMERGE_PHASES = ("merged", "final")
POSTMERGE_DISPOSITIONS = {"kept-open": "opened", "closed": "closed", "already-closed": "closed"}
POSTMERGE_FIELDS = {"kind", "phase", "issue_url", "revision", "note_id", "note_url", "disposition",
                    "observed_state", "next_action"}
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
LOCK_TIMEOUT_SECONDS = 10.0
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024


class StateError(ValueError):
    """A fixed, safe error code; never includes file content."""


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def locked(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold the exclusive sidecar lock shared by all state writers; always released on exit."""
    fd = os.open(lock_path(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() >= deadline:
                    raise StateError("state_lock_timeout") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_state(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise StateError("state_too_large")
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StateError("state_missing") from None
    except (OSError, UnicodeDecodeError):
        raise StateError("state_read_failed") from None
    except json.JSONDecodeError:
        raise StateError("state_not_json") from None
    if not isinstance(state, dict) or not isinstance(state.get("history"), list):
        raise StateError("state_history_missing")
    return state


def _write_atomic(path: Path, state: dict[str, Any]) -> None:
    data = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def require_binding(state: dict[str, Any], mr_iid: Any, project_path: Any, head_sha: Any = None) -> None:
    snapshot = state.get("last_snapshot")
    if (state.get("mr_iid") != mr_iid or state.get("project_path") != project_path
            or (head_sha is not None and not (isinstance(snapshot, dict) and snapshot.get("head_sha") == head_sha))):
        raise StateError("state_binding_mismatch")


def update(path: Path, mutate: Callable[[dict[str, Any]], Any], *, mr_iid: Any, project_path: Any,
           head_sha: Any = None) -> Any:
    """Locked re-read, binding check, mutation and atomic replace; returns ``mutate``'s result."""
    with locked(path):
        state = read_state(path)
        require_binding(state, mr_iid, project_path, head_sha)
        result = mutate(state)
        _write_atomic(path, state)
        return result


def create(path: Path, state: dict[str, Any]) -> None:
    """Initializer: write a fresh state, never replacing one that already holds history."""
    with locked(path):
        if path.exists():
            try:
                existing = read_state(path)
            except StateError:
                raise StateError("state_exists_unreadable") from None
            if existing.get("history"):
                raise StateError("state_exists_with_history")
        _write_atomic(path, state)


def _append(state: dict[str, Any], entry: dict[str, Any]) -> None:
    if not isinstance(state.get("history"), list):
        raise StateError("state_history_missing")
    state["history"].append(entry)


def append_protected(path: Path, entry: dict[str, Any], *, head_sha: Any,
                     check: Callable[[dict[str, Any]], None] | None = None) -> None:
    """Producer-only append for ``issue_sync`` / ``issue_binding``; ``check`` re-validates under the lock."""
    if entry.get("kind") not in PROTECTED_KINDS:
        raise StateError("not_a_protected_entry")

    def mutate(state: dict[str, Any]) -> None:
        if check is not None:
            check(state)
        _append(state, entry)
    update(path, mutate, mr_iid=entry.get("mr_iid"), project_path=entry.get("mr_project"), head_sha=head_sha)


def append_event(path: Path, event: Any, *, mr_iid: Any, project_path: Any) -> None:
    """Driver return, resume and post-merge cleanup events; protected kinds are rejected."""
    if not isinstance(event, dict) or not event:
        raise StateError("event_must_be_object")
    if event.get("kind") in (*PROTECTED_KINDS, POSTMERGE_KIND) or "schema" in event:
        raise StateError("protected_history_kind")
    if len(json.dumps(event, ensure_ascii=False)) > MAX_EVENT_BYTES:
        raise StateError("event_too_large")
    update(path, lambda state: _append(state, dict(event)), mr_iid=mr_iid, project_path=project_path)


def postmerge_record(payload: Any, *, mr_iid: Any, project_path: Any, host: str | None) -> dict[str, Any]:
    """Validate the caller's merged/final readback into one fixed-shape ``issue_sync_postmerge`` record."""
    from issue_review_state import parse_issue_url  # noqa: PLC0415  (lazy: keeps this module standalone)

    if not isinstance(payload, dict):
        raise StateError("postmerge_must_be_object")
    if payload.get("kind", POSTMERGE_KIND) != POSTMERGE_KIND:
        raise StateError("protected_history_kind")
    if set(payload) - POSTMERGE_FIELDS:
        raise StateError("unexpected_postmerge_field")
    phase, revision = payload.get("phase"), payload.get("revision")
    if phase not in POSTMERGE_PHASES:
        raise StateError("invalid_postmerge_phase")
    try:
        parse_issue_url(payload.get("issue_url"), host)
    except ValueError:
        raise StateError("untrusted_issue_url") from None
    if not isinstance(revision, str) or not (SHA_RE.fullmatch(revision)
                                             or (phase == "final" and DIGEST_RE.fullmatch(revision))):
        raise StateError("invalid_postmerge_revision")  # merged: exact merge SHA; final: SHA or artifact digest
    note_id = payload.get("note_id")
    if type(note_id) is not int or note_id <= 0:
        raise StateError("invalid_postmerge_note_id")
    if "note_url" in payload and payload["note_url"] != f"{payload['issue_url']}#note_{note_id}":
        raise StateError("invalid_postmerge_note_url")
    if not isinstance(payload.get("disposition"), str) or payload["disposition"] not in POSTMERGE_DISPOSITIONS:
        raise StateError("invalid_postmerge_disposition")
    if payload.get("observed_state") != POSTMERGE_DISPOSITIONS[payload["disposition"]]:
        raise StateError("postmerge_disposition_state_mismatch")
    action = payload.get("next_action")
    if action is not None and not (isinstance(action, str) and action.strip() and len(action) <= 500
                                   and "\n" not in action and "\r" not in action):
        raise StateError("invalid_postmerge_next_action")
    return {**payload, "kind": POSTMERGE_KIND, "schema": POSTMERGE_SCHEMA, "mr_iid": mr_iid,
            "mr_project": project_path, "source": "parent-readback", "native_verified_by_writer": False,
            "recorded_at_utc": datetime.now(UTC).isoformat()}


def append_postmerge(path: Path, payload: Any, *, mr_iid: Any, project_path: Any, host: str | None) -> dict[str, Any]:
    """Locked append of one merged/final Issue receipt; submitted/revised review kinds stay producer-only."""
    record = postmerge_record(payload, mr_iid=mr_iid, project_path=project_path, host=host)
    update(path, lambda state: _append(state, record), mr_iid=mr_iid, project_path=project_path)
    return record


def record_snapshot(path: Path, *, mr_iid: Any, project_path: Any, head_sha: str, pipeline_id: int,
                    unresolved_discussions: int) -> int:
    """Supervisor liveness: compare under the lock, reset or bump ``no_change_streak``; returns it.

    A missing or null ``last_snapshot`` is initialized; any other non-object is ``state_snapshot_malformed``
    (nothing written), never silently replaced."""
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise StateError("invalid_head_sha")
    def mutate(state: dict[str, Any]) -> int:
        previous = state.get("last_snapshot")
        if previous is None:
            previous = {}
        elif not isinstance(previous, dict):
            raise StateError("state_snapshot_malformed")
        observed = {"head_sha": head_sha, "pipeline_id": pipeline_id,
                    "unresolved_discussions": unresolved_discussions}
        unchanged = all(previous.get(key) == value for key, value in observed.items())
        streak = state.get("no_change_streak")
        state["no_change_streak"] = (streak + 1 if type(streak) is int and streak >= 0 else 1) if unchanged else 0
        state["last_snapshot"] = {**previous, **observed,
                                  "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return state["no_change_streak"]
    return update(path, mutate, mr_iid=mr_iid, project_path=project_path)


def append_waiver(path: Path, waiver: Any, *, mr_iid: Any, project_path: Any) -> None:
    """User-signed merge-integrity waiver; its content is judged by validate_drive_audit.py."""
    if not isinstance(waiver, dict) or not isinstance(waiver.get("head_sha"), str):
        raise StateError("waiver_must_be_object")

    def mutate(state: dict[str, Any]) -> None:
        waivers = state.setdefault("merge_integrity_waivers", [])
        if not isinstance(waivers, list):
            raise StateError("state_waivers_malformed")
        waivers.append(dict(waiver))
    update(path, mutate, mr_iid=mr_iid, project_path=project_path)


def record_rejection(path: Path, fingerprint: str, *, mr_iid: Any, project_path: Any) -> int:
    """Auditor integrity rejection: same fingerprint bumps ``count``, a new one resets to 1."""
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise StateError("fingerprint_required")

    def mutate(state: dict[str, Any]) -> int:
        current = state.get("merge_integrity_rejections")
        count = current.get("count") if isinstance(current, dict) else None
        same = isinstance(current, dict) and current.get("fingerprint") == fingerprint and type(count) is int
        state["merge_integrity_rejections"] = {"fingerprint": fingerprint, "count": count + 1 if same else 1}
        return state["merge_integrity_rejections"]["count"]
    return update(path, mutate, mr_iid=mr_iid, project_path=project_path)


def mark_completed(path: Path, *, mr_iid: Any, project_path: Any, head_sha: str) -> None:
    """Set ``completed`` only for the HEAD the audit ran against (after ``AUDIT PASS``)."""
    update(path, lambda state: state.update(completed=True), mr_iid=mr_iid, project_path=project_path,
           head_sha=head_sha)
