"""The people generator's shared-file mode: many channel configs, one shared people file.

Test IDs are L1-GIS-PF-1nn (the generator side of the dedicated people_file range).
"""

import contextlib
import fcntl
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load("gitlab_buzz_people_generate_shared_under_test", "gitlab_buzz_people_generate.py")
SYNC = _load("gitlab_buzz_sync_under_shared_test", "gitlab_buzz_sync.py")

KEY_A = "11" * 32
KEY_B = "22" * 32
KEY_C = "33" * 32
AGENT_1 = "aa" * 32
AGENT_2 = "cc" * 32
PUBLISHER = "bb" * 32


class FakeGitLab(threading.Thread):
    """members/all per project id; records every request path."""

    def __init__(self, members):
        super().__init__(daemon=True)
        self.members = {int(pid): list(names) for pid, names in members.items()}
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                parts = urlsplit(self.path)
                segments = parts.path.split("/")
                # /api/v4/projects/<id>/members/all
                if len(segments) < 7 or segments[3] != "projects" or segments[5:7] != ["members", "all"]:
                    self.send_error(404)
                    return
                names = outer.members.get(int(segments[4]))
                if names is None:
                    self.send_error(404)
                    return
                page = int(parse_qs(parts.query).get("page", ["1"])[0])
                rows = [{"id": i, "username": n} for i, n in enumerate(names[(page - 1) * 100 : page * 100], start=1)]
                body = json.dumps(rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-next-page", str(page + 1) if page * 100 < len(names) else "")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def member_requests(self):
        return [p for p in self.requests if "members/all" in p]


class SharedModeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeGitLab({1312: [], 1313: []})
        self.fake.start()
        self.addCleanup(self.fake.stop)
        os.environ["GL_TOKEN_A"] = "test-token-a"
        os.environ["GL_TOKEN_B"] = "test-token-b"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("GL_TOKEN_A", "GL_TOKEN_B")])
        self.people_file = self.root / "people.json"

    def write_private(self, name, value, mode=0o600):
        path = self.root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def channel(self, name, projects, agents=(AGENT_1,), token_env="GL_TOKEN_A", people=None):
        return self.write_private(name, {
            "channel_id": "0b6bb6ce-2a10-4c3f-8f0a-2ff59d4f7a8e",
            "publisher_pubkey": PUBLISHER,
            "since": "2026-09-13T00:00:00Z",
            "agent_pubkeys": list(agents),
            "people": dict(people or {}),
            "gitlab": {"base_url": self.fake.base_url, "token_env": token_env, "bot_user_id": 989,
                       "bot_username": "project_1312_bot_abcdef", "projects": list(projects)},
            "buzz": {"cli_path": "/opt/buzz/buzz", "cli_sha256": "0" * 64},
        })

    def export(self, mapping):
        return self.write_private("export.json", mapping)

    def run_shared(self, configs, export, dry_run=False, people_file=None):
        return MODULE.run_shared(list(configs), export, people_file or self.people_file, "a4x.io", dry_run)

    def shared_content(self):
        return json.loads(self.people_file.read_text(encoding="utf-8"))


