"""A human's top-level Buzz message as the origin of an Issue, Task, MR or milestone (ADR-0014).

The origin marker `<!-- gitlab-buzz-origin:v1 {...} -->` may name any top-level kind-9 message of the sync Channel
(a person, the Feishu mirror identity, another Agent), not only a Desk plaque or fact. The object then binds to that
message and its facts are replies in that thread; no plaque is created. A bare `buzz://message?…` link to a human
message stays a plain reference (ADR-0011). A well-formed marker whose root cannot be used falls back to a plaque of
the object's own plus one `origin_fallbacks` entry; a malformed marker still stalls the object, and a relay or
authentication failure reading the root still fails the round.

The fake `buzz messages thread` follows the real CLI (0.5.23): the whole thread with the root first, whichever event
of it is named; exit 1 for an unknown event or one of another Channel; events carry `h`, `e`, `p` and `auth` tags.
"""

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

try:
    from .test_gitlab_buzz_sync_hardening import (  # noqa: F401
        AGENT, BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, FakeGitLab, SyncCase, config, make_issue, make_mr,
    )
except ImportError:  # imported as a top-level module
    from test_gitlab_buzz_sync_hardening import (  # noqa: F401
        AGENT, BOT_ID, CHANNEL, DESK, PID, SYNC, WEB, FakeBuzz, FakeGitLab, SyncCase, config, make_issue, make_mr,
    )

MIRROR = "c8" * 32   # the Feishu mirror identity that relays people's messages
PERSON = "1a" * 32
OWNER = "8a" * 32    # the `auth` tag of every real event names the owner key
SIG = "9b" * 32
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "buzz_thread" / "human_root_thread.json"


def marker(root, channel=CHANNEL):
    return f'<!-- gitlab-buzz-origin:v1 {{"channel_id":"{channel}","root_event_id":"{root}"}} -->'


def bare_link(root, channel=CHANNEL):
    return f"buzz://message?channel={channel}&id={root}&thread={root}"


class RealShapeBuzz(FakeBuzz):
    """FakeBuzz whose `thread` and events have the shape of the real Buzz CLI output."""

    def __init__(self):
        super().__init__()
        self.thread_calls = []
        self._clock = 2000

    def _by_id(self):
        return {event["id"]: event for event in self.events}

    def root_of(self, event_id):
        by_id = self._by_id()
        while True:
            event = by_id.get(event_id)
            if event is None:
                return event_id
            tags = [tag for tag in event["tags"] if tag[0] == "e"]
            if not tags:
                return event_id
            marked = [tag for tag in tags if len(tag) >= 4 and tag[3] == "root"]
            event_id = (marked or tags)[0][1]

    def add(self, pubkey, content, *, reply_to=None, kind=9, channel=CHANNEL, mention=(), extra_tags=()):
        """A message written by someone else than the sync (a person, an Agent, the mirror)."""
        self._clock += 1
        event_id = hashlib.sha256(f"human:{self._clock}:{content}".encode()).hexdigest()
        tags = [["h", channel]]
        if reply_to is not None:
            root = self.root_of(reply_to)
            if root != reply_to:
                tags.append(["e", root, "", "root"])
            tags.append(["e", reply_to, "", "reply"])
        tags += [["p", key] for key in mention]
        tags += [list(tag) for tag in extra_tags]
        tags.append(["auth", OWNER, "", SIG])
        self.events.append({"id": event_id, "pubkey": pubkey, "kind": kind, "created_at": self._clock,
                            "tags": tags, "content": content, "sig": "0" * 128})
        return event_id

    def send(self, content, reply_to=None, mentions=()):
        event_id = super().send(content, reply_to=reply_to, mentions=mentions)
        event = self.events[-1]
        if reply_to is not None:
            root = self.root_of(reply_to)
            if root != reply_to:
                event["tags"] = [["h", CHANNEL], ["e", root, "", "root"], ["e", reply_to, "", "reply"]]
        event["tags"].append(["auth", OWNER, "", SIG])
        return event_id

    def thread(self, event_id):
        self.thread_calls.append(event_id)
        event = self._by_id().get(event_id)
        if event is None:
            raise SYNC.BuzzCliError("messages thread", 1)  # {"error":"not_found",…}
        if CHANNEL not in [tag[1] for tag in event["tags"] if tag[0] == "h"]:
            raise SYNC.BuzzCliError("messages thread", 1)  # "does not belong to channel"
        root = self.root_of(event_id)
        members = [e for e in self.events if self.root_of(e["id"]) == root]
        return sorted(members, key=lambda e: (e["id"] != root, e["created_at"]))


class WindowBuzz(RealShapeBuzz):
    """The CLI returns a window of a long thread (`--limit 500` keeps the newest events): the root can be missing
    from it, and naming a reply returns only that reply's own subtree."""

    def __init__(self, hide_root=None, subtree_only=False):
        super().__init__()
        self.hide_root, self.subtree_only = hide_root, subtree_only

    def thread(self, event_id):
        events = super().thread(event_id)
        if self.hide_root is not None:
            events = [e for e in events if e["id"] != self.hide_root]
        if self.subtree_only:
            events = [e for e in events if e["id"] == event_id or ["e", event_id, "", "reply"] in e["tags"]]
        return events


class RelayDownBuzz(RealShapeBuzz):
    def __init__(self, failing_root):
        super().__init__()
        self.failing_root = failing_root

    def thread(self, event_id):
        if event_id == self.failing_root:
            raise SYNC.BuzzCliError("messages thread", 2)
        return super().thread(event_id)


class MilestoneGitLab(FakeGitLab):
    def __init__(self):
        super().__init__()
        self.milestone_list = []

    def milestones(self, project_id):
        return [dict(item) for item in self.milestone_list]


class PipelineGitLab(FakeGitLab):
    """One failed merge-request pipeline of MR 897."""

    def pipelines(self, project_id, updated_after):
        return [{"id": 54, "iid": 54, "status": "failed", "ref": "refs/merge-requests/897/head", "sha": "a" * 40,
                 "source": "merge_request_event", "web_url": f"{WEB}/-/pipelines/54",
                 "updated_at": "2026-09-13T02:00:00.000Z"}]

    def pipeline_jobs(self, project_id, pipeline_id):
        return [{"name": "test:unit", "status": "failed"}]


