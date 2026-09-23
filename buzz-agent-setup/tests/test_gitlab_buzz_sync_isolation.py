import datetime as dt
import hashlib
import http.client
import importlib.util
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gitlab_buzz_sync"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_isolation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
ALICE, BOB = "a1" * 32, "b2" * 32
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
OTHER_CHANNEL = "00000000-0000-4000-8000-0000000000c2"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
def OBJ_URL(tail, iid):
    return f"{WEB}/-/{tail}/{iid}"


SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"
TODAY = "2026-09-13"


def unix(value):
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def make_issue(iid=182, **overrides):
    base = {"iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "", "state": "opened",
            "labels": ["type::feature", "status::ready"], "milestone": None, "confidential": False,
            "assignees": [], "web_url": f"{WEB}/-/issues/{iid}", "created_at": "2026-09-13T01:00:00Z",
            "updated_at": "2026-09-13T01:00:00Z"}
    base.update(overrides)
    return base


def make_mr(iid=31, **overrides):
    base = {"iid": iid, "project_id": PID, "title": f"MR {iid}", "state": "opened", "draft": False,
            "sha": "a" * 40, "source_branch": "hotfix-login", "target_branch": "main", "labels": [],
            "reviewers": [], "author": {"username": "dave"}, "web_url": f"{WEB}/-/merge_requests/{iid}",
            "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z"}
    base.update(overrides)
    return base


def binding_note(note_id, object_kind, iid, root):
    binding = {"project_id": PID, "object": object_kind, "iid": iid, "channel_id": CHANNEL, "root_event_id": root}
    return {"id": note_id, "author": {"id": BOT_ID, "username": BOT}, "system": False,
            "body": SYNC.render_binding_note(binding, SYNC.buzz_message_link(CHANNEL, root)),
            "created_at": "2026-09-13T01:30:00Z"}


def push_event(event_id=1, created="2026-09-13T02:00:00.000Z"):
    return {"id": event_id, "action_name": "pushed to", "target_type": None, "created_at": created,
            "author": {"username": "alice"},
            "push_data": {"ref_type": "branch", "ref": "main", "commit_count": 1, "commit_title": "x"}}


def mr_pipeline(pipeline_id=90, iid=31, status="success"):
    return {"id": pipeline_id, "status": status, "ref": f"refs/merge-requests/{iid}/head",
            "updated_at": "2026-09-13T04:00:00Z", "web_url": f"{WEB}/-/pipelines/{pipeline_id}"}


