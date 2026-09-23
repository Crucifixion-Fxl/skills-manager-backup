import copy
import importlib.util
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
WORKFLOWS = SKILL / "references" / "workflows"
WAKE_TEMPLATE = WORKFLOWS / "webhook-wake-desk.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_webhook", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
ZERO = "0" * 40
HEAD = "a" * 40
PARENT = "b" * 40
FIELDS = ("object", "event", "placement", "project", "ref", "mr_iid")
RECORD_KEYS = frozenset(SYNC._record("k", "o", "e", "p", 1))
KEY_RE = re.compile(r"webhook-[0-9a-f]{16}")
USER = {"id": 12, "name": "Alice", "username": "alice", "avatar_url": None, "email": "[REDACTED]"}


# ── GitLab project-webhook payloads (shape of GitLab's documented examples) ──


def project(default_branch="main"):
    return {
        "id": PID, "name": "pilot", "description": "", "web_url": WEB, "avatar_url": None,
        "git_ssh_url": "ssh://git@127.0.0.1:2222/buzz-sync-test/pilot.git", "git_http_url": f"{WEB}.git",
        "namespace": "buzz-sync-test", "visibility_level": 0, "path_with_namespace": "buzz-sync-test/pilot",
        "default_branch": default_branch, "ci_config_path": None, "homepage": WEB,
    }


def repository():
    return {"name": "pilot", "url": "ssh://git@127.0.0.1:2222/buzz-sync-test/pilot.git", "description": "",
            "homepage": WEB, "visibility_level": 0}


def commit(sha=HEAD, title="fix: restore"):
    return {"id": sha, "message": f"{title}\n\nbody", "title": title, "timestamp": "2026-09-13T02:00:00+00:00",
            "url": f"{WEB}/-/commit/{sha}", "author": {"name": "Alice", "email": "[REDACTED]"},
            "added": [], "modified": ["README.md"], "removed": []}


def push_hook(before=PARENT, after=HEAD, ref="refs/heads/feature/x", count=2, title="fix: restore",
              kind="push"):
    commits = [commit(after, title)] if count and after != ZERO else []
    return {
        "object_kind": kind, "event_name": kind, "before": before, "after": after, "ref": ref,
        "ref_protected": False, "checkout_sha": None if after == ZERO else after, "message": None,
        "user_id": 12, "user_name": "Alice", "user_username": "alice", "user_email": "",
        "user_avatar": None, "project_id": PID, "project": project(), "commits": commits,
        "total_commits_count": count, "push_options": {}, "repository": repository(),
    }


def tag_hook(before=ZERO, after=HEAD, ref="refs/tags/v1.2.0"):
    return push_hook(before=before, after=after, ref=ref, count=0, kind="tag_push")


def label(title):
    return {"id": 206, "title": title, "color": "#ffffff", "project_id": PID, "created_at": "2026-09-01 00:00:00 UTC",
            "updated_at": "2026-09-01 00:00:00 UTC", "template": False, "description": None, "type": "ProjectLabel",
            "group_id": None}


def issue_hook(action, iid=182, title="Checkout fails", updated="2026-09-13 02:00:00 UTC"):
    return {
        "object_kind": "issue", "event_type": "issue", "user": USER, "project": project(),
        "object_attributes": {
            "id": 9000 + iid, "iid": iid, "title": title, "description": "steps", "action": action,
            "state": "closed" if action == "close" else "opened", "state_id": 2 if action == "close" else 1,
            "author_id": 12, "assignee_ids": [13], "assignee_id": 13, "project_id": PID, "milestone_id": None,
            "created_at": "2026-09-12 01:00:00 UTC", "updated_at": updated, "confidential": False,
            "type": "Issue", "url": f"{WEB}/-/issues/{iid}", "labels": [label("type::feature")],
        },
        "repository": repository(),
        "assignees": [{"id": 13, "name": "Bob", "username": "bob", "avatar_url": None}],
        "labels": [label("type::feature")],
        "changes": {"updated_at": {"previous": "2026-09-12 01:00:00 UTC", "current": updated}},
    }


