"""Display equivalents for report blocks unsupported by Card 1.0 lark_md.

Input has already passed card_markdown's tag/control neutralization. This is
only presentation: never parse mentions, HTML, URLs or receipt metadata here.
"""
from __future__ import annotations

import re


def _cells(line):
    """Split table delimiters, retaining escaped pipes and inline code bytes."""
    if line.expandtabs(4).startswith('    '):
        return None
    line = line.strip()
    if '|' not in line:
        return None
    parts, start, at, code = [], 0, 0, 0
    while at < len(line):
        if line[at] == '\\':
            at += 2
            continue
        if line[at] == '`':
            end = at + 1
            while end < len(line) and line[end] == '`':
                end += 1
            width = end - at
            if code == width:
                code = 0
            elif not code:
                # An unmatched backtick is literal, not a table delimiter mask.
                if re.search(r'(?<!`)' + '`' * width + r'(?!`)', line[end:]):
                    code = width
            at = end
            continue
        if line[at] == '|' and not code:
            parts.append(line[start:at].strip())
            start = at + 1
        at += 1
    if not parts:
        return None
    parts.append(line[start:].strip())
    if line.startswith('|'):
        parts.pop(0)
    if line.endswith('|') and parts[-1] == '':
        parts.pop()
    return parts


def card1_report_blocks(text):
    """Keep inline markup; replace headings and complete rectangular tables.

    Fences and malformed/ambiguous tables stay literal. No truncation or network
    calls: the caller's existing card byte budget and source link still apply.
    """
    lines = text.split('\n')
    result, at, fence = [], 0, None
    while at < len(lines):
        line = lines[at]
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            result.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            at += 1
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            result.append(line)
            at += 1
            continue
        headers = _cells(line)
        separators = _cells(lines[at + 1]) if at + 1 < len(lines) else None
        if (headers and separators and len(headers) == len(separators)
                and all(re.fullmatch(r':?-{3,}:?', cell) for cell in separators)):
            end, rows = at + 2, []
            while end < len(lines):
                if re.match(r'^ {0,3}(`{3,}|~{3,})', lines[end]):
                    break
                row = _cells(lines[end])
                if row is None:
                    break
                rows.append(row)
                end += 1
            if rows and all(len(row) == len(headers) for row in rows):
                result.extend('• ' + '；'.join(f'{label}：{cell}' for label, cell in zip(headers, row)) for row in rows)
                at = end
                continue
            # Preserve the whole malformed block, rather than reinterpret its
            # rows as a second table and silently hide some source content.
            result.extend(lines[at:end])
            at = end
            continue
        heading = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*$', line)
        if heading:
            title = re.sub(r'\s+#+\s*$', '', heading[1])
            result.append('**' + title + '**')
        else:
            result.append(line)
        at += 1
    return '\n'.join(result)
