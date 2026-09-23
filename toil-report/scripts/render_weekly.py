#!/usr/bin/env python3
"""
render_weekly.py -- Toil 周报 HTML 渲染脚本 (v3)

用法：
    python3 render_weekly.py <raw_json> <enriched_json> <analysis_json>

输出：
    HTML 字符串到 stdout

说明：
    接受 3 个 JSON 输入：raw data, enriched per-task data, analysis insights，
    生成 8 段 SRE Toil 分析周报 (W14 视觉风格)。
"""

import json
import sys
from collections import defaultdict
from datetime import datetime


# ─────────────────────────────────────────────
# Helpers (kept from v2)
# ─────────────────────────────────────────────

import os as _os
_CATEGORIES_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "categories.json")
with open(_CATEGORIES_PATH) as _f:
    CATEGORIES = json.load(_f)

WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

AUTOMATION_BADGES = {
    "full":   ("可完全自动化", "badge-green"),
    "semi":   ("可半自动化",   "badge-yellow"),
    "manual": ("需人工判断",   "badge-red"),
}


def fmt_min(m):
    h, mm = divmod(int(m), 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


def fmt_tok(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def cat_color(cat):
    return CATEGORIES.get(cat, "#8b949e")


def escape_html(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def truncate(s, n):
    s = str(s)
    return s[:n] + "..." if len(s) > n else s


def safe_js(data):
    """将 Python 对象序列化为在 <script> 块中安全使用的 JSON 字符串"""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def format_tokens_dict(tok_dict):
    total = (
        tok_dict.get("input", 0)
        + tok_dict.get("output", 0)
        + tok_dict.get("cache_read", 0)
        + tok_dict.get("cache_creation", 0)
    )
    return fmt_tok(total)


def merge_tasks(raw, enriched_list):
    enrich_map = {e["id"]: e for e in enriched_list}
    tasks = []
    for t in raw.get("tasks", []):
        e = enrich_map.get(t["id"], {})
        merged = {**t, **e}
        merged.setdefault("summary", truncate(t.get("first_msg", "") or "", 40) or "(无摘要)")
        merged.setdefault("category", "未分类")
        merged.setdefault("trigger", "主动")
        merged.setdefault("automation", "manual")
        merged.setdefault("repeat_key", None)
        tasks.append(merged)
    return tasks


def get_weekday_str(date_str):
    """YYYY-MM-DD -> 周X"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return WEEKDAY_ZH[dt.weekday()]
    except Exception:
        return ""


def get_weekday_short(date_str):
    """YYYY-MM-DD -> 一/二/三..."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return ["一", "二", "三", "四", "五", "六", "日"][dt.weekday()]
    except Exception:
        return ""


def parse_week_range(raw_data, tasks):
    """从 raw_data 或 tasks 推断周范围，返回 (week_label, date_start, date_end)"""
    date_range = raw_data.get("date_range", "")
    if date_range and "~" in date_range:
        parts = date_range.split("~")
        date_start = parts[0].strip()
        date_end = parts[1].strip()
    elif tasks:
        dates = sorted(t.get("date", "") for t in tasks if t.get("date"))
        date_start = dates[0] if dates else ""
        date_end = dates[-1] if dates else ""
    else:
        date_start = date_end = ""

    week_num = ""
    year_str = ""
    if date_start:
        try:
            dt = datetime.strptime(date_start, "%Y-%m-%d")
            _year, week, _ = dt.isocalendar()
            week_num = f"W{week:02d}"
            year_str = str(_year)
        except Exception:
            year_str = date_start[:4]

    def fmt_date(d):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            return dt.strftime("%m/%d")
        except Exception:
            return d

    week_label = f"{year_str} {week_num} ({fmt_date(date_start)} - {fmt_date(date_end)})"
    return week_label, date_start, date_end


def badge_automation(auto):
    label, cls = AUTOMATION_BADGES.get(auto, ("未知", "badge-red"))
    return f'<span class="badge {cls}">{label}</span>'


# ─────────────────────────────────────────────
# CSS (copied from W14 reference + minor additions)
# ─────────────────────────────────────────────

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: #0d1117;
  color: #e6edf3;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif;
  line-height: 1.6;
  padding: 24px 16px;
}
.container { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
h2 { font-size: 22px; font-weight: 600; margin-bottom: 16px; color: #e6edf3; }
h3 { font-size: 17px; font-weight: 600; margin-bottom: 12px; color: #e6edf3; }
.subtitle { color: #8b949e; font-size: 15px; margin-bottom: 32px; }
.section { margin-bottom: 48px; }
.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 16px;
}
.card-sm {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 12px;
  padding: 16px;
}

/* S1 KPI Cards */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}
.kpi-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 12px;
  padding: 20px 16px;
  text-align: center;
}
.kpi-value { font-size: 32px; font-weight: 700; color: #58a6ff; }
.kpi-value.red { color: #f85149; }
.kpi-value.orange { color: #f0883e; }
.kpi-value.purple { color: #d2a8ff; }
.kpi-value.green { color: #3fb950; }
.kpi-label { font-size: 13px; color: #8b949e; margin-top: 4px; }
.kpi-sub { font-size: 12px; color: #6e7681; margin-top: 2px; }
.note-line { color: #8b949e; font-size: 13px; text-align: center; margin-top: 12px; line-height: 1.8; }

/* S2 Layout */
.s2-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
.chart-wrap { position: relative; max-width: 380px; margin: 0 auto; }
.chart-center-label {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  font-size: 36px; font-weight: 700; color: #e6edf3; pointer-events: none;
}
.insight-cards { display: flex; flex-direction: column; gap: 12px; }
.insight-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  padding: 16px 20px; font-size: 14px; line-height: 1.7;
}
.insight-card .num { font-size: 24px; font-weight: 700; margin-right: 8px; }

/* S3 Layout */
.s3-grid { display: grid; grid-template-columns: 1.5fr 1fr; gap: 24px; align-items: start; }
.day-summary { display: flex; flex-direction: column; gap: 10px; }
.day-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  padding: 14px 18px; font-size: 14px;
}
.day-card .day-label { font-weight: 600; color: #58a6ff; }
.day-card .day-hours { font-weight: 700; font-size: 18px; }
.day-card .day-note { color: #8b949e; font-size: 13px; }

/* S4 */
.auto-bar-wrap { margin-bottom: 20px; }
.auto-bar-label { display: flex; justify-content: space-between; font-size: 14px; margin-bottom: 6px; }
.auto-bar-outer { background: #21262d; border-radius: 8px; height: 28px; overflow: hidden; }
.auto-bar-inner { height: 100%; border-radius: 8px; display: flex; align-items: center; padding-left: 12px; font-size: 12px; font-weight: 600; color: #0d1117; }
.auto-table { width: 100%; margin-top: 12px; }

/* Tables */
table {
  width: 100%; border-collapse: collapse; font-size: 13px;
}
th {
  text-align: left; padding: 10px 12px; background: #21262d;
  color: #8b949e; font-weight: 600; border-bottom: 1px solid #30363d;
  white-space: nowrap;
}
td {
  padding: 9px 12px; border-bottom: 1px solid #21262d;
}
tr:nth-child(even) td { background: #161b22; }
tr:nth-child(odd) td { background: #0d1117; }
tr:hover td { background: #1c2128; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.badge-green { background: rgba(63,185,80,0.2); color: #3fb950; }
.badge-yellow { background: rgba(255,166,87,0.2); color: #ffa657; }
.badge-red { background: rgba(248,81,73,0.2); color: #f85149; }

/* S6 stacked bar */
.stacked-bar-container { margin: 20px 0; }
.stacked-bar-outer {
  height: 40px; border-radius: 10px; overflow: hidden;
  display: flex; background: #21262d;
}
.stacked-bar-seg { display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; }
.s6-insights { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
.s6-insight { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 16px; font-size: 14px; }

/* S7 */
.trend-up { color: #f85149; }
.trend-flat { color: #8b949e; }
.status-no { color: #f85149; }
.status-wip { color: #ffa657; }

/* S8 timeline */
.timeline { position: relative; padding-left: 24px; }
.timeline::before {
  content: ''; position: absolute; left: 8px; top: 0; bottom: 0;
  width: 2px; background: #30363d;
}
.tl-item { position: relative; margin-bottom: 24px; }
.tl-dot {
  position: absolute; left: -20px; top: 6px; width: 12px; height: 12px;
  border-radius: 50%; border: 2px solid #30363d;
}
.tl-dot.done { background: #3fb950; border-color: #3fb950; }
.tl-dot.planned { background: #58a6ff; border-color: #58a6ff; }
.tl-week { font-weight: 700; font-size: 15px; margin-bottom: 8px; color: #58a6ff; }
.tl-tasks { font-size: 13px; color: #8b949e; line-height: 2; }
.tl-tasks span { color: #e6edf3; }

.group-header td {
  background: #1c2128 !important; font-weight: 700; color: #58a6ff;
  font-size: 14px; padding: 12px;
}
footer {
  text-align: center; color: #6e7681; font-size: 12px;
  margin-top: 48px; padding: 24px 0; border-top: 1px solid #21262d;
}

@media (max-width: 768px) {
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
  .s2-grid, .s3-grid { grid-template-columns: 1fr; }
  .s6-insights { grid-template-columns: 1fr; }
}
"""


# ─────────────────────────────────────────────
# S1 / 本周 Toil 总览
# ─────────────────────────────────────────────

def build_s1(tasks, analysis):
    total_duration = sum(t.get("duration_min", 0) for t in tasks)
    total_tasks = len(tasks)

    active_dates = set(t.get("date", "") for t in tasks if t.get("date"))
    num_days = len(active_dates) or 1
    avg_duration = total_duration / num_days

    total_hours = total_duration / 60
    avg_hours = avg_duration / 60

    # Total hours color
    total_color = "red" if total_hours >= 20 else "orange"

    # Avg hours color
    avg_color = "orange" if avg_hours > 4 else "green"

    # P0/P1 from analysis
    p0_p1_events = analysis.get("p0_p1_events", "无")
    p0_p1_detail = analysis.get("p0_p1_detail", "")
    p0_p1_color = "red" if "P0" in str(p0_p1_events) else ("orange" if "P1" in str(p0_p1_events) else "green")

    # Biggest item from analysis
    biggest = analysis.get("biggest_item", {})
    biggest_name = biggest.get("name", "N/A")
    biggest_hours = biggest.get("hours", 0)
    biggest_value = f"{biggest_hours}h" if biggest_hours else "N/A"

    return f"""
<div class="section">
  <h2>S1 / 本周 Toil 总览</h2>
  <div class="kpi-row">
    <div class="kpi-card">
      <div class="kpi-value {total_color}">{total_hours:.0f}h</div>
      <div class="kpi-label">总耗时</div>
      <div class="kpi-sub">{total_duration:,} min / {num_days} 个工作日</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {avg_color}">{avg_hours:.1f}h</div>
      <div class="kpi-label">日均耗时</div>
      <div class="kpi-sub">目标: &lt;4h</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value purple">{total_tasks}</div>
      <div class="kpi-label">任务总数</div>
      <div class="kpi-sub">项</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {p0_p1_color}">{escape_html(str(p0_p1_events))}</div>
      <div class="kpi-label">P0/P1 事件</div>
      <div class="kpi-sub">{escape_html(p0_p1_detail) if p0_p1_detail else ''}</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value orange">{biggest_value}</div>
      <div class="kpi-label">最大单项</div>
      <div class="kpi-sub">{escape_html(truncate(biggest_name, 20))}</div>
    </div>
  </div>
  <div class="note-line">日报记录的全部是需要人工介入的工作。自动化完成的工作不会出现在日报中。<br>周报目标：让这个数字持续下降。</div>
</div>"""


# ─────────────────────────────────────────────
# S2 / Toil 时间都去哪了
# ─────────────────────────────────────────────

def build_s2(tasks, analysis):
    # Category duration aggregation
    cat_dur = defaultdict(int)
    for t in tasks:
        cat = t.get("category", "未分类")
        cat_dur[cat] += t.get("duration_min", 0)

    total_dur = sum(cat_dur.values())
    total_hours = total_dur / 60
    sorted_cats = sorted(cat_dur.items(), key=lambda x: x[1], reverse=True)

    labels = [c for c, _ in sorted_cats]
    data = [d for _, d in sorted_cats]
    colors = [cat_color(c) for c in labels]

    labels_js = safe_js(labels)
    data_js = safe_js(data)
    colors_js = safe_js(colors)

    # Insight cards from analysis
    insights = analysis.get("top_insights", [])
    insight_html = ""
    if insights:
        insight_html = '<div class="insight-cards">'
        for ins in insights:
            highlight = escape_html(ins.get("highlight", ""))
            color = ins.get("color", "#58a6ff")
            text = escape_html(ins.get("text", ""))
            insight_html += f"""
      <div class="insight-card">
        <span class="num" style="color:{color}">{highlight}</span>
        {text}
      </div>"""
        insight_html += "\n    </div>"

    return f"""
<div class="section">
  <h2>S2 / Toil 时间都去哪了</h2>
  <div class="s2-grid">
    <div>
      <div class="chart-wrap">
        <canvas id="doughnutChart"></canvas>
        <div class="chart-center-label">{total_hours:.0f}h</div>
      </div>
    </div>
    {insight_html}
  </div>
</div>
<script>
(function() {{
  Chart.register(ChartDataLabels);
  const dCtx = document.getElementById('doughnutChart').getContext('2d');
  const totalDur = {total_dur};
  new Chart(dCtx, {{
    type: 'doughnut',
    data: {{
      labels: {labels_js},
      datasets: [{{
        data: {data_js},
        backgroundColor: {colors_js},
        borderColor: '#0d1117',
        borderWidth: 3,
      }}]
    }},
    options: {{
      cutout: '62%',
      responsive: true,
      plugins: {{
        legend: {{
          position: 'bottom',
          labels: {{ color: '#8b949e', padding: 12, font: {{ size: 12 }}, usePointStyle: true, pointStyleWidth: 10 }}
        }},
        datalabels: {{
          color: '#e6edf3',
          font: {{ size: 11, weight: 'bold' }},
          formatter: (value, ctx) => {{
            let pct = (value / totalDur * 100).toFixed(1);
            return pct > 4 ? pct + '%' : '';
          }}
        }}
      }}
    }}
  }});
}})();
</script>"""


# ─────────────────────────────────────────────
# S3 / 每日 Toil 热力图
# ─────────────────────────────────────────────

def build_s3(tasks, analysis):
    # Aggregate by day & category
    day_cat_dur = defaultdict(lambda: defaultdict(int))
    for t in tasks:
        date = t.get("date", "unknown")
        cat = t.get("category", "未分类")
        day_cat_dur[date][cat] += t.get("duration_min", 0)

    sorted_dates = sorted(day_cat_dur.keys())
    if not sorted_dates:
        return '<div class="section"><h2>S3 / 每日 Toil 热力图</h2><p style="color:#8b949e">无数据</p></div>'

    # Date labels
    def fmt_label(d):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            wd = get_weekday_short(d)
            return dt.strftime("%m/%d") + f" ({wd})"
        except Exception:
            return d

    date_labels = [fmt_label(d) for d in sorted_dates]

    # All categories sorted by total duration
    cat_totals = defaultdict(int)
    for day_cats in day_cat_dur.values():
        for cat, dur in day_cats.items():
            cat_totals[cat] += dur
    all_cats = sorted(cat_totals.keys(), key=lambda c: cat_totals[c], reverse=True)

    # Build datasets
    datasets_js_parts = []
    for cat in all_cats:
        data = [day_cat_dur[d].get(cat, 0) for d in sorted_dates]
        color = cat_color(cat)
        datasets_js_parts.append(
            f"""{{
                label: {safe_js(cat)},
                data: {safe_js(data)},
                backgroundColor: '{color}',
                borderRadius: 2,
            }}"""
        )
    datasets_js = ",\n".join(datasets_js_parts)
    date_labels_js = safe_js(date_labels)

    day_totals = [sum(day_cat_dur[d].values()) for d in sorted_dates]
    day_totals_js = safe_js(day_totals)

    # Day summary cards from analysis
    daily_summaries = analysis.get("daily_summaries", {})
    day_cards_html = ""
    if daily_summaries:
        day_cards_html = '<div class="day-summary">'
        for date_key in sorted(daily_summaries.keys()):
            ds = daily_summaries[date_key]
            hours = ds.get("hours", 0)
            color = ds.get("color", "#8b949e")
            note = escape_html(ds.get("note", ""))
            try:
                dt = datetime.strptime(date_key, "%Y-%m-%d")
                wd = get_weekday_short(date_key)
                label = f"{dt.month}/{dt.day}" + f" ({wd})"
            except Exception:
                label = date_key
            day_cards_html += f"""
      <div class="day-card">
        <span class="day-label">{label}</span>
        <span class="day-hours" style="color:{color}; margin-left:12px;">{hours}h</span>
        <div class="day-note">{note}</div>
      </div>"""
        day_cards_html += "\n    </div>"

    return f"""
<div class="section">
  <h2>S3 / 每日 Toil 热力图</h2>
  <div class="s3-grid">
    <div class="card" style="padding:16px;">
      <canvas id="stackedBarChart" height="260"></canvas>
    </div>
    {day_cards_html}
  </div>
</div>
<script>
(function() {{
  const dayTotals = {day_totals_js};
  const sCtx = document.getElementById('stackedBarChart').getContext('2d');
  new Chart(sCtx, {{
    type: 'bar',
    data: {{
      labels: {date_labels_js},
      datasets: [{datasets_js}]
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ stacked: true, ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
        y: {{ stacked: true, ticks: {{ color: '#8b949e', callback: v => v + 'min' }}, grid: {{ color: '#21262d' }} }}
      }},
      plugins: {{
        legend: {{
          position: 'bottom',
          labels: {{ color: '#8b949e', font: {{ size: 10 }}, usePointStyle: true, pointStyleWidth: 8, padding: 8 }}
        }},
        datalabels: {{
          display: function(ctx) {{
            const datasets = ctx.chart.data.datasets;
            let topIdx = -1;
            for (let i = datasets.length - 1; i >= 0; i--) {{
              if (datasets[i].data[ctx.dataIndex] > 0) {{ topIdx = i; break; }}
            }}
            return ctx.datasetIndex === topIdx;
          }},
          anchor: 'end',
          align: 'end',
          color: '#e6edf3',
          font: {{ size: 12, weight: 'bold' }},
          formatter: (value, ctx) => {{
            const total = dayTotals[ctx.dataIndex];
            if (!total) return '';
            const h = Math.floor(total/60);
            const m = total % 60;
            return h > 0 ? h+'h '+m+'m' : m+'m';
          }}
        }}
      }}
    }}
  }});
}})();
</script>"""


# ─────────────────────────────────────────────
# S4 / 自动化潜力分析
# ─────────────────────────────────────────────

def build_s4(tasks, analysis):
    # Calculate automation breakdown from tasks
    automation_dur = defaultdict(int)
    for t in tasks:
        auto = t.get("automation", "manual") or "manual"
        automation_dur[auto] += t.get("duration_min", 0)
    total_dur = sum(automation_dur.values()) or 1

    full_dur = automation_dur.get("full", 0)
    semi_dur = automation_dur.get("semi", 0)
    manual_dur = automation_dur.get("manual", 0)
    full_pct = full_dur / total_dur * 100
    semi_pct = semi_dur / total_dur * 100
    manual_pct = manual_dur / total_dur * 100

    bars_html = f"""
    <div class="auto-bar-wrap">
      <div class="auto-bar-label">
        <span style="color:#3fb950; font-weight:600;">可完全自动化</span>
        <span>{full_dur}min ({full_pct:.0f}%) -- 目标：从日报消失</span>
      </div>
      <div class="auto-bar-outer">
        <div class="auto-bar-inner" style="width:{full_pct:.1f}%; background: linear-gradient(90deg, #3fb950, #56d364);">{full_pct:.0f}%</div>
      </div>
    </div>
    <div class="auto-bar-wrap">
      <div class="auto-bar-label">
        <span style="color:#ffa657; font-weight:600;">可半自动化</span>
        <span>{semi_dur}min ({semi_pct:.0f}%) -- 目标：耗时降 80%</span>
      </div>
      <div class="auto-bar-outer">
        <div class="auto-bar-inner" style="width:{semi_pct:.1f}%; background: linear-gradient(90deg, #f0883e, #ffa657);">{semi_pct:.0f}%</div>
      </div>
    </div>
    <div class="auto-bar-wrap">
      <div class="auto-bar-label">
        <span style="color:#f85149; font-weight:600;">需人工判断</span>
        <span>{manual_dur}min ({manual_pct:.0f}%) -- 目标：工具辅助提速</span>
      </div>
      <div class="auto-bar-outer">
        <div class="auto-bar-inner" style="width:{manual_pct:.1f}%; background: linear-gradient(90deg, #da3633, #f85149);">{manual_pct:.0f}%</div>
      </div>
    </div>"""

    # Detail tables from analysis
    auto_details = analysis.get("automation_details", {})
    detail_html = ""

    full_items = auto_details.get("full", [])
    if full_items:
        rows = ""
        for item in full_items:
            rows += f"""<tr>
        <td>{escape_html(item.get('item', ''))}</td>
        <td>{item.get('duration_min', 0)}min</td>
        <td>{escape_html(item.get('solution', ''))}</td>
        <td>{escape_html(item.get('impl_cost', ''))}</td>
        <td>{escape_html(item.get('weekly_save', ''))}</td>
      </tr>"""
        full_total = sum(i.get("duration_min", 0) for i in full_items)
        detail_html += f"""
  <div class="card">
    <h3 style="color:#3fb950;">可完全自动化 ({full_total}min, {full_total/total_dur*100:.0f}%)</h3>
    <table>
      <thead><tr><th>项目</th><th>耗时</th><th>自动化方案</th><th>实施成本</th><th>消除后周省</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""

    semi_items = auto_details.get("semi", [])
    if semi_items:
        rows = ""
        for item in semi_items:
            rows += f"""<tr>
        <td>{escape_html(item.get('item', ''))}</td>
        <td>{item.get('duration_min', 0)}min</td>
        <td>{escape_html(item.get('solution', ''))}</td>
        <td>{escape_html(item.get('reduce_to', ''))}</td>
      </tr>"""
        semi_total = sum(i.get("duration_min", 0) for i in semi_items)
        detail_html += f"""
  <div class="card">
    <h3 style="color:#ffa657;">可半自动化 ({semi_total}min, {semi_total/total_dur*100:.0f}%)</h3>
    <table>
      <thead><tr><th>项目</th><th>耗时</th><th>方案</th><th>预期降到</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""

    manual_items = auto_details.get("manual", [])
    if manual_items:
        rows = ""
        for item in manual_items:
            rows += f"""<tr>
        <td>{escape_html(item.get('item', ''))}</td>
        <td>{item.get('duration_min', 0)}min</td>
        <td>{escape_html(item.get('tool_assist', ''))}</td>
      </tr>"""
        manual_total = sum(i.get("duration_min", 0) for i in manual_items)
        detail_html += f"""
  <div class="card">
    <h3 style="color:#f85149;">需人工判断 ({manual_total}min, {manual_total/total_dur*100:.0f}%)</h3>
    <table>
      <thead><tr><th>项目</th><th>耗时</th><th>可工具辅助的部分</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""

    return f"""
<div class="section">
  <h2>S4 / 自动化潜力分析</h2>
  <div class="card">
    {bars_html}
  </div>
  {detail_html}
</div>"""


# ─────────────────────────────────────────────
# S5 / Toil 任务全清单
# ─────────────────────────────────────────────

_S5_TABLE_HEAD = "<tr><th>#</th><th>开始</th><th>分类</th><th>任务描述</th><th>耗时</th><th>自动化潜力</th></tr>"


def _render_s5_row(idx, t):
    return f"""<tr>
  <td>{idx}</td>
  <td>{escape_html(t.get("start_time") or "")}</td>
  <td>{escape_html(t.get("category") or "未分类")}</td>
  <td>{escape_html(t.get("summary") or "")}</td>
  <td>{t.get("duration_min") or 0}min</td>
  <td>{badge_automation(t.get("automation") or "manual")}</td>
</tr>"""


def _format_date_short(date):
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except Exception:
        return date


def _render_day_block(date, day_list):
    total = len(day_list)
    day_dur = sum((t.get("duration_min") or 0) for t in day_list)
    day_hours = day_dur / 60
    wd = get_weekday_short(date)
    date_short = _format_date_short(date)

    top_n = sorted(day_list, key=lambda t: t.get("duration_min") or 0, reverse=True)[:10]
    top_rows = "\n".join(_render_s5_row(i + 1, t) for i, t in enumerate(top_n))
    top_label = f"Top {len(top_n)} 耗时任务" if total > 10 else f"全部 {total} 条（按耗时降序）"

    details_html = ""
    if total > 10:
        full_list = sorted(day_list, key=lambda t: t.get("start_time") or "00:00")
        full_rows = "\n".join(_render_s5_row(i + 1, t) for i, t in enumerate(full_list))
        details_html = f"""
  <details style="margin-top:8px;">
    <summary style="cursor:pointer; color:#58a6ff;">展开完整清单（共 {total} 条，按时间先后顺序）</summary>
    <div class="card" style="overflow-x:auto; margin-top:8px;">
      <table>
        <thead>{_S5_TABLE_HEAD}</thead>
        <tbody>
          {full_rows}
        </tbody>
      </table>
    </div>
  </details>"""

    return f"""
<div class="day-block" style="margin-bottom:24px;">
  <div style="color:#58a6ff; font-weight:600; margin:16px 0 8px;">
    {date_short} ({wd}) -- 合计 {day_dur}min / {day_hours:.1f}h · {top_label}
  </div>
  <div class="card" style="overflow-x:auto;">
    <table>
      <thead>{_S5_TABLE_HEAD}</thead>
      <tbody>
        {top_rows}
      </tbody>
    </table>
  </div>{details_html}
</div>
"""


def build_s5(tasks):
    """每天先渲染 Top 10 耗时任务（耗时降序），超过 10 条时在折叠区展示全量按时间顺序。"""
    if not tasks:
        return '<div class="section"><h2>S5 / Toil 任务清单</h2><p style="color:#8b949e">无数据</p></div>'

    day_tasks = defaultdict(list)
    for t in tasks:
        day_tasks[t.get("date", "unknown")].append(t)

    days_html = "".join(_render_day_block(d, day_tasks[d]) for d in sorted(day_tasks))

    return f"""
<div class="section">
  <h2>S5 / Toil 任务清单（每日 Top 10 耗时 · 可展开全量）</h2>
  {days_html}
</div>"""


# ─────────────────────────────────────────────
# S6 / 被动 vs 主动
# ─────────────────────────────────────────────

def build_s6(tasks, analysis):
    passive_dur = sum(t.get("duration_min", 0) for t in tasks if t.get("trigger") == "被动")
    active_dur = sum(t.get("duration_min", 0) for t in tasks if t.get("trigger") != "被动")
    total_dur = passive_dur + active_dur or 1

    passive_pct = passive_dur / total_dur * 100
    active_pct = active_dur / total_dur * 100

    # Breakdown text
    passive_cats = defaultdict(int)
    active_cats = defaultdict(int)
    for t in tasks:
        cat = t.get("category", "未分类")
        dur = t.get("duration_min", 0)
        if t.get("trigger") == "被动":
            passive_cats[cat] += dur
        else:
            active_cats[cat] += dur

    passive_parts = " + ".join(f"{c}{d}" for c, d in sorted(passive_cats.items(), key=lambda x: x[1], reverse=True))
    active_parts = " + ".join(f"{c}{d}" for c, d in sorted(active_cats.items(), key=lambda x: x[1], reverse=True))

    # Insight cards
    auto_details = analysis.get("automation_details", {})
    full_items = auto_details.get("full", [])
    path_hint = full_items[0].get("solution", "自动化减少被动响应") if full_items else "自动化减少被动响应"

    return f"""
<div class="section">
  <h2>S6 / 被动 vs 主动</h2>
  <div class="card">
    <div style="display:flex; justify-content:space-between; margin-bottom:8px; font-size:14px;">
      <span><strong style="color:#f85149;">被动响应</strong> {passive_dur}min ({passive_pct:.0f}%)</span>
      <span><strong style="color:#3fb950;">主动工作</strong> {active_dur}min ({active_pct:.0f}%)</span>
    </div>
    <div class="stacked-bar-container">
      <div class="stacked-bar-outer">
        <div class="stacked-bar-seg" style="width:{passive_pct:.1f}%; background: linear-gradient(90deg, #da3633, #f85149); color:#fff;">{passive_pct:.0f}% 被动</div>
        <div class="stacked-bar-seg" style="width:{active_pct:.1f}%; background: linear-gradient(90deg, #238636, #3fb950); color:#fff;">{active_pct:.0f}% 主动</div>
      </div>
    </div>
    <div style="font-size:12px; color:#8b949e; margin-top:8px;">
      被动 = 告警触发/故障/请求驱动（{escape_html(passive_parts) if passive_parts else 'N/A'}）<br>
      主动 = 自主规划（{escape_html(active_parts) if active_parts else 'N/A'}）
    </div>
    <div class="s6-insights">
      <div class="s6-insight" style="border-left: 3px solid #f85149;">
        <strong>{passive_pct:.0f}%</strong> 的时间花在被动响应上<br>被别人或告警驱动
      </div>
      <div class="s6-insight" style="border-left: 3px solid #ffa657;">
        目标：被动响应降到 <strong>30%</strong> 以下
      </div>
      <div class="s6-insight" style="border-left: 3px solid #3fb950;">
        路径：{escape_html(path_hint)} + Self-service 减少人工请求
      </div>
    </div>
  </div>
</div>"""


# ─────────────────────────────────────────────
# S7 / 重复 Toil 追踪器
# ─────────────────────────────────────────────

def build_s7(tasks):
    # Group by repeat_key
    key_map = defaultdict(list)
    for t in tasks:
        rk = t.get("repeat_key")
        if rk:
            key_map[rk].append(t)

    # Keep only cross-day repeats
    repeat_rows = []
    for rk, group in key_map.items():
        dates = set(t.get("date", "") for t in group)
        count = len(group)
        total_dur = sum(t.get("duration_min", 0) for t in group)
        num_days = len(dates)

        # Trend
        if num_days >= 3:
            trend_html = '<span class="trend-up">&#x25B2; 频繁</span>'
        elif num_days >= 2:
            trend_html = '<span class="trend-flat">&#x25BA; 稳定</span>'
        else:
            trend_html = '<span class="trend-flat">&#x25BA; 单日</span>'

        # Automation status (best in group)
        autos = [t.get("automation", "manual") for t in group]
        auto_priority = {"full": 0, "semi": 1, "manual": 2}
        best_auto = min(autos, key=lambda a: auto_priority.get(a, 2))

        if best_auto == "full":
            status_html = '<span style="color:#3fb950">&#x2713; 可自动化</span>'
        elif best_auto == "semi":
            status_html = '<span style="color:#ffa657">&#x25CB; 可半自动化</span>'
        else:
            status_html = '<span style="color:#f85149">&#x2716; 需人工</span>'

        if num_days >= 2:
            repeat_rows.append({
                "key": rk,
                "count": count,
                "days": num_days,
                "duration": total_dur,
                "trend_html": trend_html,
                "status_html": status_html,
            })

    repeat_rows.sort(key=lambda r: r["duration"], reverse=True)

    if not repeat_rows:
        return f"""
<div class="section">
  <h2>S7 / 重复 Toil 追踪器</h2>
  <div class="card"><p style="color:#8b949e;padding:8px 0">本周未发现跨天重复 Toil</p></div>
</div>"""

    rows_html = ""
    for r in repeat_rows:
        rows_html += f"""<tr>
  <td>{escape_html(r['key'])}</td>
  <td>{r['count']}次 (跨{r['days']}天)</td>
  <td>{fmt_min(r['duration'])}</td>
  <td>{r['trend_html']}</td>
  <td>{r['status_html']}</td>
</tr>\n"""

    return f"""
<div class="section">
  <h2>S7 / 重复 Toil 追踪器</h2>
  <div class="card" style="overflow-x:auto;">
    <table>
      <thead>
        <tr><th>重复模式</th><th>出现次数</th><th>累计耗时</th><th>趋势</th><th>自动化状态</th></tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
</div>"""


# ─────────────────────────────────────────────
# S8 / Toil 消除路线图
# ─────────────────────────────────────────────

def build_s8(analysis):
    roadmap = analysis.get("roadmap", {})
    if not roadmap:
        return f"""
<div class="section">
  <h2>S8 / Toil 消除路线图</h2>
  <div class="card"><p style="color:#8b949e;padding:8px 0">暂无路线图数据</p></div>
</div>"""

    completed = roadmap.get("completed", [])
    next_week = roadmap.get("next_week", [])
    future = roadmap.get("future", [])

    # Timeline
    timeline_html = '<div class="timeline">'

    if completed:
        items_html = "<br>".join(f"&#x2705; {escape_html(item)}" for item in completed)
        timeline_html += f"""
    <div class="tl-item">
      <div class="tl-dot done"></div>
      <div class="tl-week">已完成</div>
      <div class="tl-tasks">{items_html}</div>
    </div>"""

    if next_week:
        items_html = "<br>".join(f"&#x1F4CB; <span>{escape_html(item)}</span>" for item in next_week)
        timeline_html += f"""
    <div class="tl-item">
      <div class="tl-dot planned"></div>
      <div class="tl-week">下周计划</div>
      <div class="tl-tasks">{items_html}</div>
    </div>"""

    if future:
        items_html = "<br>".join(f"&#x1F4CB; <span>{escape_html(item)}</span>" for item in future)
        timeline_html += f"""
    <div class="tl-item">
      <div class="tl-dot planned"></div>
      <div class="tl-week">未来规划</div>
      <div class="tl-tasks">{items_html}</div>
    </div>"""

    timeline_html += "\n  </div>"

    # Prediction chart
    prediction = roadmap.get("prediction", {})
    prediction_html = ""
    if prediction and prediction.get("weeks"):
        weeks = prediction["weeks"]
        no_auto = prediction.get("no_automation", [])
        with_auto = prediction.get("with_automation", [])
        current_hours = prediction.get("current_hours", 0)

        weeks_js = safe_js(weeks)
        no_auto_js = safe_js(no_auto)
        with_auto_js = safe_js(with_auto)

        all_values = no_auto + with_auto
        y_min = max(0, int(min(all_values) - 3)) if all_values else 0
        y_max = int(max(all_values) + 3) if all_values else 30

        prediction_html = f"""
  <div class="card" style="margin-top:16px;">
    <h3>Toil 趋势预测</h3>
    <canvas id="predictionChart" height="200"></canvas>
  </div>
  <script>
  (function() {{
    const pCtx = document.getElementById('predictionChart').getContext('2d');
    new Chart(pCtx, {{
      type: 'line',
      data: {{
        labels: {weeks_js},
        datasets: [
          {{
            label: '不自动化（随业务增长）',
            data: {no_auto_js},
            borderColor: '#f85149',
            backgroundColor: 'rgba(248,81,73,0.1)',
            borderDash: [6, 4],
            fill: false,
            tension: 0.3,
            pointRadius: 4,
          }},
          {{
            label: '按计划自动化',
            data: {with_auto_js},
            borderColor: '#3fb950',
            backgroundColor: 'rgba(63,185,80,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 5,
            pointBackgroundColor: '#3fb950',
          }}
        ]
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
          y: {{
            ticks: {{ color: '#8b949e', callback: v => v + 'h' }},
            grid: {{ color: '#21262d' }},
            min: {y_min}, max: {y_max}
          }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#8b949e', usePointStyle: true }} }},
          datalabels: {{
            color: '#e6edf3',
            font: {{ size: 11 }},
            align: function(ctx) {{ return ctx.datasetIndex === 0 ? 'top' : 'bottom'; }},
            formatter: (value) => value + 'h'
          }}
        }}
      }}
    }});
  }})();
  </script>"""

    return f"""
<div class="section">
  <h2>S8 / Toil 消除路线图</h2>
  <div class="card">
    {timeline_html}
  </div>
  {prediction_html}
</div>"""


# ─────────────────────────────────────────────
# HTML Assembly
# ─────────────────────────────────────────────

def build_html(raw_data, tasks, analysis, week_label):
    s1 = build_s1(tasks, analysis)
    s2 = build_s2(tasks, analysis)
    s3 = build_s3(tasks, analysis)
    s4 = build_s4(tasks, analysis)
    s5 = build_s5(tasks)
    s6 = build_s6(tasks, analysis)
    s7 = build_s7(tasks)
    s8 = build_s8(analysis)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SRE Toil 分析周报 | {escape_html(week_label)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
{CSS}
</style>
</head>
<body>
<div class="container">

<header style="text-align:center; margin-bottom: 40px;">
  <h1>SRE Toil 分析周报 | {escape_html(week_label)}</h1>
  <div class="subtitle">日报 = 人工介入记录 | 目标：让日报越来越短</div>
</header>

{s1}
{s2}
{s3}
{s4}
{s5}
{s6}
{s7}
{s8}

<footer>
  Generated by Claude Code | 方法论：Google SRE Chapter 5 - Eliminating Toil
</footer>

</div>
</body>
</html>"""


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("用法: python3 render_weekly.py <raw_json> <enriched_json> [analysis_json]", file=sys.stderr)
        sys.exit(1)

    raw_path = sys.argv[1]
    enriched_path = sys.argv[2]
    analysis_path = sys.argv[3] if len(sys.argv) > 3 else None

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
    if analysis_path:
        try:
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)
        except Exception as e:
            print(f"Warning: could not read analysis JSON: {e}", file=sys.stderr)
            analysis = {}

    tasks = merge_tasks(raw_data, enriched_list)
    week_label, date_start, date_end = parse_week_range(raw_data, tasks)

    html = build_html(raw_data, tasks, analysis, week_label)
    sys.stdout.write(html)


if __name__ == "__main__":
    main()
