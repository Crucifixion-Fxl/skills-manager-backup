from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "private_report.py"
SPEC = importlib.util.spec_from_file_location("private_report", SCRIPT)
private_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(private_report)


def test_prepare_and_finalize_keep_report_private(tmp_path):
    draft = private_report.prepare("2026-09-16", root=tmp_path)
    assert draft.stat().st_mode & 0o777 == 0o600
    draft.write_text("<html>private review</html>", encoding="utf-8")

    report = private_report.finalize(draft, "2026-09-16", root=tmp_path)

    assert report == tmp_path / "weekly-report-2026-09-16.html"
    assert report.stat().st_mode & 0o777 == 0o600
    assert not draft.exists()


def test_finalize_rejects_symlink_target(tmp_path):
    draft = private_report.prepare("2026-09-16", root=tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    target = tmp_path / "weekly-report-2026-09-16.html"
    target.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        private_report.finalize(draft, "2026-09-16", root=tmp_path)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_finalize_rejects_a_draft_from_a_different_report_date(tmp_path):
    draft = private_report.prepare("2026-09-15", root=tmp_path)

    with pytest.raises(ValueError, match="date does not match"):
        private_report.finalize(draft, "2026-09-16", root=tmp_path)

    assert draft.exists()


def test_expiry_only_selects_owned_private_weekly_files(tmp_path):
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    old = tmp_path / "weekly-report-2026-09-01.html"
    old.write_text("old", encoding="utf-8")
    old.chmod(0o600)
    old_time = (now - timedelta(days=8)).timestamp()
    os.utime(old, (old_time, old_time))
    public = tmp_path / "weekly-report-2026-09-02.html"
    public.write_text("public", encoding="utf-8")
    public.chmod(0o644)
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    assert private_report.expired_files(root=tmp_path, now=now) == [old]
