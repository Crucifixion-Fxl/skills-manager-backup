"""Plaque roots and styled bodies (issue #78): the topic card renders, identifies and
round-trips; fact bodies stay machine-comparable across the v3/v2/v1 formats."""

import hashlib
import importlib.util
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "gitlab_buzz_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("gitlab_buzz_sync_plaque", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load_module()

PID = 481
CHANNEL = "00000000-0000-4000-8000-0000000000c1"
DESK = "d" * 64
WEB = "http://127.0.0.1:8929/buzz-sync-test/pilot"


def issue_fact(iid=182, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": "Postcard 告警缺少业务分类标签", "description": "正文",
        "state": "opened", "labels": ["type::tech-debt", "status::unknown", "team::app"], "milestone": None,
        "confidential": False, "assignees": [{"username": "sshao"}],
        "web_url": f"{WEB}/-/issues/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return SYNC.issue_fact(base, PID)


def mr_fact(iid=31, **overrides):
    base = {
        "iid": iid, "project_id": PID, "title": "恢复最近工作区", "state": "opened", "draft": False,
        "sha": "a" * 40, "source_branch": "fix/x-staging", "target_branch": "staging",
        "labels": [], "reviewers": [], "author": {"username": "sshao"},
        "web_url": f"{WEB}/-/merge_requests/{iid}",
        "created_at": "2026-09-13T01:00:00Z", "updated_at": "2026-09-13T01:00:00Z",
    }
    base.update(overrides)
    return SYNC.mr_fact(base, PID)


def event(content, created_at=1, event_id="e" * 64, pubkey=DESK):
    return {"id": event_id, "pubkey": pubkey, "kind": 9, "created_at": created_at,
            "tags": [["h", CHANNEL]], "content": content}


class PlaqueRenderTest(unittest.TestCase):
    def test_plaques_carry_no_header_and_identify_by_url(self):
        """门牌无 header；末行裸 URL 是唯一机器身份，plaque_identity 认得。"""
        fact = issue_fact()
        plaque = SYNC.render_issue_plaque(fact, "buzz-sync-test/pilot")
        lines = plaque.split("\n")
        self.assertIsNone(SYNC.parse_header(plaque))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "📋 **#182 Postcard 告警缺少业务分类标签**")
        self.assertEqual(lines[1], "buzz-sync-test/pilot")
        self.assertEqual(SYNC.plaque_url(plaque), fact["url"])
        self.assertEqual(SYNC.plaque_identity(fact["url"]), {"object": "issue", "iid": 182})

        mr_plaque = SYNC.render_mr_plaque(mr_fact(), "buzz-sync-test/pilot")
        self.assertEqual(mr_plaque.split("\n")[0], "🔀 **!31 恢复最近工作区**")
        ident = SYNC.plaque_identity(SYNC.plaque_url(mr_plaque))
        self.assertEqual((ident["object"], ident["iid"]), ("mr", 31))

        branch = SYNC.render_branch_plaque(
            "feat/x", SYNC.branch_plaque_url(WEB, "feat/x"), "buzz-sync-test/pilot")
        self.assertEqual(branch.split("\n")[0], "🌿 **feat/x**")
        self.assertEqual(SYNC.plaque_identity(SYNC.plaque_url(branch)), {"object": "branch"})

    def test_plaque_title_cannot_forge_markdown(self):
        """标题里的 [] \ 被转义；门牌与事实正文都不能伪造链接语法。"""
        fact = issue_fact(title="evil [x](https://evil.example) \\ done")
        plaque = SYNC.render_issue_plaque(fact, "p")
        self.assertIn("\\[x\\]", plaque)
        self.assertNotIn("[x](https://evil.example)", plaque)
        rendered = SYNC.render_message(fact, "routing", first=True)
        self.assertNotIn("[x](https://evil.example)", rendered)
        self.assertIn("](http://127.0.0.1:8929/buzz-sync-test/pilot/-/issues/182)", rendered)

    def test_plaque_url_only_matches_a_bare_last_line(self):
        """末行不是裸 URL（比如旧 url: 行或事实消息）就不是门牌。"""
        self.assertIsNone(SYNC.plaque_url("[header]\nurl: https://x.example/-/issues/1"))
        self.assertIsNone(SYNC.plaque_url("📋 卡片\nhttps://x.example pad"))
        self.assertEqual(SYNC.plaque_url("卡片\nhttps://x.example/-/issues/1"),
                         "https://x.example/-/issues/1")
        self.assertIsNone(SYNC.plaque_identity("https://x.example/nope"))


