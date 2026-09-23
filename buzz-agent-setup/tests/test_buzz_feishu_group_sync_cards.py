"""Buzz → 飞书发成卡片（engineering/skills#110，references/feishu-group-sync.md「消息卡片」）。

沿用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env / 常量。L1 覆盖配置、CLI 适配器、卡片的纯函数（标题、署名行与链接、折叠、
截断、中和、@）；L2-1 走完整的 round：发送与回复、账本、回退成文字、重试、署名行里频道名的缓存、飞书→Buzz 不受影响。
用例编号从 200 起，避开同一个脚本上其他改动用的编号。
"""

import contextlib
import functools
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unicodedata
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_buzz_feishu_group_sync as base  # noqa: E402
from test_buzz_feishu_group_sync import (  # noqa: E402
    AGENT2_PK, AGENT_APP, AGENT_BOT_MEMBER, AGENT_PK, ALICE_OPEN, ALICE_PK, BOB_OPEN, BOB_PK, CAROL_PK, CHANNEL, CHAT, FGS,
    LARK_CLI, MIRROR_PK, NOW, OWNER_APP, OWNER_PK, T0, Env, FakeWorld, TmpCase, eid, event, fmsg, ts, union_of, write_owner_only,
)


def setUpModule():
    base.setUpModule()  # the memoised Schnorr functions: a round signs and verifies once


def tearDownModule():
    base.tearDownModule()


class CardConfig(TmpCase):
    def test_message_format_is_optional_card_by_default_and_only_card_or_text(self):
        """L1-FGS-200: `message_format` 可以不写，缺省就是 "card"；写了只能是 "card" 或 "text"，大小写不同、空串、null、别的类型一律拒绝，
        不会悄悄退回默认。"""
        env = Env(self.tmp)
        raw = {k: v for k, v in json.loads(env.config.read_text()).items() if k != "message_format"}
        plain = FGS.load_config(write_owner_only(self.tmp / "none.json", json.dumps(raw)))
        self.assertNotIn("message_format", plain)
        self.assertEqual(FGS.message_format(plain), "card")
        for value in ("card", "text"):
            cfg = FGS.load_config(write_owner_only(self.tmp / f"{value}.json", json.dumps(dict(raw, message_format=value))))
            self.assertEqual(FGS.message_format(cfg), value)
        for bad in ("Card", "TEXT", "", None, 1, True, ["card"], "markdown", "post", "interactive"):
            path = write_owner_only(self.tmp / "bad.json", json.dumps(dict(raw, message_format=bad)))
            with self.assertRaises(FGS.GroupSyncError, msg=repr(bad)):
                FGS.load_config(path)


class CardAdapters(unittest.TestCase):
    def test_lark_error_carries_the_type_the_cli_reported(self):
        """L1-FGS-201: CliError 带 lark-cli 报的 error.type（api / validation / network ...），没有就是空串；回退成文字只认服务端
        明确拒绝的 api 与 validation。"""
        def reply(payload, code=1):
            return lambda argv, **kw: base.subprocess.CompletedProcess(argv, code, "", json.dumps(payload))
        for kind in ("api", "validation", "permission", "authentication", "network"):
            runner = reply({"ok": False, "error": {"type": kind, "code": 230099, "subtype": "x"}})
            with self.assertRaises(FGS.CliError) as ctx:
                FGS.LarkCli(LARK_CLI, {}, runner=runner).send(CHAT, "x", "k")
            self.assertEqual(ctx.exception.error_type, kind)
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=reply({"ok": False, "error": {"code": 1}})).send(CHAT, "x", "k")
        self.assertEqual(ctx.exception.error_type, "")

    def test_send_card_and_reply_card_pass_the_json_as_an_interactive_message(self):
        """L1-FGS-202: 卡片走 `--msg-type interactive --content <JSON>`，身份是 bot，幂等键照旧；回复话题带 --reply-in-thread；
        返回飞书消息 id；不带 --text。"""
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return base.subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"message_id": "om_card1", "chat_id": CHAT}}), "")
        cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        card = json.dumps({"schema": "2.0", "body": {"elements": []}}, ensure_ascii=False)
        self.assertEqual(cli.send_card(CHAT, card, "key-1"), "om_card1")
        self.assertEqual(cli.reply_card("om_parent1", card, "key-2"), "om_card1")
        self.assertEqual(calls[0], [LARK_CLI, "im", "+messages-send", "--as", "bot", "--chat-id", CHAT, "--msg-type", "interactive",
                                    "--content", card, "--idempotency-key", "key-1", "--format", "json"])
        self.assertEqual(calls[1], [LARK_CLI, "im", "+messages-reply", "--as", "bot", "--message-id", "om_parent1", "--msg-type",
                                    "interactive", "--content", card, "--reply-in-thread", "--idempotency-key", "key-2", "--format", "json"])
        with self.assertRaises(FGS.CliError) as ctx:  # no message id: the outcome is unknown
            FGS.LarkCli(LARK_CLI, {}, runner=lambda argv, **kw: base.subprocess.CompletedProcess(
                argv, 0, json.dumps({"ok": True, "data": {}}), "")).send_card(CHAT, card, "k")
        self.assertFalse(ctx.exception.definite)

    def test_the_channel_name_comes_from_channels_get_and_a_missing_one_is_empty(self):
        """L1-FGS-203: `buzz channels get --channel <id>` 的 name；对象里没有 name、name 不是字符串，就是空串；命令失败仍是 CliError
        （由 round 接住，不影响发送）。"""
        calls = []

        def runner_for(value, code=0):
            def runner(argv, **kw):
                calls.append(argv)
                return base.subprocess.CompletedProcess(argv, code, json.dumps(value), "")
            return runner
        buzz = lambda value, code=0: FGS.BuzzCli(base.BUZZ_CLI, {}, runner=runner_for(value, code))
        self.assertEqual(buzz({"id": CHANNEL, "name": "naturehood"}).channel_name(CHANNEL), "naturehood")
        self.assertEqual(calls[0], [base.BUZZ_CLI, "channels", "get", "--channel", CHANNEL])
        for value in ({"id": CHANNEL}, {"name": 5}, {"name": None}, [], "naturehood", None):
            self.assertEqual(buzz(value).channel_name(CHANNEL), "", msg=repr(value))
        with self.assertRaises(FGS.CliError):
            buzz({"error": "x"}, code=2).channel_name(CHANNEL)


URL = f"{base.API_ORIGIN}/bind/open?e={eid(1)}&c={CHANNEL}"
NOTE = "（内容过长已截断，完整内容请在 Buzz 中打开）"
BIDI, LSEP, PSEP, ZWSP = chr(0x202E), chr(0x2028), chr(0x2029), chr(0x200B)  # written as chr(): invisible in source
MAX_CARD = 30 * 1024


def plain(text):
    return {"tag": "plain_text", "content": text}


def byline(text="Alice", url=URL):
    """The card's first body row: "speaker · #channel" in small grey plain text, and beside it the small link that opens the
    message in Buzz (only when there is an https link: else the grey line alone)."""
    info = {"tag": "div", "text": {"tag": "plain_text", "content": text, "text_size": "notation", "text_color": "grey"}}
    if url is None:
        return info
    link = {"tag": "markdown", "content": f"[在 Buzz 中打开]({url})", "text_size": "notation"}
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
            "columns": [{"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center", "elements": [info]},
                        {"tag": "column", "width": "auto", "vertical_align": "center", "elements": [link]}]}


def byline_text(card):
    row = card["body"]["elements"][0]
    return (row["columns"][0]["elements"][0] if row["tag"] == "column_set" else row)["text"]["content"]


def link_of(card):
    """The address of the byline's link ("" when the byline has none)."""
    row = card["body"]["elements"][0]
    if row["tag"] != "column_set":
        return ""
    return re.fullmatch(r"\[在 Buzz 中打开\]\((.*)\)", row["columns"][1]["elements"][0]["content"]).group(1)


def folded(card):
    """The whole text in the folded panel (the last element), or None when the card has no panel."""
    last = card["body"]["elements"][-1]
    return last["elements"][0]["content"] if last["tag"] == "collapsible_panel" else None


def markdown(text):
    return {"tag": "markdown", "content": text}


def panel(text, chars):
    return {"tag": "collapsible_panel", "expanded": False, "header": {"title": plain(f"展开全文（{chars} 字）")},
            "elements": [markdown(text)]}


def raw_card(content="hello", speaker="Alice", **kw):
    return FGS.build_message_card(speaker, content, **kw)


def card_of(content="hello", speaker="Alice", **kw):
    return json.loads(raw_card(content, speaker, **kw))


def elements(content="hello", **kw):
    return card_of(content, open_url=kw.pop("open_url", URL), **kw)["body"]["elements"]


TITLE = "T\n"  # a first line for the tests of what follows the title: it becomes the title


def cut_preview(text):
    """The cut primitive (_markdown_prefix, what cuts an oversized folded text) at the old preview's end — 300 characters or 8
    lines, whichever came first — with the ellipsis the old preview put after it: the cases below pinned it down that way."""
    end = min(len("\n".join(text.split("\n")[:8])), 300)
    if end >= len(text):
        return text
    shown, fenced = FGS._markdown_prefix(text, end)
    return shown + ("\n…" if fenced else "…")


