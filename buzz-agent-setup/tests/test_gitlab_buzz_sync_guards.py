"""Characterization tests for guards that review mutation runs showed could be deleted while the suite stayed green."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import urllib.request
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "gitlab_buzz_sync.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load("gitlab_buzz_sync_guards", SCRIPT)
FAKES = load("gitlab_buzz_sync_guards_fakes", TESTS / "test_gitlab_buzz_sync_isolation.py")
CHANNEL, DESK, PID = FAKES.CHANNEL, FAKES.DESK, FAKES.PID
EVENT = "e" * 64


class ReleaseDir:
    """A temporary buzz-0.5.23 release directory holding test binaries."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "buzz-0.5.23" / "usr" / "bin"
        self.bin.mkdir(parents=True)

    def binary(self, name="buzz", content=b"\x7fELFtest fixture", mode=0o700, parent=None):
        path = (parent or self.bin) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        return path

    def close(self):
        self.tmp.cleanup()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GitLabRedirectWiringTest(unittest.TestCase):
    def test_default_opener_refuses_redirects(self):
        """L2-1-GIS-001 默认 opener 装的是拒绝重定向的处理器，没有会带着 PRIVATE-TOKEN 跟随重定向的标准处理器。"""
        client = SYNC.GitLabClient(FAKES.config(), {"NH_DESK_GITLAB_TOKEN": "glpat-test-token"})
        handler_types = [type(handler) for handler in client.opener.handlers]
        self.assertIn(SYNC.NoRedirectHandler, handler_types)
        self.assertNotIn(urllib.request.HTTPRedirectHandler, handler_types)


class CliPathGuardTest(unittest.TestCase):
    def setUp(self):
        self.release = ReleaseDir()
        self.addCleanup(self.release.close)

    def test_pinned_cli_checks(self):
        """L1-GIS-012 CLI 路径逐项拒绝：非 ELF、组/其他可写、不可执行、改名、不在 buzz-0.5.23 目录、指向真二进制的同名符号链接。"""
        good = self.release.binary()
        self.assertEqual(SYNC.validate_buzz_cli_path(str(good), sha(good)), good)
        elsewhere = Path(self.release.tmp.name) / "other" / "usr" / "bin"
        cases = {
            "not elf": self.release.binary("buzz", b"#!/bin/sh\necho", parent=self.release.bin / "script"),
            "group writable": self.release.binary("buzz", mode=0o720, parent=self.release.bin / "writable"),
            "not executable": self.release.binary("buzz", mode=0o600, parent=self.release.bin / "plain"),
            "renamed": self.release.binary("buzz-real"),
            "outside release dir": self.release.binary("buzz", parent=elsewhere),
        }
        link_dir = self.release.bin / "linked"
        link_dir.mkdir()
        (link_dir / "buzz").symlink_to(good)
        cases["symlink named buzz"] = link_dir / "buzz"
        for name, path in cases.items():
            with self.subTest(name), self.assertRaises(SYNC.SyncError):
                SYNC.validate_buzz_cli_path(str(path), sha(path))

    def test_token_env_cannot_shadow_child_env(self):
        """L1-GIS-012 gitlab.token_env 不能是 CLI 子进程白名单里的变量名，否则 token 会进子进程。"""
        good = self.release.binary()
        for key in ("HOME", "PATH", "BUZZ_RELAY_URL"):
            cfg = FAKES.config()
            cfg["gitlab"]["token_env"] = key
            cfg["buzz"] = {"cli_path": str(good), "cli_sha256": sha(good)}
            with self.subTest(key), self.assertRaises(SYNC.SyncError):
                SYNC.validate_config(cfg)


