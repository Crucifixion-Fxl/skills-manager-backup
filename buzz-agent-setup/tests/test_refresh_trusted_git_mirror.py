from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_trusted_git_mirror.py"
# The script (and the tests exercising it) hardcode /usr/bin/git, matching
# this codebase's convention of never resolving a trusted binary via PATH
# (see e.g. /usr/bin/systemctl elsewhere). Some CI images are minimal enough
# not to ship git at all -- skip rather than false-fail there, the same
# precedent as this project's existing bwrap skip (skills!985).
HAS_GIT = os.path.exists("/usr/bin/git")


def load_module():
    spec = importlib.util.spec_from_file_location("refresh_trusted_git_mirror", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load refresh_trusted_git_mirror")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAS_GIT, "/usr/bin/git not installed in this image")
class RefreshTrustedGitMirrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.home.chmod(0o700)
        self.upstream = self.home / "upstream-src"
        self.upstream.mkdir()
        self._git(self.upstream, "init", "--quiet", "--initial-branch=main")
        (self.upstream / "f").write_text("1\n")
        self._git(self.upstream, "add", "f")
        self._git(self.upstream, "commit", "--quiet", "-m", "one")
        self.first = self._git(self.upstream, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }

    def _git(self, cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(cwd), *args],
            env=self._env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return completed.stdout.decode("utf-8").strip()

    def _mirror_path(self) -> Path:
        return self.home / ".local/share/buzz-agent-setup/git-mirror/skills.git"

    def test_initial_clone_creates_a_trusted_owner_only_mirror(self) -> None:
        module = load_module()
        module.CANONICAL_SKILLS_REMOTE = str(self.upstream)
        result = module.refresh(self.home)
        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])
        self.assertEqual(result["main"], self.first)
        mirror = self._mirror_path()
        mode = stat.S_IMODE(mirror.lstat().st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_refresh_fetches_new_commits_into_an_existing_trusted_mirror(self) -> None:
        module = load_module()
        module.CANONICAL_SKILLS_REMOTE = str(self.upstream)
        module.refresh(self.home)
        (self.upstream / "f").write_text("2\n")
        self._git(self.upstream, "add", "f")
        self._git(self.upstream, "commit", "--quiet", "-m", "two")
        second = self._git(self.upstream, "rev-parse", "HEAD")
        result = module.refresh(self.home)
        self.assertTrue(result["ok"])
        self.assertFalse(result["created"])
        self.assertEqual(result["main"], second)

    def test_refuses_to_touch_an_existing_mirror_with_the_wrong_remote(self) -> None:
        module = load_module()
        module.CANONICAL_SKILLS_REMOTE = str(self.upstream)
        module.refresh(self.home)
        mirror = self._mirror_path()
        self._git(mirror, "remote", "set-url", "origin", "git@attacker.example:x.git")
        with self.assertRaises(ValueError):
            module.refresh(self.home)

    def test_refuses_an_existing_mirror_that_is_not_owner_only(self) -> None:
        module = load_module()
        module.CANONICAL_SKILLS_REMOTE = str(self.upstream)
        module.refresh(self.home)
        mirror = self._mirror_path()
        mirror.chmod(0o750)
        try:
            with self.assertRaises(ValueError):
                module.refresh(self.home)
        finally:
            mirror.chmod(0o700)

    def test_refuses_an_untrusted_parent_directory_chain(self) -> None:
        module = load_module()
        module.CANONICAL_SKILLS_REMOTE = str(self.upstream)
        (self.home / ".local").mkdir()
        (self.home / ".local").chmod(0o777)
        with self.assertRaises(ValueError):
            module.refresh(self.home)

    def test_cli_prints_json_and_never_leaks_the_home_argument_on_bad_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--home", "not-absolute"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stderr)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