class FakeGitLab:
    def __init__(self):
        self.projects = {PID: {"id": PID, "visibility": "public", "web_url": WEB, "default_branch": "main",
                               "path_with_namespace": "buzz-sync-test/pilot"}}
        self.issue_list, self.mr_list = {PID: []}, {PID: []}
        self.issue_notes, self.mr_note_list = {}, {}
        self.closes, self.members_list, self.fresh_mr = {}, [], {}
        self.event_list, self.pipeline_list, self.token_list = [], [], []
        self.missing_notes, self.forbidden_notes = set(), set()
        self.writes = []

    def current_user(self):
        return {"id": BOT_ID, "username": BOT}

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        if project_id not in self.projects:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}", 404)
        return self.projects[project_id]

    def issues(self, project_id, updated_after):
        return [dict(item) for item in self.issue_list.get(project_id, [])]

    def issue(self, project_id, iid):
        return dict(next(item for item in self.issue_list.get(project_id, []) if item["iid"] == iid))

    def notes(self, project_id, iid):
        if iid in self.missing_notes:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/issues/{iid}/notes", 404)
        if iid in self.forbidden_notes:
            raise SYNC.GitLabHTTPError("GET", f"projects/{project_id}/issues/{iid}/notes", 403)
        return list(self.issue_notes.get(iid, []))

    def add_note(self, project_id, iid, body):
        self.writes.append(("issue_note", iid))
        note = {"id": 3000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.issue_notes.setdefault(iid, []).append(note)
        return note["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(item) for item in self.mr_list.get(project_id, [])]

    def merge_request(self, project_id, iid):
        if iid in self.fresh_mr:
            return dict(self.fresh_mr[iid])
        return dict(next(mr for mr in self.mr_list[project_id] if mr["iid"] == iid))

    def members(self, project_id):
        return list(self.members_list)

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def add_mr_note(self, project_id, iid, body):
        self.writes.append(("mr_note", iid))
        note = {"id": 4000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        return note["id"]

    def related_merge_requests(self, project_id, issue_iid):
        return []

    def mr_closes_issues(self, project_id, iid):
        return list(self.closes.get(iid, []))

    def mr_diffs(self, project_id, iid):
        return []

    def pipeline_jobs(self, project_id, pipeline_id):
        return []

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return list(self.pipeline_list)

    def deployments(self, project_id, updated_after):
        return []

    def releases(self, project_id):
        return []

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return list(self.token_list)


class FakeBuzz:
    def __init__(self):
        self.events, self.writes = [], []
        self.channel_calls, self.search_calls = [], []
        self.channel_error = None
        self.members = None

    def add_event(self, content, reply_to=None, pubkey=DESK, mentions=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{pubkey}:{content}".encode()).hexdigest()
        tags = [["h", CHANNEL]] + ([["e", reply_to, "", "reply"]] if reply_to else [])
        tags.extend(["p", pubkey_] for pubkey_ in mentions)
        self.events.append({"id": event_id, "pubkey": pubkey, "kind": 9, "created_at": unix(SERVER_TIME) + len(self.events),
                            "tags": tags, "content": content})
        return event_id

    def send(self, content, reply_to=None, mentions=()):
        self.writes.append((reply_to, content, tuple(mentions)))
        return self.add_event(content, reply_to, mentions=mentions)

    def thread(self, root):
        return [e for e in self.events if e["id"] == root or ["e", root, "", "reply"] in e["tags"]]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        self.search_calls.append((query, object_kind, iid, since_unix))
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_messages(self, since_unix):
        self.channel_calls.append(since_unix)
        if self.channel_error:
            raise SYNC.SyncError(self.channel_error)
        return [e for e in self.events if not any(tag[0] == "e" for tag in e["tags"])]

    def channel_members(self):
        return dict(self.members) if self.members is not None else {ALICE: "member", BOB: "member"}


def config(**overrides):
    base = {"channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": {}}
    base.update(overrides)
    return base


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab, self.buzz = FakeGitLab(), FakeBuzz()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, cfg=None):
        return SYNC.Syncer(cfg or config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def top_level(self, prefix):
        return [content for reply_to, content, _ in self.buzz.writes
                if reply_to is None and SYNC.matches_trigger_prefix(content, prefix)]


class IsolationTest(SyncCase):
    def test_poison_object_stalls_only_itself(self):
        """L1-GIS-041 (ADR-0009) binding 冲突、MR 状态异常只 stall 各自对象；其余 issue 照常写，cursor 推进。"""
        self.gitlab.issue_list[PID] = [make_issue(183), make_issue(184)]
        self.gitlab.issue_notes[183] = [binding_note(1, "issue", 183, "1" * 64), binding_note(2, "issue", 183, "2" * 64)]
        self.gitlab.mr_list[PID] = [make_mr(31, state="weird")]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 183), ("mr", 31)])
        self.assertIn("conflicting", result["stalled"][0]["reason"])
        self.assertTrue(any("/issues/184" in content for reply_to, content, _ in self.buzz.writes if reply_to is None))
        self.assertEqual(len(self.top_level("[gitlab-notify:v1][object:sync][event:stalled]")), 2)
        cache = json.loads(next(Path(self.tmp.name).glob("*.cache.json")).read_text(encoding="utf-8"))
        self.assertEqual([(s["object"], s["iid"]) for s in cache["stalled"]], [("issue", 183), ("mr", 31)])

    def test_transport_errors_still_fail_closed(self):
        """L1-GIS-041 GitLab 权限错误（403）这类读失败仍整轮失败关闭，不写缓存。"""
        self.gitlab.issue_list[PID] = [make_issue(183)]
        self.gitlab.forbidden_notes = {183}
        with self.assertRaises(SYNC.GitLabHTTPError):
            self.run_sync()
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])


