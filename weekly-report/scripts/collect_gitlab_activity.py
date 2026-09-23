#!/usr/bin/env python3
"""Collect GitLab push and attributed-commit activity for weekly-report.

Push events prove that a user acted on a project/ref. They do not prove that
every commit reachable from that ref was authored by that user. This collector
keeps those two facts separate and never uses ``push_data.commit_count`` as a
personal contribution count.

Attribution is bounded to the commits each push actually introduced. For every
push event the collector compares ``push_data.commit_from`` with
``push_data.commit_to``; a newly created ref (``commit_from`` is null) is
compared against the project default branch so that pre-existing default-branch
history is never attributed to the push. Querying a whole ref window instead
would count every in-window commit reachable from long-lived branches such as
``master``/``main``/``release/*``, which inflates the personal commit count by
an order of magnitude.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
import re
import subprocess
from typing import Any, Callable
from urllib.parse import quote, urlencode


UNRESOLVED_ATTRIBUTION = "branch_push_confirmed_commit_attribution_unresolved"
UTC_OFFSET = "+00:00"
REPORT_TZ = timezone(timedelta(hours=8))
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+(?::\d+)?$", re.ASCII)


class GlabApiError(RuntimeError):
    """Raised when glab cannot return a valid API response."""


class GlabClient:
    """Small explicit-host wrapper around ``glab api``."""

    def __init__(
        self,
        hostname: str,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not HOSTNAME_RE.fullmatch(hostname):
            raise ValueError("hostname must contain only a host name and optional port")
        self.hostname = hostname
        self._runner = runner

    def get(self, path: str, *, paginate: bool = False) -> Any:
        command = ["glab", "api", "--hostname", self.hostname]
        if paginate:
            command.append("--paginate")
        command.append(path)
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GlabApiError(f"GET {path}: transport failure ({type(exc).__name__})") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown glab error").strip()
            detail = " ".join(detail.split())[:500]
            raise GlabApiError(f"GET {path}: {detail}")
        try:
            return _decode_json_stream(completed.stdout, paginate=paginate)
        except ValueError as exc:
            raise GlabApiError(f"GET {path}: invalid JSON response") from exc


def _decode_json_stream(raw: str, *, paginate: bool) -> Any:
    """Decode both a JSON value and glab's legacy ``[...][...]`` pagination."""

    decoder = json.JSONDecoder()
    values: list[Any] = []
    offset = 0
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset >= len(raw):
            break
        value, offset = decoder.raw_decode(raw, offset)
        values.append(value)

    if not values:
        raise ValueError("empty response")
    if not paginate:
        if len(values) != 1:
            raise ValueError("multiple non-paginated JSON values")
        return values[0]

    combined: list[Any] = []
    for value in values:
        if not isinstance(value, list):
            raise ValueError("paginated response is not an array")
        combined.extend(value)
    return combined


