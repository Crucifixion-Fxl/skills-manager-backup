"""user-research-monitor 通用工具库——供 project 端 analyze.py 直接 import。

设计原则：
- 所有路径相关函数接收 project_dir 作参数，不依赖 skill 内目录
- 所有业务参数（题号 / cohort 名 / feature 列表 / 阈值）通过函数参数注入，不硬编码
- 函数应该对任何经过采集适配器归一化的 monitor response 都能跑

模块组织：
  Section 1 — Normalized response helpers  提取 textAnswers / choices / scale 值
  Section 2 — Loading helpers               读取 project 目录下的配置 + 数据
  Section 3 — 通用统计函数                  分布 / 量表汇总 / 多选频次 / 开放题导出
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml


# ============================================================
# Section 1 — Normalized response helpers
# ============================================================

def answer_text(answer: dict | None) -> str:
    """提取 monitor 归一化 answer 对象里的文本（支持多选拼接）。"""
    if not answer:
        return ""
    items = answer.get("textAnswers", {}).get("answers", [])
    return "\n".join(a.get("value", "") for a in items if a.get("value") is not None).strip()


def answer_choices(answer: dict | None) -> list[str]:
    """提取选项题（单选 / 多选）的所有选中项。"""
    if not answer:
        return []
    return [
        a.get("value", "")
        for a in answer.get("textAnswers", {}).get("answers", [])
        if a.get("value")
    ]


def scale_value(answer: dict | None) -> int | None:
    """提取量表题（0-10 / 1-5 等）的整数值，无效返回 None。"""
    txt = answer_text(answer)
    if not txt:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def get_answer(response: dict, qid: str) -> dict:
    """从 response 取某 questionId 的 answer 对象（不存在返回空 dict）。"""
    if not qid:
        return {}
    return response.get("answers", {}).get(qid, {})


# ============================================================
# Section 2 — Loading helpers（按 project_dir 读配置 + 数据）
# ============================================================

def load_cohort_mapping(project_dir: Path) -> dict:
    """读取 project/config/cohort_mapping.yaml。

    期望结构：
        research_batch: <批次代号>
        output_root: <相对 project root 的输出目录>
        cohorts:
          - cohort_key: <internal>
            business_label: <PM 友好标签>
            source: {type: google_form, form_id: <id>}
            ...
    """
    p = Path(project_dir) / "config" / "cohort_mapping.yaml"
    if not p.exists():
        raise FileNotFoundError(f"cohort_mapping.yaml not found at {p}")
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load_qmap(project_dir: Path) -> dict[str, dict[str, str]]:
    """读取 logical_qid → provider question ID 映射，按 cohort 分组。

    期望 project/config/discovered_schema.yaml 结构：
        cohorts:
          <cohort_key>:
            questions:
              <google_qid>:
                logical_qid: <项目定义的稳定逻辑 ID>
                title: <题面>
                ...
    """
    p = Path(project_dir) / "config" / "discovered_schema.yaml"
    if not p.exists():
        return {}
    with open(p) as f:
        schema = yaml.safe_load(f) or {}
    qmap: dict[str, dict[str, str]] = defaultdict(dict)
    for ck, sec in (schema.get("cohorts") or {}).items():
        for gqid, q in ((sec or {}).get("questions") or {}).items():
            lq = (q or {}).get("logical_qid")
            if lq:
                qmap[ck][lq] = gqid
    return dict(qmap)


def load_question_titles(project_dir: Path) -> dict[str, dict[str, str]]:
    """读取 logical_qid → 题面文本映射，按 cohort 分组（用于报告内的"原始 wording"展示）。"""
    p = Path(project_dir) / "config" / "discovered_schema.yaml"
    if not p.exists():
        return {}
    with open(p) as f:
        schema = yaml.safe_load(f) or {}
    titles: dict[str, dict[str, str]] = defaultdict(dict)
    for ck, sec in (schema.get("cohorts") or {}).items():
        for gqid, q in ((sec or {}).get("questions") or {}).items():
            lq = (q or {}).get("logical_qid")
            if lq:
                titles[ck][lq] = (q or {}).get("title", "")
    return dict(titles)


def load_responses(
    snapshot_root: Path,
    lens: str = "raw",
    excluded_response_ids: Iterable[str] | None = None,
) -> dict:
    """读取一次快照下所有 cohort 的 response。

    Args:
        snapshot_root: 包含 raw/ 和 cleaned/{conservative,strict}/ 的快照根目录
        lens: "raw" / "conservative" / "strict"——分别读不同清洗口径的数据
        excluded_response_ids: 额外剔除的 response ID（如 PM 审核剔除）

    Returns:
        {
            "all": [(cohort_key, response_dict), ...],
            "by_cohort": {cohort_key: [response, ...]},
            "meta": {
                "lens": str,
                "raw_total": int,
                "excluded": int,
                "valid_total": int,
                "cohort_counts": {cohort_key: int},
            }
        }
    """
    excluded = set(excluded_response_ids or [])
    snapshot_root = Path(snapshot_root)

    # raw 取最新时间戳目录
    if lens == "raw":
        raw_root = snapshot_root / "raw"
        if not raw_root.exists():
            raise FileNotFoundError(f"raw/ not found at {raw_root}")
        snaps = sorted(d for d in raw_root.iterdir() if d.is_dir())
        if not snaps:
            raise FileNotFoundError(f"no timestamped subdir under {raw_root}")
        data_dir = snaps[-1]
    else:
        data_dir = snapshot_root / "cleaned" / lens
        if not data_dir.exists():
            raise FileNotFoundError(f"cleaned/{lens}/ not found at {data_dir}")

    all_pairs: list[tuple[str, dict]] = []
    by_cohort: dict[str, list[dict]] = {}
    cohort_counts: dict[str, int] = {}
    raw_total = 0
    excluded_count = 0

    for f in sorted(data_dir.glob("*.json")):
        ck = f.stem
        with open(f) as fh:
            data = json.load(fh)
        responses = data.get("responses", [])
        raw_total += len(responses)
        kept = [r for r in responses if r.get("responseId") not in excluded]
        excluded_count += len(responses) - len(kept)
        by_cohort[ck] = kept
        cohort_counts[ck] = len(kept)
        for r in kept:
            all_pairs.append((ck, r))

    return {
        "all": all_pairs,
        "by_cohort": by_cohort,
        "meta": {
            "lens": lens,
            "raw_total": raw_total,
            "excluded": excluded_count,
            "valid_total": len(all_pairs),
            "cohort_counts": cohort_counts,
        },
    }


def load_excluded_response_ids(project_dir: Path) -> set[str]:
    """读取 project/config/excluded_responses.yaml 里 PM 审核剔除的 response ID。"""
    p = Path(project_dir) / "config" / "excluded_responses.yaml"
    if not p.exists():
        return set()
    with open(p) as f:
        cfg = yaml.safe_load(f) or {}
    return {item["response_id"] for item in (cfg.get("excluded") or []) if item.get("response_id")}


# ============================================================
# Section 3 — 通用统计函数
# ============================================================

def distribution_for_choice(
    all_pairs: list[tuple[str, dict]],
    qmap: dict[str, dict[str, str]],
    logical_qid: str,
    multi_select: bool = False,
    cohort_filter: set[str] | None = None,
) -> dict:
    """对单选 / 多选题做整体分布统计。

    Args:
        all_pairs: [(cohort_key, response), ...]
        qmap: {cohort_key: {logical_qid: google_qid}}
        logical_qid: 要分析的项目稳定题目标识（如 "USAGE_FREQUENCY"）
        multi_select: 是否为多选题（True 时 total_votes 可能 > n_answered）
        cohort_filter: 仅分析这些 cohort（None = 全部）

    Returns:
        {
            "n_answered": int,
            "n_skipped": int,
            "option_counts": {option: count, ...},
            "option_pct": {option: percent_0_to_100, ...},  # 占作答人数比例
            "total_votes": int,  # 多选时 = 全部选项 vote 数，单选时 = n_answered
            "multi_select": bool,
        }
    """
    counts: Counter[str] = Counter()
    n_answered = 0
    total_votes = 0
    n_question_present = 0

    for ck, r in all_pairs:
        if cohort_filter is not None and ck not in cohort_filter:
            continue
        gqid = qmap.get(ck, {}).get(logical_qid)
        if not gqid:
            continue
        n_question_present += 1
        choices = answer_choices(get_answer(r, gqid))
        if not choices:
            continue
        n_answered += 1
        for c in choices:
            counts[c] += 1
            total_votes += 1

    return {
        "n_answered": n_answered,
        "n_skipped": n_question_present - n_answered,
        "option_counts": dict(counts.most_common()),
        "option_pct": {
            opt: round(c / n_answered * 100, 1) if n_answered else 0
            for opt, c in counts.items()
        },
        "total_votes": total_votes,
        "multi_select": multi_select,
    }


def scale_summary(
    all_pairs: list[tuple[str, dict]],
    qmap: dict[str, dict[str, str]],
    logical_qid: str,
    cohort_filter: set[str] | None = None,
    high_threshold: int = 8,
    low_threshold: int = 3,
) -> dict:
    """对量表题（0-10 / 1-5）做汇总统计。

    Args:
        all_pairs: [(cohort_key, response), ...]
        qmap: cohort → logical_qid → google_qid
        logical_qid: 量表题的 logical 编号
        cohort_filter: 仅分析这些 cohort
        high_threshold: 高分阈值（含），如 0-10 量表里默认 ≥8 算高分
        low_threshold: 低分阈值（含），如 0-10 量表里默认 ≤3 算低分

    Returns:
        {
            "n": int,
            "available": bool,
            "mean": float | None,
            "median": float | None,
            "ge_high_count": int,   # 高分人数
            "ge_high_pct": float,   # 高分占比 (0-100)
            "le_low_count": int,
            "le_low_pct": float,
            "distribution": {score: count, ...},
            "high_threshold": int,  # 阈值回写，方便报告用
            "low_threshold": int,
        }
    """
    scores: list[int] = []
    for ck, r in all_pairs:
        if cohort_filter is not None and ck not in cohort_filter:
            continue
        gqid = qmap.get(ck, {}).get(logical_qid)
        if not gqid:
            continue
        v = scale_value(get_answer(r, gqid))
        if v is not None:
            scores.append(v)

    if not scores:
        return {"n": 0, "available": False}

    n = len(scores)
    ge_h = sum(1 for s in scores if s >= high_threshold)
    le_l = sum(1 for s in scores if s <= low_threshold)
    return {
        "n": n,
        "available": True,
        "mean": round(statistics.mean(scores), 2),
        "median": statistics.median(scores),
        "ge_high_count": ge_h,
        "ge_high_pct": round(ge_h / n * 100, 1),
        "le_low_count": le_l,
        "le_low_pct": round(le_l / n * 100, 1),
        "distribution": dict(Counter(scores)),
        "high_threshold": high_threshold,
        "low_threshold": low_threshold,
    }


def multi_select_frequency(
    all_pairs: list[tuple[str, dict]],
    qmap: dict[str, dict[str, str]],
    logical_qid: str,
    cohort_filter: set[str] | None = None,
) -> dict:
    """多选题选项频次（按选项被选次数排序）。

    跟 distribution_for_choice(multi_select=True) 的区别：
    - 此函数返回更紧凑的频次字典（不带 pct）
    - 适合"Top N 票数"这种需要频次而非占比的场景
    """
    counter: Counter[str] = Counter()
    n_answered = 0
    for ck, r in all_pairs:
        if cohort_filter is not None and ck not in cohort_filter:
            continue
        gqid = qmap.get(ck, {}).get(logical_qid)
        if not gqid:
            continue
        choices = answer_choices(get_answer(r, gqid))
        if choices:
            n_answered += 1
            for c in choices:
                counter[c] += 1
    return {
        "n_answered": n_answered,
        "vote_pool": sum(counter.values()),
        "frequency": dict(counter.most_common()),
    }


def open_text_dump(
    all_pairs: list[tuple[str, dict]],
    qmap: dict[str, dict[str, str]],
    logical_qid: str,
    cohort_filter: set[str] | None = None,
    min_length: int = 1,
) -> list[dict]:
    """导出开放题原文（去重 + 过滤过短文本），供主题编码用。

    Returns:
        [
            {"cohort_key": str, "response_id": str, "response_id_tail": str, "text": str},
            ...
        ]
    """
    out = []
    for ck, r in all_pairs:
        if cohort_filter is not None and ck not in cohort_filter:
            continue
        gqid = qmap.get(ck, {}).get(logical_qid)
        if not gqid:
            continue
        text = answer_text(get_answer(r, gqid))
        if len(text) < min_length:
            continue
        rid = r.get("responseId", "")
        out.append({
            "cohort_key": ck,
            "response_id": rid,
            "response_id_tail": rid[-8:] if rid else "",
            "text": text,
        })
    return out
