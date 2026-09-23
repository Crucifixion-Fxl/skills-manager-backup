#!/usr/bin/env python3
"""Rebuild data.json + index.html from reports/<author>/<week>.html tree.

Usage: build_index.py <repo_root>
- Scans <repo_root>/reports/<author>/*.html
- Writes <repo_root>/data.json (full rewrite)
- Writes <repo_root>/index.html (full rewrite, inline CSS+JS, no framework)
"""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})\.html$")
# Defense-in-depth: even if publish.sh's author validation were bypassed (e.g. someone
# hand-edits reports/ on disk), we still refuse to index entries whose author slug or
# week number falls outside the documented constraints. Skip silently rather than
# hard-aborting the whole rebuild — a single bad directory shouldn't break the index.
AUTHOR_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
YEAR_MIN, YEAR_MAX = 2020, 2099
WEEK_MIN, WEEK_MAX = 1, 53


def load_author_names(path: Path) -> dict[str, str]:
    """Load the optional author-slug -> display-name map."""
    if path.is_symlink():
        print("skip: authors.json must not be a symlink", file=sys.stderr)
        return {}
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"skip: invalid authors.json ({exc})", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("skip: authors.json must be a JSON object", file=sys.stderr)
        return {}
    names = {}
    for author, display_name in raw.items():
        if AUTHOR_RE.match(author) and isinstance(display_name, str):
            display_name = display_name.strip()
            if display_name and len(display_name) <= 80:
                names[author] = display_name
    return names


def load_author_aliases(path: Path) -> dict[str, str]:
    """Load and flatten the optional alias-slug -> canonical-slug map."""
    if path.is_symlink():
        print("skip: author_aliases.json must not be a symlink", file=sys.stderr)
        return {}
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"skip: invalid author_aliases.json ({exc})", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("skip: author_aliases.json must be a JSON object", file=sys.stderr)
        return {}

    candidates = {
        alias: canonical
        for alias, canonical in raw.items()
        if isinstance(alias, str)
        and isinstance(canonical, str)
        and AUTHOR_RE.match(alias)
        and AUTHOR_RE.match(canonical)
        and alias != canonical
    }
    aliases = {}
    for alias in candidates:
        seen = {alias}
        canonical = candidates[alias]
        while canonical in candidates:
            if canonical in seen:
                print(f"skip: cyclic author alias '{alias}'", file=sys.stderr)
                canonical = ""
                break
            seen.add(canonical)
            canonical = candidates[canonical]
        if canonical:
            aliases[alias] = canonical
    return aliases


def apply_author_aliases(entries: list[dict], aliases: dict[str, str]) -> list[dict]:
    """Attach a canonical identity while preserving the original report path/author."""
    return [
        {**entry, "identity": aliases.get(entry["author"], entry["author"])}
        for entry in entries
    ]


