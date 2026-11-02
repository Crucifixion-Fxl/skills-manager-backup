"""Read-only Issue submission receipt + review-state boundary (engineering/skills#61).

A submitted/revised Issue receipt is complete only when one fresh, live GET set
proves both the stable-marker comment and the review state of the same Issue,
MR and exact HEAD. Supported review-state mode: ``canonical-label`` (the Issue
carries exactly ``status::in-review``). ``native-status`` is recognised but not
implemented, so it is BLOCKED rather than assumed. Native ``opened``/``closed``
is recorded as ``observed_state`` and never counts as a review state.

A live ``closed`` Issue gets the terminal ``NOT_APPLICABLE_CLOSED`` outcome only
when its binding was admitted with role ``closed-reference`` (release-flowback
reference to already delivered work, see ``issue_binding.py``), after the same
note/MR/HEAD/related-MR checks; it is never reopened or relabeled. The role is
never inferred from ``state=closed``: a ``new-work`` binding that closes stays
``ISSUE_SYNC_UNVERIFIED issue_closed_during_review``, and a closed Issue without
an admission record stays ``ISSUE_SYNC_UNVERIFIED binding_role_unresolved``.

The marker is counted within the selected note only. Paginated reconciliation of
duplicate marker notes stays with the authorized caller before any POST; this
verifier does not claim cross-client exactly-once delivery.

This module never writes to GitLab; label changes stay with the authorized
caller. Errors are fixed codes only; CLI stderr and response bodies are never
copied into a receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

REVIEW_LABEL = "status::in-review"
CLOSED_REFERENCE = "NOT_APPLICABLE_CLOSED"
TERMINAL_STATES = {"VERIFIED": "opened", CLOSED_REFERENCE: "closed"}  # receipt result -> live Issue state
PHASES = ("submitted", "revised")
NEW_WORK, CLOSED_REF_ROLE = "new-work", "closed-reference"
ROLES = (NEW_WORK, CLOSED_REF_ROLE)
SUPPORTED_MODES = ("canonical-label",)
UNSUPPORTED_MODES = ("native-status",)
RECEIPT_SCHEMA = "gitlab-mr-issue-sync-receipt/v1"
RELATED_PAGE = 100
REPOSITORY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)+")
SHA = re.compile(r"[0-9a-f]{40}")
OWNER_FILE = Path(__file__).resolve().parents[2] / "gitlab/references/auth-discovery.json"

PARENT = "parent session (authorized Issue/MR writer)"
GAPS = {
    "read_failed": (PARENT, "restore read-only glab access to the GitLab owner host and re-run the verifier; "
                    "do not repost the receipt"),
    "review_state_missing": (PARENT, f"fresh-GET the Issue labels and confirm exact {REVIEW_LABEL} exists in the "
                             "project/ancestor label catalog (else stop: project-mode gap, do not create it); "
                             "then one PUT with remove_labels=<every observed status:: label> and "
                             f"add_labels={REVIEW_LABEL}, keeping unrelated labels; re-run the verifier"),
    "ambiguous_status_labels": (PARENT, f"fresh-GET the Issue labels and confirm exact {REVIEW_LABEL} exists in "
                                "the label catalog; one PUT with remove_labels=<every status:: label except "
                                f"{REVIEW_LABEL}> and add_labels={REVIEW_LABEL}, never overwriting labels; "
                                "if a title is duplicated in the catalog or a prior ACK disagrees with readback, "
                                "do not replay it: fresh-read attached label IDs and send only the missing "
                                "exact removeLabelIds/addLabelIds delta, else keep this gap; re-run the verifier"),
    "mr_head_mismatch": (PARENT, "write/reconcile a receipt for the current MR HEAD, then re-run"),
    "unsupported_project_mode": ("gitlab-mr skill maintainers", "implement and test a live native Status "
                                 "reader, or use canonical-label mode; record ISSUE_SYNC_UNVERIFIED meanwhile"),
    "canonical_review_label_absent": ("Issue project maintainer", f"create the canonical {REVIEW_LABEL} label "
                                      "(or a supported native mode), then re-run; keep ISSUE_SYNC_UNVERIFIED"),
    "offline_snapshot_not_live": (PARENT, "run the verifier live; offline snapshots never verify a receipt"),
    "issue_closed_during_review": (PARENT, "a new-work Issue was closed during review: do not reopen, relabel or "
                                   "re-tag it as a reference; ask the Issue owner whether the closure was "
                                   "premature (reopen through the normal lifecycle) or the MR no longer delivers "
                                   "it (remove it from the MR Issue line, then retire the binding with "
                                   "record_issue_binding.py retire); re-run the verifier"),
    "binding_role_unresolved": (PARENT, "no admission record proves this closed Issue is a reference: if it is a "
                                "release-flowback reference that never had new-work receipts, run "
                                "record_issue_binding.py admit --binding-role closed-reference --state, then "
                                "re-run the verifier with --state; otherwise treat it as issue_closed_during_review"),
    "binding_identity_mismatch": (PARENT, "the live Issue no longer matches the admitted identity; stop and "
                                  "re-verify the binding with the Issue owner"),
    "malformed_response": (PARENT, "GitLab returned an unexpected payload shape; re-run the verifier, and if it "
                           "persists report the API change to the gitlab-mr skill maintainers"),
}
DEFAULT_GAP = (PARENT, "reconcile the stable-marker note, Issue and MR identity, then re-run the verifier")
PROJECT_MODE_GAPS = {"unsupported_project_mode", "canonical_review_label_absent"}


class ReadFailed(Exception):
    """A GET or the platform owner contract could not be used."""


def load_owner_host(path: Path = OWNER_FILE) -> str | None:
    """Return the sole canonical GitLab host from the platform owner, without fallback."""
    try:
        instances = json.loads(path.read_text(encoding="utf-8")).get("instances")
        if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], str):
            return None
        url = urlsplit(instances[0])
        if (url.scheme != "https" or instances[0] != f"https://{url.netloc}" or url.port is not None
                or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", url.netloc or "")):
            return None
        return url.hostname
    except (OSError, ValueError, AttributeError):
        return None


def marker(phase: str, project_id: int, issue_iid: int, mr_iid: int, revision: str) -> str:
    return f"<!-- gitlab-mr-issue-sync:v1:{phase}:{project_id}:{issue_iid}:{mr_iid}:{revision} -->"


def parse_issue_url(value: Any, host: str | None) -> tuple[str, int]:
    if not host or not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("untrusted_issue_url")
    url = urlsplit(value)
    match = re.fullmatch(r"/(.+)/-/(?:issues|work_items)/([1-9][0-9]*)", url.path)
    if (url.scheme != "https" or url.netloc != host or url.query or url.fragment
            or not match or not REPOSITORY.fullmatch(match[1])):
        raise ValueError("untrusted_issue_url")
    return match[1], int(match[2])


def validate_request(request: dict[str, Any], host: str | None) -> tuple[str, int]:
    """Reject malformed caller input before any read; returns the Issue repo/IID."""
    repo, iid = parse_issue_url(request.get("issue_url"), host)
    if request.get("phase") not in PHASES:
        raise ValueError("unsupported_phase")
    if not isinstance(request.get("mr_project"), str) or not REPOSITORY.fullmatch(request["mr_project"]):
        raise ValueError("invalid_mr_project")
    for key in ("mr_iid", "note_id"):
        if not _positive(request.get(key)):
            raise ValueError(f"invalid_{key}")
    if not isinstance(request.get("revision"), str) or not SHA.fullmatch(request["revision"]):
        raise ValueError("invalid_revision")
    if request.get("project_mode") not in SUPPORTED_MODES + UNSUPPORTED_MODES:
        raise ValueError("unknown_project_mode")
    expected = request.get("expected_body")
    if expected is not None and (not isinstance(expected, str) or "\r" in expected):
        raise ValueError("expected_body_must_be_lf_text")
    return repo, iid


def glab_reader(host: str | None) -> Callable[[str], Any]:
    """GET-only reader on the existing glab credential; never surfaces stderr."""
    def read(endpoint: str) -> Any:
        binary = shutil.which("glab")
        if not host or not binary:
            raise ReadFailed("platform_owner_unverified")
        try:
            result = subprocess.run([binary, "api", "--hostname", host, "--method", "GET", endpoint],
                                    capture_output=True, text=True, check=True, timeout=30)
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            raise ReadFailed("read_failed") from None
    return read


def read_snapshot(request: dict[str, Any], reader: Callable[[str], Any], repo: str, iid: int) -> dict[str, Any]:
    issue_path = f"projects/{quote(repo, safe='')}/issues/{iid}"
    mr_path = f"projects/{quote(request['mr_project'], safe='')}/merge_requests/{request['mr_iid']}"
    snapshot = {
        "user": reader("user"),
        "issue": reader(issue_path),
        "note": reader(f"{issue_path}/notes/{request['note_id']}"),
        "merge_request": reader(mr_path),
        "related_merge_requests": reader(f"{issue_path}/related_merge_requests?per_page={RELATED_PAGE}"),
    }
    # A live closed Issue never reaches the catalog check in evaluate(), so its GET must not gate the receipt.
    if request["project_mode"] == "canonical-label" and _get(snapshot["issue"], "state") != "closed":
        snapshot["project_labels"] = reader(
            f"projects/{quote(repo, safe='')}/labels?search={quote(REVIEW_LABEL, safe='')}"
            f"&include_ancestor_groups=true&per_page=100")
    return snapshot


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def resolve_role(binding: Any) -> tuple[str, str]:
    """(role, source) from a history-resolved binding; missing or unrecognized intent is default new-work."""
    role = _get(binding, "role")
    if _get(binding, "source") == "admission" and role in ROLES:
        return role, "admission"
    return NEW_WORK, "default"


def evaluate(request: dict[str, Any], snapshot: dict[str, Any], host: str | None, source: str,
             binding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pure judgement over one GET snapshot. Only source=live can verify.

    ``binding`` comes from ``issue_binding.resolve`` over the drive-state history, never from the caller's
    request fields; without it the role is the default ``new-work``.
    """
    repo, iid = validate_request(request, host)
    role = resolve_role(binding)
    errors: list[str] = []
    issue, note, mr = snapshot.get("issue"), snapshot.get("note"), snapshot.get("merge_request")
    try:
        same_issue = parse_issue_url(_get(issue, "web_url"), host) == (repo, iid)
    except ValueError:
        same_issue = False
    if (not same_issue or _get(issue, "iid") != iid
            or not all(_positive(_get(issue, key)) for key in ("id", "project_id"))):
        errors.append("issue_identity_mismatch")
    if role[1] == "admission" and _get(binding, "issue_id") != _get(issue, "id"):
        errors.append("binding_identity_mismatch")
    state = _get(issue, "state")
    if state not in TERMINAL_STATES.values():
        errors.append("issue_not_open")
    project_id = _get(issue, "project_id")
    expected_marker = marker(request["phase"], project_id, iid, request["mr_iid"], request["revision"])
    body = _get(note, "body")
    if (_get(note, "id") != request["note_id"] or _get(note, "noteable_type") != "Issue"
            or _get(note, "noteable_iid") != iid or _get(note, "project_id") != project_id
            or _get(note, "system") is not False):
        errors.append("note_identity_mismatch")
    if not _positive(_get(snapshot, "user", "id")) or _get(note, "author", "id") != _get(snapshot, "user", "id"):
        errors.append("note_author_mismatch")
    if not isinstance(body, str) or body.count(expected_marker) != 1:
        errors.append("note_marker_mismatch")
    elif "\r" in body:
        errors.append("note_body_not_lf")
    elif request.get("expected_body") is not None and body.rstrip("\n") != request["expected_body"].rstrip("\n"):
        errors.append("note_body_mismatch")
    if isinstance(body, str) and (not isinstance(_get(mr, "web_url"), str) or _get(mr, "web_url") not in body):
        errors.append("note_missing_mr_url")
    if _get(mr, "iid") != request["mr_iid"] or not _positive(_get(mr, "project_id")):
        errors.append("mr_identity_mismatch")
    if _get(mr, "sha") != request["revision"]:
        errors.append("mr_head_mismatch")
    if _get(mr, "state") != "opened":
        errors.append("mr_not_open")
    related = snapshot.get("related_merge_requests")
    if not isinstance(related, list) or not any(
            isinstance(item, dict) and (item.get("project_id"), item.get("iid"))
            == (_get(mr, "project_id"), request["mr_iid"]) for item in related):
        errors.append("related_mr_page_limit" if isinstance(related, list) and len(related) >= RELATED_PAGE
                      else "mr_not_related")
    labels = _get(issue, "labels")
    status_labels = sorted(label for label in labels if isinstance(label, str)
                           and label.lower().startswith("status::")) if isinstance(labels, list) else []
    review = {"mode": request["project_mode"], "required": REVIEW_LABEL,
              "observed_status_labels": status_labels, "verdict": "UNVERIFIED",
              "provenance": f"{source} GET issue labels" if request["project_mode"] == "canonical-label"
              else "none: mode not implemented"}
    if request["project_mode"] in UNSUPPORTED_MODES:
        errors.append("unsupported_project_mode")
        review["verdict"] = "BLOCKED"
    elif state == "closed" and role == (CLOSED_REF_ROLE, "admission"):  # declared reference: no transition
        review.update(verdict=CLOSED_REFERENCE, provenance=f"{source} GET issue state=closed")
    elif state == "closed":  # never inferred from state: new-work or undeclared closed Issue stays unverified
        errors.append("issue_closed_during_review" if role[1] == "admission" else "binding_role_unresolved")
    elif not isinstance(labels, list):
        errors.append("review_state_missing")
    elif not isinstance(snapshot.get("project_labels"), list):
        errors.append("malformed_response")
    elif not any(isinstance(item, dict) and item.get("name") == REVIEW_LABEL
                 for item in snapshot["project_labels"]):
        errors.append("canonical_review_label_absent")
        review["verdict"] = "BLOCKED"
    elif status_labels == [REVIEW_LABEL]:
        review["verdict"] = "VERIFIED"
    else:
        errors.append("ambiguous_status_labels" if len(status_labels) > 1 else "review_state_missing")
    if source != "live":
        errors.append("offline_snapshot_not_live")
    return build_receipt(request, snapshot, source, errors, review, expected_marker, role)


