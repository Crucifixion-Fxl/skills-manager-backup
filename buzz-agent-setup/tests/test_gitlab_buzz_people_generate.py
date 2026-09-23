import http.server
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path


TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "gitlab_buzz_people_generate.py"
SPEC = importlib.util.spec_from_file_location("gitlab_buzz_people_generate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

SYNC_SPEC = importlib.util.spec_from_file_location("gitlab_buzz_sync", TESTS.parent / "scripts" / "gitlab_buzz_sync.py")
SYNC = importlib.util.module_from_spec(SYNC_SPEC)
sys.modules[SYNC_SPEC.name] = SYNC
SYNC_SPEC.loader.exec_module(SYNC)

KEY_A = "11" * 32
KEY_B = "22" * 32
KEY_AGENT = "aa" * 32
PUBLISHER = "bb" * 32


class FakeGitLab(threading.Thread):
    """members/all over two pages, with the caller's usernames."""

    def __init__(self, usernames):
        super().__init__(daemon=True)
        self.usernames = list(usernames)
        self.requests = []
        handler = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                handler.requests.append(self.path)
                if not self.path.startswith("/api/v4/projects/1312/members/all"):
                    self.send_error(404)
                    return
                from urllib.parse import parse_qs, urlsplit

                page = int(parse_qs(urlsplit(self.path).query).get("page", ["1"])[0])
                rows = [
                    {"id": i, "username": name}
                    for i, name in enumerate(handler.usernames[(page - 1) * 100 : page * 100], start=1)
                ]
                body = json.dumps(rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-next-page", str(page + 1) if page * 100 < len(handler.usernames) else "")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()


class PeopleGenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeGitLab([])
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def write_private(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def config(self, people=None):
        return self.write_private('config.json', self.config_value(people=people))

    def config_value(self, people=None):
        return {
            "channel_id": "0b6bb6ce-2a10-4c3f-8f0a-2ff59d4f7a8e",
            "publisher_pubkey": PUBLISHER,
            "since": "2026-09-13T00:00:00Z",
            "agent_pubkeys": [KEY_AGENT],
            "people": dict(people or {}),
            "gitlab": {
                "base_url": self.fake.base_url,
                "token_env": "GL_TOKEN_TEST",
                "bot_user_id": 989,
                "bot_username": "project_1312_bot_abcdef",
                "projects": [1312],
            },
            "buzz": {"cli_path": "/opt/buzz/buzz", "cli_sha256": "0" * 64},
        }

    def run_tool(self, config, export, dry_run=False):
        os.environ["GL_TOKEN_TEST"] = "test-token"
        return MODULE.run(config, export, "a4x.io", dry_run)

    def test_exact_match_and_manual_wins(self):
        self.fake.usernames = ["jchen", "qwang", "project_1312_bot_deadbeef"]
        config = self.config(people={"qwang": KEY_B})
        export = self.write_private(
            "people.json",
            {"jchen@a4x.io": KEY_A, "qwang@a4x.io": KEY_AGENT},  # qwang's export points at an agent key
        )
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 1)  # jchen only
        self.assertEqual(result["kept_manual"], 1)  # qwang stays manual
        self.assertEqual(result["unmapped"], [])  # the bot is never a person
        after = json.loads(config.read_text())
        self.assertEqual(after["people"], {"qwang": KEY_B, "jchen": KEY_A})

    def test_agent_pubkey_refused(self):
        self.fake.usernames = ["zlin"]
        config = self.config()
        export = self.write_private("people.json", {"zlin@a4x.io": KEY_AGENT})
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["rejected_agent_key"], 1)
        self.assertEqual(json.loads(config.read_text())["people"], {})

    def test_publisher_pubkey_refused(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        export = self.write_private("people.json", {"jchen@a4x.io": PUBLISHER})
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["rejected_agent_key"], 1)
        self.assertEqual(json.loads(config.read_text())["people"], {})

    def test_bot_prefix_lookalike_is_a_person(self):
        # "project_1312_botany" starts like a bot username but is a person:
        # only the full project_<id>_bot_<hex> shape is a bot.
        self.fake.usernames = ["project_1312_botany", "project_1312_bot_78754a7bac958228"]
        config = self.config()
        export = self.write_private(
            "people.json", {"project_1312_botany@a4x.io": KEY_A}
        )
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["unmapped"], [])
        after = json.loads(config.read_text())["people"]
        self.assertEqual(after, {"project_1312_botany": KEY_A})

    def test_malformed_export_is_refused(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        for bad in (
            {"not-an-address": KEY_A},
            {"jchen@a4x.io": 42},
            {"jchen@a4x.io": "zz"},
        ):
            export = self.write_private("people.json", bad)
            with self.assertRaises(MODULE.GenerateError):
                self.run_tool(config, export)

    def test_localpart_fallback_and_ambiguity(self):
        # ali: no ali@a4x.io key, but the localpart matches another domain's
        # entry; sam: two pubkeys share the localpart; nobody: no match.
        self.fake.usernames = ["ali", "sam", "nobody"]
        config = self.config()
        export = self.write_private(
            "people.json",
            {"ali@addx.ai": KEY_A, "sam@addx.ai": KEY_B, "sam@addx.live": KEY_A},
        )
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 1)  # ali via localpart
        self.assertEqual(result["unmapped"], ["nobody", "sam"])
        after = json.loads(config.read_text())["people"]
        self.assertEqual(after, {"ali": KEY_A})

    def test_dry_run_writes_nothing(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        before = config.read_text()
        result = self.run_tool(config, export, dry_run=True)
        self.assertTrue(result["changed"])
        self.assertFalse(result["written"])
        self.assertEqual(config.read_text(), before)

    def test_written_config_loads_in_the_sync(self):
        self.fake.usernames = ["jchen", "qwang"]
        config = self.config(people={"qwang": KEY_B})
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        self.run_tool(config, export)
        loaded = SYNC.load_config(config)
        with mock.patch.object(SYNC, "validate_buzz_cli_path"):  # people rules only; the CLI lives per host
            SYNC.validate_config(loaded)  # must not raise
        self.assertEqual(loaded["people"], {"qwang": KEY_B, "jchen": KEY_A})
        mode = config.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_rejects_symlink_config(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        link = self.root / "link.json"
        link.symlink_to(config)
        with self.assertRaises(MODULE.GenerateError):
            self.run_tool(link, export)

    def test_rejects_wide_perms(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        os.chmod(config, 0o644)
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        with self.assertRaises(MODULE.GenerateError):
            self.run_tool(config, export)

    def test_missing_token_env(self):
        self.fake.usernames = ["jchen"]
        config = self.config()
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        os.environ.pop("GL_TOKEN_TEST", None)
        with self.assertRaises(MODULE.GenerateError) as ctx:
            MODULE.run(config, export, "a4x.io", False)
        self.assertIn("GL_TOKEN_TEST", str(ctx.exception))

    def test_pagination(self):
        self.fake.usernames = [f"member{i:03d}" for i in range(150)]
        config = self.config()
        export = self.write_private(
            "people.json", {f"member{i:03d}@a4x.io": KEY_A for i in range(150)}
        )
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 150)
        self.assertEqual(len([p for p in self.fake.requests if "members/all" in p]), 2)

    def test_concurrent_run_is_refused(self):
        # A second run while the first holds the lock must fail loudly:
        # a silent read-merge-replace would lose the first run's entries.
        self.fake.usernames = ["jchen"]
        config = self.config()
        export = self.write_private("people.json", {"jchen@a4x.io": KEY_A})
        lock_path = config.with_name(config.name + ".people.lock")
        lock_path.write_bytes(b"")
        os.chmod(lock_path, 0o600)
        import fcntl

        held = os.open(lock_path, os.O_WRONLY)
        try:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(MODULE.GenerateError) as ctx:
                self.run_tool(config, export)
            self.assertIn("lock", str(ctx.exception))
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
            os.close(held)
        # Once free, the same run succeeds.
        result = self.run_tool(config, export)
        self.assertEqual(result["added"], 1)


if __name__ == "__main__":
    unittest.main()