class ThreadCapTest(SyncCase):
    def test_thread_with_500_replies_stalls_that_issue_without_replying(self):
        """L1-GIS-042 (ADR-0009) Thread 历史达到 500 条上限时该 issue stall：不回帖，进 stalled，其余对象不受影响。"""
        self.gitlab.issue_list[PID] = [make_issue(183)]
        self.run_sync()
        root = self.buzz.events[0]["id"]
        for index in range(500):
            self.buzz.add_event(f"reply {index}", reply_to=root, pubkey=BOB)
        self.gitlab.issue_list[PID] = [make_issue(183, title="changed")]
        writes = len(self.buzz.writes)
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 183)])
        self.assertIn("500", result["stalled"][0]["reason"])
        self.assertFalse(any(reply_to == root for reply_to, _, _ in self.buzz.writes[writes:]))


class ChannelScanTest(SyncCase):
    def test_channel_messages_pages_by_time(self):
        """L1-GIS-043 顶层去重按时间分页读频道消息（messages get --kinds 9，--before 翻页），没有结果上限，只留本频道。"""
        buzz = object.__new__(SYNC.BuzzCli)
        buzz.channel, buzz.publisher = CHANNEL, DESK
        calls = []

        def event(index, created, channel=CHANNEL):
            return {"id": f"{index:064x}", "pubkey": DESK, "kind": 9, "created_at": created,
                    "tags": [["h", channel]], "content": "x"}

        page1 = [event(i, 2000 + i) for i in range(200)]
        page2 = [event(0, 2000)] + [event(1000 + i, 1950 + i) for i in range(50)] + [event(5000, 1990, OTHER_CHANNEL)]

        def command(args, content=None):
            calls.append(args)
            return page2 if "--before" in args else page1

        buzz.command = command
        self.assertEqual(len(buzz.channel_messages(1000)), 250)
        self.assertEqual(calls[0][:6], ["messages", "get", "--channel", CHANNEL, "--kinds", "9"])
        self.assertEqual(calls[0][calls[0].index("--since") + 1], "1000")
        self.assertEqual(calls[0][calls[0].index("--limit") + 1], "200")
        self.assertNotIn("--before", calls[0])
        self.assertEqual(calls[1][calls[1].index("--before") + 1], "2000")
        self.assertEqual(len(calls), 2)

        buzz.command = lambda args, content=None: [event(i, 3000) for i in range(200)]
        with self.assertRaises(SYNC.SyncError):
            buzz.channel_messages(1000)

    def test_dedupe_windows(self):
        """L1-GIS-043 普通顶层记录从游标前 10 分钟读起；token 到期这类按天去重的从扫描当天 UTC 零点前 15 分钟读起（容忍时钟偏差）；同一窗口一轮只读一次。"""
        self.gitlab.pipeline_list = [{"id": 5, "status": "failed", "ref": "main",
                                      "updated_at": "2026-09-13T02:00:00Z"}]
        self.run_sync()
        self.assertEqual(self.buzz.channel_calls, [unix(SINCE) - 600])

        self.buzz.channel_calls.clear()
        self.gitlab.pipeline_list = []
        self.gitlab.token_list = [{"id": 11, "name": "deploy", "expires_at": "2026-09-18", "active": True,
                                   "revoked": False}]
        SYNC.Syncer(
            config(since="2026-09-13T00:30:00Z"), self.gitlab, self.buzz,
            state_dir=Path(self.tmp.name) / "daily-window",
        ).run()
        self.assertEqual(self.buzz.channel_calls, [unix("2026-09-13T00:00:00Z") - 900])

    def test_scan_failure_without_cache_explains_since(self):
        """L1-GIS-043 首轮（没有游标缓存）读频道消息失败时，错误提示把 since 调近。"""
        self.gitlab.pipeline_list = [{"id": 5, "status": "failed", "ref": "main",
                                      "updated_at": "2026-09-13T02:00:00Z"}]
        self.buzz.channel_error = "Buzz channel scan exceeded its page limit"
        with self.assertRaises(SYNC.SyncError) as caught:
            self.run_sync()
        self.assertIn("since", str(caught.exception))


