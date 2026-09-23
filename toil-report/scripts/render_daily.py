#!/usr/bin/env python3
"""
render_daily.py — Toil 日报 HTML 渲染脚本 (v2)

用法：
    python3 render_daily.py <raw_json_path> <enriched_json_path>

输出：
    HTML 字符串到 stdout

说明：
    接受 extract.py --json 输出 和 Claude Agent 归纳的 enriched JSON，
    纯渲染，不做任何语义判断。
"""

import json
import sys
import math
from collections import defaultdict


import os as _os
_CATEGORIES_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "categories.json")
with open(_CATEGORIES_PATH) as _f:
    CATEGORIES = json.load(_f)

DEFAULT_CATEGORY_COLOR = "#8b949e"


def get_cat_color(cat):
    return CATEGORIES.get(cat, DEFAULT_CATEGORY_COLOR)


def time_to_minutes(t):
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def minutes_to_hhmm(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


def format_tokens(tok_dict):
    cache_write = tok_dict.get("cache_creation", tok_dict.get("cache_create", 0))
    total = (tok_dict.get("input", 0) + tok_dict.get("output", 0)
             + tok_dict.get("cache_read", 0) + cache_write)
    if total >= 1_000_000:
        return f"{total/1_000_000:.1f}M"
    elif total >= 1_000:
        return f"{total/1_000:.0f}K"
    return str(total)


def safe_js(data):
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def escape_html(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def truncate(s, n):
    s = str(s)
    return s[:n] + "…" if len(s) > n else s


def merge_tasks(raw_tasks, enriched_list):
    enriched_map = {e["id"]: e for e in enriched_list}
    merged = []
    for t in raw_tasks:
        tid = t["id"]
        enr = enriched_map.get(tid, {})
        merged.append({
            **t,
            "summary": enr.get("summary") or truncate(t.get("first_msg", ""), 40),
            "category": enr.get("category") or "未分类",
            "trigger": enr.get("trigger", ""),
            "automation": enr.get("automation", ""),
            "repeat_key": enr.get("repeat_key"),
        })
    return merged


def swimlane_assign(tasks):
    sorted_tasks = sorted(tasks, key=lambda t: t.get("start_time", "00:00"))
    lanes = []
    result = []
    for t in sorted_tasks:
        start = t.get("start_time") or "00:00"
        raw_end = t.get("end_time") or ""
        if raw_end and raw_end > start:
            end = raw_end
        else:
            dur = t.get("duration_min", 1) or 1
            sh, sm = map(int, start.split(":"))
            total_m = sh * 60 + sm + dur
            end = f"{total_m // 60 % 24:02d}:{total_m % 60:02d}"
        placed = False
        for i, lane_end in enumerate(lanes):
            if lane_end <= start:
                lanes[i] = end
                result.append({**t, "lane": i})
                placed = True
                break
        if not placed:
            lanes.append(end)
            result.append({**t, "lane": len(lanes) - 1})
    return result, len(lanes)


def peak_parallelism(tasks):
    events = []
    for t in tasks:
        s = time_to_minutes(t.get("start_time", "00:00"))
        e = time_to_minutes(t.get("end_time", "00:00"))
        e = max(e, s + 1)
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def format_turns(turns_dict):
    u = turns_dict.get("user", 0)
    t = turns_dict.get("tool", 0)
    return f"{u}u/{t}t"


def build_html(raw_data, tasks, date_str, analysis=None):
    # === Metrics ===
    total_tasks = len(tasks)
    session_count = raw_data.get("session_count", 0)
    summary_data = raw_data.get("summary", {})
    total_cost = summary_data.get("total_cost", sum(t.get("cost", 0) for t in tasks))
    total_duration = sum(t.get("duration_min", 0) for t in tasks)
    dur_h, dur_m = divmod(total_duration, 60)
    dur_str = f"{dur_h}h{dur_m}m" if dur_h else f"{dur_m}m"
    peak = peak_parallelism(tasks)
    avg_cost = total_cost / total_tasks if total_tasks else 0

    starts = sorted(t.get("start_time") for t in tasks if t.get("start_time"))
    ends = sorted(t.get("end_time") for t in tasks if t.get("end_time"))
    time_start = starts[0] if starts else "?"
    time_end = ends[-1] if ends else "?"

    sorted_tasks = sorted(tasks, key=lambda t: t.get("start_time", "00:00"))

    # === Insight Cards ===
    analysis = analysis or {}
    insights = analysis.get("top_insights", [])
    insight_html = ""
    if insights:
        cards = ""
        for ins in insights[:3]:
            h = escape_html(ins.get("highlight", ""))
            c = ins.get("color", "#8b949e")
            t = escape_html(ins.get("text", ""))
            cards += f'<div class="insight-card"><span class="insight-num" style="color:{c}">{h}</span>{t}</div>'
        insight_html = f'<div class="insight-grid">{cards}</div>'

    # === Timeline ===
    assigned, num_lanes = swimlane_assign(tasks)
    all_starts_m = [time_to_minutes(t.get("start_time", "09:00")) for t in tasks]
    all_ends_m = [time_to_minutes(t.get("end_time", "09:01")) for t in tasks]
    SH = min(all_starts_m) // 60 if all_starts_m else 9
    EH = math.ceil(max(all_ends_m) / 60) if all_ends_m else 21
    if EH <= SH:
        EH = SH + 1
    PPM = 2.2
    TW = (EH - SH) * 60 * PPM

    tl_header = ""
    for hr in range(SH, EH):
        tl_header += f'<div class="timeline-hour" style="width:{60*PPM}px">{hr:02d}:00</div>'

    tl_grid = ""
    for hr in range(SH, EH):
        x = (hr - SH) * 60 * PPM
        tl_grid += f'<div style="position:absolute;left:{x}px;top:0;bottom:0;width:1px;background:#21262d;z-index:1;"></div>'

    tl_blocks = ""
    for t in assigned:
        lane = t["lane"]
        s_min = time_to_minutes(t.get("start_time", "00:00")) - SH * 60
        e_min = time_to_minutes(t.get("end_time", "00:00")) - SH * 60
        e_min = max(e_min, s_min + 2)
        left = s_min * PPM
        width = max((e_min - s_min) * PPM, 20)
        top = lane * 28 + 2
        color = get_cat_color(t.get("category", "未分类"))
        cat = escape_html(t.get("category", "未分类"))
        sum_esc = escape_html(t.get("summary", ""))
        con_short = escape_html(truncate(t.get("conclusion", ""), 80))
        cost_val = t.get("cost", 0)
        st = t.get("start_time", "")
        et = t.get("end_time", "")
        dur = t.get("duration_min", 0)
        tid = t["id"]
        tip = f"#{tid} | {cat} | {sum_esc} | {st}-{et} | {dur}min | {con_short} | ${cost_val:.2f}"

        tl_blocks += (
            f'<div class="timeline-block" '
            f'style="left:{left}px;width:{width}px;background:{color};top:{top}px;" '
            f'data-tip="{tip}" '
            f'onmouseenter="showTip(event)" onmouseleave="hideTip()" onmousemove="moveTip(event)">'
            f'{cat}</div>'
        )

    lane_area_h = num_lanes * 28 + 4

    # === Table ===
    table_rows = ""
    for t in sorted_tasks:
        cat = t.get("category", "未分类")
        color = get_cat_color(cat)
        st = t.get("start_time", "")
        et = t.get("end_time", "")
        dur = t.get("duration_min", 0)
        sum_esc = escape_html(t.get("summary", ""))
        turns = format_turns(t.get("turns", {}))
        tok_str = format_tokens(t.get("tokens", {}))
        cost_val = t.get("cost", 0)
        tid = t["id"]

        table_rows += (
            f'<tr>'
            f'<td>{tid}</td>'
            f'<td><span class="cat-badge" style="background:{color}">{escape_html(cat)}</span></td>'
            f'<td>{sum_esc}</td>'
            f'<td class="time-cell">{st} - {et}</td>'
            f'<td>{dur}min</td>'
            f'<td>{turns}</td>'
            f'<td>{tok_str}</td>'
            f'<td class="cost-cell">${cost_val:.2f}</td>'
            f'</tr>'
        )

    # === Charts data ===
    cat_count = defaultdict(int)
    for t in tasks:
        cat_count[t.get("category", "未分类")] += 1
    sorted_cats = sorted(cat_count.items(), key=lambda x: x[1], reverse=True)
    d_labels = safe_js([c for c, _ in sorted_cats])
    d_data = safe_js([n for _, n in sorted_cats])
    d_colors = safe_js([get_cat_color(c) for c, _ in sorted_cats])

    cost_sorted = sorted(tasks, key=lambda t: t.get("cost", 0), reverse=True)[:10]
    b_labels = safe_js([f"#{t['id']} {truncate(t.get('summary',''), 22)}" for t in cost_sorted])
    b_data = safe_js([round(t.get("cost", 0), 2) for t in cost_sorted])
    b_colors = safe_js([get_cat_color(t.get("category", "未分类")) for t in cost_sorted])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Toil 日报 | {escape_html(date_str)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; padding: 20px; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
  .subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 20px; }}
  .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
  .section-title {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #e6edf3; }}

  .kpi-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 20px; }}
  .kpi-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; text-align: center; }}
  .kpi-value {{ font-size: 28px; font-weight: 700; color: #58a6ff; }}
  .kpi-value.cost {{ color: #f85149; }}
  .kpi-value.time {{ color: #3fb950; }}
  .kpi-label {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}

  .insight-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
  .insight-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px 20px; font-size: 14px; line-height: 1.7; }}
  .insight-num {{ font-size: 24px; font-weight: 700; margin-right: 8px; }}

  .timeline-container {{ overflow-x: auto; position: relative; }}
  .timeline-header {{ display: flex; height: 28px; border-bottom: 1px solid #30363d; margin-bottom: 4px; position: sticky; top: 0; background: #161b22; z-index: 10; }}
  .timeline-hour {{ flex: none; text-align: center; font-size: 11px; color: #8b949e; border-left: 1px solid #21262d; }}
  .timeline-block {{ position: absolute; height: 22px; border-radius: 3px; display: flex; align-items: center; padding: 0 4px; overflow: hidden; white-space: nowrap; font-size: 11px; color: #fff; cursor: pointer; min-width: 4px; top: 2px; opacity: 0.9; transition: opacity 0.15s; }}
  .timeline-block:hover {{ opacity: 1; z-index: 20; box-shadow: 0 0 8px rgba(255,255,255,0.3); }}

  .custom-tooltip {{ display: none; position: fixed; background: #1c2128; border: 1px solid #444c56; border-radius: 6px; padding: 10px 14px; font-size: 12px; line-height: 1.8; color: #e6edf3; z-index: 1000; max-width: 450px; pointer-events: none; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
  .tt-row {{ display: flex; gap: 8px; }}
  .tt-label {{ color: #8b949e; min-width: 48px; flex-shrink: 0; }}
  .tt-value {{ color: #e6edf3; }}

  .task-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .task-table th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #30363d; color: #8b949e; font-weight: 500; position: sticky; top: 0; background: #161b22; }}
  .task-table td {{ padding: 6px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }}
  .task-table tr:hover td {{ background: #1c2128; }}
  .cat-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; color: #fff; }}
  .cost-cell {{ text-align: right; font-family: 'SF Mono', Monaco, monospace; }}
  .time-cell {{ white-space: nowrap; font-family: 'SF Mono', Monaco, monospace; font-size: 12px; }}
  .table-scroll {{ max-height: 500px; overflow-y: auto; }}

  .charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .chart-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
  .chart-box canvas {{ max-height: 320px; }}

  .footer {{ text-align: center; color: #484f58; font-size: 12px; padding: 16px 0; }}

  @media (max-width: 900px) {{
    .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }}
    .charts-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<div class="container">
  <h1>Toil 日报 | {escape_html(date_str)}</h1>
  <div class="subtitle">时间范围 {time_start} - {time_end} | {session_count} Sessions | {total_tasks} 个有效任务</div>

  <div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-value">{total_tasks}</div><div class="kpi-label">有效任务</div></div>
    <div class="kpi-card"><div class="kpi-value">{session_count}</div><div class="kpi-label">Sessions</div></div>
    <div class="kpi-card"><div class="kpi-value cost">${total_cost:.0f}</div><div class="kpi-label">总费用</div></div>
    <div class="kpi-card"><div class="kpi-value time">{dur_str}</div><div class="kpi-label">总工时(累计)</div></div>
    <div class="kpi-card"><div class="kpi-value">{peak}</div><div class="kpi-label">峰值并行</div></div>
    <div class="kpi-card"><div class="kpi-value cost">${avg_cost:.1f}</div><div class="kpi-label">均费/任务</div></div>
  </div>

  {insight_html}

  <div class="section">
    <div class="section-title">Timeline 甘特图 <span style="font-size:12px;color:#8b949e;font-weight:normal;">(泳道合并 - 不重叠任务共用行)</span></div>
    <div class="timeline-container">
      <div class="timeline-header">{tl_header}</div>
      <div style="position:relative;width:{TW}px;height:{lane_area_h}px;">
        {tl_grid}
        {tl_blocks}
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">任务明细 ({total_tasks} 个有效任务)</div>
    <div class="table-scroll">
      <table class="task-table">
        <thead>
          <tr>
            <th>#</th>
            <th>分类</th>
            <th>摘要</th>
            <th>时间</th>
            <th>耗时</th>
            <th>轮次</th>
            <th>Token</th>
            <th>费用</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="charts-grid">
    <div class="chart-box">
      <div class="section-title">分类分布 (按任务数)</div>
      <canvas id="categoryChart"></canvas>
    </div>
    <div class="chart-box">
      <div class="section-title">费用 Top 10</div>
      <canvas id="costChart"></canvas>
    </div>
  </div>

  <div class="footer">Generated by Claude Code /toil-report</div>
</div>

<div class="custom-tooltip" id="tooltip"></div>

<script>
var tip = document.getElementById('tooltip');
function showTip(ev) {{
  var parts = ev.target.dataset.tip.split(' | ');
  var labels = ['序号','分类','摘要','时间','耗时','结论','费用'];
  tip.innerHTML = parts.map(function(p, i) {{
    return '<div class="tt-row"><span class="tt-label">' + (labels[i]||'') + '</span><span class="tt-value">' + p + '</span></div>';
  }}).join('');
  tip.style.display = 'block';
  moveTip(ev);
}}
function moveTip(ev) {{
  var x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + 460 > window.innerWidth) x = ev.clientX - 470;
  if (y + 220 > window.innerHeight) y = ev.clientY - 230;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}}
function hideTip() {{ tip.style.display = 'none'; }}

(function() {{
  var totalTasks = {total_tasks};

  new Chart(document.getElementById('categoryChart'), {{
    type: 'doughnut',
    data: {{
      labels: {d_labels},
      datasets: [{{ data: {d_data}, backgroundColor: {d_colors}, borderWidth: 0 }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: true,
      plugins: {{
        legend: {{ position: 'right', labels: {{ color: '#8b949e', font: {{ size: 12 }}, padding: 8 }} }},
        datalabels: {{
          color: '#fff',
          font: {{ size: 11, weight: 'bold' }},
          formatter: function(v, ctx) {{
            var label = ctx.chart.data.labels[ctx.dataIndex];
            var pct = (v / totalTasks * 100).toFixed(0);
            return v >= 2 ? label + '\\n' + v + ' (' + pct + '%)' : '';
          }}
        }}
      }}
    }},
    plugins: [ChartDataLabels]
  }});

  new Chart(document.getElementById('costChart'), {{
    type: 'bar',
    data: {{
      labels: {b_labels},
      datasets: [{{
        data: {b_data},
        backgroundColor: {b_colors},
        borderWidth: 0,
        borderRadius: 4
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: true,
      scales: {{
        x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', callback: function(v) {{ return '$' + v; }} }} }},
        y: {{ grid: {{ display: false }}, ticks: {{ color: '#8b949e', font: {{ size: 11 }} }} }}
      }},
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{ color: '#fff', anchor: 'end', align: 'left', font: {{ size: 11 }}, formatter: function(v) {{ return '$' + v.toFixed(1); }} }}
      }}
    }},
    plugins: [ChartDataLabels]
  }});
}})();
</script>
</body>
</html>"""


def main():
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("用法: python3 render_daily.py <raw_json_path> <enriched_json_path> [<analysis_json_path>]", file=sys.stderr)
        sys.exit(1)

    raw_path = sys.argv[1]
    enriched_path = sys.argv[2]

    try:
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        print(f"Error reading raw JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(enriched_path, "r", encoding="utf-8") as f:
            enriched_list = json.load(f)
    except Exception as e:
        print(f"Error reading enriched JSON: {e}", file=sys.stderr)
        sys.exit(1)

    analysis = {}
    if len(sys.argv) == 4:
        try:
            with open(sys.argv[3], "r", encoding="utf-8") as f:
                analysis = json.load(f)
        except Exception:
            analysis = {}

    raw_tasks = raw_data.get("tasks", [])
    tasks = merge_tasks(raw_tasks, enriched_list)

    date_range = raw_data.get("date_range", "")
    if date_range:
        date_str = date_range.split("~")[0].strip()
    else:
        date_str = raw_data.get("date", "")

    html = build_html(raw_data, tasks, date_str, analysis)
    sys.stdout.write(html)


if __name__ == "__main__":
    main()
