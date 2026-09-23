"""feishu_doc_publish.py: Markdown report (with Buzz-hosted images) -> Feishu doc, made and shared by the agent's own bot."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
HELPER = SKILL / "references" / "scripts" / "feishu_doc_publish.py"
SHA1 = "a" * 64
SHA2 = "b" * 64

FAKE_LARK = r'''#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_LOG"]
args = sys.argv[1:]
body = sys.stdin.read() if "--content" in args and args[args.index("--content") + 1] == "-" else ""
with open(log, "a") as f:
    f.write(json.dumps({"tool": "lark", "args": args, "stdin": body, "cwd": os.getcwd(),
                        "cfg": os.environ.get("LARKSUITE_CLI_CONFIG_DIR")}) + "\n")
fail_file = os.environ.get("FAKE_FAIL_FILE")
if fail_file and os.path.exists(fail_file) and args[:2] == ["docs", "+update"]:
    os.remove(fail_file)
    print(json.dumps({"ok": False, "error": {"subtype": "timeout"}})); sys.exit(1)
if args[:2] == ["docs", "+create"]:
    print(json.dumps({"ok": True, "data": {"document": {"document_id": "DOC1", "url": "https://x.feishu.cn/docx/DOC1"}}}))
else:
    print(json.dumps({"ok": True, "data": {}}))
'''
FAKE_BUZZ = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": "buzz", "args": args}) + "\n")
out = args[args.index("-o") + 1]
open(out, "wb").write(b"\xff\xd8fakejpeg")
'''


def write_exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        write_exe(self.dir / "lark-cli", FAKE_LARK)
        write_exe(self.dir / "buzz", FAKE_BUZZ)
        self.log = self.dir / "log.jsonl"
        self.work = self.dir / "work"
        self.work.mkdir()

    def run_helper(self, md: str, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        if not HELPER.is_file():
            raise AssertionError(f"{HELPER.relative_to(SKILL)} is required")
        full_env = {**os.environ, "FAKE_LOG": str(self.log), "LARKSUITE_CLI_CONFIG_DIR": "/agent/cfg", **(env or {})}
        return subprocess.run(
            [sys.executable, str(HELPER), "--title", "日报 2026-09-21", "--chat-id", "oc_3b228dca0b1374c41edbc176782f6d84",
             "--lark-cli", str(self.dir / "lark-cli"), "--buzz", str(self.dir / "buzz"), *extra],
            input=md, capture_output=True, text=True, cwd=self.work, env=full_env)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def lark_calls(self) -> list[dict]:
        return [c for c in self.calls() if c["tool"] == "lark"]

    def test_creates_the_doc_as_bot_grants_the_chat_read_only_and_prints_the_link(self) -> None:
        result = self.run_helper("## 结论\n\n一切正常\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"document_id": "DOC1", "url": "https://x.feishu.cn/docx/DOC1"})
        create, grant = self.lark_calls()
        self.assertEqual(create["args"][:2], ["docs", "+create"])
        self.assertIn("--as", create["args"]); self.assertEqual(create["args"][create["args"].index("--as") + 1], "bot")
        self.assertEqual(create["args"][create["args"].index("--title") + 1], "日报 2026-09-21")
        self.assertEqual(create["stdin"], "## 结论\n\n一切正常\n")
        self.assertEqual(grant["args"][:2], ["drive", "+member-add"])
        self.assertEqual(grant["args"][grant["args"].index("--as") + 1], "bot")
        for flag, value in (("--token", "DOC1"), ("--type", "docx"), ("--member-type", "openchat"),
                            ("--member-id", "oc_3b228dca0b1374c41edbc176782f6d84"), ("--perm", "view")):
            self.assertEqual(grant["args"][grant["args"].index(flag) + 1], value)
        self.assertIn("--yes", grant["args"])

    def test_never_uses_the_user_identity_and_keeps_the_agent_profile(self) -> None:
        self.run_helper("x\n")
        for call in self.lark_calls():
            self.assertNotIn("user", call["args"])
            self.assertEqual(call["cfg"], "/agent/cfg")

    def test_buzz_hosted_images_are_downloaded_and_become_local_paths(self) -> None:
        url1 = f"https://buzz.example/media/{SHA1}.jpg"
        url2 = f"https://buzz.example/media/{SHA2}.jpg"
        md = f"## A\n\n文字\n\n![image]({url1})\n\n## B\n\n![]({url2})\n\n尾巴\n"
        result = self.run_helper(md)
        self.assertEqual(result.returncode, 0, result.stderr)
        downloads = [c for c in self.calls() if c["tool"] == "buzz"]
        self.assertEqual([c["args"][:3] for c in downloads], [["media", "get", url1], ["media", "get", url2]])
        create, update, *_ = self.lark_calls()
        self.assertNotIn("buzz.example", create["stdin"] + update["stdin"])
        self.assertRegex(create["stdin"], r"!\[[^\]]*\]\(@\./[^)]+\.jpg\)")
        # every image goes out in its own call, so one upload never has to finish inside a single 30s request
        self.assertEqual(update["args"][:2], ["docs", "+update"])
        self.assertEqual(update["args"][update["args"].index("--command") + 1], "append")
        self.assertRegex(update["stdin"], r"!\[[^\]]*\]\(@\./[^)]+\.jpg\)")
        self.assertTrue(update["stdin"].rstrip().endswith("尾巴") or "尾巴" in self.lark_calls()[-2]["stdin"])
        # the images live under the cwd (lark-cli only reads @./ paths inside it) and are cleaned up afterwards
        self.assertEqual(Path(create["cwd"]).resolve(), self.work.resolve())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_xml_significant_characters_are_escaped_outside_code_fences(self) -> None:
        md = "价格 $5 ~3 天 <b>x</b>\n\n```\n<keep> ~ $\n```\n"
        self.run_helper(md)
        stdin = self.lark_calls()[0]["stdin"]
        self.assertIn("价格 \\$5 \\~3 天 \\<b>x\\</b>", stdin)
        self.assertIn("<keep> ~ $", stdin)

    def test_a_failed_append_is_retried(self) -> None:
        fail = self.dir / "fail-once"
        fail.write_text("x")
        md = f"a\n\n![]({'https://h/media/' + SHA1}.jpg)\n\nb\n"
        result = self.run_helper(md, env={"FAKE_FAIL_FILE": str(fail)})
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = [c for c in self.lark_calls() if c["args"][:2] == ["docs", "+update"]]
        self.assertEqual(len(updates), 2)

    def test_a_failed_create_shares_nothing_and_exits_nonzero(self) -> None:
        write_exe(self.dir / "lark-cli", "#!/bin/sh\necho '{\"ok\": false}'\nexit 1\n")
        result = self.run_helper("x\n", "--retries", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_the_document_is_not_shared_when_the_body_did_not_fully_upload(self) -> None:
        fail = self.dir / "fail-once"
        fail.write_text("x")
        md = f"a\n\n![]({'https://h/media/' + SHA1}.jpg)\n\nb\n"
        result = self.run_helper(md, "--retries", "1", env={"FAKE_FAIL_FILE": str(fail)})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([c for c in self.lark_calls() if c["args"][:2] == ["drive", "+member-add"]])

    def test_a_hanging_lark_cli_is_cut_off_and_nothing_is_shared(self) -> None:
        # the child leaves a grandchild holding the pipes, like a node wrapper would: the whole group must die
        write_exe(self.dir / "lark-cli", "#!/bin/sh\nsleep 30 &\nsleep 30\n")
        started = time.monotonic()
        result = self.run_helper("x\n", "--retries", "1", "--timeout", "1")
        self.assertLess(time.monotonic() - started, 15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("timed out", result.stderr)

    def test_a_hanging_lark_cli_is_retried_within_the_budget(self) -> None:
        write_exe(self.dir / "lark-cli", "#!/bin/sh\nsleep 30\n")
        started = time.monotonic()
        result = self.run_helper("x\n", "--retries", "2", "--timeout", "1")
        self.assertLess(time.monotonic() - started, 15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.count("timed out"), 2)

    def test_a_hanging_image_download_fails_instead_of_blocking(self) -> None:
        write_exe(self.dir / "buzz", "#!/bin/sh\nsleep 30\n")
        started = time.monotonic()
        result = self.run_helper(f"![](https://h/media/{SHA1}.jpg)\n", "--timeout", "1")
        self.assertLess(time.monotonic() - started, 15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("timed out", result.stderr)
        self.assertEqual(self.lark_calls(), [])

    def test_tilde_code_fences_are_left_alone(self) -> None:
        self.run_helper("前 ~ 后\n\n~~~sh\necho <keep> ~ $HOME\n~~~\n")
        stdin = self.lark_calls()[0]["stdin"]
        self.assertIn("前 \\~ 后", stdin)
        self.assertIn("echo <keep> ~ $HOME", stdin)

    def test_images_whose_sha_shares_a_prefix_stay_distinct(self) -> None:
        one, two = "c" * 16 + "1" * 48, "c" * 16 + "2" * 48
        md = f"![](https://h/media/{one}.jpg)\n\n![](https://h/media/{two}.jpg)\n"
        result = self.run_helper(md)
        self.assertEqual(result.returncode, 0, result.stderr)
        downloads = [c for c in self.calls() if c["tool"] == "buzz"]
        self.assertEqual(len(downloads), 2)
        self.assertEqual(len({c["args"][c["args"].index("-o") + 1] for c in downloads}), 2)


if __name__ == "__main__":
    unittest.main()
