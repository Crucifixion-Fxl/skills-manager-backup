"""Per-Issue binding admission, role and controlled retirement for gitlab-mr (engineering/skills#61 F1/F4).

``kind=issue_binding`` history records are produced only by ``record_issue_binding.py`` from one live
GET set (Issue, MR, related MRs) and carry the lifecycle-binding identity of
``gitlab-issue-sop/references/lifecycle-binding.md`` (reusing ``check_artifact_binding.py``) plus the
declared role:

- ``new-work``: the MR delivers this Issue; admission requires a live ``opened``, assigned Issue.
- ``closed-reference``: release-flowback reference to already delivered work; the Issue may be
  ``opened`` or ``closed`` at admission. Only this role can reach ``NOT_APPLICABLE_CLOSED``.

The first role recorded for an Issue in this state is fixed: a later admission with another role, or a
``closed-reference`` admission after any new-work receipt (explicit or legacy/default), is a
``binding_role_conflict``. Role evidence is resolved chronologically: a receipt's own
``binding_role``/``binding_role_source`` fields are never evidence. A receipt may carry an admission
role only when a matching admission is in force at its history index; any other combination (an
admission claim without one, or a default/other-role receipt after one) is a conflict, and a receipt
that observed the Issue ``opened`` is new-work evidence unless it is backed by a ``closed-reference``
admission. A terminal receipt contradicted by its own ``observed_state`` or verdict (``VERIFIED`` but
``closed``, ``NOT_APPLICABLE_CLOSED`` but ``opened``) is also a conflict, a consistency check only.
Record shape checks (lifecycle identity, ``source="live"``) only reject malformed or
hand-made records; they do not authenticate caller intent, and fully fabricated local history stays
the documented caller bypass.

The active set is every admitted or receipted Issue whose latest binding action is not ``retire``;
omitting an Issue never retires it. Retirement is an explicit, reasoned record that requires fresh
native identity and a current MR description that no longer references the Issue in any form
(canonical/closing line, URL variant including sub-paths, ``#iid`` or ``group/project#iid``); historical cross-reference
notes may remain, so ``native_unlink`` is always false.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from issue_review_state import (
    CLOSED_REF_ROLE,
    NEW_WORK,
    PARENT,
    PHASES,
    RELATED_PAGE,
    REPOSITORY,
    ROLES,
    SHA,
    TERMINAL_STATES,
    ReadFailed,
    _get,
    _positive,
    glab_reader,
    load_owner_host,
    parse_issue_url,
)

BINDING_SCHEMA = "gitlab-mr-issue-binding/v1"
LIFECYCLE_CONTRACT = "issue-lifecycle/v2"
ACTIONS = {"admit": "ADMITTED", "retire": "RETIRED"}
SOP_SCRIPTS = Path(__file__).resolve().parents[3] / "collaboration/gitlab-issue-sop/scripts"
DECLARATION = re.compile(r"^[\s>*_|-]*(?:root\s+)?(?:issues?|work[\s_-]*items?)[*_\s]*[:：|]", re.IGNORECASE)
CLOSING = re.compile(r"\b(?:clos(?:e|es|ed|ing)|fix(?:es|ed|ing)?|resolv(?:e|es|ed|ing)|implement(?:s|ed|ing)?)\b",
                     re.IGNORECASE)
URL_TOKEN = re.compile(r"https?://([^/\s<>()\[\]|`'\"]+)(/[^\s<>()\[\]|`'\"]*)?", re.IGNORECASE)
URL_PATH = re.compile(r"/(.+?)(?:/-)?/(?:issues|work_items)/([1-9][0-9]*)(?:/.*)?")  # detection: sub-paths count
SHORT_REF = re.compile(r"(?<![\w/.#&-])((?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*[A-Za-z0-9_][A-Za-z0-9_.-]*)?#([1-9][0-9]*)"
                       r"(?![0-9])")
LIFECYCLE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
BINDING_GAPS = {
    "binding_role_conflict": "the role recorded first for this Issue is fixed; a role change needs a new MR "
                             "and drive state, never a re-tag to reach NOT_APPLICABLE_CLOSED",
    "binding_still_declared": "remove the Issue line from the MR description first; a still-current "
                              "canonical binding cannot be retired",
    "mr_binding_context_ambiguous": "remove every remaining reference to this Issue (URL, #iid, "
                                    "group/project#iid, closing keyword) from the MR description first; "
                                    "historical notes may stay",
    "binding_not_active": "only an admitted or receipted, not yet retired Issue can be retired",
    "binding_retired": "re-admit the Issue before recording a new receipt for it",
}


class MalformedHistory(ValueError):
    """A protected history record has an unusable shape; fixed code only."""


def issue_key(url: Any, host: str | None) -> tuple[str, int]:
    repo, iid = parse_issue_url(url, host)
    return repo.lower(), iid


def _self_contradictory(receipt: dict[str, Any]) -> bool:
    """A terminal outcome that its own ``observed_state``/verdict contradicts (consistency only, not auth).

    The verifier only emits ``VERIFIED`` with ``observed_state=opened`` and ``NOT_APPLICABLE_CLOSED`` with
    ``closed``, and a terminal ``result`` always equals ``review_state.verdict``; a failed receipt never
    pairs a terminal verdict with the opposite observed state. Non-string shapes are never looked up."""
    result, review = receipt.get("result"), receipt.get("review_state")
    result = result if isinstance(result, str) else None
    verdict = review.get("verdict") if isinstance(review, dict) else None
    verdict = verdict if isinstance(verdict, str) else None
    observed = receipt.get("observed_state")
    if result in TERMINAL_STATES:
        return observed != TERMINAL_STATES[result] or verdict != result
    return verdict in TERMINAL_STATES and observed in ("opened", "closed") and observed != TERMINAL_STATES[verdict]


def receipt_evidence(receipt: dict[str, Any], entry: dict[str, Any]) -> tuple[bool, bool]:
    """(consistent, new_work_evidence) of a receipt at its history index, never from its own claim.

    With an admission in force the receipt must carry exactly that admitted role (a default receipt is
    also consistent with a ``new-work`` admission); without one it must be default or legacy. A terminal
    receipt whose own ``observed_state``/verdict contradicts its result is inconsistent too, so editing
    one field cannot erase new-work evidence. A receipt that observed the Issue ``opened`` is new-work
    evidence unless a ``closed-reference`` admission in force backs it; a closed observation alone
    proves nothing (it may still be a real reference)."""
    claim = (receipt.get("binding_role"), receipt.get("binding_role_source"))
    default = claim in ((NEW_WORK, "default"), (None, None))
    if entry["admission"] is not None:
        consistent = claim == (entry["role"], "admission") or (entry["role"] == NEW_WORK and default)
    else:
        consistent = default
    consistent = consistent and not _self_contradictory(receipt)
    backed_reference = consistent and entry["admission"] is not None and entry["role"] == CLOSED_REF_ROLE
    return consistent, receipt.get("observed_state") == "opened" and not backed_reference


def _identity_ok(item: dict[str, Any], key: tuple[str, int], host: str | None) -> bool:
    """The record carries the issue-lifecycle/v2 identity of the same Issue it names (shape only)."""
    binding, admit = item.get("binding"), item.get("action") == "admit"
    if not isinstance(binding, dict):
        return False
    state, assignees = binding.get("state"), binding.get("assignee_ids")
    return (binding.get("contract_version") == LIFECYCLE_CONTRACT and binding.get("gitlab_host") == host
            and binding.get("issue_url") == item.get("issue_url") and binding.get("issue_iid") == key[1]
            and item.get("issue_iid") == key[1] and _positive(binding.get("project_id"))
            and binding.get("project_id") == item.get("issue_project_id") and state in ("opened", "closed")
            and isinstance(binding.get("snapshot_digest"), str) and bool(LIFECYCLE_DIGEST.fullmatch(
                binding["snapshot_digest"]))
            and isinstance(assignees, list) and all(_positive(a) for a in assignees)
            and (not admit or (bool(assignees) and (state == "opened" or item.get("binding_role") == CLOSED_REF_ROLE))))


def _check_record(item: dict[str, Any], key: tuple[str, int], mr_iid: Any, mr_project: Any,
                  host: str | None) -> None:
    action = item.get("action")
    if (not isinstance(action, str) or action not in ACTIONS or item.get("result") != ACTIONS[action]
            or item.get("schema") != BINDING_SCHEMA
            or item.get("source") != "live" or item.get("errors") != [] or item.get("binding_role") not in ROLES
            or item.get("mr_iid") != mr_iid or item.get("mr_project") != mr_project
            or not _positive(item.get("mr_project_id"))
            or not _positive(item.get("issue_id")) or not isinstance(item.get("revision"), str)
            or not SHA.fullmatch(item["revision"]) or item.get("native_unlink") is not False
            or not _identity_ok(item, key, host)
            or (action == "admit" and item.get("native_relation_listed") is not True)
            or (action == "retire" and (item.get("mr_declares_issue") is not False
                                        or not (isinstance(item.get("reason"), str) and item["reason"].strip())))):
        raise MalformedHistory("malformed_binding_record")


def resolve(history: Any, mr_iid: Any, mr_project: Any, host: str | None) -> dict[tuple[str, int], dict[str, Any]]:
    """Fold the append-only history into one entry per Issue key (pure; no reads)."""
    entries: dict[tuple[str, int], dict[str, Any]] = {}
    for index, item in enumerate(history if isinstance(history, list) else []):
        if not isinstance(item, dict) or item.get("kind") not in ("issue_binding", "issue_sync"):
            continue
        if item.get("kind") == "issue_sync" and (item.get("phase") not in PHASES or item.get("mr_iid") != mr_iid):
            continue
        try:
            key = issue_key(item.get("issue_url"), host)
        except ValueError:
            raise MalformedHistory(f"malformed_{item['kind']}_issue_url") from None
        entry = entries.setdefault(key, {"url": item["issue_url"], "role": None, "status": "active",
                                         "admission": None, "retirement": None, "issue_id": None,
                                         "conflict": False, "receipted": False, "new_work_evidence": False,
                                         "since": -1})
        if item["kind"] == "issue_sync":
            if entry["status"] != "active":
                continue  # a receipt after retirement does not re-activate the binding
            entry["receipted"] = True
            consistent, new_work = receipt_evidence(item, entry)
            entry["conflict"] |= not consistent  # a role claim not backed at this index never launders
            entry["new_work_evidence"] |= new_work
            if entry["issue_id"] is None and _positive(item.get("issue_id")):
                entry["issue_id"] = item["issue_id"]
            continue
        _check_record(item, key, mr_iid, mr_project, host)
        entry["since"] = index  # receipts before the latest admit/retire no longer count as current
        if entry["issue_id"] not in (None, item["issue_id"]):
            entry["conflict"] = True
        entry["issue_id"] = item["issue_id"]
        if item["action"] == "retire":
            entry.update(status="retired", retirement=item)
            continue
        role = item["binding_role"]
        if (entry["role"] not in (None, role)
                or (role != NEW_WORK and entry["new_work_evidence"])):
            entry["conflict"] = True
        entry.update(role=entry["role"] or role, status="active", admission=item, url=item["issue_url"])
        entry["new_work_evidence"] |= role == NEW_WORK
    return entries


def binding_for(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """The verifier's ``binding`` argument: role/identity from an admission record only."""
    if not entry or entry["admission"] is None or entry["conflict"]:
        return None
    return {"role": entry["role"], "source": "admission", "issue_id": entry["issue_id"],
            "admitted_at_utc": entry["admission"].get("recorded_at_utc")}


