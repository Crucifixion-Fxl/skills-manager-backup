#!/usr/bin/env python3
"""Contract: schedule cron templates use the Buzz relay's day-of-week convention.

The relay reads the fifth cron field Quartz-style: 1=SUN, 2=MON ... 7=SAT.
Reading it as "1=Monday" makes weekly reports fire on Sunday and weekday
reports run Sunday-Thursday (2026-09-21 evidence, engineering/skills#132).
These tests expand each template's ``trigger.cron`` with the relay convention
and check the days it really lands on. Only the standard library is used
because CI runs this directory with a bare ``python3``.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL_DIR / "references" / "analysis-workflows"
SCHEDULED = SKILL_DIR / "references" / "scheduled-workflows.md"

# Relay convention (Quartz style). Names are deliberately not accepted anywhere:
# `MON` / `MON-FRI` were never verified against the relay.
RELAY_DAYS = {1: "SUN", 2: "MON", 3: "TUE", 4: "WED", 5: "THU", 6: "FRI", 7: "SAT"}
WORKWEEK = ["MON", "TUE", "WED", "THU", "FRI"]

# template -> expected schedule shape. A new template must be added here, so the
# author has to decide which days it may land on.
#   weekly  : only Monday
#   weekday : Monday to Friday, never Sunday or Saturday
#   monthly : day-of-month 1, day-of-week unrestricted (no weekday to get wrong)
SHAPES = {
    "delivery-progress.yaml": "weekday",
    "data-review.yaml": "weekday",
    "pipeline-health.yaml": "weekly",
    "platform-feedback-summary.yaml": "weekly",
    "architecture-smell.yaml": "monthly",
}


def read_cron(name: str) -> tuple[str, str, str, str, str]:
    """Return the five cron fields of ``trigger.cron`` in a template."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    block = re.search(r"^trigger:[ \t]*\n((?:[ \t]+.*\n?)+)", text, re.M)
    if block is None:
        raise AssertionError(f"{name}: no trigger block")
    trigger = block.group(1)
    if not re.search(r"^[ \t]+on:[ \t]*schedule[ \t]*$", trigger, re.M):
        raise AssertionError(f"{name}: trigger.on must be schedule")
    cron = re.search(r'^[ \t]+cron:[ \t]*"([^"]*)"[ \t]*$', trigger, re.M)
    if cron is None:
        raise AssertionError(f"{name}: trigger.cron must be a quoted string")
    fields = cron.group(1).split()
    if len(fields) != 5:
        raise AssertionError(f"{name}: cron needs five fields, got {cron.group(1)!r}")
    return fields[0], fields[1], fields[2], fields[3], fields[4]


def expand_dow(field: str) -> list[str]:
    """Expand a relay day-of-week field to day names, Sunday first.

    Accepts `*`, `n`, `a-b`, `*/s`, `a-b/s` and comma lists over 1..7. Anything
    else (names, 0, 8, `n/s`) raises ValueError instead of guessing.
    """
    days: set[int] = set()
    for part in field.split(","):
        base, slash, step_text = part.partition("/")
        if slash and not re.fullmatch(r"\d+", step_text):
            raise ValueError(f"bad step in {field!r}")
        step = int(step_text) if slash else 1
        if step < 1:
            raise ValueError(f"bad step in {field!r}")
        if base == "*":
            low, high = 1, 7
        elif re.fullmatch(r"\d+-\d+", base):
            low, high = (int(number) for number in base.split("-"))
        elif re.fullmatch(r"\d+", base) and not slash:
            low = high = int(base)
        else:
            raise ValueError(f"unsupported day-of-week {field!r}: numbers 1-7 only, names are unverified")
        if not 1 <= low <= high <= 7:
            raise ValueError(f"day-of-week {field!r} is outside 1-7 (1=SUN ... 7=SAT)")
        days.update(range(low, high + 1, step))
    return [RELAY_DAYS[number] for number in sorted(days)]


def minute_of_day(name: str) -> int:
    minute, hour, *_ = read_cron(name)
    return int(hour) * 60 + int(minute)