def columns(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


@functools.lru_cache(maxsize=None)
def sync_module():
    """gitlab_buzz_sync.py, loaded once: what a card is compared with is what the sync really writes (escapes, header, 🔔 line)."""
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_for_cards", base.TESTS.parent / "scripts" / "gitlab_buzz_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Real GitLab -> Buzz sync messages of the naturehood channel (skills#134): only the people are renamed. The machine header is
# the whole line `[gitlab-notify:v1]…`: the LAST line of a message since 2026-09-18 23:39, the FIRST line of the older ones.
ISSUE_HEADER = "[gitlab-notify:v1][object:issue][type:bug][status:in-progress][state:opened][change:routing][project:1175][issue:6]"
MR_HEADER = ("[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable]"
             "[project:1175][mr:1001][desc:52c734afa834]")
ROOT_TITLE = "[监控整改] rec-engine 可观测性闭环：US 规则未加载及 OTel/日志遗留项"
ROOT_FACTS = ["url: https://gitlab.addx.ai/applications/naturehood/-/issues/6",
              "labels: app/kiwibit,app/viconature,area::observability,code-review,feature::platform,feature::rec-engine,"
              "lifecycle::implementation,priority::p1",
              "assignees: alice", "milestone: -", "description: c006e3cfa457"]
# 2026-09-17: header first, `key: value` long format (the thread root that was back-filled into Feishu on 2026-09-21)
LEGACY_ROOT = "\n".join([ISSUE_HEADER, "title: " + ROOT_TITLE, *ROOT_FACTS])
# 2026-09-17 compact MR format: header first, but no `title:` line
LEGACY_COMPACT = "\n".join([
    "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable][project:1175][mr:951]",
    "!951 feat: REC 告警适配 Grafana 12 并默认暂停 · alice",
    "fix/rec-unified-alerts-staging -> staging · sha f8e5077aabd4 · desc 431699d91fa8",
    "reviewers alice · assignees alice",
    "https://gitlab.addx.ai/applications/naturehood/-/merge_requests/951",
    "unmapped: bob(profile_not_found),carol(profile_not_found)"])
# 2026-09-21: header last, and the `🔔 通知` line (ADR-0012) just above it
MR_LINES = [
    "👀 **可评审** · [!1001 fix: 补齐 KBTV fatal 通知失败告警并核对观测 P1 剩余范围]"
    "(https://gitlab.addx.ai/applications/naturehood/-/merge_requests/1001) · alice",
    "fix/observability-followup-20260921 -> master · sha 2452ceacc039 · reviewers alice · assignees alice",
    "unmapped: bob(profile_not_found),carol(not_channel_member)"]
MR_FACT = "\n".join([*MR_LINES, "", "🔔 通知 @Alice @Bob", MR_HEADER])
MR_VISIBLE = "\n".join(MR_LINES)
MR_TITLE = "👀 可评审 · !1001 fix: 补齐 KBTV f…"
# 2026-09-21, the skill channel: a comment fact on issue #132. The sync escapes `\`, `[` and `]` in a title (gitlab_buzz_sync._md_escape),
# and the headline is a link, so the link text has `\[` in it.
COMMENT_HEADLINE = ("💬 **评论** · [#132 \\[buzz-agent-setup\\] 分析 Workflow 模板的 cron 星期字段按 1=周一 写，relay 实为 1=周日，现网提前一天触发]"
                    "(https://gitlab.addx.ai/engineering/skills/-/issues/132#note_630211) · alice")
COMMENT_HEADER = ("[gitlab-notify:v1][object:issue][type:unknown][status:in-progress][state:opened][change:activity]"
                  "[project:1021][issue:132][note:630211]")
COMMENT_FACT = "\n".join([COMMENT_HEADLINE, "labels skill::buzz-agent-setup · assignees jchen", "by: alice", "", "已定位：relay 的 cron 星期是 1=周日。",
                          COMMENT_HEADER])
COMMENT_TITLE = "💬 评论 · #132 [buzz-agent-setup]…"


class CardLinks(unittest.TestCase):
    def test_the_thread_root_is_read_like_the_bridge_reads_it(self):
        """L1-FGS-204: 话题根事件 id 的取法与 bridge 一致（relaydb.threadRoot）：有 root 标记就是它；一个 e tag 都没有标记时取第一个；
        只有 reply / mention 标记（不知道根是谁）或 id 不是 64 位小写 hex，就没有（不带 t）。"""
        root, reply, other = eid(1), eid(2), eid(3)
        ev = lambda *tags: event(eid(9), ALICE_PK, "x", tags=tags)
        self.assertEqual(FGS.buzz_thread_root(ev(("e", root, "", "root"), ("e", reply, "", "reply"))), root)
        self.assertEqual(FGS.buzz_thread_root(ev(("e", reply, "", "reply"), ("e", root, "", "root"))), root)
        self.assertEqual(FGS.buzz_thread_root(ev(("e", root, "", "root"))), root)
        self.assertEqual(FGS.buzz_thread_root(ev(("e", root))), root)
        self.assertEqual(FGS.buzz_thread_root(ev(("e", other, "", "mention"), ("e", root, "", "root"))), root)
        self.assertIsNone(FGS.buzz_thread_root(ev(("e", reply, "", "reply"))))
        self.assertIsNone(FGS.buzz_thread_root(ev(("e", reply, "", "mention"))))
        self.assertIsNone(FGS.buzz_thread_root(ev()))
        for bad in ("A" * 64, "a" * 63, "g" * 64, "", "0x" + "a" * 62):
            self.assertIsNone(FGS.buzz_thread_root(ev(("e", bad, "", "root"))), msg=bad)
        self.assertIsNone(FGS.buzz_thread_root({"tags": "not a list"}))
        self.assertIsNone(FGS.buzz_thread_root(ev(["e"], "junk", ["e", 5, "", "root"])))

    def test_the_open_link_is_an_https_page_with_ids_only(self):
        """L1-FGS-205: 链接是 `<base>/bind/open?e=<事件>&c=<频道>[&t=<话题根>]`，只有 id：不带频道名、不带签名（s / n），
        页面的签名只保护显示用的频道名。id 必须是 64 位小写 hex / 小写 UUID，base 必须是 https，不合法就没有链接。"""
        self.assertEqual(FGS.open_link(base.API_ORIGIN, eid(1), CHANNEL, None), URL)
        self.assertEqual(FGS.open_link(base.API_ORIGIN, eid(1), CHANNEL, eid(2)), f"{URL}&t={eid(2)}")
        self.assertEqual(FGS.open_link("https://bridge.example.test:8443", eid(1), CHANNEL, None),
                         f"https://bridge.example.test:8443/bind/open?e={eid(1)}&c={CHANNEL}")
        link = FGS.open_link(base.API_ORIGIN, eid(1), CHANNEL, eid(2))
        query = dict(part.split("=") for part in link.split("?")[1].split("&"))
        self.assertEqual(set(query), {"e", "c", "t"})
        for bad_event in ("A" * 64, "a" * 63, "g" * 64, "", eid(1) + "&x=1"):
            self.assertIsNone(FGS.open_link(base.API_ORIGIN, bad_event, CHANNEL, None), msg=bad_event)
        for bad_channel in (CHANNEL.upper(), CHANNEL.replace("-", ""), "", CHANNEL + "&x=1", "general"):
            self.assertIsNone(FGS.open_link(base.API_ORIGIN, eid(1), bad_channel, None), msg=bad_channel)
        for bad_root in ("A" * 64, "a" * 63, "", "z" * 64):
            self.assertIsNone(FGS.open_link(base.API_ORIGIN, eid(1), CHANNEL, bad_root), msg=bad_root)
        for bad_base in ("http://bridge.example.test", "buzz://bridge.example.test", "", "bridge.example.test", "javascript:alert(1)"):
            self.assertIsNone(FGS.open_link(bad_base, eid(1), CHANNEL, None), msg=bad_base)


class CardShape(unittest.TestCase):
    def test_a_short_message_is_a_titled_card_with_a_byline_and_a_link(self):
        """L1-FGS-206: 卡片 JSON 2.0：header 只有标题（消息的第一行）和颜色（人是 blue），没有副标题；原来副标题的「发言人 · #频道」挪进
        正文第一行（灰色小字的署名行）和 config.summary（「发言人 · #频道：内容」）；署名行旁边是「在 Buzz 中打开」的小号文字链接（https），
        没有底部按钮；正文比标题多出内容时，完整内容在默认收起的「展开全文」面板里，只有标题一行就没有面板。"""
        raw = raw_card("hello\nworld", channel="naturehood", open_url=URL)
        self.assertEqual(json.loads(raw), {
            "schema": "2.0", "config": {"summary": {"content": "Alice · #naturehood：hello world"}},
            "header": {"title": plain("hello"), "template": "blue"},
            "body": {"elements": [byline("Alice · #naturehood"), panel("hello\nworld", 11)]}})
        self.assertNotIn('"button"', raw)
        self.assertNotIn("subtitle", raw)
        one_line = card_of("hello", channel="naturehood", open_url=URL)
        self.assertEqual(one_line["header"]["title"], plain("hello"))
        self.assertEqual(one_line["body"]["elements"], [byline("Alice · #naturehood")])  # the one line is the title: nothing to fold

    def test_an_agent_card_is_green_and_a_missing_channel_or_link_leaves_those_parts_out(self):
        """L1-FGS-207: agent 的卡片是 green；没有频道名，署名行（和摘要）就只有发言人；没有链接（id 不合法）署名行就只是那行灰字，卡片照样发
        （正文至少有署名行，卡片不会没有正文）。"""
        card = card_of("done", "helper-agent", agent=True)
        self.assertEqual(card["header"], {"title": plain("done"), "template": "green"})
        self.assertEqual(card["body"]["elements"], [byline("helper-agent", None)])
        self.assertEqual(card["config"]["summary"]["content"], "helper-agent：done")
        self.assertEqual(card_of("hi", channel="")["header"]["template"], "blue")
        self.assertEqual(card_of("done\nmore", "helper-agent", agent=True)["body"]["elements"],
                         [byline("helper-agent", None), panel("done\nmore", 9)])

    def test_the_summary_is_one_plain_line_of_about_sixty_characters(self):
        """L1-FGS-208: 摘要 =「发言人 · #频道：内容前 60 个字符」（没有频道名就是「发言人：…」），纯文本、一行：换行和连续空白压成一个空格，
        超过 60 个字符加「…」，恰好 60 个不加；不含尖括号。"""
        summary = lambda content, speaker="Alice": card_of(content, speaker)["config"]["summary"]["content"]
        self.assertEqual(card_of("hello", channel="naturehood")["config"]["summary"]["content"], "Alice · #naturehood：hello")
        self.assertEqual(summary("hello\n\nworld\t again"), "Alice：hello world again")
        self.assertEqual(summary("a" * 60), "Alice：" + "a" * 60)
        self.assertEqual(summary("a" * 61), "Alice：" + "a" * 60 + "…")
        self.assertEqual(summary("字" * 100), "Alice：" + "字" * 60 + "…")
        self.assertEqual(summary("<at id=all></at> hi"), "Alice：＜at id=all>＜/at> hi")
        self.assertEqual(summary("x" + LSEP + "y\r\nz"), "Alice：x y z")


class CardTitle(unittest.TestCase):
    """L1-FGS-242 ~ 247: 卡片标题 = 消息的第一行（#127），原来的副标题「发言人 · #频道」是正文的署名行。"""

    def head(self, content, speaker="Alice", **kw):
        return card_of(content, speaker, open_url=URL, **kw)["header"]

    def test_the_title_is_the_first_non_empty_line_and_the_byline_is_speaker_and_channel(self):
        """L1-FGS-242: 标题是正文第一个非空行（前面的空行不算）；署名行是「发言人 · #频道」；折叠面板里是完整内容（含标题那一行）；
        人 blue、agent green 不变；摘要是「发言人 · #频道：内容」。"""
        card = card_of("\n\n  first line  \nsecond\nthird", channel="naturehood", open_url=URL)
        self.assertEqual(card["header"], {"title": plain("first line"), "template": "blue"})
        whole = "first line  \nsecond\nthird"
        self.assertEqual(card["body"]["elements"], [byline("Alice · #naturehood"), panel(whole, len(whole))])
        self.assertEqual(card["config"]["summary"]["content"], "Alice · #naturehood：first line second third")
        agent = card_of("done\nbody", "helper-agent", agent=True, channel="naturehood", open_url=URL)
        self.assertEqual(agent["header"], {"title": plain("done"), "template": "green"})
        self.assertEqual(agent["body"]["elements"][0], byline("helper-agent · #naturehood"))

    def test_markdown_marks_of_the_first_line_are_dropped_from_the_title(self):
        """L1-FGS-243: 标题是纯文字：加粗、删除线、斜体、行内代码的记号，标题/引用/列表/任务的行首记号去掉；链接和图片只留文字、
        地址不进标题；不是记号的（标识符里的下划线、`#127`、`2 * 3 * 4`）不动。GitLab 同步的门牌 `🎫 **#N 标题**` 就是这样变成标题的。"""
        for content, title in (("🎫 **#12 修复卡片标题**\nbody", "🎫 #12 修复卡片标题"),
                               ("**!34 Draft: x** · ~~old~~ *new* _em_ `code`\nbody", "!34 Draft: x · old new em code"),
                               ("# Heading\nbody", "Heading"), ("> quoted\nbody", "quoted"), ("- item\nbody", "item"),
                               ("3. third\nbody", "third"), ("- [ ] todo\nbody", "todo"), ("> ## both\nbody", "both"),
                               ("see [the doc](https://e.co/a?b=1) and ![pic](https://e.co/p.png)\nbody", "see the doc and pic"),
                               ("snake_case_name, #127, 2 * 3 * 4\nbody", "snake_case_name, #127, 2 * 3 * 4")):
            self.assertEqual(self.head(content)["title"], plain(title), msg=content)
        self.assertNotIn("https://e.co", self.head("see [the doc](https://e.co/a?b=1)\nbody")["title"]["content"])

    def test_a_gitlab_notify_header_line_a_rule_or_a_fence_never_makes_the_title(self):
        """L1-FGS-244: 首行是 `[gitlab-notify:v1]` header（存量老消息）就取下一个非空行做标题；header 行自己不进卡片（折叠全文里也没有，见
        L1-FGS-249 起，skills#134）；只剩记号的行（分隔线、单独的 `>`、空图片）跳过；首个有内容的行是代码围栏就不从代码里取标题
        （退回发言人）。"""
        old = "[gitlab-notify:v1] kind=issue iid=7\n🎫 **#7 老消息**\nbody"
        card = card_of(old, open_url=URL)
        self.assertEqual(card["header"]["title"], plain("🎫 #7 老消息"))
        self.assertEqual(card["body"]["elements"], [byline(), panel("🎫 **#7 老消息**\nbody", len("🎫 **#7 老消息**\nbody"))])
        self.assertEqual(self.head("---\n>\n![](https://e.co/p.png)\nreal\nbody")["title"], plain("real"))
        fenced = card_of("```python\nprint(1)\n```\nafter", open_url=URL)
        self.assertEqual(fenced["header"], {"title": plain("Alice"), "template": "blue"})
        self.assertEqual(fenced["body"]["elements"], [byline(), panel("```python\nprint(1)\n```\nafter", len("```python\nprint(1)\n```\nafter"))])

    def test_a_message_with_nothing_to_use_as_a_title_falls_back_to_the_speaker(self):
        """L1-FGS-245: 空正文、只有 header、只有记号或空白：不出错，标题退回发言人（署名行照常是「发言人 · #频道」）；没有内容就没有折叠面板，
        只剩记号的内容（分隔线等）仍在面板里；正文至少有署名行，不会是一张没有正文的卡片。"""
        for content in ("", "  \n\t", "[gitlab-notify:v1] kind=issue", "---", ">", "![](https://e.co/p.png)"):
            card = card_of(content, channel="naturehood", open_url=URL)
            self.assertEqual(card["header"], {"title": plain("Alice"), "template": "blue"}, msg=content)
            self.assertEqual(card["body"]["elements"][0], byline("Alice · #naturehood"), msg=content)
        self.assertEqual(card_of("[gitlab-notify:v1] kind=issue", open_url=URL)["body"]["elements"], [byline()])  # the header is not a body
        self.assertEqual(card_of("---", open_url=URL)["body"]["elements"], [byline(), panel("---", 3)])
        self.assertEqual(card_of("hi")["body"]["elements"], [byline("Alice", None)])  # no link, no mention: the byline alone

    def test_a_long_first_line_is_cut_with_an_ellipsis_and_folds_the_whole_line(self):
        """L1-FGS-246: 首行超过 36 列（手机一行放得下）：中文、全角、emoji 算 2 列，其余算 1 列；恰好 36 列的不动，超出的取前面放得下的部分加「…」（「…」按 2 列留位），
        总宽不超过 36 列；被截断的标题说不全那一行，所以只有这一行的消息也有折叠全文；链接地址被标题丢掉的、表格行也一样（全文里是原样的整行）。"""
        exact = "字" * 18
        self.assertEqual(self.head(exact + "\nbody")["title"], plain(exact))
        self.assertEqual(card_of(exact, open_url=URL)["body"]["elements"], [byline()])
        self.assertEqual(card_of(exact + "\nbody", open_url=URL)["body"]["elements"], [byline(), panel(exact + "\nbody", 23)])
        long = "字" * 19
        self.assertEqual(self.head(long + "\nbody")["title"], plain("字" * 17 + "…"))
        self.assertEqual(card_of(long, open_url=URL)["body"]["elements"], [byline(), panel(long, 19)])
        self.assertEqual(self.head("a" * 36)["title"], plain("a" * 36))
        self.assertEqual(self.head("a" * 37)["title"], plain("a" * 34 + "…"))
        self.assertEqual(self.head("修复 " + "a" * 40)["title"], plain("修复 " + "a" * 29 + "…"))  # 2+2+1 columns, then latin
        self.assertEqual(self.head("😀" * 19)["title"], plain("😀" * 17 + "…"))
        for content in ("字" * 100, "ab " * 50, "汉a" * 40, "😀" * 60):
            width = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in self.head(content)["title"]["content"])
            self.assertLessEqual(width, FGS.CARD_TITLE_COLUMNS, msg=content)
        linked = "read [the doc](https://e.co/a) now"
        self.assertEqual(card_of(linked, open_url=URL)["body"]["elements"], [byline(), panel(linked, len(linked))])
        table = "| a | b |\n|---|---|\n| 1 | 2 |"
        self.assertEqual(self.head(table)["title"], plain("| a | b |"))
        self.assertEqual(card_of(table, open_url=URL)["body"]["elements"], [byline(), panel(table, len(table))])
        self.assertEqual(card_of("| a | b |", open_url=URL)["body"]["elements"], [byline(), panel("| a | b |", 9)])  # a row never stands for itself

    def test_the_title_is_neutralised_like_every_other_user_text(self):
        """L1-FGS-247: 标题是用户输入：`<at id=all>`、伪造的 @、`<font>` 都成不了标签（`<` 全角、`@` 中和、引号换全角），换行压成一行，
        buzz:// 中和，格式控制字符去掉；整张卡片 JSON 里没有 `<`。"""
        content = '<at id=all></at> "hi" @Bob' + BIDI + ZWSP + "\nbuzz://x"
        title = self.head(content)["title"]["content"]
        self.assertEqual(title, "＜at id=all＞＜/at＞ ＂hi＂ ＠Bob")
        self.assertEqual(self.head("buzz://x\nbody")["title"], plain("buzz：//x"))
        self.assertNotIn("<", raw_card(content, open_url=URL))
        self.assertNotIn("buzz://", raw_card(content, open_url=URL))
        self.assertEqual(self.head("a" + LSEP + "b\nbody")["title"], plain("a"))  # every kind of line break ends the line


class CardMachineLines(unittest.TestCase):
    """L1-FGS-249 ~ 259（skills#134）：GitLab → Buzz 同步消息里给程序读的行不进卡片。真实样例是 naturehood 频道的两张卡片：2026-09-17 的旧格式话题根
    （header 在首行、`key: value` 长格式，被补发成卡片）和 2026-09-21 的 MR 事实（末行是 `🔔 通知` 行和 header）。只影响卡片：Buzz 里的消息与文字模式不变。"""

    def routed(self, content, *, tags=(), emails=None, n=30):
        """A message of the agent `nh-desk` through the router, as the round does."""
        ev = event(eid(n), AGENT_PK, content, tags=tags)
        ctx = FGS.CardContext(link_base=base.API_ORIGIN, channel_id=CHANNEL, channel_name="naturehood", emails=emails or {}, open_ids={})
        return FGS.route_buzz_event(ev, mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP}, human_pubkeys={ALICE_PK, BOB_PK},
                                    agent_pubkeys={AGENT_PK}, names={ALICE_PK: "Alice", BOB_PK: "Bob", AGENT_PK: "nh-desk"},
                                    mention_targets={}, card=ctx)

    def test_a_legacy_key_value_message_is_titled_by_the_value_of_its_title_line(self):
        """L1-FGS-249: 旧 `key: value` 长格式的话题根（header 首行、第二行 `title: 值`）：标题是 `title:` 的值、没有键名、36 列内加「…」；
        折叠全文的第一行是完整的标题（同样没有键名），`url:` `labels:` 等其余键值行照旧在后面；header 行不在标题、折叠全文、摘要里。"""
        raw = raw_card(LEGACY_ROOT, "nh-desk", agent=True, channel="naturehood", open_url=URL)
        card = json.loads(raw)
        self.assertEqual(card["header"], {"title": plain("[监控整改] rec-engine 可观测性闭环…"), "template": "green"})
        self.assertLessEqual(columns(card["header"]["title"]["content"]), FGS.CARD_TITLE_COLUMNS)
        full = "\n".join([ROOT_TITLE, *ROOT_FACTS])
        self.assertEqual(len(full), 308)
        self.assertEqual(card["body"]["elements"], [byline("nh-desk · #naturehood"), panel(full, 308)])
        self.assertEqual(card["config"]["summary"]["content"], "nh-desk · #naturehood：" + " ".join(full.split())[:60] + "…")
        self.assertEqual(card["config"]["summary"]["content"],
                         "nh-desk · #naturehood：[监控整改] rec-engine 可观测性闭环：US 规则未加载及 OTel/日志遗留项 url: https://g…")
        for machine in ("[gitlab-notify", "title:", "[object:", "[project:"):
            self.assertNotIn(machine, raw)

    def test_a_new_format_sync_message_loses_its_header_and_bell_line_but_keeps_the_cards_own_at_line(self):
        """L1-FGS-250: 2026-09-21 的 MR 事实（末行 header、header 上面一行 `🔔 通知 @… @…`）：标题、署名行、颜色不变；折叠全文只剩三行可读内容——
        header 行、`🔔 通知` 行都没有；卡片自己的 @ 行（来自 p tag）在署名行之后、面板之前；没有 p tag 就没有 @ 行；`unmapped:` 行照常显示；
        文字（文字模式与卡片被拒后的回退）逐字不变，仍带 header。"""
        out = self.routed(MR_FACT, tags=[("p", ALICE_PK), ("p", BOB_PK)], emails={ALICE_PK: "alice@a4x.io"})
        card = json.loads(out.card)
        self.assertEqual(card["header"], {"title": plain(MR_TITLE), "template": "green"})
        self.assertEqual(card["body"]["elements"], [byline("nh-desk · #naturehood", open_url(30)),
                                                    markdown("<at email=alice@a4x.io></at> @Bob"), panel(MR_VISIBLE, len(MR_VISIBLE))])
        self.assertIn("unmapped: bob(profile_not_found),carol(not_channel_member)", folded(card))
        for machine in ("[gitlab-notify", "🔔", "[desc:", "[mr:1001]"):
            self.assertNotIn(machine, out.card)
        self.assertEqual(out.text, MR_FACT)
        no_tags = json.loads(self.routed(MR_FACT).card)
        self.assertEqual(no_tags["body"]["elements"], [byline("nh-desk · #naturehood", open_url(30)), panel(MR_VISIBLE, len(MR_VISIBLE))])

    def test_a_message_of_nothing_but_machine_lines_is_titled_by_the_speaker_and_is_not_an_empty_card(self):
        """L1-FGS-251: 只有 header（前后有空白或空行、或再加一行 `🔔 通知`）的消息：标题退回发言人，正文只有署名行（卡片不空、没有折叠面板），header 不在
        卡片和摘要里；没有链接、没有 @ 时也是一张合法的卡片，不出错。"""
        for content in (MR_HEADER, "  " + ISSUE_HEADER + "  ", "\n\n" + ISSUE_HEADER + "\n\n", "🔔 通知 @Alice\n" + MR_HEADER):
            raw = raw_card(content, channel="naturehood", open_url=URL)
            card = json.loads(raw)
            self.assertEqual(card["header"], {"title": plain("Alice"), "template": "blue"}, msg=content)
            self.assertEqual(card["body"]["elements"], [byline("Alice · #naturehood")], msg=content)
            self.assertNotIn("gitlab-notify", raw, msg=content)
            self.assertNotIn("🔔", raw, msg=content)
        bare = raw_card(ISSUE_HEADER)
        self.assertNotIn("gitlab-notify", bare)
        self.assertEqual(json.loads(bare)["header"]["title"], plain("Alice"))

    def test_the_folded_full_text_has_no_machine_line_and_a_huge_one_still_fits_30_kb(self):
        """L1-FGS-252: 折叠全文里也没有 header 与 `🔔` 行，「展开全文（N 字）」的 N 是去掉之后的字数；旧格式的折叠全文里 `title:` 也没有键名；整张卡片超过
        28 KB 时仍按原规则截断加说明，< 30 KB，里面没有 header。"""
        body = "\n".join(f"第 {n} 行：" + "字" * 20 for n in range(1, 13))
        new = card_of(f"{body}\n\n🔔 通知 @Alice\n{MR_HEADER}", open_url=URL)
        self.assertEqual(new["body"]["elements"][-1], panel(body, len(body)))
        legacy = "标题\nurl: https://gitlab.addx.ai/x/-/issues/1\n" + body
        old = card_of(f"{ISSUE_HEADER}\ntitle: 标题\nurl: https://gitlab.addx.ai/x/-/issues/1\n{body}", open_url=URL)
        self.assertEqual(old["body"]["elements"][-1], panel(legacy, len(legacy)))
        self.assertEqual(old["header"]["title"], plain("标题"))
        for content in (f"{ISSUE_HEADER}\ntitle: 标题\nurl: u\n" + "y" * 40000, "y" * 40000 + f"\n\n🔔 通知 @Alice\n{MR_HEADER}"):
            raw = raw_card(content, open_url=URL)
            self.assertLess(len(raw.encode("utf-8")), MAX_CARD)
            self.assertIn(NOTE, raw)
            self.assertNotIn("gitlab-notify", raw)
            self.assertNotIn("🔔", raw)

    def test_the_summary_never_starts_with_the_header_or_the_key_of_the_title(self):
        """L1-FGS-253: 消息列表里的一行摘要「发言人：内容」不再以 header 开头（旧格式）、也不带 `title:` 键名；新格式的摘要不变。"""
        summary = lambda content: card_of(content, "nh-desk")["config"]["summary"]["content"]
        self.assertEqual(summary(LEGACY_ROOT), "nh-desk：[监控整改] rec-engine 可观测性闭环：US 规则未加载及 OTel/日志遗留项 url: https://g…")
        self.assertEqual(summary(LEGACY_COMPACT), "nh-desk：!951 feat: REC 告警适配 Grafana 12 并默认暂停 · alice fix/rec-unified…")
        self.assertEqual(summary(MR_FACT), "nh-desk：👀 可评审 · !1001 fix: 补齐 KBTV fatal 通知失败告警并核对观测 P1 剩余范围 · alice…")  # plain text: L1-FGS-263

    def test_machine_lines_are_recognised_by_where_they_are_and_never_by_what_they_say(self):
        """L1-FGS-254: 只按结构认：header 只认同步自己认它的两个位置（第一行、最后一个非空行），整行以 `[gitlab-notify:v1]` 开头；正文中间的同名行和句子里
        引用的 header 是作者写的内容，原样留着。旧 `key: value` 长格式只认「header 在首行、第二行是 `title: 值`」：没有 header 的 `title:` 行、header 在末行的
        `title:` 行、header 首行但第二行不是 `title:`（2026-09-17 的紧凑 MR 格式）、第三行的 `title:` 都不动键名。`🔔 通知` 行只在带 header 的消息里去掉。
        （折叠全文就是去掉机器行之后的可读内容，下面按它断言。）"""
        folded_of = lambda content: folded(card_of(content, open_url=URL))
        title_of = lambda content: card_of(content, open_url=URL)["header"]["title"]
        self.assertEqual(title_of("title: 只是一行普通文字\nbody"), plain("title: 只是一行普通文字"))
        self.assertEqual(folded_of("title: 只是一行普通文字\nbody"), "title: 只是一行普通文字\nbody")
        self.assertEqual(title_of(f"title: 末行是 header\nbody\n{MR_HEADER}"), plain("title: 末行是 header"))
        self.assertEqual(folded_of(f"title: 末行是 header\nbody\n{MR_HEADER}"), "title: 末行是 header\nbody")
        self.assertEqual(title_of(LEGACY_COMPACT), plain("!951 feat: REC 告警适配 Grafana 12…"))
        self.assertEqual(folded_of(LEGACY_COMPACT), LEGACY_COMPACT.split("\n", 1)[1])
        third = f"{ISSUE_HEADER}\nfirst\ntitle: 第三行\nlast"
        self.assertEqual(folded_of(third), "first\ntitle: 第三行\nlast")
        quoted = "intro\n[gitlab-notify:v1][object:mr]\n看这一行 [gitlab-notify:v1][object:mr] 是给程序读的\noutro"
        self.assertEqual(folded_of(quoted), quoted)
        human_bell = "hello\n🔔 通知 大家今天下班前提交周报"
        self.assertEqual(folded_of(human_bell), human_bell)
        self.assertEqual(title_of(human_bell), plain("hello"))
        fenced = f"```\n{MR_HEADER}\n```"
        self.assertEqual(folded_of(fenced), fenced)
        # 变异检查补的用例：只认位置和整行，不认「哪一行都行」「行里带着就行」「缩进也行」
        self.assertEqual(folded_of(f"intro\ntitle: 第二行\nbody\n{MR_HEADER}"), "intro\ntitle: 第二行\nbody")
        self.assertEqual(folded_of(f"intro\n[gitlab-notify:v1][object:mr]\noutro\n{MR_HEADER}"), "intro\n[gitlab-notify:v1][object:mr]\noutro")
        sentence = "看这一行 [gitlab-notify:v1][object:mr] 是给程序读的"
        self.assertEqual(folded_of(f"{sentence}\nbody"), f"{sentence}\nbody")
        self.assertEqual(folded_of(f"hello\n{sentence}"), f"hello\n{sentence}")
        self.assertEqual(title_of(f"{ISSUE_HEADER}\n  title: 缩进\nurl: https://gitlab.addx.ai/x/-/issues/1"), plain("title: 缩进"))
        cut = "字" * 30
        self.assertEqual(folded_of(f"{ISSUE_HEADER}\n\n{cut}\n事实"), f"{cut}\n事实")  # no blank line left where the header was

    def test_a_legacy_key_value_message_keeps_its_other_lines_and_folds_the_title_without_its_key(self):
        """L1-FGS-255: 旧长格式（issue：header、title、url、labels、assignees、milestone、description；MR：title、url、branches、sha、labels、reviewers、
        author、unmapped）：标题取 `title:` 的值；折叠全文的第一行也是这个值（没有键名），其余键值行（`url:` 要留着方便点开）和 `unmapped:` 说明照常在后面，
        键名不动（见 L1-FGS-249）。"""
        issue = "\n".join([ISSUE_HEADER, "title: 修复卡片标题", *ROOT_FACTS[:3]])
        card = card_of(issue, open_url=URL)
        self.assertEqual(card["header"]["title"], plain("修复卡片标题"))
        self.assertEqual(folded(card), "\n".join(["修复卡片标题", *ROOT_FACTS[:3]]))
        mr = "\n".join(["[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][project:1175][mr:951]", "title: fix: 补齐告警",
                        "url: https://gitlab.addx.ai/applications/naturehood/-/merge_requests/951", "branches: fix/a -> staging",
                        "sha: f8e5077aabd4", "labels: -", "reviewers: alice", "author: bob", "unmapped: carol(profile_not_found)"])
        card = card_of(mr, open_url=URL)
        self.assertEqual(card["header"]["title"], plain("fix: 补齐告警"))
        self.assertEqual(folded(card), "\n".join(["fix: 补齐告警", *mr.split("\n")[2:]]))
        no_value = "\n".join([ISSUE_HEADER, "title:", "url: https://gitlab.addx.ai/x/-/issues/1"])
        self.assertEqual(card_of(no_value, open_url=URL)["header"]["title"], plain("title:"))  # nothing after the key: not a title line

    def test_the_bell_line_goes_wherever_the_sync_put_it_and_the_at_line_stays_tied_to_the_p_tags(self):
        """L1-FGS-256: `🔔 通知` 行在带 header 的消息里去掉，不论在哪：新格式在 header 上面（或 `note:` / `events:` 行上面）、旧格式（header 首行）里在末尾；
        卡片自己的 @ 行只由 p tag 产生：有 p tag 就在，没有就不在（`🔔` 行不能造出 @ 行，也不因为它被去掉而丢）。"""
        legacy = f"{ISSUE_HEADER}\ntitle: 标题\nurl: https://gitlab.addx.ai/x/-/issues/1\n🔔 通知 @Alice @Bob"
        tagged = FGS.build_message_card("nh-desk", legacy, agent=True, open_url=URL,
                                        mentions=(FGS.CardMention("Alice", "alice@a4x.io"), FGS.CardMention("Bob")))
        readable = "标题\nurl: https://gitlab.addx.ai/x/-/issues/1"
        self.assertEqual(json.loads(tagged)["body"]["elements"], [byline("nh-desk"), markdown("<at email=alice@a4x.io></at> @Bob"),
                                                                 panel(readable, len(readable))])
        self.assertEqual(elements(legacy), [byline(), panel(readable, len(readable))])
        middle = f"事实\n更多\n\n🔔 通知 @Alice\nnote: 5\nevents: a,b\n{MR_HEADER}"
        kept = "事实\n更多\n\nnote: 5\nevents: a,b"
        self.assertEqual(elements(middle), [byline(), panel(kept, len(kept))])
        kept = "事实\n更多 🔔 通知 @Alice"  # only a line that starts with it
        self.assertEqual(elements(f"{kept}\n{MR_HEADER}"), [byline(), panel(kept, len(kept))])

    def test_what_the_sync_writes_today_is_what_the_card_leaves_out(self):
        """L1-FGS-259: 卡片认的机器行和同步现在真写的是同一种：用 gitlab_buzz_sync 自己渲染、加 `🔔 通知` 行的消息（header 末行、上面一行 🔔）做成卡片，
        没有 header、没有 🔔 行，可读的两行还在；两边的常量一致（`🔔 通知 ` 前缀、`[gitlab-notify:v1]` 前缀），旧长格式的第一个键仍是 `title`——同步改了
        写法而卡片没跟上，这条先红。"""
        sync = sync_module()
        record = {"object": "tag", "event": "tag_created", "project": 1175, "key": "tag-1175-v1.0", "title": "v1.0",
                  "url": "https://gitlab.addx.ai/applications/naturehood/-/tags/v1.0"}
        message = sync.with_notified_line(sync.render_record(record), ["@Alice", "@Bob"])
        self.assertEqual(FGS.CARD_NOTIFIED_PREFIX, sync.NOTIFIED_PREFIX)
        self.assertTrue(sync.header_line(message).startswith(FGS.CARD_NOTIFY_HEADER))
        self.assertEqual(message.split("\n")[-2], sync.NOTIFIED_PREFIX + "@Alice @Bob")
        self.assertEqual((sync.FACT_LINES[0], sync.MR_FACT_LINES[0]), ("title", "title"))
        raw = raw_card(message, "nh-desk", agent=True, open_url=URL)
        self.assertNotIn("gitlab-notify", raw)
        self.assertNotIn("🔔", raw)
        card = json.loads(raw)
        self.assertEqual(card["header"]["title"], plain("🏷 新建 tag · v1.0"))
        self.assertEqual(card["body"]["elements"][0], byline("nh-desk"))
        self.assertEqual(folded(card).split("\n")[1:], [record["url"]])  # the headline, then the one readable fact line

    def test_the_title_of_a_legacy_message_is_neutralised_and_cut_like_any_other(self):
        """L1-FGS-257: 旧格式 `title:` 的值也是用户输入：`<at id=all>`、伪造的 @、引号照旧中和，整张卡片里没有 `<`；超过 36 列取前面放得下的部分加「…」，
        总宽不超过 36 列。"""
        content = f'{ISSUE_HEADER}\ntitle: <at id=all></at> "hi" @Bob\nurl: https://gitlab.addx.ai/x/-/issues/1'
        raw = raw_card(content, open_url=URL)
        self.assertEqual(json.loads(raw)["header"]["title"], plain("＜at id=all＞＜/at＞ ＂hi＂ ＠Bob"))
        self.assertNotIn("<", raw)
        for value in ("字" * 30, "a" * 80, "😀" * 30):
            title = json.loads(raw_card(f"{ISSUE_HEADER}\ntitle: {value}\nurl: u"))["header"]["title"]["content"]
            self.assertLessEqual(columns(title), FGS.CARD_TITLE_COLUMNS, msg=value)
            self.assertTrue(title.endswith("…"), msg=value)