class StyledBodyTest(unittest.TestCase):
    def test_issue_styles_cover_change_kinds(self):
        fact = issue_fact(state="closed")
        first = SYNC.render_message(fact, "routing", first=True)
        self.assertIn("📋 **已关闭**", first)
        transition = SYNC.render_message(fact, "routing", first=False, state_changed=True)
        self.assertIn("🔄 **状态流转 → 已关闭**", transition)
        regrouped = SYNC.render_message(fact, "routing", first=False, state_changed=False)
        self.assertIn("🏷 **归类变更**", regrouped)
        self.assertIn("✏️ **标题/描述更新**", SYNC.render_message(fact, "content"))
        self.assertIn("🏷 **字段更新**", SYNC.render_message(fact, "activity"))

    def test_mr_styles_cover_change_kinds(self):
        fact = mr_fact()
        self.assertIn("👀 **可评审**", SYNC.render_mr_message(fact, "lifecycle", became_reviewable=True))
        self.assertIn("🔀 **新建 · 已打开**", SYNC.render_mr_message(fact, "lifecycle", first=True))
        merged = mr_fact(state="merged")
        self.assertIn("🔄 **状态流转 → 已合并**", SYNC.render_mr_message(merged, "lifecycle"))
        draft = mr_fact(draft=True)
        self.assertIn(" · Draft", SYNC.render_mr_message(draft, "lifecycle"))
        self.assertIn("📦 **新提交**", SYNC.render_mr_message(fact, "update", sha_changed=True))
        self.assertIn("✏️ **字段更新**", SYNC.render_mr_message(fact, "update", sha_changed=False))

    def test_instant_records_are_styled(self):
        record = {
            "object": "deployment", "event": "failed", "project": PID, "key": "deployment-3836-failed",
            "title": "pages-publisher", "url": f"{WEB}/-/environments", "ref": "docs-pages",
        }
        lines = SYNC.render_record(record).split("\n")
        self.assertEqual(lines[0], "🔴 **部署失败** · pages-publisher")
        self.assertEqual(lines[1], f"{WEB}/-/environments")
        self.assertNotIn("events: deployment-3836-failed", lines)
        self.assertTrue(lines[-1].startswith("[gitlab-notify:v1][object:deployment][event:failed]"))
        self.assertIn("[events:deployment-3836-failed]", lines[-1])

    def test_activity_style_map(self):
        self.assertEqual(SYNC._activity_style({"object": "pipeline", "event": "failed"}),
                         ("❌", "流水线失败"))
        self.assertEqual(SYNC._activity_style({"object": "mr", "event": "approved"}), ("✔", "已批准"))
        self.assertEqual(SYNC._activity_style({"object": "?", "event": "?"}), ("💬", "活动"))