def milestone_event(event_id, action, iid, title="2026-Q4"):
    return {"id": event_id, "action_name": action, "target_type": "Milestone", "target_iid": iid,
            "target_title": title, "created_at": "2026-09-13T02:00:00.000Z", "author": {"username": "alice"}}


class HumanOriginCase(SyncCase):
    def setUp(self):
        super().setUp()
        self.buzz = RealShapeBuzz()
        self.discussion = self.buzz.add(MIRROR, "[飞书] 陈敬敏：@skill-desk 能不能做 skill 瘦身？", mention=[DESK])
        self.buzz.add(PERSON, "我觉得可以先看 token 占用", reply_to=self.discussion)

    # -- reading the result ----------------------------------------------------------------------------------------

    def roots(self):
        return [e for e in self.buzz.events if not any(t[0] == "e" for t in e["tags"])]

    def plaques(self):
        """Top-level Desk messages the sync created for objects."""
        return [e["content"] for e in self.roots() if e["pubkey"] == DESK and "📋" in e["content"]]

    def desk_replies(self, root):
        return [e for e in self.buzz.thread(root) if e["id"] != root and e["pubkey"] == DESK]

    def binding(self, kind, iid):
        notes = self.gitlab.issue_notes if kind == "issue" else self.gitlab.mr_note_list
        return SYNC.parse_binding(notes.get(iid, []), BOT_ID, PID, kind, iid, CHANNEL)

    def bind(self, iid, root, kind="issue"):
        note = SYNC.render_binding_note(
            {"project_id": PID, "object": kind, "iid": iid, "channel_id": CHANNEL, "root_event_id": root},
            SYNC.buzz_message_link(CHANNEL, root))
        target = self.gitlab.issue_notes if kind == "issue" else self.gitlab.mr_note_list
        target.setdefault(iid, []).append({"id": 900 + iid, "author": {"id": BOT_ID}, "body": note, "system": False,
                                           "created_at": "2026-09-13T02:00:00Z"})

    def comment(self, iid, body, note_id=50, username="alice"):
        self.gitlab.issue_notes.setdefault(iid, []).append(
            {"id": note_id, "author": {"id": 3, "username": username}, "body": body, "system": False,
             "created_at": "2026-09-13T02:30:00Z"})

    def fallbacks(self, result):
        return [(item["object"], item["iid"]) for item in result["origin_fallbacks"]]


# ── the origin is a human's top-level message ────────────────────────────────────────────────────────────────────────


class MarkerToHumanRootBindsTest(HumanOriginCase):
    def test_an_issue_binds_to_the_discussion_and_replies_there_without_a_plaque(self):
        """L1-GIS-HO-001 marker 指向人的顶层消息：issue 绑到该话题，事实回复在话题里，不另开门牌线程（skills#125）。"""
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身", description=f"讨论见话题\n{marker(self.discussion)}")]
        roots_before = self.roots()
        result = self.run_sync()
        self.assertEqual((result["status"], result["stalled"], result["origin_fallbacks"]), ("ok", [], []))
        self.assertEqual(self.binding("issue", 125), self.discussion)
        self.assertEqual(self.roots(), roots_before)  # no plaque, no new top-level message at all
        replies = self.desk_replies(self.discussion)
        self.assertEqual(len(replies), 1)
        self.assertIn(["e", self.discussion, "", "reply"], replies[0]["tags"])

    def test_the_first_fact_carries_the_title_and_link_so_the_discussion_sees_the_issue(self):
        """L1-GIS-HO-002 没有伪造的门牌行：话题里的第一条事实自带 Issue 标题和链接。"""
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身", description=marker(self.discussion))]
        self.run_sync()
        self.assertEqual(self.binding("issue", 125), self.discussion)
        fact = self.desk_replies(self.discussion)[0]["content"]
        self.assertIn(f"[#125 skill 瘦身]({WEB}/-/issues/125)", fact)
        self.assertIsNone(SYNC.plaque_url(fact))  # a fact, not a plaque: no bare URL line, no project-path line
        self.assertNotIn("buzz-sync-test/pilot", fact.split("\n"))
        human = next(e for e in self.buzz.events if e["id"] == self.discussion)
        self.assertEqual(human["pubkey"], MIRROR)  # the person's message is never touched
        self.assertNotIn("#125", human["content"])

    def test_the_author_of_the_top_level_message_does_not_matter(self):
        """L1-GIS-HO-003 作者不限：人、Feishu 镜像身份、别的 Agent 的顶层消息都可以（Desk 自己的只认门牌／事实，见 HO-029）。"""
        for name, author in (("person", PERSON), ("mirror", MIRROR), ("agent", AGENT)):
            with self.subTest(author=name):
                root = self.buzz.add(author, f"来自 {name} 的顶层话题")
                iid = 200 + len(self.gitlab.issue_list[PID])
                self.gitlab.issue_list[PID].append(make_issue(iid, description=marker(root)))
                result = self.run_sync()
                self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
                self.assertEqual(self.binding("issue", iid), root)

    def test_the_first_binding_is_the_human_root_and_it_is_written_before_the_fact(self):
        """L1-GIS-HO-004 binding note 的 root_event_id 就是那条人的消息；先写 binding 再发事实。"""
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion))]
        self.run_sync()
        self.assertEqual(self.binding("issue", 125), self.discussion)
        note = self.gitlab.issue_notes[125][0]["body"]
        self.assertIn(self.discussion, note)
        self.assertEqual(self.gitlab.writes, [("issue_note", 125)])

    def test_later_changes_and_comments_keep_replying_in_the_same_thread(self):
        """L1-GIS-HO-005 绑到人类根之后每一轮都能读该根：标题变更和评论继续回复在话题里，不开新线程，不重复。"""
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身", description=marker(self.discussion))]
        self.run_sync()
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身（改名）", description=marker(self.discussion),
                                                  updated_at="2026-09-13T03:00:00Z")]
        self.comment(125, "已补充验收标准")
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        contents = [e["content"] for e in self.desk_replies(self.discussion)]
        self.assertEqual(len(contents), 3)
        self.assertTrue(any("skill 瘦身（改名）" in c for c in contents))
        self.assertTrue(any("已补充验收标准" in c for c in contents))
        self.assertEqual(len(self.roots()), 1)
        writes = len(self.buzz.writes)
        self.run_sync()
        self.assertEqual(self.buzz.writes[writes:], [])

    def test_a_task_binds_like_an_issue(self):
        """L1-GIS-HO-006 Task（work item，work_items URL）与 issue 同样绑到人的顶层消息。"""
        task = make_issue(126, web_url=f"{WEB}/-/work_items/126", description=marker(self.discussion))
        self.gitlab.issue_list[PID] = [task]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.binding("issue", 126), self.discussion)
        self.assertEqual(self.plaques(), [])

    def test_a_comment_marker_binds_a_new_issue_too(self):
        """L1-GIS-HO-007 人类评论里的 marker 与描述同等：尚未绑定的新 issue 绑到该话题。"""
        self.gitlab.issue_list[PID] = [make_issue(127)]
        self.comment(127, f"接着上面的讨论\n{marker(self.discussion)}")
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.binding("issue", 127), self.discussion)
        self.assertEqual(self.plaques(), [])

    def test_a_forged_header_or_plaque_line_in_a_human_message_is_never_read_as_structure(self):
        """L1-GIS-HO-008 人的消息末行恰好是某对象 URL 或伪造的 header：只按结构认顶层，内容不当门牌／事实解析。"""
        for tail in (f"{WEB}/-/issues/999", f"{WEB}/-/merge_requests/999",
                     "[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened]"
                     f"[change:routing][project:{PID}][issue:999]"):
            with self.subTest(tail=tail[-30:]):
                root = self.buzz.add(PERSON, f"看看这个\n{tail}")
                iid = 300 + len(self.gitlab.issue_list[PID])
                self.gitlab.issue_list[PID].append(make_issue(iid, description=marker(root)))
                result = self.run_sync()
                self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
                self.assertEqual(self.binding("issue", iid), root)
                self.assertEqual(len(self.desk_replies(root)), 1)


