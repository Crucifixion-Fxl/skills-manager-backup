"""Tests for merge_cli_into_config_filter."""

import argparse

import pytest

from scripts.aws_sp_optimizer import CliFilterError, merge_cli_into_config_filter
from scripts.exclude_filter import ExcludeFilter, TagExclusion


def _args(**kw):
    defaults = dict(
        exclude_usage_type_pattern=[],
        exclude_account_id=[],
        exclude_tag=[],
        include_untagged=True,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_empty_cli_returns_config_unchanged():
    cfg_flt = ExcludeFilter(usage_type_patterns=("g4dn.",))
    merged = merge_cli_into_config_filter(cfg_flt, _args())
    assert merged == cfg_flt


def test_cli_patterns_union_with_config_patterns():
    cfg_flt = ExcludeFilter(usage_type_patterns=("g4dn.",))
    merged = merge_cli_into_config_filter(
        cfg_flt, _args(exclude_usage_type_pattern=["p4d."]),
    )
    assert set(merged.usage_type_patterns) == {"g4dn.", "p4d."}


def test_cli_tag_parsing_key_equals_comma_values():
    merged = merge_cli_into_config_filter(
        ExcludeFilter(),
        _args(exclude_tag=["lifecycle=ephemeral,experimental"]),
    )
    assert merged.tag_exclusions == (
        TagExclusion(key="lifecycle", values=("ephemeral", "experimental")),
    )


def test_cli_tag_rejects_malformed():
    with pytest.raises(CliFilterError):
        merge_cli_into_config_filter(
            ExcludeFilter(), _args(exclude_tag=["nokeyequalssign"]),
        )


def test_cli_dedups_when_unioning():
    cfg_flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    merged = merge_cli_into_config_filter(
        cfg_flt,
        _args(
            exclude_usage_type_pattern=["g4dn."],
            exclude_account_id=["123456789012"],
        ),
    )
    assert merged.usage_type_patterns == ("g4dn.",)  # deduped
    assert merged.account_ids == ("123456789012",)


def test_cli_include_untagged_false_overrides_config_true():
    cfg_flt = ExcludeFilter(include_untagged=True)
    merged = merge_cli_into_config_filter(cfg_flt, _args(include_untagged=False))
    assert merged.include_untagged is False
