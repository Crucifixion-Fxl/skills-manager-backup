#!/usr/bin/env python3
"""
Render ArgoCD fleet scan results to a self-contained HTML report.

Input  (argv[1]): path to classified.ndjson (one JSON record per line)
Input  (argv[2]): path to output .html file
Optional (argv[3]): comma-separated list of cluster names that were unreachable
"""
import json
import sys
import datetime
import html
import os

CLUSTER_ARGOCD = {
    "us-prod":              "argocd-us.addx.live",
    "eu-prod":              "argocd-eu.addx.live",
    "cn-prod":              "argocd-cn.addx.live",
    "us-tech-service":      "argocd-us-tech-service.addx.live",
    "eu-tech-service":      "argocd-eu-tech-service.addx.live",
    "cn-tech-service":      "argocd-cn-tech-service.addx.live",
    "us-data":              "argocd-us-data.addx.live",
    "eu-data":              "argocd-eu-data.addx.live",
    "cn-main":              "argocd-cn-k8s.addx.live",
    "us-staging":           "argocd-us-staging.addx.live",
    "eu-staging":           "argocd-eu-staging.addx.live",
    "cn-staging":           "argocd-cn-staging.addx.live",
    "cn-dev":               "argocd-cn-dev.addx.live",
    "sg-devops":            "argocd-sg-devops.addx.live",
    "us-prod-gke":          "argocd-us-prod-gke.addx.live",
    "us-tech-service-gke":  "argocd-us-tech-service-gke.addx.live",
}

SEV_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
EMPTY_STATS = {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "total": 0}
LINK_REL = "noopener noreferrer"


# ─── pure helpers ──────────────────────────────────────────────────────────

def fmt_age(secs):
    if secs is None:
        return "—"
    # 时钟偏移 / sync 刚完成几毫秒前 → 负数, 当成 0s ("just now") 显示, 不要露 "—"
    if secs <= 0:
        return "0s"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def app_url(cluster, app):
    host = CLUSTER_ARGOCD.get(cluster)
    return f"https://{host}/applications/argo-cd/{app}?view=tree" if host else None


def cluster_url(cluster):
    host = CLUSTER_ARGOCD.get(cluster)
    return f"https://{host}/applications" if host else None


def esc(value):
    return html.escape(str(value)) if value is not None else ""


def sev_badge(severity):
    return f'<span class="badge sev-{severity.lower()}">{severity}</span>'


def cat_badge(category):
    return f'<span class="badge cat-{category}">{category}</span>'


def anomaly_chips(anomaly):
    return "".join(
        f'<span class="chip anom-{esc(a.split(":")[0])}">{esc(a)}</span>'
        for a in (anomaly or [])
    )


def _link(href, text, css_class=""):
    if not href:
        return esc(text)
    cls = f' class="{css_class}"' if css_class else ""
    return f'<a href="{esc(href)}" target="_blank" rel="{LINK_REL}"{cls}>{esc(text)}</a>'


# ─── data loading + summary ────────────────────────────────────────────────

