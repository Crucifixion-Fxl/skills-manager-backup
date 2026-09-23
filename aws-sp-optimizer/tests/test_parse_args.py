"""Tests for CLI parse_args."""

import pytest

from scripts.aws_sp_optimizer import parse_args


def test_org_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_org_alias_is_dest():
    args = parse_args(["--org", "a4x-us"])
    assert args.org_alias == "a4x-us"


def test_defaults():
    args = parse_args(["--org", "a4x-us"])
    assert args.window_days is None
    assert args.window_end is None
    assert args.prefer is None
    assert args.validate_only is False
    assert args.refresh_cache is False
    assert args.skip_pricing_refresh is False
    assert args.verbose is False
    assert args.log_file is None
    assert args.profile is None
    assert args.cur_database is None
    assert args.cur_table is None
    assert args.athena_output is None
    assert args.athena_workgroup is None


def test_overrides():
    args = parse_args(
        [
            "--org",
            "a4x-us",
            "--window-days",
            "60",
            "--window-end",
            "2026-04-01",
            "--prefer",
            "freshness",
            "--profile",
            "aws-special",
            "--validate-only",
            "--verbose",
        ]
    )
    assert args.window_days == 60
    assert args.window_end == "2026-04-01"
    assert args.prefer == "freshness"
    assert args.profile == "aws-special"
    assert args.validate_only is True
    assert args.verbose is True


def test_prefer_choices():
    with pytest.raises(SystemExit):
        parse_args(["--org", "a4x-us", "--prefer", "invalid"])


def test_additional_regions_flag_parses_list():
    args = parse_args(["--org", "a4x-us", "--additional-regions", "eu-central-1,ap-northeast-2"])
    assert args.additional_regions == "eu-central-1,ap-northeast-2"


def test_additional_regions_default_is_none():
    args = parse_args(["--org", "a4x-us"])
    assert args.additional_regions is None


def test_only_regions_flag_parses_list():
    args = parse_args(["--org", "a4x-us", "--only-regions", "us-east-1,eu-central-1"])
    assert args.only_regions == "us-east-1,eu-central-1"


def test_only_regions_default_is_none():
    args = parse_args(["--org", "a4x-us"])
    assert args.only_regions is None


def test_parse_exclude_flags():
    from scripts.aws_sp_optimizer import parse_args

    args = parse_args([
        "--org", "a4x-us",
        "--exclude-usage-type-pattern", "g4dn.",
        "--exclude-usage-type-pattern", "p4d.",
        "--exclude-account-id", "123456789012",
        "--exclude-account-id", "987654321098",
        "--exclude-tag", "lifecycle=ephemeral,experimental",
        "--exclude-tag", "cost-category=research",
        "--exclude-untagged",
    ])
    assert args.exclude_usage_type_pattern == ["g4dn.", "p4d."]
    assert args.exclude_account_id == ["123456789012", "987654321098"]
    assert args.exclude_tag == [
        "lifecycle=ephemeral,experimental",
        "cost-category=research",
    ]
    assert args.include_untagged is False


def test_default_include_untagged_is_none_so_config_wins():
    """MR !290 P1-1: argparse default must be None, not True, so that
    merge_cli_into_config_filter can tell 'user didn't pass the flag' apart
    from 'user explicitly asked for --include-untagged'. Without this, a
    config with include_untagged=false would be silently overwritten by the
    default-True argparse value."""
    from scripts.aws_sp_optimizer import parse_args
    args = parse_args(["--org", "a4x-us"])
    assert args.exclude_usage_type_pattern == []
    assert args.exclude_account_id == []
    assert args.exclude_tag == []
    assert args.include_untagged is None
