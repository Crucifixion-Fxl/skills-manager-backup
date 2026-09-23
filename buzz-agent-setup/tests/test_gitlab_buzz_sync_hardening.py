import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_hardening", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
AGENT = "a9" * 32
ALICE = "a1" * 32
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
BOT_ID = 7
BOT = "buzz-sync-bot"
PID = 481
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"
SINCE = "2026-09-13T00:00:00Z"
SERVER_TIME = "2026-09-13T04:00:00Z"


def make_issue(iid=182, **overrides):
    base = {"iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "", "state": "opened",
            "labels": ["type::feature", "status::ready"], "milestone": None, "confidential": False,
            "assignees": [], "web_url": f"{WEB}/-/issues/{iid}", "created_at": "2026-09-13T01:00:00Z",
            "updated_at": "2026-09-13T01:00:00Z"}
    base.update(overrides)
    return base


def make_mr(iid=31, **overrides):
    base = {"iid": iid, "project_id": PID, "title": f"MR {iid}", "state": "opened", "draft": False,
            "sha": "a" * 40, "source_branch": "feature/182-restore", "target_branch": "main", "labels": [],
            "reviewers": [], "author": {"username": "dave"}, "web_url": f"{WEB}/-/merge_requests/{iid}",
            "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z"}
    base.update(overrides)
    return base


class FakeGitLab:
    def __init__(self):
        self.projects = {PID: {"id": PID, "visibility": "public", "web_url": WEB, "default_branch": "main",
                               "path_with_namespace": "buzz-sync-test/pilot"}}
        self.issue_list, self.mr_list = {PID: []}, {PID: []}
        self.issue_notes, self.mr_note_list, self.closes = {}, {}, {}
        self.event_list, self.token_list = [], []
        self.writes = []

    def current_user(self):
        return {"id": BOT_ID, "username": BOT}

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        if project_id not in self.projects:
            raise SYNC.SyncError(f"GitLab project {project_id} is not readable")
        return self.projects[project_id]

    def issues(self, project_id, updated_after):
        return [dict(item) for item in self.issue_list.get(project_id, [])]

    def issue(self, project_id, iid):
        return dict(next(item for item in self.issue_list.get(project_id, []) if item["iid"] == iid))

    def notes(self, project_id, iid):
        return list(self.issue_notes.get(iid, []))

    def add_note(self, project_id, iid, body):
        self.writes.append(("issue_note", iid))
        note = {"id": 3000 + len(self.writes), "author": {"id": BOT_ID}, "body": body, "system": False,
                "created_at": SERVER_TIME}
        self.issue_notes.setdefault(iid, []).append(note)
        return note["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(item) for item in self.mr_list.get(project_id, [])]

    def members(self, project_id):
        return []

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def add_mr_note(self, project_id, iid, body):
        self.writes.append(("mr_note", iid))
        note = {"id": 4000 + len(self.writes), "author": {"id": BOT_ID}, "body": body, "system": False,
                "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        return note["id"]

    def related_merge_requests(self, project_id, issue_iid):
        return []

    def mr_closes_issues(self, project_id, iid):
        return [{"iid": value} for value in self.closes.get(iid, [])]

    def mr_diffs(self, project_id, iid):
        return []

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return []

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

    def send(self, content, reply_to=None, mentions=()):
        event_id = hashlib.sha256(f"{len(self.events)}:{content}".encode()).hexdigest()
        tags = [["h", CHANNEL]] + ([["e", reply_to, "", "reply"]] if reply_to else [])
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9, "created_at": 1000 + len(self.events),
                            "tags": tags, "content": content})
        self.writes.append((reply_to, content, tuple(mentions)))
        return event_id

    def thread(self, root):
        return [e for e in self.events if e["id"] == root or ["e", root, "", "reply"] in e["tags"]]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_messages(self, since_unix):
        return list(self.events)

    def channel_members(self):
        return {}  # no member roles: a stalled notice has no owner to tag (ADR-0010)


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


class InternalNotesTest(unittest.TestCase):
    def test_internal_and_confidential_notes_skipped(self):
        """L1-GIS-027 GitLab internal / confidential 评论不发到频道。"""
        def note(note_id, **flags):
            return {"id": note_id, "author": {"id": 3, "username": "alice"}, "body": "x", "system": False,
                    "created_at": "2026-09-13T02:00:00Z", **flags}

        pending = SYNC.pending_comments([note(1, internal=True), note(2, confidential=True), note(3)],
                                        [], DESK, BOT_ID, SINCE)
        self.assertEqual([item["id"] for item in pending], [3])


class AgentMentionTest(unittest.TestCase):
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

    def full_config(self, **overrides):
        cfg = config(**overrides)
        cfg["buzz"] = {"cli_path": str(self.cli), "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest()}
        return cfg

    def test_people_rejects_agent_pubkeys(self):
        """L1-GIS-039 people 不能映射到 agent_pubkeys 里的 agent；agent_pubkeys 必须是 hex 列表。"""
        SYNC.validate_config(self.full_config(people={"alice": ALICE}, agent_pubkeys=[AGENT]))
        for bad in ({"people": {"alice": AGENT}, "agent_pubkeys": [AGENT]},
                    {"people": {}, "agent_pubkeys": ["npub1agent"]},
                    {"people": {}, "agent_pubkeys": AGENT}):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.validate_config(self.full_config(**bad))

    def test_mention_targets_skip_bots_and_agents(self):
        """L1-GIS-039 @ 目标排除 GitLab bot 成员与 agent pubkey。"""
        mr = make_mr(reviewers=[{"username": "project_2_bot_ab12"}, {"username": "alice"}])
        members = [{"username": "group_5_bot_x", "access_level": 50},
                   {"username": "svc", "access_level": 40, "bot": True},
                   {"username": "erin", "access_level": 40}]
        people = {"alice": ALICE, "project_2_bot_ab12": "b2" * 32, "group_5_bot_x": "c3" * 32,
                  "svc": "d4" * 32, "erin": AGENT}
        pubkeys, unmapped = SYNC.mr_mention_targets(mr, members, people, agent_pubkeys=[AGENT])
        self.assertEqual((pubkeys, unmapped), ([ALICE], []))


class ProjectPrecheckTest(SyncCase):
    def test_unreadable_project_before_any_write(self):
        """L1-GIS-021 任一配置项目读不到时，在任何写入前失败关闭。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        cfg = config()
        cfg["gitlab"]["projects"] = [PID, 999]
        with self.assertRaises(SYNC.SyncError):
            self.run_sync(cfg)
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))


class BranchFallbackTest(SyncCase):
    def test_branch_name_does_not_link_issues(self):
        """L1-GIS-030 Issue 链接只来自 GitLab closes_issues；分支名（未批准的 FCHAC 分支规范）不再用来找 Issue。"""
        self.gitlab.issue_list[PID] = [make_issue(182)]
        self.gitlab.mr_list[PID] = [make_mr(31, source_branch="feature/182-restore")]
        self.run_sync()
        root_31 = next(e["content"] for e in self.buzz.events if "[mr:31]" in e["content"])
        self.assertFalse(any(line.startswith("issues:") for line in root_31.split("\n")))


if __name__ == "__main__":
    unittest.main()
