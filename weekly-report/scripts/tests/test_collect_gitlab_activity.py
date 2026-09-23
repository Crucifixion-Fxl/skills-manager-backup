from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from collect_gitlab_activity import (  # noqa: E402
    GlabApiError,
    GlabClient,
    UNRESOLVED_ATTRIBUTION,
    collect_gitlab_activity,
)


LCHEN_USER = {
    "username": "lchen",
    "name": "Lei Chen",
    "public_email": None,
}

VIP_PROJECT = {
    "id": 1134,
    "path_with_namespace": "CLOUD/vip-service",
    "web_url": "https://gitlab.addx.ai/CLOUD/vip-service",
    "default_branch": "master",
}

VIP_EVENT = {
    "id": 313541,
    "created_at": "2026-07-10T03:22:27.029Z",
    "project_id": 1134,
    "action_name": "pushed new",
    "push_data": {
        "commit_count": 77,
        "action": "created",
        "ref_type": "branch",
        "commit_from": None,
        "commit_to": "810cd36fb3cc90a1e29587ac6bb531d458d9bafa",
        "ref": "feat/issue-53-payment-migration-cdc",
    },
}

VIP_COMMITS = [
    {
        "id": commit_id,
        "short_id": commit_id[:8],
        "title": title,
        "author_name": "chenlaijian",
        "author_email": "chenlaijian@chenlaijiandeMacBook-Pro.local",
        "committer_name": "chenlaijian",
        "committer_email": "chenlaijian@chenlaijiandeMacBook-Pro.local",
        "authored_date": authored_date,
        "committed_date": "2026-07-10T11:19:51.000+08:00",
        "web_url": f"https://gitlab.addx.ai/CLOUD/vip-service/-/commit/{commit_id}",
    }
    for commit_id, title, authored_date in (
        (
            "810cd36fb3cc90a1e29587ac6bb531d458d9bafa",
            "feat(flink-cdc): add product migration and launch initialization",
            "2026-07-10T11:22:18.000+08:00",
        ),
        (
            "201e0369e0000000000000000000000000000000",
            "fix: correct payment migration mappings",
            "2026-07-09T16:57:25.000+08:00",
        ),
        (
            "6089dfc4e0000000000000000000000000000000",
            "fix(flink-cdc): convert airwallex intent amount to cents",
            "2026-07-08T17:51:03.000+08:00",
        ),
        (
            "458ac4a3e0000000000000000000000000000000",
            "fix(flink-cdc): isolate payment migration server id range",
            "2026-07-08T16:47:41.000+08:00",
        ),
        (
            "3969f08be0000000000000000000000000000000",
            "fix(flink-cdc): use entitlement CDC credentials for payment migration",
            "2026-07-08T14:44:52.000+08:00",
        ),
        (
            "91299a72e0000000000000000000000000000000",
            "feat(cdc): add payment migration job",
            "2026-07-07T19:51:00.000+08:00",
        ),
    )
]


class FakeClient:
    """Serves ``repository/compare`` keyed by the exact (from, to) pair."""

    def __init__(self, *, events=None, compares=None, fail_events=False, fail_ranges=None):
        self.events = list(events or [])
        # {(base_sha_or_branch, head_sha): [commit, ...]}
        self.compares = dict(compares or {})
        self.fail_events = fail_events
        self.fail_ranges = set(fail_ranges or [])
        self.calls: list[tuple[str, bool]] = []

    def get(self, path: str, *, paginate: bool = False):
        self.calls.append((path, paginate))
        if path == "users?username=lchen":
            return [LCHEN_USER]
        if path == "user":
            return {
                "username": "zlin",
                "name": "Zhi Lin",
                "email": "zlin@a4x.io",
                "commit_email": "zlin@a4x.io",
            }
        if path.startswith("users/") and "/events?" in path:
            if self.fail_events:
                raise GlabApiError("events unavailable")
            return self.events
        if path == "projects/1134":
            return VIP_PROJECT
        if path == "projects/42":
            return {
                "id": 42,
                "path_with_namespace": "engineering/example",
                "web_url": "https://gitlab.addx.ai/engineering/example",
                "default_branch": "main",
            }
        if "/repository/compare?" in path:
            query = parse_qs(urlsplit(path).query)
            # Two-dot semantics are part of the attribution contract: a
            # three-dot (merge-base) comparison can return commits the push
            # never introduced when a criss-cross history has several merge
            # bases.
            assert query.get("straight") == ["true"], (
                f"compare must request straight=true, got {path}"
            )
            key = (query["from"][0], query["to"][0])
            if key in self.fail_ranges:
                raise GlabApiError(f"compare {key[0]}..{key[1]} unavailable")
            return {"commits": list(self.compares.get(key, []))}
        raise AssertionError(f"unexpected API path: {path}")