class SharedWriteTest(SharedModeCase):
    def test_union_of_all_configs_projects_into_one_file(self):
        """L1-GIS-PF-101 多个频道 config 的项目成员取并集，写成一份共享文件（0600）；各 config 一字不改。"""
        self.fake.members = {1312: ["alice", "carol"], 1313: ["bob", "carol"]}
        a = self.channel("a.json", [1312])
        b = self.channel("b.json", [1313], token_env="GL_TOKEN_B")
        before = (a.read_bytes(), b.read_bytes())
        result = self.run_shared([a, b], self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B, "carol@a4x.io": KEY_C}))
        self.assertEqual(result["added"], 3)
        self.assertTrue(result["written"])
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B, "carol": KEY_C})
        self.assertEqual(self.people_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)

    def test_existing_shared_entries_win_and_are_never_deleted(self):
        """L1-GIS-PF-102 共享文件里已有的条目优先于导出，也不因不在任何项目里而被删。"""
        self.fake.members = {1312: ["alice", "bob"], 1313: []}
        self.write_private("people.json", {"alice": KEY_C, "retired": KEY_B})
        a = self.channel("a.json", [1312])
        result = self.run_shared([a], self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B}))
        self.assertEqual(result["kept_manual"], 1)
        self.assertEqual(self.shared_content(), {"alice": KEY_C, "retired": KEY_B, "bob": KEY_B})

    def test_a_key_forbidden_in_any_config_is_refused(self):
        """L1-GIS-PF-103 任一 config 的 agent/publisher pubkey 都不许进共享文件（取并集校验）。"""
        self.fake.members = {1312: ["alice", "bob", "pat"], 1313: []}
        a = self.channel("a.json", [1312], agents=(AGENT_1,))
        b = self.channel("b.json", [1312], agents=(AGENT_2,))
        result = self.run_shared(
            [a, b],
            self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": AGENT_2, "pat@a4x.io": PUBLISHER}),
        )
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["rejected_agent_key"], 2)
        self.assertEqual(self.shared_content(), {"alice": KEY_A})

    def test_an_existing_entry_that_is_an_agent_key_is_refused(self):
        """L1-GIS-PF-104 共享文件里已有的条目若是某个 config 的 agent/publisher pubkey：整次拒绝，什么都不写。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        existing = self.write_private("people.json", {"erin": AGENT_2})
        before = existing.read_bytes()
        a = self.channel("a.json", [1312], agents=(AGENT_1,))
        b = self.channel("b.json", [1312], agents=(AGENT_2,))
        with self.assertRaises(MODULE.GenerateError) as ctx:
            self.run_shared([a, b], self.export({"alice@a4x.io": KEY_A}))
        self.assertNotIn(AGENT_2, str(ctx.exception))
        self.assertEqual(existing.read_bytes(), before)

    def test_same_project_is_fetched_once(self):
        """L1-GIS-PF-105 两个 config 含同一个项目时只拉一次成员。"""
        self.fake.members = {1312: ["alice"], 1313: ["bob"]}
        a = self.channel("a.json", [1312, 1313])
        b = self.channel("b.json", [1312])
        self.run_shared([a, b], self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B}))
        self.assertEqual(len(self.fake.member_requests()), 2)

    def test_each_config_uses_its_own_token_env(self):
        """L1-GIS-PF-106 每个 config 用自己声明的 token 环境变量；缺哪个就报哪个的名字。"""
        self.fake.members = {1312: ["alice"], 1313: ["bob"]}
        a = self.channel("a.json", [1312])
        b = self.channel("b.json", [1313], token_env="GL_TOKEN_B")
        os.environ.pop("GL_TOKEN_B")
        with self.assertRaises(MODULE.GenerateError) as ctx:
            self.run_shared([a, b], self.export({"alice@a4x.io": KEY_A}))
        self.assertIn("GL_TOKEN_B", str(ctx.exception))
        self.assertFalse(self.people_file.exists())


class SharedDryRunAndOutputTest(SharedModeCase):
    def test_dry_run_writes_nothing_and_prints_no_email_or_pubkey(self):
        """L1-GIS-PF-107 --dry-run 不建也不改共享文件；结果只有用户名和数量，没有邮箱和 pubkey。"""
        self.fake.members = {1312: ["alice", "ghost"], 1313: []}
        a = self.channel("a.json", [1312])
        result = self.run_shared([a], self.export({"alice@a4x.io": KEY_A}), dry_run=True)
        self.assertTrue(result["changed"])
        self.assertFalse(result["written"])
        self.assertFalse(self.people_file.exists())
        blob = json.dumps(result)
        self.assertNotIn("@a4x.io", blob)
        for key in (KEY_A, AGENT_1, PUBLISHER):
            self.assertNotIn(key, blob)
        self.assertEqual(result["unmapped"], ["ghost"])

    def test_dry_run_leaves_an_existing_file_untouched(self):
        """L1-GIS-PF-108 已有共享文件时 --dry-run 也不动它。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        existing = self.write_private("people.json", {"bob": KEY_B})
        before = existing.read_bytes()
        self.run_shared([self.channel("a.json", [1312])], self.export({"alice@a4x.io": KEY_A}), dry_run=True)
        self.assertEqual(existing.read_bytes(), before)

    def test_dry_run_creates_no_lock_file_and_leaves_the_directory_unchanged(self):
        """L1-GIS-PF-117 --dry-run 不建任何文件，包括 <共享文件>.people.lock：目录清单前后完全一致。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        export = self.export({"alice@a4x.io": KEY_A})
        before = sorted(p.name for p in self.people_file.parent.iterdir())
        self.run_shared([a], export, dry_run=True)
        after = sorted(p.name for p in self.people_file.parent.iterdir())
        self.assertEqual(after, before)
        self.assertFalse(self.people_file.with_name(self.people_file.name + ".people.lock").exists())

    def test_real_run_still_holds_the_lock_file(self):
        """L1-GIS-PF-118 对照：真写时仍然用锁文件串行化（dry-run 不建锁不代表真写不要锁）。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        self.run_shared([a], self.export({"alice@a4x.io": KEY_A}))
        self.assertTrue(self.people_file.with_name(self.people_file.name + ".people.lock").exists())

    def test_output_of_the_sync_loader_matches(self):
        """L1-GIS-PF-109 生成出来的共享文件，被 sync 用 people_file 加载后正是预期的 people。"""
        self.fake.members = {1312: ["alice", "bob"], 1313: []}
        a = self.channel("a.json", [1312])
        self.run_shared([a], self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B}))
        cfg = json.loads(a.read_text())
        cfg.pop("people")
        cfg["people_file"] = str(self.people_file)
        with mock.patch.object(SYNC, "validate_buzz_cli_path"):
            SYNC.validate_config(cfg)
        self.assertEqual(SYNC.effective_people(cfg), {"alice": KEY_A, "bob": KEY_B})


