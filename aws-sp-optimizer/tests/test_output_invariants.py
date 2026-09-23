"""Tests for validate_output_invariants — one failure case per invariant."""

import pytest

from scripts.output_builder import validate_output_invariants


def _ok_output():
    return {
        "schema_version": 1,
        "status": "ok",
        "context": {},
        "baseline": {},
        "recommendation": {
            "delta_per_hour_to_buy": 5.0,
            "annual_delta_usd": round(5.0 * 8760, 2),
            "must_review_before_acting": False,
        },
        "search_summary": {},
        "purchase_instructions": {
            "step_2_create_savings_plan": {"command_template": "aws ... --commitment '5.0000' ..."}
        },
        "risks": [],
        "warnings": [],
    }


def test_ok_output_passes():
    validate_output_invariants(_ok_output())


def test_inv1_schema_version_check():
    o = _ok_output()
    o["schema_version"] = 2
    with pytest.raises(AssertionError, match="INV-1"):
        validate_output_invariants(o)


def test_inv2_unknown_status():
    o = _ok_output()
    o["status"] = "banana"
    with pytest.raises(AssertionError, match="INV-2"):
        validate_output_invariants(o)


def test_inv3_missing_recommendation():
    o = _ok_output()
    del o["recommendation"]
    with pytest.raises(AssertionError, match="INV-3"):
        validate_output_invariants(o)


def test_inv4_ok_with_adjustment_needs_adjustments_made():
    o = _ok_output()
    o["status"] = "ok_with_adjustment"
    with pytest.raises(AssertionError, match="INV-4"):
        validate_output_invariants(o)


def test_inv5_must_review_must_null_purchase_instructions():
    o = _ok_output()
    o["recommendation"]["must_review_before_acting"] = True
    # purchase_instructions still set → violation
    with pytest.raises(AssertionError, match="INV-5"):
        validate_output_invariants(o)


def test_inv5_must_review_false_requires_purchase_instructions():
    o = _ok_output()
    o["purchase_instructions"] = None
    with pytest.raises(AssertionError, match="INV-5"):
        validate_output_invariants(o)


def test_inv6_delta_non_negative():
    o = _ok_output()
    o["recommendation"]["delta_per_hour_to_buy"] = -1.0
    with pytest.raises(AssertionError, match="INV-6"):
        validate_output_invariants(o)


def test_inv7_annual_matches_hourly():
    o = _ok_output()
    o["recommendation"]["annual_delta_usd"] = 999999.0
    with pytest.raises(AssertionError, match="INV-7"):
        validate_output_invariants(o)


def test_inv10_command_template_contains_formatted_commitment():
    o = _ok_output()
    o["purchase_instructions"]["step_2_create_savings_plan"]["command_template"] = (
        "aws ... --commitment '5.00' ..."  # wrong precision
    )
    with pytest.raises(AssertionError, match="INV-10"):
        validate_output_invariants(o)
