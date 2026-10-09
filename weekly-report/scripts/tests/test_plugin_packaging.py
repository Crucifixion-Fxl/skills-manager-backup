from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY = Path(__file__).resolve().parents[5]
PACKAGE_SCRIPT = REPOSITORY / "scripts" / "package_plugin.py"


def _load_package_module():
    spec = importlib.util.spec_from_file_location("package_plugin", PACKAGE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("package_plugin module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    original_path = sys.path[:]
    try:
        sys.path.insert(0, str(REPOSITORY / "scripts"))
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


def test_analytics_plugin_contains_weekly_report_and_its_review_dependencies(tmp_path):
    module = _load_package_module()
    destination = tmp_path / "plugin"

    module._stage_analytics_plugin(REPOSITORY, destination)

    for skill_name in (
        "weekly-report",
        "weekly-report-publish",
        "worktime-filing",
        "work-method-retrospective",
    ):
        assert (destination / "skills" / skill_name / "SKILL.md").is_file()

    assert not (destination / "skills" / "agent-harness" / "find-your-skill-gaps").exists()

    collector = destination / "skills" / "weekly-report" / "scripts" / "collect_langfuse_analytics.py"
    help_result = subprocess.run([sys.executable, str(collector), "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0, help_result.stderr
    unavailable_result = subprocess.run(
        [sys.executable, str(collector), "--since", "2026-09-08", "--until", "2026-09-14"],
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("LANGFUSE_")},
    )
    assert unavailable_result.returncode == 0, unavailable_result.stderr
    assert json.loads(unavailable_result.stdout) == {
        "available": False,
        "reason": "langfuse_not_configured",
    }
