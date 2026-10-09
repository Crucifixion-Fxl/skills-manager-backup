"""Data-driven error signatures and reset-time parsing."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import asset_path

KINDS = ("quota", "transient", "auth")
CN_TZ = dt.timezone(dt.timedelta(hours=8))
CN_RESET_RE = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*后可继续使用")
TRY_AGAIN_RE = re.compile(
    r"try again at ([A-Za-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4}),? (\d{1,2}):(\d\d)\s*([AP]M)\b", re.I)
RESETS_RE = re.compile(r"resets\s+(?:([A-Za-z]{3})\s+(\d{1,2}),?\s*)?(\d{1,2})(?::(\d\d))?\s*(am|pm)\s*\(([^)]+)\)", re.I)


@dataclass(frozen=True)
class Signature:
    id: str
    harness: str
    kind: str
    pattern: str
    reset: str = "none"
    provider: str | None = None
    verified: bool = False

    def regex(self) -> re.Pattern:
        return re.compile(self.pattern, re.I)


@dataclass(frozen=True)
class Match:
    id: str
    kind: str  # quota | transient | auth
    until: object = None  # aware datetime or None


def load_signatures(path: str | None = None) -> list[Signature]:
    out = []
    for s in json.load(open(path or asset_path("signatures.json")))["signatures"]:
        out.append(Signature(id=s["id"], harness=s["harness"], kind=s["kind"], pattern=s["pattern"],
                             reset=s.get("reset", "none"), provider=s.get("provider"), verified=bool(s.get("verified"))))
    return out


def parse_cn_reset(text: str):
    """智谱错误体里的恢复时刻，例如 `2026-09-18 18:36:35 后可继续使用`（+08）。"""
    m = CN_RESET_RE.search(text)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ).astimezone(dt.timezone.utc)


def parse_resets(text: str, after: dt.datetime):
    """Claude 订阅限额文案 `resets 12am (UTC)`（可带 `Sep 25,`）→ after 之后的下一次重置时刻。"""
    m = RESETS_RE.search(text)
    if not m:
        return None
    mon, day, hh, mm, ap, tzname = m.groups()
    try:
        tz = ZoneInfo(tzname.strip())
    except Exception:
        return None
    hour = int(hh) % 12 + (12 if ap.lower() == "pm" else 0)
    base = after.astimezone(tz)
    try:
        if mon:
            month = dt.datetime.strptime(mon[:3].title(), "%b").month
            cand = base.replace(month=month, day=int(day), hour=hour, minute=int(mm or 0), second=0, microsecond=0)
            if cand <= base:
                cand = cand.replace(year=cand.year + 1)
        else:
            cand = base.replace(hour=hour, minute=int(mm or 0), second=0, microsecond=0)
            if cand <= base:
                cand += dt.timedelta(days=1)
    except ValueError:
        return None
    return cand.astimezone(dt.timezone.utc)


def parse_try_again(text: str, tz=None):
    """codex 额度文案 `… try again at Oct 1st, 2026 12:13 AM.` → UTC。codex 按**主机本地时区**打印（tz=None 即本机时区）。
    没有日期的 `Try again at 4:15 PM.` 不当作恢复时刻（返回 None，按「无恢复时刻」处理）。"""
    m = TRY_AGAIN_RE.search(text)
    if not m:
        return None
    mon, day, year, hh, mm, ap = m.groups()
    try:
        month = dt.datetime.strptime(mon.title(), "%b").month
        hour = int(hh) % 12 + (12 if ap.upper() == "PM" else 0)
        naive = dt.datetime(int(year), month, int(day), hour, int(mm))
    except ValueError:
        return None
    local = naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
    return local.astimezone(dt.timezone.utc)


ANY = object()  # "do not scope by provider"


def classify(text: str, sigs: list[Signature], harness: str | None = None, after: dt.datetime | None = None,
             provider=ANY):
    """`provider` scopes provider-specific signatures (e.g. glm): pass the profile's provider (None for a plain
    account) so a stale GLM error in a shared history dir cannot make another account look exhausted."""
    for s in sigs:
        if harness and s.harness != harness:
            continue
        if provider is not ANY and s.provider and s.provider != provider:
            continue
        if not s.regex().search(text):
            continue
        until = None
        if s.reset == "cn-datetime":
            until = parse_cn_reset(text)
        elif s.reset == "resets" and after is not None:
            until = parse_resets(text, after)
        elif s.reset == "try-again-at":
            until = parse_try_again(text)
        return Match(id=s.id, kind=s.kind, until=until)
    return None
