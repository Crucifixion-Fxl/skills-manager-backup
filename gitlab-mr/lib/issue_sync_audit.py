"""Live drive-audit of Issue submission receipts over the authoritative binding set (engineering/skills#61).

The active set comes from ``issue_binding.resolve``: every admitted or receipted Issue whose latest
binding action is not ``retire``. Each active Issue needs a fresh terminal receipt for the current
HEAD whose live re-read reproduces the same outcome under the history-resolved role; each retired
Issue is re-read live to prove its retirement still holds. Malformed history or payload shapes give
the structured ``ISSUE_SYNC_UNVERIFIED`` error, never an uncaught exception or a pass.

An injected ``verifier`` keeps the pre-binding call surface ``verifier(request)``: ``binding=`` is
passed only when the history holds an admission and the callback accepts it. A ``new-work`` admission
with an old callback is safe (its default role is new-work and the audit compares the admitted Issue
identity itself); a ``closed-reference`` admission with one is the distinct ``verifier_binding_unsupported``
error, never a silently dropped role. A ``ValueError`` (or subclass) raised inside the verifier keeps the
compatibility ``receipt request is invalid (<reason>)`` error; any other exception is reported as
``verifier_error (<exception type name>)``. Neither is reported as malformed history, and the callback
runs at most once per Issue.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from issue_binding import MalformedHistory, binding_for, issue_key, recheck_retirement, resolve
from issue_review_state import (
    NEW_WORK,
    PHASES,
    RECEIPT_SCHEMA,
    TERMINAL_STATES,
    _get,
    glab_reader,
    load_owner_host,
    verify,
)


def request_from_receipt(receipt: dict[str, Any], mr_project: str) -> dict[str, Any]:
    return {"issue_url": receipt.get("issue_url"), "phase": receipt.get("phase"), "mr_project": mr_project,
            "mr_iid": receipt.get("mr_iid"), "revision": receipt.get("revision"),
            "note_id": receipt.get("note_id"),
            "project_mode": _get(receipt, "review_state", "mode") or "canonical-label"}


def accepts_binding(verifier: Callable[..., Any]) -> bool:
    """Signature inspection only; the callback is never called to probe it."""
    try:
        parameters = inspect.signature(verifier).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind is p.VAR_KEYWORD or (p.name == "binding" and p.kind in (p.POSITIONAL_OR_KEYWORD,
                                                                              p.KEYWORD_ONLY))
               for p in parameters)


def audit_issue_sync(state: dict[str, Any], error: type[Exception],
                     verifier: Callable[..., dict[str, Any]] = verify,
                     reader: Callable[[str], Any] | None = None, host: str | None = None) -> list[dict[str, Any]]:
    """Fail closed unless every active Issue has a fresh terminal receipt for the current HEAD.

    Terminal means VERIFIED for an opened Issue, or NOT_APPLICABLE_CLOSED for a closed Issue admitted as
    ``closed-reference``; the live re-read must reproduce the same outcome and Issue state.
    """
    try:
        return _audit(state, error, verifier, reader, host if host is not None else load_owner_host())
    except error:
        raise
    except MalformedHistory as exc:
        raise error(f"ISSUE_SYNC_UNVERIFIED: malformed issue-sync history ({exc})") from None
    except (TypeError, AttributeError, KeyError, ValueError):
        raise error("ISSUE_SYNC_UNVERIFIED: malformed issue-sync history or response") from None


def _audit(state: dict[str, Any], error: type[Exception], verifier: Callable[..., dict[str, Any]],
           reader: Callable[[str], Any] | None, host: str | None) -> list[dict[str, Any]]:
    head, mr_iid, project = _get(state, "last_snapshot", "head_sha"), state.get("mr_iid"), state.get("project_path")
    history = state.get("history") if isinstance(state.get("history"), list) else []
    entries = resolve(history, mr_iid, project, host)
    current: dict[tuple[str, int], dict[str, Any]] = {}
    for index, item in enumerate(history):
        if (isinstance(item, dict) and item.get("kind") == "issue_sync" and item.get("phase") in PHASES
                and item.get("mr_iid") == mr_iid and item.get("revision") == head):
            key = issue_key(item.get("issue_url"), host)
            if index > entries[key]["since"]:
                current[key] = item  # the latest receipt per Issue after its latest binding action wins
    conflicts = sorted(entry["url"] for entry in entries.values() if entry["conflict"])
    if conflicts:
        raise error(f"ISSUE_SYNC_UNVERIFIED: binding_role_conflict for {', '.join(conflicts)}; a recorded role "
                    "cannot be re-tagged, and a receipt's role must match the admission in force when it was "
                    "recorded")
    active = {key: entry for key, entry in entries.items() if entry["status"] == "active"}
    if not any(key in active for key in current):
        raise error("ISSUE_SYNC_UNVERIFIED: no submitted/revised Issue receipt for the current HEAD")
    stale = sorted(entry["url"] for key, entry in active.items() if key not in current)
    if stale:
        raise error(f"ISSUE_SYNC_UNVERIFIED: receipt not refreshed for current HEAD: {', '.join(stale)}")
    for entry in (entry for entry in entries.values() if entry["status"] == "retired"):
        failed = recheck_retirement(entry, reader or glab_reader(host), str(project), mr_iid, host)
        if failed:
            raise error(f"ISSUE_SYNC_UNVERIFIED: {entry['url']} retirement no longer holds ({failed[0]}); "
                        "re-admit the binding and refresh its receipt")
    fresh_results = []
    for key, entry in active.items():
        stored, url, binding = current[key], current[key].get("issue_url"), binding_for(entry)
        if stored.get("mr_project") != project:
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} receipt is bound to another MR project")
        outcome = stored.get("result") if isinstance(stored.get("result"), str) else None
        if (stored.get("schema") != RECEIPT_SCHEMA or stored.get("source") != "live"
                or outcome not in TERMINAL_STATES or _get(stored, "review_state", "verdict") != outcome
                or stored.get("observed_state") != TERMINAL_STATES[outcome]):
            reason = _get(stored, "gap", "reason") or "receipt_not_verified"
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} receipt is {stored.get('result')!r} ({reason})")
        role = (_get(binding, "role") or "new-work", "admission" if binding else "default")
        if "binding_role" in stored and (stored.get("binding_role"), stored.get("binding_role_source")) != role:
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} receipt role {stored.get('binding_role')!r} does not match "
                        f"the recorded binding ({role[0]}, {role[1]}); re-run the verifier with --state")
        request = request_from_receipt(stored, str(project))
        if binding is not None and not accepts_binding(verifier) and binding["role"] != NEW_WORK:
            raise error(f"ISSUE_SYNC_UNVERIFIED: verifier_binding_unsupported for {url}: its {binding['role']} "
                        "admission needs a verifier that accepts binding=; use the default verify")
        try:
            fresh = (verifier(request, binding=binding) if binding is not None and accepts_binding(verifier)
                     else verifier(request))
        except ValueError as exc:
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} receipt request is invalid ({exc})") from None
        except Exception as exc:  # the injected verifier's own failure is not malformed history
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} verifier_error ({type(exc).__name__}); fix the "
                        "injected verifier") from exc
        if not isinstance(fresh, dict):
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} fresh readback is not an object")
        if fresh.get("result") not in TERMINAL_STATES or fresh.get("source") != "live":
            reason = _get(fresh, "gap", "reason") or "fresh_readback_failed"
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} fresh review-state readback failed ({reason})")
        if binding is not None and fresh.get("issue_id") != binding["issue_id"]:
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} fresh readback is not the admitted Issue")
        if (fresh.get("result"), fresh.get("observed_state")) != (outcome, stored.get("observed_state")):
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} Issue state changed since the recorded receipt "
                        f"({stored.get('observed_state')} -> {fresh.get('observed_state')}); re-run the verifier")
        if any(fresh.get(key) != stored.get(key) for key in ("issue_id", "note_id", "note_body_sha256")):
            raise error(f"ISSUE_SYNC_UNVERIFIED: {url} fresh readback differs from the recorded receipt")
        fresh_results.append(fresh)
    return fresh_results
