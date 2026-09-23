#!/usr/bin/env python3
"""
extract.py — 从 Claude Code JSONL 对话记录中提取结构化任务数据

功能:
  - 日报模式 (--date YYYY-MM-DD): 提取指定日期数据
  - 周报模式 (--weekly): 提取本周一→今天的数据
  - 多任务切分: 时间间隔 >30min 或 Skill 调用
  - Token 去重 & 费用计算
  - 结论追溯: 从末尾向上找 >20 字符的 assistant text
"""

import argparse
import json
import os
import re
import glob
from datetime import datetime, timezone, timedelta, date
from collections import Counter

BJT = timezone(timedelta(hours=8))

TASK_GAP_MINUTES = 30

# 工作日边界：08:00 BJT → 次日 08:00 BJT
# "2026-04-03" 表示 2026-04-03 08:00 BJT → 2026-04-04 08:00 BJT
DAY_START_HOUR = 8

def load_pricing():
    """从 references/pricing.md 读取模型定价，避免硬编码重复"""
    pricing_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "pricing.md")
    pricing = {}
    try:
        with open(pricing_path) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("|") or line.startswith("| 模型") or line.startswith("|--"):
                    continue
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 5:
                    model = parts[0]
                    try:
                        pricing[model] = {
                            "input": float(parts[1]),
                            "output": float(parts[2]),
                            "cache_read": float(parts[3]),
                            "cache_creation": float(parts[4]),
                        }
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    # fallback if pricing.md is missing or empty
    if not pricing:
        pricing = {
            "claude-sonnet-4-6": {"input": 3, "output": 15, "cache_read": 0.3, "cache_creation": 3.75},
        }
    return pricing

MODEL_PRICING = load_pricing()

# 回退定价 — 未知模型按 sonnet 计费
DEFAULT_PRICING = MODEL_PRICING["claude-sonnet-4-6"]


def parse_ts(ts_str):
    """解析 ISO timestamp 为 BJT datetime"""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(BJT)
    except Exception:
        return None


def extract_text(msg_field):
    """从 message 字段提取纯文本"""
    if isinstance(msg_field, str):
        return msg_field
    if isinstance(msg_field, dict):
        content = msg_field.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
            return " ".join(texts)
    return str(msg_field)


def clean_first_msg(text):
    """清理首条消息中的 XML 标签和图片标记"""
    if "<command-name>" in text:
        m = re.search(r"<command-name>/?([\w:-]+)", text)
        if m:
            return f"/{m.group(1)}"
    text = re.sub(r"\[Image #\d+\]", "", text)
    return text.strip()[:200]


def extract_skill_from_user(raw):
    """从 user message 中提取 skill 调用"""
    if isinstance(raw, str) and "<command-name>" in raw:
        m = re.search(r"<command-name>/?([\w:-]+)", raw)
        if m:
            return m.group(1)
    if isinstance(raw, dict):
        content = raw.get("content", "")
        if isinstance(content, str) and "<command-name>" in content:
            m = re.search(r"<command-name>/?([\w:-]+)", content)
            if m:
                return m.group(1)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    t = b.get("text", "")
                    if "<command-name>" in t:
                        m = re.search(r"<command-name>/?([\w:-]+)", t)
                        if m:
                            return m.group(1)
    return None


def get_pricing(model_name):
    """根据模型名获取定价，支持模糊匹配"""
    if not model_name:
        return DEFAULT_PRICING
    name = model_name.lower()
    for key, pricing in MODEL_PRICING.items():
        if key in name:
            return pricing
    # 模糊匹配
    if "opus" in name:
        return MODEL_PRICING["claude-opus-4-6"]
    if "sonnet" in name:
        return MODEL_PRICING["claude-sonnet-4-6"]
    if "haiku" in name:
        return MODEL_PRICING["claude-haiku-4-5"]
    return DEFAULT_PRICING


def calc_cost(tokens, model_name):
    """计算费用，tokens 是 dict {input, output, cache_read, cache_creation}"""
    pricing = get_pricing(model_name)
    cost = 0.0
    cost += tokens.get("input", 0) / 1_000_000 * pricing["input"]
    cost += tokens.get("output", 0) / 1_000_000 * pricing["output"]
    cost += tokens.get("cache_read", 0) / 1_000_000 * pricing["cache_read"]
    cost += tokens.get("cache_creation", 0) / 1_000_000 * pricing["cache_creation"]
    return cost