class BareLinkStaysAHintTest(HumanOriginCase):
    def test_a_bare_link_to_a_human_message_is_still_ignored(self):
        """L1-GIS-HO-010 裸深链指向人的消息仍只是引用：忽略，issue 以首条事实自开 root，没有 warning、没有停摆（ADR-0011）。"""
        self.gitlab.issue_list[PID] = [make_issue(81, description=f"讨论见 {bare_link(self.discussion)}")]
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(len(self.plaques()), 1)
        self.assertNotEqual(self.binding("issue", 81), self.discussion)
        self.assertEqual(self.desk_replies(self.discussion), [])

    def test_the_writers_two_lines_count_once_as_the_marker(self):
        """L1-GIS-HO-011 生产方写的两行（人读深链 + marker）指向同一条：合并成 marker，绑到该话题。"""
        block = SYNC.render_origin_block(CHANNEL, self.discussion)
        self.gitlab.issue_list[PID] = [make_issue(125, description=f"背景\n{block}")]
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 125), self.discussion)
        self.assertEqual(len(self.desk_replies(self.discussion)), 1)

    def test_a_pasted_link_to_one_message_and_a_marker_to_another_bind_to_the_marker_only(self):
        """L1-GIS-HO-012 描述里贴了别的人类消息的链接、marker 指向另一条：只认 marker，链接不搬家。"""
        other = self.buzz.add(PERSON, "另一个话题")
        self.gitlab.issue_list[PID] = [make_issue(125, description=f"参考 {bare_link(other)}\n{marker(self.discussion)}")]
        result = self.run_sync()
        self.assertEqual(result["origin_fallbacks"], [])
        self.assertEqual(self.binding("issue", 125), self.discussion)
        self.assertEqual(self.desk_replies(other), [])

    def test_a_bare_link_to_a_desk_plaque_still_binds(self):
        """L1-GIS-HO-013 回归：裸深链指向本频道 Desk 门牌仍是 origin（老规则不变）。"""
        plaque = self.buzz.send(f"📋 **#14 weekly**\nbuzz-sync-test/pilot\n{WEB}/-/issues/14")
        self.gitlab.issue_list[PID] = [make_issue(83, description=f"接上 {bare_link(plaque)}")]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.binding("issue", 83), plaque)
        self.assertEqual(len(self.plaques()), 1)


# ── the marker names a root that cannot be used: fall back, do not stall ─────────────────────────────────────────────