def load_rows(ndjson_path):
    rows = []
    with open(ndjson_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    rows.sort(key=_row_sort_key)
    return rows


def _row_sort_key(row):
    return (
        SEV_ORDER.get(row.get("severity", "P2"), 99),
        row.get("cluster", ""),
        0 if row.get("category") == "infra" else 1,
        row.get("name", ""),
    )


def compute_summary(rows):
    counts = {
        "total": len(rows),
        "P0": sum(1 for r in rows if r.get("severity") == "P0"),
        "P1": sum(1 for r in rows if r.get("severity") == "P1"),
        "P2": sum(1 for r in rows if r.get("severity") == "P2"),
        "P3": sum(1 for r in rows if r.get("severity") == "P3"),
        "infra": sum(1 for r in rows if r.get("category") == "infra"),
        "app": sum(1 for r in rows if r.get("category") == "app"),
    }
    counts["active"] = counts["P0"] + counts["P1"] + counts["P2"]
    clusters_seen = sorted({r["cluster"] for r in rows})
    cluster_stats = {}
    for r in rows:
        bucket = cluster_stats.setdefault(r["cluster"], dict(EMPTY_STATS))
        sev = r.get("severity", "P2")
        bucket[sev] = bucket.get(sev, 0) + 1
        bucket["total"] += 1
    return counts, clusters_seen, cluster_stats


# ─── HTML rendering ────────────────────────────────────────────────────────

def policy_flag_chips(row):
    """Tiny grey pills showing syncPolicy state — context for OutOfSync interpretation."""
    out = []
    if row.get("automated"):
        out.append('<span class="flag flag-auto" title="syncPolicy.automated 已启用">auto</span>')
    else:
        out.append('<span class="flag flag-manual" title="无 automated syncPolicy，需要手动 sync">manual</span>')
    if row.get("selfHeal"):
        out.append('<span class="flag flag-heal" title="selfHeal=true，应当自动收敛 drift">selfHeal</span>')
    return "".join(out)


def _primary_age(row):
    """Pick the most meaningful age for this anomaly type."""
    anomaly = row.get("anomaly") or []
    health_age = row.get("healthAge") or 0
    sync_age = row.get("syncAge") or 0
    # Health-related anomaly: show healthAge; sync-only: show syncAge
    if any(a in ("Degraded", "Missing", "HealthUnknown", "LongProgressing") for a in anomaly):
        return health_age
    if any(a in ("OutOfSync", "SyncUnknown", "SyncFailed", "SelfHealStuck") or a.startswith("Condition:") for a in anomaly):
        return sync_age if (sync_age is not None and sync_age > 0) else health_age
    return health_age


def render_table_row(row):
    cluster = row.get("cluster", "")
    name = row.get("name", "")
    category = row.get("category", "app")
    severity = row.get("severity", "P2")
    sync = row.get("sync", "")
    health = row.get("health", "")
    op_msg = row.get("op_msg") or ""
    op_msg_short = op_msg if len(op_msg) <= 80 else op_msg[:77] + "…"

    app_html = _link(app_url(cluster, name), name, "app-link")
    clu_html = _link(cluster_url(cluster), cluster, "clu-link")

    return (
        f'<tr data-severity="{severity}" data-category="{category}" data-cluster="{esc(cluster)}">'
        f'<td>{sev_badge(severity)}</td>'
        f'<td>{clu_html}</td>'
        f'<td class="app-cell">{app_html}<span class="ns-hint">{esc(row.get("ns", ""))}</span></td>'
        f'<td>{cat_badge(category)}</td>'
        f'<td class="anomaly-cell">{anomaly_chips(row.get("anomaly", []))}</td>'
        f'<td class="flag-cell">{policy_flag_chips(row)}</td>'
        f'<td class="status-cell">'
        f'<span class="status-sync sync-{esc(sync)}">{esc(sync)}</span> · '
        f'<span class="status-health health-{esc(health)}">{esc(health)}</span></td>'
        f'<td class="age-cell">{esc(fmt_age(_primary_age(row)))}</td>'
        f'<td class="msg-cell" title="{esc(op_msg)}">{esc(op_msg_short)}</td>'
        f'<td class="rev-cell"><code>{esc(row.get("revision", ""))}</code></td>'
        f'</tr>'
    )


def render_summary_row(cluster, cluster_stats, unreachable_set):
    stats = cluster_stats.get(cluster, EMPTY_STATS)
    unreach = cluster in unreachable_set
    link_html = _link(cluster_url(cluster), cluster)
    unreach_html = ' <span class="unreach-pill">unreachable</span>' if unreach else ''
    total_cell = stats["total"] or ('—' if unreach else 0)
    return (
        f'<tr>'
        f'<td>{link_html}{unreach_html}</td>'
        f'<td>{stats["P0"] or ""}</td>'
        f'<td>{stats["P1"] or ""}</td>'
        f'<td>{stats["P2"] or ""}</td>'
        f'<td class="p3-col">{stats["P3"] or ""}</td>'
        f'<td>{total_cell}</td>'
        f'</tr>'
    )


def render_unreach_banner(unreachable):
    if not unreachable:
        return ""
    names = ", ".join(esc(c) for c in unreachable)
    return (
        f'<div class="banner warn">'
        f'<strong>不可达集群 ({len(unreachable)}):</strong> {names}'
        f'<span class="banner-hint">— 这些集群没扫，对应行不在统计中</span>'
        f'</div>'
    )


def render_cluster_filter_options(clusters_seen):
    return "\n".join(
        f'<option value="{esc(c)}">{esc(c)}</option>' for c in clusters_seen
    )


# ─── style + script (static) ───────────────────────────────────────────────

STYLE = """
:root {
  --bg: #f8fafc;
  --panel: #ffffff;
  --border: #e2e8f0;
  --text: #0f172a;
  --muted: #64748b;
  --p0: #dc2626;
  --p0-bg: #fef2f2;
  --p1: #d97706;
  --p1-bg: #fffbeb;
  --p2: #475569;
  --p2-bg: #f1f5f9;
  --p3: #94a3b8;
  --p3-bg: #f8fafc;
  --infra: #4338ca;
  --infra-bg: #eef2ff;
  --app: #047857;
  --app-bg: #ecfdf5;
  --link: #2563eb;
  --warn: #b45309;
  --warn-bg: #fef3c7;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--text);
  font-size: 13px; line-height: 1.5;
}
.container { max-width: 1500px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 4px 0; font-weight: 600; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
.stats {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 16px;
}
.stat {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;
}
.stat .label { color: var(--muted); font-size: 12px; }
.stat .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
.stat.p0 .value { color: var(--p0); }
.stat.p1 .value { color: var(--p1); }
.stat.p2 .value { color: var(--p2); }
.stat.p3 .value { color: var(--p3); }
.banner {
  padding: 10px 14px; border-radius: 6px; margin-bottom: 16px;
  background: var(--warn-bg); color: var(--warn); border: 1px solid #fde68a;
}
.banner-hint { color: var(--muted); font-weight: 400; }
.controls {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 12px;
}
.controls label { color: var(--muted); font-size: 12px; }
.controls select, .controls input {
  padding: 4px 8px; border: 1px solid var(--border); border-radius: 4px;
  background: var(--bg); color: var(--text); font: inherit;
}
.controls button {
  padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px;
  background: var(--bg); color: var(--text); cursor: pointer; font: inherit;
}
.controls button.active { background: var(--text); color: white; border-color: var(--text); }
table {
  width: 100%; border-collapse: collapse;
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden;
}
thead {
  background: #f8fafc; border-bottom: 1px solid var(--border);
}
th {
  text-align: left; padding: 10px 12px; font-weight: 600;
  color: var(--muted); font-size: 12px;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top;
}
tr:last-child td { border-bottom: none; }
tr.hidden { display: none; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.02em;
}
.sev-p0 { background: var(--p0-bg); color: var(--p0); border: 1px solid #fecaca; }
.sev-p1 { background: var(--p1-bg); color: var(--p1); border: 1px solid #fed7aa; }
.sev-p2 { background: var(--p2-bg); color: var(--p2); border: 1px solid #cbd5e1; }
.sev-p3 { background: var(--p3-bg); color: var(--p3); border: 1px dashed #cbd5e1; }
.cat-infra { background: var(--infra-bg); color: var(--infra); border: 1px solid #c7d2fe; }
.cat-app { background: var(--app-bg); color: var(--app); border: 1px solid #a7f3d0; }
.chip {
  display: inline-block; padding: 2px 6px; border-radius: 3px; margin-right: 3px;
  background: #f1f5f9; color: var(--text); font-size: 11px; border: 1px solid var(--border);
  white-space: nowrap;
}
.anom-Degraded, .anom-Missing, .anom-HealthUnknown { background: #fee2e2; color: #991b1b; border-color: #fecaca; }
.anom-SyncFailed { background: #fef3c7; color: #92400e; border-color: #fde68a; }
.anom-SyncUnknown, .anom-Condition { background: #f3e8ff; color: #6b21a8; border-color: #e9d5ff; }
.anom-OutOfSync { background: #dbeafe; color: #1e40af; border-color: #bfdbfe; }
.anom-LongProgressing { background: #fef3c7; color: #92400e; border-color: #fde68a; }
.anom-SelfHealStuck { background: #fee2e2; color: #991b1b; border-color: #fecaca; font-weight: 600; }
.flag {
  display: inline-block; padding: 1px 5px; border-radius: 3px; margin-right: 3px;
  font-size: 10px; letter-spacing: 0.02em;
}
.flag-auto   { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.flag-manual { background: #f1f5f9; color: var(--muted); border: 1px solid var(--border); }
.flag-heal   { background: #eef2ff; color: #4338ca; border: 1px solid #c7d2fe; }
.flag-cell { white-space: nowrap; }
.app-link { color: var(--link); text-decoration: none; font-weight: 500; }
.app-link:hover { text-decoration: underline; }
.clu-link { color: var(--text); text-decoration: none; font-weight: 500; }
.clu-link:hover { color: var(--link); text-decoration: underline; }
.app-cell { min-width: 200px; }
.ns-hint { color: var(--muted); font-size: 11px; margin-left: 6px; }
.status-cell { color: var(--muted); font-size: 12px; white-space: nowrap; }
.sync-Synced { color: #16a34a; font-weight: 500; }
.sync-OutOfSync { color: #2563eb; font-weight: 500; }
.sync-Unknown { color: #9333ea; font-weight: 500; }
.health-Healthy { color: #16a34a; }
.health-Degraded { color: var(--p0); font-weight: 600; }
.health-Missing { color: var(--p0); }
.health-Progressing { color: var(--p1); }
.health-Unknown { color: #9333ea; }
.age-cell { color: var(--muted); white-space: nowrap; }
.msg-cell {
  color: var(--muted); font-size: 12px; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rev-cell code {
  font-family: ui-monospace, Menlo, Monaco, "Cascadia Mono", monospace;
  font-size: 11px; color: var(--muted);
}
.unreach-pill {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  background: var(--warn-bg); color: var(--warn);
  font-size: 10px; margin-left: 4px;
}
h2 { font-size: 16px; margin: 24px 0 10px 0; font-weight: 600; }
.footnote { color: var(--muted); font-size: 11px; margin-top: 24px; }
.footnote code {
  background: #f1f5f9; padding: 1px 4px; border-radius: 3px;
  font-family: ui-monospace, Menlo, Monaco, monospace;
}
"""

SCRIPT = """
const ACTIVE_SEVS = ['P0', 'P1', 'P2'];
function applyFilters() {
  const sevFilter = document.querySelector('.sev-filter.active').dataset.sev;
  const catFilter = document.getElementById('cat-filter').value;
  const cluFilter = document.getElementById('clu-filter').value;
  const txtFilter = document.getElementById('txt-filter').value.toLowerCase();
  let visible = 0;
  document.querySelectorAll('tbody.main-tbody tr').forEach(tr => {
    let show = true;
    const sev = tr.dataset.severity;
    if (sevFilter === 'ACTIVE')      show = ACTIVE_SEVS.includes(sev);
    else if (sevFilter !== 'ALL')    show = sev === sevFilter;
    if (catFilter !== 'ALL' && tr.dataset.category !== catFilter) show = false;
    if (cluFilter !== 'ALL' && tr.dataset.cluster !== cluFilter) show = false;
    if (txtFilter && !tr.textContent.toLowerCase().includes(txtFilter)) show = false;
    tr.classList.toggle('hidden', !show);
    if (show) visible++;
  });
  document.getElementById('visible-count').textContent = visible;
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.sev-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sev-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilters();
    });
  });
  ['cat-filter', 'clu-filter'].forEach(id => document.getElementById(id).addEventListener('change', applyFilters));
  document.getElementById('txt-filter').addEventListener('input', applyFilters);
});
"""


def render_html(rows, unreachable):
    counts, clusters_seen, cluster_stats = compute_summary(rows)
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    unreach_set = set(unreachable)

    table_html = "".join(render_table_row(r) for r in rows)
    summary_html = "".join(
        render_summary_row(c, cluster_stats, unreach_set)
        for c in sorted(CLUSTER_ARGOCD.keys())
    )
    filter_options_html = render_cluster_filter_options(clusters_seen)
    banner_html = render_unreach_banner(unreachable)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>ArgoCD Fleet Scan — {now_utc}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="container">
  <h1>ArgoCD Fleet Scan</h1>
  <div class="subtitle">{now_utc} · 集群: {len(clusters_seen)} 扫通 / {len(unreachable)} 不可达 · 长 Progressing 阈值: 15min</div>

  {banner_html}

  <div class="stats">
    <div class="stat"><div class="label">Active 异常 (P0+P1+P2)</div><div class="value">{counts['active']}</div></div>
    <div class="stat p0"><div class="label">P0 基础设施真异常</div><div class="value">{counts['P0']}</div></div>
    <div class="stat p1"><div class="label">P1 业务异常</div><div class="value">{counts['P1']}</div></div>
    <div class="stat p2"><div class="label">P2 Stale Drift</div><div class="value">{counts['P2']}</div></div>
    <div class="stat p3"><div class="label">P3 等待自愈 (默认折叠)</div><div class="value">{counts['P3']}</div></div>
  </div>

  <div class="controls">
    <label>严重度:</label>
    <button class="sev-filter active" data-sev="ACTIVE">Active ({counts['active']})</button>
    <button class="sev-filter" data-sev="ALL">All ({counts['total']})</button>
    <button class="sev-filter" data-sev="P0">P0 ({counts['P0']})</button>
    <button class="sev-filter" data-sev="P1">P1 ({counts['P1']})</button>
    <button class="sev-filter" data-sev="P2">P2 ({counts['P2']})</button>
    <button class="sev-filter" data-sev="P3">P3 ({counts['P3']})</button>
    <span style="flex:1"></span>
    <label>类别:</label>
    <select id="cat-filter">
      <option value="ALL">全部</option>
      <option value="infra">infra</option>
      <option value="app">app</option>
    </select>
    <label>集群:</label>
    <select id="clu-filter">
      <option value="ALL">全部</option>
      {filter_options_html}
    </select>
    <input id="txt-filter" type="search" placeholder="搜索 app / 信息..." style="min-width: 200px;">
    <span style="color: var(--muted); font-size: 12px;">显示 <span id="visible-count">{counts['active']}</span> / {counts['total']}</span>
  </div>

  <table>
    <thead>
      <tr>
        <th>Sev</th>
        <th>Cluster</th>
        <th>App</th>
        <th>Category</th>
        <th>Anomaly</th>
        <th>Policy</th>
        <th>Sync · Health</th>
        <th>Age</th>
        <th>Last Op Msg</th>
        <th>Rev</th>
      </tr>
    </thead>
    <tbody class="main-tbody">
      {table_html}
    </tbody>
  </table>

  <h2>集群汇总</h2>
  <table>
    <thead>
      <tr>
        <th>Cluster</th>
        <th>P0</th>
        <th>P1</th>
        <th>P2</th>
        <th class="p3-col">P3</th>
        <th>Total</th>
      </tr>
    </thead>
    <tbody>
      {summary_html}
    </tbody>
  </table>

  <div class="footnote">
    点击 <strong>App</strong> 跳转 ArgoCD UI 应用页（resource tree）；点击 <strong>Cluster</strong> 跳到该集群 applications 列表。<br>
    <strong>严重度</strong>：
    <code>infra + 真异常 → P0</code> ·
    <code>app + 真异常 / infra OutOfSync / SelfHealStuck → P1</code> ·
    <code>OutOfSync + selfHeal=false → P2 stale drift</code> ·
    <code>OutOfSync + selfHeal=true 且 history 无重复 → P3 等待自愈 (默认折叠)</code>。<br>
    <strong>Anomaly chip</strong>：
    <code>Degraded</code> 运行异常 ·
    <code>SyncFailed</code> 上次 sync 失败 ·
    <code>SyncUnknown / Condition:ComparisonError</code> 仓库/revision 拉不到 ·
    <code>OutOfSync</code> Git 有未部署变更 ·
    <code>LongProgressing</code> 卡 Progressing 超阈值 ·
    <code class="anom-SelfHealStuck" style="padding:1px 4px">SelfHealStuck</code> 同 revision 在 status.history 出现 ≥ 2 次但仍 OutOfSync = selfHeal 反复 sync 收敛不了。<br>
    <strong>Policy flag</strong>：
    <code class="flag flag-auto">auto</code> automated syncPolicy 启用 ·
    <code class="flag flag-manual">manual</code> 需手动 sync ·
    <code class="flag flag-heal">selfHeal</code> selfHeal=true 应自动修 drift。
  </div>
</div>
<script>{SCRIPT}</script>
</body>
</html>
"""


# ─── CLI orchestration ─────────────────────────────────────────────────────

def parse_unreachable_arg(argv):
    if len(argv) < 4 or not argv[3]:
        return []
    return [s.strip() for s in argv[3].split(",") if s.strip()]


def main():
    if len(sys.argv) < 3:
        print(
            f"usage: {sys.argv[0]} <classified.ndjson> <output.html> [unreachable_csv]",
            file=sys.stderr,
        )
        sys.exit(2)

    ndjson_path = sys.argv[1]
    out_path = sys.argv[2]
    unreachable = parse_unreachable_arg(sys.argv)

    rows = load_rows(ndjson_path)
    html_out = render_html(rows, unreachable)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    counts, _, _ = compute_summary(rows)
    print(f"Report written: {out_path}")
    print(f"  total={counts['total']}  active={counts['active']}  "
          f"P0={counts['P0']} P1={counts['P1']} P2={counts['P2']} P3={counts['P3']}")
    print(f"  open: file://{os.path.abspath(out_path)}")


if __name__ == "__main__":
    main()
