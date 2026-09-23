import hashlib
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"
WORKFLOWS = SKILL / "references" / "workflows"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

DESK = "d" * 64
OTHER = "e" * 64
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
OTHER_CHANNEL = "00000000-0000-4000-8000-0000000000c2"
ROOT_ID = "a" * 64
HEADER = (
    "[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened]"
    "[change:routing][project:481][issue:182]"
)


def issue(**overrides):
    base = {
        "iid": 182,
        "project_id": 481,
        "title": "账号切换后恢复最近工作区",
        "description": "",
        "state": "opened",
        "labels": ["type::feature", "status::ready"],
        "assignees": [],
        "milestone": None,
        "confidential": False,
        "web_url": "http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/182",
        "created_at": "2026-09-13T03:00:00Z",
        "updated_at": "2026-09-13T03:15:00Z",
    }
    base.update(overrides)
    return base


def event(content, pubkey=DESK, *, event_id=ROOT_ID, created_at=100, kind=9,
          channels=(CHANNEL,), reply_to=None, tags=()):
    all_tags = [["h", channel] for channel in channels]
    if reply_to:
        all_tags.append(["e", reply_to, "", "reply"])
    all_tags.extend([list(tag) for tag in tags])
    return {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": all_tags,
        "content": content,
    }


def message(type_="feature", status="ready", state="opened", change="routing",
            project=481, iid=182, title="标题", labels="-", assignees="-", milestone="-",
            description="-", extra=()):
    lines = [
        f"[gitlab-notify:v1][object:issue][type:{type_}][status:{status}][state:{state}]"
        f"[change:{change}][project:{project}][issue:{iid}]",
        f"title: {title}",
        f"url: http://127.0.0.1:8929/x/-/issues/{iid}",
        f"labels: {labels}",
        f"assignees: {assignees}",
        f"milestone: {milestone}",
        f"description: {description}",
        *extra,
    ]
    return "\n".join(lines)