def workday_of(dt_bjt):
    """计算 BJT datetime 所属的工作日（YYYY-MM-DD）

    工作日定义：08:00 BJT → 次日 08:00 BJT
    例：04-03 00:30 BJT → 属于 04-02 的工作日
        04-03 09:00 BJT → 属于 04-03 的工作日
        04-04 07:59 BJT → 属于 04-03 的工作日
    """
    if dt_bjt.hour < DAY_START_HOUR:
        return (dt_bjt - timedelta(days=1)).strftime("%Y-%m-%d")
    return dt_bjt.strftime("%Y-%m-%d")


def workday_range(date_str):
    """返回工作日的实际 UTC 时间范围 (start_dt, end_dt)

    "2026-04-03" → 2026-04-03 08:00 BJT ~ 2026-04-04 08:00 BJT
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT)
    start = d.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


RECAP_TAIL_SUFFIX = "(disable recaps in /config)"


def _strip_recap_tail(s):
    """剥掉尾部 '(disable recaps in /config)' 提示，幂等。

    替代原 regex 实现以消除 SonarQube python:S5852 (ReDoS) 告警。
    """
    if not s:
        return s
    s = s.strip()
    if s.endswith(RECAP_TAIL_SUFFIX):
        s = s[:-len(RECAP_TAIL_SUFFIX)].rstrip()
    return s


def _iter_jsonl(filepath):
    """逐行 yield 解析后的 dict，跳过坏行；with open 保证 handle 关闭。"""
    with open(filepath, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                yield json.loads(line)
            except Exception:
                continue


def _in_time_window(d, range_start, range_end):
    """日期过滤：基于工作日时间窗口。

    - 有 ts: 解析成功且落在窗口内 → True
    - 无 ts: 仅 system 类型保留（用于 turn_duration slug 等无 ts 的事件）
    """
    ts_str = d.get("timestamp", "")
    if ts_str:
        dt = parse_ts(ts_str)
        return bool(dt and range_start <= dt < range_end)
    return d.get("type", "") == "system"


def _handle_user(d, state):
    ts_str = d.get("timestamp", "")
    if not ts_str:
        return
    msg = d.get("message", "")
    state["messages"].append({
        "type": "user",
        "timestamp": ts_str,
        "text": extract_text(msg),
        "skill_invoke": extract_skill_from_user(msg),
    })


def _extract_usage(raw_usage):
    if not isinstance(raw_usage, dict):
        return {}
    return {
        "input": raw_usage.get("input_tokens", 0),
        "output": raw_usage.get("output_tokens", 0),
        "cache_read": raw_usage.get("cache_read_input_tokens", 0),
        "cache_creation": raw_usage.get("cache_creation_input_tokens", 0),
    }


def _block_to_tool_use(block):
    inp = block.get("input", {}) or {}
    tu = {"name": block.get("name", "")}
    if "command" in inp:
        tu["command"] = str(inp["command"])[:120]
    if "file_path" in inp:
        tu["file_path"] = inp["file_path"]
    if "skill" in inp:
        tu["skill"] = inp["skill"]
    return tu


def _parse_content_blocks(content):
    tool_uses, text_blocks = [], []
    if not isinstance(content, list):
        return tool_uses, text_blocks
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            tool_uses.append(_block_to_tool_use(block))
        elif btype == "text":
            txt = block.get("text", "").strip()
            if txt:
                text_blocks.append(txt)
    return tool_uses, text_blocks


def _merge_token_record(records, msg_id, usage, model):
    """去重: 同 msg_id 多条 usage 取每个 token 字段的 max（streaming chunks）。"""
    if msg_id not in records:
        records[msg_id] = {
            "input": usage.get("input", 0),
            "output": usage.get("output", 0),
            "cache_read": usage.get("cache_read", 0),
            "cache_creation": usage.get("cache_creation", 0),
            "model": model,
        }
        return
    rec = records[msg_id]
    for k in ("input", "output", "cache_read", "cache_creation"):
        rec[k] = max(rec[k], usage.get(k, 0))
    if model:
        rec["model"] = model


def _handle_assistant(d, state):
    ts_str = d.get("timestamp", "")
    if not ts_str:
        return
    msg = d.get("message", {})
    if not isinstance(msg, dict):
        msg = {}
    msg_id = msg.get("id")
    msg_model = msg.get("model")
    usage = _extract_usage(msg.get("usage"))
    tool_uses, text_blocks = _parse_content_blocks(msg.get("content", []))

    if msg_id and usage:
        _merge_token_record(state["token_records"], msg_id, usage, msg_model)

    state["messages"].append({
        "type": "assistant",
        "timestamp": ts_str,
        "text_blocks": text_blocks,
        "tool_uses": tool_uses,
        "msg_id": msg_id,
        "msg_model": msg_model,
    })


def _handle_system(d, state):
    subtype = d.get("subtype", "")
    if subtype == "turn_duration":
        slug = d.get("slug")
        if slug:
            state["slugs"].add(slug)
    elif subtype == "away_summary":
        ts_str = d.get("timestamp", "")
        if not ts_str:
            return
        content = _strip_recap_tail(d.get("content") or "")
        dt = parse_ts(ts_str)
        if content and dt:
            state["recaps"].append({"ts": dt, "ts_str": ts_str, "content": content})


_DISPATCH = {
    "user": _handle_user,
    "assistant": _handle_assistant,
    "system": _handle_system,
}


def parse_session_messages(filepath, date_start, date_end):
    """解析 session JSONL，返回日期范围内的消息流和元数据

    日期过滤基于工作日：08:00 BJT → 次日 08:00 BJT

    Args:
        filepath: JSONL 文件路径
        date_start: 开始工作日 (inclusive, YYYY-MM-DD)
        date_end: 结束工作日 (inclusive, YYYY-MM-DD)

    Returns:
        (messages, meta) - 过滤后的消息列表和元数据
    """
    state = {
        "messages": [],
        "slugs": set(),
        # token 去重: message_id -> {input, output, cache_read, cache_creation, model}
        "token_records": {},
        # recap (away_summary) 条目，供 extract_task_info 按时间窗绑定到 task
        "recaps": [],
    }
    range_start, _ = workday_range(date_start)
    _, range_end = workday_range(date_end)

    for d in _iter_jsonl(filepath):
        if not _in_time_window(d, range_start, range_end):
            continue
        handler = _DISPATCH.get(d.get("type", ""))
        if handler:
            handler(d, state)

    meta = {
        "slugs": state["slugs"],
        "token_records": state["token_records"],
        "recaps": state["recaps"],
    }
    return state["messages"], meta


def split_into_tasks(messages):
    """将消息流切分为多个任务"""
    tasks = []
    current_task = []
    last_user_ts = None

    for msg in messages:
        if msg["type"] == "user":
            ts = parse_ts(msg.get("timestamp", ""))
            should_split = False

            if last_user_ts and ts:
                gap = (ts - last_user_ts).total_seconds() / 60
                if gap >= TASK_GAP_MINUTES:
                    should_split = True

            if msg.get("skill_invoke") and current_task:
                has_user = any(m["type"] == "user" for m in current_task)
                if has_user:
                    should_split = True

            if should_split and current_task:
                tasks.append(current_task)
                current_task = []

            last_user_ts = ts

        current_task.append(msg)

    if current_task:
        tasks.append(current_task)

    return tasks


def _new_task_info(session_slugs):
    return {
        "first_user_msg": "",
        "first_ts": None,
        "last_ts": None,
        "user_count": 0,
        "assistant_count": 0,
        "tool_use_count": 0,
        "tool_uses": [],
        "skills": [],
        "slugs": list(session_slugs),
        "msg_ids": set(),
        "recaps": [],
    }


def _aggregate_user_msg(msg, info):
    info["user_count"] += 1
    if info["user_count"] == 1:
        info["first_user_msg"] = clean_first_msg(msg.get("text", ""))
    if msg.get("skill_invoke"):
        info["skills"].append(msg["skill_invoke"])


def _aggregate_assistant_msg(msg, info):
    info["assistant_count"] += 1
    if msg.get("msg_id"):
        info["msg_ids"].add(msg["msg_id"])
    for tu in msg.get("tool_uses", []):
        info["tool_uses"].append(tu)
        info["tool_use_count"] += 1
        if tu.get("skill"):
            info["skills"].append(tu["skill"])


def _aggregate_messages(messages, info):
    for msg in messages:
        ts = msg.get("timestamp")
        if ts:
            if not info["first_ts"]:
                info["first_ts"] = ts
            info["last_ts"] = ts
        if msg["type"] == "user":
            _aggregate_user_msg(msg, info)
        elif msg["type"] == "assistant":
            _aggregate_assistant_msg(msg, info)


def _bind_recaps(info, session_recaps):
    """按时间窗把 session 内 recap 绑定到当前 task。

    规则: recap.ts 落在 [first_ts, last_ts + 5min] 内即绑定
    (+5min 容忍: recap 通常在最后一条消息 ≥3min 停顿后触发, 会超出 last_ts)
    """
    if not (session_recaps and info["first_ts"] and info["last_ts"]):
        return
    t1 = parse_ts(info["first_ts"])
    t2 = parse_ts(info["last_ts"])
    if not (t1 and t2):
        return
    tolerance_end = t2 + timedelta(minutes=5)
    bound = [r for r in session_recaps if t1 <= r["ts"] <= tolerance_end]
    bound.sort(key=lambda r: r["ts"])
    info["recaps"] = bound


def _aggregate_tokens(info, session_token_records):
    """汇总 token（只计入属于此任务的 msg_ids）。"""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    model_name = None
    for mid in info["msg_ids"]:
        rec = session_token_records.get(mid)
        if not rec:
            continue
        tokens["input"] += rec["input"]
        tokens["output"] += rec["output"]
        tokens["cache_read"] += rec["cache_read"]
        tokens["cache_creation"] += rec["cache_creation"]
        if rec.get("model"):
            model_name = rec["model"]
    info["tokens"] = tokens
    info["model"] = model_name
    info["total_tokens"] = sum(tokens.values())
    info["cost"] = calc_cost(tokens, model_name)


def extract_task_info(task_messages, session_slugs, session_token_records, session_recaps=None):
    """从一组消息中提取任务信息"""
    info = _new_task_info(session_slugs)
    _aggregate_messages(task_messages, info)
    _bind_recaps(info, session_recaps)
    _aggregate_tokens(info, session_token_records)
    return info


def get_date_range(args):
    """根据参数确定日期范围"""
    today = date.today()

    if args.weekly:
        # 本周一 → 今天
        monday = today - timedelta(days=today.weekday())
        return monday.isoformat(), today.isoformat(), "weekly"
    else:
        d = args.date if args.date else today.isoformat()
        return d, d, "daily"


def discover_jsonl_files(date_start):
    """扫描所有 project 目录的 JSONL 文件"""
    base = os.path.expanduser("~/.claude/projects/")
    if not os.path.isdir(base):
        return []

    files = []
    for project_dir in glob.glob(base + "*/"):
        for filepath in glob.glob(project_dir + "*.jsonl"):
            # Trade-off: mtime 过滤可加速扫描（跳过明显不在日期范围内的文件），
            # 但如果文件在目标日期活跃后未被修改过（如硬盘恢复），会被误跳过。
            # 实际使用中 JSONL 文件的 mtime 总是≥最后一条记录时间，此风险极低。
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=BJT)
                if mtime.strftime("%Y-%m-%d") < date_start:
                    continue
            except OSError:
                continue
            files.append(filepath)

    return files


def is_noise(task):
    """判断是否为噪声任务（自动化运行、空任务、无 recap 等）"""
    user_turns = task.get("user_count", 0)
    tool_turns = task.get("tool_use_count", 0)
    cost = task.get("cost", 0)
    duration = 0
    if task.get("first_ts") and task.get("last_ts"):
        t1 = parse_ts(task["first_ts"])
        t2 = parse_ts(task["last_ts"])
        duration = (t2 - t1).total_seconds() / 60 if t1 and t2 else 0
    first_msg = task.get("first_user_msg", "") or ""

    # 完全空任务
    output_tokens = task.get("tokens", {}).get("output", 0)
    if duration == 0 and cost == 0 and output_tokens == 0:
        return True
    # sre-agent 自动化运行：高轮次 + 系统首条消息
    if user_turns > 100 and (first_msg.startswith("<local-command") or first_msg.startswith("<system")):
        return True
    # 内置 slash 命令片段（无实质工具调用）
    if re.match(r"^/(extra-usage|model|help|clear|status)(\s|$)", first_msg) and tool_turns == 0:
        return True
    # 无 recap 即丢弃（切 recap 数据源后, 无 away_summary 的任务视作短任务/非 toil）
    if not task.get("recaps"):
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="从 Claude Code JSONL 提取结构化任务数据")
    parser.add_argument("--date", type=str, default=None, help="日报模式: YYYY-MM-DD (默认今天)")
    parser.add_argument("--weekly", action="store_true", help="周报模式: 本周一→今天")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式（默认人类可读文本）")
    args = parser.parse_args()

    date_start, date_end, mode = get_date_range(args)

    files = discover_jsonl_files(date_start)

    all_tasks = []
    session_count = 0

    for filepath in files:
        messages, meta = parse_session_messages(filepath, date_start, date_end)
        if not messages:
            continue

        session_count += 1
        sid = os.path.basename(filepath).replace(".jsonl", "")[:8]

        tasks = split_into_tasks(messages)
        for task_idx, task_msgs in enumerate(tasks):
            info = extract_task_info(task_msgs, meta["slugs"], meta["token_records"], meta.get("recaps"))
            if info["user_count"] == 0:
                continue
            info["session_id"] = sid
            info["task_idx"] = task_idx
            info["task_count_in_session"] = len(tasks)

            # 计算工作日归属（08:00 BJT → 次日 08:00 BJT）
            if info["first_ts"]:
                dt = parse_ts(info["first_ts"])
                info["date"] = workday_of(dt) if dt else date_start
            else:
                info["date"] = date_start

            all_tasks.append(info)

    all_tasks.sort(key=lambda t: t.get("first_ts", ""))
    all_tasks = [t for t in all_tasks if not is_noise(t)]

    # --- 输出 ---
    if args.json:
        _output_json(all_tasks, mode, date_start, date_end, session_count)
    else:
        _output_text(all_tasks, mode, date_start, date_end, session_count)


def _group_tasks_by_date(all_tasks):
    out = {}
    for task in all_tasks:
        out.setdefault(task["date"], []).append(task)
    return out


def _print_task_text(idx, task):
    t1 = parse_ts(task["first_ts"]) if task["first_ts"] else None
    t2 = parse_ts(task["last_ts"]) if task["last_ts"] else None
    wall_min = (t2 - t1).total_seconds() / 60 if t1 and t2 else 0
    time_start = t1.strftime("%H:%M") if t1 else "?"
    time_end = t2.strftime("%H:%M") if t2 else "?"

    tools = Counter(tu["name"] for tu in task["tool_uses"])
    tool_str = ", ".join(f"{n}x{c}" for n, c in tools.most_common(8)) if tools else "-"
    skills_str = ", ".join(sorted(set(task["skills"]))) if task["skills"] else "-"
    slugs_str = ", ".join(sorted(task["slugs"])) if task["slugs"] else "-"

    recaps = task.get("recaps", [])
    first_recap = recaps[0]["content"] if recaps else "(无)"
    last_recap = recaps[-1]["content"] if len(recaps) > 1 else ""

    tokens = task["tokens"]
    print(f"任务 #{idx}:")
    print(f"  首条消息: {task['first_user_msg']}")
    print(f"  Slug: {slugs_str}")
    print(f"  Skills: {skills_str}")
    print(f"  工具调用: {tool_str}")
    print(f"  初始进展: {first_recap[:200]}")
    if last_recap:
        print(f"  最终进展: {last_recap[:200]}")
    print(f"  耗时: {wall_min:.0f}min")
    print(f"  Token: {task['total_tokens']} (input:{tokens['input']} output:{tokens['output']} cache_read:{tokens['cache_read']} cache_create:{tokens['cache_creation']})")
    print(f"  费用: ${task['cost']:.4f}")
    print(f"  时间: {time_start} -> {time_end}")
    print(f"  日期: {task['date']}")
    print(f"  轮次: {task['user_count']}u/{task['assistant_count']}a/{task['tool_use_count']}t")
    print(f"  Session: {task['session_id']}")
    print()


def _print_text_summary(all_tasks):
    total_cost = sum(t["cost"] for t in all_tasks)
    total_input = sum(t["tokens"]["input"] for t in all_tasks)
    total_output = sum(t["tokens"]["output"] for t in all_tasks)
    total_cache_read = sum(t["tokens"]["cache_read"] for t in all_tasks)
    total_cache_creation = sum(t["tokens"]["cache_creation"] for t in all_tasks)
    total_input_all = total_input + total_cache_read + total_cache_creation
    cache_hit_rate = (total_cache_read / total_input_all * 100) if total_input_all > 0 else 0.0

    print("=== SUMMARY ===")
    print(f"TOTAL_TASKS: {len(all_tasks)}")
    print(f"TOTAL_COST: ${total_cost:.4f}")
    print(f"TOTAL_INPUT_TOKENS: {total_input}")
    print(f"TOTAL_OUTPUT_TOKENS: {total_output}")
    print(f"TOTAL_CACHE_READ: {total_cache_read}")
    print(f"TOTAL_CACHE_CREATION: {total_cache_creation}")
    print(f"CACHE_HIT_RATE: {cache_hit_rate:.1f}%")


def _output_text(all_tasks, mode, date_start, date_end, session_count):
    print(f"MODE: {mode}")
    print(f"DATE_RANGE: {date_start} ~ {date_end}")
    print(f"TASK_COUNT: {len(all_tasks)}")
    print(f"SESSION_COUNT: {session_count}")
    print()

    tasks_by_date = _group_tasks_by_date(all_tasks)
    global_idx = 0
    for day in sorted(tasks_by_date.keys()):
        day_tasks = tasks_by_date[day]
        if mode == "weekly":
            print(f"=== {day} ({len(day_tasks)} 个任务) ===")
            print()
        for task in day_tasks:
            global_idx += 1
            _print_task_text(global_idx, task)

    _print_text_summary(all_tasks)


def _task_to_json_dict(idx, task, date_start):
    t1 = parse_ts(task["first_ts"]) if task.get("first_ts") else None
    t2 = parse_ts(task["last_ts"]) if task.get("last_ts") else None
    wall_min = int((t2 - t1).total_seconds() / 60) if t1 and t2 else 0

    tools_counter = Counter(tu["name"] for tu in task.get("tool_uses", []))
    tools_str = ", ".join(f"{n}×{c}" for n, c in tools_counter.most_common(8)) if tools_counter else ""

    slugs = list(task.get("slugs", []))
    recaps = task.get("recaps", [])
    first_recap = recaps[0]["content"] if recaps else ""
    last_recap = recaps[-1]["content"] if recaps else ""

    return {
        "id": idx,
        "date": task.get("date", date_start),
        "start_time": t1.strftime("%H:%M") if t1 else "",
        "end_time": t2.strftime("%H:%M") if t2 else "",
        "duration_min": wall_min,
        # first_msg / conclusion 改从 recap 填充, 保持 render 脚本字段兼容
        "first_msg": first_recap[:200],
        "conclusion": last_recap[:200],
        "recaps": [{"ts": r["ts_str"][:19], "content": r["content"]} for r in recaps],
        "slug": slugs[0] if slugs else "",
        "skills": list(set(task.get("skills", []))),
        "tools": tools_str,
        "turns": {
            "user": task.get("user_count", 0),
            "assistant": task.get("assistant_count", 0),
            "tool": task.get("tool_use_count", 0),
        },
        "model": task.get("model") or "claude-sonnet-4-6",
        "tokens": task["tokens"],
        "cost": round(task.get("cost", 0), 4),
        "session_id": task.get("session_id", ""),
    }


def _compute_json_summary(all_tasks):
    total_cost = sum(t.get("cost", 0) for t in all_tasks)
    total_input = sum(t["tokens"]["input"] for t in all_tasks)
    total_output = sum(t["tokens"]["output"] for t in all_tasks)
    total_cache_read = sum(t["tokens"]["cache_read"] for t in all_tasks)
    total_cache_creation = sum(t["tokens"]["cache_creation"] for t in all_tasks)
    total_all = total_input + total_cache_read + total_cache_creation
    cache_hit = total_cache_read / total_all if total_all > 0 else 0.0
    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read": total_cache_read,
        "total_cache_creation": total_cache_creation,
        "total_cost": round(total_cost, 4),
        "cache_hit_rate": round(cache_hit, 4),
    }


def _output_json(all_tasks, mode, date_start, date_end, session_count):
    """输出结构化 JSON，供 render 脚本消费"""
    task_list = [_task_to_json_dict(idx, t, date_start) for idx, t in enumerate(all_tasks, 1)]
    output = {
        "mode": mode,
        "date_range": f"{date_start} ~ {date_end}",
        "task_count": len(task_list),
        "session_count": session_count,
        "tasks": task_list,
        "summary": _compute_json_summary(all_tasks),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