def _compare_keys(client: FakeClient) -> list[tuple[str, str]]:
    keys = []
    for path, _ in client.calls:
        if "/repository/compare?" in path:
            query = parse_qs(urlsplit(path).query)
            keys.append((query["from"][0], query["to"][0]))
    return keys


def _collect(client: FakeClient, *aliases: str):
    return collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        username="lchen",
        author_aliases=aliases,
        client=client,
    )


VIP_HEAD = "810cd36fb3cc90a1e29587ac6bb531d458d9bafa"
# commit_from is null, so attribution is bounded by the default branch.
VIP_RANGE = ("master", VIP_HEAD)


def test_historical_vip_push_is_preserved_when_author_identity_is_unresolved():
    client = FakeClient(events=[VIP_EVENT], compares={VIP_RANGE: VIP_COMMITS})

    result = _collect(client)

    assert result["available"] is True
    assert result["complete"] is True
    assert result["totals"] == {
        "attributed_commits": 0,
        "merge_commits": 0,
        "rebase_duplicates_collapsed": 0,
        "push_events": 1,
        "active_projects": 1,
        "unresolved_projects": 1,
        "collection_errors": 0,
    }
    project = result["projects"][0]
    assert project["path_with_namespace"] == "CLOUD/vip-service"
    assert project["activity_status"] == "push_only"
    assert project["activity_note_code"] == UNRESOLVED_ATTRIBUTION
    assert project["commit_count"] == 0
    assert "commit_count" not in project["refs"][0]
    assert project["refs"][0]["ref"] == "feat/issue-53-payment-migration-cdc"

    assert _compare_keys(client) == [VIP_RANGE]


def test_explicit_exact_author_alias_attributes_the_six_historical_commits():
    client = FakeClient(events=[VIP_EVENT], compares={VIP_RANGE: VIP_COMMITS})

    result = _collect(client, "chenlaijian")

    project = result["projects"][0]
    assert result["totals"]["attributed_commits"] == 6
    assert result["totals"]["unresolved_projects"] == 0
    assert project["activity_status"] == "attributed"
    assert project["activity_note_code"] is None
    assert project["commit_count"] == 6
    assert {commit["short_id"] for commit in project["commits"]} == {
        "810cd36f",
        "201e0369",
        "6089dfc4",
        "458ac4a3",
        "3969f08b",
        "91299a72",
    }


