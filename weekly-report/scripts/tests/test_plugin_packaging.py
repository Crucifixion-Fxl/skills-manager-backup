from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[4]
PACKAGE_SCRIPT = REPOSITORY / "scripts" / "package_plugin.py"


def _load_package_module():
    spec = importlib.util.spec_from_file_location("package_plugin", PACKAGE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("package_plugin module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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

    assert not (destination / "skills" / "find-your-skill-gaps").exists()