def scan_reports(reports_dir: Path) -> list[dict]:
    entries = []
    if not reports_dir.is_dir():
        return entries
    for author_dir in sorted(p for p in reports_dir.iterdir() if p.is_dir()):
        author = author_dir.name
        if not AUTHOR_RE.match(author):
            print(f"skip: invalid author slug '{author}'", file=sys.stderr)
            continue
        for html in sorted(author_dir.glob("*.html")):
            m = WEEK_RE.match(html.name)
            if not m:
                continue
            year, week = int(m.group(1)), int(m.group(2))
            if not (YEAR_MIN <= year <= YEAR_MAX) or not (WEEK_MIN <= week <= WEEK_MAX):
                print(f"skip: out-of-range week {author}/{html.name}", file=sys.stderr)
                continue
            stat = html.stat()
            entries.append({
                "author": author,
                "week": f"{year}-W{week:02d}",
                "year": year,
                "week_num": week,
                "path": f"reports/{author}/{html.name}",
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return entries


def current_index_week(entries: list[dict], today: Optional[date] = None) -> str:
    y, w, _ = (today or date.today()).isocalendar()
    current = f"{y}-W{w:02d}"
    if any(entry["week"] == current for entry in entries):
        return current
    # A Monday rebuild commonly happens before anyone has submitted the new week.
    # Keep the landing page on the latest available report week instead of showing
    # an empty "本周" menu.
    return max((entry["week"] for entry in entries), default=current)


def render_index(
    entries: list[dict],
    current_week: str,
    authors: dict[str, str],
    author_aliases: Optional[dict[str, str]] = None,
) -> str:
    # Escape every "<" so no case variant of </script can terminate the raw-text element.
    payload = (
        json.dumps(
            {
                "entries": entries,
                "current_week": current_week,
                "authors": authors,
                "author_aliases": author_aliases or {},
            },
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
    )
    # Static HTML, inline CSS + JS, no framework. Reads embedded payload.
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Weekly Reports · weekly-reports/software</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; color: #1f2328; background: #f6f8fa; }}
  header {{ display: flex; align-items: center; gap: 16px; padding: 12px 20px; background: #fff; border-bottom: 1px solid #d0d7de; position: sticky; top: 0; z-index: 10; }}
  header h1 {{ margin: 0; font-size: 16px; font-weight: 600; }}
  header .week {{ color: #656d76; font-size: 13px; }}
  nav {{ margin-left: auto; display: flex; gap: 4px; }}
  nav button {{ padding: 6px 14px; border: 1px solid #d0d7de; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; color: #1f2328; }}
  nav button.active {{ background: #0969da; color: #fff; border-color: #0969da; }}
  main {{ display: grid; grid-template-columns: 240px 1fr; height: calc(100vh - 49px); }}
  aside {{ background: #fff; border-right: 1px solid #d0d7de; overflow-y: auto; padding: 12px 0; }}
  aside .group {{ padding: 4px 12px; font-size: 11px; text-transform: uppercase; color: #656d76; letter-spacing: .5px; margin-top: 8px; }}
  aside ul {{ list-style: none; margin: 0; padding: 0; }}
  aside li {{ padding: 6px 16px; cursor: pointer; font-size: 13px; user-select: none; border-left: 3px solid transparent; }}
  aside li:hover {{ background: #f6f8fa; }}
  aside li.active {{ background: #ddf4ff; border-left-color: #0969da; color: #0969da; font-weight: 600; }}
  aside li.child {{ padding-left: 28px; font-size: 12px; }}
  aside li.author-header {{ font-weight: 600; padding: 8px 16px 4px; cursor: default; color: #1f2328; }}
  aside li.author-header:hover {{ background: transparent; }}
  aside .username {{ color: #8c959f; font-size: 11px; font-weight: 400; margin-left: 4px; }}
  aside .meta {{ color: #656d76; font-size: 11px; margin-left: 6px; }}
  section {{ overflow-y: auto; padding: 16px; }}
  section .empty {{ color: #656d76; padding: 40px; text-align: center; }}
  iframe {{ width: 100%; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; min-height: 1200px; }}
  footer {{ padding: 8px 16px; font-size: 11px; color: #656d76; text-align: right; border-top: 1px solid #d0d7de; background: #fff; }}
</style>
</head>
<body>
<header>
  <h1>Weekly Reports</h1>
  <span class="week" id="hdr-week"></span>
  <nav>
    <button id="nav-weekly" class="active" data-tab="weekly">本周</button>
    <button id="nav-history" data-tab="history">历史</button>
  </nav>
</header>
<main>
  <aside id="menu"></aside>
  <section id="content"><div class="empty">从左侧选择一份周报</div></section>
</main>
<footer id="footer"></footer>
<script id="payload" type="application/json">{payload}</script>
<script>
(function(){{
  const TAB_WEEKLY = 'weekly';
  const TAB_HISTORY = 'history';
  const data = JSON.parse(document.getElementById('payload').textContent);
  const entries = data.entries || [];
  const authorNames = data.authors || {{}};
  const currentWeek = data.current_week;
  document.getElementById('hdr-week').textContent = '本周 = ' + currentWeek;
  document.getElementById('footer').textContent = '共 ' + entries.length + ' 份报告 · 生成于 ' + new Date().toISOString();

  const state = {{ author: null, week: null, sourceAuthor: null }};

  function getTab() {{
    return location.hash.startsWith('#/' + TAB_HISTORY + '/') ? TAB_HISTORY : TAB_WEEKLY;
  }}

  function fmtSize(b) {{ return b < 1024 ? b + ' B' : (b/1024).toFixed(1) + ' KB'; }}

  function authorLabel(author) {{
    return authorNames[author] || author;
  }}

  function appendAuthorLabel(node, author) {{
    node.appendChild(document.createTextNode(authorLabel(author)));
    if (authorNames[author]) {{
      const username = document.createElement('span');
      username.className = 'username';
      username.textContent = '· ' + author;
      node.appendChild(username);
    }}
  }}

  function authorsThisWeek() {{
    return entries.filter(e => e.week === currentWeek)
      .map(e => e.identity || e.author)
      .filter((v, i, a) => a.indexOf(v) === i)
      .sort((a, b) => authorLabel(a).localeCompare(authorLabel(b), 'zh-CN'));
  }}

  function entriesByAuthor() {{
    const by = {{}};
    for (const e of entries) {{
      const identity = e.identity || e.author;
      (by[identity] = by[identity] || []).push(e);
    }}
    for (const k in by) by[k].sort((a, b) => b.week.localeCompare(a.week));
    return by;
  }}

  function findEntry(author, week, sourceAuthor) {{
    const matches = entries.filter(e => (e.identity || e.author) === author && e.week === week);
    if (sourceAuthor) return matches.find(e => e.author === sourceAuthor);
    return matches.find(e => e.author === author) || matches[0];
  }}

  function makeAuthorLi(author, week, label, sizeBytes, sourceAuthor) {{
    const li = document.createElement('li');
    if (label === author) appendAuthorLabel(li, author);
    else li.appendChild(document.createTextNode(label));
    if (sizeBytes != null) {{
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = ' ' + fmtSize(sizeBytes);
      li.appendChild(meta);
    }}
    if (state.author === author && state.week === week && state.sourceAuthor === (sourceAuthor || null)) li.classList.add('active');
    li.onclick = () => select(author, week, sourceAuthor);
    return li;
  }}

  function renderMenu() {{
    const menu = document.getElementById('menu');
    menu.innerHTML = '';
    if (getTab() === TAB_WEEKLY) {{
      const authors = authorsThisWeek();
      const grp = document.createElement('div');
      grp.className = 'group';
      grp.textContent = '本周 ' + currentWeek + ' (' + authors.length + ' 人)';
      menu.appendChild(grp);
      const ul = document.createElement('ul');
      if (authors.length === 0) {{
        const li = document.createElement('li');
        li.className = 'author-header';
        li.textContent = '尚无报告';
        ul.appendChild(li);
      }} else {{
        for (const a of authors) ul.appendChild(makeAuthorLi(a, currentWeek, a));
      }}
      menu.appendChild(ul);
    }} else {{
      const by = entriesByAuthor();
      for (const a of Object.keys(by).sort((x, y) => authorLabel(x).localeCompare(authorLabel(y), 'zh-CN'))) {{
        const ul = document.createElement('ul');
        const hdr = document.createElement('li');
        hdr.className = 'author-header';
        appendAuthorLabel(hdr, a);
        ul.appendChild(hdr);
        for (const e of by[a]) {{
          const duplicateWeek = by[a].filter(candidate => candidate.week === e.week).length > 1;
          const label = duplicateWeek ? e.week + ' · ' + e.author : e.week;
          const li = makeAuthorLi(a, e.week, label, e.size_bytes, e.author);
          li.classList.add('child');
          ul.appendChild(li);
        }}
        menu.appendChild(ul);
      }}
    }}
  }}

  function renderContent() {{
    const sec = document.getElementById('content');
    if (!state.author || !state.week) {{
      sec.innerHTML = '<div class="empty">从左侧选择一份周报</div>';
      return;
    }}
    const e = findEntry(state.author, state.week, state.sourceAuthor);
    if (!e) {{
      sec.innerHTML = '<div class="empty">未找到 ' + state.author + ' / ' + state.week + '</div>';
      return;
    }}
    if (!/^reports\\/[a-z0-9-]+\\/\\d{{4}}-W\\d{{2}}\\.html$/.test(e.path)) {{
      sec.innerHTML = '<div class="empty">非法路径</div>';
      return;
    }}
    sec.innerHTML = '';
    const f = document.createElement('iframe');
    f.src = e.path;
    const sourceSuffix = e.author !== state.author ? ' (' + e.author + ')' : '';
    f.title = authorLabel(state.author) + (authorNames[state.author] ? ' · ' + state.author : '') + sourceSuffix + ' ' + e.week;
    f.onload = function() {{
      try {{
        const h = f.contentDocument && f.contentDocument.body && f.contentDocument.body.scrollHeight;
        if (h && h > 200) f.style.minHeight = (h + 24) + 'px';
      }} catch (_) {{ /* cross-origin: keep default 1200px */ }}
    }};
    sec.appendChild(f);
  }}

  function syncNav() {{
    const tab = getTab();
    document.getElementById('nav-weekly').classList.toggle('active', tab === TAB_WEEKLY);
    document.getElementById('nav-history').classList.toggle('active', tab === TAB_HISTORY);
  }}

  function select(author, week, sourceAuthor) {{
    state.author = author;
    state.week = week;
    state.sourceAuthor = sourceAuthor || null;
    location.hash = '#/' + getTab() + '/' + author + '/' + week + (sourceAuthor ? '/' + sourceAuthor : '');
    renderMenu();
    renderContent();
  }}

  function setTab(tab) {{
    state.author = null;
    state.week = null;
    state.sourceAuthor = null;
    location.hash = '#/' + tab + '/';
    if (tab === TAB_WEEKLY) {{
      const authors = authorsThisWeek();
      if (authors.length) {{ select(authors[0], currentWeek); return; }}
    }}
    syncNav();
    renderMenu();
    renderContent();
  }}

  function applyHash() {{
    const m = location.hash.match(/^#\\/(weekly|history)\\/([^/]+)\\/([^/]+)(?:\\/([^/]+))?$/);
    if (m) {{
      syncNav();
      state.author = m[2];
      state.week = m[3];
      state.sourceAuthor = m[4] || null;
      renderMenu();
      renderContent();
      return true;
    }}
    return false;
  }}

  document.getElementById('nav-weekly').onclick = () => setTab(TAB_WEEKLY);
  document.getElementById('nav-history').onclick = () => setTab(TAB_HISTORY);
  window.addEventListener('hashchange', applyHash);

  if (!applyHash()) setTab(TAB_WEEKLY);
}})();
</script>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: build_index.py <repo_root>", file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    entries = scan_reports(root / "reports")
    authors = load_author_names(root / "authors.json")
    author_aliases = load_author_aliases(root / "author_aliases.json")
    entries = apply_author_aliases(entries, author_aliases)
    current_week = current_index_week(entries)
    data = {
        "entries": entries,
        "current_week": current_week,
        "authors": authors,
        "author_aliases": author_aliases,
    }
    (root / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        render_index(entries, current_week, authors, author_aliases), encoding="utf-8"
    )
    print(f"built: {len(entries)} entries, current_week={current_week}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