class UnusableMarkerFallsBackTest(HumanOriginCase):
    def assert_own_plaque_and_warning(self, iid, reason_words):
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.fallbacks(result), [("issue", iid)])
        entry = result["origin_fallbacks"][0]
        self.assertEqual(entry["project"], PID)
        for word in (f"issue {iid}", *reason_words):
            self.assertIn(word, entry["reason"])
        plaque_root = self.binding("issue", iid)
        self.assertIsNotNone(plaque_root)
        self.assertNotEqual(plaque_root, self.discussion)
        self.assertTrue(any(f"#{iid}" in text for text in self.plaques()))
        return result

    def test_a_marker_naming_a_reply_falls_back(self):
        """L1-GIS-HO-020 marker 指向话题里的一条回帖（不是顶层）：以首条事实自开 root + warning，不停摆，也不走到根去。"""
        reply = self.buzz.add(PERSON, "这条是回帖", reply_to=self.discussion)
        self.gitlab.issue_list[PID] = [make_issue(130, description=marker(reply))]
        self.assert_own_plaque_and_warning(130, ["top-level"])
        self.assertEqual(self.desk_replies(self.discussion), [])

    def test_a_root_the_cli_cannot_find_falls_back(self):
        """L1-GIS-HO-021 根读不到（CLI 退出码 1，被删或不存在）：以首条事实自开 root + warning，对象照常同步。"""
        self.gitlab.issue_list[PID] = [make_issue(131, description=marker("c" * 64)), make_issue(132)]
        result = self.assert_own_plaque_and_warning(131, ["cannot be read"])
        self.assertTrue(any("#132" in text for text in self.plaques()))
        self.assertEqual(len(result["origin_fallbacks"]), 1)

    def test_a_root_deleted_before_the_first_round_falls_back(self):
        """L1-GIS-HO-022 话题根在第一轮之前被删：同样回退（复现 2026-09-19 的形态但不停摆）。"""
        self.buzz.events = [e for e in self.buzz.events if e["id"] != self.discussion]
        self.gitlab.issue_list[PID] = [make_issue(133, description=marker(self.discussion))]
        self.assert_own_plaque_and_warning(133, ["cannot be read"])

    def test_a_thread_window_without_its_root_falls_back(self):
        """L1-GIS-HO-030a 长 Thread 的窗口里没有根（CLI 只回最新 500 条）：读不到根，回退，不当成有效根。"""
        self.buzz = WindowBuzz()
        self.discussion = self.buzz.add(MIRROR, "长话题")
        reply = self.buzz.add(PERSON, "窗口里的回帖", reply_to=self.discussion)
        self.buzz.hide_root = self.discussion
        for iid, named in ((139, self.discussion), (140, reply)):
            with self.subTest(named="root" if named == self.discussion else "reply"):
                self.gitlab.issue_list[PID].append(make_issue(iid, description=marker(named)))
                result = self.run_sync()
                self.assertEqual(result["stalled"], [])
                self.assertIn((("issue", iid)), self.fallbacks(result))
                self.assertIn("cannot be read", next(f for f in result["origin_fallbacks"] if f["iid"] == iid)["reason"])

    def test_the_reason_is_neutralized_like_a_stalled_reason(self):
        """L1-GIS-HO-030b origin_fallbacks 的原因文本和 stalled 的一样中和 @、nostr: 并打码像密钥的长串（它来自 GitLab 文本时不能带出提及或密钥）。"""
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        syncer._note_origin_fallback(PID, "issue", 5, "issue 5 @alice nostr:npub1abc glpat-AbCdEfGhIjKlMnOpQrStUv")
        reason = syncer._origin_fallbacks[0]["reason"]
        for forbidden in ("@alice", "nostr:", "glpat-AbCdEfGhIjKlMnOpQrStUv"):
            self.assertNotIn(forbidden, reason)
        self.assertIn("issue 5", reason)

    def test_a_marker_for_another_channel_falls_back_without_reading_anything(self):
        """L1-GIS-HO-023 marker 的 channel_id 不是本频道：以首条事实自开 root + warning；不去读别的频道。"""
        other = "00000000-0000-4000-8000-0000000000c9"
        self.gitlab.issue_list[PID] = [make_issue(134, description=marker(self.discussion, channel=other))]
        self.assert_own_plaque_and_warning(134, ["channel"])
        self.assertEqual(self.buzz.thread_calls.count(self.discussion), 0)

    def test_a_root_of_another_channel_falls_back(self):
        """L1-GIS-HO-024 CLI 说该事件不属于本频道（退出码 1）：回退。"""
        foreign = self.buzz.add(PERSON, "别的频道的话题", channel="00000000-0000-4000-8000-0000000000c9")
        self.gitlab.issue_list[PID] = [make_issue(135, description=marker(foreign))]
        self.assert_own_plaque_and_warning(135, ["cannot be read"])

    def test_a_root_that_is_not_a_plain_channel_message_falls_back(self):
        """L1-GIS-HO-025 kind 不是 9、有两个 h 标签、没有 h 标签：都不是顶层频道消息，回退。"""
        other = "00000000-0000-4000-8000-0000000000c9"
        cases = {
            "reaction": self.buzz.add(PERSON, "👍", kind=7),
            "two h tags": self.buzz.add(PERSON, "跨两个频道", extra_tags=[["h", other]]),
        }
        no_h = self.buzz.add(PERSON, "没有频道标签")
        for event in self.buzz.events:
            if event["id"] == no_h:
                event["tags"] = [tag for tag in event["tags"] if tag[0] != "h"]
        cases["no h tag"] = no_h
        for name, root in cases.items():
            with self.subTest(root=name):
                iid = 400 + len(self.gitlab.issue_list[PID])
                self.gitlab.issue_list[PID].append(make_issue(iid, description=marker(root)))
                result = self.run_sync()
                self.assertEqual((result["stalled"], (iid in [i for _, i in self.fallbacks(result)])), ([], True))

    def test_a_desk_chat_message_is_not_a_root_but_a_desk_fact_is(self):
        """L1-GIS-HO-029 Desk 自己的顶层消息只有是门牌／事实才算根；Desk 的普通发言（它也在讨论里说话）回退，不当话题根。"""
        chat = self.buzz.add(DESK, "收到，我来看看。")
        self.gitlab.issue_list[PID] = [make_issue(137, description=marker(chat))]
        self.assert_own_plaque_and_warning(137, ["Desk-published"])
        plaque = self.buzz.send(f"📋 **#14 weekly**\nbuzz-sync-test/pilot\n{WEB}/-/issues/14")
        self.gitlab.issue_list[PID] = [make_issue(138, description=marker(plaque))]
        result = self.run_sync()
        self.assertEqual(self.binding("issue", 138), plaque)
        self.assertNotIn(("issue", 138), self.fallbacks(result))

    def test_the_report_names_each_object_and_reason_once_per_round(self):
        """L1-GIS-HO-026 origin_fallbacks 每个对象、每个原因一条（同一对象在一轮里被多次解析也不重复）；健康的一轮是空列表。"""
        healthy = self.run_sync()
        self.assertEqual(healthy["origin_fallbacks"], [])
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="feature/x", description=marker("c" * 64))]
        result = self.run_sync()
        self.assertEqual(self.fallbacks(result), [("mr", 897)])
        self.assertEqual(set(result["origin_fallbacks"][0]), {"project", "object", "iid", "reason"})

    def test_the_report_does_not_carry_fallbacks_over_to_the_next_run(self):
        """L1-GIS-HO-097 同一个 Syncer 再跑一轮：origin_fallbacks 只含这一轮的，上一轮已返回的结果不被改动。"""
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        self.gitlab.issue_list[PID] = [make_issue(190, description=marker("c" * 64))]
        first = syncer.run()
        self.assertEqual(self.fallbacks(first), [("issue", 190)])
        self.gitlab.issue_list[PID] = [make_issue(190, description="", updated_at="2026-09-13T03:00:00Z")]
        second = syncer.run()
        self.assertEqual(second["origin_fallbacks"], [])
        self.assertEqual(self.fallbacks(first), [("issue", 190)])

    def test_a_dry_run_reports_the_fallback_and_writes_nothing(self):
        """L1-GIS-HO-027 --dry-run 同样报出会回退的对象，不写任何东西。"""
        self.gitlab.issue_list[PID] = [make_issue(136, description=marker("c" * 64))]
        result = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)
        self.assertEqual(self.fallbacks(result), [("issue", 136)])
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))

    def test_an_mr_and_a_milestone_fall_back_the_same_way(self):
        """L1-GIS-HO-028 MR 和 milestone：不可用的 marker 同样回退到自己的门牌并记 warning。"""
        self.gitlab = MilestoneGitLab()
        self.gitlab.milestone_list = [{"iid": 9, "title": "2026-Q4", "description": marker("c" * 64)}]
        self.gitlab.event_list = [milestone_event(5, "created", 9)]
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="feature/x", description=marker("c" * 64))]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(sorted(self.fallbacks(result)), [("milestone", 9), ("mr", 897)])
        self.assertTrue(any("🎯" in e["content"] for e in self.roots()))
        self.assertTrue(any("!897" in e["content"] for e in self.roots()))