def test_commits_are_deduplicated_across_pushed_refs():
    commit = {
        "id": "a" * 40,
        "short_id": "a" * 8,
        "title": "fix: shared commit",
        "author_name": "zlin",
        "author_email": "zlin@a4x.io",
        "committer_name": "zlin",
        "committer_email": "zlin@a4x.io",
        "authored_date": "2026-07-08T01:00:00Z",
        "committed_date": "2026-07-08T01:00:00Z",
    }
    events = [
        {
            "created_at": "2026-07-08T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": ref,
                "ref_type": "branch",
                "commit_count": 999,
                "commit_from": "f" * 40,
                "commit_to": "a" * 40,
            },
        }
        for ref in ("feat/a", "feat/b")
    ]
    client = FakeClient(events=events, compares={("f" * 40, "a" * 40): [commit]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    assert result["totals"]["push_events"] == 2
    assert result["projects"][0]["commit_count"] == 1
    assert len(result["projects"][0]["refs"]) == 2


def test_events_api_failure_is_not_reported_as_zero_activity():
    result = _collect(FakeClient(fail_events=True))

    assert result["available"] is False
    assert result["complete"] is False
    assert result["reason"] == "events_api_failed"
    assert result["projects"] == []
    assert result["totals"] == {}


def test_push_range_failure_keeps_push_evidence_and_marks_partial_result():
    result = _collect(FakeClient(events=[VIP_EVENT], fail_ranges={VIP_RANGE}))

    assert result["available"] is True
    assert result["complete"] is False
    assert result["totals"]["push_events"] == 1
    assert result["totals"]["collection_errors"] == 1
    assert result["projects"][0]["activity_status"] == "push_only"
    assert result["projects"][0]["activity_note_code"] == UNRESOLVED_ATTRIBUTION
    assert result["errors"][0]["scope"] == "push_range"


def test_one_change_committed_under_two_aliases_of_the_same_person_collapses():
    """One change committed under two name/email aliases of the same user.

    Both aliases resolve to the same user, so identity must not be part of the
    logical key or one change is counted twice.
    """
    first = _zlin_commit("a" * 40, authored_date="2026-07-08T17:06:37.000+08:00")
    second = _zlin_commit("b" * 40, authored_date="2026-07-09T12:12:34.000+00:00")
    for commit in (first, second):
        commit["title"] = "feat(skills): add retro-collector skill"
    second["author_name"] = "Zhi Lin"
    second["author_email"] = "zlin@workstation.local"
    second["committer_name"] = "Zhi Lin"
    second["committer_email"] = "zlin@workstation.local"

    event = {
        "created_at": "2026-07-09T13:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "main",
            "ref_type": "branch",
            "commit_count": 2,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(events=[event], compares={("0" * 40, "b" * 40): [first, second]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    assert result["totals"]["rebase_duplicates_collapsed"] == 1


def test_compare_requests_two_dot_straight_semantics():
    """The push range must be queried as `commit_from..commit_to`.

    GitLab defaults to three-dot (merge-base) comparison. With a criss-cross
    history producing several merge bases, the chosen base may not dominate
    `commit_from`, so the response could include commits this push never
    introduced and re-inflate attribution.
    """
    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/straight",
            "ref_type": "branch",
            "commit_count": 1,
            "commit_from": "0" * 40,
            "commit_to": "a" * 40,
        },
    }
    client = FakeClient(
        events=[event], compares={("0" * 40, "a" * 40): [_zlin_commit("a" * 40)]}
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    compare_calls = [path for path, _ in client.calls if "/repository/compare?" in path]
    assert compare_calls, "expected a compare call"
    for path in compare_calls:
        assert parse_qs(urlsplit(path).query)["straight"] == ["true"]


def test_two_independent_changes_sharing_a_subject_are_merged_known_limitation():
    """Documents the accepted cost of using the subject as a patch-identity proxy.

    GitLab exposes no `git patch-id`, so two genuinely independent commits that
    share one subject inside a project collapse into a single logical change. The
    inverse error (counting one change many times, once per branch it travelled
    through) was measured as far larger, so this trade is deliberate. The count is
    of logical changes, not of commit objects, and the collapse is disclosed via
    `rebase_duplicates_collapsed`.
    """
    first = _zlin_commit("a" * 40, authored_date="2026-07-05T01:00:00Z")
    second = _zlin_commit("b" * 40, authored_date="2026-07-09T01:00:00Z")
    # Four days apart, different real changes, unhelpfully identical subject.
    for commit in (first, second):
        commit["title"] = "fix: typo"

    event = {
        "created_at": "2026-07-09T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "main",
            "ref_type": "branch",
            "commit_count": 2,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(events=[event], compares={("0" * 40, "b" * 40): [first, second]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    # The collapse is reported, never silent.
    assert result["totals"]["rebase_duplicates_collapsed"] == 1


def test_truncated_compare_is_an_error_not_a_silently_smaller_count():
    """`compare_timeout` means GitLab cut the comparison short.

    Accepting the partial `commits` array would under-count while reporting a
    complete total, so it must fail closed and keep push evidence.
    """

    class TruncatingClient(FakeClient):
        def get(self, path, *, paginate=False):
            if "/repository/compare?" in path:
                self.calls.append((path, paginate))
                return {
                    "commits": [_zlin_commit("a" * 40)],
                    "compare_timeout": True,
                }
            return super().get(path, paginate=paginate)

    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/huge",
            "ref_type": "branch",
            "commit_count": 5000,
            "commit_from": "0" * 40,
            "commit_to": "a" * 40,
        },
    }
    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=TruncatingClient(events=[event]),
    )

    assert result["available"] is True
    assert result["complete"] is False
    assert result["totals"]["attributed_commits"] == 0
    assert result["totals"]["collection_errors"] == 1
    assert result["errors"][0]["scope"] == "push_range"
    assert "compare_timeout" in result["errors"][0]["error"]
    # Push evidence survives; it is not rewritten as zero activity.
    assert result["projects"][0]["activity_status"] == "push_only"
    assert result["projects"][0]["push_count"] == 1


def test_a_push_range_larger_than_a_page_is_read_in_full():
    """compare returns one complete object; it is not a paginated list.

    Verified live: 2445 commits arrived in a single response and neither
    `--paginate` nor `per_page` changed that. This pins that a range far larger
    than any default page size is counted in full.
    """
    commits = [
        _zlin_commit(f"{index:040x}", authored_date="2026-07-08T01:00:00Z")
        for index in range(1, 251)
    ]
    for index, commit in enumerate(commits):
        commit["title"] = f"feat: change {index}"

    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/bulk",
            "ref_type": "branch",
            "commit_count": 250,
            "commit_from": "0" * 40,
            "commit_to": f"{250:040x}",
        },
    }
    client = FakeClient(
        events=[event], compares={("0" * 40, f"{250:040x}"): commits}
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["complete"] is True
    assert result["totals"]["attributed_commits"] == 250
    assert result["totals"]["collection_errors"] == 0


def test_glab_client_always_forwards_explicit_hostname_and_pagination():
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, stdout="[][]", stderr="")

    client = GlabClient("gitlab.addx.ai", runner=runner)

    assert client.get("users/zlin/events", paginate=True) == []
    assert calls == [
        [
            "glab",
            "api",
            "--hostname",
            "gitlab.addx.ai",
            "--paginate",
            "users/zlin/events",
        ]
    ]


def _zlin_commit(sha: str, *, authored_date="2026-07-08T01:00:00Z"):
    return {
        "id": sha,
        "short_id": sha[:8],
        "title": f"commit {sha[:8]}",
        "author_name": "zlin",
        "author_email": "zlin@a4x.io",
        "committer_name": "zlin",
        "committer_email": "zlin@a4x.io",
        "authored_date": authored_date,
        "committed_date": authored_date,
    }


def test_push_to_shared_branch_counts_only_what_that_push_introduced():
    """Regression: a master push must not attribute master's whole in-window history.

    The old collector queried ``commits?ref_name=master&since&until`` and counted
    every in-window commit reachable from master, inflating one small push into
    the branch's entire weekly history.
    """
    introduced = [_zlin_commit("a" * 40), _zlin_commit("b" * 40)]
    # Commits already on master before this push; reachable in-window but not pushed now.
    preexisting = [_zlin_commit(c * 40) for c in "cdef"]

    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "master",
            "ref_type": "branch",
            "commit_count": 6,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(
        events=[event],
        compares={
            ("0" * 40, "b" * 40): introduced,
            # Present in the fixture to prove it is never requested.
            ("main", "b" * 40): introduced + preexisting,
        },
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 2
    assert _compare_keys(client) == [("0" * 40, "b" * 40)]


def test_created_branch_is_bounded_by_default_branch_not_full_history():
    """A created ref has no commit_from; bound it to the project default branch."""
    branch_only = [_zlin_commit("a" * 40)]
    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/new",
            "ref_type": "branch",
            "commit_count": 40,
            "commit_from": None,
            "commit_to": "a" * 40,
        },
    }
    client = FakeClient(events=[event], compares={("main", "a" * 40): branch_only})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    assert _compare_keys(client) == [("main", "a" * 40)]


def test_multiple_pushes_to_one_branch_are_each_bounded_and_deduplicated():
    first = _zlin_commit("a" * 40)
    second = _zlin_commit("b" * 40)
    events = [
        {
            "created_at": "2026-07-08T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": "feat/x",
                "ref_type": "branch",
                "commit_count": 1,
                "commit_from": "0" * 40,
                "commit_to": "a" * 40,
            },
        },
        {
            "created_at": "2026-07-09T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": "feat/x",
                "ref_type": "branch",
                "commit_count": 1,
                # Second push re-reports the first commit plus a new one.
                "commit_from": "a" * 40,
                "commit_to": "b" * 40,
            },
        },
    ]
    client = FakeClient(
        events=events,
        compares={
            ("0" * 40, "a" * 40): [first],
            ("a" * 40, "b" * 40): [first, second],
        },
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 2
    assert result["projects"][0]["refs"][0]["push_events"] == 2


def test_ref_deletion_introduces_no_commits_but_keeps_push_evidence():
    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/gone",
            "ref_type": "branch",
            "commit_count": 0,
            "commit_from": "a" * 40,
            "commit_to": None,
        },
    }
    client = FakeClient(events=[event])

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 0
    assert result["totals"]["collection_errors"] == 0
    assert result["projects"][0]["activity_status"] == "push_only"
    assert _compare_keys(client) == []