class StyledReadbackTest(unittest.TestCase):
    def test_issue_v3_roundtrip_and_legacy_fallback(self):
        """v3 样式读回逐项一致；旧 key: value 长格式仍能读回。"""
        fact = issue_fact()
        v3 = SYNC.render_message(fact, "routing", first=True)
        previous = SYNC.previous_fact_from_thread([event(v3)], DESK, PID, 182)
        self.assertEqual(previous["title"], fact["title"])
        self.assertEqual(previous["labels"], fact["labels"])
        self.assertEqual(previous["assignees"], ["sshao"])
        self.assertEqual(previous["description"], fact["description"])

        legacy = (
            "[gitlab-notify:v1][object:issue][type:tech-debt][status:unknown][state:opened]"
            "[change:routing][project:481][issue:182]\n"
            f"title: {fact['title']}\nurl: {fact['url']}\n"
            "labels: team•app\nassignees: sshao\nmilestone: -\n"
            f"description: {fact['description']}"
        )
        previous = SYNC.previous_fact_from_thread([event(legacy)], DESK, PID, 182)
        self.assertEqual((previous["title"], previous["labels"]), (fact["title"], ["team•app"]))

    def test_mr_v3_roundtrip_and_older_fallbacks(self):
        """v3 样式读回逐项一致；v2 紧凑与 v1 key: value 仍能读回。"""
        fact = mr_fact(labels=["team::app"], reviewers=[{"username": "carol"}])
        v3 = SYNC.render_mr_message(fact, "lifecycle", first=True)
        previous = SYNC.previous_mr_fact_from_thread([event(v3)], DESK, PID, 31)
        self.assertEqual(previous["title"], fact["title"])
        self.assertEqual(previous["author"], "sshao")
        self.assertEqual(previous["branches"], fact["branches"])
        self.assertEqual(previous["sha"], fact["sha"][:12])  # v3/v2 carry the short sha
        self.assertEqual(previous["labels"], fact["labels"])
        self.assertEqual(previous["reviewers"], ["carol"])

        v2 = (
            "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
            "[transition:none][project:481][mr:31]\n"
            "!31 恢复最近工作区 · sshao\n"
            f"fix/x-staging -> staging · sha {'a' * 12} · desc {fact['description']}\n"
            "labels team•app · reviewers carol\n"
            f"{fact['url']}"
        )
        previous = SYNC.previous_mr_fact_from_thread([event(v2)], DESK, PID, 31)
        self.assertEqual((previous["title"], previous["sha"], previous["reviewers"]),
                         ("恢复最近工作区", "a" * 12, ["carol"]))

        v1 = (
            "[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle]"
            "[transition:none][project:481][mr:31]\n"
            f"title: 恢复最近工作区\nurl: {fact['url']}\nbranches: fix/x-staging -> staging\n"
            f"sha: {'a' * 40}\nlabels: -\nreviewers: -\nassignees: -\nmilestone: -\n"
            f"description: {fact['description']}\nauthor: sshao"
        )
        previous = SYNC.previous_mr_fact_from_thread([event(v1)], DESK, PID, 31)
        self.assertEqual((previous["title"], previous["sha"], previous["author"]),
                         ("恢复最近工作区", "a" * 40, "sshao"))

    def test_title_with_separator_round_trips(self):
        """标题里的 “ · ” 不会拆坏 v3 读回。"""
        fact = mr_fact(title="A · B")
        v3 = SYNC.render_mr_message(fact, "lifecycle", first=True)
        previous = SYNC.previous_mr_fact_from_thread([event(v3)], DESK, PID, 31)
        self.assertEqual(previous["title"], "A · B")
        self.assertIsNone(SYNC.classify_mr_change(previous, fact))


class SelectRootPlaqueTest(unittest.TestCase):
    def test_select_root_matches_plaque_and_refuses_ambiguity(self):
        """URL 相等的门牌可作 root；两个候选（门牌 + 旧 header root）拒绝恢复。"""
        url = f"{WEB}/-/issues/182"
        plaque = event(SYNC.render_issue_plaque(issue_fact(), "p"), event_id="1" * 64)
        root = SYNC.select_root([plaque], DESK, CHANNEL, PID, "issue", 182, url)
        self.assertEqual(root, "1" * 64)

        legacy_root = event(
            "[gitlab-notify:v1][object:issue][type:bug][status:unknown][state:opened]"
            "[change:routing][project:481][issue:182]\ntitle: x",
            event_id="2" * 64,
        )
        with self.assertRaises(SYNC.SyncError):
            SYNC.select_root([plaque, legacy_root], DESK, CHANNEL, PID, "issue", 182, url)

    def test_plaque_without_expected_url_is_not_a_candidate(self):
        plaque = event(SYNC.render_issue_plaque(issue_fact(iid=183), "p"), event_id="3" * 64)
        self.assertIsNone(
            SYNC.select_root([plaque], DESK, CHANNEL, PID, "issue", 182, f"{WEB}/-/issues/182"))


if __name__ == "__main__":
    unittest.main()
