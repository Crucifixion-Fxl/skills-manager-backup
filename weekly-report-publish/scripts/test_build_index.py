#!/usr/bin/env python3
import importlib.util
import json
import hashlib
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("build_index", Path(__file__).with_name("build_index.py"))
build_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_index)

APPROVAL_SPEC = importlib.util.spec_from_file_location(
    "validate_approval", Path(__file__).with_name("validate_approval.py")
)
validate_approval = importlib.util.module_from_spec(APPROVAL_SPEC)
APPROVAL_SPEC.loader.exec_module(validate_approval)
WEEKLY_REPORT_SKILL = Path(__file__).resolve().parents[2] / "weekly-report" / "SKILL.md"


def write_approval(path, report, *, author="test-user", week="2099-W53", **updates):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    payload = {
        "schema": "addx.weekly_report_approval.v1",
        "approval_source": "top-level-user-message",
        "author": author,
        "week": week,
        "report_path": str(report.resolve()),
        "round_id": "round-1",
        "report_digest": "sha256:" + hashlib.sha256(report.read_bytes()).hexdigest(),
        "quality_review_digest": "sha256:" + "a" * 64,
        "approved_by": "test-user",
        "accepted_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "nonce": "a" * 32,
        "consumed": False,
    }
    payload.update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return payload


