#!/usr/bin/env python3
"""review_builder.py unit tests."""

import os
import sys
import tempfile
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from review_builder import ReviewBuilder


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


_CHECKPOINT = {"id": "rc-1", "passed": True, "detail": "Root cause clearly identified"}

_ALL_SECTION_NAMES = [
    "root_cause_review",
    "timeline_review",
    "impact_review",
    "risk_review",
    "evidence_review",
]


def _make_valid_builder(output_dir):
    """Helper: create a ReviewBuilder with all required sections and approved verdict."""
    rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
    for section in _ALL_SECTION_NAMES:
        rb.add_section(
            name=section,
            verdict="approved",
            checkpoints=[{"id": f"{section}-1", "passed": True, "detail": "Looks good"}],
        )
    rb.set_verdict("approved")
    return rb


class TestValidReview:
    def test_save_succeeds_with_all_sections_and_approved_verdict(self, output_dir):
        rb = _make_valid_builder(output_dir)
        assert rb.save() is True
        assert os.path.isfile(os.path.join(output_dir, "review.yaml"))

    def test_saved_yaml_is_valid_and_complete(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.save()

        with open(os.path.join(output_dir, "review.yaml")) as f:
            data = yaml.safe_load(f)

        assert data["cg_id"] == "CG-1"
        assert data["overall_verdict"] == "approved"
        assert "reviewed_at" in data
        for section in _ALL_SECTION_NAMES:
            assert section in data
            assert "verdict" in data[section]
            assert "checkpoints" in data[section]


class TestMissingSection:
    def test_save_fails_when_one_section_missing(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        # Add only 4 of the 5 required sections
        for section in _ALL_SECTION_NAMES[:-1]:
            rb.add_section(
                name=section,
                verdict="approved",
                checkpoints=[{"id": "cp-1", "passed": True, "detail": "ok"}],
            )
        rb.set_verdict("approved")
        assert rb.save() is False

    def test_save_fails_with_specific_missing_section_error(self, output_dir, capsys):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        rb.set_verdict("approved")
        rb.save()

        captured = capsys.readouterr()
        for section in _ALL_SECTION_NAMES:
            assert f"Missing review section: {section}" in captured.out


class TestNeedsRevision:
    def test_save_fails_needs_revision_without_revision_notes(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.set_verdict("needs_revision")  # no revision_notes
        assert rb.save() is False

    def test_save_fails_needs_revision_empty_revision_notes(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.set_verdict("needs_revision", revision_notes="")
        assert rb.save() is False

    def test_save_succeeds_needs_revision_with_revision_notes(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.set_verdict("needs_revision", revision_notes="Root cause analysis is too shallow.")
        assert rb.save() is True

    def test_revision_notes_in_output(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.set_verdict("needs_revision", revision_notes="Timeline is incomplete.")
        rb.save()

        with open(os.path.join(output_dir, "review.yaml")) as f:
            data = yaml.safe_load(f)

        assert data["overall_verdict"] == "needs_revision"
        assert data["revision_notes"] == "Timeline is incomplete."


class TestInvalidVerdict:
    def test_invalid_verdict_raises_value_error(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        with pytest.raises(ValueError, match="overall_verdict"):
            rb.set_verdict("rejected")

    def test_invalid_verdict_another_value(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        with pytest.raises(ValueError, match="overall_verdict"):
            rb.set_verdict("pending")


class TestCheckpointValidation:
    def test_checkpoint_missing_id_raises(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        with pytest.raises(ValueError, match="Checkpoint missing required fields"):
            rb.add_section(
                name="root_cause_review",
                verdict="approved",
                checkpoints=[{"passed": True, "detail": "ok"}],  # missing 'id'
            )

    def test_checkpoint_missing_passed_raises(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        with pytest.raises(ValueError, match="Checkpoint missing required fields"):
            rb.add_section(
                name="root_cause_review",
                verdict="approved",
                checkpoints=[{"id": "rc-1", "detail": "ok"}],  # missing 'passed'
            )

    def test_checkpoint_missing_detail_raises(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        with pytest.raises(ValueError, match="Checkpoint missing required fields"):
            rb.add_section(
                name="root_cause_review",
                verdict="approved",
                checkpoints=[{"id": "rc-1", "passed": False}],  # missing 'detail'
            )

    def test_valid_checkpoint_accepted(self, output_dir):
        rb = ReviewBuilder(cg_id="CG-1", output_dir=output_dir)
        # Should not raise
        rb.add_section(
            name="root_cause_review",
            verdict="approved",
            checkpoints=[{"id": "rc-1", "passed": True, "detail": "All good"}],
        )


class TestSolutionReview:
    def test_add_solution_review_works(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.add_solution_review(
            index=0,
            verdict="approved",
            checkpoints=[{"id": "sol-1", "passed": True, "detail": "Solution is actionable"}],
            recommendation="Deploy during low-traffic window.",
        )
        assert rb.save() is True

    def test_solution_review_in_output(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.add_solution_review(
            index=0,
            verdict="needs_revision",
            checkpoints=[{"id": "sol-1", "passed": False, "detail": "Missing rollback step"}],
            recommendation="Add rollback plan before executing.",
        )
        rb.save()

        with open(os.path.join(output_dir, "review.yaml")) as f:
            data = yaml.safe_load(f)

        assert "solutions_review" in data
        assert len(data["solutions_review"]) == 1
        sr = data["solutions_review"][0]
        assert sr["index"] == 0
        assert sr["verdict"] == "needs_revision"
        assert sr["recommendation"] == "Add rollback plan before executing."
        assert sr["checkpoints"][0]["id"] == "sol-1"
        assert sr["checkpoints"][0]["passed"] is False

    def test_multiple_solution_reviews(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.add_solution_review(index=0, verdict="approved",
                               checkpoints=[{"id": "s0-1", "passed": True, "detail": "ok"}])
        rb.add_solution_review(index=1, verdict="approved",
                               checkpoints=[{"id": "s1-1", "passed": True, "detail": "ok"}])
        rb.save()

        with open(os.path.join(output_dir, "review.yaml")) as f:
            data = yaml.safe_load(f)

        assert len(data["solutions_review"]) == 2
        assert data["solutions_review"][1]["index"] == 1

    def test_no_solutions_review_key_when_none_added(self, output_dir):
        rb = _make_valid_builder(output_dir)
        rb.save()

        with open(os.path.join(output_dir, "review.yaml")) as f:
            data = yaml.safe_load(f)

        assert "solutions_review" not in data


class TestBuildDict:
    def test_build_dict_includes_reviewed_at_timestamp(self, output_dir):
        rb = _make_valid_builder(output_dir)
        result = rb._build_dict()

        assert "reviewed_at" in result
        # Verify it's a valid ISO 8601 UTC timestamp
        ts = result["reviewed_at"]
        assert ts.endswith("Z")
        assert "T" in ts
        assert len(ts) == 20  # "YYYY-MM-DDTHH:MM:SSZ"

    def test_build_dict_sections_ordered(self, output_dir):
        rb = _make_valid_builder(output_dir)
        result = rb._build_dict()

        # All required sections present
        from review_builder import _REQUIRED_SECTIONS
        for section in _REQUIRED_SECTIONS:
            assert section in result

    def test_build_dict_no_revision_notes_when_approved(self, output_dir):
        rb = _make_valid_builder(output_dir)
        result = rb._build_dict()
        assert "revision_notes" not in result