def mr_hook(action, iid=31, title="Restore checkout", updated="2026-09-13 02:00:00 UTC", user=USER):
    state = {"close": "closed", "merge": "merged"}.get(action, "opened")
    return {
        "object_kind": "merge_request", "event_type": "merge_request", "user": user, "project": project(),
        "object_attributes": {
            "id": 900 + iid, "iid": iid, "title": title, "action": action, "state": state,
            "source_branch": "feature/x", "target_branch": "main", "source_project_id": PID,
            "target_project_id": PID, "author_id": 12, "assignee_ids": [], "reviewer_ids": [13],
            "draft": False, "work_in_progress": False, "merge_status": "can_be_merged",
            "detailed_merge_status": "mergeable", "created_at": "2026-09-12 01:00:00 UTC",
            "updated_at": updated, "url": f"{WEB}/-/merge_requests/{iid}", "description": "",
            "last_commit": commit(), "labels": [],
        },
        "labels": [], "changes": {}, "assignees": [],
        "reviewers": [{"id": 13, "name": "Bob", "username": "bob", "avatar_url": None}],
        "repository": repository(),
    }


def note_hook(noteable_type, note_id=1243, body="nit", action="create", updated="2026-09-13 02:00:00 UTC"):
    payload = {
        "object_kind": "note", "event_type": "note", "user": USER, "project_id": PID, "project": project(),
        "repository": repository(),
        "object_attributes": {
            "id": note_id, "note": body, "noteable_type": noteable_type, "author_id": 12,
            "created_at": "2026-09-13 02:00:00 UTC", "updated_at": updated, "project_id": PID,
            "attachment": None, "line_code": None, "commit_id": HEAD if noteable_type == "Commit" else "",
            "noteable_id": None if noteable_type == "Commit" else 9182, "system": False, "st_diff": None,
            "action": action, "url": f"{WEB}/-/commit/{HEAD}#note_{note_id}",
        },
    }
    attached = {"Commit": ("commit", commit()),
                "Issue": ("issue", {"id": 9182, "iid": 182, "title": "Checkout fails", "state": "opened"}),
                "MergeRequest": ("merge_request", {"id": 931, "iid": 31, "title": "Restore", "state": "opened"}),
                "Snippet": ("snippet", {"id": 53, "title": "snippet1", "file_name": "test.rb"})}
    if noteable_type in attached:
        name, value = attached[noteable_type]
        payload[name] = value
    return payload


def pipeline_hook(pipeline_id, status, ref="main", mr_iid=None, duration=300):
    terminal = status in ("success", "failed", "canceled")
    merge_request = None
    if mr_iid is not None:
        merge_request = {"id": 900 + mr_iid, "iid": mr_iid, "title": "Restore checkout",
                         "source_branch": "feature/x", "source_project_id": PID, "target_branch": "main",
                         "target_project_id": PID, "state": "opened", "merge_status": "can_be_merged",
                         "detailed_merge_status": "mergeable", "url": f"{WEB}/-/merge_requests/{mr_iid}"}
    return {
        "object_kind": "pipeline",
        "object_attributes": {
            "id": pipeline_id, "iid": pipeline_id, "name": None, "ref": ref, "tag": False, "sha": HEAD,
            "before_sha": PARENT, "source": "merge_request_event" if mr_iid else "push", "status": status,
            "detailed_status": status, "stages": ["test"], "created_at": "2026-09-13 01:55:00 UTC",
            "finished_at": "2026-09-13 02:00:00 UTC" if terminal else None, "duration": duration,
            "queued_duration": 2, "variables": [], "url": f"{WEB}/-/pipelines/{pipeline_id}",
        },
        "merge_request": merge_request, "user": USER, "project": project(), "commit": commit(),
        "source_pipeline": None,
        "builds": [{"id": 380, "stage": "test", "name": "unit", "status": status, "created_at": "2026-09-13 01:55:00 UTC",
                    "started_at": None, "finished_at": None, "duration": None, "queued_duration": None,
                    "failure_reason": None, "when": "on_success", "manual": False, "allow_failure": False,
                    "user": USER, "runner": None, "artifacts_file": {"filename": None, "size": None},
                    "environment": None}],
    }