def _short_ref_key(project: str | None, iid: int, mr_project: str | None) -> tuple[str, int] | None:
    """GitLab short reference ``#iid`` / ``project#iid`` / ``group/project#iid`` relative to the MR project."""
    if project is None:
        return (mr_project.lower(), iid) if mr_project else None
    if "/" in project:
        return project.lower(), iid
    namespace = mr_project.rsplit("/", 1)[0] if mr_project and "/" in mr_project else None
    return (f"{namespace}/{project}".lower(), iid) if namespace else None


def _mentions(line: str, key: tuple[str, int], host: str | None, mr_project: str | None) -> bool:
    """Detection only (never trust or access): normalizes scheme, host case, trailing slash or sub-path
    after the IID (``/issues/24/designs/x.png``), query and fragment."""
    for match in URL_TOKEN.finditer(line):
        netloc = match[1].rsplit("@", 1)[-1].split(":", 1)[0].lower()
        path = re.split(r"[?#]", match[2] or "", maxsplit=1)[0].rstrip("*_.,;:!")
        found = URL_PATH.fullmatch(path)
        if host and netloc == host.lower() and found and (found[1].lower(), int(found[2])) == key:
            return True
    return any(_short_ref_key(ref[1], int(ref[2]), mr_project) == key
               for ref in SHORT_REF.finditer(URL_TOKEN.sub(" ", line)))