def build_receipt(request: dict[str, Any], snapshot: dict[str, Any], source: str, errors: list[str],
                  review: dict[str, Any] | None, expected_marker: str | None,
                  role: tuple[str, str] = (NEW_WORK, "default")) -> dict[str, Any]:
    issue, note, mr = (snapshot.get(key) for key in ("issue", "note", "merge_request"))
    body = _get(note, "body")
    gaps = [code for code in errors if code in PROJECT_MODE_GAPS]
    primary = gaps[0] if gaps else (errors[0] if errors else None)
    verdict = _get(review, "verdict")
    result = ("BLOCKED" if gaps else "ISSUE_SYNC_UNVERIFIED") if errors or verdict not in TERMINAL_STATES \
        else verdict
    owner, action = GAPS.get(primary, DEFAULT_GAP) if primary else (None, None)
    return {
        "kind": "issue_sync", "schema": RECEIPT_SCHEMA, "phase": request.get("phase"),
        "issue_url": request.get("issue_url"), "issue_project_id": _get(issue, "project_id"),
        "issue_iid": _get(issue, "iid"), "issue_id": _get(issue, "id"),
        "mr_project": request.get("mr_project"), "mr_project_id": _get(mr, "project_id"),
        "mr_iid": request.get("mr_iid"), "revision": request.get("revision"),
        "note_id": _get(note, "id") if _get(note, "id") == request.get("note_id") else None,
        "note_author_id": _get(note, "author", "id"), "marker": expected_marker,
        "note_body_sha256": hashlib.sha256(body.encode()).hexdigest() if isinstance(body, str) else None,
        "observed_state": _get(issue, "state") if _get(issue, "state") in ("opened", "closed") else None,
        "review_state": review, "source": source, "binding_role": role[0], "binding_role_source": role[1],
        "verified": result == "VERIFIED", "result": result, "errors": errors,
        "gap": {"reason": primary, "owner": owner, "next_action": action} if primary else None,
        "verified_at_utc": datetime.now(UTC).isoformat(),
    }


def audit_issue_sync(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Compatibility forward to ``issue_sync_audit.audit_issue_sync`` (same call surface:
    ``state, error, verifier=verify``); imported lazily because that module imports this one."""
    from issue_sync_audit import audit_issue_sync as audit  # noqa: PLC0415

    return audit(*args, **kwargs)


def verify(request: dict[str, Any], reader: Callable[[str], Any] | None = None,
           host: str | None = None, snapshot: dict[str, Any] | None = None,
           binding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce one receipt. ``snapshot`` marks an offline replay, which never verifies."""
    host = host if host is not None else load_owner_host()
    repo, iid = validate_request(request, host)
    if snapshot is not None:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot_must_be_object")
        return evaluate(request, snapshot, host, "offline-snapshot", binding)
    try:
        live = read_snapshot(request, reader or glab_reader(host), repo, iid)
    except ReadFailed:
        return build_receipt(request, {}, "live", ["read_failed"], None, None, resolve_role(binding))
    return evaluate(request, live, host, "live", binding)