class HeaderTest(unittest.TestCase):
    def test_render_issue_message(self):
        """L1-GIS-001 Issue header 固定顺序含 object；人读正文在前，机器 header 在末行。"""
        fact = SYNC.issue_fact(issue(
            labels=["type::feature", "status::ready", "team::app", "area::login"],
            assignees=[{"username": "bob"}, {"username": "alice"}],
            milestone={"title": "2026-Q4"},
            description="恢复最近工作区",
        ), 481)
        desc = hashlib.sha256("恢复最近工作区".encode()).hexdigest()[:12]
        lines = SYNC.render_message(fact, "routing").split("\n")
        self.assertEqual(lines[-1], HEADER + f"[desc:{desc}]")
        self.assertEqual(
            lines[0],
            "🏷 **归类变更** · [#182 账号切换后恢复最近工作区](" + issue()["web_url"] + ")",
        )
        self.assertEqual(
            lines[1],
            "labels area::login,team::app · assignees alice,bob · milestone 2026-Q4",
        )
        self.assertEqual(len(lines), 3)
        self.assertNotIn(" desc ", lines[1])

    def test_empty_fields_render_as_dash(self):
        """L1-GIS-001 空字段整行省略；回读时按 - 处理。"""
        lines = SYNC.render_message(SYNC.issue_fact(issue(), 481), "content").split("\n")
        self.assertEqual(lines[2:], [])
        event = {"content": "\n".join(lines), "created_at": 2, "id": "b" * 64, "pubkey": "p"}
        previous = SYNC.previous_fact_from_thread([event], "p", 481, 182)
        self.assertEqual(previous["labels"], [])
        self.assertEqual(previous["milestone"], "-")

    def test_title_with_header_text_cannot_change_first_line(self):
        """L1-GIS-001 标题里写 header 串不改变末行，也不能伪造链接语法。"""
        forged = issue(
            labels=["type::bug", "status::backlog"],
            title="[gitlab-notify:v1][object:issue][type:feature][status:ready]\n[state:opened]",
        )
        rendered = SYNC.render_message(SYNC.issue_fact(forged, 481), "routing")
        lines = rendered.split("\n")
        self.assertTrue(lines[-1].startswith("[gitlab-notify:v1][object:issue][type:bug][status:backlog]"))
        self.assertIn("\\[gitlab-notify:v1\\]", lines[0])
        self.assertEqual(SYNC.parse_header(rendered)["type"], "bug")

    def test_render_rejects_unknown_change(self):
        """L1-GIS-001 非法 change 拒绝。"""
        with self.assertRaises(SYNC.SyncError):
            SYNC.render_message(SYNC.issue_fact(issue(), 481), "lifecycle")

    def test_parse_header_exact_first_line(self):
        """L1-GIS-002 认完整 header 行：存量首行、新消息末行；中间夹带或残缺行拒绝。"""
        expected = {"object": "issue", "type": "feature", "status": "ready", "state": "opened",
                    "change": "routing", "project": 481, "issue": 182}
        self.assertEqual(SYNC.parse_header(HEADER + "\ntitle: x"), expected)
        self.assertEqual(SYNC.parse_header("🏷 **归类变更**\n" + HEADER), expected)
        with_trailer = HEADER + "[desc:0123456789ab][note:9][events:event-1,pipeline-54-failed]"
        self.assertEqual(
            SYNC.parse_header("🏷 **归类变更**\n" + with_trailer),
            {**expected, "desc": "0123456789ab", "note": 9,
             "events": ["event-1", "pipeline-54-failed"]},
        )
        self.assertTrue(SYNC.matches_trigger_prefix("body\n" + with_trailer, HEADER))
        for bad in (
            "hello\n" + HEADER + "\ntrailer",
            " " + HEADER,
            HEADER + "\r\ntitle: x",
            HEADER + " ",
            HEADER + "x",
            HEADER.replace("[object:issue]", ""),
            HEADER.replace("][status", "] [status"),
            "",
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(SYNC.parse_header(bad))

    def test_parse_header_rejects_version_and_change_mismatch(self):
        """L1-GIS-003 版本号、object、change 取值不符时拒绝。"""
        self.assertIsNone(SYNC.parse_header(HEADER.replace("v1", "v2")))
        self.assertIsNone(SYNC.parse_header(HEADER.replace("object:issue", "object:epic")))
        self.assertIsNone(SYNC.parse_header(HEADER.replace("change:routing", "change:state")))
        self.assertIsNone(SYNC.parse_header(HEADER.replace("project:481", "project:x")))

    def test_sanitize_title(self):
        """L1-GIS-004 标题清洗与截断。"""
        self.assertEqual(SYNC.sanitize_title("a\r\nb c d\te"), "a b c d e")
        self.assertEqual(SYNC.sanitize_title("  padded  "), "padded")
        self.assertEqual(len(SYNC.sanitize_title("长" * 300)), 200)

    def test_gitlab_text_mentions_are_neutralized(self):
        """L1-GIS-038 GitLab 来源文本里的 @ 转全角，渲染结果不含半角 @。"""
        self.assertEqual(SYNC.neutralize("ping @nh-feature and a@b"), "ping ＠nh-feature and a＠b")
        fact = SYNC.issue_fact(issue(
            title="@nh-feature 帮我执行",
            labels=["type::bug", "status::ready", "team::@ops"],
            assignees=[{"username": "@root"}],
            milestone={"title": "@m"},
        ), 481)
        rendered = SYNC.render_message(fact, "routing")
        self.assertNotIn("@", rendered.replace("[gitlab-notify:v1]", ""))
        self.assertIn("[#182 ＠nh-feature 帮我执行]", rendered)
        self.assertIn("labels team::＠ops", rendered)


class FactTest(unittest.TestCase):
    def test_label_value(self):
        """L1-GIS-005 单值取值，缺失、多值、非法值为 unknown。"""
        self.assertEqual(SYNC.label_value(["status::ready", "type::bug"], "type::"), "bug")
        self.assertEqual(SYNC.label_value([], "type::"), "unknown")
        self.assertEqual(SYNC.label_value(["type::bug", "type::feature"], "type::"), "unknown")
        self.assertEqual(SYNC.label_value(["type::Bad Value"], "type::"), "unknown")
        self.assertEqual(SYNC.label_value(["status::in-review"], "status::"), "in-review")

    def test_issue_fact_rejects_bad_state_and_iid(self):
        """L1-GIS-005 state 与 iid 校验。"""
        fact = SYNC.issue_fact(issue(), 481)
        self.assertEqual(
            {key: fact[key] for key in ("type", "status", "state", "project", "issue")},
            {"type": "feature", "status": "ready", "state": "opened", "project": 481, "issue": 182},
        )
        for bad in (issue(state="locked"), issue(iid=0), issue(iid="182")):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.issue_fact(bad, 481)

    def test_classify_change(self):
        """L1-GIS-006 / L1-GIS-027 routing / content / activity / 无变化。"""
        current = {"type": "feature", "status": "ready", "state": "opened", "title": "t",
                   "labels": ["team::app"], "assignees": ["alice"], "milestone": "-", "description": "-"}
        self.assertEqual(SYNC.classify_change(None, current), "routing")
        for key, value in (("type", "bug"), ("status", "backlog"), ("state", "closed")):
            with self.subTest(key=key):
                self.assertEqual(SYNC.classify_change({**current, key: value}, current), "routing")
        self.assertEqual(SYNC.classify_change({**current, "title": "old"}, current), "content")
        for key, value in (("labels", []), ("assignees", ["bob"]), ("milestone", "M1"), ("description", "abc")):
            with self.subTest(key=key):
                self.assertEqual(SYNC.classify_change({**current, key: value}, current), "activity")
        self.assertIsNone(SYNC.classify_change(dict(current), current))

    def test_previous_fact_from_thread(self):
        """L1-GIS-007 只认 Desk 作者、合法 Issue header、同 Issue，取最新，并还原正文事实。"""
        events = [
            event(message(status="backlog", title="旧"), created_at=100, event_id="1" * 64),
            event(message(status="ready", title="新", labels="team::app", assignees="alice"),
                  created_at=200, event_id="2" * 64, reply_to=ROOT_ID),
            event(message(status="done"), OTHER, created_at=300, event_id="3" * 64, reply_to=ROOT_ID),
            event("普通讨论", created_at=400, event_id="4" * 64, reply_to=ROOT_ID),
            event(message(status="blocked", iid=183), created_at=500, event_id="5" * 64),
        ]
        self.assertEqual(
            SYNC.previous_fact_from_thread(events, DESK, 481, 182),
            {"type": "feature", "status": "ready", "state": "opened", "title": "新",
             "labels": ["team::app"], "assignees": ["alice"], "milestone": "-", "description": "-"},
        )
        self.assertIsNone(SYNC.previous_fact_from_thread([], DESK, 481, 182))


class CommentTest(unittest.TestCase):
    def note(self, note_id, author_id=3, username="alice", body="看看", system=False,
             created_at="2026-09-13T04:00:00Z"):
        return {"id": note_id, "author": {"id": author_id, "username": username}, "body": body,
                "system": system, "created_at": created_at}

    def test_pending_comments(self):
        """L1-GIS-027 只取人写的、since 之后、尚未在 Thread 里出现 note 行的评论。"""
        notes = [
            self.note(1, system=True),
            self.note(2, author_id=7, username="buzz-sync-bot"),
            self.note(3, created_at="2026-09-12T23:00:00Z"),
            self.note(5),
            self.note(4),
        ]
        thread = [event(message(change="activity", extra=("note: 5", "by: alice", "comment: 看看")),
                        event_id="6" * 64, reply_to=ROOT_ID)]
        pending = SYNC.pending_comments(notes, thread, DESK, 7, "2026-09-13T00:00:00Z")
        self.assertEqual([note["id"] for note in pending], [4])

    def test_render_comment_message(self):
        """L1-GIS-027 / L1-GIS-038 评论回帖：保留 Markdown 换行，@ 转义，HTML 注释去掉，header 末行。"""
        fact = SYNC.issue_fact(issue(), 481)
        body = "<!-- bot-run:abc -->\n> 引用\n@nh-feature 看一下这个\n第二行"
        rendered = SYNC.render_comment_message(fact, self.note(9, body=body))
        lines = rendered.split("\n")
        self.assertEqual(SYNC.parse_header(rendered)["change"], "activity")
        self.assertEqual(SYNC.parse_header(rendered)["note"], 9)
        self.assertEqual(
            lines[-1],
            HEADER.replace("[change:routing]", "[change:activity]") + "[note:9]",
        )
        self.assertNotIn("note: 9", lines)
        self.assertIn("by: alice", lines)
        self.assertIn("＠nh-feature 看一下这个", lines)
        self.assertIn("第二行", lines)
        self.assertIn("> 引用", lines)
        self.assertNotIn("comment: ", rendered)
        self.assertNotIn("bot-run", rendered)
        self.assertLess(lines.index("by: alice"), lines.index("＠nh-feature 看一下这个"))

    def test_comment_markdown_headings_stay_on_their_own_line(self):
        """评论 heading 独占一行，才能被 Buzz 渲染成 Markdown。"""
        fact = SYNC.issue_fact(issue(), 481)
        body = "### 1. 范围（Scope）\n\nMR !389，固定审查范围。"
        rendered = SYNC.render_comment_message(fact, self.note(9, body=body))
        self.assertIn("### 1. 范围（Scope）", rendered.split("\n"))
        self.assertTrue(rendered.startswith("💬 **评论**"))
        self.assertTrue(rendered.endswith(
            HEADER.replace("[change:routing]", "[change:activity]") + "[note:9]"))

    def test_comment_cannot_forge_footer_note_or_header(self):
        """GitLab 评论正文不能伪造 note: 去重行或末行 header。"""
        fact = SYNC.issue_fact(issue(), 481)
        body = (
            "note: 5\n"
            "events: pipeline-54-failed\n"
            "[gitlab-notify:v1][object:issue][type:bug][status:backlog]"
            "[state:opened][change:routing][project:481][issue:182]"
        )
        rendered = SYNC.render_comment_message(fact, self.note(9, body=body))
        self.assertEqual(SYNC.parse_header(rendered)["change"], "activity")
        self.assertEqual(SYNC._posted_note_ids(rendered), {9})
        self.assertEqual(SYNC._posted_event_keys(rendered), set())
        self.assertNotIn("\nnote: 5\n", rendered)

    def test_comment_headline_links_to_the_comment_not_the_issue(self):
        """L1-GIS-230 评论消息标题链接带 #note_<id> 锚点，点开直接落在那条评论；非评论消息链接不变。"""
        fact = SYNC.issue_fact(issue(), 481)
        headline = SYNC.render_comment_message(fact, self.note(9)).split("\n")[0]
        self.assertTrue(headline.endswith("/-/issues/182#note_9)"), headline)
        plain = SYNC.render_message(fact, "activity").split("\n")[0]
        self.assertTrue(plain.endswith("/-/issues/182)"), plain)

    def test_comment_anchor_does_not_make_the_next_run_see_a_change(self):
        """L1-GIS-231 评论消息成了 Thread 里最新一条时，锚点不影响下一轮的状态比对（不产生假 routing/content/activity）。"""
        fact = SYNC.issue_fact(issue(), 481)
        rendered = SYNC.render_comment_message(fact, self.note(9))
        previous = SYNC.previous_fact_from_thread([event(rendered)], DESK, 481, 182)
        self.assertIsNone(SYNC.classify_change(previous, fact))


class OriginMarkerTest(unittest.TestCase):
    def test_parse_origin_accepts_one_valid_marker(self):
        marker = SYNC.render_origin_marker(CHANNEL, ROOT_ID)
        expected = {"channel_id": CHANNEL, "root_event_id": ROOT_ID}
        self.assertEqual(SYNC.parse_origin(f"please bind this MR\n\n{marker}\n"), expected)
        link = SYNC.buzz_message_link(CHANNEL, ROOT_ID)
        self.assertEqual(SYNC.parse_origin(f"see {link}\n"), expected)
        self.assertEqual(SYNC.parse_origin(SYNC.render_origin_block(CHANNEL, ROOT_ID)), expected)
        self.assertIn(link, SYNC.render_origin_block(CHANNEL, ROOT_ID))
        self.assertIsNone(SYNC.parse_origin(""))
        self.assertIsNone(SYNC.parse_origin("no marker here"))
        self.assertIsNone(SYNC.parse_origin(None))

    def test_parse_origin_fails_closed_on_malformed(self):
        good = SYNC.render_origin_marker(CHANNEL, ROOT_ID)
        self.assertEqual(
            SYNC.parse_origins(good + "\n" + good),
            [{"channel_id": CHANNEL, "root_event_id": ROOT_ID}],
        )
        with self.assertRaises(SYNC.SyncError):
            SYNC.parse_origins("<!-- gitlab-buzz-origin:v1 {not-json} -->")
        with self.assertRaises(SYNC.SyncError):
            SYNC.parse_origins('<!-- gitlab-buzz-origin:v1 {"channel_id":"x","root_event_id":"' + ROOT_ID + '"} -->')
        with self.assertRaises(SYNC.SyncError):
            SYNC.parse_origins(
                '<!-- gitlab-buzz-origin:v1 {"channel_id":"' + CHANNEL
                + '","root_event_id":"' + ROOT_ID + '","extra":1} -->'
            )

    def test_parse_origins_returns_every_unique_target(self):
        other = "b" * 64
        found = SYNC.parse_origins(
            SYNC.buzz_message_link(CHANNEL, ROOT_ID) + "\n"
            + SYNC.buzz_message_link(CHANNEL, other)
        )
        self.assertEqual(
            found,
            [
                {"channel_id": CHANNEL, "root_event_id": ROOT_ID},
                {"channel_id": CHANNEL, "root_event_id": other},
            ],
        )
        self.assertEqual(SYNC.parse_origin(SYNC.buzz_message_link(CHANNEL, ROOT_ID)), found[0])

    def test_origin_note_bodies_skip_system_bot_and_binding(self):
        notes = [
            {"id": 1, "author": {"id": 8}, "body": "see " + SYNC.buzz_message_link(CHANNEL, ROOT_ID),
             "system": False},
            {"id": 2, "author": {"id": 8}, "body": "secret", "system": True},
            {"id": 3, "author": {"id": 8}, "body": "secret", "internal": True},
            {"id": 4, "author": {"id": 8}, "body": "secret", "confidential": True},
            {"id": 5, "author": {"id": 7}, "body": "bot " + SYNC.buzz_message_link(CHANNEL, ROOT_ID)},
            {"id": 6, "author": {"id": 8},
             "body": "<!-- gitlab-buzz-binding:v1 {} -->\n" + SYNC.buzz_message_link(CHANNEL, ROOT_ID)},
        ]
        self.assertEqual(
            SYNC.origin_note_bodies(notes, 7),
            ["see " + SYNC.buzz_message_link(CHANNEL, ROOT_ID)],
        )

    def test_origin_canonical_root_requires_desk_top_level_plaque_or_fact(self):
        plaque = event("📋 **#14 weekly**\nengineering/skills\nhttp://x/-/issues/14", event_id=ROOT_ID)
        self.assertEqual(SYNC.origin_canonical_root(plaque, DESK, CHANNEL), ROOT_ID)
        fact = event("🏷 **归类变更**\n" + HEADER, event_id=ROOT_ID)
        self.assertEqual(SYNC.origin_canonical_root(fact, DESK, CHANNEL), ROOT_ID)
        self.assertIsNone(SYNC.origin_canonical_root(event("hello", event_id=ROOT_ID), DESK, CHANNEL))
        self.assertIsNone(SYNC.origin_canonical_root(
            event("📋 **#14 weekly**\nhttp://x/-/issues/14", pubkey=OTHER, event_id=ROOT_ID), DESK, CHANNEL))
        self.assertIsNone(SYNC.origin_canonical_root(
            event("📋 **#14 weekly**\nhttp://x/-/issues/14", event_id=ROOT_ID, reply_to="b" * 64),
            DESK, CHANNEL))
        self.assertIsNone(SYNC.origin_canonical_root(
            event("📋 **#14 weekly**\nhttp://x/-/issues/14", event_id=ROOT_ID, channels=(OTHER_CHANNEL,)),
            DESK, CHANNEL))

    def test_origin_named_root_id_walks_reply_to_thread_root(self):
        reply = event("follow-up", event_id="c" * 64, reply_to=ROOT_ID)
        reply["tags"] = [["h", CHANNEL], ["e", ROOT_ID, "", "root"], ["e", ROOT_ID, "", "reply"]]
        self.assertEqual(SYNC.origin_named_root_id(reply, "c" * 64), ROOT_ID)
        self.assertEqual(SYNC.origin_named_root_id(event("top", event_id=ROOT_ID), ROOT_ID), ROOT_ID)


class BindingTest(unittest.TestCase):
    def binding(self, **overrides):
        value = {"project_id": 481, "object": "issue", "iid": 182, "channel_id": CHANNEL,
                 "root_event_id": ROOT_ID}
        value.update(overrides)
        return value

    def note(self, body, author_id=7, note_id=1):
        return {"id": note_id, "author": {"id": author_id}, "body": body}

    def test_render_and_parse_binding(self):
        """L1-GIS-008 binding note 渲染与解析（object + iid）。"""
        link = f"buzz://message?channel={CHANNEL}&id={ROOT_ID}&thread={ROOT_ID}"
        body = SYNC.render_binding_note(self.binding(), link)
        first = body.split("\n", 1)[0]
        self.assertTrue(first.startswith("<!-- gitlab-buzz-binding:v1 {"))
        self.assertTrue(first.endswith(" -->"))
        self.assertIn(link, body)
        self.assertEqual(SYNC.parse_binding([self.note(body)], 7, 481, "issue", 182, CHANNEL), ROOT_ID)
        mr_body = SYNC.render_binding_note(self.binding(object="mr", iid=31), link)
        self.assertEqual(SYNC.parse_binding([self.note(mr_body)], 7, 481, "mr", 31, CHANNEL), ROOT_ID)

    def test_parse_binding_trust_and_conflicts(self):
        """L1-GIS-008 非 bot 忽略；同 root 重复可接受；冲突与畸形报错；他频道、指向别的对象的 binding（克隆/移动）忽略。"""
        body = SYNC.render_binding_note(self.binding(), "buzz://x")
        other_root = SYNC.render_binding_note(self.binding(root_event_id="b" * 64), "buzz://y")
        other_issue = SYNC.render_binding_note(self.binding(iid=183), "buzz://z")
        other_object = SYNC.render_binding_note(self.binding(object="mr"), "buzz://m")
        other_channel = SYNC.render_binding_note(self.binding(channel_id=OTHER_CHANNEL), "buzz://w")
        args = (7, 481, "issue", 182, CHANNEL)
        self.assertIsNone(SYNC.parse_binding([self.note(body, author_id=99)], *args))
        self.assertEqual(SYNC.parse_binding([self.note(body), self.note(body, note_id=2)], *args), ROOT_ID)
        self.assertIsNone(SYNC.parse_binding([self.note(other_channel)], *args))
        self.assertIsNone(SYNC.parse_binding([self.note(other_issue)], *args))
        self.assertIsNone(SYNC.parse_binding([self.note(other_object)], *args))
        self.assertIsNone(SYNC.parse_binding([self.note("说明\n" + body)], *args))
        for notes in (
            [self.note(body), self.note(other_root, note_id=2)],
            [self.note("<!-- gitlab-buzz-binding:v1 {not json} -->")],
        ):
            with self.subTest(notes=notes), self.assertRaises(SYNC.SyncError):
                SYNC.parse_binding(notes, *args)
        with self.assertRaises(SYNC.SyncError):
            SYNC.render_binding_note(self.binding(object="epic"), "buzz://x")

    def test_select_root(self):
        """L1-GIS-009 root 选择与多候选报错。"""
        root = event(message(), event_id=ROOT_ID)
        noise = [
            event(message(), event_id="1" * 64, reply_to=ROOT_ID),
            event(message(), OTHER, event_id="2" * 64),
            event(message(), event_id="3" * 64, channels=(OTHER_CHANNEL,)),
            event(message(), event_id="4" * 64, channels=(CHANNEL, OTHER_CHANNEL)),
            event(message(iid=183), event_id="5" * 64),
        ]
        self.assertEqual(SYNC.select_root([root, *noise], DESK, CHANNEL, 481, "issue", 182), ROOT_ID)
        self.assertIsNone(SYNC.select_root(noise, DESK, CHANNEL, 481, "issue", 182))
        self.assertIsNone(SYNC.select_root([root], DESK, CHANNEL, 481, "mr", 182))
        with self.assertRaises(SYNC.SyncError):
            SYNC.select_root([root, event(message(), event_id="6" * 64)], DESK, CHANNEL, 481, "issue", 182)


class SelectionTest(unittest.TestCase):
    config = {
        "include_confidential": False,
        "exclude": [{"assignee_username": "sre-investigator"}, {"label": "skip::sync"}],
    }

    def test_confidential_and_exclude(self):
        """L1-GIS-010 confidential 与 exclude。"""
        self.assertEqual(SYNC.issue_selected(issue(), self.config), (True, None))
        self.assertEqual(SYNC.issue_selected(issue(confidential=True), self.config), (False, "confidential"))
        self.assertEqual(
            SYNC.issue_selected(issue(confidential=True), {**self.config, "include_confidential": True}),
            (True, None),
        )
        self.assertEqual(
            SYNC.issue_selected(issue(assignees=[{"username": "sre-investigator"}]), self.config),
            (False, "excluded"),
        )
        self.assertEqual(
            SYNC.issue_selected(issue(labels=["type::bug", "skip::sync"]), self.config),
            (False, "excluded"),
        )
        for bad in (issue(confidential=None), {k: v for k, v in issue().items() if k != "confidential"}):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.issue_selected(bad, self.config)

    def test_since_boundary(self):
        """L1-GIS-011 since 之前创建且未绑定的不回灌。"""
        since = "2026-09-13T03:10:00Z"
        self.assertTrue(SYNC.is_backfill(issue(created_at="2026-09-13T03:00:00Z"), since, has_binding=False))
        self.assertFalse(SYNC.is_backfill(issue(created_at="2026-09-13T03:00:00Z"), since, has_binding=True))
        self.assertFalse(SYNC.is_backfill(issue(created_at="2026-09-13T03:10:00Z"), since, has_binding=False))
        with self.assertRaises(SYNC.SyncError):
            SYNC.is_backfill(issue(created_at="yesterday"), since, has_binding=False)


class ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        release = Path(cls.tmp.name) / "buzz-0.5.23" / "usr" / "bin"
        release.mkdir(parents=True)
        cls.cli = release / "buzz"
        cls.cli.write_bytes(b"\x7fELFtest fixture")
        cls.cli.chmod(0o700)
        cls.cli_sha = hashlib.sha256(cls.cli.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def config(self):
        return {
            "channel_id": CHANNEL,
            "publisher_pubkey": DESK,
            "since": "2026-09-13T00:00:00Z",
            "include_confidential": False,
            "exclude": [{"assignee_username": "sre-investigator"}],
            "diff": {"enabled": False, "private": False},
            "gitlab": {
                "base_url": "http://127.0.0.1:8929",
                "token_env": "NH_DESK_GITLAB_TOKEN",
                "bot_user_id": 7,
                "bot_username": "buzz-sync-bot",
                "projects": [481],
            },
            "buzz": {"cli_path": str(self.cli), "cli_sha256": self.cli_sha},
        }

    def test_valid_config(self):
        """L1-GIS-012 合法配置通过。"""
        SYNC.validate_config(self.config())
        config = self.config()
        config["gitlab"]["base_url"] = "https://gitlab.example.test"
        SYNC.validate_config(config)

    def test_invalid_config(self):
        """L1-GIS-012 非法配置逐项拒绝。"""
        link = Path(self.tmp.name) / "buzz-0.5.23" / "usr" / "bin" / "buzz-link"
        if not link.exists():
            link.symlink_to(self.cli)
        mutations = {
            "gitlab path": lambda c: c["gitlab"].update(base_url="https://gitlab.example.test/api"),
            "gitlab userinfo": lambda c: c["gitlab"].update(base_url="https://u:p@gitlab.example.test"),
            "gitlab plain http": lambda c: c["gitlab"].update(base_url="http://gitlab.example.test"),
            "token env buzz": lambda c: c["gitlab"].update(token_env="BUZZ_PRIVATE_KEY"),
            "token env lower": lambda c: c["gitlab"].update(token_env="gitlab_token"),
            "bot id": lambda c: c["gitlab"].update(bot_user_id="7"),
            "projects empty": lambda c: c["gitlab"].update(projects=[]),
            "channel": lambda c: c.update(channel_id="naturehood"),
            "publisher pubkey": lambda c: c.update(publisher_pubkey="npub1xyz"),
            "audience retired": lambda c: c.update(audience={"allowed_pubkeys": [DESK]}),
            "since": lambda c: c.update(since="2026-09-13"),
            "confidential type": lambda c: c.update(include_confidential="no"),
            "cli relative": lambda c: c["buzz"].update(cli_path="buzz"),
            "cli symlink": lambda c: c["buzz"].update(cli_path=str(link)),
            "cli sha": lambda c: c["buzz"].update(cli_sha256="0" * 64),
        }
        for name, mutate in mutations.items():
            config = self.config()
            mutate(config)
            with self.subTest(name=name), self.assertRaises(SYNC.SyncError):
                SYNC.validate_config(config)

    def test_relay_url(self):
        """L1-GIS-012 relay URL 只允许精确 origin，ws/http 仅限回环。"""
        for good in ("wss://buzz.example.test", "ws://127.0.0.1:3000", "http://localhost:3000"):
            with self.subTest(good=good):
                self.assertEqual(SYNC.validate_relay_url(good), good)
        for bad in ("ws://buzz.example.test", "wss://u@buzz.example.test",
                    "wss://buzz.example.test/path", "wss://buzz.example.test?x=1", "ftp://127.0.0.1"):
            with self.subTest(bad=bad), self.assertRaises(SYNC.SyncError):
                SYNC.validate_relay_url(bad)

    def test_send_args_never_mention_by_default(self):
        """L1-GIS-013 / L1-GIS-038 root 与回帖参数默认不带 --mention。"""
        cli = str(self.cli)
        self.assertEqual(
            SYNC.build_send_args(cli, CHANNEL),
            [cli, "messages", "send", "--channel", CHANNEL, "--content", "-"],
        )
        reply = SYNC.build_send_args(cli, CHANNEL, reply_to=ROOT_ID)
        self.assertEqual(reply[-2:], ["--reply-to", ROOT_ID])
        self.assertNotIn("--mention", reply)
        with self.assertRaises(SYNC.SyncError):
            SYNC.build_send_args(cli, CHANNEL, reply_to="not-hex")

    def test_child_env_allowlist(self):
        """L1-GIS-013 子进程 env 只含白名单，不含 GitLab token。"""
        parent = {
            "BUZZ_RELAY_URL": "ws://127.0.0.1:3000",
            "BUZZ_PRIVATE_KEY": "test-key",
            "HOME": "/home/test",
            "PATH": "/usr/bin",
            "NH_DESK_GITLAB_TOKEN": "glpat-test",
            "GITLAB_TOKEN": "glpat-test",
        }
        env = SYNC.child_env(parent, token_env="NH_DESK_GITLAB_TOKEN")
        self.assertEqual(env["BUZZ_PRIVATE_KEY"], "test-key")
        self.assertNotIn("NH_DESK_GITLAB_TOKEN", env)
        self.assertNotIn("GITLAB_TOKEN", env)
        self.assertTrue(set(env) <= SYNC.CHILD_ENV_KEYS)
        with self.assertRaises(SYNC.SyncError):
            SYNC.child_env({k: v for k, v in parent.items() if k != "BUZZ_PRIVATE_KEY"},
                           token_env="NH_DESK_GITLAB_TOKEN")

    def test_child_env_passes_egress_proxy_variables(self):
        """L1-GIS-013 代理变量透传给 CLI 子进程，GitLab token 与无关变量仍被剔除。"""
        proxies = {
            "HTTPS_PROXY": "http://proxy.internal:3128",
            "https_proxy": "http://proxy.internal:3128",
            "ALL_PROXY": "socks5://proxy.internal:1080",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
        parent = {
            "BUZZ_RELAY_URL": "wss://relay.example",
            "BUZZ_PRIVATE_KEY": "test-key",
            "NH_DESK_GITLAB_TOKEN": "glpat-test",
            "UNRELATED_SECRET": "x",
            **proxies,
        }
        env = SYNC.child_env(parent, token_env="NH_DESK_GITLAB_TOKEN")
        for key, value in proxies.items():
            self.assertEqual(env[key], value)
        self.assertNotIn("NH_DESK_GITLAB_TOKEN", env)
        self.assertNotIn("UNRELATED_SECRET", env)

    def test_publisher_private_key_is_derived_and_checked_before_use(self):
        """L1-GIS-072 The dedicated publisher key must match config before any external adapter is built."""
        secret = "0" * 63 + "3"
        expected = "f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9"
        self.assertEqual(SYNC.publisher_pubkey_from_private_key(secret), expected)
        self.assertEqual(SYNC.validate_publisher_identity({"BUZZ_PRIVATE_KEY": secret}, expected), expected)
        with self.assertRaises(SYNC.SyncError):
            SYNC.validate_publisher_identity({"BUZZ_PRIVATE_KEY": secret}, "1" * 64)

    def test_lock_path_per_channel(self):
        """L1-GIS-016 锁路径由 channel 与项目集合决定，不同频道不同。"""
        state = Path(self.tmp.name) / "state"
        first = SYNC.lock_path(self.config(), state)
        self.assertEqual(first, SYNC.lock_path(self.config(), state))
        other = self.config()
        other["channel_id"] = OTHER_CHANNEL
        self.assertNotEqual(first, SYNC.lock_path(other, state))
        self.assertEqual(first.parent, state)


class DiffTest(unittest.TestCase):
    def test_split_diff_by_file(self):
        """L1-GIS-014 按文件切分，超限跳过。"""
        a = "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n-x\n+y\n"
        b = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-1\n+2\n"
        chunks, skipped = SYNC.split_diff_by_file(a + b, limit=61440)
        self.assertEqual([chunk["file"] for chunk in chunks], ["src/a.ts", "b.py"])
        self.assertEqual(chunks[0]["diff"], a)
        self.assertEqual(skipped, [])
        big = "diff --git a/big.txt b/big.txt\n--- a/big.txt\n+++ b/big.txt\n@@ -1 +1 @@\n+" + "x" * 100 + "\n"
        chunks, skipped = SYNC.split_diff_by_file(a + big, limit=len(a.encode()) + 10)
        self.assertEqual([chunk["file"] for chunk in chunks], ["src/a.ts"])
        self.assertEqual(skipped, ["big.txt"])

    def test_diff_already_posted_and_allowed(self):
        """L1-GIS-014 同 commit+file 已发判定；private 开关。"""
        sha = "9b72ae1" + "0" * 33
        posted = event("diff", kind=40008, reply_to=ROOT_ID,
                       tags=(("commit", sha), ("file", "src/a.ts"), ("repo", "http://x")))
        self.assertTrue(SYNC.diff_already_posted([posted], DESK, sha, "src/a.ts"))
        self.assertFalse(SYNC.diff_already_posted([posted], DESK, sha, "b.py"))
        self.assertFalse(SYNC.diff_already_posted([posted], OTHER, sha, "src/a.ts"))
        self.assertFalse(SYNC.diff_allowed("public", {"enabled": False, "private": True}))
        self.assertTrue(SYNC.diff_allowed("public", {"enabled": True, "private": False}))
        self.assertFalse(SYNC.diff_allowed("private", {"enabled": True, "private": False}))
        self.assertFalse(SYNC.diff_allowed("internal", {"enabled": True, "private": False}))
        self.assertTrue(SYNC.diff_allowed("private", {"enabled": True, "private": True}))


class WorkflowTemplateTest(unittest.TestCase):
    ALLOWED = {
        "trigger_text", "trigger_author", "trigger_channel_id", "trigger_message_id",
        "trigger_timestamp", "trigger_is_reply", "str_contains", "str_starts_with",
        "str_ends_with", "str_len",
    }

    def read(self, name):
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_schedule_template(self):
        """L1-GIS-015 ADR-0008: the public schedule tick is retired; no schedule file or reference template remains."""
        shipped = [path for path in SKILL.rglob("*.y*ml") if "schedule" in path.name.lower()]
        self.assertEqual([], shipped)
        reference = (SKILL / "references" / "gitlab-buzz-sync.md").read_text(encoding="utf-8")
        self.assertNotIn("<!-- template:schedule-desk -->", reference)
        self.assertNotRegex(reference, r"(?m)^\s*on:\s*schedule\s*$")

    def test_no_native_route_workflow_is_shipped(self):
        """L1-GIS-015 No route Workflow is shipped; Role routing lives in Canvas plus the local gate."""
        self.assertFalse((WORKFLOWS / "route-type-status.yaml").exists())
        self.assertFalse((WORKFLOWS / "route-mr-ready.yaml").exists())
        canvas = (SKILL / "references" / "gitlab-buzz-routing-canvas.md").read_text(encoding="utf-8")
        self.assertIn("<!-- gitlab-buzz-routing:v1 -->", canvas)
        self.assertIn("[change:routing]", canvas)
        self.assertNotIn("reply_in_thread", canvas)


if __name__ == "__main__":
    unittest.main()