def reference_status(description: Any, key: tuple[str, int], host: str | None,
                     mr_project: str | None = None) -> str:
    """``declared`` (Issue/Work Item/Root Issue label, table or next-line form, or closing keyword),
    ``ambiguous`` (any other current mention of this Issue), ``absent`` or ``unreadable`` (non-text)."""
    if not isinstance(description, str):
        return "unreadable"
    status, previous = "absent", ""
    for line in description.splitlines():
        if _mentions(line, key, host, mr_project):
            label_above = DECLARATION.match(previous) and not DECLARATION.sub("", previous, count=1).strip(" *_|")
            if DECLARATION.match(line) or label_above or CLOSING.search(line):
                return "declared"
            status = "ambiguous"
        if line.strip():
            previous = line
    return status


def declares_issue(description: Any, key: tuple[str, int], host: str | None,
                   mr_project: str | None = None) -> bool | None:
    """Does the MR description still declare this Issue (label line or closing keyword)? None if unreadable."""
    status = reference_status(description, key, host, mr_project)
    return None if status == "unreadable" else status == "declared"


def reference_errors(description: Any, key: tuple[str, int], host: str | None, mr_project: str | None) -> list[str]:
    """Retirement precondition: the current description references the Issue in no form at all."""
    return {"unreadable": ["mr_binding_context_unreadable"], "declared": ["binding_still_declared"],
            "ambiguous": ["mr_binding_context_ambiguous"]}.get(
        reference_status(description, key, host, mr_project), [])