class BuildIndexTest(unittest.TestCase):
    def test_canonical_weekly_html_template_passes_publish_validation(self):
        source = WEEKLY_REPORT_SKILL.read_text(encoding="utf-8")
        template = source.split("```html\n", 1)[1].split("\n```", 1)[0]
        rendered = template.replace("<BOT_NAME>", "Codex")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "weekly-report-2099-12-31.html"
            report.write_text(rendered, encoding="utf-8")
            report.chmod(0o600)

            validate_approval.validate_report(report)

    def test_publish_guard_compares_authors_to_head(self):
        publish_script = Path(__file__).with_name("publish.sh").read_text(encoding="utf-8")
        self.assertIn("authors.json author_aliases.json", publish_script)
        self.assertIn('diff --quiet HEAD -- "$METADATA_NAME"', publish_script)
        self.assertIn('merge-base --is-ancestor "$COMMIT_SHA" "$REMOTE_MAIN_SHA"', publish_script)
        self.assertIn('HEAD:reports/$AUTHOR/$WEEK.html', publish_script)
        self.assertIn('origin/main:reports/$AUTHOR/$WEEK.html', publish_script)

    def test_auto_publish_rejects_mismatched_authenticated_host_before_clone(self):
        publish_script = Path(__file__).with_name("publish.sh")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            report = next(
                (
                    root.parent / f"weekly-report-2099-12-{day:02d}.html"
                    for day in range(31, 0, -1)
                    if not (root.parent / f"weekly-report-2099-12-{day:02d}.html").exists()
                ),
                None,
            )
            if report is None:
                self.skipTest("no unused fixed weekly-report path under /tmp")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_glab = fake_bin / "glab"
            fake_glab.write_text(
                "#!/bin/sh\n"
                "if [ \"$1 $2 $3\" = \"config get host\" ]; then echo gitlab.example.com; exit 0; fi\n"
                "if [ \"$1 $2\" = \"api user\" ]; then exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_glab.chmod(0o755)
            report.write_text("<html></html>", encoding="utf-8")
            try:
                env = {
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "WEEKLY_REPORT_REPO": "git@evil.example:team/reports.git",
                    "WEEKLY_PUBLISH_CACHE": str(root / "cache"),
                }
                result = subprocess.run(
                    [str(publish_script), str(report), "--week", "2026-W31", "--auto", "--approval-receipt", str(root / "dummy.json")],
                    capture_output=True,
                    check=False,
                    env=env,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not match authenticated glab host", result.stderr)
                self.assertFalse((root / "cache").exists())
            finally:
                report.unlink(missing_ok=True)

    def test_auto_publish_rejects_author_and_mismatched_week(self):
        publish_script = Path(__file__).with_name("publish.sh")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            report = next(
                (
                    root.parent / f"weekly-report-2098-12-{day:02d}.html"
                    for day in range(31, 0, -1)
                    if not (root.parent / f"weekly-report-2098-12-{day:02d}.html").exists()
                ),
                None,
            )
            if report is None:
                self.skipTest("no unused fixed weekly-report path under /tmp")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (fake_bin / "glab").write_text(
                "#!/bin/sh\n"
                "if [ \"$1 $2 $3\" = \"config get host\" ]; then echo gitlab.example.com; exit 0; fi\n"
                "if [ \"$1 $2\" = \"api user\" ]; then exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            (fake_bin / "git").write_text(
                "#!/bin/sh\n"
                "if [ \"$1 $2 $3\" = \"config --get user.name\" ]; then echo test-user; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            (fake_bin / "glab").chmod(0o755)
            (fake_bin / "git").chmod(0o755)
            report.write_text("<html></html>", encoding="utf-8")
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "WEEKLY_REPORT_REPO": "git@gitlab.example.com:team/reports.git",
                "WEEKLY_PUBLISH_CACHE": str(root / "cache"),
            }
            try:
                author_result = subprocess.run(
                    [str(publish_script), str(report), "--author", "other-user", "--auto", "--approval-receipt", str(root / "dummy.json")],
                    capture_output=True,
                    check=False,
                    env=env,
                    text=True,
                )
                self.assertNotEqual(author_result.returncode, 0)
                self.assertIn("--author is not allowed with --auto", author_result.stderr)

                week_result = subprocess.run(
                    [str(publish_script), str(report), "--week", "2026-W31", "--auto", "--approval-receipt", str(root / "dummy.json")],
                    capture_output=True,
                    check=False,
                    env=env,
                    text=True,
                )
                self.assertNotEqual(week_result.returncode, 0)
                self.assertIn("does not match report date week", week_result.stderr)
                self.assertFalse((root / "cache").exists())
            finally:
                report.unlink(missing_ok=True)

    def test_auto_publish_requires_approval_receipt_before_external_actions(self):
        publish_script = Path(__file__).with_name("publish.sh")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            report = root.parent / "weekly-report-2097-12-31.html"
            if report.exists():
                self.skipTest("fixed weekly report path is in use")
            report.write_text("<html></html>", encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(publish_script), str(report), "--auto"],
                    capture_output=True,
                    check=False,
                    text=True,
                    env={**os.environ, "WEEKLY_PUBLISH_CACHE": str(root / "cache")},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--approval-receipt is required", result.stderr)
                self.assertFalse((root / "cache").exists())
            finally:
                report.unlink(missing_ok=True)

    def test_auto_publish_refuses_a_concurrently_claimed_receipt(self):
        publish_script = Path(__file__).with_name("publish.sh")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            report = root.parent / "weekly-report-2096-12-31.html"
            if report.exists():
                self.skipTest("fixed weekly report path is in use")
            report.write_text("<html></html>", encoding="utf-8")
            report.chmod(0o600)
            receipt = root / "approval.json"
            lock = Path(str(receipt) + ".publish.lock")
            lock.mkdir()
            try:
                result = subprocess.run(
                    [
                        str(publish_script),
                        str(report),
                        "--week",
                        "2097-W01",
                        "--auto",
                        "--approval-receipt",
                        str(receipt),
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                    env={
                        **os.environ,
                        "WEEKLY_REPORT_REPO": "git@gitlab.example.com:team/reports.git",
                        "WEEKLY_PUBLISH_CACHE": str(root / "cache"),
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("already claimed by another publish", result.stderr)
                self.assertFalse((root / "cache").exists())
            finally:
                report.unlink(missing_ok=True)

    def test_approval_receipt_binds_report_digest_and_is_one_time(self):
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "weekly-report-2099-12-31.html"
            report.write_text("<html>approved</html>", encoding="utf-8")
            report.chmod(0o600)
            receipt = root / "approval.json"
            payload = write_approval(receipt, report)

            validated = validate_approval.validate_receipt(
                receipt,
                report,
                "test-user",
                "2099-W53",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(validated["round_id"], "round-1")

            without_quality_digest = dict(payload)
            del without_quality_digest["quality_review_digest"]
            receipt.write_text(json.dumps(without_quality_digest), encoding="utf-8")
            with self.assertRaisesRegex(
                validate_approval.ApprovalError, "exact allowed fields"
            ):
                validate_approval.validate_receipt(
                    receipt, report, "test-user", "2099-W53"
                )
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            receipt.chmod(0o600)

            report.write_text("<html>changed</html>", encoding="utf-8")
            with self.assertRaisesRegex(validate_approval.ApprovalError, "digest does not match"):
                validate_approval.validate_receipt(receipt, report, "test-user", "2099-W53")

            report.write_text("<html>approved</html>", encoding="utf-8")
            validate_approval.consume_receipt(receipt, payload)
            with self.assertRaisesRegex(validate_approval.ApprovalError, "already consumed"):
                validate_approval.validate_receipt(receipt, report, "test-user", "2099-W53")

    def test_report_validation_rejects_sensitive_content_and_active_markup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "weekly-report-2099-12-31.html"
            receipt = root / "approval.json"
            for content, error in (
                (
                    "<html>Authorization: Bearer sk-FAKESECRET123456</html>",
                    "unredacted sensitive data",
                ),
                ("<html><script>alert(1)</script></html>", "unsafe active markup"),
                (
                    '<html><a href="java&#x73;cript:alert(1)">review</a></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><svg><a xlink:href="java&#x73;cript:alert(1)">x</a></svg></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><a href="java&#x09;script:alert(1)">x</a></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><a href="data:text/html;base64,PHNjcmlwdD4=">x</a></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><svg><a><animate attributeName="href" values="javascript:alert(1)"/></a></svg></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><svg><a id="x"></a><set href="#x" attributeName="href" to="javascript:alert(1)"/></svg></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><style>body{background:u/**/rl(https://evil.example/x)}</style></html>',
                    "unsafe active markup",
                ),
                (
                    "<html><body>sk-FAKE<em>SECRET123456</em></body></html>",
                    "unredacted sensitive data",
                ),
                (
                    "<html><body>owner<span></span>@example.com</body></html>",
                    "unredacted sensitive data",
                ),
                (
                    '<html><style>body::before{content:"sk-FAKE" "SECRET123456"}</style></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><style>body{background-image:image-set("https://evil.example/x" 1x)}</style></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><meta http-equiv="refresh" http-equiv="x" content="0;url=https://evil.example/"></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><body background="https://evil.example/tracker">x</body></html>',
                    "unsafe active markup",
                ),
                (
                    '<html><body><table background="https://evil.example/tracker"><tr><td>x</td></tr></table></body></html>',
                    "unsafe active markup",
                ),
            ):
                with self.subTest(error=error):
                    report.write_text(content, encoding="utf-8")
                    report.chmod(0o600)
                    write_approval(receipt, report)
                    with self.assertRaisesRegex(validate_approval.ApprovalError, error):
                        validate_approval.validate_receipt(
                            receipt, report, "test-user", "2099-W53"
                        )

    def test_mapping_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authors.json"
            path.write_text(json.dumps({"alice": "\u5f20\u4e09", "../bad": "\u574f", "bob": ""}))
            self.assertEqual(build_index.load_author_names(path), {"alice": "\u5f20\u4e09"})

    def test_mapping_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "victim.json"
            target.write_text(json.dumps({"token": "sensitive-value"}))
            link = root / "authors.json"
            link.symlink_to(target)
            self.assertEqual(build_index.load_author_names(link), {})

    def test_mapping_rejects_invalid_shapes_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authors.json"
            invalid_documents = [
                "not-json",
                json.dumps(["alice"]),
                json.dumps({"alice": "x" * 81}),
                json.dumps({"alice": 42}),
            ]
            for document in invalid_documents:
                with self.subTest(document=document):
                    path.write_text(document)
                    self.assertEqual(build_index.load_author_names(path), {})

    def test_aliases_flatten_chains_and_reject_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "author_aliases.json"
            path.write_text(
                json.dumps(
                    {
                        "ws": "legacy",
                        "legacy": "zlin",
                        "loop-a": "loop-b",
                        "loop-b": "loop-a",
                        "bad/path": "zlin",
                    }
                )
            )
            self.assertEqual(
                build_index.load_author_aliases(path),
                {"ws": "zlin", "legacy": "zlin"},
            )

    def test_alias_mapping_preserves_raw_author_and_path(self):
        entries = [
            {"author": "ws", "path": "reports/ws/2026-W28.html", "week": "2026-W28"}
        ]
        merged = build_index.apply_author_aliases(entries, {"ws": "zlin"})
        self.assertEqual(merged[0]["identity"], "zlin")
        self.assertEqual(merged[0]["author"], "ws")
        self.assertEqual(merged[0]["path"], "reports/ws/2026-W28.html")

    def test_duplicate_alias_weeks_remain_individually_selectable(self):
        entries = build_index.apply_author_aliases(
            [
                {"author": "canonical", "week": "2026-W28", "path": "reports/canonical/2026-W28.html"},
                {"author": "legacy", "week": "2026-W28", "path": "reports/legacy/2026-W28.html"},
            ],
            {"legacy": "canonical"},
        )
        rendered = build_index.render_index(entries, "2026-W28", {})
        self.assertIn("duplicateWeek ? e.week + ' · ' + e.author", rendered)
        self.assertIn("findEntry(state.author, state.week, state.sourceAuthor)", rendered)
        self.assertIn("state.sourceAuthor = m[4] || null", rendered)

    def test_latest_nonempty_week_fallback(self):
        entries = [{"week": "2026-W28"}]
        self.assertEqual(build_index.current_index_week(entries, date(2026, 7, 13)), "2026-W28")
        entries.append({"week": "2026-W29"})
        self.assertEqual(build_index.current_index_week(entries, date(2026, 7, 13)), "2026-W29")

    def test_payload_escapes_mixed_case_script_terminator(self):
        rendered = build_index.render_index([], "2026-W28", {"alice": "</SCRIPT>"})
        self.assertNotIn("</SCRIPT>", rendered)
        self.assertIn("\\u003c/SCRIPT>", rendered)

    def test_unmapped_title_does_not_duplicate_slug(self):
        rendered = build_index.render_index([], "2026-W28", {})
        self.assertIn(
            "authorNames[state.author] ? ' · ' + state.author : ''", rendered
        )

    def test_main_keeps_data_and_embedded_payload_in_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "reports" / "alice"
            report_dir.mkdir(parents=True)
            (report_dir / "2026-W28.html").write_text("<p>report</p>")
            alias_report_dir = root / "reports" / "alice-old"
            alias_report_dir.mkdir(parents=True)
            (alias_report_dir / "2026-W27.html").write_text("<p>old report</p>")
            display_name = "\u5f20\u4e09"
            (root / "authors.json").write_text(
                json.dumps({"alice": display_name}), encoding="utf-8"
            )
            (root / "author_aliases.json").write_text(
                json.dumps({"alice-old": "alice"}), encoding="utf-8"
            )

            self.assertEqual(build_index.main(["build_index.py", str(root)]), 0)
            data = json.loads((root / "data.json").read_text(encoding="utf-8"))
            rendered = (root / "index.html").read_text(encoding="utf-8")

            self.assertEqual(data["authors"], {"alice": display_name})
            self.assertEqual(data["author_aliases"], {"alice-old": "alice"})
            self.assertEqual(data["current_week"], "2026-W28")
            self.assertEqual(len(data["entries"]), 2)
            old_entry = next(e for e in data["entries"] if e["author"] == "alice-old")
            self.assertEqual(old_entry["identity"], "alice")
            self.assertEqual(old_entry["path"], "reports/alice-old/2026-W27.html")
            self.assertIn(json.dumps(display_name, ensure_ascii=False)[1:-1], rendered)
            self.assertIn("· ' + author", rendered)


if __name__ == "__main__":
    unittest.main()