def build_hook():
    return {
        "object_kind": "build", "ref": "main", "tag": False, "before_sha": PARENT, "sha": HEAD,
        "retries_count": 0, "build_id": 380, "build_name": "unit", "build_stage": "test",
        "build_status": "failed", "build_created_at": "2026-09-13 01:55:00 UTC",
        "build_started_at": "2026-09-13 01:56:00 UTC", "build_finished_at": "2026-09-13 02:00:00 UTC",
        "build_duration": 240.0, "build_queued_duration": 2.0, "build_allow_failure": False,
        "build_failure_reason": "script_failure", "pipeline_id": 51, "runner": None, "project_id": PID,
        "project_name": "buzz-sync-test / pilot", "user": USER,
        "commit": {"id": 51, "name": None, "sha": HEAD, "message": "fix: restore", "author_name": "Alice",
                   "author_email": "[REDACTED]", "status": "failed", "duration": 240},
        "repository": repository(), "project": project(), "environment": None,
    }


def wiki_hook(action, slug="runbook", title="Runbook", version=HEAD):
    return {
        "object_kind": "wiki_page", "user": USER, "project": project(),
        "wiki": {"web_url": f"{WEB}/-/wikis/home", "git_ssh_url": "ssh://git@127.0.0.1:2222/buzz-sync-test/pilot.wiki.git",
                 "git_http_url": f"{WEB}.wiki.git", "path_with_namespace": "buzz-sync-test/pilot.wiki",
                 "default_branch": "main"},
        "object_attributes": {"title": title, "content": "steps", "format": "markdown", "message": f"{action} {slug}",
                              "slug": slug, "url": f"{WEB}/-/wikis/{slug}", "action": action,
                              "diff_url": f"{WEB}/-/wikis/{slug}/diff?version_id={version}", "version_id": version},
    }


def deployment_hook(deployment_id, status, environment="production", ref="main",
                    changed="2026-09-13 02:00:00 +0000"):
    return {
        "object_kind": "deployment", "status": status, "status_changed_at": changed,
        "deployment_id": deployment_id, "deployable_id": 796, "deployable_url": f"{WEB}/-/jobs/796",
        "environment": environment, "environment_tier": environment, "environment_slug": environment,
        "environment_external_url": None, "project": project(), "short_sha": HEAD[:8], "user": USER,
        "user_url": "http://127.0.0.1:8929/alice", "commit_url": f"{WEB}/-/commit/{HEAD}",
        "commit_title": "fix: restore", "ref": ref,
    }


def release_hook(action, tag="v1.2.0", name="1.2.0"):
    return {
        "object_kind": "release", "id": 1, "created_at": "2026-09-13 01:00:00 UTC", "description": "notes",
        "name": name, "released_at": "2026-09-13 01:00:00 UTC", "tag": tag, "project": project(),
        "url": f"{WEB}/-/releases/{tag}", "action": action,
        "assets": {"count": 0, "links": [], "sources": []}, "commit": commit(),
    }


def milestone_hook(action, title="2026-Q4"):
    return {
        "object_kind": "milestone", "event_type": "milestone", "project": project(),
        "object_attributes": {"id": 61, "iid": 10, "title": title, "description": "",
                              "state": "closed" if action == "close" else "active",
                              "created_at": "2026-09-01 00:00:00 UTC", "updated_at": "2026-09-13 02:00:00 UTC",
                              "due_date": None, "start_date": None, "group_id": None, "project_id": PID},
        "action": action,
    }


def feature_flag_hook(active, name="new-checkout"):
    return {
        "object_kind": "feature_flag", "project": project(), "user": USER,
        "user_url": "http://127.0.0.1:8929/alice",
        "object_attributes": {"id": 6, "name": name, "description": "checkout rewrite", "active": active},
    }


def access_token_hook(name="pilot-read-api", expires="2026-09-18"):
    return {
        "object_kind": "access_token", "project": project(),
        "object_attributes": {"user_id": 90, "created_at": "2026-08-14 16:27:40 UTC", "id": 25, "name": name,
                              "expires_at": expires},
        "event_name": "expiring_access_token",
    }


