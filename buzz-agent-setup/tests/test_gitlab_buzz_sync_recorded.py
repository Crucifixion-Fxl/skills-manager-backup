"""L2-1-GIS-003: real GitLab 18.0 recordings through gitlab_buzz_sync.py's pure functions.

The fixtures under tests/fixtures/gitlab_buzz_sync/ were recorded once from the localstack GitLab CE 18.0
(record_gitlab.py) and scrubbed. These tests need no network: they check that the field shapes the script
assumes are the shapes GitLab really returns, including the endpoints the reference marked 「待实测」
(closes_issues, approvals, diffs, feature_flags, access_tokens, wiki/milestone events) and the
project-webhook payloads the normalizer targets. An assertion that fails here documents a real mismatch.
"""
import copy
import datetime as dt
import glob
import importlib.util
import json
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
FIXTURES = SKILL / "tests" / "fixtures" / "gitlab_buzz_sync"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_recorded", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()
META = json.loads((FIXTURES / "_meta.json").read_text(encoding="utf-8"))
OBJECTS = META["objects"]
PID = META["project_id"]
BOT_ID = META["users"]["bot"]["id"]
DEV = META["users"]["dev"]["username"]
MAINTAINER = META["users"]["maintainer"]["username"]
SINCE = META["recorded_at"]
DESK = "d" * 64
FIELDS = ("object", "event", "placement", "project", "ref", "mr_iid")


def recording(name):
    return json.loads((FIXTURES / "api" / f"{name}.json").read_text(encoding="utf-8"))


def body(name):
    return recording(name)["body"]


def webhook(kind, action=None):
    matches = []
    for path in sorted(glob.glob(str(FIXTURES / "webhooks" / "*.json"))):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))["body"]
        attrs = payload.get("object_attributes") or {}
        if payload.get("object_kind") == kind and action in (None, attrs.get("action"), payload.get("action"),
                                                             attrs.get("status"), payload.get("status")):
            matches.append(payload)
    return matches


def one_webhook(kind, action=None):
    matches = webhook(kind, action)
    if len(matches) != 1:
        raise AssertionError(f"expected one recorded {kind}/{action} webhook, found {len(matches)}")
    return matches[0]


PROJECT = body("project")
WEB = PROJECT["web_url"]
EVENTS = body("events")


def event(target_type, action, **match):
    found = [e for e in EVENTS if e.get("target_type") == target_type and e.get("action_name") == action
             and all((e.get(k) if not isinstance(v, dict) else {kk: (e.get(k) or {}).get(kk) for kk in v}) == v
                     for k, v in match.items())]
    if not found:
        raise AssertionError(f"no recorded event {target_type}/{action} {match}")
    return found[0]


def push_event(action, ref_type, ref):
    found = [e for e in EVENTS if e.get("action_name") == action and (e.get("push_data") or {}).get("ref_type") == ref_type
             and e["push_data"].get("ref") == ref]
    if len(found) != 1:
        raise AssertionError(f"expected one push event {action} {ref_type} {ref}, found {len(found)}")
    return found[0]


def polled(item):
    return SYNC.record_from_event(item, PID, WEB)


def same_change(record):
    return {field: record[field] for field in FIELDS}


def normalized(payload):
    result = SYNC.normalize_webhook(payload)
    if result["status"] != "ok" or len(result["records"]) != 1:
        raise AssertionError(f"normalize_webhook returned {result}")
    return result["records"][0]


