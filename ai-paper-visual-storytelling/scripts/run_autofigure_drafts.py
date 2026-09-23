#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Generate paired AutoFigure text-to-figure and paper-to-figure drafts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SUPPORTED_GENERATION_PROVIDERS = {"openrouter", "bianxie", "gemini"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create paired AutoFigure text-to-figure and paper-to-figure candidates."
    )
    parser.add_argument("--brief", required=True, type=Path, help="Visual brief JSON produced by this skill.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for AutoFigure results and manifest.")
    parser.add_argument("--paper", required=True, type=Path, help="Real paper file, PDF or Markdown, for paper-to-figure.")
    parser.add_argument("--output-format", choices=["svg", "mxgraphxml"], default="svg")
    parser.add_argument("--topic", choices=["paper", "survey", "blog", "textbook"], default="paper")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--quality-threshold", type=float, default=9.0)
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument(
        "--generation-api-key",
        help="Generation API token passed to AutoFigure. Prefer --generation-api-key-file when possible.",
    )
    key_group.add_argument(
        "--generation-api-key-file",
        type=Path,
        help="Path to a per-run token file outside the repo. The file content is passed to AutoFigure.",
    )
    parser.add_argument(
        "--generation-provider",
        choices=sorted(SUPPORTED_GENERATION_PROVIDERS),
        required=True,
        type=lambda value: value.strip().lower(),
        help="AutoFigure provider selector.",
    )
    parser.add_argument("--generation-model", required=True, help="Generation model.")
    parser.add_argument(
        "--generation-base-url",
        required=True,
        help="OpenAI-compatible generation endpoint.",
    )
    parser.add_argument("--enable-enhancement", action="store_true")
    return parser.parse_args()


def load_api_key(args: argparse.Namespace) -> str:
    if args.generation_api_key_file is not None:
        key_file = args.generation_api_key_file.expanduser().resolve()
        if not key_file.is_file():
            raise SystemExit(f"Generation API key file not found: {key_file}")
        return key_file.read_text(encoding="utf-8").strip()
    return (args.generation_api_key or "").strip()


def load_brief(brief_file: Path) -> dict[str, Any]:
    if not brief_file.is_file():
        raise SystemExit(f"Visual brief not found: {brief_file}")
    try:
        brief = json.loads(brief_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Visual brief is not valid JSON: {exc}") from exc
    if not isinstance(brief, dict):
        raise SystemExit("Visual brief JSON must be an object")
    required_for_autofigure = ["story_spine", "figure_role", "selected_context"]
    missing = [key for key in required_for_autofigure if key not in brief]
    if missing:
        raise SystemExit(f"Visual brief missing required AutoFigure field(s): {', '.join(missing)}")
    if not isinstance(brief.get("selected_context"), list) or not brief["selected_context"]:
        raise SystemExit("Visual brief selected_context must be a non-empty list for AutoFigure")
    return brief


def build_brief_description(brief: dict[str, Any]) -> str:
    keys = [
        "story_spine",
        "figure_role",
        "selected_context",
    ]
    constrained = {key: brief.get(key) for key in keys if key in brief}
    return (
        "Create an editable main mechanism figure for an AI/ML paper.\n"
        "Use the following constrained visual brief as the source of truth. "
        "Do not add modules, arrows, claims, or evidence not supported by this brief.\n\n"
        f"{json.dumps(constrained, ensure_ascii=False, indent=2)}"
    )


def jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return str(value)


def result_to_dict(result: Any) -> dict[str, Any]:
    fields = [
        "success",
        "svg_path",
        "mxgraph_path",
        "preview_path",
        "enhanced_paths",
        "final_score",
        "methodology_text",
        "error",
    ]
    return {field: jsonable(getattr(result, field)) for field in fields if getattr(result, field, None) is not None}


def error_result(exc: Exception) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"{type(exc).__name__}: {exc}",
    }


def main() -> int:
    args = parse_args()
    brief_file = args.brief.expanduser().resolve()
    brief = load_brief(brief_file)

    generation_provider = args.generation_provider.strip().lower()
    if generation_provider and generation_provider not in SUPPORTED_GENERATION_PROVIDERS:
        print(
            f"Unsupported generation provider: {generation_provider}. "
            "Use openrouter, bianxie, or gemini.",
            file=sys.stderr,
        )
        return 1

    api_key = load_api_key(args)
    if not api_key:
        print("Missing required value: --generation-api-key or --generation-api-key-file", file=sys.stderr)
        return 1
    generation_model = args.generation_model.strip()
    generation_base_url = args.generation_base_url.strip()

    try:
        from autofigure import AutoFigureAgent, Config
    except ImportError as exc:
        print(
            "AutoFigure is not available in this Python environment. "
            "Run this wrapper with the permanent autofigure-python command, or another Python "
            "environment where AutoFigure is installed.",
            file=sys.stderr,
        )
        return 2

    out_dir = args.out.expanduser().resolve()
    paper_file = args.paper.expanduser().resolve()
    if not paper_file.is_file():
        print(f"Paper file not found: {paper_file}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    config_kwargs: dict[str, Any] = {
        "generation_api_key": api_key,
        "max_iterations": args.max_iterations,
        "quality_threshold": args.quality_threshold,
        "output_dir": str(out_dir / "autofigure-output"),
    }
    if generation_provider:
        config_kwargs["generation_provider"] = generation_provider
    if generation_model:
        config_kwargs["generation_model"] = generation_model
    if generation_base_url:
        config_kwargs["generation_base_url"] = generation_base_url
    config = Config(**config_kwargs)
    agent = AutoFigureAgent(config)
    manifest: dict[str, Any] = {
        "brief": str(brief_file),
        "paper": str(paper_file),
        "output_dir": str(out_dir),
        "generation_provider": getattr(config, "generation_provider", generation_provider),
        "generation_model": getattr(config, "generation_model", generation_model),
        "generation_base_url": getattr(config, "generation_base_url", generation_base_url),
        "drafts": [],
    }

    try:
        result = agent.generate(
            description=build_brief_description(brief),
            max_iterations=args.max_iterations,
            quality_threshold=args.quality_threshold,
            output_format=args.output_format,
            topic=args.topic,
            enable_enhancement=args.enable_enhancement,
        )
        text_result = result_to_dict(result)
    except Exception as exc:  # noqa: BLE001 - preserve partial draft failures in the manifest.
        text_result = error_result(exc)
    manifest["drafts"].append({"mode": "text-to-figure", "result": text_result})

    try:
        result = agent.generate_from_paper(
            paper_path=str(paper_file),
            max_iterations=args.max_iterations,
            output_format=args.output_format,
            enable_enhancement=args.enable_enhancement,
        )
        paper_result = result_to_dict(result)
    except Exception as exc:  # noqa: BLE001 - preserve partial draft failures in the manifest.
        paper_result = error_result(exc)
    manifest["drafts"].append({"mode": "paper-to-figure", "result": paper_result})

    manifest_file = out_dir / "autofigure-drafts-manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    success_count = sum(1 for draft in manifest["drafts"] if draft.get("result", {}).get("success") is True)
    print(
        json.dumps(
            {
                "manifest": str(manifest_file),
                "draft_count": len(manifest["drafts"]),
                "draft_success_count": success_count,
            },
            indent=2,
        )
    )
    return 0 if success_count > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