class RelayDayOfWeekConventionTest(unittest.TestCase):
    """The expander itself: it must follow the relay, not the "1=Monday" habit."""

    def test_numbers_follow_the_relay_convention(self) -> None:
        cases = (
            ("1", ["SUN"]),
            ("2", ["MON"]),
            ("7", ["SAT"]),
            ("2-6", WORKWEEK),
            ("1-5", ["SUN", "MON", "TUE", "WED", "THU"]),
            ("1,7", ["SUN", "SAT"]),
            ("2,4,6", ["MON", "WED", "FRI"]),
            ("*", list(RELAY_DAYS.values())),
            ("*/2", ["SUN", "TUE", "THU", "SAT"]),
            ("2-6/2", ["MON", "WED", "FRI"]),
        )
        for field, expected in cases:
            with self.subTest(field=field):
                self.assertEqual(expand_dow(field), expected)

    def test_unverified_or_out_of_range_values_are_rejected(self) -> None:
        for field in ("0", "8", "0-6", "MON", "MON-FRI", "mon", "2-", "-6", "6-2", "2/2", "*/0", ""):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    expand_dow(field)


class AnalysisWorkflowCronTest(unittest.TestCase):
    def test_every_template_has_a_declared_shape(self) -> None:
        on_disk = sorted(path.name for path in TEMPLATES.glob("*.yaml"))
        self.assertEqual(on_disk, sorted(SHAPES), "declare new templates in SHAPES")

    def test_templates_land_on_the_intended_relay_days(self) -> None:
        for name, shape in SHAPES.items():
            with self.subTest(template=name, shape=shape):
                _, _, day_of_month, month, day_of_week = read_cron(name)
                self.assertEqual(month, "*", "month is unrestricted")
                try:
                    days = expand_dow(day_of_week)
                except ValueError as error:
                    self.fail(f"{name}: {error}")
                if shape == "weekly":
                    self.assertEqual(day_of_month, "*")
                    self.assertEqual(days, ["MON"], f"weekly must run on Monday only, got {days}")
                elif shape == "weekday":
                    self.assertEqual(day_of_month, "*")
                    self.assertEqual(days, WORKWEEK, f"weekday must run Monday-Friday, got {days}")
                    self.assertNotIn("SUN", days)
                    self.assertNotIn("SAT", days)
                else:
                    self.assertEqual(day_of_month, "1")
                    self.assertEqual(day_of_week, "*", "monthly has no weekday restriction to shift")

    def test_platform_feedback_summary_runs_the_same_day_after_pipeline_health(self) -> None:
        # Order constraint: pipeline-health -> hand-off -> platform summary, same day.
        health_days = expand_dow(read_cron("pipeline-health.yaml")[4])
        summary_days = expand_dow(read_cron("platform-feedback-summary.yaml")[4])
        self.assertEqual(summary_days, health_days)
        self.assertEqual(health_days, ["MON"])
        self.assertGreater(
            minute_of_day("platform-feedback-summary.yaml"),
            minute_of_day("pipeline-health.yaml"),
            "the summary must start after pipeline-health on that day",
        )

    def test_templates_that_have_a_weekday_warn_copiers_about_the_relay_convention(self) -> None:
        for name, shape in SHAPES.items():
            if shape == "monthly":
                continue
            with self.subTest(template=name):
                text = (TEMPLATES / name).read_text(encoding="utf-8")
                self.assertTrue(
                    re.search(r"(?m)^[ \t]*#.*星期字段 1=周日", text),
                    f"{name}: add a comment saying the relay's day-of-week is 1=周日",
                )


class ScheduledWorkflowsDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCHEDULED.read_text(encoding="utf-8")

    def test_the_relay_convention_is_stated_with_its_evidence(self) -> None:
        for needle in (
            "星期字段 1=周日",
            "周一=2",
            "周一到周五=2-6",
            "名字写法未验证",
            "2026-09-21",
            "09-20",
            "周日",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text, f"scheduled-workflows.md must state {needle!r}")

    def test_name_style_weekday_is_never_offered_as_working(self) -> None:
        # `MON` / `MON-FRI` were not verified; the page may say so, never recommend them.
        for pattern in (r"名字写法(可用|支持|也可以|同样)", r"(MON|TUE|WED|THU|FRI|SAT|SUN)[^\n]{0,12}(可用|支持)"):
            with self.subTest(pattern=pattern):
                found = re.search(pattern, self.text)
                self.assertIsNone(found, found and found.group(0))

    def test_catalog_table_matches_the_templates(self) -> None:
        rows = dict(
            re.findall(r"^\|\s*`analysis-workflows/([\w.-]+)`\s*\|\s*`([^`]+)`\s*\|", self.text, re.M)
        )
        self.assertEqual(sorted(rows), sorted(SHAPES), "every template needs a catalog row")
        for name, cron in rows.items():
            with self.subTest(template=name):
                self.assertEqual(cron, " ".join(read_cron(name)))


if __name__ == "__main__":
    unittest.main()