class StillStallsOrStopsTest(HumanOriginCase):
    def test_a_malformed_marker_still_stalls_that_object_only(self):
        """L1-GIS-HO-030 marker 格式非法（不是 JSON）：仍按现有规则停摆该对象，其余照常，不记 fallback。"""
        self.gitlab.issue_list[PID] = [
            make_issue(140, description="x\n<!-- gitlab-buzz-origin:v1 {not json} -->"), make_issue(141)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 140)])
        self.assertEqual((result["status"], result["origin_fallbacks"]), ("degraded", []))
        self.assertTrue(any("#141" in text for text in self.plaques()))
        self.assertEqual(self.desk_replies(self.discussion), [])

    def test_a_marker_with_the_wrong_key_set_or_ids_still_stalls(self):
        """L1-GIS-HO-031 键集合不对、channel_id 不是 UUID、root 不是 64 位 hex：仍停摆。"""
        bad = (
            f'<!-- gitlab-buzz-origin:v1 {{"channel_id":"{CHANNEL}","root_event_id":"{self.discussion}","x":1}} -->',
            f'<!-- gitlab-buzz-origin:v1 {{"channel_id":"nope","root_event_id":"{self.discussion}"}} -->',
            f'<!-- gitlab-buzz-origin:v1 {{"channel_id":"{CHANNEL}","root_event_id":"zz"}} -->',
        )
        for index, text in enumerate(bad):
            with self.subTest(text=text[:60]):
                iid = 150 + index
                self.gitlab.issue_list[PID].append(make_issue(iid, description=text))
                result = self.run_sync()
                self.assertIn(("issue", iid), [(s["object"], s["iid"]) for s in result["stalled"]])
                self.assertEqual(result["origin_fallbacks"], [])

    def test_a_relay_failure_reading_the_root_still_fails_the_round(self):
        """L1-GIS-HO-032 读根时 CLI 退出码 2+（relay／认证）不是数据问题：整轮失败，不写缓存。"""
        self.buzz = RelayDownBuzz("d" * 64)
        self.gitlab.issue_list[PID] = [make_issue(160, description=marker("d" * 64)), make_issue(161)]
        with self.assertRaises(SYNC.BuzzCliError):
            self.run_sync()
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])

    def test_a_bound_root_that_was_deleted_still_stalls_the_object(self):
        """L1-GIS-HO-033 已绑定到人的根、之后根被删：该对象 stalled（bound root cannot be read），其余照常。"""
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion))]
        self.run_sync()
        self.assertEqual(self.binding("issue", 125), self.discussion)
        self.buzz.events = [e for e in self.buzz.events if e["id"] != self.discussion]
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion),
                                                  updated_at="2026-09-13T03:00:00Z"), make_issue(162)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 125)])
        self.assertIn("cannot be read", result["stalled"][0]["reason"])
        self.assertTrue(any("#162" in text for text in self.plaques()))


class BoundRootValidationTest(HumanOriginCase):
    def test_a_binding_to_a_human_top_level_message_is_a_valid_bound_root(self):
        """L1-GIS-HO-040 已有 binding 指向人的顶层消息（来自 origin 的绑定）：这一轮能读、能回复，不停摆。"""
        self.bind(170, self.discussion)
        self.gitlab.issue_list[PID] = [make_issue(170)]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(len(self.desk_replies(self.discussion)), 1)

    def test_a_binding_to_a_reply_is_still_refused(self):
        """L1-GIS-HO-041 binding 指向一条回帖：仍是「不是本对象的 Desk 门牌」，该对象 stalled。"""
        reply = self.buzz.add(PERSON, "这条是回帖", reply_to=self.discussion)
        self.bind(171, reply)
        self.gitlab.issue_list[PID] = [make_issue(171)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 171)])
        self.assertEqual(self.desk_replies(self.discussion), [])

    def test_a_binding_to_a_message_of_another_kind_is_still_refused(self):
        """L1-GIS-HO-042 binding 指向的不是 kind 9 的频道消息：stalled。"""
        reaction = self.buzz.add(PERSON, "👍", kind=7)
        self.bind(172, reaction)
        self.gitlab.issue_list[PID] = [make_issue(172)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 172)])

    def test_a_human_root_is_only_a_root_for_issues_mrs_and_milestones(self):
        """L1-GIS-HO-044 人的顶层消息只能当 issue／MR／milestone 的根，不是分支线程等别的对象的根（分支根一直只认 Desk 门牌）。"""
        syncer = SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))
        for kind in ("issue", "mr", "milestone"):
            with self.subTest(kind=kind):
                self.assertTrue(syncer._thread(self.discussion, PID, kind, 1))
        with self.assertRaises(SYNC.ObjectError):
            syncer._thread(self.discussion, PID, "branch", 0, expected_plaque=f"{WEB}/-/tree/main")

    def test_a_human_root_thread_at_the_reply_cap_stalls_the_object(self):
        """L1-GIS-HO-043 话题回帖已到 500 条上限：该对象 stalled（沿用 ADR-0009 的上限），不静默丢事实。"""
        for index in range(SYNC.THREAD_REPLY_LIMIT):
            self.buzz.add(PERSON, f"回帖 {index}", reply_to=self.discussion)
        self.bind(173, self.discussion)
        self.gitlab.issue_list[PID] = [make_issue(173)]
        result = self.run_sync()
        self.assertEqual([(s["object"], s["iid"]) for s in result["stalled"]], [("issue", 173)])
        self.assertIn(str(SYNC.THREAD_REPLY_LIMIT), result["stalled"][0]["reason"])


