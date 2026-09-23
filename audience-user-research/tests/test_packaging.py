from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CONTRACTS = (
    "audience-platform-public-v1.openapi.json",
    "audience-platform-public-v2.openapi.json",
    "operation-registry.json",
    "project-control-plane.openapi.json",
    "semantic-owner.json",
    "source.json",
)


class PackagingTests(unittest.TestCase):
    def test_wheel_install_includes_contracts_and_starts_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory) / "dist"
            site = Path(directory) / "site"
            work = Path(directory) / "work"
            dist.mkdir()
            work.mkdir()
            built = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--disable-pip-version-check",
                    "--no-build-isolation",
                    "--no-deps",
                    "--wheel-dir",
                    str(dist),
                    str(ROOT),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            wheels = list(dist.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            names = zipfile.ZipFile(wheels[0]).namelist()
            for contract in REQUIRED_CONTRACTS:
                self.assertIn(f"user_research/contracts/{contract}", names)

            installed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--no-deps",
                    "--target",
                    str(site),
                    str(wheels[0]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            env = {
                **os.environ,
                "PYTHONPATH": str(site),
                "PYTHONNOUSERSITE": "1",
            }
            env.pop("PYTHONHOME", None)
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from importlib import resources;"
                        "from user_research.allowlist import load_bundled_document;"
                        "root = resources.files('user_research').joinpath('contracts');"
                        "assert root.joinpath('project-control-plane.openapi.json').is_file();"
                        "assert isinstance(load_bundled_document('v3'), dict)"
                    ),
                ],
                cwd=work,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            capabilities = subprocess.run(
                [sys.executable, "-m", "user_research", "capabilities"],
                cwd=work,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(capabilities.returncode, 0, capabilities.stdout + capabilities.stderr)
            payload = json.loads(capabilities.stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["capability_source"], "bundled_contracts")
            self.assertIn("get_project_personal_key_context", payload["operations"])
            self.assertIn("personal_voc_execution_get", payload["operations"])
            self.assertIn("personal_research_journey_form", payload["operations"])
            self.assertTrue(all(not name.startswith("send_") for name in payload["operations"]))

    def test_python_39_imports_postponed_union_annotations(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.9"', pyproject)
        self.assertIn('target-version = "py39"', pyproject)
        self.assertIn("Python 3.9+", readme)
        self.assertIn("from __future__ import annotations", readme)
        annotation = re.compile(r"(?:^|\s)(?:\w+\s*)?:[^\n#]*\||->[^\n#]*\|", re.MULTILINE)
        for folder in ("src", "scripts"):
            for path in (ROOT / folder).rglob("*.py"):
                if any(part in {"build", "__pycache__"} for part in path.parts):
                    continue
                text = path.read_text(encoding="utf-8")
                if annotation.search(text):
                    self.assertIn(
                        "from __future__ import annotations",
                        text,
                        str(path.relative_to(ROOT)),
                    )
        from user_research.client import SafeApiError

        status = SafeApiError.__init__.__annotations__["status"]
        self.assertIsInstance(status, str)
        self.assertEqual(status, "int | None")


if __name__ == "__main__":
    unittest.main()
