"""The shared people file: one local map of GitLab username -> Buzz pubkey that many channel configs point at.

Test IDs are L1-GIS-PF-nnn (a dedicated range: the plain L1-GIS-nnn series is shared with sibling branches).
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("gitlab_buzz_sync", SCRIPTS / "gitlab_buzz_sync.py")
SYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SYNC
assert SPEC.loader is not None
SPEC.loader.exec_module(SYNC)

CHANNEL = "0b6bb6ce-2a10-4c3f-8f0a-2ff59d4f7a8e"
DESK = "bb" * 32
AGENT = "aa" * 32
OTHER_AGENT = "cc" * 32
ALICE = "11" * 32
BOB = "22" * 32
CAROL = "33" * 32


class PeopleFileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(SYNC, "validate_buzz_cli_path")  # people rules only; the CLI lives per host
        patcher.start()
        self.addCleanup(patcher.stop)

    def shared(self, value, mode=0o600, name="people.json"):
        path = self.root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def config(self, people_file=None, **overrides):
        cfg = {
            "channel_id": CHANNEL, "publisher_pubkey": DESK, "since": "2026-09-13T00:00:00Z",
            "agent_pubkeys": [AGENT],
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "GL_TOKEN_TEST",
                       "bot_user_id": 989, "bot_username": "project_1312_bot_abcdef", "projects": [1312]},
            "buzz": {"cli_path": "/opt/buzz/buzz", "cli_sha256": hashlib.sha256(b"x").hexdigest()},
        }
        if people_file is not None:
            cfg["people_file"] = str(people_file)
        cfg.update(overrides)
        return cfg


class EffectivePeopleTest(PeopleFileCase):
    def test_shared_map_is_the_channels_people(self):
        """L1-GIS-PF-001 people_file 的内容就是本频道的 people；没有内联 people 也成立。"""
        path = self.shared({"alice": ALICE, "bob": BOB})
        cfg = self.config(path)
        SYNC.validate_config(cfg)
        self.assertEqual(SYNC.effective_people(cfg), {"alice": ALICE, "bob": BOB})

    def test_inline_people_override_the_shared_map(self):
        """L1-GIS-PF-002 内联 people 优先：同一用户名以频道内联的为准，其余取共享文件。"""
        path = self.shared({"alice": ALICE, "bob": BOB})
        cfg = self.config(path, people={"alice": CAROL, "dave": ALICE})
        SYNC.validate_config(cfg)
        self.assertEqual(SYNC.effective_people(cfg), {"alice": CAROL, "bob": BOB, "dave": ALICE})

    def test_without_people_file_nothing_changes(self):
        """L1-GIS-PF-003 不带 people_file 的旧配置行为不变。"""
        cfg = self.config(people={"alice": ALICE})
        SYNC.validate_config(cfg)
        self.assertEqual(SYNC.effective_people(cfg), {"alice": ALICE})
        self.assertEqual(SYNC.effective_people(self.config()), {})

    def test_edits_are_picked_up_without_a_restart(self):
        """L1-GIS-PF-004 每次取用都重新读文件：共享文件改了，下一轮就生效，不缓存。"""
        path = self.shared({"alice": ALICE})
        cfg = self.config(path)
        self.assertEqual(SYNC.effective_people(cfg), {"alice": ALICE})
        self.shared({"alice": ALICE, "bob": BOB})
        self.assertEqual(SYNC.effective_people(cfg), {"alice": ALICE, "bob": BOB})

    def test_syncer_uses_the_merged_map(self):
        """L1-GIS-PF-005 Syncer 用的是合并后的 people。"""
        path = self.shared({"alice": ALICE, "bob": BOB})
        syncer = SYNC.Syncer(self.config(path, people={"bob": CAROL}), object(), object(), state_dir=self.root)
        self.assertEqual(syncer.people, {"alice": ALICE, "bob": CAROL})


class FailClosedTest(PeopleFileCase):
    def assertRefused(self, cfg, leak=()):
        # Positive control: a good shared file is accepted, so a refusal below is about the file under test,
        # not about people_file being an unknown key.
        SYNC.validate_config(self.config(self.shared({"control": BOB}, name="control.json")))
        with self.assertRaises(SYNC.SyncError) as ctx:
            SYNC.validate_config(cfg)
        for secret in leak:
            self.assertNotIn(secret, str(ctx.exception))
        return str(ctx.exception)

    def test_path_must_be_an_absolute_string(self):
        """L1-GIS-PF-006 people_file 必须是绝对路径字符串。"""
        for bad in ("people.json", "./people.json", "", 7, ["/x"], None):
            with self.subTest(bad=bad):
                cfg = self.config()
                cfg["people_file"] = bad
                self.assertRefused(cfg)

    def test_missing_file_fails_closed(self):
        """L1-GIS-PF-007 共享文件不存在：整份配置拒绝，不当成「没有人」悄悄放行。"""
        message = self.assertRefused(self.config(self.root / "missing.json"))
        self.assertIn("people_file", message)

    def test_wide_permissions_are_refused(self):
        """L1-GIS-PF-008 共享文件必须 0600、属主本人。"""
        self.assertRefused(self.config(self.shared({"alice": ALICE}, mode=0o644)))

    def test_symlink_is_refused(self):
        """L1-GIS-PF-009 共享文件不能是符号链接。"""
        real = self.shared({"alice": ALICE}, name="real.json")
        link = self.root / "link.json"
        link.symlink_to(real)
        self.assertRefused(self.config(link))

    def test_dotdot_and_symlinked_directories_are_refused(self):
        """L1-GIS-PF-021 路径里带 .. 或经过符号链接目录：与 load_config 同款，拒绝。"""
        real_dir = self.root / "real"
        real_dir.mkdir()
        good = self.shared({"alice": ALICE}, name="real/people.json")
        SYNC.validate_config(self.config(good))
        self.assertRefused(self.config(self.root / "real" / ".." / "real" / "people.json"))
        alias = self.root / "alias"
        alias.symlink_to(real_dir)
        self.assertRefused(self.config(alias / "people.json"))

    def test_non_json_and_non_object_are_refused(self):
        """L1-GIS-PF-010 不是 JSON、或不是 JSON 对象：拒绝。"""
        for name, body in (("a.json", "not json"), ("b.json", "[]"), ("c.json", '"x"'), ("d.json", "null")):
            with self.subTest(body=body):
                self.assertRefused(self.config(self.shared(body, name=name)))

    def test_entries_follow_the_humans_only_rules_without_leaking_keys(self):
        """L1-GIS-PF-011 共享文件里的条目照样过 humans-only：非法用户名、非 hex、撞 Desk/agent 都拒绝，且报错不含 pubkey。"""
        for label, bad in (
            ("agent", {"alice": AGENT}),
            ("desk", {"alice": DESK}),
            ("hex", {"alice": "npub1abc"}),
            ("short", {"alice": ALICE[:-2]}),
            ("username", {"": ALICE}),
            ("type", {"alice": 5}),
        ):
            with self.subTest(label=label):
                self.assertRefused(self.config(self.shared(bad, name=f"{label}.json")), leak=(AGENT, DESK, ALICE))

    def test_agent_key_is_per_channel(self):
        """L1-GIS-PF-012 同一份共享文件：对一个频道合法，对另一个把它当 agent 的频道就拒绝。"""
        path = self.shared({"alice": ALICE, "erin": OTHER_AGENT})
        SYNC.validate_config(self.config(path))
        self.assertRefused(self.config(path, agent_pubkeys=[AGENT, OTHER_AGENT]))

    def test_inline_entries_still_follow_the_rules(self):
        """L1-GIS-PF-013 内联覆盖也不能借共享文件绕过 humans-only。"""
        path = self.shared({"alice": ALICE})
        self.assertRefused(self.config(path, people={"alice": AGENT}))

    def test_file_swapped_after_validation_is_caught_when_the_syncer_reads_it(self):
        """L1-GIS-PF-014 校验之后文件被换成含 agent 的版本：Syncer 取用时再次校验，拒绝而不是照发。"""
        path = self.shared({"alice": ALICE})
        cfg = self.config(path)
        SYNC.validate_config(cfg)
        self.shared({"alice": AGENT})
        with self.assertRaises(SYNC.SyncError) as ctx:
            SYNC.Syncer(cfg, object(), object(), state_dir=self.root)
        self.assertNotIn(AGENT, str(ctx.exception))

    def test_execute_reports_a_bad_shared_file_as_a_stable_error(self):
        """L1-GIS-PF-015 整轮入口：共享文件有问题时返回 status=error，不发任何东西，报错不含 pubkey。"""
        path = self.shared({"alice": AGENT})
        config_path = self.root / "sync.json"
        config_path.write_text(json.dumps(self.config(path)), encoding="utf-8")
        os.chmod(config_path, 0o600)
        code, result = SYNC.execute(config_path, self.root / "state", dry_run=True, env={})
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertNotIn(AGENT, json.dumps(result))


class LoadConfigStaysStrictTest(PeopleFileCase):
    def test_config_reader_error_texts_are_unchanged(self):
        """L1-GIS-PF-016 抽出共用读取器后，load_config 的报错文案不变。"""
        wide = self.root / "wide.json"
        wide.write_text("{}", encoding="utf-8")
        os.chmod(wide, 0o644)
        with self.assertRaisesRegex(SYNC.SyncError, "config must be an owner-only regular file"):
            SYNC.load_config(wide)
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        os.chmod(broken, 0o600)
        with self.assertRaisesRegex(SYNC.SyncError, r"cannot read config broken\.json"):
            SYNC.load_config(broken)


def _load_mr_harness():
    """The L1 MR harness (fake GitLab, fake Buzz, MR builders) from the sibling test module, loaded by path."""

    path = Path(__file__).resolve().parent / "test_gitlab_buzz_sync_mr.py"
    spec = importlib.util.spec_from_file_location("mr_harness_for_people_file", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MR = _load_mr_harness()


class MentionsFromPeopleFileTest(PeopleFileCase):
    """The real Syncer: a mapping that only lives in the shared file must @ the right person."""

    def setUp(self):
        super().setUp()
        self.gitlab, self.buzz = MR.FakeGitLab(), MR.FakeBuzz()
        self.gitlab.mr_list = [MR.make_mr(reviewers=MR.reviewers("carol"))]

    def mr_config(self, people_file, **overrides):
        cfg = {
            "channel_id": MR.CHANNEL, "publisher_pubkey": MR.DESK, "since": MR.SINCE, "include_confidential": False,
            "exclude": [], "diff": {"enabled": False, "private": False},
            "gitlab": {"base_url": "http://127.0.0.1:8929", "token_env": "NH_DESK_GITLAB_TOKEN",
                       "bot_user_id": MR.BOT_ID, "bot_username": MR.BOT, "projects": [MR.PID]},
            "buzz": {}, "people_file": str(people_file),
        }
        cfg.update(overrides)
        return cfg

    def run_sync(self, cfg):
        state = self.root / "state"
        state.mkdir(exist_ok=True)
        os.chmod(state, 0o700)
        return SYNC.Syncer(cfg, self.gitlab, self.buzz, state_dir=state).run()

    def reviewable_mentions(self):
        replies = [w for w in self.buzz.writes if w[0] is not None
                   and SYNC.parse_header(w[1]).get("transition") == "reviewable"]
        self.assertEqual(len(replies), 1, self.buzz.writes)
        return replies[0][2], replies[0][1]

    def test_reviewer_mapped_only_in_the_shared_file_is_mentioned(self):
        """L1-GIS-PF-017 config 里没有内联 people，只有 people_file：MR 可评审时 p-tag 恰好是 reviewer∪Maintainer 的映射 pubkey。"""
        path = self.shared(dict(MR.PEOPLE))
        self.run_sync(self.mr_config(path))
        mentions, content = self.reviewable_mentions()
        self.assertEqual(sorted(mentions), sorted([MR.ALICE, MR.CAROL]))
        self.assertIn("unmapped: erin", content)

    def test_inline_people_override_wins_in_the_mention(self):
        """L1-GIS-PF-018 内联 people 覆盖共享文件的同名条目：@ 的是内联那个 pubkey，共享文件里的旧值不出现。"""
        path = self.shared(dict(MR.PEOPLE))
        self.run_sync(self.mr_config(path, people={"carol": MR.BOB}))
        mentions, _ = self.reviewable_mentions()
        self.assertEqual(sorted(mentions), sorted([MR.ALICE, MR.BOB]))
        self.assertNotIn(MR.CAROL, mentions)

    def test_a_shared_agent_key_is_not_mentioned_and_stops_the_run(self):
        """L1-GIS-PF-019 共享文件里被换成 Desk pubkey：Syncer 拒绝，零发送。"""
        path = self.shared({**MR.PEOPLE, "carol": MR.DESK})
        with self.assertRaises(SYNC.SyncError):
            self.run_sync(self.mr_config(path))
        self.assertEqual(self.buzz.writes, [])

    def test_missing_or_wide_shared_file_stops_the_run_before_any_adapter(self):
        """L1-GIS-PF-020 共享文件缺失或权限过宽：整轮入口拒绝加载，连 GitLab/Buzz 适配器都不创建，零发送。"""
        built = []

        def factory(config, env, dry_run):
            built.append(1)
            return self.gitlab, self.buzz

        env = {"NH_DESK_GITLAB_TOKEN": "x" * 24}
        for label, path in (("missing", self.root / "absent.json"),
                            ("wide", self.shared(dict(MR.PEOPLE), mode=0o644, name="wide.json"))):
            with self.subTest(label=label):
                config_path = self.root / f"sync-{label}.json"
                config_path.write_text(json.dumps(self.mr_config(path)), encoding="utf-8")
                os.chmod(config_path, 0o600)
                code, result = SYNC.execute(config_path, self.root / "state", dry_run=False, env=env,
                                            adapter_factory=factory)
                self.assertEqual((code, result["status"]), (1, "error"), result)
                self.assertIn("people_file", result["error"])
        self.assertEqual(built, [])
        self.assertEqual(self.buzz.writes, [])


if __name__ == "__main__":
    unittest.main()