# ── unchanged semantics with a human root ────────────────────────────────────────────────────────────────────────────


class OriginSemanticsStayTest(HumanOriginCase):
    def test_a_marker_to_a_desk_plaque_still_binds_and_walks_from_a_fact_reply(self):
        """L1-GIS-HO-050 回归：marker 指向 Desk 门牌绑定；指向 Desk 事实回帖时走到该 Thread 的根（真实形态的整条 Thread 输出）。"""
        first = SYNC.issue_fact(make_issue(14), PID)
        plaque = self.buzz.send(SYNC.render_issue_plaque(first, "buzz-sync-test/pilot"))
        fact = self.buzz.send(SYNC.render_message(first, "routing", first=True), reply_to=plaque)
        self.bind(14, plaque)
        self.gitlab.issue_list[PID] = [make_issue(14)]
        self.gitlab.issue_list[PID].append(make_issue(180, description=marker(plaque)))
        self.gitlab.issue_list[PID].append(make_issue(181, description=marker(fact)))
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual((self.binding("issue", 180), self.binding("issue", 181)), (plaque, plaque))

    def test_a_marker_to_a_new_fact_root_uses_that_thread(self):
        self.gitlab.issue_list[PID] = [make_issue(14)]
        self.run_sync()
        root = self.binding("issue", 14)
        self.assertEqual(SYNC.parse_header(self.buzz.thread(root)[0]["content"])["issue"], 14)
        self.gitlab.issue_list[PID].append(make_issue(180, description=marker(root)))
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 180), root)

    def test_a_person_reply_inside_a_desk_thread_still_walks_to_the_desk_root(self):
        """L1-GIS-HO-055 回归：marker 指向 Desk 门牌 Thread 里某个人的回帖：照旧走到 Thread 的根（Desk 门牌）并绑定；只有根也不是 Desk 的才回退（人的话题里的回帖）。"""
        self.gitlab.issue_list[PID] = [make_issue(14)]
        self.run_sync()
        plaque = self.binding("issue", 14)
        reply = self.buzz.add(PERSON, "这个 issue 我来跟", reply_to=plaque)
        self.gitlab.issue_list[PID].append(make_issue(184, description=marker(reply)))
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 184), plaque)

    def test_a_named_desk_reply_walks_to_its_root_even_when_the_cli_returns_only_its_subtree(self):
        """L1-GIS-HO-056 点名 Desk 事实回帖时，即使 CLI 只回该事件自己的子树（不含根），也再读一次根并绑定（老规则不依赖 CLI 输出里带根）。"""
        self.buzz = WindowBuzz(subtree_only=True)
        self.discussion = self.buzz.add(MIRROR, "话题")
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion))]
        self.run_sync()
        fact = self.desk_replies(self.discussion)[0]["id"]
        self.gitlab.issue_list[PID].append(make_issue(185, description=marker(fact)))
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 185), self.discussion)

    def test_a_desk_fact_inside_a_human_thread_walks_to_that_human_root(self):
        """L1-GIS-HO-051 marker 指向话题里 Desk 已发的事实回帖：走到该话题的人类顶层根并绑定（新话题里的 Desk 事实也是「事实」）。"""
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion))]
        self.run_sync()
        self.assertEqual(self.binding("issue", 125), self.discussion)
        fact = self.desk_replies(self.discussion)[0]["id"]
        self.gitlab.issue_list[PID].append(make_issue(182, description=marker(fact)))
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 182), self.discussion)

    def test_several_origins_are_deduplicated_and_the_first_is_the_binding(self):
        """L1-GIS-HO-052 多个 origin 去重后都回复，第一条（描述里的）是 binding；同一话题被描述和评论各写一次只回复一次。"""
        self.gitlab.issue_list[PID] = [make_issue(14)]
        self.run_sync()
        plaque = self.binding("issue", 14)
        second_discussion = self.buzz.add(PERSON, "另一个话题")
        self.gitlab.issue_list[PID].append(make_issue(
            183, description=f"{marker(self.discussion)}\n{marker(self.discussion)}\n{marker(second_discussion)}"))
        self.comment(183, marker(self.discussion))
        self.comment(183, marker(plaque), note_id=51)
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("issue", 183), self.discussion)
        for root in (self.discussion, second_discussion):
            self.assertEqual(len([e for e in self.desk_replies(root) if "#183" in e["content"] or "Issue 183" in e["content"]]), 1)
        self.assertEqual(len([e for e in self.desk_replies(plaque) if "Issue 183" in e["content"]]), 1)

    def test_an_object_that_already_has_a_binding_is_not_moved_but_the_new_thread_gets_a_copy(self):
        """L1-GIS-HO-053 已有 binding 的对象不因后来加了 origin 而搬家；后来评论里的 origin 仍向该话题补一份本轮事实（#125 的现状）。"""
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身")]
        self.run_sync()
        own_plaque = self.binding("issue", 125)
        self.comment(125, f"讨论在这里\n{marker(self.discussion)}")
        self.gitlab.issue_list[PID] = [make_issue(125, title="skill 瘦身", updated_at="2026-09-13T03:00:00Z")]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.binding("issue", 125), own_plaque)
        self.assertEqual(len(self.gitlab.issue_notes[125]) - 1, 1)  # still exactly one binding note (plus the comment)
        self.assertTrue(any("#125 skill 瘦身" in e["content"] for e in self.desk_replies(self.discussion)))

    def test_a_gitlab_issue_association_still_beats_an_origin_for_an_mr(self):
        """L1-GIS-HO-054（ADR-0015 改）MR 绑定优先级不变：关联 Issue 先于 origin；origin 的话题不再收事实，只收一条交叉链接。"""
        self.gitlab.issue_list[PID] = [make_issue(14)]
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(self.discussion))]
        self.gitlab.closes[897] = [14]
        self.run_sync()
        self.assertEqual(self.binding("mr", 897), self.binding("issue", 14))
        changes = [(SYNC.parse_header(e["content"]) or {}).get("change") for e in self.desk_replies(self.discussion)
                   if (SYNC.parse_header(e["content"]) or {}).get("mr") == 897]
        self.assertEqual(changes, ["xref"])
        facts = [e for e in self.desk_replies(self.binding("issue", 14))
                 if (SYNC.parse_header(e["content"]) or {}).get("change") == "lifecycle"
                 and (SYNC.parse_header(e["content"]) or {}).get("mr") == 897]
        self.assertEqual(len(facts), 1)