def _sop_validate(issue: Any, repository: str) -> list[str]:
    """Reuse the SOP binding preflight predicates (identity, assignee, opened) as the canonical SSOT."""
    try:
        sys.path.insert(0, str(SOP_SCRIPTS))
        import check_artifact_binding as sop  # noqa: PLC0415
    except ImportError:
        return ["binding_contract_unavailable"]
    finally:
        if sys.path and sys.path[0] == str(SOP_SCRIPTS):
            sys.path.pop(0)
    import inspect

    if "require_local_work" in inspect.signature(sop.validate_binding).parameters:
        codes = sop.validate_binding(issue, issue, {}, repository, require_local_work=False)
    else:
        # Older gitlab-issue-sop: no local-work predicates exist, so calling
        # without the flag runs exactly the checks require_local_work=False asks for.
        codes = sop.validate_binding(issue, issue, {}, repository)
    return sorted({code.removeprefix("root_").removeprefix("work_") for code in codes})


def history_errors(action: str, entry: dict[str, Any] | None, role: str | None) -> list[str]:
    """Rules that depend only on the append-only history (re-checked under the state lock)."""
    if action == "retire":
        return [] if entry and entry["status"] == "active" and not entry["conflict"] else ["binding_not_active"]
    if entry and (entry["conflict"] or entry["role"] not in (None, role)
                  or (role != NEW_WORK and entry["new_work_evidence"])):
        return ["binding_role_conflict"]
    return []