def collect_gitlab_activity(
    *,
    since: str,
    until: str,
    hostname: str,
    username: str | None = None,
    author_aliases: tuple[str, ...] | list[str] = (),
    client: Any | None = None,
) -> dict[str, Any]:
    """Return push evidence and strictly attributed commits for a date range."""

    start_dt, end_dt = _date_bounds(since, until)
    api = client or GlabClient(hostname)

    user_path = "user" if username is None else f"users?{urlencode({'username': username})}"
    try:
        user_response = api.get(user_path)
    except GlabApiError as exc:
        return _unavailable(hostname, since, until, "user_api_failed", exc)
    if username is None:
        user = user_response
    elif isinstance(user_response, list):
        user = next(
            (
                candidate
                for candidate in user_response
                if isinstance(candidate, dict)
                and _normalize(candidate.get("username")) == _normalize(username)
            ),
            None,
        )
    else:
        user = None
    if not isinstance(user, dict) or not user.get("username"):
        return _unavailable(
            hostname,
            since,
            until,
            "user_api_invalid",
            GlabApiError(f"GET {user_path}: missing username"),
        )

    target_username = str(user["username"])
    names, emails = _identity_sets(user, author_aliases)
    events_path = _events_path(target_username, start_dt, end_dt)
    try:
        raw_events = api.get(events_path, paginate=True)
    except GlabApiError as exc:
        return _unavailable(hostname, since, until, "events_api_failed", exc, user=user)
    if not isinstance(raw_events, list):
        return _unavailable(
            hostname,
            since,
            until,
            "events_api_invalid",
            GlabApiError(f"GET {events_path}: expected array"),
            user=user,
        )

    events = [
        event
        for event in raw_events
        if isinstance(event, dict) and _is_in_range(event.get("created_at"), start_dt, end_dt)
    ]
    grouped_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        push_data = event.get("push_data")
        project_id = event.get("project_id")
        if not isinstance(push_data, dict) or not push_data.get("ref"):
            continue
        try:
            grouped_events[int(project_id)].append(event)
        except (TypeError, ValueError):
            continue

    projects: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for project_id, project_events in sorted(grouped_events.items()):
        project, project_errors = _collect_project(
            api=api,
            project_id=project_id,
            events=project_events,
            start_dt=start_dt,
            end_dt=end_dt,
            identity_names=names,
            identity_emails=emails,
        )
        projects.append(project)
        errors.extend(project_errors)

    projects.sort(key=lambda item: (-item["commit_count"], item["path_with_namespace"]))
    attributed_commits = sum(project["commit_count"] for project in projects)
    unresolved_projects = sum(
        project["activity_status"] == "push_only" for project in projects
    )
    return {
        "provider": "gitlab",
        "available": True,
        "complete": not errors,
        "hostname": hostname,
        "period": {"since": since, "until": until, "timezone": "UTC+08:00"},
        "user": {
            "username": target_username,
            "name": str(user.get("name") or target_username),
        },
        "projects": projects,
        "totals": {
            "attributed_commits": attributed_commits,
            "merge_commits": sum(project["merge_commits"] for project in projects),
            "rebase_duplicates_collapsed": sum(
                project["rebase_duplicates_collapsed"] for project in projects
            ),
            "push_events": sum(len(value) for value in grouped_events.values()),
            "active_projects": len(projects),
            "unresolved_projects": unresolved_projects,
            "collection_errors": len(errors),
        },
        "errors": errors,
    }