class MrSnapshotTest(SyncCase):
    def test_activity_reply_is_not_a_snapshot(self):
        """L1-GIS-044 MR activity 回帖不算快照：activity 里的 draft:no 不会吞掉 draft→ready 的 lifecycle。"""
        root = SYNC.render_mr_message(SYNC.mr_fact(make_mr(31, draft=True), PID), "lifecycle")
        activity = SYNC.render_mr_message(SYNC.mr_fact(make_mr(31, draft=False), PID), "activity")
        events = [{"id": "1" * 64, "pubkey": DESK, "created_at": 1, "content": root},
                  {"id": "2" * 64, "pubkey": DESK, "created_at": 2, "content": activity}]
        self.assertEqual(SYNC.previous_mr_fact_from_thread(events, DESK, PID, 31)["draft"], "yes")

    def test_activity_renders_from_run_snapshot(self):
        """L1-GIS-044 本轮已列出的 MR，流水线回帖用本轮快照渲染，不用之后重新读到的 MR。"""
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync()
        self.gitlab.fresh_mr[31] = make_mr(31, draft=True, title="Changed later")
        self.gitlab.pipeline_list = [mr_pipeline()]
        self.run_sync()
        activity = [content for reply_to, content, _ in self.buzz.writes
                    if reply_to and "流水线通过" in content]
        self.assertEqual(len(activity), 1)
        self.assertIn("[draft:no]", activity[0].split("\n")[-1])
        self.assertTrue(activity[0].split("\n")[0].startswith("✅ **流水线通过** · [!31 MR 31]("))


class NeutralizeTest(unittest.TestCase):
    def test_nostr_uris_and_raw_ref_urls(self):
        """L1-GIS-045 GitLab 文本里的 nostr: 与 @ 转成全角，CLI 不会据此生成 p tag；tag/分支链接用原始 ref 做 URL 编码。"""
        self.assertEqual(SYNC.neutralize("ping nostr:npub1abc NOSTR:npub1x @bob"),
                         "ping nostr：npub1abc NOSTR：npub1x ＠bob")
        record = SYNC.record_from_event(
            {"id": 9, "action_name": "pushed new", "target_type": None, "created_at": "2026-09-13T02:00:00.000Z",
             "author": {"username": "alice"},
             "push_data": {"ref_type": "tag", "ref": "v1@rc", "commit_count": 0, "commit_title": ""}}, PID, WEB)
        self.assertEqual(record["url"], f"{WEB}/-/tags/v1%40rc")
        self.assertEqual(record["ref"], "v1＠rc")
        self.assertEqual(SYNC.branch_plaque_url(WEB, "feature/v1@rc"), f"{WEB}/-/tree/feature/v1%40rc")


class MembershipTest(SyncCase):
    def test_mentions_only_channel_members(self):
        """L1-GIS-046 只 @ 当前频道成员；映射了但不在频道的人列进 unmapped，CLI 不会因非成员拒发。"""
        self.gitlab.members_list = [{"username": "alice", "access_level": 40}, {"username": "bob", "access_level": 50}]
        self.buzz.members = {ALICE: "member"}
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync(config(people={"alice": ALICE, "bob": BOB}))
        _, content, mentions = self.buzz.writes[1]  # first fact; the plaque carries no mentions
        self.assertEqual(mentions, (ALICE,))
        self.assertIn("unmapped: bob(not_channel_member)", content.split("\n"))