def read_context(reader: Callable[[str], Any], repo: str, iid: int, mr_project: str, mr_iid: int) -> dict[str, Any]:
    issue_path = f"projects/{quote(repo, safe='')}/issues/{iid}"
    return {"issue": reader(issue_path),
            "merge_request": reader(f"projects/{quote(mr_project, safe='')}/merge_requests/{mr_iid}"),
            "related_merge_requests": reader(f"{issue_path}/related_merge_requests?per_page={RELATED_PAGE}")}


def context_errors(ctx: dict[str, Any], repo: str, iid: int, mr_iid: int, entry: dict[str, Any] | None,
                   host: str | None) -> list[str]:
    issue, mr = ctx.get("issue"), ctx.get("merge_request")
    errors = []
    try:
        same = parse_issue_url(_get(issue, "web_url"), host) == (repo, iid)
    except ValueError:
        same = False
    if not same or _get(issue, "iid") != iid or not all(_positive(_get(issue, k)) for k in ("id", "project_id")):
        errors.append("issue_identity_mismatch")
    if entry and entry["issue_id"] not in (None, _get(issue, "id")):
        errors.append("binding_identity_mismatch")
    if _get(mr, "iid") != mr_iid or not _positive(_get(mr, "project_id")):
        errors.append("mr_identity_mismatch")
    return errors


def related(ctx: dict[str, Any], mr_iid: int) -> str | None:
    """None when GitLab lists the MR as related to the Issue, else the fixed error code."""
    items, mr = ctx.get("related_merge_requests"), ctx.get("merge_request")
    if isinstance(items, list) and any(isinstance(item, dict) and (item.get("project_id"), item.get("iid"))
                                       == (_get(mr, "project_id"), mr_iid) for item in items):
        return None
    return "related_mr_page_limit" if isinstance(items, list) and len(items) >= RELATED_PAGE else "mr_not_related"


def lifecycle_binding(issue: Any, url: str, host: str | None, now: str) -> dict[str, Any]:
    """The lifecycle-binding contract fields (issue-lifecycle/v2) for this live Issue read."""
    assignees = _get(issue, "assignees")
    ids = sorted(a["id"] for a in assignees if isinstance(a, dict) and _positive(a.get("id"))) \
        if isinstance(assignees, list) else []
    observed = {key: _get(issue, key) for key in ("id", "project_id", "iid", "state", "updated_at")}
    digest = hashlib.sha256(json.dumps({**observed, "assignee_ids": ids}, sort_keys=True, default=str).encode())
    return {"contract_version": LIFECYCLE_CONTRACT, "gitlab_host": host, "project_id": _get(issue, "project_id"),
            "issue_iid": _get(issue, "iid"), "issue_url": url,
            "state": observed["state"] if observed["state"] in ("opened", "closed") else None,
            "assignee_ids": ids, "snapshot_digest": "sha256:" + digest.hexdigest(), "verified_at": now}


def validate_request(action: Any, request: dict[str, Any], host: str | None) -> tuple[str, int]:
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("unsupported_binding_action")
    repo, iid = parse_issue_url(request.get("issue_url"), host)
    if not isinstance(request.get("mr_project"), str) or not REPOSITORY.fullmatch(request["mr_project"]):
        raise ValueError("invalid_mr_project")
    if not _positive(request.get("mr_iid")):
        raise ValueError("invalid_mr_iid")
    if not isinstance(request.get("revision"), str) or not SHA.fullmatch(request["revision"]):
        raise ValueError("invalid_revision")
    if action == "admit" and request.get("binding_role") not in ROLES:
        raise ValueError("unknown_binding_role")  # never defaulted to a reference
    reason = request.get("reason")
    if action == "retire" and not (isinstance(reason, str) and reason.strip() and len(reason) <= 500
                                   and "\n" not in reason and "\r" not in reason):
        raise ValueError("retirement_reason_required")
    return repo, iid