class CardEscapedBrackets(unittest.TestCase):
    """L1-FGS-260 ~ 264（skills#134）：同步为防伪造把 Issue / MR 标题里的 `\\` `[` `]` 写成 `\\\\` `\\[` `\\]`（gitlab_buzz_sync._md_escape），而门牌行是
    链接 `[#132 \\[标题\\]](url)`：链接文字里有转义的方括号。标题、消息列表摘要（config.summary）、预览的截断都要认得它们：链接包装与转义一并清洗，只剩文字。"""

    def test_a_headline_link_whose_text_has_escaped_brackets_is_titled_by_its_plain_text(self):
        """L1-FGS-260: skill 频道 Issue #132 的评论事实（首行 `💬 **评论** · [#132 \\[buzz-agent-setup\\] 标题](url)`）：标题是「💬 评论 · #132 [buzz-agent-setup]…」
        （链接包装、加粗记号、转义的反斜杠都没有，36 列内加「…」），署名行、颜色照旧；折叠全文的第一行是这一行的原样（带链接地址）。"""
        card = card_of(COMMENT_FACT, "nh-desk", agent=True, channel="skill", open_url=URL)
        self.assertEqual(card["header"], {"title": plain(COMMENT_TITLE), "template": "green"})
        self.assertEqual(card["body"]["elements"][0], byline("nh-desk · #skill"))
        self.assertLessEqual(columns(COMMENT_TITLE), FGS.CARD_TITLE_COLUMNS)
        self.assertEqual(folded(card).split("\n")[0], COMMENT_HEADLINE)
        for junk in ("\\", "](", "**", "[#132"):
            self.assertNotIn(junk, card["header"]["title"]["content"], msg=junk)

    def test_escaped_brackets_and_backslashes_come_back_as_the_text_they_were(self):
        """L1-FGS-261: 与同步写的精确互逆：用 gitlab_buzz_sync._md_escape 转义各种标题（几对方括号、中间一个 `]`、单个 `[`、反斜杠、结尾的反斜杠、`\\[`、
        里面带 `(c)` 的 `[b](c)`）放进链接，标题就是原来的标题；门牌（加粗里的 `**#9 \\[x\\] y**`）和通知（`· v1\\[rc\\]`）没有链接，转义也一样还原。"""
        sync = sync_module()
        for original in ("[a] b [c]", "a]b", "[", "]", "C:\\dir\\[x]", "x\\", "\\\\", "\\[", "[[a]]", "a [b](c) d", "\\]\\["):
            headline = f"**评论** · [#5 {sync._md_escape(original)}](https://gitlab.addx.ai/x/-/issues/5#note_1)"
            card = card_of(headline + "\nbody", open_url=URL)
            self.assertEqual(card["header"]["title"], plain("评论 · #5 " + original), msg=original)
            self.assertEqual(card["body"]["elements"][-1], panel(headline + "\nbody", len(headline + "\nbody")), msg=original)
        plaque = sync.render_issue_plaque({"issue": 9, "title": "[x] y\\z", "url": "https://gitlab.addx.ai/x/-/issues/9"}, "applications/naturehood")
        self.assertEqual(card_of(plaque)["header"]["title"], plain("📋 #9 [x] y\\z"))
        notice = sync.render_record({"object": "tag", "event": "tag_created", "project": 1, "key": "tag-1-v1", "title": "v1[rc]",
                                     "url": "https://gitlab.addx.ai/x/-/tags/v1"})
        self.assertEqual(card_of(notice)["header"]["title"], plain("🏷 新建 tag · v1[rc]"))

    def test_links_images_marks_and_other_backslashes_are_cleaned_as_before(self):
        """L1-FGS-262: 没有转义的照旧：普通链接、图片、加粗 / 删除线 / 斜体 / 行内代码的记号；`[a]` 后面不是 `(` 的不是链接；反斜杠后面不是 `\\` `[` `]` 的
        （路径 `C:\\dir`、`\\d`、`\\*`）是反斜杠本身，不动；标题被截断时也不留下转义。"""
        for content, title in (("see [the doc](https://e.co/a?b=1) and ![pic](https://e.co/p.png)\nbody", "see the doc and pic"),
                               ("**bold** [a] [b](https://e.co)\nbody", "bold [a] b"),
                               ("**!34 Draft: x** · ~~old~~ *new* _em_ `code`\nbody", "!34 Draft: x · old new em code"),
                               ("C:\\dir\\x and \\d and \\*\nbody", "C:\\dir\\x and \\d and \\*"),
                               ("[a](b) [c](d)\nbody", "a c")):
            self.assertEqual(card_of(content, open_url=URL)["header"]["title"], plain(title), msg=content)
        long = "**评论** · [#5 " + "\\[abc\\] " * 12 + "](https://e.co/a)"
        title = card_of(long, open_url=URL)["header"]["title"]["content"]
        self.assertLessEqual(columns(title), FGS.CARD_TITLE_COLUMNS)
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("\\", title)
        self.assertNotIn("](", title)

    def test_the_summary_is_cleaned_like_the_title(self):
        """L1-FGS-263: 消息列表里的一行摘要（config.summary）走同一套清洗，再取前约 60 个字符：链接只留文字、加粗 / 删除线 / 代码的记号和转义都没有（不再是
        「[#132 \\[buzz-agent-setup…」）；纯文字的照旧；清洗在截断之前，所以不会切出半截记号。"""
        summary = lambda content: card_of(content, "nh-desk")["config"]["summary"]["content"]
        self.assertEqual(summary(COMMENT_FACT), "nh-desk：💬 评论 · #132 [buzz-agent-setup] 分析 Workflow 模板的 cron 星期字段按 1=…")
        self.assertEqual(summary("**bold** [a](https://e.co) `code` ~~x~~ \\[b\\] \\\\"), "nh-desk：bold a code x [b] \\")
        self.assertEqual(summary("hello\n\nworld\t again"), "nh-desk：hello world again")
        self.assertEqual(summary("x" * 58 + " [doc](https://e.co/a/b/c) tail"), "nh-desk：" + "x" * 58 + " d…")
        for content in (COMMENT_FACT, "\n".join(["[#5 \\[a\\] b](https://e.co/x)"] * 6)):
            self.assertNotIn("\\", summary(content))
            self.assertNotIn("](", summary(content))
        # 变异检查补的用例：清洗只做到摘要放得下的那几行（不是整条消息），恰好 60 个字符加一行才算被截断；发言人和频道名是 Buzz 档案里的原文，不是同步转义过的，不还原
        self.assertEqual(summary("a" * 60 + "\nb"), "nh-desk：" + "a" * 60 + "…")
        self.assertEqual(summary("a" * 60), "nh-desk：" + "a" * 60)
        with mock.patch.object(FGS, "_plain_markdown", wraps=FGS._plain_markdown) as cleaned:
            card_of("\n".join(f"line {n} [a](https://e.co)" for n in range(2000)))
        self.assertLess(cleaned.call_count, 30)  # the title's line and the few lines the summary shows, not all 2000
        named = card_of("[#5 \\[a\\] b](https://e.co/x)", "a\\[b\\]", channel="c\\]d")
        self.assertEqual(named["body"]["elements"][0], byline("a\\[b\\] · #c\\]d", None))
        self.assertEqual(named["config"]["summary"]["content"], "a\\[b\\] · #c\\]d：#5 [a] b")

    def test_a_cut_preview_never_leaves_a_link_with_escaped_brackets_or_half_an_escape(self):
        """L1-FGS-264: 截断原语（折叠全文超过卡片上限时用的 _markdown_prefix）在字符数处切断：链接文字里有转义的方括号（`[#132 \\[标题\\] …](url)`）时，
        切在链接文字里、地址里都退回到链接开始之前（宁可少放一点）；切在转义的中间（反斜杠留在末尾、下一个字符是 `[` `]` `\\`）就连这半个转义一起去掉；
        反斜杠后面是别的字符（路径）不动，完整的转义不动；没被截断的完整链接（含转义）原样保留。"""
        preview = cut_preview
        link = "[#132 \\[buzz-agent-setup\\] 分析 Workflow 模板](https://e.co/a)"
        self.assertEqual(preview("x" * 250 + " " + link + " tail"), "x" * 250 + "…")  # the cut is in the address
        self.assertEqual(preview("x" * 270 + " [#132 \\[buzz-agent-setup\\] 分析 Workflow](https://e.co)"), "x" * 270 + "…")  # in the text
        self.assertEqual(preview("x" * 290 + " [a\\] b\\] c](https://e.co) more"), "x" * 290 + "…")  # \] does not close the text
        self.assertEqual(preview("x" * 299 + "\\[a\\] b"), "x" * 299 + "…")
        self.assertEqual(preview("x" * 299 + "\\] b"), "x" * 299 + "…")
        self.assertEqual(preview("x" * 299 + "\\\\ b"), "x" * 299 + "…")
        self.assertEqual(preview("x" * 299 + "\\d more"), "x" * 299 + "\\…")  # a backslash before anything else is a backslash
        self.assertEqual(preview("x" * 298 + "\\[a more"), "x" * 298 + "\\[…")  # a whole escape stays
        self.assertEqual(preview("x" * 297 + "\\\\\\[a more"), "x" * 297 + "\\\\…")  # \\ then \[ cut in the middle: only the half goes
        whole = "x" * 100 + " " + link + " tail"
        self.assertEqual(preview(whole), whole)