def test_out_of_window_commits_in_a_push_range_are_excluded():
    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/y",
            "ref_type": "branch",
            "commit_count": 2,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(
        events=[event],
        compares={
            ("0" * 40, "b" * 40): [
                _zlin_commit("a" * 40, authored_date="2026-06-01T01:00:00Z"),
                _zlin_commit("b" * 40, authored_date="2026-07-08T01:00:00Z"),
            ]
        },
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1


def _rebase_copy(sha: str, *, committed_date: str):
    """Same logical change as its siblings: identical author+authored_date+title."""
    return {
        "id": sha,
        "short_id": sha[:8],
        "title": "fix(notification): isolate Novu credential readiness by provider",
        "author_name": "zlin",
        "author_email": "zlin@a4x.io",
        "committer_name": "zlin",
        "committer_email": "zlin@a4x.io",
        "authored_date": "2026-07-08T16:39:27.000+08:00",
        "committed_date": committed_date,
    }


def test_rebased_copies_across_stage_and_release_collapse_to_one_commit():
    """Regression: one change pushed through feature/stage/release counted 3x.

    Rebase and cherry-pick keep author identity, authored_date and subject but
    rewrite committer date and SHA, so SHA deduplication cannot see the copies.
    Observed on real data as 381 attributed commits for only 200 distinct titles.
    """
    events = [
        {
            "created_at": "2026-07-08T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": ref,
                "ref_type": "branch",
                "commit_count": 1,
                "commit_from": "0" * 40,
                "commit_to": head,
            },
        }
        for ref, head in (
            ("issue-207-fix", "a" * 40),
            ("issue-207-fix-stage", "b" * 40),
            ("issue-207-fix-release", "c" * 40),
        )
    ]
    client = FakeClient(
        events=events,
        compares={
            ("0" * 40, "a" * 40): [
                _rebase_copy("a" * 40, committed_date="2026-07-08T16:39:27.000+08:00")
            ],
            ("0" * 40, "b" * 40): [
                _rebase_copy("b" * 40, committed_date="2026-07-08T20:29:43.000+08:00")
            ],
            ("0" * 40, "c" * 40): [
                _rebase_copy("c" * 40, committed_date="2026-07-09T17:50:43.000+08:00")
            ],
        },
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    project = result["projects"][0]
    assert result["totals"]["attributed_commits"] == 1
    assert result["totals"]["rebase_duplicates_collapsed"] == 2
    assert project["commit_count"] == 1
    assert project["rebase_duplicates_collapsed"] == 2
    # The earliest committed copy represents the logical change.
    assert project["commits"][0]["short_id"] == "a" * 8
    # Push evidence for all three refs is preserved.
    assert {ref["ref"] for ref in project["refs"]} == {
        "issue-207-fix",
        "issue-207-fix-stage",
        "issue-207-fix-release",
    }


def test_distinct_changes_by_one_author_are_never_collapsed():
    events = [
        {
            "created_at": "2026-07-08T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": "feat/z",
                "ref_type": "branch",
                "commit_count": 2,
                "commit_from": "0" * 40,
                "commit_to": "b" * 40,
            },
        }
    ]
    same_time = "2026-07-08T01:00:00Z"
    first = _zlin_commit("a" * 40, authored_date=same_time)
    second = _zlin_commit("b" * 40, authored_date=same_time)
    # Same author and authored_date, genuinely different subjects.
    first["title"] = "feat: add retry"
    second["title"] = "test: cover retry"
    client = FakeClient(events=events, compares={("0" * 40, "b" * 40): [first, second]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 2
    assert result["totals"]["rebase_duplicates_collapsed"] == 0


def test_commits_without_a_usable_logical_key_are_counted_individually():
    events = [
        {
            "created_at": "2026-07-08T02:00:00Z",
            "project_id": 42,
            "push_data": {
                "ref": "feat/w",
                "ref_type": "branch",
                "commit_count": 2,
                "commit_from": "0" * 40,
                "commit_to": "b" * 40,
            },
        }
    ]
    first = _zlin_commit("a" * 40)
    second = _zlin_commit("b" * 40)
    # Missing titles make the logical key incomplete; fail open to counting.
    first["title"] = ""
    second["title"] = ""
    client = FakeClient(events=events, compares={("0" * 40, "b" * 40): [first, second]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 2
    assert result["totals"]["rebase_duplicates_collapsed"] == 0


def test_merge_commits_are_reported_separately_not_as_owner_commits():
    """A merge is an integration action, not a change the owner wrote.

    Real data showed 30 of 223 attributed commits were merges into
    ``master_for_stage`` / ``release/*``.
    """
    authored = _zlin_commit("a" * 40)
    merge = _zlin_commit("b" * 40)
    merge["title"] = "Merge branch 'feature/x' into 'master_for_stage'"
    merge["parent_ids"] = ["c" * 40, "d" * 40]
    authored["parent_ids"] = ["0" * 40]

    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "master_for_stage",
            "ref_type": "branch",
            "commit_count": 2,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(
        events=[event], compares={("0" * 40, "b" * 40): [authored, merge]}
    )

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    project = result["projects"][0]
    assert result["totals"]["attributed_commits"] == 1
    assert result["totals"]["merge_commits"] == 1
    assert project["commit_count"] == 1
    assert project["merge_commits"] == 1
    # The merge subject must not appear among the owner's commits.
    assert [c["short_id"] for c in project["commits"]] == ["a" * 8]


def test_recommitted_change_with_a_new_author_date_still_collapses():
    """Amend/cherry-pick rewrites the author date, so dates cannot be in the key.

    Real data showed the same subject committed three seconds apart.
    """
    first = _zlin_commit("a" * 40, authored_date="2026-07-08T16:27:06.000+08:00")
    second = _zlin_commit("b" * 40, authored_date="2026-07-08T16:27:09.000+08:00")
    first["title"] = second["title"] = "ci(notification): run Redis gate on standard runner"

    event = {
        "created_at": "2026-07-08T02:00:00Z",
        "project_id": 42,
        "push_data": {
            "ref": "feat/ci",
            "ref_type": "branch",
            "commit_count": 2,
            "commit_from": "0" * 40,
            "commit_to": "b" * 40,
        },
    }
    client = FakeClient(events=[event], compares={("0" * 40, "b" * 40): [first, second]})

    result = collect_gitlab_activity(
        since="2026-07-04",
        until="2026-07-10",
        hostname="gitlab.addx.ai",
        client=client,
    )

    assert result["totals"]["attributed_commits"] == 1
    assert result["totals"]["rebase_duplicates_collapsed"] == 1
    # Earliest committed copy represents the change.
    assert result["projects"][0]["commits"][0]["short_id"] == "a" * 8