class SharedRefusalTest(SharedModeCase):
    def test_bad_shared_file_is_refused(self):
        """L1-GIS-PF-110 共享文件是符号链接、权限过宽、不是 JSON 对象：拒绝，且不动它。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        exp = self.export({"alice@a4x.io": KEY_A})
        real = self.write_private("real.json", {})
        link = self.root / "link.json"
        link.symlink_to(real)
        wide = self.write_private("wide.json", {}, mode=0o644)
        junk = self.write_private("junk.json", "[]")
        for label, path in (("symlink", link), ("wide", wide), ("junk", junk)):
            with self.subTest(label=label), self.assertRaises(MODULE.GenerateError):
                self.run_shared([a], exp, people_file=path)

    def test_people_file_path_must_be_absolute_with_an_existing_parent(self):
        """L1-GIS-PF-111 共享文件路径必须是绝对路径，且目录已存在。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        exp = self.export({"alice@a4x.io": KEY_A})
        for bad in (Path("people.json"), self.root / "no-such-dir" / "people.json"):
            with self.subTest(bad=str(bad)), self.assertRaises(MODULE.GenerateError):
                self.run_shared([a], exp, people_file=bad)

    def test_concurrent_run_is_refused(self):
        """L1-GIS-PF-112 另一个实例持有共享文件旁的锁时立即报错，不静默丢条目。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        exp = self.export({"alice@a4x.io": KEY_A})
        lock_path = self.people_file.with_name(self.people_file.name + ".people.lock")
        lock_path.write_bytes(b"")
        os.chmod(lock_path, 0o600)
        held = os.open(lock_path, os.O_WRONLY)
        try:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(MODULE.GenerateError) as ctx:
                self.run_shared([a], exp)
            self.assertIn("lock", str(ctx.exception))
        finally:
            os.close(held)
        self.assertFalse(self.people_file.exists())

    def test_a_bad_config_among_several_refuses_the_whole_run(self):
        """L1-GIS-PF-113 多个 config 里有一个不合格（宽权限）：整次拒绝，什么都不写。"""
        self.fake.members = {1312: ["alice"], 1313: ["bob"]}
        a = self.channel("a.json", [1312])
        b = self.channel("b.json", [1313])
        os.chmod(b, 0o644)
        with self.assertRaises(MODULE.GenerateError):
            self.run_shared([a, b], self.export({"alice@a4x.io": KEY_A}))
        self.assertFalse(self.people_file.exists())


class CommandLineTest(SharedModeCase):
    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = MODULE.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_repeated_config_with_people_file(self):
        """L1-GIS-PF-114 命令行：--config 可重复，配合 --people-file 走共享模式；输出没有 pubkey。"""
        self.fake.members = {1312: ["alice"], 1313: ["bob"]}
        a = self.channel("a.json", [1312])
        b = self.channel("b.json", [1313], token_env="GL_TOKEN_B")
        exp = self.export({"alice@a4x.io": KEY_A, "bob@a4x.io": KEY_B})
        code, out, err = self.call(["--config", str(a), "--config", str(b), "--people-file", str(self.people_file),
                                    "--export", str(exp)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["added"], 2)
        self.assertEqual(self.shared_content(), {"alice": KEY_A, "bob": KEY_B})
        for key in (KEY_A, KEY_B):
            self.assertNotIn(key, out)

    def test_several_configs_without_people_file_is_an_error(self):
        """L1-GIS-PF-115 不带 --people-file 时只能给一个 --config（旧的内联模式）；给多个直接报错。"""
        a = self.channel("a.json", [1312])
        b = self.channel("b.json", [1313])
        exp = self.export({"alice@a4x.io": KEY_A})
        code, _, err = self.call(["--config", str(a), "--config", str(b), "--export", str(exp)])
        self.assertEqual(code, 1)
        self.assertIn("--people-file", err)

    def test_single_config_without_people_file_keeps_the_inline_mode(self):
        """L1-GIS-PF-116 旧用法原样可用：单个 --config、不带 --people-file，仍然写回该 config 的内联 people。"""
        self.fake.members = {1312: ["alice"], 1313: []}
        a = self.channel("a.json", [1312])
        exp = self.export({"alice@a4x.io": KEY_A})
        code, _, err = self.call(["--config", str(a), "--export", str(exp)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(a.read_text())["people"], {"alice": KEY_A})
        self.assertFalse(self.people_file.exists())


if __name__ == "__main__":
    unittest.main()