class CardPreview(unittest.TestCase):
    def test_before_the_fold_there_is_only_the_byline_and_the_mentions(self):
        """L1-FGS-209: 卡片展开前只有标题、署名行（「发言人 · #频道」+「在 Buzz 中打开」链接）和 @ 行：不再有预览（原来 ≤ 300 个字符、≤ 8 行，
        手机上太长）；正文比标题多出任何内容（第二行、被截断的首行）就放一个默认收起的「展开全文（N 字）」面板，里面是完整内容（含标题那一行）；
        只有标题那一行就没有面板。首尾空白不算。"""
        long = "\n".join(f"line{i}" for i in range(1, 30)) + "\n" + "字" * 400
        els = elements(long, mentions=(FGS.CardMention("Bob", email="bob@a4x.io"),))
        self.assertEqual(els, [byline(), markdown("<at email=bob@a4x.io></at>"), panel(long, len(long))])
        self.assertFalse(els[-1]["expanded"])
        for before_the_fold in els[:-1]:
            self.assertNotIn("line2", json.dumps(before_the_fold, ensure_ascii=False))
        self.assertEqual(elements("hello"), [byline()])
        self.assertEqual(elements("hello\n\n"), [byline()])  # the only line is the title
        self.assertEqual(elements(TITLE + "x"), [byline(), panel(TITLE + "x", 3)])
        self.assertEqual(elements("a" * 37), [byline(), panel("a" * 37, 37)])  # the title had to cut it

    def test_a_cut_never_leaves_a_code_fence_open(self):
        """L1-FGS-210: 截断原语（折叠全文超过卡片上限时用的 _markdown_prefix）在代码围栏中间截断时补上收尾的围栏（``` 或 ~~~，同样的符号、
        不短于开头），「…」另起一行放在围栏外；围栏在截断范围内已经闭合就不动。（按原来预览的上限——300 个字符或 8 行——取切点。）"""
        body = "x = 1\n" * 100
        cut = cut_preview("intro\n```python\n" + body + "```\nafter")
        self.assertEqual(cut, "intro\n```python\n" + "x = 1\n" * 5 + "x = 1" + "\n```\n…")
        tilde = cut_preview("~~~\n" + "x\n" * 100 + "~~~")
        self.assertEqual(tilde, "~~~\n" + "x\n" * 6 + "x" + "\n~~~\n…")
        long_fence = cut_preview("````\n```\ninner\n" + "y\n" * 100 + "````")
        self.assertEqual(long_fence, "````\n```\ninner\n" + "y\n" * 4 + "y" + "\n````\n…")
        closed = "```\nc\n```\n" + "\n".join(f"t{i}" for i in range(7))
        self.assertEqual(cut_preview(closed + "\nmore"), "```\nc\n```\n" + "\n".join(f"t{i}" for i in range(5)) + "…")

    def test_a_cut_never_leaves_a_link_code_span_or_emphasis_half_open(self):
        """L1-FGS-211: 截断原语的字符数切点落在链接、图片、裸 URL、行内代码、加粗的中间时，退回到它开始之前（宁可少放一点）；
        没被截断的完整链接原样保留。"""
        head = "x" * 290
        for tail, kept in [(" [docs](https://example.com/abcdefghijk) tail", head),
                           (" ![pic](https://example.com/abcdefghijk.png) tail", head),
                           (" https://example.com/abcdefghijklmnop tail", head),
                           (" `some code here` tail", head),
                           (" **important text** tail", head),
                           (" ~~struck through~~ tail", head)]:
            self.assertEqual(cut_preview(head + tail), kept + "…", msg=tail)
        self.assertEqual(cut_preview("x" * 295 + "[doc](https://e.co) more"), "x" * 295 + "…")
        whole = "x" * 250 + " [docs](https://e.co) and https://a.co tail tail tail tail tail tail"
        kept = cut_preview(whole)
        self.assertEqual(kept, whole[:300].rstrip() + "…")
        self.assertIn(" [docs](https://e.co) and https://a.co tail", kept)
        lines = "\n".join(["l"] * 7 + ["see [a](https://b.co)", "l9"])
        self.assertEqual(cut_preview(lines), "\n".join(["l"] * 7 + ["see [a](https://b.co)"]) + "…")

    def test_the_full_text_folds_into_a_panel_as_the_markdown_it_is(self):
        """L1-FGS-212: 折叠面板里是完整 markdown，一个字不改（加粗、列表、表格、代码块、链接、引用都保留，`>` 不动）；
        元素顺序：署名行、（@ 行）、面板；没有按钮。"""
        md = ("# 标题\n\n**加粗** 和 `code` 与 [链接](https://example.com/a?b=1&c=2)\n\n- 一\n- 二\n\n> 引用 a > b\n\n"
              "| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('<x>'.replace('<', ''))\n```\n第十二行")
        full = md.replace("<", "＜")
        els = elements(md)
        self.assertEqual([e["tag"] for e in els], ["column_set", "collapsible_panel"])
        self.assertEqual(els[1], panel(full, len(full)))
        self.assertFalse(els[1]["expanded"])
        self.assertIn("**加粗** 和 `code` 与 [链接](https://example.com/a?b=1&c=2)", els[1]["elements"][0]["content"])
        self.assertIn("> 引用 a > b", els[1]["elements"][0]["content"])


class CardNeutralising(unittest.TestCase):
    TAGS = ('<at id=all></at> <at user_id="ou_x">x</at> <at email=a@b.co></at> <font color="red">r</font> '
            '<a href="https://evil.example">click</a> <AT id=all> < at id=all> <person id=ou_x></person> <text_tag>')

    def test_no_user_text_can_become_a_card_tag(self):
        """L1-FGS-213: 用户文字里的 `<` 一律换成全角 `＜`：<at id=all>（@所有人）、伪造的 @、<font>、<a>、<person> 都成不了标签，
        整张卡片 JSON 里（标题、署名行、面板、摘要都算）没有 `<`；markdown 的其余语法原样保留。"""
        for content in (self.TAGS, self.TAGS + "\n" * 9 + "**bold** > quote"):
            raw = raw_card(content, open_url=URL)
            self.assertNotIn("<", raw)
            self.assertIn("＜at id=all>", raw)
        card = card_of("**bold** `c` > quote\n| a | b |", open_url=URL)
        self.assertEqual(folded(card), "**bold** `c` > quote\n| a | b |")
        self.assertEqual(card["header"]["title"], plain("bold c ＞ quote"))

    def test_names_channel_names_and_summary_are_one_clean_line(self):
        """L1-FGS-214: 署名行（原来的副标题）、摘要里的名字和频道名走 _safe_name（去掉格式字符、换行压成空格、尖括号和引号换成全角、@ 中和），
        是 plain_text（名字成不了链接或别的 markdown），不含换行、`<`，长度有上限；名字没有可用字符就是「?」。"""
        raw_name = "Al\nice<at id=all></at>" + BIDI + LSEP + "\"x\" @Bob"
        card = card_of("hi", raw_name, channel="chan\r\nnel<b>" + ZWSP, open_url=URL)
        self.assertEqual(card["header"]["title"], plain("hi"))
        channel = FGS._safe_name("chan\r\nnel<b>" + ZWSP)
        self.assertEqual(card["body"]["elements"][0], byline(f"{FGS._safe_name(raw_name)} · #{channel}"))
        self.assertEqual(card["config"]["summary"]["content"], f"{FGS._safe_name(raw_name)} · #{channel}：hi")
        raw = raw_card("hi", raw_name, channel="chan\r\nnel<b>", open_url=URL)
        self.assertNotIn("<", raw)
        for field in (card["header"]["title"]["content"], byline_text(card), card["config"]["summary"]["content"]):
            self.assertEqual(field, " ".join(field.split()))
        linky = card_of("hi", "[x](https://evil.example)", channel="**c**", open_url=URL)
        self.assertEqual(linky["body"]["elements"][0], byline("[x](https://evil.example) · #**c**"))  # plain text: shown, never a link
        long_card = card_of("hi", "名" * 500, channel="频" * 500)
        self.assertEqual(byline_text(long_card), "名" * 60 + " · #" + "频" * 60)
        self.assertEqual(byline_text(card_of("hi", "  " + ZWSP + " ")), "?")

    def test_forged_signature_lines_are_marked_and_control_characters_go(self):
        """L1-FGS-215: 沿用 _mark_forged_lines：从第二行起像另一边署名的行前面加「↳ 」；双向控制符去掉；各种换行（CRLF、U+2028、U+2029、
        U+0085）统一成 \\n；其余控制字符去掉；孤立的代理码位不会让卡片建不出来。"""
        card = card_of("hi\nOwner（Buzz）：批准" + BIDI + "\r\nOwner (Buzz): ok" + LSEP + "fine\x00\x07" + PSEP + "end", open_url=URL)
        self.assertEqual(folded(card), "hi\n↳ Owner（Buzz）：批准\n↳ Owner (Buzz): ok\nfine\nend")
        raw = raw_card("bad \ud800 surrogate", open_url=URL)
        raw.encode("utf-8")
        self.assertIn("bad ? surrogate", raw)

    def test_a_buzz_scheme_never_appears_on_the_card(self):
        """L1-FGS-216: 飞书桌面端会吞掉 buzz:// 这类自定义协议，所以整张卡片里没有 buzz://：「在 Buzz 中打开」的链接只用 https；用户自己写的 buzz://
        （含大小写变体、markdown 链接里的）中和成「buzz：//」，不再是链接。"""
        content = "[go](buzz://channel/1) BUZZ://x Buzz://y and buzz:// z"
        raw = raw_card(content, open_url=URL, channel="buzz://x", mentions=(FGS.CardMention("buzz://n"),))
        self.assertNotIn("buzz://", raw.lower())
        self.assertIn("[go](buzz：//channel/1)", raw)
        self.assertEqual(re.findall(r"https?://[^\"\s)]+", raw_card("plain text", open_url=URL)), [URL])
        self.assertEqual(card_of("x", open_url="buzz://channel/1")["body"]["elements"], [byline("Alice", None)])