def emoji_hook():
    return {
        "object_kind": "emoji", "event_type": "award", "user": USER, "project_id": PID, "project": project(),
        "object_attributes": {"user_id": 12, "created_at": "2026-09-13 02:00:00 UTC", "id": 1,
                              "name": "thumbsup", "awardable_type": "Issue", "awardable_id": 9182,
                              "updated_at": "2026-09-13 02:00:00 UTC", "action": "award",
                              "awarded_on_url": f"{WEB}/-/issues/182"},
        "issue": {"id": 9182, "iid": 182, "title": "Checkout fails", "state": "opened"},
    }


def vulnerability_hook():
    # GitLab's vulnerability example has no top-level project; unsupported must not need one.
    return {
        "object_kind": "vulnerability",
        "object_attributes": {"url": f"{WEB}/-/security/vulnerabilities/1", "title": "REXML DoS vulnerability",
                              "state": "confirmed", "project_id": PID, "severity": "high",
                              "report_type": "dependency_scanning", "created_at": "2026-09-13 02:00:00 UTC",
                              "updated_at": "2026-09-13 02:00:00 UTC"},
    }


# ── polling-side fixtures for the same change ────────────────────────────────


def push_event(event_id, action="pushed to", ref_type="branch", ref="feature/x", count=2, title="fix: restore"):
    return {
        "id": event_id, "action_name": action, "target_type": None, "target_iid": None, "target_title": None,
        "created_at": "2026-09-13T02:00:00.000Z", "author": {"username": "alice", "name": "Alice"},
        "push_data": {"ref_type": ref_type, "ref": ref, "commit_count": count, "commit_title": title},
    }


def target_event(event_id, target_type, action, iid=None, title="t", note=None):
    return {
        "id": event_id, "action_name": action, "target_type": target_type, "target_iid": iid,
        "target_title": title, "created_at": "2026-09-13T02:00:00.000Z",
        "author": {"username": "alice", "name": "Alice"}, "note": note,
    }


def api_pipeline(pipeline_id, status, ref="main"):
    return {"id": pipeline_id, "iid": pipeline_id, "status": status, "ref": ref, "sha": HEAD, "source": "push",
            "web_url": f"{WEB}/-/pipelines/{pipeline_id}", "updated_at": "2026-09-13T02:00:00.000Z"}


def api_deployment(deployment_id, status):
    return {"id": deployment_id, "iid": 7, "status": status, "ref": "main", "sha": HEAD,
            "environment": {"name": "production"}, "updated_at": "2026-09-13T02:00:00Z"}


def polled(event):
    return SYNC.record_from_event(event, PID, WEB)


def same_change(record):
    return tuple(record[field] for field in FIELDS)


def normalize_one(test, payload):
    result = SYNC.normalize_webhook(payload)
    test.assertEqual(set(result), {"status", "records"})
    test.assertEqual(result["status"], "ok")
    test.assertEqual(len(result["records"]), 1)
    record = result["records"][0]
    test.assertEqual(set(record), RECORD_KEYS)
    return record


def all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


