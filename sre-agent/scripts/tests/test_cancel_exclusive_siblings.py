#!/usr/bin/env python3
"""Tests for _cancel_exclusive_siblings — dispatcher Step 7a 互斥方案联动撤销。"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher_loop import _cancel_exclusive_siblings
from state_manager import StateManager


@pytest.fixture
def mgr():
    with tempfile.TemporaryDirectory() as d:
        yield StateManager(d)


def _sol(title, mode="parallel", group=None, **extras):
    s = {
        "title": title,
        "action": "...", "addresses": "causal_chain[0]",
        "expected_effect": "...", "risk": "high",
        "reversible": False, "blast_radius": "...",
        "verify_cmd": "...", "prompt": "...",
        "relation": {"mode": mode},
    }
    if group:
        s["relation"]["group"] = group
    s.update(extras)
    return s


def _cg(short_term):
    return {
        "correlation_group": "CG-1",
        "solutions": {"short_term": short_term},
    }


def _seed_pending(mgr, cg_id, idx, instance_code):
    mgr.save_approval(cg_id, idx, {
        "status": "pending_approval",
        "instance_code": instance_code,
        "approver_open_id": "ou_test",
    })


class TestCancelExclusiveSiblings:
    def test_parallel_noop(self, mgr):
        cg = _cg([_sol("A"), _sol("B")])
        _seed_pending(mgr, "CG-1", 1, "INST-B")
        errors = []
        cancelled = _cancel_exclusive_siblings(
            mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
        )
        assert cancelled == []
        assert errors == []
        # sibling still pending
        assert mgr.load_approval("CG-1", 1)["status"] == "pending_approval"

    def test_exclusive_no_siblings_noop(self, mgr):
        cg = _cg([_sol("A", mode="exclusive", group="g1")])
        errors = []
        cancelled = _cancel_exclusive_siblings(
            mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
        )
        assert cancelled == []
        assert errors == []

    def test_cancels_single_sibling(self, mgr):
        cg = _cg([
            _sol("续费", mode="exclusive", group="g1"),
            _sol("弃用", mode="exclusive", group="g1"),
        ])
        _seed_pending(mgr, "CG-1", 1, "INST-B")
        errors = []
        with patch("dispatcher_loop.cancel_instance") as mock_cancel:
            mock_cancel.return_value = {"code": 0}
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert cancelled == [(1, "弃用")]
        assert errors == []
        mock_cancel.assert_called_once_with("tok", "INST-B", "ou_sub")
        assert mgr.load_approval("CG-1", 1)["status"] == "exclusive_cancelled"

    def test_cancels_multiple_siblings(self, mgr):
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g1"),
            _sol("C", mode="exclusive", group="g1"),
        ])
        _seed_pending(mgr, "CG-1", 1, "INST-B")
        _seed_pending(mgr, "CG-1", 2, "INST-C")
        errors = []
        with patch("dispatcher_loop.cancel_instance", return_value={"code": 0}):
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert sorted(cancelled) == [(1, "B"), (2, "C")]
        assert mgr.load_approval("CG-1", 1)["status"] == "exclusive_cancelled"
        assert mgr.load_approval("CG-1", 2)["status"] == "exclusive_cancelled"

    def test_different_group_not_cancelled(self, mgr):
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g2"),
        ])
        _seed_pending(mgr, "CG-1", 1, "INST-B")
        errors = []
        with patch("dispatcher_loop.cancel_instance") as mock_cancel:
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert cancelled == []
        mock_cancel.assert_not_called()
        assert mgr.load_approval("CG-1", 1)["status"] == "pending_approval"

    def test_skips_non_pending_sibling(self, mgr):
        """Sibling already approved/rejected/cancelled — don't touch it."""
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g1"),
        ])
        mgr.save_approval("CG-1", 1, {
            "status": "approved",
            "instance_code": "INST-B",
        })
        errors = []
        with patch("dispatcher_loop.cancel_instance") as mock_cancel:
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert cancelled == []
        mock_cancel.assert_not_called()
        assert mgr.load_approval("CG-1", 1)["status"] == "approved"

    def test_sibling_without_state_file_skipped(self, mgr):
        """approval state doesn't exist for sibling — skip gracefully."""
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g1"),
        ])
        # 注意: sibling 没有 seed
        errors = []
        with patch("dispatcher_loop.cancel_instance") as mock_cancel:
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert cancelled == []
        mock_cancel.assert_not_called()

    def test_cancel_api_error_recorded_but_continues(self, mgr):
        """一个 sibling cancel API 失败不应阻止其他 sibling 的撤销。"""
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g1"),
            _sol("C", mode="exclusive", group="g1"),
        ])
        _seed_pending(mgr, "CG-1", 1, "INST-B")
        _seed_pending(mgr, "CG-1", 2, "INST-C")
        errors = []

        def flaky(token, code, submitter):
            if code == "INST-B":
                raise RuntimeError("feishu api error 500")
            return {"code": 0}

        with patch("dispatcher_loop.cancel_instance", side_effect=flaky):
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        # C should still be cancelled, B should not
        assert cancelled == [(2, "C")]
        assert len(errors) == 1
        assert "INST-B" in errors[0] or "ST-1" in errors[0]
        assert mgr.load_approval("CG-1", 1)["status"] == "pending_approval"
        assert mgr.load_approval("CG-1", 2)["status"] == "exclusive_cancelled"

    def test_none_cg_result_noop(self, mgr):
        errors = []
        cancelled = _cancel_exclusive_siblings(
            mgr, "CG-1", 0, None, "tok", "ou_sub", errors,
        )
        assert cancelled == []
        assert errors == []

    def test_sibling_missing_instance_code_skipped(self, mgr):
        cg = _cg([
            _sol("A", mode="exclusive", group="g1"),
            _sol("B", mode="exclusive", group="g1"),
        ])
        # sibling 有 approval 记录但没 instance_code
        mgr.save_approval("CG-1", 1, {"status": "pending_approval"})
        errors = []
        with patch("dispatcher_loop.cancel_instance") as mock_cancel:
            cancelled = _cancel_exclusive_siblings(
                mgr, "CG-1", 0, cg, "tok", "ou_sub", errors,
            )
        assert cancelled == []
        mock_cancel.assert_not_called()
        # state 未改
        assert mgr.load_approval("CG-1", 1).get("status") == "pending_approval"