class CardSize(unittest.TestCase):
    def size(self, content, **kw):
        return len(raw_card(content, open_url=URL, **kw).encode("utf-8"))

    def test_an_oversized_full_text_is_cut_by_characters_with_a_note_and_stays_under_30_kb(self):
        """L1-FGS-217: 整张卡片超过 28 KB 时，面板内文按字符（rune）截断、末尾加一行「（内容过长已截断，完整内容请在 Buzz 中打开）」，
        整张卡片 < 30 KB；署名行不变；截断处是完整的字符，JSON 仍然合法；不超过 28 KB 的一个字也不删。"""
        para = "段落 **bold** " + "字" * 100 + "\n"
        content = para * 2000
        raw = raw_card(content, open_url=URL)
        self.assertLess(len(raw.encode("utf-8")), MAX_CARD)
        self.assertGreater(len(raw.encode("utf-8")), 26 * 1024)
        els = json.loads(raw)["body"]["elements"]
        self.assertEqual([e["tag"] for e in els], ["column_set", "collapsible_panel"])
        text = els[1]["elements"][0]["content"]
        self.assertTrue(text.endswith("\n\n" + NOTE))
        kept = text[: -len("\n\n" + NOTE)]
        self.assertTrue(content.replace("<", "＜").startswith(kept))
        self.assertEqual(els[0], byline())
        self.assertEqual(els[1]["header"]["title"]["content"], f"展开全文（{len(content.strip())} 字）")
        # the boundary: the largest content that still fits is untouched, one character more is cut
        cut = lambda n: NOTE in raw_card("a" * n, open_url=URL)
        lo, hi = 300, 40000  # lo is not cut, hi is
        self.assertFalse(cut(lo))
        self.assertTrue(cut(hi))
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if cut(mid) else (mid, hi)
        self.assertEqual(self.size("a" * lo), 28 * 1024)  # exactly the trim size still fits, one byte more is cut
        self.assertLessEqual(self.size("a" * hi), 28 * 1024)

    def test_escapes_cannot_push_a_card_over_the_limit_and_an_open_fence_is_closed_before_the_note(self):
        """L1-FGS-218: 每个字符在 JSON 里占几个字节不一样（引号、换行转义成两个）：按卡片实际的字节数截断，任何内容都 < 30 KB；
        截断处落在代码围栏里时先补收尾的围栏再写那行说明；名字、署名行里的频道名、@ 行都有上限，撑不大卡片。"""
        for content in ('"' * 200000, "\n" * 5000 + "x", "\\" * 100000, "😀" * 60000, "ab\n" * 50000):
            self.assertLess(self.size(content), MAX_CARD, msg=content[:6])
        panels = [e for e in json.loads(raw_card("```\n" + "x\n" * 100000 + "```", open_url=URL))["body"]["elements"]
                  if e["tag"] == "collapsible_panel"]
        self.assertEqual(len(panels), 1)
        fenced = panels[0]["elements"][0]["content"]
        self.assertTrue(fenced.endswith("\n```\n\n" + NOTE))
        self.assertEqual(sum(1 for line in fenced.split("\n") if line == "```") % 2, 0)
        many = tuple(FGS.CardMention("n" * 500, email=f"{'e' * 60}{i}@{'d' * 200}.example") for i in range(500))
        self.assertLess(self.size("x" * 400000, speaker="名" * 100000, channel="频" * 100000, mentions=many), MAX_CARD)


class CardMentions(unittest.TestCase):
    def line(self, *mentions, content="T\nhi"):
        els = elements(content, mentions=tuple(mentions))
        self.assertEqual(els[0], byline())
        return els

    def test_a_mention_is_an_address_or_an_open_id_or_plain_text_in_that_order(self):
        """L1-FGS-219: 被提及者在正文末尾单独一行：邮箱已知 → `<at email=…></at>`；否则 open_id 已知 → `<at id=ou_…></at>`；
        都没有 → 不通知的纯文字 `@名字`。这一行是署名行之后、面板之前的一个独立 markdown。"""
        els = self.line(FGS.CardMention("Bob", email="bob@a4x.io", open_id="ou_bob1"),
                        FGS.CardMention("Carol", open_id="ou_carol1"),
                        FGS.CardMention("Dave"))
        self.assertEqual(els, [byline(), markdown("<at email=bob@a4x.io></at> <at id=ou_carol1></at> @Dave"), panel("T\nhi", 4)])
        long_els = self.line(FGS.CardMention("Dave"), content="\n".join(["l"] * 13))
        self.assertEqual([e["tag"] for e in long_els], ["column_set", "markdown", "collapsible_panel"])
        self.assertEqual(long_els[1], markdown("@Dave"))
        self.assertEqual(self.line(), [byline(), panel("T\nhi", 4)])
        self.assertEqual(self.line(FGS.CardMention("Dave"), content="hi"), [byline(), markdown("@Dave")])

    def test_a_union_id_or_anything_odd_is_never_put_in_an_at(self):
        """L1-FGS-220: 卡片里 `<at id=on_…>`（union_id）会被飞书拒绝（230099），所以绝不出现：不是 `ou_…` 的 id 一律当作没有；
        形状不对的邮箱（大写、尖括号、空白、引号）也当作没有，退到 open_id 或纯文字；名字先清洗，`@名字` 里不能有 `<`。"""
        els = self.line(FGS.CardMention("U", open_id="on_" + "a" * 32), FGS.CardMention("V", open_id="ou_x y"),
                        FGS.CardMention("W", email="Bob@A4X.io", open_id="ou_w1"),
                        FGS.CardMention("X", email="a@b.co><at id=all>", open_id="ou_x1"),
                        FGS.CardMention("Y", email="a b@c.co"),
                        FGS.CardMention("Eve<at id=all></at>\n@Bob"))
        text = els[1]["content"]
        self.assertEqual(text, "@U @V <at id=ou_w1></at> <at id=ou_x1></at> @Y @Eve＜at id=all＞＜/at＞ ＠Bob")
        self.assertNotIn("<at id=on_", text)
        self.assertNotIn("\n", text)

    def test_at_most_twenty_mentions_go_on_the_line(self):
        """L1-FGS-221: 一条消息 p tag 再多，@ 行最多 20 个（其余不写），卡片不会被 p tag 撑大。"""
        many = [FGS.CardMention(f"n{i}", open_id=f"ou_p{i}") for i in range(50)]
        text = self.line(*many)[1]["content"]
        self.assertEqual(text, " ".join(f"<at id=ou_p{i}></at>" for i in range(20)))


class CardRouting(unittest.TestCase):
    """route_buzz_event 带上 CardContext：同一条 Buzz 事件，谁来发不变，多出来的是卡片；文字仍然备着（回退用）。"""

    def ctx(self, **kw):
        args = dict(link_base=base.API_ORIGIN, channel_id=CHANNEL, channel_name="naturehood", emails={}, open_ids={})
        args.update(kw)
        return FGS.CardContext(**args)

    def route(self, ev, card="ctx", **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", BOB_PK: "Bob", CAROL_PK: "Carol", AGENT_PK: "helper-agent"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")},
                    card=self.ctx() if card == "ctx" else card)
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def card(self, out):
        self.assertIsNotNone(out.card)
        return json.loads(out.card)

    def test_a_person_is_a_blue_card_from_the_owner_bot_and_the_text_stays_as_the_fallback(self):
        """L1-FGS-222: 人的发言：owner bot 发，卡片标题是消息第一行、署名行是「他的名字 · #频道名」（没有「（Buzz）：」前缀）、blue；「在 Buzz 中打开」是
        https 链接（事件 id、频道 id、话题根）；同一个 Outbound 里的文字与没有卡片时逐字节相同，回退成文字时就发它。"""
        ev = event(eid(1), ALICE_PK, "hello\nworld", tags=[("e", eid(5), "", "root"), ("e", eid(6), "", "reply")])
        out = self.route(ev)
        self.assertEqual((out.via_app_id, out.parent_event_id), (None, eid(6)))
        self.assertEqual(out.text, self.route(ev, card=None).text)
        self.assertEqual(out.text, "Alice（Buzz）：hello\nworld")
        self.assertEqual(self.card(out), {
            "schema": "2.0", "config": {"summary": {"content": "Alice · #naturehood：hello world"}},
            "header": {"title": plain("hello"), "template": "blue"},
            "body": {"elements": [byline("Alice · #naturehood", f"{URL}&t={eid(5)}"), panel("hello\nworld", 11)]}})
        self.assertIsNone(self.route(ev, card=None).card)

    def test_an_agent_is_a_green_card_from_its_own_bot(self):
        """L1-FGS-223: agent 的发言：它自己的 bot 发，标题是消息第一行、署名行是「它的名字 · #频道名」，green；文字仍是原文。"""
        out = self.route(event(eid(2), AGENT_PK, "done\nall"))
        self.assertEqual((out.via_app_id, out.text), (AGENT_APP, "done\nall"))
        card = self.card(out)
        self.assertEqual(card["header"], {"title": plain("done"), "template": "green"})
        self.assertEqual(card["body"]["elements"], [byline("helper-agent · #naturehood", f"{base.API_ORIGIN}/bind/open?e={eid(2)}&c={CHANNEL}"),
                                                    panel("done\nall", 8)])

    def test_the_speaker_is_cleaned_and_falls_back_to_the_pubkey_prefix(self):
        """L1-FGS-224: 发言人的名字先清洗（尖括号、换行、@），没有名字就用 pubkey 前 12 位——人和 agent 都一样；发言人在署名行里；没有频道名署名行就只有发言人。"""
        out = self.route(event(eid(3), BOB_PK, "x"), names={BOB_PK: "Bo\nb<at id=all>"}, card=self.ctx(channel_name=""))
        card = self.card(out)
        self.assertEqual(card["header"], {"title": plain("x"), "template": "blue"})
        self.assertEqual(card["body"]["elements"], [byline(FGS._safe_name("Bo\nb<at id=all>"), open_url(3))])
        self.assertNotIn("<", out.card)
        self.assertEqual(byline_text(self.card(self.route(event(eid(4), BOB_PK, "x"), names={}))), f"{BOB_PK[:12]} · #naturehood")
        self.assertEqual(byline_text(self.card(self.route(event(eid(5), AGENT_PK, "x"), names={}))), f"{AGENT_PK[:12]} · #naturehood")

    def test_mentions_are_an_address_or_an_open_id_or_a_plain_name(self):
        """L1-FGS-225: p tag → 卡片里的 @：邮箱已知用邮箱，否则用 owner 应用的 open_id（人或 agent bot 的成员 id），都没有就是不通知的
        纯文字 @名字；发言人自己和重复的不算；映射不到的人也写上名字（不再悄悄丢掉）；文字回退里的 <at user_id=…> 不变。"""
        ev = event(eid(6), BOB_PK, "@all", tags=[("p", ALICE_PK), ("p", AGENT_PK), ("p", CAROL_PK), ("p", ALICE_PK), ("p", BOB_PK),
                                                 ("p", OWNER_PK)])
        ctx = self.ctx(emails={ALICE_PK: "alice@a4x.io", BOB_PK: "bob@a4x.io"},
                       open_ids={ALICE_PK: ALICE_OPEN, AGENT_PK: AGENT_BOT_MEMBER})
        out = self.route(ev, card=ctx, names={BOB_PK: "Bob", ALICE_PK: "Alice", CAROL_PK: "Carol", AGENT_PK: "helper-agent"})
        self.assertEqual(self.card(out)["body"]["elements"][1],
                         markdown(f"<at email=alice@a4x.io></at> <at id={AGENT_BOT_MEMBER}></at> @Carol @{OWNER_PK[:12]}"))
        self.assertEqual(out.text, f'Bob（Buzz）：@all <at user_id="{ALICE_OPEN}">Alice</at> <at user_id="{AGENT_BOT_MEMBER}">helper-agent</at>')
        for text in (out.card,):
            self.assertNotIn("<at id=on_", text)
            self.assertNotIn('user_id="', text)

    def test_an_agents_own_bot_never_carries_open_ids_of_the_owners_app(self):
        """L1-FGS-226: open_id 按应用隔离：agent 自己的 bot 发的卡片里，owner 应用的 open_id 不能用，只有邮箱（各应用通用）或纯文字。"""
        ev = event(eid(7), AGENT_PK, "@Alice @Bob 已完成", tags=[("p", ALICE_PK), ("p", BOB_PK)])
        ctx = self.ctx(emails={ALICE_PK: "alice@a4x.io"}, open_ids={ALICE_PK: ALICE_OPEN, BOB_PK: BOB_OPEN})
        out = self.route(ev, card=ctx)
        self.assertEqual(self.card(out)["body"]["elements"][1], markdown("<at email=alice@a4x.io></at> @Bob"))
        self.assertNotIn("ou_", out.card)

    def test_the_link_needs_valid_ids_and_a_missing_one_only_drops_the_link(self):
        """L1-FGS-227: 「在 Buzz 中打开」的链接只在事件 id、频道 id（和话题根）都合法时才有；话题根从 e tag 的 root 标记取，只有 reply 标记就不带 t；
        id 不合法署名行就没有链接、不失败，卡片照发。"""
        reply_only = self.route(event(eid(8), ALICE_PK, "x", tags=[("e", eid(6), "", "reply")]))
        self.assertEqual(self.card(reply_only)["body"]["elements"], [byline("Alice · #naturehood", f"{base.API_ORIGIN}/bind/open?e={eid(8)}&c={CHANNEL}")])
        no_root = self.card(self.route(event(eid(9), ALICE_PK, "x")))
        self.assertEqual(link_of(no_root), open_url(9))
        for bad in (dict(channel_id="general"), dict(link_base="http://bridge.example.test"), dict(link_base="")):
            out = self.route(event(eid(10), ALICE_PK, "x"), card=self.ctx(**bad))
            self.assertEqual(self.card(out)["body"]["elements"], [byline("Alice · #naturehood", None)], msg=bad)
        bad_event = dict(event(eid(11), ALICE_PK, "x"), id="NOT-HEX")
        self.assertEqual(self.card(self.route(bad_event))["body"]["elements"], [byline("Alice · #naturehood", None)])

    def test_card_text_is_neutralised_and_the_skips_are_the_same(self):
        """L1-FGS-228: 卡片里 Buzz 正文的 `<` 全部中和（<at id=all> 成不了 @所有人）；谁来发、跳过哪些（回声、非消息 kind、空正文、
        没有 bot 的 agent、不在频道里的作者）与文字模式完全一致。"""
        raw = '<at id=all></at> <AT user_id="ou_x">x</AT> hi'
        for author in (ALICE_PK, AGENT_PK):
            out = self.route(event(eid(12), author, raw))
            self.assertIsNotNone(out.card)
            self.assertNotIn("<", out.card)
            self.assertIn("＜at id=all>", out.card)
        for ev, want in ((event(eid(13), MIRROR_PK, "x"), "echo"), (event(eid(14), ALICE_PK, "x", kind=40008), "kind"),
                         (event(eid(15), ALICE_PK, "  "), "empty"), (event(eid(16), AGENT2_PK, "x"), "agent_bot_unavailable"),
                         (event(eid(17), CAROL_PK, "x"), "not_channel_human")):
            self.assertEqual(self.route(ev), want)
            self.assertEqual(self.route(ev, card=None), want)


def open_url(n, root=None):
    return f"{base.API_ORIGIN}/bind/open?e={eid(n)}&c={CHANNEL}" + (f"&t={eid(root)}" if root else "")


def key_of(n):
    return "b2f-" + hashlib.sha256(eid(n).encode()).hexdigest()[:40]


