"""Contract: every agent app gets the doc-family scopes that need no admin review, applied in one owner click."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SKILL = Path(__file__).resolve().parents[1]
REFS = SKILL / "references"
SCOPES = REFS / "feishu-agent-app-scopes.txt"
HELPER = REFS / "scripts" / "feishu_scope_apply_url.py"
DOC_REPORT = REFS / "feishu-doc-report.md"
GROUP_SYNC = REFS / "feishu-group-sync.md"

# what the doc-report flow needs (bot: create doc, upload images, grant the group, read back)
NEEDED = {"docx:document", "docx:document:create", "docs:permission.member:create", "docs:document.media:upload",
          "drive:file:upload", "docs:document.content:read"}


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(SKILL)} is required")
    return path.read_text(encoding="utf-8")


def scopes() -> list[str]:
    return [line.strip() for line in read(SCOPES).splitlines() if line.strip() and not line.startswith("#")]


def run(*args: str) -> subprocess.CompletedProcess:
    if not HELPER.is_file():
        raise AssertionError(f"{HELPER.relative_to(SKILL)} is required")
    return subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True)


class ScopeListTest(unittest.TestCase):
    def test_lists_only_wellformed_unique_scopes(self) -> None:
        items = scopes()
        self.assertGreater(len(items), 30)
        self.assertEqual(len(items), len(set(items)), "duplicate scope")
        for item in items:
            self.assertRegex(item, r"^[a-z_]+(:[a-z_.]+)+$")

    def test_covers_what_the_doc_report_flow_needs(self) -> None:
        self.assertLessEqual(NEEDED, set(scopes()))

    def test_no_user_only_or_broad_admin_scopes(self) -> None:
        for item in scopes():
            self.assertNotEqual(item, "offline_access")
            self.assertFalse(item.startswith(("admin:", "contact:user", "mail:")), item)


class ApplyUrlHelperTest(unittest.TestCase):
    def test_prints_the_scope_apply_url_for_the_app(self) -> None:
        result = run("cli_aa255610ab78dbd1")
        self.assertEqual(result.returncode, 0, result.stderr)
        url = urlparse(result.stdout.strip())
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "open.feishu.cn", "/page/scope-apply"))
        query = parse_qs(url.query)
        self.assertEqual(query["clientID"], ["cli_aa255610ab78dbd1"])
        self.assertEqual(query["scopes"][0].split(","), scopes())

    def test_rejects_an_app_id_that_is_not_a_cli_id(self) -> None:
        for bad in ("", "aa255610", "cli_x&scopes=evil", "cli_ x", "https://evil.example/cli_a"):
            with self.subTest(app_id=bad):
                result = run(bad)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_needs_exactly_one_argument(self) -> None:
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run("cli_a", "cli_b").returncode, 2)


class DocsTest(unittest.TestCase):
    def test_setup_flow_hands_the_owner_the_url_after_creating_the_app(self) -> None:
        text = read(GROUP_SYNC)
        self.assertIn("feishu_scope_apply_url.py", text)
        self.assertIn("feishu-agent-app-scopes.txt", text)

    def test_doc_report_guide_points_at_the_bundle_instead_of_two_scopes(self) -> None:
        text = read(DOC_REPORT)
        self.assertIn("feishu_scope_apply_url.py", text)
        self.assertRegex(text, r"免审")


if __name__ == "__main__":
    unittest.main()