class MrAndMilestoneTest(HumanOriginCase):
    def test_an_unassociated_mr_binds_to_the_discussion(self):
        """L1-GIS-HO-060 未关联 Issue 的 MR：marker 指向人的顶层消息，绑定并回复在话题里，不开 MR 门牌；标题和链接在第一条事实里。"""
        self.gitlab.mr_list[PID] = [make_mr(897, title="瘦身第一步", source_branch="fix/x", description=marker(self.discussion))]
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(self.binding("mr", 897), self.discussion)
        self.assertEqual(len(self.roots()), 1)
        fact = self.desk_replies(self.discussion)[0]["content"]
        self.assertIn(f"[!897 瘦身第一步]({WEB}/-/merge_requests/897)", fact)

    def test_a_later_mr_update_replies_in_the_discussion(self):
        """L1-GIS-HO-061 绑到人类根的 MR 之后的更新（新提交）继续回复在话题里，接在它自己的上一条事实之后。"""
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(self.discussion))]
        self.run_sync()
        self.assertEqual(self.binding("mr", 897), self.discussion)
        first = self.desk_replies(self.discussion)[0]["id"]
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(self.discussion),
                                            sha="b" * 40, updated_at="2026-09-13T03:00:00Z")]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        replies = self.desk_replies(self.discussion)
        self.assertEqual(len(replies), 2)
        self.assertIn(["e", first, "", "reply"], replies[1]["tags"])
        self.assertEqual(len(self.roots()), 1)

    def test_an_mr_merged_into_an_issue_bound_to_a_discussion_posts_there(self):
        """L1-GIS-HO-062 关联 Issue 绑在人类话题里：MR 的事实并入该话题（读 Issue 的绑定根不再要求是 Desk 门牌）。"""
        self.gitlab.issue_list[PID] = [make_issue(125, description=marker(self.discussion))]
        self.gitlab.mr_list[PID] = [make_mr(897, title="瘦身第一步", source_branch="fix/x")]
        self.gitlab.closes[897] = [125]
        result = self.run_sync()
        self.assertEqual(result["stalled"], [])
        self.assertEqual(self.binding("mr", 897), self.discussion)
        self.assertTrue(any("!897" in e["content"] for e in self.desk_replies(self.discussion)))
        self.assertEqual(len(self.roots()), 1)

    def test_a_human_root_that_ends_with_the_mr_url_is_not_a_group_anchor(self):
        """L1-GIS-HO-063 人的消息末行恰好是该 MR 的 URL：不能被当作它自己的 per-MR 根去登记分支族锚点。"""
        root = self.buzz.add(PERSON, f"请看这个 MR\n{WEB}/-/merge_requests/897")
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(root))]
        self.run_sync()
        self.assertEqual(self.binding("mr", 897), root)
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(root), sha="b" * 40,
                                            updated_at="2026-09-13T03:00:00Z")]
        self.run_sync()
        self.assertEqual(list(Path(self.tmp.name).glob("mr-group-bindings-*.json")), [])

    def test_mr_activity_replies_to_the_human_root_not_to_a_fact_chain(self):
        """L1-GIS-HO-065 绑到人类根的 MR 收到流水线失败等活动：回复挂在话题根上；人的消息末行伪造的 issue header 不能让它被当成 Issue 线程去接续 MR 事实链。"""
        forged = ("[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened]"
                  f"[change:routing][project:{PID}][issue:999]")
        root = self.buzz.add(PERSON, f"讨论\n{forged}")
        self.gitlab = PipelineGitLab()
        self.gitlab.mr_list[PID] = [make_mr(897, source_branch="fix/x", description=marker(root))]
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["activity"]), ([], 1))
        self.assertEqual(self.binding("mr", 897), root)
        failure = [e for e in self.desk_replies(root) if "流水线失败" in e["content"]]
        self.assertEqual(len(failure), 1)
        self.assertIn(["e", root, "", "reply"], failure[0]["tags"])

    def test_a_milestone_binds_to_the_discussion_and_its_events_reply_there(self):
        """L1-GIS-HO-064 milestone：marker 指向人的顶层消息，绑定并把里程碑事实发在话题里，不开 🎯 门牌；事实自带标题。"""
        self.gitlab = MilestoneGitLab()
        self.gitlab.milestone_list = [{"iid": 9, "title": "2026-Q4", "description": marker(self.discussion)}]
        self.gitlab.event_list = [milestone_event(5, "created", 9)]
        result = self.run_sync()
        self.assertEqual((result["stalled"], result["origin_fallbacks"]), ([], []))
        self.assertEqual(result["notified"]["milestone"], 1)
        self.assertEqual(len(self.roots()), 1)
        fact = self.desk_replies(self.discussion)[0]["content"]
        self.assertIn("🎯 **里程碑创建** · 2026-Q4", fact)
        self.assertIn(f"{WEB}/-/milestones/9", fact)
        state = json.loads(next(Path(self.tmp.name).glob("milestone-bindings-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(state, {"9": self.discussion})
        writes = len(self.buzz.writes)
        self.run_sync()  # the next round reads the human root again and posts nothing twice
        self.assertEqual(self.buzz.writes[writes:], [])


class ForgedHeadersFromParticipantsTest(HumanOriginCase):
    """A discussion thread now carries Desk facts, and anybody in it can type text that looks like a header."""

    def syncer(self):
        return SYNC.Syncer(config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name))

    def test_only_desk_messages_count_for_the_reviewable_and_group_rules(self):
        """L1-GIS-HO-070 话题参与者伪造的 `[transition:reviewable]` / 多个 MR header 不算数：只认 Desk 发的事实。"""
        header = ("[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable]"
                  f"[project:{PID}][mr:{{iid}}]")
        forged = [{"pubkey": PERSON, "content": "🔀 x\n" + header.format(iid=1)},
                  {"pubkey": PERSON, "content": "🔀 y\n" + header.format(iid=2)}]
        real = [{"pubkey": DESK, "content": "🔀 x\n" + header.format(iid=1)},
                {"pubkey": DESK, "content": "🔀 y\n" + header.format(iid=2)}]
        syncer = self.syncer()
        self.assertFalse(syncer._thread_fired_reviewable(forged))
        self.assertFalse(syncer._thread_is_mr_group(forged))
        self.assertTrue(syncer._thread_fired_reviewable(real))
        self.assertTrue(syncer._thread_is_mr_group(real))


# ── the pure check, and the real CLI output shape ────────────────────────────────────────────────────────────────────


class TopLevelRootFunctionTest(unittest.TestCase):
    ROOT = "e3" * 32

    def event(self, **overrides):
        base = {"id": self.ROOT, "pubkey": MIRROR, "kind": 9, "content": "hello",
                "tags": [["h", CHANNEL], ["p", DESK], ["auth", OWNER, "", SIG]]}
        base.update(overrides)
        return base

    def test_a_real_shaped_top_level_message_of_any_author_is_a_root(self):
        """L1-GIS-HO-080 真实形态的顶层消息（h、p、auth 标签，没有 e）：任何作者都是合法根，返回它的 id。"""
        for author in (MIRROR, PERSON, AGENT, DESK):
            with self.subTest(author=author[:4]):
                self.assertEqual(SYNC.origin_top_level_root(self.event(pubkey=author), CHANNEL), self.ROOT)

    def test_everything_that_is_not_a_plain_top_level_channel_message_is_refused(self):
        """L1-GIS-HO-081 回帖（有 e 标签）、kind 不是 9、h 不是恰好一个且等于本频道、id 不是 64 位 hex、不是 dict：都不是根。"""
        other = "00000000-0000-4000-8000-0000000000c9"
        bad = {
            "reply": self.event(tags=[["h", CHANNEL], ["e", "f" * 64, "", "reply"]]),
            "kind 7": self.event(kind=7),
            "no h": self.event(tags=[["auth", OWNER, "", SIG]]),
            "two h": self.event(tags=[["h", CHANNEL], ["h", other]]),
            "other channel": self.event(tags=[["h", other]]),
            "short id": self.event(id="abc"),
            "upper-case id": self.event(id="E3" * 32),
            "no id": {k: v for k, v in self.event().items() if k != "id"},
        }
        for name, event in bad.items():
            with self.subTest(case=name):
                self.assertIsNone(SYNC.origin_top_level_root(event, CHANNEL))
        self.assertIsNone(SYNC.origin_top_level_root("not an event", CHANNEL))

    def test_the_desk_only_rule_is_unchanged_for_bare_links(self):
        """L1-GIS-HO-082 origin_canonical_root（裸链和 Desk 事实用的老规则）不变：人的消息不是 Desk 门牌。"""
        self.assertIsNone(SYNC.origin_canonical_root(self.event(), DESK, CHANNEL))


class RealCliOutputTest(unittest.TestCase):
    """The recorded shape of `buzz messages thread` (ids, keys and text are synthetic; the tag layout is real)."""

    def cli(self, stdout, returncode=0):
        buzz = object.__new__(SYNC.BuzzCli)
        buzz.channel, buzz.publisher, buzz.cli, buzz.env = CHANNEL, DESK, "/x/buzz", {}
        buzz.sleeper = lambda seconds: None
        buzz.runner = lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
        return buzz

    def test_the_root_is_first_and_a_human_root_passes_the_check_from_real_output(self):
        """L1-GIS-HO-090 真实 `messages thread` 输出：JSON 数组、根在前、回帖只带 e/h/auth 标签；从中取根可通过顶层检查。"""
        raw = FIXTURE.read_text(encoding="utf-8")
        events = self.cli(raw).thread(json.loads(raw)[0]["id"])
        root = events[0]
        self.assertEqual(SYNC.origin_top_level_root(root, CHANNEL), root["id"])
        for reply in events[1:]:
            self.assertIsNone(SYNC.origin_top_level_root(reply, CHANNEL))
        self.assertEqual(SYNC.origin_named_root_id(events[1], events[1]["id"]), root["id"])

    def test_not_found_is_exit_one_and_reads_as_a_data_problem(self):
        """L1-GIS-HO-091 未知事件／别的频道：CLI 退出码 1 + JSON 错误；BuzzCliError.returncode == 1（对象级），不是 2+。"""
        error = '{"error":"not_found","message":"event x not found","retryable":false}'
        with self.assertRaises(SYNC.BuzzCliError) as caught:
            self.cli(error, returncode=1).thread("f" * 64)
        self.assertEqual(caught.exception.returncode, 1)


if __name__ == "__main__":
    unittest.main()