class CardFake(TmpCase):
    """FakeWorld 扮演的飞书对卡片的态度，照实测：这些是后面所有 round 用例的前提。"""

    def send(self, w, card, key="k1", *, reply_to=None):
        content = card if isinstance(card, str) else json.dumps(card, ensure_ascii=False)
        args = ["im", "+messages-reply" if reply_to else "+messages-send", "--as", "bot",
                *(["--message-id", reply_to, "--reply-in-thread"] if reply_to else ["--chat-id", CHAT]),
                "--msg-type", "interactive", "--content", content, "--idempotency-key", key, "--format", "json"]
        result = w([LARK_CLI, *args])
        return json.loads(result.stdout or result.stderr)

    def doc(self, markdown_text):
        return {"schema": "2.0", "header": {"title": plain("t"), "template": "blue"},
                "body": {"elements": [markdown(markdown_text)]}}

    def test_feishu_takes_open_id_email_and_all_but_refuses_a_union_id_with_230099(self):
        """L2-1-FGS-200: 卡片里 `<at id=ou_…>`、`<at email=…>` 被接受；`<at id=on_…>`（union_id）被拒：code 230099、type api、
        stderr 里的 JSON、消息里有 100290（invalid user resource）；被拒的卡片没有发出去。"""
        w = FakeWorld(self.tmp)
        for good in ("<at id=ou_x1></at>", "<at email=a@b.co></at>", "<at id=all></at>", "plain"):
            self.assertTrue(self.send(w, self.doc(good), key=good)["ok"], msg=good)
        before = len(w.messages)
        answer = self.send(w, self.doc("hi <at id=on_" + "a" * 32 + "></at>"), key="bad")
        self.assertEqual((answer["ok"], answer["error"]["type"], answer["error"]["code"]), (False, "api", 230099))
        self.assertIn("100290", answer["error"]["message"])
        self.assertEqual(len(w.messages), before)

    def test_feishu_refuses_a_card_of_30_kb_and_lark_cli_refuses_a_bad_json(self):
        """L2-1-FGS-201: 30 KB 及以上的卡片被拒（230099，api）、不足 30 KB 的收；--content 不是 JSON 是 lark-cli 自己的 validation 错误；
        interactive 没有 --content 也是；结构不对的卡片（没有 schema 2.0 / 标题 / body.elements）被拒。"""
        w = FakeWorld(self.tmp)
        fits = self.doc("x")
        fits["pad"] = ""
        size = len(json.dumps(fits, ensure_ascii=False).encode())
        fits["pad"] = "p" * (MAX_CARD - 1 - size)
        self.assertEqual(len(json.dumps(fits, ensure_ascii=False).encode()), MAX_CARD - 1)
        self.assertTrue(self.send(w, fits, key="fits")["ok"])
        fits["pad"] += "p"
        answer = self.send(w, fits, key="toobig")
        self.assertEqual((answer["error"]["type"], answer["error"]["code"]), ("api", 230099))
        self.assertEqual(self.send(w, "{not json", key="bad")["error"]["type"], "validation")
        result = w([LARK_CLI, "im", "+messages-send", "--as", "bot", "--chat-id", CHAT, "--msg-type", "interactive",
                    "--idempotency-key", "nc", "--format", "json"])
        self.assertEqual(json.loads(result.stderr)["error"]["type"], "validation")
        for broken in ({}, {"schema": "1.0", "header": {"title": plain("t")}, "body": {"elements": []}},
                       {"schema": "2.0", "header": {"title": plain("")}, "body": {"elements": []}},
                       {"schema": "2.0", "header": {"title": plain("t")}, "body": {}}):
            self.assertEqual(self.send(w, broken, key="shape")["error"]["code"], 230099, msg=broken)

    def test_a_card_is_deduplicated_by_key_per_endpoint_for_an_hour_and_lands_as_an_app_message(self):
        """L2-1-FGS-202: 卡片的幂等键和文字一样按接口计一小时：同一个键、同一个接口只发一次；同一个键换接口（send / reply）是两条；
        一小时之后是新的一条。发出的卡片在飞书里是 sender_type=app、msg_type=interactive，卡片里的 <at id=ou_…> 是提及。"""
        w = FakeWorld(self.tmp)
        first = self.send(w, self.doc("<at id=ou_x1></at> hi"))["data"]["message_id"]
        self.assertEqual(self.send(w, self.doc("<at id=ou_x1></at> hi"))["data"]["message_id"], first)
        self.assertEqual(len(w.messages), 1)
        reply = self.send(w, self.doc("again"), reply_to=first)["data"]["message_id"]
        self.assertNotEqual(reply, first)
        w.clock = NOW + timedelta(hours=1, minutes=1)
        self.assertNotEqual(self.send(w, self.doc("hi"))["data"]["message_id"], first)
        message = w.messages[0]
        self.assertEqual((message["sender"]["sender_type"], message["msg_type"]), ("app", "interactive"))
        self.assertEqual([m["id"] for m in message["mentions"]], ["ou_x1"])
        self.assertEqual(w.threads[first][0]["msg_type"], "interactive")


class CardRound(TmpCase):
    """一整轮：Buzz → 飞书发成卡片。"""

    def world(self):
        return base.RoundIdentity.world(self)

    def one(self, content="hello", author=ALICE_PK, **kw):
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), author, content, **kw)]
        return w

    def env(self, **kw):
        return Env(self.tmp, message_format="card", **kw)

    def at(self, minutes):
        return NOW + timedelta(minutes=minutes)

    @contextlib.contextmanager
    def fresh(self):
        """A new, empty sandbox (world, config, state) for the next scenario of a loop."""
        with tempfile.TemporaryDirectory() as name:
            previous, self.tmp = self.tmp, Path(name)
            os.chmod(self.tmp, 0o700)
            try:
                yield
            finally:
                self.tmp = previous

    def test_every_message_is_said_as_a_card_by_the_same_bot_as_before(self):
        """L2-1-FGS-203: card 模式：每条被镜像的 Buzz 消息一张卡片（不再有 --text）。人的发言经 owner 应用 bot、标题是他的名字、blue、
        没有「（Buzz）：」；agent 的经它自己的 profile、green；话题回复用 +messages-reply --reply-in-thread 挂到父消息；@ 按
        邮箱 / open_id / 纯文字；幂等键与文字模式同一个规则；账本里是飞书消息 id；cards_sent 计数，不触发 needs_attention。"""
        w = self.world()
        env = self.env()
        report = env.round(w)
        cards = w.card_sends()
        self.assertEqual(len(w.lark_sends()), 3)
        self.assertEqual(len(cards), 3)
        self.assertTrue(all("--text" not in c["args"] for c in w.lark_sends()))
        human, agent, reply = cards
        self.assertEqual((human["app"], human["endpoint"], human["key"]), (OWNER_APP, "send", key_of(1)))
        self.assertNotIn("LARKSUITE_CLI_CONFIG_DIR", human["call"]["env"])
        self.assertEqual(human["card"], {
            "schema": "2.0", "config": {"summary": {"content": "Alice · #naturehood：hello from buzz"}},
            "header": {"title": plain("hello from buzz"), "template": "blue"},
            "body": {"elements": [byline("Alice · #naturehood", open_url(1))]}})
        self.assertEqual((agent["app"], agent["key"]), (AGENT_APP, key_of(2)))
        self.assertEqual(agent["call"]["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(agent["card"]["header"]["template"], "green")
        self.assertEqual(agent["card"]["header"]["title"], plain("＠Alice 已完成"))
        self.assertEqual(agent["card"]["body"]["elements"], [byline("helper-agent · #naturehood", open_url(2)), markdown("<at email=alice@a4x.io></at>")])
        root_mid = w.sent_keys[human["key"]]
        self.assertEqual((reply["app"], reply["endpoint"], reply["key"]), (OWNER_APP, "reply", key_of(3)))
        self.assertEqual(reply["call"]["args"][reply["call"]["args"].index("--message-id") + 1], root_mid)
        self.assertIn("--reply-in-thread", reply["call"]["args"])
        self.assertEqual(reply["card"]["header"]["title"], plain("＠helper-agent 看下"))
        self.assertEqual(reply["card"]["body"]["elements"], [byline("Bob · #naturehood", open_url(3)), markdown(f"<at id={AGENT_BOT_MEMBER}></at>")])
        state = env.state()
        self.assertEqual(state["b2f"][eid(1)], root_mid)
        self.assertEqual(state["threads"].get(root_mid), ts(NOW))
        self.assertEqual((report["to_feishu"], report["cards_sent"], report["cards_fallback_text"], report["errors"]), (3, 3, 0, 0))
        self.assertFalse(FGS.needs_attention(report))
        self.assertEqual(env.round(w, now=self.at(1))["cards_sent"], 0)  # the second round sends nothing again
        self.assertEqual(len(w.lark_sends()), 3)

    def test_a_synced_message_reaches_feishu_without_its_machine_lines(self):
        """L2-1-FGS-225: 一整轮：同步进 Buzz 的旧格式话题根（header 首行、`key: value`）和新格式 MR 事实（`🔔 通知` 行、末行 header）发到飞书的卡片里，没有
        header、没有 `🔔` 行、标题是可读的标题；新格式的 p tag 仍然是卡片里的 @ 行；账本和计数照旧（一条消息一张卡片、幂等键不变）。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), AGENT_PK, LEGACY_ROOT), event(eid(2), AGENT_PK, MR_FACT, tags=[("p", ALICE_PK)])]
        report = self.env().round(w)
        legacy, fact = w.card_sends()
        self.assertEqual((legacy["key"], fact["key"]), (key_of(1), key_of(2)))
        self.assertEqual(legacy["card"]["header"]["title"], plain("[监控整改] rec-engine 可观测性闭环…"))
        self.assertEqual(fact["card"]["header"]["title"], plain(MR_TITLE))
        self.assertEqual(fact["card"]["body"]["elements"][1:], [markdown("<at email=alice@a4x.io></at>"), panel(MR_VISIBLE, len(MR_VISIBLE))])
        for sent in (legacy, fact):
            raw = json.dumps(sent["card"], ensure_ascii=False)
            self.assertNotIn("gitlab-notify", raw)
            self.assertNotIn("🔔", raw)
        self.assertEqual((report["to_feishu"], report["cards_sent"], report["cards_fallback_text"], report["errors"]), (2, 2, 0, 0))

    def test_text_is_still_one_setting_away_and_the_default_is_a_card(self):
        """L2-1-FGS-204: message_format "text" 发的和以前逐字节一样（--text、同一个幂等键、不取频道名、cards_sent 为 0）；配置里不写这个键
        就是 card。"""
        w = self.world()
        report = Env(self.tmp, message_format="text").round(w)
        sends = w.lark_sends()
        self.assertEqual([base.text_of(c) for c in sends],
                         ["Alice（Buzz）：hello from buzz", "@Alice 已完成",
                          f'Bob（Buzz）：@helper-agent 看下 <at user_id="{AGENT_BOT_MEMBER}">helper-agent</at>'])
        self.assertEqual([c["args"][c["args"].index("--idempotency-key") + 1] for c in sends], [key_of(1), key_of(2), key_of(3)])
        self.assertEqual(w.channel_gets(), [])
        self.assertEqual((report["cards_sent"], report["cards_fallback_text"]), (0, 0))
        self.assertEqual(w.card_sends(), [])
        with self.fresh():
            default = self.one("hi")
            env = Env(self.tmp)
            raw = {k: v for k, v in json.loads(env.config.read_text()).items() if k != "message_format"}
            write_owner_only(env.config, json.dumps(raw))
            default_report = env.round(default)
            self.assertEqual(len(default.card_sends()), 1)
            self.assertEqual(default_report["cards_sent"], 1)

    def test_the_channel_name_is_asked_for_once_per_round_and_only_when_a_card_needs_it(self):
        """L2-1-FGS-205: 署名行（原来的副标题）的频道名：一轮最多 `channels get` 一次（缓存在内存里，多张卡片共用），一轮没有卡片要发就一次也不取；
        名字里的尖括号、换行被清洗。"""
        w = self.world()
        w.channel_name = "chan<at id=all>\nx"
        env = self.env()
        env.round(w)
        self.assertEqual(len(w.channel_gets()), 1)
        channel = FGS._safe_name("chan<at id=all>\nx")
        self.assertEqual([byline_text(c["card"]) for c in w.card_sends()],
                         [f"{who} · #{channel}" for who in ("Alice", "helper-agent", "Bob")])
        env.round(w, now=self.at(1))
        self.assertEqual(len(w.channel_gets()), 1)  # nothing to send: nothing asked
        w.events.append(event(eid(9), BOB_PK, "later", created_at=ts(self.at(2))))
        w.events.append(event(eid(10), BOB_PK, "later still", created_at=ts(self.at(2))))
        env.round(w, now=self.at(2))
        self.assertEqual(len(w.channel_gets()), 2)  # two cards, one question

    def test_a_channel_name_that_cannot_be_read_only_costs_the_channel_on_the_byline(self):
        """L2-1-FGS-206: `channels get` 失败：卡片的署名行（和摘要）只有发言人（没有「 · #频道」）照样发出，errors 为 0，一轮里只试一次。"""
        w = self.world()
        w.channel_get_fail = True
        report = self.env().round(w)
        self.assertEqual(len(w.channel_gets()), 1)
        self.assertEqual(len(w.card_sends()), 3)
        self.assertEqual([byline_text(c["card"]) for c in w.card_sends()], ["Alice", "helper-agent", "Bob"])
        self.assertEqual((report["errors"], report["cards_sent"]), (0, 3))

    def test_a_card_feishu_refuses_goes_out_as_text_under_its_own_key_and_is_counted(self):
        """L2-1-FGS-207: 飞书明确拒绝这张卡（api / validation 错误：说卡片内容有问题）：这张卡没发出去，回退成文字重发——文字发送路径、
        文字内容与文字模式一样、幂等键换成带 -text 后缀的；cards_fallback_text 加一、cards_sent 不加、to_feishu 加一、不算错误、
        不触发 needs_attention；账本里是文字消息的 id。"""
        for mode in ("content", "validation"):
            with self.subTest(mode=mode), self.fresh():
                w = self.one("hello")
                w.card_reject = [mode]
                env = self.env()
                report = env.round(w)
                calls = w.lark_sends()
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(w.card_sends()), 1)
                self.assertEqual(w.card_sends()[0]["key"], key_of(1))
                text = calls[1]
                self.assertEqual((text["app"], base.text_of(text)), (OWNER_APP, "Alice（Buzz）：hello"))
                self.assertEqual(text["args"][text["args"].index("--idempotency-key") + 1], key_of(1) + "-text")
                self.assertEqual(len(w.messages), 1)
                self.assertEqual(env.state()["b2f"][eid(1)], w.messages[0]["message_id"])
                self.assertEqual((report["to_feishu"], report["cards_sent"], report["cards_fallback_text"], report["errors"],
                                  report["failed"]), (1, 0, 1, 0, 0))
                self.assertFalse(FGS.needs_attention(report))

    def test_a_refusal_that_is_not_about_the_card_is_not_turned_into_text(self):
        """L2-1-FGS-208: 认证、权限、限流这类拒绝说的不是卡片内容，文字也会被拒：不回退，照旧记一次拒绝、下一轮用卡片重试（同一个键），
        重试成功就是卡片。"""
        for label, setup in (("auth", lambda w: w.card_reject.append("auth")), ("permission", lambda w: w.card_reject.append("permission")),
                             ("rate_limited", lambda w: w.lark_send_fail.append("rate_limited"))):
            with self.subTest(label=label), self.fresh():
                w = self.one("hello")
                setup(w)
                env = self.env()
                first = env.round(w)
                self.assertEqual(len(w.lark_sends()), 1, msg=label)
                self.assertEqual((first["errors"], first["cards_fallback_text"], first["to_feishu"]), (1, 0, 0))
                self.assertEqual(env.state()["b2f"][eid(1)], f"retry:{ts(NOW)}:-,card")
                second = env.round(w, now=self.at(1))
                self.assertEqual((second["to_feishu"], second["cards_sent"], second["cards_fallback_text"]), (1, 1, 0))
                self.assertEqual([c["key"] for c in w.card_sends()], [key_of(1), key_of(1)])
                self.assertEqual(len(w.messages), 1)

    def test_an_unknown_outcome_retries_the_card_under_the_same_key_and_never_falls_back(self):
        """L2-1-FGS-209: 超时、network 错误这类结果不确定的失败：卡片可能已经发出去了，不能回退成文字（会重复）；下一轮用同一个键、同一个
        接口重试，飞书按键去重，最终只有一条消息；文字一次都没发。"""
        for mode in ("network_envelope", "timeout"):
            with self.subTest(mode=mode), self.fresh():
                w = self.one("hello")
                w.lark_send_fail = [mode]
                env = self.env()
                first = env.round(w)
                self.assertEqual((first["unknown"], first["to_feishu"], first["cards_fallback_text"]), (1, 0, 0))
                self.assertEqual(env.state()["b2f"][eid(1)], f"pending:{ts(NOW)}:-,card")
                second = env.round(w, now=self.at(1))
                self.assertEqual((second["to_feishu"], second["cards_sent"], second["errors"]), (1, 1, 0))
                self.assertEqual([c["key"] for c in w.card_sends()], [key_of(1), key_of(1)])
                self.assertFalse([c for c in w.lark_sends() if "--text" in c["args"]])
                self.assertEqual(len(w.messages), 1)

    def test_a_text_fallback_that_is_refused_or_unknown_is_retried_as_text_only(self):
        """L2-1-FGS-210: 回退发出的文字也可能被拒或结果不确定：账本记下「已经回退成文字」，下一轮直接重试文字、用同一个 -text 键，不再发卡片
        （文字可能已送达，再发卡片会重复）；最终只有一条消息，cards_fallback_text 只在决定回退的那一轮计一次。"""
        for label, failure in (("refused", "rate_limited"), ("unknown", "network_envelope")):
            with self.subTest(label=label), self.fresh():
                w = self.one("hello")
                w.card_reject = ["content"]
                w.lark_send_fail = [None, failure]  # the card call passes this queue, the text call does not
                env = self.env()
                first = env.round(w)
                self.assertEqual(first["cards_fallback_text"], 1)
                if label == "refused":
                    self.assertEqual(env.state()["b2f"][eid(1)], f"retry:{ts(NOW)}:-,text")
                else:
                    self.assertEqual(env.state()["b2f"][eid(1)], f"pending:{ts(NOW)}:-,text")
                second = env.round(w, now=self.at(1))
                self.assertEqual(len(w.card_sends()), 1)  # no second card
                texts = [c for c in w.lark_sends() if "--text" in c["args"]]
                self.assertEqual([c["args"][c["args"].index("--idempotency-key") + 1] for c in texts], [key_of(1) + "-text"] * 2)
                self.assertEqual((second["to_feishu"], second["cards_fallback_text"], second["errors"]), (1, 0, 0))
                self.assertEqual(len(w.messages), 1)
                self.assertEqual(env.state()["b2f"][eid(1)], w.messages[0]["message_id"])

    def test_the_fallback_keeps_the_thread_and_the_agents_own_bot(self):
        """L2-1-FGS-211: 回退的文字走和这张卡一样的路径：回复话题的卡片被拒，文字也是 +messages-reply --reply-in-thread 到同一个父消息；
        agent 的卡片被拒，文字也由它自己的 bot 发（绝不改由 owner bot 代发）。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "root"), event(eid(2), BOB_PK, "re", created_at=T0 + 1, tags=[("e", eid(1), "", "reply")]),
                    event(eid(3), AGENT_PK, "done", created_at=T0 + 2)]
        w.card_reject = [None, "content", "content"]
        report = self.env().round(w)
        self.assertEqual((report["to_feishu"], report["cards_sent"], report["cards_fallback_text"]), (3, 1, 2))
        root_mid = w.sent_keys[key_of(1)]
        sends = w.lark_sends()
        reply_text = next(c for c in sends if "--text" in c["args"] and base.text_of(c) == "Bob（Buzz）：re")
        self.assertEqual((reply_text["args"][1], reply_text["args"][reply_text["args"].index("--message-id") + 1]),
                         ("+messages-reply", root_mid))
        self.assertIn("--reply-in-thread", reply_text["args"])
        self.assertEqual([m["content"] for m in w.threads[root_mid]], ["Bob（Buzz）：re"])
        agent_calls = [c for c in sends if c["app"] == AGENT_APP]
        self.assertEqual(len(agent_calls), 2)  # the card, then the text
        self.assertEqual((agent_calls[1]["app"], base.text_of(agent_calls[1])), (AGENT_APP, "done"))
        self.assertEqual(agent_calls[1]["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))

    def test_a_reply_and_its_thread_root_are_in_the_link(self):
        """L2-1-FGS-212: 话题里的回复：卡片「在 Buzz 中打开」的链接带 t=<话题根事件 id>（root 标记）；不在话题里（包括话题根本身）就不带 t。回复的根在飞书里
        没有副本时先补发根（也是一张卡片，标题是根的作者），再把回复发在它下面，回复的链接照样带 t。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "root"),
                    event(eid(2), BOB_PK, "re", created_at=T0 + 1, tags=[("e", eid(1), "", "root"), ("e", eid(1), "", "reply")]),
                    event(eid(50), ALICE_PK, "old root", created_at=T0 - 5000),  # before the binding: only a reply's thread brings it
                    event(eid(3), BOB_PK, "late reply", created_at=T0 + 2, tags=[("e", eid(50), "", "root"), ("e", eid(50), "", "reply")])]
        report = self.env().round(w)
        cards = w.card_sends()
        urls = [link_of(c["card"]) for c in cards]
        self.assertEqual(urls, [open_url(1), open_url(2, root=1), open_url(50), open_url(3, root=50)])
        self.assertEqual([c["endpoint"] for c in cards], ["send", "reply", "send", "reply"])
        self.assertEqual([c["card"]["header"]["title"] for c in cards], [plain("root"), plain("re"), plain("old root"), plain("late reply")])
        self.assertEqual((report["thread_roots_backfilled"], report["cards_sent"], report["cards_fallback_text"]), (1, 4, 0))
        self.assertEqual(cards[3]["call"]["args"][cards[3]["call"]["args"].index("--message-id") + 1], w.sent_keys[key_of(50)])

    def test_mentions_use_an_open_id_the_owners_app_knows_and_never_a_union_id(self):
        """L2-1-FGS-213: bridge 没给邮箱时：Feishu 里发过言的人（缓存里有他在 owner 应用的 open_id）用 `<at id=ou_…>`，agent 的 bot 用群里的
        成员 id，还没配对的人和映射不到的人是不通知的 @名字；所有卡片里都没有 union_id。"""
        w = self.world()
        w.bridge_omit = {"emails"}
        env = self.env()
        env.round(w)  # Alice speaks in Feishu: her open_id is paired with her union_id
        self.assertEqual(env.state()["idmap"].get(ALICE_OPEN), union_of(ALICE_OPEN))
        w.events.append(event(eid(5), BOB_PK, "cc", created_at=ts(self.at(1)),
                              tags=[("p", ALICE_PK), ("p", CAROL_PK), ("p", AGENT_PK), ("p", OWNER_PK)]))
        env.round(w, now=self.at(1))
        card = w.card_sends()[-1]["card"]
        self.assertEqual(card["body"]["elements"][1], markdown(f"<at id={ALICE_OPEN}></at> @Carol <at id={AGENT_BOT_MEMBER}></at> @Owner"))
        self.assertEqual(w.card_sends()[1]["card"]["body"]["elements"][1], markdown("@Alice"))  # her message came before she was paired
        for c in w.card_sends():
            dumped = json.dumps(c["card"])
            self.assertIsNone(re.search(r"<at id=on_", dumped))
            self.assertNotIn(union_of(ALICE_OPEN), dumped)

    def test_email_identity_mode_names_people_by_address_too(self):
        """L2-1-FGS-214: email 身份模式：@ 同样优先用邮箱（`<at email=…>`）。"""
        w = self.world()
        self.env(identity="email").round(w)
        agents = [c for c in w.card_sends() if byline_text(c["card"]).startswith("helper-agent")]
        self.assertEqual([c["card"]["body"]["elements"][1:2] for c in agents], [[markdown("<at email=alice@a4x.io></at>")]])

    def test_addresses_are_used_for_one_send_and_written_nowhere(self):
        """L2-1-FGS-215: 邮箱只在内存里用于这一轮的 @：不进 state，不进报告（报告只有计数）；lark-cli 的参数里只有发卡片的那一次（和文字模式下
        通讯录搜索的 --query 一样是参数，见「边界」）。"""
        w = self.world()
        env = self.env()
        report = env.round(w)
        self.assertNotIn("@a4x.io", (env.state_dir / FGS.STATE_FILE).read_text())
        self.assertNotIn("@a4x.io", json.dumps(report))
        carrying = [c for c in w.lark_calls if any("@a4x.io" in a for a in c["args"])]
        self.assertEqual([c["args"][:2] for c in carrying], [["im", "+messages-send"]])

    def test_a_huge_message_is_one_truncated_card_and_not_a_fallback(self):
        """L2-1-FGS-216: 一条 200 KB 的 Buzz 消息：一张 < 30 KB 的卡片（面板被截断、有那行说明），飞书收下，不回退成文字。"""
        w = self.one("长文 **x**\n" * 20000)
        report = self.env().round(w)
        self.assertEqual(len(w.card_sends()), 1)
        sent = w.card_sends()[0]
        content = sent["call"]["args"][sent["call"]["args"].index("--content") + 1]
        self.assertLess(len(content.encode("utf-8")), MAX_CARD)
        self.assertIn(NOTE, content)
        self.assertEqual((report["cards_sent"], report["cards_fallback_text"], report["errors"]), (1, 0, 0))

    def test_text_history_is_not_resent_when_a_channel_switches_to_cards(self):
        """L2-1-FGS-217: 文字模式的 state 换成 card 模式照常读：已经发过的不会再发一遍，新消息才是卡片；state 没有新字段，回退成文字也不需要迁移。"""
        w = self.world()
        Env(self.tmp, message_format="text").round(w)
        sent = len(w.lark_sends())
        env = self.env()
        report = env.round(w, now=self.at(1))
        self.assertEqual((len(w.lark_sends()), report["to_feishu"], report["cards_sent"]), (sent, 0, 0))
        w.events.append(event(eid(7), ALICE_PK, "new one", created_at=ts(self.at(2))))
        env.round(w, now=self.at(2))
        self.assertEqual(len(w.card_sends()), 1)
        self.assertEqual(set(env.state()), set(FGS.State.__dataclass_fields__))