class WebhookEquivalenceTest(unittest.TestCase):
    def assert_same_change(self, payload, polling_record):
        record = normalize_one(self, payload)
        self.assertEqual(same_change(record), same_change(polling_record))
        return record

    def test_push_and_branches(self):
        """L1-GIS-036 2026-09-18 政策：push（含新建/删除分支）不再唤醒也不产生记录。"""
        for name, payload, event in (
            ("pushed", push_hook(), push_event(1)),
            ("branch_created", push_hook(before=ZERO), push_event(2, action="pushed new")),
            ("branch_deleted", push_hook(after=ZERO, count=0), push_event(3, action="deleted", count=0)),
        ):
            with self.subTest(name=name):
                self.assertEqual(SYNC.normalize_webhook(payload), {"status": "unsupported", "records": []})
                self.assertIsNone(polled(event))

    def test_tag_push(self):
        """L1-GIS-036 tag_push 新建/删除为即时（13:16 恢复），与轮询记录一致；分支 push 不唤醒。"""
        cases = [
            ("tag_created", tag_hook(), push_event(4, action="pushed new", ref_type="tag", ref="v1.2.0", count=0)),
            ("tag_deleted", tag_hook(before=HEAD, after=ZERO),
             push_event(5, action="deleted", ref_type="tag", ref="v1.2.0", count=0)),
        ]
        for name, payload, event in cases:
            with self.subTest(name=name):
                record = self.assert_same_change(payload, polled(event))
                self.assertEqual((record["object"], record["event"], record["placement"], record["ref"]),
                                 ("tag", name, "instant", "v1.2.0"))
        branch = push_hook()
        self.assertEqual(SYNC.normalize_webhook(branch), {"status": "unsupported", "records": []})
        self.assertIsNone(polled(push_event(1)))

    def test_notes(self):
        """L1-GIS-036 note：Issue/MR 评论交给 Thread 与轮询一致；commit/snippet 评论不再通知（2026-09-18 政策）。"""
        for noteable in ("Issue", "MergeRequest"):
            with self.subTest(noteable=noteable):
                event = target_event(10, "Note", "commented on", note={"noteable_type": noteable, "body": "nit"})
                record = self.assert_same_change(note_hook(noteable), polled(event))
                self.assertEqual(record["placement"], "thread")
        for noteable in ("Commit", "Snippet"):
            with self.subTest(noteable=noteable):
                self.assertEqual(SYNC.normalize_webhook(note_hook(noteable)), {"status": "ok", "records": []})
                event = target_event(10, "Note", "commented on", note={"noteable_type": noteable, "body": "nit"})
                self.assertIsNone(SYNC.record_from_event(event, PID, WEB))

    def test_wiki_page(self):
        """L1-GIS-036 2026-09-18 政策：wiki_page 不再唤醒也不产生记录。"""
        for action, polled_action in (("create", "created"), ("update", "updated"), ("delete", "destroyed")):
            with self.subTest(action=action):
                self.assertEqual(SYNC.normalize_webhook(wiki_hook(action)),
                                 {"status": "unsupported", "records": []})
                event = target_event(20, "WikiPage::Meta", polled_action, title="Runbook")
                self.assertIsNone(SYNC.record_from_event(event, PID, WEB))

    def test_milestone(self):
        """L1-GIS-036 milestone create/close/reopen → created/closed/reopened，进独立 Thread（2026-09-18 政策）。"""
        for action, polled_action in (("create", "created"), ("close", "closed"), ("reopen", "reopened")):
            with self.subTest(action=action):
                event = target_event(30, "Milestone", polled_action, title="2026-Q4", iid=10)
                record = self.assert_same_change(milestone_hook(action), polled(event))
                self.assertEqual((record["object"], record["event"], record["placement"], record["source_id"]),
                                 ("milestone", polled_action, "milestone_thread", 10))
                self.assertEqual(record["url"], f"{WEB}/-/milestones/10")

    def test_pipeline(self):
        """L1-GIS-036 pipeline：MR 流水线（merge_request.iid 或 refs/merge-requests/N/head）进 MR Thread，默认分支失败即时，其余不通知（2026-09-18 政策）。"""
        mr_polled = SYNC.record_from_pipeline(api_pipeline(54, "success", ref="refs/merge-requests/31/head"),
                                              PID, "main")
        cases = [
            ("mr via merge_request", pipeline_hook(54, "success", ref="feature/x", mr_iid=31), mr_polled),
            ("mr via ref", pipeline_hook(54, "success", ref="refs/merge-requests/31/head"), mr_polled),
            ("default failed", pipeline_hook(51, "failed"),
             SYNC.record_from_pipeline(api_pipeline(51, "failed"), PID, "main")),
        ]
        for name, payload, polling_record in cases:
            with self.subTest(name=name):
                self.assert_same_change(payload, polling_record)
        mr_record = normalize_one(self, pipeline_hook(54, "success", ref="feature/x", mr_iid=31))
        self.assertEqual((mr_record["placement"], mr_record["mr_iid"]), ("mr_thread", 31))
        self.assertEqual(normalize_one(self, pipeline_hook(51, "failed"))["placement"], "instant")

        for status, ref in (("running", "main"), ("success", "feature/x"), ("failed", "feature/x")):
            with self.subTest(suppressed=f"{status}@{ref}"):
                self.assertIsNone(SYNC.record_from_pipeline(api_pipeline(55, status, ref=ref), PID, "main"))
                if status != "running":
                    self.assertEqual(SYNC.normalize_webhook(pipeline_hook(55, status, ref=ref)),
                                     {"status": "ok", "records": []})

    def test_deployment(self):
        """L1-GIS-036 deployment 与轮询一致；只有失败即时，happy path 不通知（2026-09-18 政策）。"""
        record = self.assert_same_change(deployment_hook(70, "failed"),
                                         SYNC.record_from_deployment(api_deployment(70, "failed"), PID, WEB))
        self.assertEqual((record["object"], record["event"], record["placement"], record["title"]),
                         ("deployment", "failed", "instant", "production"))
        for status in ("running", "success"):
            with self.subTest(status=status):
                self.assertEqual(SYNC.normalize_webhook(deployment_hook(70, status)),
                                 {"status": "ok", "records": []})
                self.assertIsNone(SYNC.record_from_deployment(api_deployment(70, status), PID, WEB))

    def test_release_create(self):
        """L1-GIS-036 release create/delete 为即时（13:16 恢复），与轮询一致；update 不通知。"""
        api_release = {"tag_name": "v1.2.0", "name": "1.2.0", "created_at": "2026-09-13T01:00:00Z",
                       "_links": {"self": f"{WEB}/-/releases/v1.2.0"}}
        record = self.assert_same_change(release_hook("create"), SYNC.record_from_release(api_release, PID, SINCE))
        self.assertEqual((record["object"], record["event"], record["placement"], record["ref"]),
                         ("release", "created", "instant", "v1.2.0"))
        deleted = normalize_one(self, release_hook("delete"))
        self.assertEqual((deleted["event"], deleted["placement"]), ("deleted", "instant"))
        self.assertEqual(SYNC.normalize_webhook(release_hook("update")), {"status": "ok", "records": []})

    def test_issue_actions(self):
        """L1-GIS-036 issue open/close/reopen/update → opened/closed/reopened/updated，交给 Thread。"""
        for action, polled_action in (("open", "opened"), ("close", "closed"), ("reopen", "reopened"),
                                      ("update", "updated")):
            with self.subTest(action=action):
                event = target_event(40, "Issue", polled_action, iid=182, title="Checkout fails")
                record = self.assert_same_change(issue_hook(action), polled(event))
                self.assertEqual((record["object"], record["event"], record["placement"], record["mr_iid"]),
                                 ("issue", polled_action, "thread", None))

    def test_work_item_is_an_issue(self):
        """L1-GIS-036 GitLab 18 的 work_item（Issue、Task）与 issue 得到同一条交给 Thread 的记录。"""
        for item_type in ("Issue", "Task"):
            with self.subTest(item_type=item_type):
                payload = issue_hook("update")
                payload.update({"object_kind": "work_item", "event_type": "work_item"})
                payload["object_attributes"] = {**payload["object_attributes"], "type": item_type}
                record = normalize_one(self, payload)
                self.assertEqual(same_change(record), same_change(normalize_one(self, issue_hook("update"))))
                self.assertEqual((record["object"], record["placement"]), ("issue", "thread"))

    def test_merge_request_actions(self):
        """L1-GIS-036 merge_request 动作名映射到轮询；approved 进 MR Thread 并带 mr_iid。"""
        cases = [("open", "opened"), ("close", "closed"), ("reopen", "reopened"), ("merge", "accepted"),
                 ("approved", "approved"), ("unapproved", "unapproved"), ("update", "updated")]
        for action, polled_action in cases:
            with self.subTest(action=action):
                event = target_event(50, "MergeRequest", polled_action, iid=31, title="Restore checkout")
                record = self.assert_same_change(mr_hook(action), polled(event))
                self.assertEqual((record["object"], record["event"], record["mr_iid"]), ("mr", polled_action, 31))
        approved = normalize_one(self, mr_hook("approved"))
        self.assertEqual((approved["placement"], approved["mr_iid"]), ("mr_thread", 31))
        self.assertEqual(normalize_one(self, mr_hook("update"))["placement"], "thread")


