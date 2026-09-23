"""Regression tests for _locate_1y_nu_compute_term.

Real AWS Compute SP rate sheet has `terms.savingsPlan` as a LIST of term
objects, not a dict. The original spec assumed dict; smoke-testing a real
a4x-us cold run hit `AttributeError: 'list' object has no attribute 'values'`.
These tests lock in the list-shape contract so the shape can't regress.
"""

import pytest

from scripts._common import CacheError
from scripts.pricing_cache import _locate_1y_nu_compute_term


def test_finds_1y_nu_compute_term_in_list():
    sheet = {
        "terms": {
            "savingsPlan": [
                {
                    "sku": "sp-3y-nu",
                    "description": "3 year No Upfront Compute Savings Plan",
                    "rates": [],
                },
                {
                    "sku": "sp-1y-nu",
                    "description": "1 year No Upfront Compute Savings Plan",
                    "rates": [{"discountedSku": "X"}],
                },
            ]
        }
    }
    term = _locate_1y_nu_compute_term(sheet)
    assert term["sku"] == "sp-1y-nu"
    assert term["description"] == "1 year No Upfront Compute Savings Plan"
    assert term["rates"] == [{"discountedSku": "X"}]


def test_missing_term_raises_term_not_found_with_descriptions_seen():
    sheet = {
        "terms": {
            "savingsPlan": [
                {"sku": "sp-3y-nu", "description": "3 year No Upfront Compute Savings Plan"},
                {"sku": "sp-1y-au", "description": "1 year All Upfront Compute Savings Plan"},
            ]
        }
    }
    with pytest.raises(CacheError) as exc:
        _locate_1y_nu_compute_term(sheet)
    assert exc.value.code == "term_not_found"
    assert "descriptions_seen" in exc.value.context
    assert "3 year No Upfront Compute Savings Plan" in exc.value.context["descriptions_seen"]


def test_dict_shape_raises_shape_changed_guard():
    """If AWS ever reverts to dict-keyed terms, fail loud with a specific code."""
    sheet = {
        "terms": {
            "savingsPlan": {
                "offerCodeSP": {
                    "description": "1 year No Upfront Compute Savings Plan",
                    "rates": [],
                }
            }
        }
    }
    with pytest.raises(CacheError) as exc:
        _locate_1y_nu_compute_term(sheet)
    assert exc.value.code == "sp_sheet_shape_changed"
    assert exc.value.context.get("real_type") == "dict"


def test_empty_list_raises_term_not_found():
    sheet = {"terms": {"savingsPlan": []}}
    with pytest.raises(CacheError) as exc:
        _locate_1y_nu_compute_term(sheet)
    assert exc.value.code == "term_not_found"
    assert exc.value.context["descriptions_seen"] == []


def test_missing_terms_key_raises_term_not_found():
    """Fully empty sheet still fails gracefully with term_not_found,
    not an AttributeError or KeyError."""
    sheet: dict = {}
    with pytest.raises(CacheError) as exc:
        _locate_1y_nu_compute_term(sheet)
    assert exc.value.code == "term_not_found"