class CardRoundEdges(TmpCase):
    """Round-level cases the mutation check found unpinned."""

    def env(self, **kw):
        return Env(self.tmp, message_format="card", **kw)

    def at(self, minutes):
        return NOW + timedelta(minutes=minutes)

    def test_people_who_share_an_account_are_not_named_by_address(self):
        """L2-1-FGS-219: 两个 pubkey 绑到同一个飞书账号：分不清谁是谁，都算映射不到——@ 他们不用邮箱（邮箱也是「认得这个人」的一种说法），
        写成不通知的纯文字。"""
        w = FakeWorld(self.tmp)
        w.bindings[BOB_PK] = ALICE_OPEN
        w.events = [event(eid(1), OWNER_PK, "cc", tags=[("p", ALICE_PK), ("p", BOB_PK)])]
        report = self.env().round(w)
        self.assertEqual(report["identity_conflicts"], 1)
        self.assertEqual(w.card_sends()[0]["card"]["body"]["elements"][1], markdown("@Alice @Bob"))
        self.assertNotIn("@a4x.io", json.dumps(w.card_sends()[0]["card"]))

    def test_a_person_with_several_addresses_is_named_by_the_first(self):
        """L2-1-FGS-220: bridge 给了一个人多个邮箱：用应答里的第一个。"""
        w = FakeWorld(self.tmp)
        w.emails[ALICE_PK] = ["alice@a4x.io", "alice.second@a4x.io"]
        w.events = [event(eid(1), BOB_PK, "cc", tags=[("p", ALICE_PK)])]
        self.env().round(w)
        self.assertEqual(w.card_sends()[0]["card"]["body"]["elements"][1], markdown("<at email=alice@a4x.io></at>"))

    def test_text_stays_text_when_the_text_itself_has_an_unknown_outcome_twice(self):
        """L2-1-FGS-221: 卡片被拒、回退的文字结果不确定，下一轮重试文字时又结果不确定：账本一直记着「已经是文字」，第三轮仍然只重试文字
        （用卡片会在文字可能已送达时再发一条）；整个过程飞书里只有一条消息。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "hello")]
        w.card_reject = ["content"]
        w.lark_send_fail = [None, "network_envelope", "timeout"]  # the text reaches Feishu once; the retry then times out
        env = self.env()
        for minute in range(3):
            env.round(w, now=self.at(minute))
            if minute < 2:
                self.assertEqual(env.state()["b2f"][eid(1)], f"pending:{ts(NOW)}:-,text", msg=minute)
        self.assertEqual(len(w.card_sends()), 1)  # the third round finds the text by its key
        self.assertEqual([c["args"][c["args"].index("--idempotency-key") + 1] for c in w.lark_sends() if "--text" in c["args"]],
                         [key_of(1) + "-text"] * 3)
        self.assertEqual(len(w.messages), 1)
        self.assertEqual(env.state()["b2f"][eid(1)], w.messages[0]["message_id"])

    def test_the_fallback_is_on_disk_before_the_text_goes_out(self):
        """L2-1-FGS-222: 回退发文字之前，「已经回退成文字」（,text）就已经落盘：进程在文字发出之后、写回账本之前被杀，下一轮也只会重试文字。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "hello")]
        w.card_reject = ["content"]
        env = self.env()
        seen = []
        w.before_send = lambda kind, key: seen.append((key, env.state()["b2f"].get(eid(1)))) if kind == "lark" else None
        env.round(w)
        self.assertEqual(seen, [(key_of(1), f"pending:{ts(NOW)}:-,card"), (key_of(1) + "-text", f"pending:{ts(NOW)}:-,text")])


class CardModeSwitch(TmpCase):
    """The format of a send is the format of its first attempt: a retry repeats that attempt exactly (same key, same request)."""

    def world(self):
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "hello")]
        return w

    def test_a_text_attempt_of_unknown_outcome_stays_text_after_the_config_switches_to_card(self):
        """L2-1-FGS-223: message_format 从 text 切到 card 时，已经发过、结果未知（pending）或被拒（retry）的那条消息仍按文字重试：同一个键、同一个
        请求（文字），不改成卡片——否则同一个幂等键下请求载荷变了，文字若已送达就有重复的风险。切换之后的新消息才是卡片。"""
        for failure, label in (("network_envelope", "pending"), ("rate_limited", "retry")):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as name:
                    self.tmp = Path(name)
                    os.chmod(self.tmp, 0o700)
                    w = self.world()
                    w.lark_send_fail = [failure]
                    Env(self.tmp, message_format="text").round(w)
                    env = Env(self.tmp, message_format="card")
                    self.assertTrue(env.state()["b2f"][eid(1)].startswith(f"{label}:{ts(NOW)}:-"))
                    self.assertNotIn(",", env.state()["b2f"][eid(1)])
                    report = env.round(w, now=NOW + timedelta(minutes=1))
                    self.assertEqual(w.card_sends(), [])
                    texts = [c for c in w.lark_sends() if "--text" in c["args"]]
                    self.assertEqual([c["args"][c["args"].index("--idempotency-key") + 1] for c in texts], [key_of(1)] * 2)
                    self.assertEqual((report["to_feishu"], report["cards_sent"], report["errors"]), (1, 0, 0))
                    self.assertEqual(len(w.messages), 1)
                    self.assertEqual(env.state()["b2f"][eid(1)], w.messages[0]["message_id"])
                    w.events.append(event(eid(2), ALICE_PK, "after the switch", created_at=ts(NOW) + 100))
                    env.round(w, now=NOW + timedelta(minutes=2))
                    self.assertEqual([c["card"]["header"]["title"] for c in w.card_sends()], [plain("after the switch")])

    def test_a_card_attempt_of_unknown_outcome_stays_a_card_after_the_config_switches_to_text(self):
        """L2-1-FGS-224: 反过来，card 切到 text 时，首次是卡片、结果未知的消息仍按卡片重试（同一个键，飞书按键去重，只有一条）；
        文字模式只管切换之后的新消息。"""
        w = self.world()
        w.lark_send_fail = ["network_envelope"]
        Env(self.tmp, message_format="card").round(w)
        env = Env(self.tmp, message_format="text")
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual([c["key"] for c in w.card_sends()], [key_of(1)] * 2)
        self.assertFalse([c for c in w.lark_sends() if "--text" in c["args"]])
        self.assertEqual((report["to_feishu"], report["cards_sent"], len(w.messages)), (1, 1, 1))
        w.events.append(event(eid(2), ALICE_PK, "after the switch", created_at=ts(NOW) + 100))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual([base.text_of(c) for c in w.lark_sends() if "--text" in c["args"]], ["Alice（Buzz）：after the switch"])