def produce(action: str, request: dict[str, Any], history: Any, reader: Callable[[str], Any] | None = None,
            host: str | None = None) -> dict[str, Any]:
    """One admission or retirement record from one live GET set; ``result`` is ADMITTED/RETIRED/REJECTED."""
    host = host if host is not None else load_owner_host()
    repo, iid = validate_request(action, request, host)
    key, mr_iid, mr_project = (repo.lower(), iid), request["mr_iid"], request["mr_project"]
    entry = resolve(history, mr_iid, mr_project, host).get(key)
    role = request.get("binding_role") if action == "admit" else (entry or {}).get("role") or NEW_WORK
    errors = history_errors(action, entry, role)
    try:
        ctx = read_context(reader or glab_reader(host), repo, iid, mr_project, mr_iid)
    except ReadFailed:
        ctx, errors = {}, errors + ["read_failed"]
    issue, mr = ctx.get("issue"), ctx.get("merge_request")
    if ctx:
        errors += context_errors(ctx, repo, iid, mr_iid, entry, host)
        if _get(mr, "state") != "opened":
            errors.append("mr_not_open")
        if _get(mr, "sha") != request["revision"]:
            errors.append("mr_head_mismatch")
    relation = related(ctx, mr_iid) if ctx else "read_failed"
    status = reference_status(_get(mr, "description"), key, host, mr_project) if ctx else "unreadable"
    declared = None if status == "unreadable" else status != "absent"  # any current reference counts
    if ctx and action == "admit":
        sop = _sop_validate(issue, mr_project)
        if role == CLOSED_REF_ROLE and _get(issue, "state") in ("opened", "closed"):
            sop = [code for code in sop if code != "not_opened"]
        errors += [f"issue_{code}" for code in sop] + ([relation] if relation else [])
    elif ctx:
        errors += reference_errors(_get(mr, "description"), key, host, mr_project)
    errors = list(dict.fromkeys(errors))
    now = datetime.now(UTC).isoformat()
    primary = errors[0] if errors else None
    identity = lifecycle_binding(issue, request["issue_url"], host, now) if ctx else None
    return {
        "kind": "issue_binding", "schema": BINDING_SCHEMA, "action": action,
        "result": "REJECTED" if errors else ACTIONS[action], "binding_role": role,
        "issue_url": request["issue_url"], "issue_project_id": _get(issue, "project_id"),
        "issue_iid": _get(issue, "iid"), "issue_id": _get(issue, "id"),
        "mr_project": mr_project, "mr_project_id": _get(mr, "project_id"), "mr_iid": mr_iid,
        "revision": request["revision"], "binding": identity,
        "mr_declares_issue": declared, "native_relation_listed": relation is None,
        "native_unlink": False, "reason": request.get("reason") if action == "retire" else None,
        "source": "live", "errors": errors,
        "gap": {"reason": primary, "owner": PARENT,
                "next_action": BINDING_GAPS.get(primary, "fix the reported identity/relationship/HEAD "
                                                "mismatch, then re-run")} if primary else None,
        "recorded_at_utc": now,
    }


def recheck_retirement(entry: dict[str, Any], reader: Callable[[str], Any], mr_project: str, mr_iid: int,
                       host: str | None) -> list[str]:
    """Audit-time live re-read: identity unchanged and the MR description still does not declare it."""
    repo, iid = parse_issue_url(entry["url"], host)
    try:
        ctx = read_context(reader, repo, iid, mr_project, mr_iid)
    except ReadFailed:
        return ["read_failed"]
    errors = context_errors(ctx, repo, iid, mr_iid, entry, host)
    return errors + reference_errors(_get(ctx.get("merge_request"), "description"), (repo.lower(), iid), host,
                                     mr_project)