class RootSendReadbackTest(unittest.TestCase):
    def setUp(self):
        verifier = mock.patch.object(SYNC, "verify_nostr_event_signature")
        verifier.start()
        self.addCleanup(verifier.stop)
        self.release = ReleaseDir()
        self.addCleanup(self.release.close)
        cli = self.release.binary()
        cfg = FAKES.config()
        cfg["buzz"] = {"cli_path": str(cli), "cli_sha256": sha(cli)}
        self.calls, self.thread_events = [], []
        env = {"BUZZ_RELAY_URL": "ws://127.0.0.1:3000", "BUZZ_PRIVATE_KEY": "test-key",
               "NH_DESK_GITLAB_TOKEN": "glpat-test-token"}
        self.buzz = SYNC.BuzzCli(cfg, env, runner=self.run_cli, sleeper=lambda seconds: None)

    def run_cli(self, args, input=None, capture_output=True, text=True, timeout=None, check=False, env=None):
        self.calls.append(args[1:3])
        if args[1:3] == ["messages", "send"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"accepted": True, "event_id": EVENT}))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"events": self.thread_events}))

    def event(self, content, **changes):
        base = {"id": EVENT, "pubkey": DESK, "kind": 9, "tags": [["h", CHANNEL]], "content": content}
        base.update(changes)
        return base

    def test_root_send_reads_back_its_own_thread(self):
        """L2-1-GIS-002 发顶层 root 后按新 event id 回读：内容、作者、频道一致且不带 e tag、p tag 才算成功。"""
        content = "[gitlab-notify:v1][object:activity][event:digest][project:481]\nevents: event-1"
        self.thread_events = [self.event(content)]
        self.assertEqual(self.buzz.send(content), EVENT)
        self.assertIn(["messages", "thread"], self.calls)
        for name, bad in {
            "has e tag": self.event(content, tags=[["h", CHANNEL], ["e", "f" * 64, "", "reply"]]),
            "unexpected p tag": self.event(content, tags=[["h", CHANNEL], ["p", "a1" * 32]]),
            "other author": self.event(content, pubkey="c" * 64),
            "edited content": self.event(content + "!"),
        }.items():
            self.thread_events = [bad]
            with self.subTest(name), self.assertRaises(SYNC.SyncError):
                self.buzz.send(content)


class DryRunTest(FAKES.SyncCase):
    def run_dry(self):
        return SYNC.Syncer(FAKES.config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name)).run(dry_run=True)

    def assert_no_writes(self):
        self.assertEqual((self.buzz.writes, self.gitlab.writes), ([], []))
        self.assertEqual(list(Path(self.tmp.name).glob("*.cache.json")), [])

    def test_dry_run_mr_top_level_and_recovery(self):
        """L1-GIS-026 dry-run 对新 MR、顶层通知、root 找回都只计数：两侧零写入、不写缓存。
        digest 输入用合成记录（live push 已不产生记录——2026-09-18 政策）。"""
        from unittest import mock

        root = self.buzz.add_event(SYNC.render_message(SYNC.issue_fact(FAKES.make_issue(183), PID), "routing"))
        self.gitlab.issue_list[PID] = [FAKES.make_issue(183)]
        self.gitlab.mr_list[PID] = [FAKES.make_mr(31)]
        self.gitlab.event_list = [FAKES.push_event()]

        def synthetic_push_digest(event, project_id, web_url):
            push = event.get("push_data")
            if not isinstance(push, dict):
                return None
            raw_ref = SYNC._single_line(push.get("ref") or "")
            return SYNC._record(f"event-{event['id']}", "push", "pushed", "digest", project_id,
                                url=f"{web_url}/-/commits/{raw_ref}",
                                created_at=event.get("created_at"),
                                actor=(event.get("author") or {}).get("username") or "?",
                                ref=SYNC.neutralize(raw_ref), commits=push.get("commit_count") or 0)

        with mock.patch.object(SYNC, "record_from_event", side_effect=synthetic_push_digest):
            summary = self.run_dry()
        self.assertEqual((summary["recovered"], summary["mr_created"], summary["notified"]["milestone"]), (1, 1, 0))
        self.assertEqual(len(summary["summary_requests"]), 1)
        self.assertIn(SYNC.buzz_message_link(CHANNEL, root), summary["links"])
        self.assert_no_writes()

    def test_dry_run_bound_mr_activity(self):
        """L1-GIS-026 dry-run 对已绑定 MR 的流水线活动只计数，不回帖。"""
        self.gitlab.mr_list[PID] = [FAKES.make_mr(31)]
        SYNC.Syncer(FAKES.config(), self.gitlab, self.buzz, state_dir=Path(self.tmp.name) / "real").run()
        self.buzz.writes.clear()
        self.gitlab.writes.clear()
        self.gitlab.pipeline_list = [FAKES.mr_pipeline()]
        summary = self.run_dry()
        self.assertEqual(summary["activity"], 1)
        self.assert_no_writes()


if __name__ == "__main__":
    unittest.main()
