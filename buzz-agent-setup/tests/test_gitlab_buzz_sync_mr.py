import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mr", SCRIPT)
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
ALICE, BOB, CAROL, DAVE = "a1" * 32, "b2" * 32, "c3" * 32, "d4" * 32
PEOPLE = {"alice": ALICE, "bob": BOB, "carol": CAROL, "dave": DAVE}
MEMBERS = [
    {"username": "alice", "access_level": 40},
    {"username": "bob", "access_level": 30},
    {"username": "dave", "access_level": 50},
    {"username": "erin", "access_level": 40},
]
MR_HEADER = ("[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
             "[transition:reviewable][project:481][mr:31]")


def make_mr(iid=31, **overrides):
    base = {
        "iid": iid,
        "project_id": PID,
        "title": "恢复最近工作区",
        "state": "opened",
        "draft": False,
        "sha": "a" * 40,
        "source_branch": "feature/restore-workspace",
        "target_branch": "main",
        "labels": [],
        "reviewers": [],
        "author": {"username": "dave"},
        "web_url": f"http://127.0.0.1:8929/buzz-sync-test/pilot/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z",
        "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def reviewers(*names):
    return [{"username": name} for name in names]


class MrMessageTest(unittest.TestCase):
    def test_render_and_parse_mr_header(self):
        """L1-GIS-028 / L1-GIS-179 MR header 固定顺序在末行；人读正文：标题·作者、分支·sha·desc。"""
        fact = SYNC.mr_fact(make_mr(labels=["team::app"], reviewers=reviewers("carol")), PID)
        rendered = SYNC.render_mr_message(fact, "lifecycle", became_reviewable=True)
        lines = rendered.split("\n")
        self.assertEqual(lines[-1], MR_HEADER + f"[desc:{fact['description']}]")
        self.assertEqual(lines[:-1], [
            "👀 **可评审** · [!31 恢复最近工作区]"
            "(http://127.0.0.1:8929/buzz-sync-test/pilot/-/merge_requests/31) · dave",
            "feature/restore-workspace -> main · sha " + "a" * 12
            + " · labels team::app · reviewers carol",
        ])
        self.assertEqual(
            SYNC.parse_header(rendered),
            {"object": "mr", "state": "opened", "draft": "no", "change": "lifecycle",
             "transition": "reviewable", "project": 481, "mr": 31, "desc": fact["description"]},
        )

    def test_mr_header_rejects_bad_values_and_neutralizes_text(self):
        """L1-GIS-028 / L1-GIS-038 MR header 取值校验；标题与分支名的 @ 转义。"""
        for bad in (
            MR_HEADER.replace("change:lifecycle", "change:routing"),
            MR_HEADER.replace("transition:reviewable", "transition:maybe"),
            MR_HEADER.replace("[transition:reviewable]", ""),
            MR_HEADER.replace("draft:no", "draft:maybe"),
            MR_HEADER.replace("state:opened", "state:reopened"),
            MR_HEADER.replace("[mr:31]", "[issue:31]"),
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(SYNC.parse_header(bad))
        fact = SYNC.mr_fact(make_mr(state="merged", draft=True, title="@nh-dev merge me",
                                    source_branch="feature/@x"), PID)
        self.assertEqual((fact["state"], fact["draft"]), ("merged", "yes"))
        rendered = SYNC.render_mr_message(fact, "update")
        self.assertIn("[!31 ＠nh-dev merge me]", rendered)
        self.assertNotIn("@", rendered.replace("[gitlab-notify:v1]", ""))
        with self.assertRaises(SYNC.SyncError):
            SYNC.render_mr_message(fact, "routing")
        with self.assertRaises(SYNC.SyncError):
            SYNC.render_mr_message(fact, "update", became_reviewable=True)
        with self.assertRaises(SYNC.SyncError):
            SYNC.render_mr_message(SYNC.mr_fact(make_mr(state="locked"), PID), "lifecycle",
                                   became_reviewable=True)
        with self.assertRaises(SYNC.SyncError):
            SYNC.mr_fact(make_mr(state="weird"), PID)

    def test_classify_mr_change_and_previous(self):
        """L1-GIS-029 lifecycle / update / 无变化；从 MR Thread 还原上一版。"""
        current = SYNC.mr_fact(make_mr(reviewers=reviewers("carol")), PID)
        self.assertEqual(SYNC.classify_mr_change(None, current), "lifecycle")
        for key, value in (("state", "merged"), ("draft", "yes")):
            with self.subTest(key=key):
                self.assertEqual(SYNC.classify_mr_change({**current, key: value}, current), "lifecycle")
        for key, value in (("sha", "b" * 40), ("labels", ["x"]), ("reviewers", []), ("title", "旧")):
            with self.subTest(key=key):
                self.assertEqual(SYNC.classify_mr_change({**current, key: value}, current), "update")
        self.assertIsNone(SYNC.classify_mr_change(dict(current), current))

        root = {"id": "1" * 64, "pubkey": DESK, "kind": 9, "created_at": 100, "tags": [["h", CHANNEL]],
                "content": SYNC.render_mr_message(SYNC.mr_fact(make_mr(), PID), "lifecycle")}
        reply = {"id": "2" * 64, "pubkey": DESK, "kind": 9, "created_at": 200,
                 "tags": [["h", CHANNEL], ["e", "1" * 64, "", "reply"]],
                 "content": SYNC.render_mr_message(current, "update")}
        previous = SYNC.previous_mr_fact_from_thread([root, reply], DESK, PID, 31)
        self.assertEqual({key: value for key, value in previous.items() if key != "sha"},
                         {key: current[key] for key in previous if key != "sha"})
        self.assertEqual(previous["sha"], current["sha"][:12])
        self.assertEqual(previous["reviewers"], ["carol"])
        self.assertIsNone(SYNC.classify_mr_change(previous, current))

    def test_compact_message_has_no_empty_field_lines(self):
        """L1-GIS-179 空的 labels / reviewers / assignees / milestone 整行省略；消息只有 4 行正文。"""
        fact = SYNC.mr_fact(make_mr(state="merged", title="修复百科 CMS 基础镜像漏洞 · 双架构扫描"), PID)
        lines = SYNC.render_mr_message(fact, "lifecycle", first=True).split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(
            lines[0],
            "🔀 **新建 · 已合并** · [!31 修复百科 CMS 基础镜像漏洞 · 双架构扫描]"
            "(http://127.0.0.1:8929/buzz-sync-test/pilot/-/merge_requests/31) · dave",
        )
        self.assertFalse(any(line.startswith(("labels", "reviewers", "assignees", "milestone", "title:"))
                             for line in lines))

    def test_compact_message_round_trips_every_compared_field(self):
        """L1-GIS-180 紧凑消息读回后与 GitLab 快照逐项一致（含标题里的 “ · ”），不会误判为 update；改任一字段仍判为 update。"""
        mr = make_mr(title="A · B", labels=["team::app", "type::bug"], reviewers=reviewers("carol", "erin"),
                     assignees=reviewers("frank"), milestone={"title": "1.4.0"}, description="body")
        current = SYNC.mr_fact(mr, PID)
        event = {"id": "3" * 64, "pubkey": DESK, "kind": 9, "created_at": 300, "tags": [["h", CHANNEL]],
                 "content": SYNC.render_mr_message(current, "lifecycle")}
        previous = SYNC.previous_mr_fact_from_thread([event], DESK, PID, 31)
        self.assertEqual(previous["title"], "A · B")
        self.assertEqual(previous["author"], "dave")
        self.assertEqual(previous["assignees"], ["frank"])
        self.assertEqual(previous["milestone"], "1.4.0")
        self.assertIsNone(SYNC.classify_mr_change(previous, current))
        for override in ({"sha": "b" * 40}, {"labels": ["team::app"]}, {"milestone": None}, {"description": "new"}):
            with self.subTest(override=override):
                changed = SYNC.mr_fact({**mr, **override}, PID)
                self.assertEqual(SYNC.classify_mr_change(previous, changed), "update")

    def test_separator_inside_labels_and_milestone_round_trips(self):
        """L1-GIS-202 a label or milestone containing “ · ” cannot truncate or forge compact fields on readback."""
        mr = make_mr(title="fix · reviewers mallory · sha 000", labels=["area · ops"],
                     milestone={"title": "x · labels evil"})
        current = SYNC.mr_fact(mr, PID)
        event = {"id": "5" * 64, "pubkey": DESK, "kind": 9, "created_at": 500, "tags": [["h", CHANNEL]],
                 "content": SYNC.render_mr_message(current, "lifecycle")}
        previous = SYNC.previous_mr_fact_from_thread([event], DESK, PID, 31)
        self.assertEqual(previous["labels"], current["labels"])
        self.assertEqual(previous["milestone"], current["milestone"])
        self.assertEqual(previous["title"], current["title"])
        self.assertIsNone(SYNC.classify_mr_change(previous, current))

    def test_legacy_long_message_still_reads_back_without_spurious_update(self):
        """L1-GIS-181 已发出的旧格式（title: / sha: 全长 …）仍能读回，与相同快照比较不产生 update。"""
        current = SYNC.mr_fact(make_mr(labels=["team::app"], reviewers=reviewers("carol")), PID)
        legacy = "\n".join([
            MR_HEADER.replace("[transition:reviewable]", "[transition:none]").replace("change:lifecycle", "change:update"),
            f"title: {current['title']}", f"url: {current['url']}", f"branches: {current['branches']}",
            f"sha: {current['sha']}", "labels: team::app", "reviewers: carol", "assignees: -", "milestone: -",
            f"description: {current['description']}", "author: dave",
        ])
        event = {"id": "4" * 64, "pubkey": DESK, "kind": 9, "created_at": 400, "tags": [["h", CHANNEL]], "content": legacy}
        previous = SYNC.previous_mr_fact_from_thread([event], DESK, PID, 31)
        self.assertEqual(previous["sha"], current["sha"])
        self.assertIsNone(SYNC.classify_mr_change(previous, current))

    def test_mr_comment_headline_links_to_the_comment_not_the_mr(self):
        """L1-GIS-232 MR 评论消息标题链接带 #note_<id> 锚点；非评论消息链接不变，且标题行仍能被读回。"""
        fact = SYNC.mr_fact(make_mr(), PID)
        note = {"id": 9, "author": {"id": 3, "username": "alice"}, "body": "看看", "system": False}
        headline = SYNC.render_mr_comment_message(fact, note).split("\n")[0]
        self.assertIn("/-/merge_requests/31#note_9) · dave", headline)
        plain = SYNC.render_mr_message(fact, "update").split("\n")[0]
        self.assertIn("/-/merge_requests/31) · dave", plain)
        self.assertNotIn("#note_", plain)
        fields = SYNC._styled_mr_fields(SYNC.render_mr_comment_message(fact, note), 31)
        self.assertEqual(fields["author"], "dave")


class MentionTest(unittest.TestCase):
    def test_mention_targets(self):
        """L1-GIS-039 reviewers ∪ Maintainer+，排除作者，只取映射；未映射只列名字。"""
        mr = make_mr(reviewers=reviewers("carol", "bob"))
        pubkeys, unmapped = SYNC.mr_mention_targets(mr, MEMBERS, PEOPLE)
        self.assertEqual(pubkeys, [CAROL, BOB, ALICE])
        self.assertEqual(unmapped, ["erin"])
        self.assertNotIn(DAVE, pubkeys)

    def test_mention_targets_share_the_three_human_attention_budget(self):
        """L1-GIS-125 Sync records, but does not tag, the fourth responsible human."""
        erin, frank = "e5" * 32, "f6" * 32
        people = {**PEOPLE, "erin": erin, "frank": frank}
        mr = make_mr(reviewers=reviewers("alice", "bob", "carol", "erin", "frank"))

        pubkeys, unresolved = SYNC.mr_mention_targets(mr, [], people)

        self.assertEqual(pubkeys, [ALICE, BOB, CAROL])
        self.assertEqual(unresolved, [
            "erin(attention_budget)",
            "frank(attention_budget)",
        ])

    def test_mention_timing(self):
        """L1-GIS-040 非 draft 新建与 draft→ready @ 全集；新增 reviewer 只 @ 新人；draft 新建不 @。"""
        full = sorted([ALICE, CAROL])
        ready = SYNC.mr_fact(make_mr(reviewers=reviewers("carol")), PID)
        draft = SYNC.mr_fact(make_mr(draft=True, reviewers=reviewers("carol")), PID)
        more = SYNC.mr_fact(make_mr(reviewers=reviewers("carol", "bob")), PID)
        self.assertEqual(SYNC.mr_mentions(None, ready, full, PEOPLE), full)
        self.assertEqual(SYNC.mr_mentions(None, draft, full, PEOPLE), [])
        self.assertEqual(SYNC.mr_mentions(draft, ready, full, PEOPLE), full)
        self.assertEqual(SYNC.mr_mentions(ready, more, full, PEOPLE), [BOB])
        self.assertEqual(SYNC.mr_mentions(ready, ready, full, PEOPLE), [])
        self.assertEqual(SYNC.mr_mentions(ready, SYNC.mr_fact(make_mr(reviewers=reviewers("carol", "dave")), PID),
                                          full, PEOPLE), [])

    def test_send_args_mentions(self):
        """L1-GIS-040 --mention 参数逐个追加；非法 pubkey 拒绝。"""
        args = SYNC.build_send_args("/x/buzz-0.5.23/buzz", CHANNEL, mentions=[ALICE, CAROL])
        self.assertEqual(args[-4:], ["--mention", ALICE, "--mention", CAROL])
        with self.assertRaises(SYNC.SyncError):
            SYNC.build_send_args("/x/buzz-0.5.23/buzz", CHANNEL, mentions=["npub1x"])


class PeopleConfigTest(unittest.TestCase):
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

    def config(self, people):
        return {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE,
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {"cli_path": str(self.cli), "cli_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest()},
            "people": people,
        }

    def test_people_mapping_validation(self):
        """L1-GIS-039 people 只填人：拒绝 Desk pubkey、非法 pubkey 与非法用户名。"""
        SYNC.validate_config(self.config(PEOPLE))
        for bad in ({"alice": DESK}, {"alice": "npub1abc"}, {"": ALICE}, ["alice"]):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.validate_config(self.config(bad))


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.mr_list = []
        self.issue_notes = {}
        self.mr_note_list = {}
        self.writes = []
        self.fail_mr_note_for = set()

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "visibility": "public", "web_url": WEB,
                "path_with_namespace": "buzz-sync-test/pilot"}

    def issues(self, project_id, updated_after):
        return []

    def notes(self, project_id, iid):
        return list(self.issue_notes.get(iid, []))

    def issue(self, project_id, iid):
        return {"iid": iid, "project_id": project_id, "title": f"Issue {iid}", "description": "",
                "state": "opened", "labels": ["type::feature", "status::ready"], "milestone": None,
                "confidential": False, "assignees": [],
                "web_url": f"http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/{iid}",
                "created_at": SINCE, "updated_at": SINCE}

    def add_note(self, project_id, iid, body):
        raise AssertionError("MR sync must not write Issue notes")

    def merge_requests(self, project_id, updated_after):
        return [dict(mr) for mr in self.mr_list]

    def members(self, project_id):
        return list(MEMBERS)

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

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def related_merge_requests(self, project_id, issue_iid):
        return []

    def mr_closes_issues(self, project_id, iid):
        return []

    def add_mr_note(self, project_id, iid, body):
        if iid in self.fail_mr_note_for:
            raise SYNC.SyncError("GitLab MR note write failed")
        note = {"id": 2000 + len(self.writes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(note)
        self.writes.append(("mr_note", iid, body))
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
        self.events.append({"id": event_id, "pubkey": DESK, "kind": 9,
                            "created_at": 1000 + len(self.events), "tags": tags, "content": content})
        self.writes.append((reply_to, content, tuple(mentions)))
        return event_id

    def channel_messages(self, since_unix):
        return []

    def thread(self, root_event_id):
        return [e for e in self.events if e["id"] == root_event_id or ["e", root_event_id, "", "reply"] in e["tags"]]

    def search_roots(self, query, object_kind, iid, since_unix=None):
        # issue #78: recovery searches by the canonical object URL (plaque or legacy root)
        return [e for e in self.events
                if query in str(e.get("content", ""))
                and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_members(self):
        return {pubkey: "member" for pubkey in PEOPLE.values()}


class MrSyncRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": PEOPLE,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def test_ready_mr_gets_root_binding_and_mentions(self):
        """L1-GIS-030 / L1-GIS-039 非 draft 新 MR → 1 个 MR root @ reviewers∪Maintainer（去作者）+ 1 条 MR binding。"""
        self.gitlab.mr_list = [make_mr(reviewers=reviewers("carol"))]
        summary = self.run_sync()
        self.assertEqual(summary["mr_created"], 1)
        self.assertEqual(len(self.buzz.writes), 2)  # plaque root + first fact (issue #78)
        plaque_reply, plaque_content, plaque_mentions = self.buzz.writes[0]
        self.assertIsNone(plaque_reply)
        self.assertEqual(plaque_mentions, ())
        self.assertEqual(SYNC.plaque_url(plaque_content),
                         "http://127.0.0.1:8929/buzz-sync-test/pilot/-/merge_requests/31")
        reply_to, content, mentions = self.buzz.writes[1]
        self.assertEqual(reply_to, self.buzz.events[0]["id"])
        self.assertEqual(SYNC.parse_header(content)["change"], "lifecycle")
        self.assertEqual(SYNC.parse_header(content)["transition"], "reviewable")
        self.assertEqual(sorted(mentions), sorted([ALICE, CAROL]))
        self.assertIn("unmapped: erin", content)
        root = self.buzz.events[0]["id"]
        self.assertEqual(SYNC.parse_binding(self.gitlab.mr_note_list[31], BOT_ID, PID, "mr", 31, CHANNEL), root)

        before = list(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes, before)

    def test_draft_ready_reviewer_merge_and_comment_flow(self):
        """L1-GIS-030 / L1-GIS-040 draft 不 @；ready @ 全集；加 reviewer 只 @ 新人；合并 @ 作者；评论回 activity 并 @ 点名的人、作者与 reviewers。"""
        self.gitlab.mr_list = [make_mr(draft=True, reviewers=reviewers("carol"))]
        self.run_sync()
        root = self.buzz.events[0]["id"]
        self.assertEqual(self.buzz.writes[-1][2], ())

        self.gitlab.mr_list = [make_mr(draft=False, reviewers=reviewers("carol"), updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertEqual((reply_to, SYNC.parse_header(content)["change"]), (root, "lifecycle"))
        self.assertEqual(SYNC.parse_header(content)["transition"], "reviewable")
        self.assertEqual(sorted(mentions), sorted([ALICE, CAROL]))

        self.gitlab.mr_list = [make_mr(reviewers=reviewers("carol", "bob"), updated_at="2026-09-13T02:10:00Z")]
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertEqual((reply_to, SYNC.parse_header(content)["change"], mentions), (root, "update", (BOB,)))
        self.assertEqual(SYNC.parse_header(content)["transition"], "none")

        self.gitlab.mr_list = [make_mr(state="merged", reviewers=reviewers("carol", "bob"),
                                       updated_at="2026-09-13T02:20:00Z")]
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        # 2026-09-19 attention policy: a merge tells the author (never the route-gate transition).
        self.assertEqual((reply_to, SYNC.parse_header(content)["state"], mentions), (root, "merged", (DAVE,)))
        self.assertEqual(SYNC.parse_header(content)["transition"], "none")

        self.gitlab.mr_note_list.setdefault(31, []).append({
            "id": 77, "author": {"id": 3, "username": "alice"}, "body": "LGTM @dave",
            "system": False, "created_at": "2026-09-13T02:30:00Z"})
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        # comment attention: named project member first, then author and reviewers (minus the commenter).
        self.assertEqual((reply_to, SYNC.parse_header(content)["change"], mentions),
                         (root, "activity", (DAVE, BOB, CAROL)))
        self.assertIn("[note:77]", content)
        self.assertEqual(SYNC.parse_header(content)["note"], 77)
        self.assertIn("＠dave", content)

    def test_locked_to_opened_is_not_reviewable_but_closed_to_opened_is(self):
        """L1-GIS-095 locked→opened 不会被 Workflow 再指派；closed→opened 明确标记为可评审。"""
        self.gitlab.mr_list = [make_mr(state="locked")]
        self.run_sync()
        root = self.buzz.events[0]["id"]
        self.assertEqual(SYNC.parse_header(self.buzz.writes[-1][1])["transition"], "none")

        self.gitlab.mr_list = [make_mr(state="opened", updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertEqual(reply_to, root)
        self.assertEqual(SYNC.parse_header(content)["transition"], "none")
        self.assertEqual(mentions, ())

        self.gitlab.mr_list = [make_mr(state="closed", updated_at="2026-09-13T02:10:00Z")]
        self.run_sync()
        self.assertEqual(SYNC.parse_header(self.buzz.writes[-1][1])["transition"], "none")
        self.gitlab.mr_list = [make_mr(state="opened", updated_at="2026-09-13T02:20:00Z")]
        self.run_sync()
        reply_to, content, mentions = self.buzz.writes[-1]
        self.assertEqual(reply_to, root)
        self.assertEqual(SYNC.parse_header(content)["transition"], "reviewable")
        self.assertEqual(sorted(mentions), sorted([ALICE]))

    def test_mr_binding_recovery(self):
        """L1-GIS-030 MR root 已发、note 未写 → 找回原 root 补 note，不新建 root、不重复 @。"""
        self.gitlab.mr_list = [make_mr(reviewers=reviewers("carol"))]
        self.gitlab.fail_mr_note_for = {31}
        with self.assertRaises(SYNC.SyncError):
            self.run_sync()
        self.gitlab.fail_mr_note_for = set()
        summary = self.run_sync()
        self.assertEqual(summary["recovered"], 1)
        # plaque survived; the first fact is sent once on the rerun
        self.assertEqual(len(self.buzz.writes), 2)
        self.assertEqual(SYNC.parse_binding(self.gitlab.mr_note_list[31], BOT_ID, PID, "mr", 31, CHANNEL),
                         self.buzz.events[0]["id"])


if __name__ == "__main__":
    unittest.main()