def _collect_project(
    *,
    api: Any,
    project_id: int,
    events: list[dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    identity_names: set[str],
    identity_emails: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    try:
        metadata = api.get(f"projects/{project_id}")
        if not isinstance(metadata, dict):
            raise GlabApiError(f"GET projects/{project_id}: expected object")
    except GlabApiError as exc:
        metadata = {}
        errors.append(_error_record("project", project_id, None, exc))

    matched_commits, refs, ref_errors = _collect_ref_activity(
        api=api,
        project_id=project_id,
        events=events,
        start_dt=start_dt,
        end_dt=end_dt,
        identity_names=identity_names,
        identity_emails=identity_emails,
        default_branch=str(metadata.get("default_branch") or "") or None,
    )
    errors.extend(ref_errors)

    # Merges are integration actions, not authored changes: exclude them from the
    # owner's commit count before collapsing, and report them separately.
    authored_matches = {
        commit_id: entry
        for commit_id, entry in matched_commits.items()
        if not entry["is_merge"]
    }
    merge_commits = len(matched_commits) - len(authored_matches)

    kept, collapsed = _collapse_rebase_duplicates(authored_matches)
    commits = sorted(
        (entry["record"] for entry in kept.values()),
        key=lambda item: (item.get("authored_date") or item.get("committed_date") or ""),
        reverse=True,
    )
    status = "attributed" if commits else "push_only"
    note = None if commits else UNRESOLVED_ATTRIBUTION
    path_with_namespace = str(metadata.get("path_with_namespace") or f"project:{project_id}")
    project = {
        "id": project_id,
        "path_with_namespace": path_with_namespace,
        "web_url": metadata.get("web_url"),
        "push_count": len(events),
        "refs": refs,
        "activity_status": status,
        "activity_note_code": note,
        "commit_count": len(commits),
        "merge_commits": merge_commits,
        "rebase_duplicates_collapsed": collapsed,
        "commits": commits,
        "complete": not errors,
    }
    return project, errors


def _logical_commit_key(commit: dict[str, Any]) -> tuple[str] | None:
    """Identity of one logical change, stable across rebase, cherry-pick and amend.

    Re-applying a change rewrites the SHA and often the author date too
    (``rebase --ignore-date``, ``commit --amend``, cherry-pick), so dates cannot
    be part of the key: real data showed the same subject re-committed three
    seconds apart.

    The author identity is deliberately **not** part of the key either. A commit
    only reaches deduplication after it has already been attributed to the single
    target user, so re-including identity is redundant and actively harmful: the
    same person commits under several name/email aliases (one observed case had
    one change committed once under the GitLab username with a device-local email
    and once under the display name with the corporate email), which would split
    one logical change into two. The subject, scoped to one project, is the
    strongest available proxy for patch identity, because GitLab does not expose
    ``git patch-id``.

    Returns ``None`` when the subject is missing, so such commits are counted
    individually rather than wrongly merged.
    """

    title = str(commit.get("title") or "").strip()
    if not title:
        return None
    return (title,)


def _is_merge_commit(commit: dict[str, Any]) -> bool:
    """True for a commit with more than one parent.

    Uses ``parent_ids`` rather than matching subject prefixes, because merge
    subjects are template- and locale-dependent while parent count is a fact.
    A merge is an integration action, not a change the author wrote.
    """

    parents = commit.get("parent_ids")
    return isinstance(parents, list) and len(parents) > 1


def _collapse_rebase_duplicates(
    matched: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    """Collapse rebase/cherry-pick/amend copies of the same logical change.

    Pushing one change through feature -> stage -> release branches creates a new
    SHA per branch. SHA deduplication cannot see those copies, so a single logical
    change was previously counted once per branch it travelled through. The
    earliest committed copy is kept as the representative.
    """

    representative: dict[tuple[str], str] = {}
    kept: dict[str, dict[str, Any]] = {}
    collapsed = 0
    for commit_id in sorted(matched):
        entry = matched[commit_id]
        key = entry["key"]
        if key is None:
            kept[commit_id] = entry
            continue
        incumbent_id = representative.get(key)
        if incumbent_id is None:
            representative[key] = commit_id
            kept[commit_id] = entry
            continue
        collapsed += 1
        if _committed_instant(entry["record"]) < _committed_instant(kept[incumbent_id]["record"]):
            del kept[incumbent_id]
            representative[key] = commit_id
            kept[commit_id] = entry
    return kept, collapsed


def _committed_instant(record: dict[str, Any]) -> tuple[int, str]:
    """Sortable committed instant; undated records sort last, ties break on SHA."""

    parsed = _parse_datetime(record.get("committed_date") or record.get("authored_date"))
    if parsed is None:
        return (1, str(record.get("id") or ""))
    return (0, parsed.isoformat() + str(record.get("id") or ""))


def _collect_ref_activity(
    *,
    api: Any,
    project_id: int,
    events: list[dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    identity_names: set[str],
    identity_emails: set[str],
    default_branch: str | None = None,
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Resolve commits introduced by each push while preserving partial errors.

    Attribution is bounded per push event, not per ref window: a push to a
    long-lived branch must not attribute that branch's whole in-window history.
    """

    ref_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        ref = str(event["push_data"]["ref"])
        ref_events[ref].append(event)

    matched_commits: dict[str, dict[str, Any]] = {}
    refs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for ref, matching_events in sorted(ref_events.items()):
        refs.append(
            {
                "ref": ref,
                "ref_type": str(matching_events[0]["push_data"].get("ref_type") or "unknown"),
                "push_events": len(matching_events),
                "latest_push_at": max(str(event.get("created_at") or "") for event in matching_events),
            }
        )
        for event in matching_events:
            push_data = event["push_data"]
            commit_to = str(push_data.get("commit_to") or "")
            if not commit_to:
                # Ref deletion introduces no commits; keep the push evidence only.
                continue
            commit_from = str(push_data.get("commit_from") or "")
            base = commit_from or (default_branch or "")
            if not base:
                errors.append(
                    _error_record(
                        "push_range",
                        project_id,
                        ref,
                        GlabApiError(
                            "created ref without commit_from and no default branch "
                            "to bound attribution"
                        ),
                    )
                )
                continue
            if base == commit_to:
                continue

            path = _compare_path(project_id, base, commit_to)
            try:
                body = api.get(path)
                if not isinstance(body, dict):
                    raise GlabApiError(f"GET {path}: expected object")
                if body.get("compare_timeout"):
                    # GitLab cut the comparison short, so `commits` may be
                    # truncated. Accepting it would under-count while claiming a
                    # complete total, so fail closed instead.
                    raise GlabApiError(f"GET {path}: compare_timeout, result truncated")
                commits = body.get("commits")
                if not isinstance(commits, list):
                    raise GlabApiError(f"GET {path}: missing commits array")
            except GlabApiError as exc:
                # Fail closed: never widen back to the full ref window, because
                # that is the inflation this bounding exists to prevent.
                errors.append(_error_record("push_range", project_id, ref, exc))
                continue

            for commit in commits:
                if not isinstance(commit, dict) or not commit.get("id"):
                    continue
                if not _commit_in_range(commit, start_dt, end_dt):
                    continue
                matched_by = _commit_matches(commit, identity_names, identity_emails)
                if not matched_by:
                    continue
                commit_id = str(commit["id"])
                matched_commits.setdefault(
                    commit_id,
                    {
                        "record": _commit_record(commit, matched_by),
                        "key": _logical_commit_key(commit),
                        "is_merge": _is_merge_commit(commit),
                    },
                )

    return matched_commits, refs, errors


def _events_path(username: str, start_dt: datetime, end_dt: datetime) -> str:
    # Widen the server-side dates, then apply exact UTC+8 instants locally.
    params = urlencode(
        {
            "action": "pushed",
            "after": (start_dt.date() - timedelta(days=1)).isoformat(),
            "before": (end_dt.date() + timedelta(days=1)).isoformat(),
            "per_page": 100,
        }
    )
    return f"users/{quote(username, safe='')}/events?{params}"


def _compare_path(project_id: int, base: str, head: str) -> str:
    """Path for the commits a push introduced, relative to its base revision.

    ``straight=true`` requests two-dot ``base..head`` semantics, matching the
    ``commit_from..commit_to`` contract this collector documents. GitLab defaults
    to three-dot (merge-base) comparison. For the ``commits`` array the two
    normally agree, because every common ancestor of ``base`` and ``head`` is an
    ancestor of the merge base, so both exclude the same history; that was
    confirmed against diverged revisions on a live project. They can diverge when
    a criss-cross history yields several merge bases and the chosen one does not
    dominate ``base``, which would attribute commits this push never introduced.
    Being explicit costs nothing and pins the intended semantics.

    This endpoint is intentionally **not** paginated. Unlike the commits list
    endpoint it returns a single object whose ``commits`` array is complete: a
    live check returned 2445 commits in one response, and both ``--paginate`` and
    ``per_page`` left that unchanged. Truncation is instead signalled by
    ``compare_timeout``, which the caller treats as an error.
    """

    params = urlencode({"from": base, "to": head, "straight": "true"})
    return f"projects/{project_id}/repository/compare?{params}"


def _date_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    start_date = _strict_date(since)
    end_date = _strict_date(until)
    if start_date > end_date:
        raise ValueError("since must be before or equal to until")
    return (
        datetime.combine(start_date, time.min, tzinfo=REPORT_TZ),
        datetime.combine(end_date, time.max, tzinfo=REPORT_TZ),
    )


def _strict_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("dates must use canonical YYYY-MM-DD format")
    return parsed


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", UTC_OFFSET))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_in_range(value: Any, start_dt: datetime, end_dt: datetime) -> bool:
    parsed = _parse_datetime(value)
    return parsed is not None and start_dt <= parsed <= end_dt


def _commit_in_range(commit: dict[str, Any], start_dt: datetime, end_dt: datetime) -> bool:
    timestamp = commit.get("authored_date") or commit.get("committed_date")
    return _is_in_range(timestamp, start_dt, end_dt)


def _identity_sets(
    user: dict[str, Any], aliases: tuple[str, ...] | list[str]
) -> tuple[set[str], set[str]]:
    names = _normalized_values(user.get("username"), user.get("name"))
    emails = _normalized_values(
        user.get("email"), user.get("public_email"), user.get("commit_email")
    )
    for alias in aliases:
        normalized = _normalize(alias)
        if not normalized:
            continue
        if "@" in normalized:
            emails.add(normalized)
        else:
            names.add(normalized)
    return names, emails


def _normalized_values(*values: Any) -> set[str]:
    return {normalized for value in values if (normalized := _normalize(value))}


def _normalize(value: Any) -> str:
    return str(value).strip().casefold() if value is not None else ""


def _commit_matches(
    commit: dict[str, Any], names: set[str], emails: set[str]
) -> list[str]:
    matched: list[str] = []
    fields = (
        ("author_name", names),
        ("committer_name", names),
        ("author_email", emails),
        ("committer_email", emails),
    )
    for field, identities in fields:
        if identities and _normalize(commit.get(field)) in identities:
            matched.append(field)
    return matched


def _commit_record(commit: dict[str, Any], matched_by: list[str]) -> dict[str, Any]:
    commit_id = str(commit["id"])
    return {
        "id": commit_id,
        "short_id": str(commit.get("short_id") or commit_id[:8]),
        "title": str(commit.get("title") or ""),
        "authored_date": commit.get("authored_date"),
        "committed_date": commit.get("committed_date"),
        "web_url": commit.get("web_url"),
        "matched_by": matched_by,
    }


def _error_record(
    scope: str, project_id: int, ref: str | None, error: GlabApiError
) -> dict[str, Any]:
    return {
        "scope": scope,
        "project_id": project_id,
        "ref": ref,
        "error": str(error),
    }


def _unavailable(
    hostname: str,
    since: str,
    until: str,
    reason: str,
    error: GlabApiError,
    *,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": "gitlab",
        "available": False,
        "complete": False,
        "hostname": hostname,
        "period": {"since": since, "until": until},
        "reason": reason,
        "error": str(error),
        "projects": [],
        "totals": {},
    }
    if user:
        result["user"] = {
            "username": str(user.get("username") or "unknown"),
            "name": str(user.get("name") or user.get("username") or "unknown"),
        }
    return result


def select_merge_requests(rows: list[dict[str, Any]], since: str, until: str) -> dict[str, list]:
    """Select dated events, not current state or the last comment timestamp."""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("expected MR array")
    start, end = _date_bounds(since, until)
    return {
        label: [row for row in rows if _is_in_range(row.get(field), start, end)]
        for label, field in (("created", "created_at"), ("merged", "merged_at"), ("closed", "closed_at"))
    }


def diff_stats(body: Any) -> dict[str, Any]:
    """Fail closed on limited/missing diffs; count text lines only inside hunks."""
    unknown = {"complete": False, "additions": None, "deletions": None}
    if not isinstance(body, dict) or body.get("overflow") is not False:
        return unknown
    changes = body.get("changes")
    if not isinstance(changes, list):
        return unknown
    count = body.get("changes_count")
    if count is not None and (not str(count).isdigit() or int(count) != len(changes)):
        return unknown
    additions = deletions = 0
    for change in changes:
        if not isinstance(change, dict) or change.get("too_large") or change.get("collapsed"):
            return unknown
        diff = change.get("diff")
        if not isinstance(diff, str) or (not diff and not change.get("renamed_file")):
            return unknown
        in_hunk = False
        for line in diff.splitlines():
            if line.startswith("@@ "):
                in_hunk = True
            elif in_hunk:
                additions += int(line.startswith("+"))
                deletions += int(line.startswith("-"))
        if diff and not in_hunk:
            return unknown
    return {"complete": True, "additions": additions, "deletions": deletions}


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect GitLab activity for weekly-report")
    parser.add_argument("--since", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--until", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument(
        "--hostname",
        required=True,
        help="Authenticated GitLab hostname; always forwarded to glab api",
    )
    parser.add_argument("--username", default=None, help="Optional GitLab username")
    parser.add_argument(
        "--author-alias",
        action="append",
        default=[],
        help="Exact Git author/committer name or email alias; repeatable",
    )
    args = parser.parse_args()
    try:
        result = collect_gitlab_activity(
            since=args.since,
            until=args.until,
            hostname=args.hostname,
            username=args.username,
            author_aliases=args.author_alias,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
