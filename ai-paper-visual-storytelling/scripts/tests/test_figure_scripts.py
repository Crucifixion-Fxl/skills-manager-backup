from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType


SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def load_script(name: str) -> ModuleType:
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_brief_preserves_valid_brief(tmp_path: Path) -> None:
    validator = load_script("validate_figure_html")
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(
        """{
  "story_spine": "input to output",
  "figure_role": "overview",
  "selected_context": [{"id": "ctx-1"}],
  "panels": [{"id": "panel-a", "context_ids": ["ctx-1"]}],
  "caption_suggestion": "Overview.",
  "caption_outside_figure": true,
  "open_assumptions": []
}
""",
        encoding="utf-8",
    )

    brief, errors = validator.load_brief(brief_file)

    assert errors == []
    assert brief["panels"][0]["id"] == "panel-a"


def test_load_brief_preserves_panel_reference_errors(tmp_path: Path) -> None:
    validator = load_script("validate_figure_html")
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(
        """{
  "story_spine": "input to output",
  "figure_role": "overview",
  "selected_context": [{"id": "ctx-1"}],
  "panels": [{"id": "panel-a", "context_ids": ["missing"]}],
  "caption_suggestion": "Overview.",
  "caption_outside_figure": true,
  "open_assumptions": []
}
""",
        encoding="utf-8",
    )

    _, errors = validator.load_brief(brief_file)

    assert errors == [
        "visual brief panel panel-a references unknown context id: missing",
    ]


def test_load_brief_stays_a_small_orchestrator() -> None:
    source = (SCRIPTS_DIR / "validate_figure_html.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    load_brief = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "load_brief"
    )
    decision_nodes = sum(
        isinstance(node, (ast.If, ast.For, ast.Try)) for node in ast.walk(load_brief)
    )

    assert decision_nodes <= 3


def test_frame_glob_literal_has_a_single_source_of_truth() -> None:
    source = (SCRIPTS_DIR / "extract_video_frames.py").read_text(encoding="utf-8")

    assert source.count('"frame-*.jpg"') == 1
