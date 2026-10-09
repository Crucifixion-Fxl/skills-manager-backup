import re
from datetime import datetime
from typing import Iterable, TypeVar

T = TypeVar("T")
_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_time(value) -> datetime | None:
    """Timezone-aware datetime, or None when missing, unparsable or naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(_FRACTION.sub(r"\1", value.strip()))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def attribute(shots: Iterable[T], start: datetime | None, end: datetime | None) -> tuple[list[T], list[T]]:
    """Split shots into (kept, raw-only): kept iff stroke time is known and in [start, end)."""
    kept, excluded = [], []
    for shot in shots:
        t = parse_time(shot.time)
        inside = t is not None and (start is None or t >= start) and (end is None or t < end)
        (kept if inside else excluded).append(shot)
    return kept, excluded
