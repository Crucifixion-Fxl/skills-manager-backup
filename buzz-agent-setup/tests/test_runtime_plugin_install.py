"""L1 contracts for resolving the plugin revision used by a live Agent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "resolve_plugin_install.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("resolve_plugin_install", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load resolve_plugin_install")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimePluginInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / "cache" / "abc123def456"
        for name in ("buzz-agent-setup", "gitlab-issue-sop"):
            skill = self.install / "skills" / name / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        self.registry = self.root / "installed_plugins.json"
        self.write_registry(
            [
                {
                    "installPath": str(self.install),
                    "gitCommitSha": "a" * 40,
                    "version": "abc123def456",
                    "scope": "user",
                }
            ]
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_registry(self, records: list[dict[str, str]]) -> None:
        self.registry.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {"addx@addx-engineering": records},
                }
            ),
            encoding="utf-8",
        )

    def test_resolves_exact_registered_install_and_required_skills(self) -> None:
        module = load_module()
        receipt = module.resolve_install(
            self.registry,
            "addx@addx-engineering",
            ["buzz-agent-setup", "gitlab-issue-sop"],
        )
        self.assertEqual(str(self.install), receipt["install_path"])
        self.assertEqual("a" * 40, receipt["git_commit_sha"])
        self.assertEqual(
            ["buzz-agent-setup", "gitlab-issue-sop"], receipt["required_skills"]
        )

    def test_rejects_multiple_active_records(self) -> None:
        module = load_module()
        record = json.loads(self.registry.read_text(encoding="utf-8"))["plugins"][
            "addx@addx-engineering"
        ][0]
        self.write_registry([record, record])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            module.resolve_install(self.registry, "addx@addx-engineering", [])

    def test_rejects_symlinked_install_root(self) -> None:
        module = load_module()
        link = self.root / "linked-install"
        link.symlink_to(self.install, target_is_directory=True)
        self.write_registry(
            [
                {
                    "installPath": str(link),
                    "gitCommitSha": "a" * 40,
                    "version": "abc123def456",
                    "scope": "user",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            module.resolve_install(self.registry, "addx@addx-engineering", [])

    def test_rejects_missing_or_wrong_skill_frontmatter(self) -> None:
        module = load_module()
        with self.assertRaisesRegex(ValueError, "missing required Skill"):
            module.resolve_install(
                self.registry, "addx@addx-engineering", ["does-not-exist"]
            )
        skill = self.install / "skills" / "gitlab-issue-sop" / "SKILL.md"
        skill.write_text("---\nname: wrong-name\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            module.resolve_install(
                self.registry, "addx@addx-engineering", ["gitlab-issue-sop"]
            )

    def test_cli_emits_a_machine_readable_receipt(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--registry",
                str(self.registry),
                "--plugin-id",
                "addx@addx-engineering",
                "--require-skill",
                "buzz-agent-setup",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(str(self.install), payload["install_path"])
        self.assertEqual(["buzz-agent-setup"], payload["required_skills"])

    def codex_fixture(self) -> tuple[dict[str, object], Path, Path, str]:
        cache_root = self.root / "codex-cache"
        install = cache_root / "addx" / "addx" / "1.0.0"
        skill = install / "skills" / "agent-harness" / "buzz-agent-setup" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: buzz-agent-setup\n---\n", encoding="utf-8")
        source = self.root / "marketplace"
        source_skill = source / "skills" / "agent-harness" / "buzz-agent-setup" / "SKILL.md"
        source_skill.parent.mkdir(parents=True)
        source_skill.write_bytes(skill.read_bytes())
        revision = "b" * 40
        listing = {
            "installed": [
                {
                    "pluginId": "addx@addx",
                    "name": "addx",
                    "marketplaceName": "addx",
                    "version": "1.0.0",
                    "installed": True,
                    "enabled": True,
                    "source": {"source": "local", "path": str(source)},
                    "marketplaceSource": {
                        "sourceType": "git",
                        "source": "git@gitlab.example:engineering/skills.git",
                    },
                }
            ]
        }
        return listing, cache_root, source, revision

    def fake_git_run(self, source: Path, revision: str):
        clean_skill = b"---\nname: buzz-agent-setup\n---\n"

        def run(args, **kwargs):
            self.assertEqual(["git", "-C", str(source)], args[:3])
            command = args[3:]
            if command == ["rev-parse", "HEAD"]:
                stdout = f"{revision}\n"
            elif command == ["status", "--porcelain", "--untracked-files=no"]:
                source_skill = source / "skills/agent-harness/buzz-agent-setup/SKILL.md"
                stdout = "" if source_skill.read_bytes() == clean_skill else " M skills/agent-harness/buzz-agent-setup/SKILL.md\n"
            elif command == ["branch", "--show-current"]:
                stdout = "test/runtime-fixture\n"
            else:
                raise AssertionError(f"unexpected git command: {args}")
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        return run

    def test_resolves_codex_plugin_list_and_git_marketplace_snapshot(self) -> None:
        module = load_module()
        listing, cache_root, source, revision = self.codex_fixture()
        with mock.patch.object(
            module.subprocess,
            "run",
            side_effect=self.fake_git_run(source, revision),
        ):
            receipt = module.resolve_codex_install(
                listing, cache_root, "addx@addx", ["buzz-agent-setup"]
            )
        self.assertEqual(
            str(cache_root / "addx" / "addx" / "1.0.0"), receipt["install_path"]
        )
        self.assertEqual(revision, receipt["git_commit_sha"])

    def test_rejects_codex_cache_that_differs_from_marketplace_snapshot(self) -> None:
        module = load_module()
        listing, cache_root, source, revision = self.codex_fixture()
        skill = cache_root / "addx/addx/1.0.0/skills/agent-harness/buzz-agent-setup/SKILL.md"
        skill.write_text("---\nname: buzz-agent-setup\n---\ndrift\n", encoding="utf-8")
        with mock.patch.object(
            module.subprocess,
            "run",
            side_effect=self.fake_git_run(source, revision),
        ):
            with self.assertRaisesRegex(ValueError, "differs from marketplace"):
                module.resolve_codex_install(
                    listing, cache_root, "addx@addx", ["buzz-agent-setup"]
                )

    def test_rejects_dirty_codex_marketplace_snapshot(self) -> None:
        module = load_module()
        listing, cache_root, source, _ = self.codex_fixture()
        source_skill = source / "skills/agent-harness/buzz-agent-setup/SKILL.md"
        source_skill.write_text(source_skill.read_text() + "dirty\n", encoding="utf-8")
        with mock.patch.object(
            module.subprocess,
            "run",
            side_effect=self.fake_git_run(source, "b" * 40),
        ):
            with self.assertRaisesRegex(ValueError, "tracked changes"):
                module.resolve_codex_install(
                    listing, cache_root, "addx@addx", ["buzz-agent-setup"]
                )

    def test_missing_git_fails_closed_with_a_validation_error(self) -> None:
        module = load_module()
        listing, cache_root, _, _ = self.codex_fixture()
        with mock.patch.object(
            module.subprocess,
            "run",
            side_effect=FileNotFoundError("git"),
        ):
            with self.assertRaisesRegex(ValueError, "readable git revision"):
                module.resolve_codex_install(
                    listing, cache_root, "addx@addx", ["buzz-agent-setup"]
                )

    def test_registered_classified_and_mixed_installs(self) -> None:
        module = load_module()
        skills = self.install / "skills"
        (skills / "agent-harness").mkdir()
        (skills / "buzz-agent-setup").rename(skills / "agent-harness/buzz-agent-setup")
        receipt = module.resolve_install(
            self.registry, "addx@addx-engineering", ["buzz-agent-setup", "gitlab-issue-sop"]
        )
        self.assertEqual(receipt["required_skills"], ["buzz-agent-setup", "gitlab-issue-sop"])
        (skills / "collaboration").mkdir()
        (skills / "gitlab-issue-sop").rename(skills / "collaboration/gitlab-issue-sop")
        module.resolve_install(
            self.registry, "addx@addx-engineering", ["buzz-agent-setup", "gitlab-issue-sop"]
        )

    def test_rejects_duplicate_names_across_flat_and_category(self) -> None:
        module = load_module()
        duplicate = self.install / "skills/agent-harness/buzz-agent-setup/SKILL.md"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text("---\nname: buzz-agent-setup\n---\n")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            module.resolve_install(self.registry, "addx@addx-engineering", ["buzz-agent-setup"])
        (self.install / "skills/other").mkdir()
        (self.install / "skills/buzz-agent-setup").rename(
            self.install / "skills/other/buzz-agent-setup"
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            module.resolve_install(self.registry, "addx@addx-engineering", ["buzz-agent-setup"])

    def test_rejects_symlink_at_every_skill_ancestor_or_file(self) -> None:
        module = load_module()
        for component in ("skills", "category", "skill", "file"):
            with self.subTest(component=component):
                install = self.root / ("links-" + component)
                skill = install / "skills/category/buzz-agent-setup/SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text("---\nname: buzz-agent-setup\n---\n")
                node = {"skills":install/"skills", "category":skill.parent.parent,
                        "skill":skill.parent, "file":skill}[component]
                outside = self.root / ("outside-" + component)
                node.rename(outside)
                node.symlink_to(outside, target_is_directory=component != "file")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    module._validate_install_root(install, ["buzz-agent-setup"])

    def test_wrong_or_ambiguous_frontmatter_never_supplies_identity(self) -> None:
        module = load_module()
        skill = self.install / "skills/buzz-agent-setup/SKILL.md"
        for content in ("---\nname: wrong\n---\n", "---\nname: buzz-agent-setup\n",
                        "---\nname: buzz-agent-setup\nname: wrong\n---\n"):
            with self.subTest(content=content):
                skill.write_text(content)
                with self.assertRaisesRegex(ValueError, "frontmatter"):
                    module.resolve_install(self.registry, "addx@addx-engineering", ["buzz-agent-setup"])

    def test_skill_name_cannot_escape_and_deeper_examples_are_not_scanned(self) -> None:
        module = load_module()
        for name in ("../buzz-agent-setup", "category/buzz-agent-setup", "/tmp", "a.b"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid Skill"):
                module._validate_install_root(self.install, [name])
        nested = self.install / "skills/category/examples/hidden/SKILL.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("---\nname: hidden\n---\n")
        with self.assertRaisesRegex(ValueError, "missing required Skill"):
            module._validate_install_root(self.install, ["hidden"])
        # A real flat skill's fixtures/resources do not become plugin entries.
        nested = self.install / "skills/buzz-agent-setup/hidden/SKILL.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("---\nname: hidden\n---\n")
        with self.assertRaisesRegex(ValueError, "missing required Skill"):
            module._validate_install_root(self.install, ["hidden"])

    def test_codex_legacy_flat_cache_can_match_classified_git_source(self) -> None:
        module = load_module()
        listing, cache_root, source, revision = self.codex_fixture()
        skills = cache_root / "addx/addx/1.0.0/skills"
        (skills / "agent-harness/buzz-agent-setup").rename(skills / "buzz-agent-setup")
        with mock.patch.object(module.subprocess, "run", side_effect=self.fake_git_run(source, revision)):
            receipt = module.resolve_codex_install(listing, cache_root, "addx@addx", ["buzz-agent-setup"])
        self.assertEqual(receipt["git_commit_sha"], revision)

    def test_classified_cache_with_real_git_snapshot_retains_git_and_digest_gates(self) -> None:
        module = load_module()
        listing, cache_root, source, _ = self.codex_fixture()
        for command in (["init", "-q"], ["add", "skills"],
                        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True)
        receipt = module.resolve_codex_install(listing, cache_root, "addx@addx", ["buzz-agent-setup"])
        expected = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(receipt["git_commit_sha"], expected)
        installed = cache_root / "addx/addx/1.0.0/skills/agent-harness/buzz-agent-setup/SKILL.md"
        original = installed.read_bytes()
        installed.write_bytes(original + b"drift\n")
        with self.assertRaisesRegex(ValueError, "differs from marketplace"):
            module.resolve_codex_install(listing, cache_root, "addx@addx", ["buzz-agent-setup"])
        installed.write_bytes(original)
        (source / "skills/agent-harness/buzz-agent-setup/SKILL.md").write_bytes(original + b"dirty\n")
        with self.assertRaisesRegex(ValueError, "tracked changes"):
            module.resolve_codex_install(listing, cache_root, "addx@addx", ["buzz-agent-setup"])

    def test_classified_cache_install_metadata_cannot_override_git_revision(self) -> None:
        module = load_module()
        listing, cache_root, source, revision = self.codex_fixture()
        (cache_root / "addx/addx/1.0.0/.codex-marketplace-install.json").write_text(
            json.dumps({"source_type":"git", "revision":"c"*40})
        )
        with mock.patch.object(module.subprocess, "run", side_effect=self.fake_git_run(source, revision)):
            with self.assertRaisesRegex(ValueError, "disagrees with marketplace"):
                module.resolve_codex_install(listing, cache_root, "addx@addx", ["buzz-agent-setup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
