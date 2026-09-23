"""Tests for find_sp_transitions_in."""

from datetime import datetime, timezone

from scripts._common import SPInfo, Window, find_sp_transitions_in


def _sp(id_: str, start: datetime, end: datetime, state: str = "active") -> SPInfo:
    return SPInfo(
        id=id_,
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=1.0,
        start=start,
        end=end,
        state=state,
        payment_option="No Upfront",
    )


def test_sp_starting_inside_window():
    window = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=60)
    sp = _sp(
        "sp-a",
        start=datetime(2026, 3, 1, tzinfo=timezone.utc),
        end=datetime(2027, 3, 1, tzinfo=timezone.utc),
    )
    events = find_sp_transitions_in([sp], window)
    assert len(events) == 1
    assert events[0].sp_id == "sp-a"
    assert events[0].event_type == "start"


def test_sp_ending_inside_window():
    window = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=60)
    sp = _sp(
        "sp-b",
        start=datetime(2025, 3, 15, tzinfo=timezone.utc),
        end=datetime(2026, 3, 15, tzinfo=timezone.utc),
        state="retired",
    )
    events = find_sp_transitions_in([sp], window)
    assert len(events) == 1
    assert events[0].event_type == "end"


def test_sp_fully_inside_window_both_events():
    window = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=90)
    sp = _sp(
        "sp-c",
        start=datetime(2026, 2, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    events = find_sp_transitions_in([sp], window)
    assert len(events) == 2
    assert {e.event_type for e in events} == {"start", "end"}


def test_sp_outside_window_no_events():
    window = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=60)
    sp = _sp(
        "sp-d",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    events = find_sp_transitions_in([sp], window)
    assert events == []
