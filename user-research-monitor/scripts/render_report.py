"""Assemble health_report.md from upstream artifacts—通用版。

仅产出数据健康度报告（health_report.md）—— 业务无关，跟 quality_engine 输出绑定。
findings.md 不再由本 skill 生成，由 user-research-report skill 接收 findings_data.json
+ raw responses + research brief 后写终稿。

Inputs:
  - project_dir/config/cohort_mapping.yaml
  - snapshot_root/raw/<TIMESTAMP>/*.json
  - snapshot_root/cleaned/{conservative,strict}/*.json
  - snapshot_root/review_tab.csv

Output:
  - snapshot_root/health_report.md
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml


def load_cohort_mapping(project_dir: Path) -> dict:
    p = Path(project_dir) / "config" / "cohort_mapping.yaml"
    if not p.exists():
        raise FileNotFoundError(f"cohort_mapping.yaml not found at {p}")
    with open(p) as f:
        return yaml.safe_load(f)


def load_review_tab(snapshot_root: Path) -> list[dict]:
    p = snapshot_root / "review_tab_detail.csv"
    # 优先用 detail 版，因含 cohort_key + action 字段供本脚本聚合
    if not p.exists():
        p = snapshot_root / "review_tab.csv"
    if not p.exists():
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


def load_quality_manifest(snapshot_root: Path) -> dict:
    path = snapshot_root / "quality_run_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"quality_run_manifest.json not found at {path}; quality checks are incomplete"
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def count_responses_by_cohort(snapshot_root: Path) -> dict[str, dict[str, int]]:
    """{cohort_key: {raw, conservative, strict}}"""
    out: dict[str, dict[str, int]] = {}
    raw_root = snapshot_root / "raw"
    if raw_root.exists():
        snaps = sorted([d for d in raw_root.iterdir() if d.is_dir()])
        latest = snaps[-1] if snaps else None
        if latest:
            for f in latest.glob("*.json"):
                with open(f) as fh:
                    d = json.load(fh)
                out.setdefault(f.stem, {})["raw"] = len(d.get("responses", []))
    for lens in ("conservative", "strict"):
        d = snapshot_root / "cleaned" / lens
        if d.exists():
            for f in d.glob("*.json"):
                with open(f) as fh:
                    data = json.load(fh)
                out.setdefault(f.stem, {})[lens] = len(data.get("responses", []))
    return out


def render_health_report(project_dir: Path, snapshot_root: Path) -> str:
    cohort_mapping = {c["cohort_key"]: c for c in load_cohort_mapping(project_dir)["cohorts"]}
    counts = count_responses_by_cohort(snapshot_root)
    review_rows = load_review_tab(snapshot_root)
    quality_manifest = load_quality_manifest(snapshot_root)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# 数据健康度报告\n\n> Snapshot: {ts}\n"]

    # 1.1 样本概览
    lines.append("\n## 1.1 样本概览\n")
    lines.append("| Cohort | 业务标签 | 收集样本（原始） | 完成样本（保守） | 完成样本（严格） | 完成率* |")
    lines.append("|---|---|---|---|---|---|")
    for ck, info in cohort_mapping.items():
        c = counts.get(ck, {})
        raw = c.get("raw", 0)
        cons = c.get("conservative", 0)
        strict = c.get("strict", 0)
        rate = f"{cons / raw * 100:.1f}%" if raw else "—"
        lines.append(f"| {ck} | {info['business_label']} | {raw} | {cons} | {strict} | {rate} |")
    lines.append("\n> *完成率 = 保守口径完成 / 收集样本。受邀但未提交的样本不在此分母。\n")

    lines.append(
        "\n> 完成时间规则只有在项目显式启用且时间字段可用时才执行；是否跳过见 quality_run_manifest.json。\n"
    )
    unavailable = []
    for cohort in quality_manifest.get("cohorts") or []:
        for rule_id in cohort.get("unevaluable_rules") or []:
            unavailable.append(f"{cohort.get('cohort_key')}: {rule_id}")
        for rule_id in cohort.get("skipped_rules") or []:
            unavailable.append(f"{cohort.get('cohort_key')}: {rule_id}（项目明确允许跳过）")
    if unavailable:
        lines.append("\n> ⚠️ 以下已配置规则没有实际评估证据："
                     + "；".join(unavailable) + "。不得把它们描述为已通过。\n")

    # 1.2 双口径对照 + 剔除原因 top3
    lines.append("\n## 1.2 双口径对照与剔除原因\n")
    if review_rows and "cohort_key" in (review_rows[0] if review_rows else {}):
        drop_rows = [r for r in review_rows if r.get("action") in ("drop_both", "drop_strict")]
        dropped_by_cohort_rule: dict[str, Counter] = defaultdict(Counter)
        for r in drop_rows:
            dropped_by_cohort_rule[r["cohort_key"]][r["rule"]] += 1
        lines.append("| Cohort | 保守剔除 N | 严格剔除 N | 剔除原因 Top 3（rule × 命中数）|")
        lines.append("|---|---|---|---|")
        for ck, info in cohort_mapping.items():
            c = counts.get(ck, {})
            raw = c.get("raw", 0)
            cons = c.get("conservative", 0)
            strict = c.get("strict", 0)
            top3 = ", ".join(f"{r}×{n}" for r, n in dropped_by_cohort_rule[ck].most_common(3)) or "—"
            lines.append(
                f"| {info['business_label']} | {max(raw - cons, 0)} | {max(raw - strict, 0)} | {top3} |"
            )

    # 1.3 人工复核清单
    lines.append("\n## 1.3 人工复核清单（标记但未自动剔除）\n")
    flag_only = [r for r in review_rows if r.get("action") == "flag_only"]
    if not flag_only:
        lines.append("无 S2 标记样本。")
    else:
        by_rule = Counter(r["rule"] for r in flag_only)
        lines.append(f"共 {len(flag_only)} 条标记样本，按规则分布：\n")
        for rule, n in by_rule.most_common():
            lines.append(f"- **{rule}**: {n} 条")
        lines.append(
            "\n明细见 `review_tab.csv`，研究 owner 在人工决定列填 `keep` / `drop` 后保存即可。"
        )

    return "\n".join(lines)


def run(project_dir: Path, snapshot_root: Path):
    health_md = render_health_report(project_dir, snapshot_root)
    out_path = snapshot_root / "health_report.md"
    out_path.write_text(health_md)
    print(f"✓ Wrote {out_path}")
    print(f"  ℹ️  findings.md 不再由 monitor 生成。请用 user-research-report skill")
    print(f"      接收 {snapshot_root}/findings_data.json + raw responses 写终稿")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--snapshot-root", required=True, type=Path)
    args = parser.parse_args()
    run(args.project_dir, args.snapshot_root)