class WebhookOnlyKindsTest(unittest.TestCase):
    def test_note_edit(self):
        """L1-GIS-036 Issue 评论编辑（轮询缺口）单独成事件，key 与新建不同。"""
        created = normalize_one(self, note_hook("Issue", note_id=1244))
        edited = normalize_one(self, note_hook("Issue", note_id=1244, action="update",
                                               updated="2026-09-13 03:00:00 UTC"))
        self.assertEqual((edited["object"], edited["event"], edited["placement"]),
                         ("note", "comment_edited", "thread"))
        self.assertNotEqual(created["key"], edited["key"])

    def test_access_token(self):
        """L1-GIS-036 access_token expiring_access_token → expiring，即时。"""
        record = normalize_one(self, access_token_hook())
        self.assertEqual((record["object"], record["event"], record["placement"], record["project"]),
                         ("access_token", "expiring", "instant", PID))
        self.assertIn("pilot-read-api", record["title"])


class WebhookStatusTest(unittest.TestCase):
    def test_build_is_attached(self):
        """L1-GIS-036 build 附着在 pipeline 消息上，不单独产出记录。"""
        self.assertEqual(SYNC.normalize_webhook(build_hook()), {"status": "attached", "records": []})

    def test_unsupported_kinds(self):
        """L1-GIS-036 emoji、vulnerability 与未知 object_kind 明确返回 unsupported；
        2026-09-18 政策下 push、wiki_page、feature_flag 一并 unsupported（tag_push/release 已恢复）。"""
        unknown = {"object_kind": "repository_update", "project": project(), "user": USER}
        for payload in (emoji_hook(), vulnerability_hook(), unknown, push_hook(),
                        wiki_hook("create"), feature_flag_hook(True)):
            with self.subTest(kind=payload["object_kind"]):
                self.assertEqual(SYNC.normalize_webhook(payload), {"status": "unsupported", "records": []})

    def test_malformed_payload_fails_closed(self):
        """L1-GIS-036 缺 object_kind 或 project.id 时 SyncError。"""
        no_kind = note_hook("Issue", note_id=1244)
        del no_kind["object_kind"]
        no_project = note_hook("Issue", note_id=1244)
        del no_project["project"]
        bad_project_id = issue_hook("open")
        bad_project_id["project"]["id"] = "481"
        no_project_id = pipeline_hook(51, "failed")
        del no_project_id["project"]["id"]
        for name, payload in (("no kind", no_kind), ("no project", no_project), ("string id", bad_project_id),
                              ("no project id", no_project_id), ("not an object", ["push"])):
            with self.subTest(name=name):
                with self.assertRaises(SYNC.SyncError):
                    SYNC.normalize_webhook(payload)

    def test_missing_identity_fails_closed(self):
        """L1-GIS-036 与轮询一样，流水线或部署缺 id 时 SyncError。"""
        no_pipeline_id = pipeline_hook(51, "failed")
        del no_pipeline_id["object_attributes"]["id"]
        no_deployment_id = deployment_hook(70, "success")
        del no_deployment_id["deployment_id"]
        for payload in (no_pipeline_id, no_deployment_id):
            with self.subTest(kind=payload["object_kind"]):
                with self.assertRaises(SYNC.SyncError):
                    SYNC.normalize_webhook(payload)