class CardsAreNotEchoed(TmpCase):
    def test_a_card_our_bot_sent_never_flows_back_into_buzz_or_a_thread_poll(self):
        """L2-1-FGS-218: 我们的 bot 发出的卡片在飞书里是 sender_type=app、msg_type=interactive：下一轮读群消息和话题回复时都不会被当作人的发言
        镜像回 Buzz，也不会在话题轮询里被当成回复。账本认得自己发出的消息；账本丢了（这里改成 failed）也一样，因为按 sender_type=app 跳过。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), ALICE_PK, "root"), event(eid(2), BOB_PK, "re", created_at=T0 + 1, tags=[("e", eid(1), "", "reply")])]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        self.assertEqual(len(w.card_sends()), 2)
        self.assertTrue(all(m["msg_type"] == "interactive" and m["sender"]["sender_type"] == "app"
                            for m in w.messages + [r for rs in w.threads.values() for r in rs]))
        buzz_sends = len(w.buzz_sends())
        report = env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual((report["to_buzz"], len(w.buzz_sends()), report["errors"]), (0, buzz_sends, 0))
        self.assertTrue(w.thread_polls)  # the thread our reply opened was polled, and its card left alone
        state = env.state()  # forget that these messages are ours: only their sender is left to tell
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(dict(state, b2f={k: "failed" for k in state["b2f"]})))
        w.events.clear()
        again = env.round(w, now=NOW + timedelta(minutes=3))
        self.assertEqual((again["to_buzz"], len(w.buzz_sends()), again["errors"]), (0, buzz_sends, 0))
        self.assertEqual(again["skipped"].get("bot"), 2)  # the card in the group and the card in the thread

    def test_route_feishu_message_skips_an_interactive_message_from_an_app(self):
        """L1-FGS-229: 路由层：sender_type=app 的 interactive 消息就是 "bot"，跳过；即使里面有一行像人的话。"""
        msg = fmsg("om_card1", "cli_someapp00000000001", '{"header":{"title":"Alice"}} hello', sender_type="app", msg_type="interactive")
        out = FGS.route_feishu_message(msg, open_id_to_pubkey={ALICE_OPEN: ALICE_PK}, bot_member_to_pubkey={}, channel_members={ALICE_PK},
                                       names={ALICE_PK: "Alice"}, now=NOW)
        self.assertEqual(out, "bot")


class CardReport(unittest.TestCase):
    def test_the_card_counters_are_reported_and_never_page_anyone(self):
        """L1-FGS-230: 报告里有 cards_sent 和 cards_fallback_text 两个计数，初始为 0；再多也不触发「需要关注」（退出码 3）。"""
        report = FGS._new_report()
        self.assertEqual((report["cards_sent"], report["cards_fallback_text"]), (0, 0))
        report.update(cards_sent=500, cards_fallback_text=500)
        self.assertFalse(FGS.needs_attention(report))


class CardEdges(unittest.TestCase):
    """Cases the mutation check showed nothing pinned down yet: each one is a small, real way for the card code to go wrong."""

    def test_the_thread_root_edge_cases_follow_the_bridge(self):
        """L1-FGS-233: 话题根的边角：一个 e tag 没有标记、另一个是 reply → 有标记的说明不知道根是谁（没有）；两个都没有标记 → 第一个；
        没有标记的那个 id 不合法 → 没有；标记不是字符串当作没有标记。"""
        root, other, reply = eid(1), eid(3), eid(2)
        ev = lambda *tags: event(eid(9), ALICE_PK, "x", tags=tags)
        self.assertIsNone(FGS.buzz_thread_root(ev(("e", other), ("e", reply, "", "reply"))))
        self.assertEqual(FGS.buzz_thread_root(ev(("e", root), ("e", other))), root)
        self.assertIsNone(FGS.buzz_thread_root(ev(("e", "ZZ"))))
        self.assertEqual(FGS.buzz_thread_root(ev(["e", root, "", 5])), root)

    def test_the_link_base_must_be_a_bare_https_origin(self):
        """L1-FGS-234: base 带路径、结尾斜杠、查询、片段、空白，或者根本没有主机，都不做链接（bridge 的 BIND_PUBLIC_ORIGIN 就是这种裸 origin）。"""
        for bad in ("https://", "https:///x", "https://bridge.example.test/x", "https://bridge.example.test/",
                    "https://bridge.example.test?x=1", "https://bridge.example.test#f", "https://bridge.example.test ",
                    "https://bridge .example.test", "https://bridge.example.test\n"):
            self.assertIsNone(FGS.open_link(bad, eid(1), CHANNEL, None), msg=repr(bad))
        for bad_url in ("http://bridge.example.test/bind/open?e=1", "buzz://channel/1", "", None):
            self.assertEqual(card_of("x", open_url=bad_url)["body"]["elements"], [byline("Alice", None)], msg=repr(bad_url))

    def test_a_short_text_is_shown_as_it_is_even_when_it_looks_unfinished(self):
        """L1-FGS-235: 没被截断的文字原样放进折叠全文，哪怕看起来没写完（行内代码、链接、裸 URL、没收尾的围栏、加粗）——只有截断产生的半截才处理。
        名字在字符数上限处截断后不留结尾空白。"""
        for text in ("a ` b", "see [note", "arr[0]", "go to https://a.co", "www.a.co", "x **bold", "```\ncode", "~~~\n~~~"):
            self.assertEqual(elements(TITLE + text), [byline(), panel(TITLE + text, len(TITLE + text))], msg=text)
            self.assertEqual(cut_preview(text), text, msg=text)
        self.assertEqual(byline_text(card_of("hi", "a" * 59 + " bbb")), "a" * 59)

    def test_a_line_cut_by_the_line_limit_keeps_what_the_author_wrote(self):
        """L1-FGS-236: 截断原语的切点落在行尾（这里是原来预览的 8 行上限）：这一行是作者写完的，哪怕带着没配对的反引号、方括号，也不动；
        只有切在行中间才处理半截。"""
        for last in ("a ` b", "x [note", "**bold"):
            lines = "\n".join(["l"] * 7 + [last, "l9"])
            self.assertEqual(cut_preview(lines), "\n".join(["l"] * 7 + [last]) + "…", msg=last)

    def test_fence_lines_inside_a_fence_only_close_it_when_they_should(self):
        """L1-FGS-237: 围栏里的另一种围栏（``` 里的 ~~~）、带说明的围栏行（```python）、缩进 4 格的（是代码不是围栏）、只有两个反引号的都不算收尾或开头；
        截断原语切出的末尾正好是收尾围栏（哪怕是被切成一半的更长的围栏）时「…」另起一行，不要粘在围栏上。"""
        preview = cut_preview
        self.assertEqual(preview("```\n~~~\n" + "x\n" * 20), "```\n~~~\n" + "x\n" * 5 + "x" + "\n```\n…")
        self.assertEqual(preview("```\n```python\n" + "x\n" * 20), "```\n```python\n" + "x\n" * 5 + "x" + "\n```\n…")
        self.assertEqual(preview("intro\n    ```\n" + "y\n" * 20), "intro\n    ```\n" + "y\n" * 5 + "y…")
        self.assertEqual(preview("a\n``x``\n" + "y\n" * 20), "a\n``x``\n" + "y\n" * 5 + "y…")
        first = "```" + "i" * 96 + "\n"  # the 300th character is the line break: the cut prefix ends with one
        self.assertEqual(preview(first + ("x" * 99 + "\n") * 2 + "x" * 50), first + ("x" * 99 + "\n") + "x" * 99 + "\n```\n…")
        closing = "\n".join(["```"] + ["c"] * 6 + ["```", "more"])
        self.assertEqual(preview(closing), "\n".join(["```"] + ["c"] * 6 + ["```"]) + "\n…")
        half = "```\n" + "x" * 292 + "\n" + "``````" + "\nafter"  # the character limit cuts the closing fence in two
        self.assertEqual(preview(half), "```\n" + "x" * 292 + "\n```\n…")

    def test_the_character_limit_never_leaves_half_a_markdown_construct_on_its_line(self):
        """L1-FGS-238: 截断原语的字符数切点在行中间：只有链接文字没写完的 `[docum`、图片的 `![alt`、`![d]` 后面跟着 `(`、裸 URL（http、www）、成对标记的最后一个
        没配对（`**a** **b`）、嵌套时取最早的开头，切完之后剩下的还要再检查（`[a][b]` 切掉 `[b]` 后 `[a]` 后面不是 `(`）；
        没有粘着切点的（后面是空格）完整 URL、后面不是 `(` 的 `[doc]` 保留。"""
        preview = cut_preview
        self.assertEqual(preview("x" * 292 + " [documentation](https://e.co) more"), "x" * 292 + "…")
        self.assertEqual(preview("x" * 290 + " ![alt text here"), "x" * 290 + "…")
        self.assertEqual(preview("x" * 296 + "![d](https://e.co) more"), "x" * 296 + "…")
        self.assertEqual(preview("x" * 290 + " www.example.com/path tail"), "x" * 290 + "…")
        self.assertEqual(preview("x" * 290 + " http://example.com/path tail"), "x" * 290 + "…")
        self.assertEqual(preview("x" * 270 + " **a** **bold text goes on and on and on"), "x" * 270 + " **a**…")
        self.assertEqual(preview("x" * 290 + " [a](b [cd more"), "x" * 290 + "…")
        self.assertEqual(preview("x" * 293 + " [a][b](https://e.co) tail"), "x" * 293 + " [a]…")
        self.assertEqual(preview("y" * 285 + " https://a.co[x more"), "y" * 285 + "…")
        self.assertEqual(preview("x" * 287 + " https://a.co tail"), "x" * 287 + " https://a.co…")
        self.assertEqual(preview("x" * 295 + "[doc] more"), "x" * 295 + "[doc]…")

    def test_mentions_with_odd_data_fall_back_to_a_plain_name(self):
        """L1-FGS-239: 邮箱太长（超过 254 个字符，虽然形状对）或者不是字符串就当作没有；名字清洗后是空的写 `@?`；p tag 的值不是 64 位小写 hex 就不算提及。"""
        long_email = "a" * 64 + "@" + "d" * 190 + ".io"
        self.assertGreater(len(long_email), 254)
        els = elements("T\nhi", mentions=(FGS.CardMention("Long", email=long_email), FGS.CardMention("Num", email=5),
                                       FGS.CardMention("", open_id=None), FGS.CardMention(ZWSP + " ")))
        self.assertEqual(els[1], markdown("@Long @Num @? @?"))
        ctx = FGS.CardContext(link_base=base.API_ORIGIN, channel_id=CHANNEL)
        route = lambda tags: FGS.route_buzz_event(
            event(eid(3), ALICE_PK, "x", tags=tags), mirror_pubkey=MIRROR_PK, agent_apps={}, human_pubkeys={ALICE_PK}, agent_pubkeys=set(),
            names={}, mention_targets={}, card=ctx)
        out = json.loads(route([("p", "not-hex"), ("p", "AB" * 32), ("p", BOB_PK)]).card)
        self.assertEqual(out["body"]["elements"][1], markdown(f"@{BOB_PK[:12]}"))

    def test_a_card_can_never_reach_the_limit_even_if_the_trim_size_were_set_wrong(self):
        """L1-FGS-240: 截断后的整张卡片恰好用满 28 KB（ASCII 一个字符一个字节，二分找到的是最大的那个）；就算 CARD_TRIM_BYTES 被改错、比飞书的上限
        还大，最后一道检查也不让卡片 ≥ 30 KB（丢掉折叠面板，只留署名行）。"""
        size = lambda text: len(raw_card(text, open_url=URL).encode("utf-8"))
        self.assertEqual(size("a" * 100000), 28 * 1024)
        for wrong in (MAX_CARD, MAX_CARD + 500, 40 * 1024):  # exactly the limit is already too big
            with mock.patch.object(FGS, "CARD_TRIM_BYTES", wrong):
                raw = raw_card("a" * 100000, open_url=URL)
            self.assertLess(len(raw.encode("utf-8")), MAX_CARD, msg=wrong)
            self.assertEqual(json.loads(raw)["body"]["elements"], [byline()], msg=wrong)

    def test_which_refusals_mean_the_card_is_wrong(self):
        """L1-FGS-241: card_refused：只有确定被拒（definite）的 api / validation 错误、且不是限流（码 230020、11232、99991400，或 subtype rate_limited）
        才说明卡片本身有问题；认证、权限、network、结果不确定（就算 type 写着 api）都不是。账本里记的发送方式（首次尝试用的是卡片 ",card"、
        卡片被拒后改发的文字 ",text"、没有记号就是文字模式的普通文字）与父消息的读写互逆。"""
        err = lambda code=230099, kind="card_invalid", type_="api", definite=True: FGS.CliError("send card", code, kind, definite=definite, error_type=type_)
        self.assertTrue(FGS.card_refused(err()))
        self.assertTrue(FGS.card_refused(err(code=1, kind="invalid_content", type_="validation")))
        for refused in (err(code=230020), err(code=11232), err(code=99991400), err(kind="rate_limited"), err(type_="authentication"),
                        err(type_="permission"), err(type_="network", definite=False), err(type_=""), err(definite=False)):
            self.assertFalse(FGS.card_refused(refused))
        self.assertEqual(FGS._send_extra("om_1", "text"), "om_1,text")
        self.assertEqual(FGS._send_extra(None, "text"), "-,text")
        self.assertEqual(FGS._send_extra("om_1", "card"), "om_1,card")
        self.assertEqual(FGS._send_extra(None, "card"), "-,card")
        self.assertEqual(FGS._send_extra("om_1", ""), "om_1")
        self.assertEqual(FGS._send_extra(None, ""), "-")
        for parent in ("om_1", None):
            for mode in ("", "card", "text"):
                self.assertEqual(FGS._parse_send_extra(FGS._send_extra(parent, mode)), (parent, mode))
        self.assertEqual(FGS._parse_send_extra("om_1,other"), ("om_1", ""))
        self.assertEqual(FGS._parse_send_extra(None), (None, ""))


class CardDocs(unittest.TestCase):
    """The reference must say what the script does: a drifting document is the usual way this goes wrong."""

    SKILL = base.TESTS.parent
    DOC = (SKILL / "references" / "feishu-group-sync.md").read_text(encoding="utf-8")

    def test_every_report_field_and_the_new_config_key_are_documented(self):
        """L1-FGS-231: 报告里的每个字段（含 cards_sent、cards_fallback_text）和配置键 message_format（两个取值、缺省）都写在参考文档里；
        脚本 README 与 SKILL.md 提到卡片和 message_format。"""
        for key in FGS._new_report():
            self.assertIn(f"`{key}`", self.DOC, msg=key)
        for term in ("`message_format`", '`"card"`（缺省）', '`"text"`'):
            self.assertIn(term, self.DOC, msg=term)
        readme = (self.SKILL / "references" / "scripts" / "README.md").read_text(encoding="utf-8")
        skill = (self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        for text in (readme, skill):
            self.assertIn("message_format", text)
            self.assertIn("卡片", text)

    def test_the_card_section_says_why_the_button_is_https_and_what_union_id_cannot_do(self):
        """L1-FGS-232: 「消息卡片」一节写明：为什么按钮只能是 https（飞书桌面端吞掉 buzz://）、不带签名的链接为什么可以、@ 的三种形式与
        union_id 不可用（230099）、30 KB 上限与截断、飞书拒绝卡片时回退成文字（-text 键、",text" 记号）与结果不确定时不回退。"""
        start, end = self.DOC.find("## 消息卡片"), self.DOC.find("## 配置（0600")
        self.assertTrue(0 <= start < end, msg="the reference has no 消息卡片 section before 配置")
        section = self.DOC[start:end]
        for term in ("https", "buzz://", "吞掉", "签名", "`n`", "HMAC", "`<at email=…></at>`", "`<at id=ou_…></at>`", "`@名字`",
                     "union_id", "230099", "100290", "30 KB", "28 KB", "（内容过长已截断，完整内容请在 Buzz 中打开）",
                     "回退成文字", "-text", ",text", ",card", "原样重复首次尝试", "结果不确定", "`sender_type=app`", "`＜`", "`CHANNEL_PEOPLE_EMAILS_ENABLED`"):
            self.assertIn(term, section, msg=term)

    def test_the_card_section_says_what_the_title_and_subtitle_are(self):
        """L1-FGS-248: 「消息卡片」一节写明标题是消息的第一行（去哪些记号、`[gitlab-notify:v1]` 行跳过、36 列加「…」、没有可用的行退回发言人、
        手机一行放得下的宽度没有真机实测）；没有副标题、预览和底部按钮：「发言人 · #频道名」是正文第一行的灰字、旁边是「在 Buzz 中打开」链接，
        消息比标题多出内容（含标题被截断、是表格行）才有折叠全文（2026-09-23，skills#145）。"""
        start, end = self.DOC.find("## 消息卡片"), self.DOC.find("## 配置（0600")
        section = self.DOC[start:end]
        for term in ("消息的第一行", "`[gitlab-notify:v1]`", "36 列", "手机一行", "取前面放得下的部分加「…」", "退回发言人", "没有真机实测", "`CARD_TITLE_COLUMNS`",
                     "发言人 · #频道名", "正文第一行", "没有副标题、没有预览、没有底部按钮", "比标题多出内容", "表格行"):
            self.assertIn(term, section, msg=term)
        self.assertNotIn("发言人：人是 Buzz 显示名", section)
        self.assertNotIn("去掉标题那一行之后", section)
        self.assertNotIn("primary", section)
        self.assertEqual(FGS.CARD_TITLE_COLUMNS, 36)

    def test_the_card_section_says_which_machine_lines_never_reach_the_card(self):
        """L1-FGS-258: 「消息卡片」一节写明同步消息的机器行不进卡片（skills#134）：header 行（整行以 `[gitlab-notify:v1]` 开头，只认最后一个非空行与第一行，
        标题 / 预览 / 折叠全文 / `config.summary` 都没有）、`🔔 通知 @…` 行（ADR-0012，只在带 header 的消息里去掉，卡片自己的 @ 行不受影响）、旧 `key: value`
        长格式的 `title:`（取值、不带键名，只按结构认）；只影响卡片，文字模式不变；标题规则里不再写「跳过 header」。"""
        start, end = self.DOC.find("## 消息卡片"), self.DOC.find("## 配置（0600")
        section = self.DOC[start:end]
        for term in ("机器行不进卡片", "整行以 `[gitlab-notify:v1]` 开头", "最后一个非空行", "`config.summary`", "`🔔 通知 @…`", "ADR-0012",
                     "只在带 header 的", "卡片自己的 @ 行", "旧 `key: value` 长格式", "`title:` 的值", "不带键名", "只按结构", "`url:`", "`unmapped:`",
                     "文字模式"):
            self.assertIn(term, section, msg=term)
        self.assertNotIn("跳过 `[gitlab-notify:v1]` 开头的行", section)

    def test_the_card_section_says_the_title_and_summary_clean_escaped_brackets_and_link_wrappers(self):
        """L1-FGS-265: 「消息卡片」一节写明：标题里的转义方括号与链接包装一并清洗（`\\[` `\\]` `\\\\` 是同步为防伪造写的，与 `_md_escape` 互逆），摘要走同一套清洗，
        预览在转义中间截断时连半个转义一起去掉。"""
        start, end = self.DOC.find("## 消息卡片"), self.DOC.find("## 配置（0600")
        section = self.DOC[start:end]
        for term in ("标题里的转义方括号与链接包装一并清洗", "`\\[`", "`\\]`", "`_md_escape`", "摘要", "同一套清洗", "半个转义"):
            self.assertIn(term, section, msg=term)


if __name__ == "__main__":
    unittest.main()
