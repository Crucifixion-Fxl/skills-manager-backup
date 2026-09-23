import fcntl
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"


def make_issue(iid, **overrides):
    base = {
        "iid": iid,
        "project_id": PID,
        "title": f"Issue {iid}",
        "description": "",
        "state": "opened",
        "labels": ["type::feature", "status::ready"],
        "milestone": None,
        "confidential": False,
        "assignees": [],
        "web_url": f"http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/{iid}",
        "created_at": "2026-09-13T01:00:00Z",
        "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.projects = {PID: {"id": PID, "visibility": "public", "web_url": WEB,
                                "path_with_namespace": "buzz-sync-test/pilot"}}
        self.issue_list = {PID: []}
        self.issue_details = {}
        self.note_list = {}
        self.writes = []
        self.issue_queries = []
        self.fail_note_for = set()
        self.server_time = SERVER_TIME

    def current_user(self):
        return self.user

    def scan_time(self):
        return self.server_time

    def project(self, project_id):
        if project_id not in self.projects:
            raise SYNC.SyncError(f"GitLab project {project_id} is not readable")
        return self.projects[project_id]

    def issues(self, project_id, updated_after):
        self.issue_queries.append((project_id, updated_after))
        return [dict(issue) for issue in self.issue_list[project_id]]

    def issue(self, project_id, iid):
        if (project_id, iid) in self.issue_details:
            return dict(self.issue_details[(project_id, iid)])
        return next(dict(issue) for issue in self.issue_list[project_id] if issue.get("iid") == iid)

    def notes(self, project_id, iid):
        return list(self.note_list.get((project_id, iid), []))

    def merge_requests(self, project_id, updated_after):
        return []

    def members(self, project_id):
        return []

    def events(self, project_id, after_date):
        return []

    def pipelines(self, project_id, updated_after):
        return []

    def deployments(self, project_id, updated_after):
        return []

    def releases(self, project_id):
        return []

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return []

    def add_user_note(self, iid, note_id, body, username="alice", system=False,
                      created_at="2026-09-13T02:00:00Z"):
        self.note_list.setdefault((PID, iid), []).append({
            "id": note_id, "author": {"id": 3, "username": username}, "body": body,
            "system": system, "created_at": created_at,
        })

    def add_note(self, project_id, iid, body):
        if iid in self.fail_note_for:
            raise SYNC.SyncError("GitLab note write failed")
        note = {"id": 1000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.note_list.setdefault((project_id, iid), []).append(note)
        self.writes.append(("note", project_id, iid, body))
        return note["id"]


class FakeBuzz:
    def __init__(self):
        self.events = []
        self.writes = []

    def send(self, content, reply_to=None, mentions=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{content}".encode()).hexdigest()
        tags = [["h", CHANNEL]]
        if reply_to:
            tags.append(["e", reply_to, "", "reply"])
        tags.extend(["p", pubkey] for pubkey in mentions)
        self.events.append({
            "id": event_id, "pubkey": DESK, "kind": 9,
            "created_at": 1000 + len(self.events), "tags": tags, "content": content,
        })
        self.writes.append(("send", reply_to, content))
        return event_id

    def channel_messages(self, since_unix):
        return []

    def channel_members(self):
        return {}  # no owner to tag on a stalled notice (ADR-0010)

    def thread(self, root_event_id):
        return [
            event for event in self.events
            if event["id"] == root_event_id or ["e", root_event_id, "", "reply"] in event["tags"]
        ]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

def sync_config():
    return {
        "channel_id": CHANNEL,
        "publisher_pubkey": DESK,
        "since": SINCE,
        "include_confidential": False,
        "exclude": [{"assignee_username": "sre-investigator"}],
        "diff": {"enabled": False, "private": False},
        "gitlab": {
            "base_url": "http://127.0.0.1:8929",
            "token_env": "NH_DESK_GITLAB_TOKEN",
            "bot_user_id": BOT_ID,
            "bot_username": BOT,
            "projects": [PID],
        },
        "buzz": {},
    }


class SyncRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir(mode=0o700)
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = sync_config()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, **kwargs):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=self.state).run(**kwargs)

    def sends(self):
        return [write for write in self.buzz.writes if write[0] == "send"]

    def test_new_issue_creates_one_root_and_binding(self):
        """L1-GIS-017 新 Issue → 恰好 1 个 root + 1 条 binding note。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        summary = self.run_sync()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["created"], 1)
        sends = self.sends()
        self.assertEqual(len(sends), 2)  # plaque root + first fact (issue #78)
        _, plaque_reply, plaque_content = sends[0]
        self.assertIsNone(plaque_reply)
        self.assertEqual(SYNC.plaque_url(plaque_content), make_issue(182)["web_url"])
        root = self.buzz.events[0]["id"]
        fact_reply, fact_content = sends[1][1], sends[1][2]
        self.assertEqual(fact_reply, root)
        header = SYNC.parse_header(fact_content)
        self.assertEqual((header["object"], header["change"], header["project"], header["issue"]),
                         ("issue", "routing", PID, 182))
        self.assertIn("首次同步", fact_content)
        notes = self.gitlab.note_list[(PID, 182)]
        self.assertEqual(len(notes), 1)
        self.assertEqual(SYNC.parse_binding(notes, BOT_ID, PID, "issue", 182, CHANNEL), root)
        self.assertIn(f"buzz://message?channel={CHANNEL}&id={root}&thread={root}", notes[0]["body"])
        self.assertEqual([tag for tag in self.buzz.events[0]["tags"] if tag[0] == "p"], [])

    def test_rerun_is_noop(self):
        """L1-GIS-018 立即重跑无新增。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.run_sync()
        before = (list(self.buzz.writes), list(self.gitlab.writes))
        summary = self.run_sync()
        self.assertEqual((self.buzz.writes, self.gitlab.writes), before)
        self.assertEqual((summary["created"], summary["updated"]), (0, 0))

    def test_changes_reply_in_original_thread(self):
        """L1-GIS-019 routing / content 回帖进原 Thread；只有 updated_at 变化不发。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.run_sync()
        root = self.buzz.events[0]["id"]

        self.gitlab.issue_list[PID] = [make_issue(182, labels=["type::feature", "status::in-progress"],
                                                  updated_at="2026-09-13T02:00:00Z")]
        self.assertEqual(self.run_sync()["updated"], 1)
        _, reply_to, content = self.sends()[-1]
        self.assertEqual(reply_to, root)
        self.assertEqual(SYNC.parse_header(content)["change"], "routing")
        self.assertEqual(SYNC.parse_header(content)["status"], "in-progress")

        self.gitlab.issue_list[PID] = [make_issue(182, labels=["type::feature", "status::in-progress"],
                                                  title="新标题", updated_at="2026-09-13T02:10:00Z")]
        self.run_sync()
        _, reply_to, content = self.sends()[-1]
        self.assertEqual((reply_to, SYNC.parse_header(content)["change"]), (root, "content"))

        count = len(self.sends())
        self.gitlab.issue_list[PID] = [make_issue(182, labels=["type::feature", "status::in-progress"],
                                                  title="新标题", updated_at="2026-09-13T02:20:00Z")]
        self.assertEqual(self.run_sync()["updated"], 0)
        self.assertEqual(len(self.sends()), count)

    def test_activity_replies_for_comments_and_other_fields(self):
        """L1-GIS-027 评论与其他字段变化回 activity；同一评论只发一次；bot 与 system note 不发。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.run_sync()
        root = self.buzz.events[0]["id"]

        self.gitlab.add_user_note(182, 55, "@nh-feature 看一下")
        self.gitlab.add_user_note(182, 56, "added ~team::app label", system=True)
        self.run_sync()
        activity = [write for write in self.sends() if "[note:55]" in write[2]]
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0][1], root)
        self.assertEqual(SYNC.parse_header(activity[0][2])["change"], "activity")
        self.assertEqual(SYNC.parse_header(activity[0][2])["note"], 55)
        self.assertIn("＠nh-feature", activity[0][2])
        self.assertFalse(any("[note:56]" in write[2] for write in self.sends()))
        self.assertFalse(any(f"[note:{note['id']}]" in write[2]
                             for write in self.sends() for note in self.gitlab.note_list[(PID, 182)]
                             if note["author"]["id"] == BOT_ID))

        count = len(self.sends())
        self.run_sync()
        self.assertEqual(len(self.sends()), count)

        self.gitlab.issue_list[PID] = [make_issue(182, labels=["type::feature", "status::ready", "team::app"],
                                                  assignees=[{"username": "bob"}],
                                                  updated_at="2026-09-13T03:00:00Z")]
        self.run_sync()
        _, reply_to, content = self.sends()[-1]
        self.assertEqual((reply_to, SYNC.parse_header(content)["change"]), (root, "activity"))
        self.assertIn("labels team::app", content)
        self.assertIn("assignees bob", content)

    def test_recovers_missing_binding_without_new_root(self):
        """L1-GIS-020 root 已发、note 未写 → 找回原 root 补 note，不新建 root。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.gitlab.fail_note_for = {182}
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()
        self.assertEqual(len(self.sends()), 1)
        root = self.buzz.events[0]["id"]
        self.gitlab.fail_note_for = set()
        summary = self.run_sync()
        # the plaque survived the crash; the first fact still has to be sent once
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(summary["recovered"], 1)
        notes = self.gitlab.note_list[(PID, 182)]
        self.assertEqual(SYNC.parse_binding(notes, BOT_ID, PID, "issue", 182, CHANNEL), root)

    def test_fail_closed_before_and_during_writes(self):
        """L1-GIS-021 / L1-GIS-041 身份、project 或 bound root 不可信时失败关闭且不跨 cursor。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.gitlab.user = {"id": 99, "username": "someone"}
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()
        self.gitlab.user = {"id": BOT_ID, "username": BOT}
        self.gitlab.projects = {}
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))

        self.gitlab.projects = {PID: {"id": PID, "visibility": "public",
                                      "web_url": "http://127.0.0.1:8929/buzz-sync-test/pilot"}}
        missing_root = "f" * 64
        self.gitlab.note_list[(PID, 182)] = [{
            "id": 1, "author": {"id": BOT_ID, "username": BOT}, "system": False,
            "created_at": SERVER_TIME,
            "body": SYNC.render_binding_note(
                {"project_id": PID, "object": "issue", "iid": 182, "channel_id": CHANNEL,
                 "root_event_id": missing_root},
                "buzz://x"),
        }]
        self.gitlab.issue_list[PID] = [make_issue(182), make_issue(183, created_at="2026-09-13T01:30:00Z")]
        # ADR-0009: an unreadable bound root is bad data on issue 182 only — it stalls, issue 183 still syncs.
        summary = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in summary["stalled"]], [("issue", 182)])
        self.assertFalse(any(reply_to == missing_root for _, reply_to, _ in self.buzz.writes))
        self.assertTrue(any("183" in content for _, _, content in self.buzz.writes))
        cache = json.loads(next(path for path in self.state.glob("*") if path.name.endswith(".cache.json")).read_text())
        self.assertEqual(cache["stalled"], [{"project": PID, "object": "issue", "iid": 182}])

    def test_skips_confidential_excluded_and_backfill(self):
        """L1-GIS-022 confidential、exclude、since 前存量都不写，只计数。"""
        self.gitlab.issue_list[PID] = [
            make_issue(190, confidential=True),
            make_issue(191, assignees=[{"username": "sre-investigator"}]),
            make_issue(192, created_at="2026-09-12T23:00:00Z", updated_at="2026-09-13T01:00:00Z"),
        ]
        summary = self.run_sync()
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))
        self.assertEqual(summary["skipped"], {"confidential": 1, "excluded": 1, "backfill": 1, "milestone_identity": 0})

    def test_lock_held_means_no_work(self):
        """L1-GIS-023 同频道并发：拿不到锁的一方零调用退出。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        path = SYNC.lock_path(self.config, self.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            summary = self.run_sync()
        self.assertEqual(summary["status"], "locked")
        self.assertEqual((self.gitlab.issue_queries, self.buzz.writes), ([], []))

    def test_cache_cursor_only_accelerates(self):
        """L1-GIS-024 无缓存从 since 扫；成功后缓存 = 扫描起点 − 60 秒；缓存损坏回退 since。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.run_sync()
        self.assertEqual(self.gitlab.issue_queries[-1], (PID, SINCE))
        caches = list(self.state.glob("*.cache.json"))
        self.assertEqual(len(caches), 1)
        self.assertEqual(caches[0].stat().st_mode & 0o777, 0o600)
        self.run_sync()
        self.assertEqual(self.gitlab.issue_queries[-1], (PID, "2026-09-13T03:59:00Z"))
        caches[0].write_text("{broken", encoding="utf-8")
        self.run_sync()
        self.assertEqual(self.gitlab.issue_queries[-1], (PID, SINCE))

    def test_dry_run_plans_without_writes(self):
        """L1-GIS-026 dry-run 只报计划，不写任何一侧，也不写缓存。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        summary = self.run_sync(dry_run=True)
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))
        self.assertEqual(summary["created"], 1)
        self.assertEqual(list(self.state.glob("*.cache.json")), [])


class MainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        release = Path(cls.tmp.name) / "buzz-0.5.23" / "usr" / "bin"
        release.mkdir(parents=True)
        cls.cli = release / "buzz"
        cls.cli.write_bytes(b"\x7fELFtest fixture")
        cls.cli.chmod(0o700)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def write_config(self):
        config = sync_config()
        config["buzz"] = {"cli_path": str(self.cli),
                          "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest()}
        path = Path(self.tmp.name) / "sync.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        path.chmod(0o600)
        return path

    def run_main(self, gitlab, buzz):
        env = {
            "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
            "BUZZ_PRIVATE_KEY": "nsec-secret-value",
            "NH_DESK_GITLAB_TOKEN": "glpat-secret-value",
            "HOME": self.tmp.name,
        }
        out = io.StringIO()
        with redirect_stdout(out):
            code = SYNC.main(
                ["--config", str(self.write_config()), "--state-dir", str(Path(self.tmp.name) / "state")],
                env=env,
                adapter_factory=lambda config, env, dry_run: (gitlab, buzz),
            )
        return code, out.getvalue()

    def test_main_outputs_counts_and_links_without_secrets(self):
        """L1-GIS-025 stdout 只有计数与链接；失败非零退出；不回显 secret。"""
        gitlab, buzz = FakeGitLab(), FakeBuzz()
        gitlab.issue_list[PID] = [make_issue(182)]
        code, stdout = self.run_main(gitlab, buzz)
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual((result["status"], result["created"]), ("ok", 1))
        self.assertTrue(all(link.startswith("buzz://message?") for link in result["links"]))
        self.assertNotIn("Issue 182", stdout)

        gitlab.user = {"id": 99, "username": "someone"}
        code, stdout = self.run_main(gitlab, FakeBuzz())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["status"], "error")
        for secret in ("nsec-secret-value", "glpat-secret-value"):
            self.assertNotIn(secret, stdout)


if __name__ == "__main__":
    unittest.main()