def ok_payloads():
    return [
        note_hook("Issue", note_id=1244), milestone_hook("close"),
        pipeline_hook(51, "failed"), pipeline_hook(54, "success", ref="feature/x", mr_iid=31),
        deployment_hook(70, "failed"), issue_hook("open"), mr_hook("approved"),
        tag_hook(), release_hook("create"), access_token_hook(),
    ]


class WebhookKeyTest(unittest.TestCase):
    def test_key_format_and_determinism(self):
        """L1-GIS-036 key 为 webhook-<16 hex>，同一 payload 重复归一化得到同一 key，不同变化 key 不同。"""
        keys = []
        for payload in ok_payloads():
            with self.subTest(kind=payload["object_kind"]):
                first = normalize_one(self, payload)["key"]
                again = normalize_one(self, copy.deepcopy(payload))["key"]
                self.assertRegex(first, KEY_RE)
                self.assertTrue(SYNC.EVENT_KEY_RE.fullmatch(first))
                self.assertEqual(first, again)
                keys.append(first)
        self.assertEqual(len(keys), len(set(keys)))

    def test_key_follows_identity_and_status(self):
        """L1-GIS-036 流水线、部署的 key 只随 id 与状态变化，与轮询的 id+状态去重口径一致。"""
        base = normalize_one(self, pipeline_hook(51, "failed"))["key"]
        self.assertEqual(normalize_one(self, pipeline_hook(51, "failed", duration=999))["key"], base)
        self.assertNotEqual(normalize_one(self, pipeline_hook(52, "failed"))["key"], base)
        mr_failed = normalize_one(self, pipeline_hook(54, "failed", ref="feature/x", mr_iid=31))["key"]
        self.assertNotEqual(normalize_one(self, pipeline_hook(54, "success", ref="feature/x", mr_iid=31))["key"],
                            mr_failed)
        deployed = normalize_one(self, deployment_hook(70, "failed"))["key"]
        self.assertEqual(normalize_one(self, deployment_hook(70, "failed", changed="2026-09-13 02:05:00 +0000"))["key"],
                         deployed)
        self.assertNotEqual(normalize_one(self, deployment_hook(70, "blocked"))["key"], deployed)


