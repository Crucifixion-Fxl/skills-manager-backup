import importlib.util
from pathlib import Path
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SOURCE = SCRIPTS / "collect_gitlab_activity.py"
spec = importlib.util.spec_from_file_location("gitlab_under_test", SOURCE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def test_gitlab_bounds_are_utc8():
    start, end = g._date_bounds("2026-08-31", "2026-09-04")
    assert g._is_in_range("2026-08-31T00:30:00+08:00", start, end)
    assert not g._is_in_range("2026-09-05T00:30:00+08:00", start, end)


def test_transport_timeout_is_structured_unavailable():
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired("glab", 60)
    r = g.collect_gitlab_activity(since="2026-08-31", until="2026-09-04",
                                  hostname="gitlab.addx.ai", client=g.GlabClient("gitlab.addx.ai", runner))
    assert r["available"] is False
    assert r["totals"] == {}


def test_mr_selection_uses_both_bounds_not_current_open_state():
    # An MR created during the period remains a created-this-week fact even
    # when it was merged later; the later merge must not enter this week.
    rows = [
        {"iid": 1, "created_at": "2026-08-31T00:30:00+08:00", "merged_at": "2026-09-07T01:00:00+08:00", "state": "merged"},
        {"iid": 2, "created_at": "2026-08-01T00:00:00Z", "merged_at": "2026-09-04T17:00:00+08:00", "state": "merged"},
        {"iid": 3, "created_at": "2026-09-05T00:30:00+08:00", "state": "opened"},
        {"iid": 4, "created_at": "2026-08-01T00:00:00Z", "closed_at": "2026-09-03T03:00:00Z", "updated_at": "2026-09-07T01:00:00Z", "state": "closed"},
    ]
    r = g.select_merge_requests(rows, "2026-08-31", "2026-09-04")
    assert [m["iid"] for m in r["created"]] == [1]
    assert [m["iid"] for m in r["merged"]] == [2]
    assert [m["iid"] for m in r["closed"]] == [4]


@pytest.mark.parametrize("body", [
    {"overflow": True, "changes": [{"diff": "@@ -0,0 +1 @@\n+line"}]},
    {"overflow": False, "changes": [{"too_large": True, "diff": "@@ -0,0 +1 @@\n+line"}]},
    {"overflow": False, "changes": [{"collapsed": True, "diff": "@@ -0,0 +1 @@\n+line"}]},
    {"overflow": False, "changes": [{}]},
    {},
])
def test_incomplete_diff_is_not_zero_or_complete(body):
    r = g.diff_stats(body)
    assert r["complete"] is False
    assert r["additions"] is None
    assert r["deletions"] is None


def test_diff_hunks_count_content_that_looks_like_headers():
    # +++value is an added line whose contents start with ++, not a file header.
    body = {"overflow": False, "changes": [{"diff": "--- a/file\n+++ b/file\n@@ -1 +1,2 @@\n-old\n+++value\n+new\n"}]}
    assert g.diff_stats(body) == {"complete": True, "additions": 2, "deletions": 1}


@pytest.mark.parametrize("flag", ["too_large", "collapsed"])
def test_limit_flags_reject_an_otherwise_complete_diff(flag):
    body = {"overflow": False, "changes": [{"diff": "@@ -0,0 +1 @@\n+line"}]}
    assert g.diff_stats(body) == {"complete": True, "additions": 1, "deletions": 0}
    body["changes"][0][flag] = True
    assert g.diff_stats(body) == {"complete": False, "additions": None, "deletions": None}


def test_empty_pagination_is_an_error_not_no_activity():
    with pytest.raises(ValueError):
        g._decode_json_stream("", paginate=True)
