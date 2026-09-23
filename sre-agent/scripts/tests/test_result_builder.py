#!/usr/bin/env python3
"""result_builder.py unit tests."""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from result_builder import ResultBuilder


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestRequiredFields:
    def test_save_fails_without_status(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        assert rb.save() is False

    def test_save_succeeds_with_success(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_success(summary="Completed the fix successfully for the service")
        assert rb.save() is True
        assert os.path.isfile(os.path.join(output_dir, "result.json"))

    def test_save_succeeds_with_failure(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_failure(
            summary="Failed to apply the configuration change",
            error_detail="Permission denied on namespace prod-us",
        )
        assert rb.save() is True

    def test_save_succeeds_with_skipped(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_skipped(reason="Problem self-healed before execution")
        assert rb.save() is True


class TestMutualExclusion:
    def test_cannot_set_success_then_failure(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_success(summary="A" * 20)
        with pytest.raises(ValueError, match="already set"):
            rb.set_failure(summary="A" * 20, error_detail="err")

    def test_cannot_set_failure_then_skipped(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_failure(summary="A" * 20, error_detail="err")
        with pytest.raises(ValueError, match="already set"):
            rb.set_skipped(reason="reason")


class TestContentValidation:
    def test_failure_requires_error_detail(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_failure(summary="A" * 20, error_detail="")
        assert rb.save() is False

    def test_summary_minimum_length(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_success(summary="short")
        assert rb.save() is False


class TestOutputStructure:
    def test_success_json_structure(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_success(
            summary="Applied HikariCP pool size change successfully",
            changes_made=["Changed maxPoolSize from 50 to 20"],
            verification={"method": "prometheus query", "result": "connections dropped to 2000"},
        )
        rb.save()

        with open(os.path.join(output_dir, "result.json")) as f:
            result = json.load(f)

        assert result["cg_id"] == "CG-1"
        assert result["solution_idx"] == 0
        assert result["status"] == "success"
        assert "executed_at" in result
        assert result["changes_made"] == ["Changed maxPoolSize from 50 to 20"]

    def test_skipped_json_structure(self, output_dir):
        rb = ResultBuilder("CG-1", 0, output_dir)
        rb.set_skipped(reason="Problem self-healed")
        rb.save()

        with open(os.path.join(output_dir, "result.json")) as f:
            result = json.load(f)

        assert result["status"] == "skipped_self_healed"
        assert result["summary"] == "Problem self-healed"
        assert result["reason"] == "Problem self-healed"