class WebhookNeutralizeTest(unittest.TestCase):
    def test_gitlab_text_is_neutralized(self):
        """L1-GIS-036 GitLab 来源文本（标题、环境名、token 名）里的 @ 转成全角 ＠，标题折成单行。"""
        payloads = [
            issue_hook("open", title="@nh-dev 请看\n第二行"),
            mr_hook("open", title="@root restore"),
            milestone_hook("create", title="@team Q4"),
            deployment_hook(71, "failed", environment="prod@sg"),
            access_token_hook(name="@token"),
        ]
        for payload in payloads:
            with self.subTest(kind=payload["object_kind"]):
                record = normalize_one(self, payload)
                self.assertTrue(any("＠" in text for text in all_strings(record)), record)
                self.assertFalse(any("@" in text for text in all_strings(record)), record)
                self.assertNotIn("\n", record["title"])
        self.assertEqual(normalize_one(self, payloads[0])["title"], "＠nh-dev 请看 第二行")


class WebhookWakeTemplateTest(unittest.TestCase):
    def read_template(self):
        text = WAKE_TEMPLATE.read_text(encoding="utf-8")
        comments = "\n".join(line for line in text.splitlines() if line.lstrip().startswith("#"))
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        return comments, body

    def test_wake_template(self):
        """L1-GIS-037 on: webhook，trigger 下没有 filter；步骤 if 只用 trigger_object_kind 的 == 与 ||；一个 send_message 发 @<desk> gitlab sync。"""
        comments, body = self.read_template()
        self.assertRegex(body, r"(?m)^\s*on:\s*webhook\s*$")
        self.assertNotRegex(body, r"(?m)^\s*filter:")
        if_match = re.search(r"(?ms)^\s*if:\s*>-?\s*\n(.*?)\n\s*action:", body)
        self.assertIsNotNone(if_match)
        expression = " ".join(if_match.group(1).split())
        self.assertRegex(expression,
                         r'^trigger_object_kind == "[a-z_]+"(?: \|\| trigger_object_kind == "[a-z_]+")*$')
        kinds = re.findall(r'"([a-z_]+)"', expression)
        self.assertTrue(kinds)
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertFalse({"emoji", "vulnerability", "build"} & set(kinds))
        self.assertEqual(re.findall(r"(?m)^\s*(?:-\s*)?action:\s*(\S+)\s*$", body), ["send_message"])
        self.assertRegex(body, r'(?m)^\s*text:\s*"@\S+ gitlab sync"\s*$')
        self.assertIn("X-Webhook-Secret", comments)

    def test_wake_template_has_no_thread_reply_or_outbound_call(self):
        """L1-GIS-037 webhook 触发的 Workflow 不带 reply_in_thread、不含 call_webhook；文件名不含 schedule。"""
        _, body = self.read_template()
        self.assertNotIn("reply_in_thread", body)
        self.assertNotIn("call_webhook", body)
        self.assertNotIn("schedule", WAKE_TEMPLATE.name.lower())
        self.assertEqual([], [path.name for path in WORKFLOWS.glob("*.y*ml") if "schedule" in path.name.lower()])


if __name__ == "__main__":
    unittest.main()