class RecordedFixtureTest(unittest.TestCase):
    def test_recorded_from_gitlab_18_and_scrubbed(self):
        """L2-1-GIS-003 夹具来自 GitLab 18.0，且已脱敏：无 PAT、邮箱、头像 URL、局域网地址。"""
        self.assertTrue(META["gitlab_version"].startswith("18.0"), META["gitlab_version"])
        for path in glob.glob(str(FIXTURES / "**" / "*.json"), recursive=True):
            text = Path(path).read_text(encoding="utf-8")
            with self.subTest(path=Path(path).name):
                self.assertIsNone(re.search(r"glpat-(?!\[REDACTED\])[A-Za-z0-9._-]{8,}", text))
                self.assertIsNone(re.search(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", text))
                self.assertIsNone(re.search(r"192\.168\.\d+\.\d+", text))
                self.assertNotRegex(text, r'"avatar_url": "http')

    def test_list_endpoints_return_lists_with_paging_headers(self):
        """L2-1-GIS-003 脚本翻页读的列表接口都返回 JSON 数组，并带 x-next-page 等翻页头。"""
        for name in ("members_all", "issues", "issue_notes", "merge_requests", "merge_request_notes",
                     "merge_request_closes_issues", "merge_request_diffs", "events", "pipelines",
                     "pipeline_jobs_failed_mr", "deployments", "feature_flags", "access_tokens"):
            with self.subTest(name):
                rec = recording(name)
                self.assertEqual(rec["status"], 200)
                self.assertIsInstance(rec["body"], list)
                self.assertIn("x-next-page", rec["headers"])


class RecordedIssueTest(unittest.TestCase):
    def issue(self, iid):
        return next(item for item in body("issues") if item["iid"] == iid)

    def test_issue_fact_from_real_issue(self):
        """L2-1-GIS-003 真实 Issue：label 拆出 type/status，assignee、milestone、描述摘要、标题 ＠ 中和、state 与 confidential 类型符合假设。"""
        issue = self.issue(OBJECTS["issue_iid"])
        fact = SYNC.issue_fact(issue, PID)
        self.assertEqual((fact["type"], fact["status"], fact["state"]), ("feature", "in-progress", "closed"))
        self.assertEqual(fact["labels"], ["recording"])
        self.assertEqual(fact["assignees"], [DEV])
        self.assertEqual(fact["milestone"], f"rec-{OBJECTS['stamp']} milestone")
        self.assertRegex(fact["description"], r"^[0-9a-f]{12}$")
        self.assertEqual(fact["title"], f"Recording issue {OBJECTS['stamp']} cc ＠{MAINTAINER}")
        self.assertEqual(fact["url"], issue["web_url"])
        self.assertEqual(SYNC.issue_selected(issue, {}), (True, None))
        self.assertFalse(SYNC.is_backfill(issue, SINCE, has_binding=False))
        self.assertTrue(SYNC.ISSUE_HEADER_RE.fullmatch(SYNC.render_message(fact, "routing").split("\n")[-1]))

    def test_task_work_item_is_listed_by_issues_api(self):
        """L2-1-GIS-003 Task 类型 work item 出现在 /issues（issue_type=task，URL 在 /-/work_items/），可按 Issue 建 Thread。"""
        task = self.issue(OBJECTS["task_iid"])
        self.assertEqual(task["issue_type"], "task")
        fact = SYNC.issue_fact(task, PID)
        self.assertEqual((fact["type"], fact["status"]), ("unknown", "unknown"))
        self.assertIn("/-/work_items/", fact["url"])

    def test_binding_note_survives_gitlab_verbatim(self):
        """L2-1-GIS-003 bot 写的 binding note 在 GitLab 原样保存（HTML 注释不被转义），parse_binding 取回 root。"""
        notes = body("issue_notes")
        note = next(n for n in notes if n["id"] == OBJECTS["binding_note_id"])
        marker = json.loads(re.match(r"<!-- gitlab-buzz-binding:v1 (\{.*\}) -->", note["body"]).group(1))
        link = SYNC.buzz_message_link(marker["channel_id"], marker["root_event_id"])
        self.assertEqual(note["body"], SYNC.render_binding_note(marker, link))
        self.assertEqual(note["author"]["id"], BOT_ID)
        self.assertEqual(SYNC.parse_binding(notes, BOT_ID, PID, "issue", OBJECTS["issue_iid"], marker["channel_id"]),
                         "ab" * 32)
        self.assertIsNone(SYNC.parse_binding(notes, BOT_ID, PID, "issue", OBJECTS["issue_iid"],
                                             "00000000-0000-4000-8000-000000000000"))

    def test_pending_comments_skip_system_and_bot_notes(self):
        """L2-1-GIS-003 真实 notes：system 为布尔、created_at 可解析；只剩人工评论，引用行与 HTML 注释被剥掉。"""
        notes = body("issue_notes")
        self.assertTrue(all(isinstance(n["system"], bool) for n in notes))
        pending = SYNC.pending_comments(notes, [], DESK, BOT_ID, SINCE)
        self.assertEqual([n["id"] for n in pending], [OBJECTS["issue_note_id"]])
        fact = SYNC.issue_fact(self.issue(OBJECTS["issue_iid"]), PID)
        rendered = SYNC.render_comment_message(fact, pending[0]).split("\n")
        self.assertIn(f"Recording comment for ＠{MAINTAINER}", rendered)
        self.assertIn(f"by: {DEV}", rendered)
        self.assertNotIn("comment: ", "\n".join(rendered))


class RecordedMergeRequestTest(unittest.TestCase):
    def test_mr_fact_opened_merged_and_draft(self):
        """L2-1-GIS-003 真实 MR（单个与列表）：state、draft、sha、分支、reviewers、author 字段符合 mr_fact 假设。"""
        opened = SYNC.mr_fact(body("merge_request_opened"), PID)
        self.assertEqual((opened["state"], opened["draft"]), ("opened", "no"))
        self.assertEqual(opened["sha"], OBJECTS["commit_2"])
        self.assertEqual(opened["branches"], f"{OBJECTS['branch']} -> main")
        self.assertEqual((opened["reviewers"], opened["author"], opened["labels"]), ([MAINTAINER], DEV, ["type::feature"]))
        self.assertEqual(opened["title"], f"Recording MR {OBJECTS['stamp']} cc ＠{MAINTAINER}")
        listed = {mr["iid"]: SYNC.mr_fact(mr, PID) for mr in body("merge_requests")}
        self.assertEqual(listed[OBJECTS["mr_iid"]]["state"], "merged")
        self.assertEqual(listed[OBJECTS["draft_mr_iid"]]["draft"], "yes")
        self.assertEqual(SYNC.mr_fact(body("merge_request_draft"), PID)["draft"], "yes")

    def test_mention_targets_from_members_all(self):
        """L2-1-GIS-003 真实 members/all：access_level 为整数；reviewer ∪ Maintainer+ 排除作者，Reporter 与项目 bot 不算。"""
        members = body("members_all")
        self.assertTrue(all(isinstance(m["access_level"], int) for m in members))
        mr = body("merge_request_opened")
        people = {MAINTAINER: "1" * 64, "root": "2" * 64, DEV: "3" * 64, "buzz-sync-bot": "4" * 64}
        self.assertEqual(SYNC.mr_mention_targets(mr, members, people), (["1" * 64, "2" * 64], []))
        self.assertEqual(SYNC.mr_mention_targets(mr, members, {}), ([], ["buzz-sync-maintainer", "root"]))

    def test_closes_issues_endpoint(self):
        """L2-1-GIS-003 closes_issues：返回 Issue 对象数组，带 iid 与 project_id（可跨项目）。"""
        issues = body("merge_request_closes_issues")
        self.assertEqual([item["iid"] for item in issues], [OBJECTS["issue_iid"]])
        self.assertTrue(all(isinstance(item.get("project_id"), int) for item in issues))

    def test_approvals_endpoint_and_approved_event(self):
        """L2-1-GIS-003 待实测 approvals：CE 18.0 返回 approved/approved_by[].user；脚本实际靠 events 的 approved 进 MR Thread。"""
        approvals = body("merge_request_approvals")
        self.assertIs(approvals["approved"], True)
        self.assertEqual([item["user"]["username"] for item in approvals["approved_by"]], [MAINTAINER])
        record = polled(event("MergeRequest", "approved"))
        self.assertEqual((record["object"], record["event"], record["placement"], record["mr_iid"], record["actor"]),
                         ("mr", "approved", "mr_thread", OBJECTS["mr_iid"], MAINTAINER))

    def test_diffs_endpoint_to_per_file_chunks(self):
        """L2-1-GIS-003 待实测 diffs：每项只有 hunk（无 diff --git 头），拼接后按文件切分，文件名与 new_path 一致。"""
        files = body("merge_request_diffs")
        self.assertTrue(all(not item["diff"].startswith("diff --git") for item in files))
        chunks, skipped = SYNC.split_diff_by_file(SYNC.unified_diff_from_files(files))
        self.assertEqual(skipped, [])
        self.assertEqual([c["file"] for c in chunks], [item["new_path"] for item in files])
        for chunk, item in zip(chunks, files):
            self.assertTrue(chunk["diff"].startswith(f"diff --git a/{item['old_path']} b/{item['new_path']}\n"))
            self.assertIn(item["diff"].rstrip("\n"), chunk["diff"])

    def test_new_file_diff_uses_dev_null_preimage(self):
        """L2-1-GIS-003 diffs 对新文件给 new_file=true 且 old_path==new_path：拼出的 patch 前像应是 --- /dev/null（当前为 --- a/<path>）。"""
        created = [item for item in body("merge_request_diffs") if item["new_file"]]
        self.assertTrue(created)
        self.assertTrue(all(item["old_path"] == item["new_path"] for item in created))
        for item in created:
            with self.subTest(item["new_path"]):
                chunk = SYNC.unified_diff_from_files([item])
                self.assertIn("\n--- /dev/null\n", chunk)
                self.assertIn(f"\n+++ b/{item['new_path']}\n", chunk)


class RecordedEventsTest(unittest.TestCase):
    def test_push_branch_and_tag_events(self):
        """L2-1-GIS-003 真实 push/tag 事件：分支 push 返回 None（政策）；tag 建删即时（13:16 恢复）。"""
        for event_ in EVENTS:
            push = event_.get("push_data")
            if not isinstance(push, dict):
                continue
            with self.subTest(action=event_["action_name"], ref_type=push.get("ref_type")):
                record = polled(event_)
                if push.get("ref_type") == "tag":
                    self.assertEqual((record["object"], record["placement"]), ("tag", "instant"))
                else:
                    self.assertIsNone(record)

    def test_releases(self):
        """L2-1-GIS-003 真实 releases：tag_name、name、created_at、_links.self 符合 record_from_release（13:16 恢复）；游标之后才发。"""
        (release,) = body("releases")
        record = SYNC.record_from_release(release, PID, SINCE)
        self.assertEqual((record["event"], record["placement"], record["ref"]), ("created", "instant", OBJECTS["tag"]))
        self.assertEqual(record["title"], f"Recording release {OBJECTS['stamp']} ＠{MAINTAINER}")
        self.assertEqual(record["url"], release["_links"]["self"])
        later = SYNC.format_timestamp(SYNC.parse_timestamp(release["created_at"]) + dt.timedelta(seconds=1))
        self.assertIsNone(SYNC.record_from_release(release, PID, later))

    def test_issue_mr_note_and_work_item_events(self):
        """L2-1-GIS-003 真实 Issue/MR/WorkItem/Note 事件：target_type 与 note.noteable_type 的取值符合分流假设。"""
        self.assertEqual(same_change(polled(event("Issue", "opened"))),
                         {"object": "issue", "event": "opened", "placement": "thread", "project": PID, "ref": "", "mr_iid": None})
        self.assertEqual(polled(event("WorkItem", "opened"))["object"], "issue")
        self.assertEqual(polled(event("MergeRequest", "accepted"))["placement"], "thread")
        for noteable in ("Issue", "MergeRequest"):
            with self.subTest(noteable):
                note = next(e for e in EVENTS if e.get("target_type") == "Note" and (e.get("note") or {}).get("noteable_type") == noteable)
                self.assertEqual((polled(note)["object"], polled(note)["placement"]), ("note", "thread"))
        commit_note = next(e for e in EVENTS if (e.get("note") or {}).get("noteable_type") == "Commit")
        self.assertIsNone(polled(commit_note))

    def test_wiki_and_milestone_events(self):
        """L2-1-GIS-003 真实 wiki/milestone 事件：wiki 不通知；milestone 进独立 Thread，GitLab 18 录制事件带 target_iid。"""
        for action in ("created", "updated"):
            self.assertIsNone(polled(event("WikiPage::Meta", action)), action)
        for action in ("opened", "closed"):
            record = polled(event("Milestone", action))
            self.assertEqual((record["object"], record["event"], record["placement"], record["source_id"]),
                             ("milestone", action, "milestone_thread", record["source_id"]))
            self.assertTrue(SYNC._positive_int(record["source_id"]))
            self.assertEqual(record["title"], f"rec-{OBJECTS['stamp']} milestone")

    def test_every_recorded_event_renders_and_round_trips(self):
        """L2-1-GIS-003 全部真实事件：created_at 可解析、能归一化或按政策返回 None；
        产出的记录 render_record header 可回解析，events: 行可回读去重。"""
        records = [polled(e) for e in SYNC.events_since(EVENTS, SINCE)]
        self.assertTrue(records)
        kept = [r for r in records if r is not None]
        self.assertTrue(kept)
        for record in kept:
            self.assertTrue(SYNC.parse_timestamp(record["created_at"]))
            if record["object"] in ("issue", "mr"):
                continue  # thread placements render via their own fact renderers, not render_record
            rendered = SYNC.render_record(record)
            header = SYNC.parse_header(rendered)
            self.assertEqual((header["object"], header["event"], header["project"]),
                             (record["object"], record["event"], PID))
            self.assertEqual(SYNC.posted_keys([{"pubkey": DESK, "content": rendered}], DESK), {record["key"]})


class RecordedTopLevelTest(unittest.TestCase):
    def pipeline(self, pipeline_id):
        return next(p for p in body("pipelines") if p["id"] == pipeline_id)

    def test_deployments_query_matches_recorded_contract(self):
        """L2-1-GIS-003 真实 GitLab：deployments 带 updated_after 时必须按 updated_at 排序（order_by=id 回 400）；客户端发出的查询与录制一致。"""
        recorded = recording("deployments")["request"]["query"]
        client = object.__new__(SYNC.GitLabClient)
        calls = []
        client.request = lambda method, path, params=None, body=None: calls.append(params) or ([], {})
        client.deployments(PID, recorded["updated_after"])
        sent = {key: value for key, value in calls[0].items() if key not in ("page", "per_page")}
        self.assertEqual(sent, recorded)

    def test_pipelines_and_failed_jobs(self):
        """L2-1-GIS-003 真实流水线：MR 流水线 ref 为 refs/merge-requests/<iid>/head 进 MR Thread 并附失败 job；默认分支失败为即时；其他分支不通知（2026-09-18 政策）。"""
        by_ref = {p["ref"]: p for p in body("pipelines")}
        mr_pipeline = by_ref[f"refs/merge-requests/{OBJECTS['mr_iid']}/head"]
        record = SYNC.record_from_pipeline(mr_pipeline, PID, PROJECT["default_branch"])
        self.assertEqual((record["placement"], record["mr_iid"], record["event"], record["source_id"]),
                         ("mr_thread", OBJECTS["mr_iid"], "failed", mr_pipeline["id"]))
        jobs = [job["name"] for job in body("pipeline_jobs_failed_mr")]
        self.assertEqual(jobs, ["test:unit"])
        rendered = SYNC.render_mr_activity(SYNC.mr_fact(body("merge_request_opened"), PID), record, jobs).split("\n")
        self.assertTrue(rendered[0].startswith("❌ **流水线失败**"))
        self.assertIn("jobs: test:unit", rendered)
        main = SYNC.record_from_pipeline(by_ref["main"], PID, PROJECT["default_branch"])
        self.assertEqual((main["placement"], main["event"]), ("instant", "failed"))
        self.assertIsNone(SYNC.record_from_pipeline(by_ref[OBJECTS["branch"]], PID, PROJECT["default_branch"]))
        self.assertTrue(all(SYNC.parse_timestamp(p["updated_at"]) for p in body("pipelines")))

    def test_external_commit_status_is_not_a_job(self):
        """L2-1-GIS-003 外部 CI 用 statuses 接口上报的失败不出现在 jobs?scope[]=failed：默认分支失败消息拿不到 job 名。"""
        self.assertEqual([(s["name"], s["status"]) for s in body("commit_statuses_main")], [("test:unit", "failed")])
        self.assertEqual(body("pipeline_jobs_failed_main"), [])
        self.assertEqual(self.pipeline(body("commit_statuses_main")[0]["pipeline_id"])["source"], "external")

    def test_deployments(self):
        """L2-1-GIS-003 真实 deployments：status、updated_at、environment.name、ref、sha 字段符合 record_from_deployment；
        成功不通知（2026-09-18 政策），失败字段仍取自同一 shape。"""
        (deployment,) = body("deployments")
        self.assertIsNone(SYNC.record_from_deployment(deployment, PID, WEB))
        failed = SYNC.record_from_deployment({**deployment, "status": "failed"}, PID, WEB)
        self.assertEqual((failed["object"], failed["event"], failed["placement"], failed["title"], failed["ref"]),
                         ("deployment", "failed", "instant", "rec-staging", "main"))
        self.assertEqual(failed["sha"], deployment["sha"])

    def test_access_tokens_endpoint(self):
        """L2-1-GIS-003 待实测 access_tokens：active/revoked/expires_at（日期）/id 符合假设；7 天内到期才发；Reporter bot 得 401。"""
        (token,) = body("access_tokens")
        self.assertEqual((token["active"], token["revoked"]), (True, False))
        record = SYNC.record_from_access_token(token, PID, WEB, "2026-09-13")
        self.assertEqual((record["event"], record["placement"], record["title"]),
                         ("expiring", "instant", f"{OBJECTS['access_token_name']} ({token['expires_at']})"))
        self.assertIsNone(SYNC.record_from_access_token(token, PID, WEB, "2026-09-01"))
        self.assertIn(recording("access_tokens_as_bot")["status"], (401, 403, 404))

    def test_project_visibility_gates_diff(self):
        """L2-1-GIS-003 真实 project：visibility、default_branch、http_url_to_repo 字段存在；private 默认不发 Diff。"""
        self.assertEqual((PROJECT["id"], PROJECT["visibility"], PROJECT["default_branch"]), (PID, "private", "main"))
        self.assertFalse(SYNC.diff_allowed(PROJECT["visibility"], {"enabled": True}))
        self.assertTrue(SYNC.diff_allowed(PROJECT["visibility"], {"enabled": True, "private": True}))
        args = SYNC.build_diff_args("/x/buzz", "00000000-0000-4000-8000-0000000000c1", repo=PROJECT["http_url_to_repo"],
                                    commit=OBJECTS["commit_2"], file_path="README.md", reply_to="f" * 64,
                                    source_branch=OBJECTS["branch"], target_branch="main", pr=OBJECTS["mr_iid"])
        self.assertIn(PROJECT["http_url_to_repo"], args)


class RecordedWebhookTest(unittest.TestCase):
    """Real GitLab 18.0 project-webhook payloads for the same changes as the polling recordings."""

    def test_webhook_and_polling_agree_on_the_same_changes(self):
        """L2-1-GIS-003 真实 webhook 与同一变化的轮询记录在 object/event/placement/project/ref/mr_iid 上一致
        （2026-09-18 政策下两边同步收窄：cut 类型两边都是 unsupported/None）。"""
        today = "2026-09-13"
        pipeline = next(p for p in body("pipelines") if p["ref"] == f"refs/merge-requests/{OBJECTS['mr_iid']}/head")
        pairs = {
            "tag created": (one_webhook("tag_push"), polled(push_event("pushed new", "tag", OBJECTS["tag"]))),
            "issue opened": (one_webhook("issue", "open"), polled(event("Issue", "opened"))),
            "issue closed": (one_webhook("issue", "close"), polled(event("Issue", "closed"))),
            "task opened": (one_webhook("work_item", "open"), polled(event("WorkItem", "opened"))),
            "mr approved": (one_webhook("merge_request", "approved"), polled(event("MergeRequest", "approved"))),
            "mr merged": (one_webhook("merge_request", "merge"), polled(event("MergeRequest", "accepted"))),
            "issue comment": (next(p for p in webhook("note") if p["object_attributes"]["id"] == OBJECTS["issue_note_id"]),
                              polled(next(e for e in EVENTS if e.get("target_id") == OBJECTS["issue_note_id"]))),
            "mr pipeline failed": (one_webhook("pipeline", "failed"),
                                   SYNC.record_from_pipeline(pipeline, PID, PROJECT["default_branch"])),
            "release created": (one_webhook("release", "create"),
                                SYNC.record_from_release(body("releases")[0], PID, SINCE)),
            "access token expiring": (one_webhook("access_token"),
                                      SYNC.record_from_access_token(body("access_tokens")[0], PID, WEB, today)),
        }
        for name, (payload, polling) in pairs.items():
            with self.subTest(name):
                self.assertEqual(same_change(normalized(payload)), same_change(polling))
        for name, payload in (
            ("branch created", next(p for p in webhook("push") if p["ref"] == f"refs/heads/{OBJECTS['branch']}"
                                    and p["before"] == "0" * 40)),
            ("commit comment", next(p for p in webhook("note") if p["object_attributes"]["noteable_type"] == "Commit")),
            ("wiki created", one_webhook("wiki_page", "create")),
            ("feature flag", one_webhook("feature_flag")),
            ("deployment success", one_webhook("deployment", "success")),
        ):
            with self.subTest(cut=name):
                expected = {"status": "ok", "records": []} if name in ("commit comment", "deployment success") \
                    else {"status": "unsupported", "records": []}
                self.assertEqual(SYNC.normalize_webhook(payload), expected)

    def test_shape_mr_pipeline_ref_is_the_source_branch(self):
        """L2-1-GIS-003 webhook 形状 1：MR 流水线 payload 的 object_attributes.ref 是源分支，merge_request.iid 指明 MR；归一化后 ref 为 MR head ref。"""
        payload = one_webhook("pipeline", "failed")
        self.assertEqual(payload["object_attributes"]["source"], "merge_request_event")
        self.assertEqual(payload["object_attributes"]["ref"], OBJECTS["branch"])
        self.assertEqual(payload["merge_request"]["iid"], OBJECTS["mr_iid"])
        self.assertEqual(normalized(payload)["ref"], f"refs/merge-requests/{OBJECTS['mr_iid']}/head")

    def test_shape_milestone_hook_is_not_emitted_by_gitlab_18_0(self):
        """L2-1-GIS-003 webhook 形状 2：GitLab 18.0 CE 没有 milestone webhook（milestone_events 被忽略、无 payload），action 位置无法实测。"""
        self.assertIn("milestone_events", META["webhooks"]["hook_flags_requested_but_ignored"])
        self.assertNotIn("milestone", META["webhooks"]["kinds_received"])
        self.assertEqual(webhook("milestone"), [])

    def test_shape_note_action_is_in_object_attributes(self):
        """L2-1-GIS-003 webhook 形状 3：note payload 的 action 在 object_attributes.action（create），顶层没有 action。"""
        notes = webhook("note")
        self.assertGreaterEqual(len(notes), 3)
        for payload in notes:
            self.assertEqual(payload["object_attributes"]["action"], "create")
            self.assertNotIn("action", payload)

    def test_shape_ce_approval_action_is_approved(self):
        """L2-1-GIS-003 webhook 形状 4：CE 批准发 action=approved（user 为批准人），没有 approval；归一化进 MR Thread。"""
        payload = one_webhook("merge_request", "approved")
        self.assertEqual(payload["user"]["username"], MAINTAINER)
        self.assertEqual(webhook("merge_request", "approval"), [])
        record = normalized(payload)
        self.assertEqual((record["event"], record["placement"], record["mr_iid"]), ("approved", "mr_thread", OBJECTS["mr_iid"]))

    def test_shape_access_token_event_name_and_interval(self):
        """L2-1-GIS-003 webhook 形状 5：access_token payload 的 event_name 为 expiring_access_token，另带顶层 interval（seven_days）。"""
        payload = one_webhook("access_token")
        self.assertEqual(payload["event_name"], "expiring_access_token")
        self.assertEqual(payload["interval"], "seven_days")
        self.assertEqual(set(payload["object_attributes"]), {"id", "name", "user_id", "created_at", "expires_at"})

    def test_access_token_thirty_and_sixty_day_warnings_are_not_seven_day_records(self):
        """L2-1-GIS-003 GitLab 18.0 在到期前 60/30/7 天各发一次 expiring_access_token（interval 不同）：只有 seven_days 应产出即时记录，与轮询的 7 天窗口一致。"""
        base = one_webhook("access_token")
        seven = normalized(base)
        for interval in ("thirty_days", "sixty_days"):
            with self.subTest(interval):
                early = copy.deepcopy(base)
                early["interval"] = interval
                result = SYNC.normalize_webhook(early)
                self.assertEqual(result["records"], [], f"{interval} warning became {result['records']} (7-day key {seven['key']})")

    def test_shape_feature_flag_payload_has_no_timestamps(self):
        """L2-1-GIS-003 webhook 形状 6：feature_flag payload 只有 id/name/description/active，无时间戳；新建开关不发 webhook，只有切换发。"""
        (payload,) = webhook("feature_flag")
        self.assertEqual(set(payload["object_attributes"]), {"id", "name", "description", "active"})
        self.assertIs(payload["object_attributes"]["active"], False)
        self.assertIn("updated_at", body("feature_flags")[0])

    def test_shape_work_item_object_kind(self):
        """L2-1-GIS-003 webhook 形状 7：Task 类型 work item 发 object_kind=work_item（type Task），issue 类型仍是 issue；归一化都按 Issue。"""
        task = one_webhook("work_item", "open")
        self.assertEqual((task["event_type"], task["object_attributes"]["type"]), ("work_item", "Task"))
        issue = one_webhook("issue", "open")
        self.assertEqual(issue["object_attributes"]["type"], "Issue")
        self.assertEqual(normalized(task)["object"], "issue")

    def test_job_payloads_are_attached(self):
        """L2-1-GIS-003 真实 build（job）payload 归一化为 attached，不单独成记录。"""
        builds = webhook("build")
        self.assertTrue(builds)
        for payload in builds:
            self.assertEqual(SYNC.normalize_webhook(payload), {"status": "attached", "records": []})


if __name__ == "__main__":
    unittest.main()
