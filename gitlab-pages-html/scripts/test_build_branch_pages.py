from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("build-branch-pages.sh")
SKILL = SCRIPT.parent.parent / "SKILL.md"
REFERENCE = SCRIPT.parent.parent / "references" / "branch-pages-publisher.md"
ISSUE_SOP = SCRIPT.parents[2] / "gitlab-issue-sop" / "SKILL.md"
CI_CONFIG = SCRIPT.parents[3] / ".gitlab-ci.yml"


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class BranchPagesBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        self.source = root / "source"
        self.publisher = root / "publisher"

        run("git", "init", "--bare", str(self.remote), cwd=root)
        self.source.mkdir()
        run("git", "init", "--initial-branch=main", cwd=self.source)
        run("git", "config", "user.name", "Branch Pages Test", cwd=self.source)
        run("git", "config", "user.email", "branch-pages@example.invalid", cwd=self.source)
        run("git", "remote", "add", "origin", str(self.remote), cwd=self.source)
        self._write_docs("main")
        run("git", "add", "docs", cwd=self.source)
        run("git", "commit", "-m", "docs: seed main", cwd=self.source)
        run("git", "push", "-u", "origin", "main", cwd=self.source)
        run("git", "clone", "--branch", "main", str(self.remote), str(self.publisher), cwd=root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_docs(self, value: str) -> None:
        docs = self.source / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "index.html").write_text(value, encoding="utf-8")
        architecture = docs / "architecture"
        architecture.mkdir(exist_ok=True)
        (architecture / "example.html").write_text(value, encoding="utf-8")

    def _push_branch(self, name: str, value: str) -> None:
        run("git", "checkout", "-b", name, "main", cwd=self.source)
        self._write_docs(value)
        run("git", "add", "docs", cwd=self.source)
        run("git", "commit", "-m", f"docs: add {name}", cwd=self.source)
        run("git", "push", "origin", name, cwd=self.source)
        run("git", "checkout", "main", cwd=self.source)

    def _push_docs_file(self, name: str) -> None:
        run("git", "checkout", "-b", name, "main", cwd=self.source)
        run("git", "rm", "-r", "docs", cwd=self.source)
        (self.source / "docs").write_text("not a directory", encoding="utf-8")
        run("git", "add", "docs", cwd=self.source)
        run("git", "commit", "-m", f"docs: replace directory on {name}", cwd=self.source)
        run("git", "push", "origin", name, cwd=self.source)
        run("git", "checkout", "main", cwd=self.source)

    def _build(self, ref: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PUBLISH_REF"] = ref
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.publisher,
            env=env,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def test_incrementally_preserves_existing_branch_snapshots(self) -> None:
        self._push_branch("docs/topic", "topic")

        self._build("main")
        self._build("docs/topic")

        self.assertEqual(
            (self.publisher / "public/main/docs/index.html").read_text(), "main"
        )
        self.assertEqual(
            (
                self.publisher
                / "public/main/docs/architecture/example.html"
            ).read_text(),
            "main",
        )
        self.assertEqual(
            (self.publisher / "public/docs-topic/docs/index.html").read_text(), "topic"
        )
        self.assertEqual(
            (self.publisher / "public/.branch-map.tsv").read_text().splitlines(),
            ["docs-topic\tdocs/topic", "main\tmain"],
        )

    def test_slug_collision_fails_closed(self) -> None:
        self._push_branch("feature/a", "slash")
        self._push_branch("feature-a", "dash")
        self._build("feature/a")

        result = self._build("feature-a", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("slug collision", result.stdout)
        self.assertEqual(
            (self.publisher / "public/feature-a/docs/index.html").read_text(), "slash"
        )

    def test_empty_slug_fails_before_touching_public_root(self) -> None:
        result = self._build("___", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty slug", result.stdout)
        self.assertFalse((self.publisher / "public").exists())

    def test_line_break_in_ref_fails_before_fetching(self) -> None:
        for ref in ("invalid\nref", "invalid\rref"):
            with self.subTest(ref=repr(ref)):
                result = self._build(ref, check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid source ref", result.stdout)
                self.assertNotIn("fetch docs", result.stdout)
                self.assertFalse((self.publisher / "public").exists())

    def test_root_index_escapes_raw_branch_name(self) -> None:
        self._push_branch("feature&x", "ampersand")

        self._build("feature&x")

        index = (self.publisher / "public/index.html").read_text()
        self.assertIn('href="feature-x/docs/"', index)
        self.assertIn("feature&amp;x", index)
        self.assertNotIn(">feature&x<", index)

    def test_docs_object_must_be_a_tree(self) -> None:
        self._push_docs_file("invalid/docs-file")

        result = self._build("invalid/docs-file", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no docs directory", result.stdout)
        self.assertFalse((self.publisher / "public/invalid-docs-file").exists())


class BranchPagesReferenceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.reference = REFERENCE.read_text(encoding="utf-8")
        cls.issue_sop = ISSUE_SOP.read_text(encoding="utf-8")
        cls.ci_config = CI_CONFIG.read_text(encoding="utf-8")

    def test_issue_html_links_publish_and_preserve_relative_path(self) -> None:
        for document in (self.skill, self.reference, self.issue_sop):
            self.assertIn("public/<branch-slug>/<html-relative-path>", document)
            self.assertIn(
                "<CI_PAGES_URL>/<branch-slug>/<html-relative-path>", document
            )
        self.assertIn("$gitlab-pages-html", self.issue_sop)

    def test_ci_runs_contract_for_issue_sop_changes(self) -> None:
        self.assertEqual(
            self.ci_config.count('- "skills/gitlab-issue-sop/SKILL.md"'), 2
        )

    def test_push_and_mr_map_to_current_and_target_branch(self) -> None:
        self.assertIn(
            'PUBLISH_REF: $CI_MERGE_REQUEST_TARGET_BRANCH_NAME', self.reference
        )
        self.assertIn('PUBLISH_REF: $CI_COMMIT_BRANCH', self.reference)
        self.assertIn(
            'if: \'$CI_PIPELINE_SOURCE == "push" && '
            '$CI_COMMIT_BRANCH != "docs-pages"\'',
            self.reference,
        )
        self.assertNotIn(
            'PUBLISH_REF: $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME', self.reference
        )

    def test_source_job_uses_ephemeral_token_and_literal_form_values(self) -> None:
        self.assertIn('--form-string "token=${CI_JOB_TOKEN:?}"', self.reference)
        self.assertIn(
            '--form-string "variables[PUBLISH_REF]=${PUBLISH_REF}"', self.reference
        )
        self.assertIn("--connect-timeout 10", self.reference)
        self.assertIn("--max-time 60", self.reference)
        self.assertNotIn('--form "token=', self.reference)

    def test_publisher_write_target_and_credential_scope_are_fixed(self) -> None:
        self.assertIn('git push --quiet origin HEAD:docs-pages', self.reference)
        self.assertIn('name: pages-publisher', self.reference)
        self.assertIn('git add -f -- public', self.reference)
        self.assertNotIn('\n      git add -f .\n', self.reference)


if __name__ == "__main__":
    unittest.main()
