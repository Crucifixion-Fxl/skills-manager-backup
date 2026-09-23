"""Every sync message type's attention rule: who is @-mentioned, and who never is.

Structured sources (assignees, author, reviewers, pipeline/deployment user) plus
exact `@username` tokens in comment bodies, all resolved through the same gate:
mapped in `people`, current human Channel member, never an agent, at most three
per message, `p` tags read back. GitLab text itself stays neutralized.
"""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_mentions", SCRIPT)
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
SHA = "a" * 40
ALICE, BOB, CAROL, DAVE = "a1" * 32, "b2" * 32, "c3" * 32, "d4" * 32
PEOPLE = {"alice": ALICE, "bob": BOB, "carol": CAROL, "dave": DAVE}
# erin is a project member with no Buzz mapping; outsider is not a project member at all.
PROJECT_MEMBERS = [{"username": name, "access_level": 30} for name in
                   ("alice", "bob", "carol", "dave", "erin")]


def make_issue(iid=5, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": f"Issue {iid}", "description": "", "state": "opened",
        "labels": ["type::feature", "status::ready"], "milestone": None, "confidential": False,
        "assignees": [], "author": {"username": "dave"}, "web_url": f"{WEB}/-/issues/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def make_mr(iid=31, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": "恢复最近工作区", "state": "opened", "draft": False,
        "sha": SHA, "source_branch": "feature/restore-workspace", "target_branch": "main", "labels": [],
        "reviewers": [], "author": {"username": "dave"}, "web_url": f"{WEB}/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return base


def users(*names):
    return [{"username": name} for name in names]


def note(note_id, author, body, created_at="2026-09-13T02:30:00Z"):
    return {"id": note_id, "author": {"id": 100 + note_id, "username": author}, "body": body,
            "system": False, "created_at": created_at}


class FakeGitLab:
    def __init__(self):
        self.user = {"id": BOT_ID, "username": BOT}
        self.issue_list = []
        self.issue_notes = {}
        self.mr_list = []
        self.mr_note_list = {}
        self.event_list = []
        self.pipeline_list = []
        self.pipeline_users = {}
        self.deployment_list = []
        self.release_list = []
        self.token_list = []
        self.members_list = list(PROJECT_MEMBERS)
        self.calls = []

    def current_user(self):
        return self.user

    def scan_time(self):
        return SERVER_TIME

    def project(self, project_id):
        return {"id": project_id, "visibility": "public", "web_url": WEB, "default_branch": "main",
                "path_with_namespace": "buzz-sync-test/pilot"}

    def issues(self, project_id, updated_after):
        return [dict(issue) for issue in self.issue_list]

    def issue(self, project_id, iid):
        return dict(next(issue for issue in self.issue_list if issue["iid"] == iid))

    def notes(self, project_id, iid):
        return list(self.issue_notes.get(iid, []))

    def add_note(self, project_id, iid, body):
        entry = {"id": 3000 + len(self.issue_notes), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                 "system": False, "created_at": SERVER_TIME}
        self.issue_notes.setdefault(iid, []).append(entry)
        return entry["id"]

    def merge_requests(self, project_id, updated_after):
        return [dict(mr) for mr in self.mr_list]

    def merge_request(self, project_id, iid):
        return dict(next(mr for mr in self.mr_list if mr["iid"] == iid))

    def mr_notes(self, project_id, iid):
        return list(self.mr_note_list.get(iid, []))

    def add_mr_note(self, project_id, iid, body):
        entry = {"id": 4000 + len(self.mr_note_list), "author": {"id": BOT_ID, "username": BOT}, "body": body,
                 "system": False, "created_at": SERVER_TIME}
        self.mr_note_list.setdefault(iid, []).append(entry)
        return entry["id"]

    def members(self, project_id):
        return list(self.members_list)

    def related_merge_requests(self, project_id, issue_iid):
        return []

    def mr_closes_issues(self, project_id, iid):
        return []

    def events(self, project_id, after_date):
        return list(self.event_list)

    def pipelines(self, project_id, updated_after):
        return list(self.pipeline_list)

    def pipeline(self, project_id, pipeline_id):
        self.calls.append(("pipeline", pipeline_id))
        return {"id": pipeline_id, "user": {"username": self.pipeline_users.get(pipeline_id, "")}}

    def pipeline_jobs(self, project_id, pipeline_id):
        return []

    def deployments(self, project_id, updated_after):
        return list(self.deployment_list)

    def releases(self, project_id):
        return list(self.release_list)

    def feature_flags(self, project_id):
        return []

    def access_tokens(self, project_id):
        return list(self.token_list)


class FakeBuzz:
    def __init__(self):
        self.events = []
        self.writes = []
        self.roles = {pubkey: "member" for pubkey in PEOPLE.values()}

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
        return [e for e in self.events
                if query in str(e.get("content", "")) and not any(tag[0] == "e" for tag in e.get("tags") or [])]

    def channel_members(self):
        return dict(self.roles)


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gitlab = FakeGitLab()
        self.buzz = FakeBuzz()
        self.config = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": BOT_ID, "bot_username": BOT, "projects": [PID]},
            "buzz": {}, "people": dict(PEOPLE),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return SYNC.Syncer(self.config, self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run()

    def last(self):
        return self.buzz.writes[-1]

    def mentions_containing(self, needle):
        return [mentions for _, content, mentions in self.buzz.writes if needle in content]


class CommentMentionCandidatesTest(unittest.TestCase):
    MEMBERS = {"alice", "bob", "carol", "dave", "erin", "a.b-c", "all"}

    def names(self, body, author="zed"):
        return [item["username"] for item in SYNC.comment_mention_candidates(body, self.MEMBERS, author)]

    def test_exact_project_members_only_in_first_seen_order(self):
        """L1-GIS-206 评论里的 @username 只认精确匹配的项目成员，按首次出现排序、去重。"""
        self.assertEqual(self.names("cc @carol and @alice, also @carol again"), ["carol", "alice"])
        self.assertEqual(self.names("@stranger @erin"), ["erin"])
        self.assertEqual(self.names("ping @a.b-c."), ["a.b-c"])
        self.assertEqual(self.names("@Carol"), [])  # exact and case-sensitive, never fuzzy
        for item in SYNC.comment_mention_candidates("@carol", self.MEMBERS, "zed"):
            self.assertEqual(item, {"username": "carol", "source": "gitlab.comment_mention"})

    def test_reserved_group_email_url_and_code_forms_never_count(self):
        """L1-GIS-207 @all/@everyone、@group/sub、邮箱、URL、代码块、行内代码、引用都不产生候选。"""
        body = "\n".join([
            "@all @everyone @here @channel",
            "@carol/subgroup",
            "mail bob@example.com or https://host/@alice",
            "```",
            "@dave in a fence",
            "```",
            "inline `@erin` code",
            "> quoted @alice reply",
            "<!-- @bob hidden -->",
        ])
        self.assertEqual(self.names(body), [])

    def test_commenter_and_gitlab_bots_are_not_candidates(self):
        """L1-GIS-208 评论人自己与 project/group bot 用户名不进候选。"""
        members = self.MEMBERS | {"project_1740_bot_ab12cd"}
        got = [item["username"] for item in
               SYNC.comment_mention_candidates("@alice @project_1740_bot_ab12cd @bob", members, "alice")]
        self.assertEqual(got, ["bob"])


class IssueAttentionTest(SyncCase):
    def test_new_opened_issue_mentions_mapped_assignees(self):
        """L1-GIS-209 新 Issue 首个事实 @ 已映射的 assignee；未映射保留文字；关闭的新 Issue 不 @。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol", "erin")),
                                  make_issue(6, state="closed", assignees=users("bob"))]
        self.run_sync()
        first_facts = {content: mentions for reply, content, mentions in self.buzz.writes if reply}
        by_issue = {SYNC.parse_header(content)["issue"]: mentions for content, mentions in first_facts.items()}
        self.assertEqual(by_issue[5], (CAROL,))
        self.assertEqual(by_issue[6], ())
        self.assertIn("assignees carol,erin", "\n".join(first_facts))

    def test_added_assignee_mentions_only_the_new_person(self):
        """L1-GIS-210 之后新增 assignee 只 @ 新增的人；无变化不再 @。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"))]
        self.run_sync()
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol", "bob"),
                                             updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual((SYNC.parse_header(content)["change"], mentions), ("activity", (BOB,)))
        count = len(self.buzz.writes)
        self.run_sync()
        self.assertEqual(len(self.buzz.writes), count)

    def test_comment_mentions_assignees_author_and_named_members_but_not_the_commenter(self):
        """L1-GIS-211 Issue 评论 → 评论里点名的项目成员，再 assignee、作者；去掉评论人；最多 3 人。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("bob", "alice"))]
        self.run_sync()
        self.gitlab.issue_notes[5] = [note(91, "alice", "ping @carol please")]
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual(SYNC.parse_header(content)["change"], "activity")
        self.assertIn("[note:91]", content)
        self.assertEqual(mentions, (CAROL, BOB, DAVE))
        self.assertNotIn("@carol", content)
        self.assertIn("＠carol", content)

    def test_comment_named_members_need_a_project_member_commenter(self):
        """L1-GIS-212 评论人不是项目成员时，正文里的 @username 不算；结构化来源照常。"""
        self.gitlab.issue_list = [make_issue(5, assignees=users("bob"))]
        self.run_sync()
        self.gitlab.issue_notes[5] = [note(92, "outsider", "@carol @alice")]
        self.run_sync()
        self.assertEqual(self.last()[2], (BOB, DAVE))


class MrAttentionTest(SyncCase):
    def bound_mr(self, **overrides):
        self.gitlab.mr_list = [make_mr(**overrides)]
        self.run_sync()

    def test_merged_and_closed_mention_the_author(self):
        """L1-GIS-213 MR 合并 / 关闭 → @ MR 作者；merge_user 就是作者时不 @。"""
        self.bound_mr(reviewers=users("carol"))
        self.gitlab.mr_list = [make_mr(state="merged", reviewers=users("carol"),
                                       updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual((SYNC.parse_header(content)["state"], mentions), ("merged", (DAVE,)))

        self.tmp.cleanup()
        self.setUp()
        self.bound_mr(reviewers=users("carol"))
        self.gitlab.mr_list = [make_mr(state="closed", updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        self.assertEqual((SYNC.parse_header(self.last()[1])["state"], self.last()[2]), ("closed", (DAVE,)))

        self.tmp.cleanup()
        self.setUp()
        self.bound_mr()
        self.gitlab.mr_list = [make_mr(state="merged", merge_user={"username": "dave"},
                                       updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        self.assertEqual(self.last()[2], ())

    def test_reopen_never_tells_the_author_about_their_own_mr(self):
        """L1-GIS-225 MR 从 closed 重开：作者不因「状态变化」被 @（只走可评审的 reviewers/Maintainers 规则）。"""
        self.bound_mr(state="closed", reviewers=users("carol"))
        self.gitlab.mr_list = [make_mr(state="opened", reviewers=users("carol"),
                                       updated_at="2026-09-13T02:00:00Z")]
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual(SYNC.parse_header(content)["state"], "opened")
        self.assertNotIn(DAVE, mentions)

    def test_already_merged_on_first_sight_mentions_nobody(self):
        """L1-GIS-214 第一次同步到就已合并的 MR 不 @ 任何人（保持现有规则）。"""
        self.gitlab.mr_list = [make_mr(state="merged")]
        self.run_sync()
        self.assertTrue(all(mentions == () for _, _, mentions in self.buzz.writes))

    def test_comment_mentions_author_reviewers_and_named_members(self):
        """L1-GIS-215 MR 评论 → 评论点名，再作者、reviewers；去掉评论人；未映射的成员不出 p tag。"""
        self.bound_mr(reviewers=users("carol", "bob"))
        self.gitlab.mr_note_list.setdefault(31, []).append(note(77, "alice", "LGTM @erin @dave"))
        self.run_sync()
        reply, content, mentions = self.last()
        self.assertEqual(SYNC.parse_header(content)["change"], "activity")
        self.assertEqual(mentions, (DAVE, BOB, CAROL))
        self.assertIn("＠dave", content)

    def test_attention_budget_is_three_per_message_with_named_members_first(self):
        """L1-GIS-216 每条消息最多 3 人；评论点名优先于结构化来源。"""
        self.bound_mr(reviewers=users("carol", "bob"))
        self.gitlab.mr_note_list.setdefault(31, []).append(note(78, "erin", "@alice @bob @carol @dave"))
        self.run_sync()
        self.assertEqual(self.last()[2], (ALICE, BOB, CAROL))

    def test_approval_mentions_the_author_but_not_the_approver(self):
        """L1-GIS-217 MR 被批准 → @ 作者；批准人就是作者时不 @。"""
        self.bound_mr()
        self.gitlab.event_list = [{"id": 15, "action_name": "approved", "target_type": "MergeRequest",
                                   "target_iid": 31, "target_title": "恢复最近工作区",
                                   "author": {"username": "alice"}, "created_at": "2026-09-13T04:00:30.000Z"},
                                  {"id": 16, "action_name": "approved", "target_type": "MergeRequest",
                                   "target_iid": 31, "target_title": "恢复最近工作区",
                                   "author": {"username": "dave"}, "created_at": "2026-09-13T04:00:40.000Z"}]
        self.run_sync()
        self.assertEqual(self.mentions_containing("[events:event-15]"), [(DAVE,)])
        self.assertEqual(self.mentions_containing("[events:event-16]"), [()])

    def test_failed_mr_pipeline_mentions_only_the_trigger_user(self):
        """L1-GIS-218 MR 流水线失败 → @ 触发人；成功、取消不 @，也不读触发人。"""
        self.bound_mr()
        ref = "refs/merge-requests/31/head"
        self.gitlab.pipeline_list = [
            {"id": 54, "status": "failed", "ref": ref, "sha": SHA, "web_url": f"{WEB}/-/pipelines/54",
             "updated_at": "2026-09-13T02:00:00.000Z"},
            {"id": 55, "status": "success", "ref": ref, "sha": SHA, "web_url": f"{WEB}/-/pipelines/55",
             "updated_at": "2026-09-13T02:01:00.000Z"},
        ]
        self.gitlab.pipeline_users = {54: "carol", 55: "bob"}
        self.run_sync()
        self.assertEqual(self.mentions_containing("[events:pipeline-54-failed]"), [(CAROL,)])
        self.assertEqual(self.mentions_containing("[events:pipeline-55-success]"), [()])
        self.assertEqual(self.gitlab.calls, [("pipeline", 54)])


class TopLevelAttentionTest(SyncCase):
    def test_failed_default_branch_pipeline_and_failed_deployment_mention_their_user(self):
        """L1-GIS-219 默认分支流水线失败 → @ 触发人；部署失败 → @ 部署人。"""
        self.gitlab.pipeline_list = [{"id": 60, "status": "failed", "ref": "main", "sha": SHA,
                                      "web_url": f"{WEB}/-/pipelines/60", "updated_at": "2026-09-13T02:00:00.000Z"}]
        self.gitlab.pipeline_users = {60: "bob"}
        self.gitlab.deployment_list = [{"id": 9, "status": "failed", "ref": "main", "sha": SHA,
                                        "environment": {"name": "prod"}, "user": {"username": "carol"},
                                        "updated_at": "2026-09-13T02:00:00.000Z"}]
        self.run_sync()
        self.assertEqual(self.mentions_containing("[events:pipeline-60-failed]"), [(BOB,)])
        self.assertEqual(self.mentions_containing("[events:deployment-9-failed]"), [(CAROL,)])
        self.assertTrue(all("@" not in content.replace("[gitlab-notify:v1]", "")
                            for _, content, _ in self.buzz.writes))

    def test_unmapped_non_member_and_agent_users_are_never_tagged(self):
        """L1-GIS-220 触发人未映射、不在频道或是 Agent 时不出 p tag。"""
        self.buzz.roles[CAROL] = "bot"
        del self.buzz.roles[BOB]
        self.gitlab.deployment_list = [
            {"id": 9, "status": "failed", "ref": "main", "sha": SHA, "environment": {"name": "prod"},
             "user": {"username": "carol"}, "updated_at": "2026-09-13T02:00:00.000Z"},
            {"id": 10, "status": "failed", "ref": "main", "sha": SHA, "environment": {"name": "prod"},
             "user": {"username": "bob"}, "updated_at": "2026-09-13T02:00:01.000Z"},
            {"id": 11, "status": "failed", "ref": "main", "sha": SHA, "environment": {"name": "prod"},
             "user": {"username": "erin"}, "updated_at": "2026-09-13T02:00:02.000Z"},
        ]
        self.run_sync()
        self.assertTrue(self.buzz.writes)
        self.assertTrue(all(mentions == () for _, _, mentions in self.buzz.writes))

    def test_broadcast_notices_never_mention_anyone(self):
        """L1-GIS-221 tag / release / 即将过期的 access token 是广播类，不 @ 任何人。"""
        self.gitlab.event_list = [{"id": 20, "action_name": "pushed new", "target_type": None,
                                   "push_data": {"ref_type": "tag", "ref": "v1.0", "commit_count": 0,
                                                 "commit_to": SHA},
                                   "author": {"username": "dave"}, "created_at": "2026-09-13T04:00:30.000Z"}]
        self.gitlab.release_list = [{"tag_name": "v1.0", "name": "v1.0", "created_at": "2026-09-13T03:00:00Z",
                                     "author": {"username": "dave"}, "_links": {"self": f"{WEB}/-/releases/v1.0"}}]
        self.gitlab.token_list = [{"id": 5, "name": "ci", "expires_at": "2026-09-15", "active": True,
                                   "revoked": False, "user_id": 9}]
        self.run_sync()
        self.assertGreaterEqual(len(self.buzz.writes), 3)
        self.assertTrue(all(mentions == () for _, _, mentions in self.buzz.writes))

    def test_no_people_means_no_extra_gitlab_or_channel_reads(self):
        """L1-GIS-222 没配 people 时不为 @ 多读触发人或频道成员。"""
        self.config["people"] = {}
        self.gitlab.pipeline_list = [{"id": 60, "status": "failed", "ref": "main", "sha": SHA,
                                      "web_url": f"{WEB}/-/pipelines/60", "updated_at": "2026-09-13T02:00:00.000Z"}]
        self.gitlab.pipeline_users = {60: "bob"}
        self.run_sync()
        self.assertEqual(self.gitlab.calls, [])


class ChannelRoleAttentionTest(SyncCase):
    """skills#136: the sync's gate is the responsible-person helper's, so a Channel admin is a person there too.

    A reviewer or assignee who is the Channel's admin (gitsecops: jchen; 云服务成本: two admins; devt: sluo) used to
    be listed `unmapped: <name>(not_human_member)` and never tagged. guest and bot stay untagged.
    """

    def mr_root(self):
        return next((content, mentions) for reply, content, mentions in self.buzz.writes
                    if reply and "[mr:31]" in content)

    def unmapped_line(self, content):
        return next((line for line in content.split("\n") if line.startswith("unmapped:")), None)

    def test_channel_admin_reviewer_is_tagged_like_owner_and_member_reviewers(self):
        """L1-GIS-240 MR 变可评审：reviewer 是频道 admin 时和 owner／member 一样被 @，`unmapped:` 里没有他。"""
        self.buzz.roles.update({ALICE: "owner", BOB: "admin", CAROL: "member"})
        self.gitlab.mr_list = [make_mr(31, reviewers=users("alice", "bob", "carol"))]
        self.run_sync()
        content, mentions = self.mr_root()
        self.assertEqual(sorted(mentions), sorted([ALICE, BOB, CAROL]))
        self.assertIsNone(self.unmapped_line(content))

    def test_guest_and_bot_reviewers_stay_untagged_and_are_listed_unmapped(self):
        """L1-GIS-241 guest（受限角色）、bot（Agent）reviewer 仍不 @，列进 `unmapped:`（原因 not_human_member）；admin 不受牵连。"""
        self.buzz.roles.update({ALICE: "admin", BOB: "guest", CAROL: "bot"})
        self.gitlab.mr_list = [make_mr(31, reviewers=users("alice", "bob", "carol"))]
        self.run_sync()
        content, mentions = self.mr_root()
        self.assertEqual(mentions, (ALICE,))
        self.assertEqual(self.unmapped_line(content), "unmapped: bob(not_human_member),carol(not_human_member)")

    def test_channel_admin_assignee_and_named_admin_are_tagged(self):
        """L1-GIS-242 Issue 指派给频道 admin、评论里 @ 了另一位 admin：两处都出 p tag（同一道闸门）。"""
        self.buzz.roles.update({CAROL: "admin", BOB: "admin"})
        self.gitlab.issue_list = [make_issue(5, assignees=users("carol"))]
        self.run_sync()
        first = next(mentions for reply, content, mentions in self.buzz.writes if reply and "[issue:5]" in content)
        self.assertEqual(first, (CAROL,))
        self.gitlab.issue_notes[5] = [note(91, "alice", "ping @bob please")]
        self.run_sync()
        self.assertEqual(self.last()[2], (BOB, CAROL, DAVE))

    def test_admins_share_the_three_person_budget_per_message(self):
        """L1-GIS-243 四位都是 admin 的成员被评论点名：仍每条消息最多 3 人，点名顺序优先。"""
        self.buzz.roles.update({pubkey: "admin" for pubkey in PEOPLE.values()})
        self.gitlab.mr_list = [make_mr(31, reviewers=users("carol", "bob"))]
        self.run_sync()
        self.gitlab.mr_note_list.setdefault(31, []).append(note(78, "erin", "@alice @bob @carol @dave"))
        self.run_sync()
        self.assertEqual(self.last()[2], (ALICE, BOB, CAROL))

    def test_resolve_attention_targets_accepts_the_three_human_roles_only(self):
        """L1-GIS-244 同一道闸门的纯函数口径：owner／admin／member 出 pubkey，guest 写成 `name(not_human_member)`。"""
        roles = {ALICE: "owner", BOB: "admin", CAROL: "member", DAVE: "guest"}
        candidates = [{"username": name, "source": "gitlab.reviewers"} for name in ("alice", "bob", "carol", "dave")]
        mentions, unresolved = SYNC.resolve_attention_targets(candidates, PEOPLE, roles)
        self.assertEqual(mentions, [ALICE, BOB, CAROL])
        self.assertEqual(unresolved, ["dave(not_human_member)"])


if __name__ == "__main__":
    unittest.main()
