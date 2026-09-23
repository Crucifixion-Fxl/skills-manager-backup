"""CLI entry point for user-research-monitor — 通用版。

Usage:
  cd skills/user-research/user-research-monitor
  python -m scripts.main analyze --project-dir <path> [--date YYYY-MM-DD] [--run-project-analyzer]
  python -m scripts.main update --project-dir <path> [--run-project-analyzer]

工作流（5 phase）：
  Phase 1: ingest_forms  — 从 Google Forms/Typeform 拉取并归一化 raw responses
  Phase 2: enrich_schema — 给 discovered_schema.yaml 自动打 logical_qid（按 schema_overrides.yaml 规则）
  Phase 3: quality_engine — 跑 quality_rules.yaml 规则，生成 review_tab.csv + cleaned/
  Phase 4: project analyze.py（可选）— 跑 project_dir/scripts/analyze.py 做业务分析
  Phase 5: render_report  — 写 health_report.md（数据健康度报告）

跑完后用 user-research-report skill 接收 findings_data.json 写 findings.md 终稿。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts import ingest_forms, enrich_schema, quality_engine, render_report


def get_snapshot_root(project_dir: Path, date_str: str | None = None) -> Path:
    return ingest_forms.get_snapshot_root(project_dir, date_str)


def cmd_analyze(args):
    project_dir = Path(args.project_dir).resolve()
    if not (project_dir / "config" / "cohort_mapping.yaml").exists():
        print(f"✗ {project_dir}/config/cohort_mapping.yaml 不存在")
        print("  复制本 skill 的 templates/cohort_mapping.yaml.template 到项目 config 后填写")
        sys.exit(1)

    # 在发起任何网络采集前验证规则文件，避免拉完数据才发现配置不可执行。
    quality_engine.load_rules(project_dir)

    print("=" * 60)
    print(f"Project dir: {project_dir}")
    print("=" * 60)

    # Phase 1: Ingest
    print("\n" + "=" * 60)
    print("Phase 1/5: Ingest survey responses")
    print("=" * 60)
    ingest_forms.run(project_dir, args.date)

    # 找最新 snapshot 时间戳目录
    snapshot_root = get_snapshot_root(project_dir, args.date)
    raw_root = snapshot_root / "raw"
    if not raw_root.exists():
        print(f"✗ 没有 raw 目录：{raw_root}")
        return
    snapshots = sorted([d for d in raw_root.iterdir() if d.is_dir()])
    if not snapshots:
        print(f"✗ {raw_root} 下没有时间戳目录")
        return
    latest = snapshots[-1]

    # Phase 2: Enrich schema
    print("\n" + "=" * 60)
    print("Phase 2/5: Enrich schema (auto logical_qid 标注)")
    print("=" * 60)
    enrich_schema.enrich(project_dir)

    # Phase 3: Quality engine
    print("\n" + "=" * 60)
    print("Phase 3/5: Quality engine")
    print("=" * 60)
    quality_engine.run_for_snapshot(project_dir, latest, snapshot_root)

    # Phase 4: project-specific analyze.py（可选）
    print("\n" + "=" * 60)
    print("Phase 4/5: Project business analysis")
    print("=" * 60)
    project_analyze = project_dir / "scripts" / "analyze.py"
    if project_analyze.exists() and args.run_project_analyzer:
        print(f"→ 调 {project_analyze}")
        skill_dir = Path(__file__).resolve().parents[1]
        scripts_dir = skill_dir / "scripts"
        env = os.environ.copy()
        env["USER_RESEARCH_MONITOR_SKILL_DIR"] = str(skill_dir)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(scripts_dir) if not existing_pythonpath else f"{scripts_dir}{os.pathsep}{existing_pythonpath}"
        result = subprocess.run(
            [sys.executable, str(project_analyze),
             "--project-dir", str(project_dir),
             "--snapshot-root", str(snapshot_root)],
            capture_output=False,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"project analyze.py failed with exit code {result.returncode}; "
                "health data may still exist, but findings are not report-ready"
            )
    elif project_analyze.exists():
        print(f"  ℹ️  已发现 {project_analyze}，默认不执行项目代码")
        print("     检查脚本来源和内容后，显式传 --run-project-analyzer 才会运行")
    else:
        print(f"  ℹ️  {project_analyze} 不存在——跳过业务分析")
        print(f"     LLM 接到新调研时基于 templates/analyze.py.template 在 project 写定制版")

    # Phase 5: Render health report
    print("\n" + "=" * 60)
    print("Phase 5/5: Render health report")
    print("=" * 60)
    render_report.run(project_dir, snapshot_root)

    print("\n" + "=" * 60)
    print("✓ DONE.")
    print(f"输出目录：{snapshot_root}")
    print(f"  - health_report.md    （数据健康度）")
    print(f"  - review_tab.csv      （研究 owner 复核清单）")
    if (snapshot_root / "findings_data.json").exists():
        print(f"  - findings_data.json  （业务分析结果，由 project analyze.py 产出）")
    print(f"\n下一步：调 user-research-report skill 写 findings.md 终稿")
    print("=" * 60)


def cmd_update(args):
    """Refresh all configured sources; this is not an incremental diff."""
    print("ℹ️  update 会重新读取所有已配置回答；当前不提供增量 diff")
    cmd_analyze(args)


def main():
    parser = argparse.ArgumentParser(prog="user-research-monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ana = sub.add_parser("analyze", help="运行采集、映射、质量检查与报告；项目分析脚本默认不执行")
    p_ana.add_argument("--project-dir", required=True, type=Path,
                       help="Project root with config/cohort_mapping.yaml")
    p_ana.add_argument("--date", default=None, help="Snapshot date (YYYY-MM-DD)")
    p_ana.add_argument(
        "--run-project-analyzer",
        action="store_true",
        help="显式执行 <project-dir>/scripts/analyze.py；使用前应检查其来源和内容",
    )
    p_ana.set_defaults(func=cmd_analyze)

    p_upd = sub.add_parser("update", help="增量更新")
    p_upd.add_argument("--project-dir", required=True, type=Path)
    p_upd.add_argument("--date", default=None)
    p_upd.add_argument(
        "--run-project-analyzer",
        action="store_true",
        help="显式执行 <project-dir>/scripts/analyze.py；使用前应检查其来源和内容",
    )
    p_upd.set_defaults(func=cmd_update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