class IssueLinkTest(SyncCase):
    def test_cross_project_and_missing_issues_are_not_linked(self):
        """L1-GIS-047 closes_issues 里别的项目的 Issue 不按本项目 iid 查；分支名不参与找 Issue。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.gitlab.mr_list[PID] = [make_mr(31), make_mr(32, source_branch="feature/555-x")]
        self.gitlab.closes = {31: [{"iid": 182, "project_id": 999}]}
        self.gitlab.missing_notes = {555}
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        for iid in (31, 32):
            root = next(content for _, content, _ in self.buzz.writes if f"[mr:{iid}]" in content)
            self.assertFalse(any(line.startswith("issues:") for line in root.split("\n")))


class ExcludedMrTest(SyncCase):
    def test_excluded_mr_gets_no_activity(self):
        """L1-GIS-048 已绑定的 MR 后来命中 exclude，流水线与批准活动都不再回帖。"""
        self.gitlab.mr_list[PID] = [make_mr(31)]
        self.run_sync()
        self.gitlab.mr_list[PID] = [make_mr(31, labels=["no-sync"], updated_at="2026-09-13T03:00:00Z")]
        self.gitlab.pipeline_list = [mr_pipeline()]
        self.gitlab.event_list = [{"id": 15, "action_name": "approved", "target_type": "MergeRequest", "target_iid": 31,
                                   "target_title": "MR 31", "created_at": "2026-09-13T04:00:00.000Z",
                                   "author": {"username": "alice"}}]
        writes = len(self.buzz.writes)
        summary = self.run_sync(config(exclude=[{"label": "no-sync"}]))
        self.assertEqual(self.buzz.writes[writes:], [])
        self.assertGreaterEqual(summary["skipped"]["excluded"], 1)


class DigestHeaderTest(unittest.TestCase):
    def test_single_record_digest_uses_activity_header(self):
        """L1-GIS-049 摘要只有一条时也用 [object:activity][event:digest] header，与即时消息区分（合成 digest 记录，
        live 记录已不进 digest——2026-09-18 政策）。"""
        record = SYNC._record("event-1", "push", "pushed", "digest", PID, ref="feature/x",
                              url=f"{WEB}/-/commits/feature/x")
        lines = SYNC.render_digest([record], PID).split("\n")
        self.assertEqual(
            lines[-1],
            f"[gitlab-notify:v1][object:activity][event:digest][project:{PID}][events:event-1]",
        )
        self.assertNotIn("events: event-1", lines)


class CacheSafetyTest(SyncCase):
    def test_cache_bound_to_projects_and_not_ahead_of_server(self):
        """L1-GIS-050 缓存绑定项目集合，增删项目后不沿用游标；游标晚于 GitLab 服务器时间时忽略。"""
        self.run_sync()
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        self.assertIsNotNone(syncer.read_cursor())
        cfg = config()
        cfg["gitlab"]["projects"] = [PID, 482]
        self.assertIsNone(SYNC.Syncer(cfg, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).read_cursor())
        cache = syncer.cache_path()
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["cursor"] = "2099-01-01T00:00:00Z"
        cache.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(syncer.read_cursor(SYNC.parse_timestamp(SERVER_TIME)))

    def test_state_dir_mode_and_lock_symlink(self):
        """L1-GIS-050 新建状态目录为 0700；锁文件是符号链接时拒绝运行。"""
        state = Path(self.tmp.name) / "state"
        SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=state).run()
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        lock = SYNC.lock_path(config(), state)
        lock.unlink()
        target = Path(self.tmp.name) / "elsewhere"
        target.write_text("", encoding="utf-8")
        lock.symlink_to(target)
        with self.assertRaises(SYNC.SyncError):
            SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=state).run()

    def test_existing_state_dir_must_be_owner_only_and_not_a_symlink(self):
        """L1-GIS-127 Existing state paths fail closed before any external read."""
        exposed = Path(self.tmp.name) / "exposed-state"
        exposed.mkdir(mode=0o755)
        with self.assertRaisesRegex(SYNC.SyncError, "owner-only real directory"):
            SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=exposed).run()

        real = Path(self.tmp.name) / "real-state"
        real.mkdir(mode=0o700)
        linked = Path(self.tmp.name) / "linked-state"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(SYNC.SyncError, "owner-only real directory"):
            SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=linked).run()


class ReadPathTest(SyncCase):
    def test_diff_files_partition(self):
        """L1-GIS-051 过大、折叠、空 diff（改名/二进制）与非法路径的文件跳过并计数，不发空 diff。"""
        body = "@@ -1 +1 @@\n-x\n+y\n"
        usable, skipped = SYNC.partition_diff_files([
            {"old_path": "a.py", "new_path": "a.py", "diff": body},
            {"old_path": "big.bin", "new_path": "big.bin", "diff": "", "too_large": True},
            {"old_path": "c.py", "new_path": "c.py", "diff": body, "collapsed": True},
            {"old_path": "old.py", "new_path": "new.py", "diff": ""},
            {"old_path": "x\ny", "new_path": "x\ny", "diff": body},
        ])
        self.assertEqual([item["new_path"] for item in usable], ["a.py"])
        self.assertEqual(skipped, ["big.bin", "c.py", "new.py", "<invalid path>"])

    def test_pipelines_ordered_by_id_deployments_by_updated_at(self):
        """L1-GIS-051 pipelines 按 id 升序翻页，扫描期间有更新也不漏项；deployments 的 updated_after 只允许配 updated_at 排序（GitLab 否则 400）。"""
        client = object.__new__(SYNC.GitLabClient)
        calls = []
        client.request = lambda method, path, params=None, body=None: calls.append(params) or ([], {})
        client.pipelines(PID, SINCE)
        client.deployments(PID, SINCE)
        self.assertEqual([(p["order_by"], p["sort"], p["updated_after"]) for p in calls],
                         [("id", "asc", SINCE), ("updated_at", "asc", SINCE)])

    def test_root_recovery_search_window(self):
        """L1-GIS-051 root 恢复搜索只查对象创建前 15 分钟之后的 Bridge 消息；命中上限首错停轮。"""
        self.gitlab.issue_list[PID] = [make_issue(183)]
        self.run_sync()
        since = unix("2026-09-13T01:00:00Z") - 900
        # No canonical hit: an issue is searched once more in the work_items form an older run may have
        # published (engineering/skills#101), inside the same window.
        self.assertEqual(self.buzz.search_calls, [
            (f"{WEB}/-/issues/183", "issue", 183, since),
            (f"{WEB}/-/work_items/183", "issue", 183, since),
        ])
        buzz = object.__new__(SYNC.BuzzCli)
        buzz.publisher, buzz.channel = DESK, CHANNEL
        calls = []
        buzz.command = lambda args, content=None: calls.append(args) or []
        buzz.search_roots(OBJ_URL("issues", 183), "issue", 183, 5000)
        self.assertEqual(calls[0][calls[0].index("--since") + 1], "5000")
        self.assertEqual(calls[0][calls[0].index("--query") + 1], "/-/issues/183")  # relay path tail (#106)
        buzz.command = lambda args, content=None: [
            {"id": f"{i:064x}", "pubkey": DESK, "content": "x", "tags": []} for i in range(SYNC.SEARCH_LIMIT)]
        with self.assertRaises(SYNC.ObjectError):
            buzz.search_roots(OBJ_URL("issues", 183), "issue", 183, 5000)

    def test_pipeline_webhook_carries_source_id(self):
        """L1-GIS-051 流水线 webhook 记录带 source_id，与轮询一致，失败时能查失败的 job。"""
        body = json.loads((FIXTURES / "webhooks" / "31-pipeline-failed.json").read_text(encoding="utf-8"))["body"]
        record = SYNC.normalize_webhook(body)["records"][0]
        self.assertEqual(record["source_id"], body["object_attributes"]["id"])


class RaisingOpener:
    def __init__(self, exc):
        self.exc = exc

    def open(self, request, timeout):
        raise self.exc


class EnvelopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        release = Path(self.tmp.name) / "buzz-0.5.23" / "usr" / "bin"
        release.mkdir(parents=True)
        self.cli = release / "buzz"
        self.cli.write_bytes(b"\x7fELFtest fixture")
        self.cli.chmod(0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, token_env="NH_DESK_GITLAB_TOKEN"):
        cfg = config()
        cfg["gitlab"]["token_env"] = token_env
        cfg["buzz"] = {"cli_path": str(self.cli), "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest()}
        return cfg

    def run_main(self, factory):
        token = "glpat-secret-value-123"
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps(self.cfg("NH_DESK_GITLAB_PAT")), encoding="utf-8")
        path.chmod(0o600)
        env = {"NH_DESK_GITLAB_PAT": token, "BUZZ_PRIVATE_KEY": "k" * 64, "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
               "HOME": self.tmp.name}
        out = io.StringIO()
        with redirect_stdout(out):
            code = SYNC.main(["--config", str(path), "--state-dir", str(Path(self.tmp.name) / "state")],
                             env=env, adapter_factory=lambda config, env, dry_run: factory(token))
        return code, out.getvalue(), token

    def test_unexpected_exception_prints_redacted_json(self):
        """L1-GIS-052 非 SyncError 异常也输出 JSON 错误信封并退出 1；gitlab.token_env 的值无论变量名如何都遮蔽。"""
        def boom(token):
            raise RuntimeError(f"connection reset while using {token}")

        code, output, token = self.run_main(boom)
        result = json.loads(output)
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertIn("RuntimeError", result["error"])
        self.assertNotIn(token, output)

        def sync_error(token):
            raise SYNC.SyncError(f"bad credentials {token}")

        code, output, token = self.run_main(sync_error)
        self.assertEqual(code, 1)
        self.assertNotIn(token, output)

    def test_strict_config_keys(self):
        """L1-GIS-052 配置里未知的键、exclude 不是列表都拒绝，拼错的键不会被静默忽略。"""
        SYNC.validate_config(self.cfg())
        mutations = {
            "top": lambda c: c.update(exlude=[]),
            "gitlab": lambda c: c["gitlab"].update(token="x"),
            "buzz": lambda c: c["buzz"].update(wrapper="x"),
            "diff": lambda c: c["diff"].update(public=True),
            "exclude_null": lambda c: c.update(exclude=None),
        }
        for name, mutate in mutations.items():
            cfg = self.cfg()
            mutate(cfg)
            with self.subTest(name), self.assertRaises(SYNC.SyncError):
                SYNC.validate_config(cfg)

    def test_gitlab_connection_errors_are_sync_errors(self):
        """L1-GIS-052 GitLab 连接被断（RemoteDisconnected、ConnectionResetError、IncompleteRead）按 SyncError 失败关闭。"""
        for exc in (http.client.RemoteDisconnected("closed"), ConnectionResetError("reset"),
                    http.client.IncompleteRead(b"")):
            client = SYNC.GitLabClient(config(), {"NH_DESK_GITLAB_TOKEN": "t" * 20}, opener=RaisingOpener(exc))
            with self.subTest(type(exc).__name__), self.assertRaises(SYNC.SyncError):
                client.request("GET", "user")


if __name__ == "__main__":
    unittest.main()
