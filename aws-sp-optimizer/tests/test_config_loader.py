"""Tests for config_loader.load_config — happy path (Task 1.5) and error paths (Task 1.6)."""

from pathlib import Path

import pytest

from scripts._common import ConfigError, OrgConfig
from scripts.config_loader import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "sample_orgs.yaml"


def test_happy_path_returns_orgconfig(monkeypatch):
    # Make load_config read from the fixture instead of ~/.config/
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    c = load_config("a4x-us")
    assert isinstance(c, OrgConfig)
    assert c.alias == "a4x-us"
    assert c.profile == "aws-002497567426-us-tech-service"
    assert c.payer_account_id == "002497567426"
    assert c.org_id == "o-4bw6d7ou28"
    assert c.cur_database == "cur-db"
    assert c.cur_table == "a4x_report"
    assert c.athena_output == "s3://a4x-cur-query/sp-optimizer/"
    assert c.athena_workgroup == "primary"
    assert c.primary_region == "us-east-1"
    assert c.window_days == 90
    assert c.window_end == "today"
    assert c.prefer == "balanced"


def test_cli_overrides_drop_none_values(monkeypatch):
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    c = load_config(
        "a4x-us",
        cli_overrides={"window_days": 60, "window_end": None, "prefer": None},
    )
    assert c.window_days == 60  # overridden
    assert c.window_end == "today"  # None dropped — use config default
    assert c.prefer == "balanced"  # None dropped


def test_cli_overrides_replace_strings(monkeypatch):
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    c = load_config(
        "a4x-us",
        cli_overrides={"prefer": "freshness"},
    )
    assert c.prefer == "freshness"


def test_missing_file_raises_config_missing(monkeypatch, tmp_path):
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(missing))
    with pytest.raises(ConfigError) as excinfo:
        load_config("anything")
    assert excinfo.value.code == "config_missing"
    assert excinfo.value.status == "needs_setup"


def test_invalid_yaml_raises_config_invalid(monkeypatch, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("orgs:\n  -bad yaml: [unclosed\n")
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(bad))
    with pytest.raises(ConfigError) as excinfo:
        load_config("anything")
    assert excinfo.value.code == "config_invalid"


def test_missing_orgs_key_raises_schema_invalid(monkeypatch, tmp_path):
    bad = tmp_path / "noorgs.yaml"
    bad.write_text("schema_version: 1\n")
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(bad))
    with pytest.raises(ConfigError) as excinfo:
        load_config("anything")
    assert excinfo.value.code == "config_schema_invalid"


def test_unknown_alias_lists_available(monkeypatch):
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    with pytest.raises(ConfigError) as excinfo:
        load_config("nonexistent")
    assert excinfo.value.code == "org_alias_not_found"
    assert "a4x-us" in excinfo.value.context["available_aliases"]


def test_bad_window_end_surfaces_from_parse(monkeypatch, tmp_path):
    # Must be schema-valid so the jsonschema pass in load_config doesn't
    # short-circuit BEFORE parse_window_end runs. Only the window_end field
    # is deliberately bad.
    bad = tmp_path / "badwindow.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "orgs:\n"
        "  x:\n"
        "    profile: p\n"
        "    payer_account_id: '000000000000'\n"
        "    org_id: o-abc1234567\n"
        "    cur_database: d\n"
        "    cur_table: t\n"
        "    athena_output: s3://b/p/\n"
        "    defaults:\n"
        "      window_end: 04/13/2026\n"
    )
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(bad))
    with pytest.raises(ConfigError) as excinfo:
        load_config("x")
    assert excinfo.value.code == "config_invalid"
    assert "window_end" in excinfo.value.message


import textwrap


def test_config_loader_parses_exclude_block(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              usage_type_patterns: ["g4dn.", "p4d."]
              account_ids: ["123456789012"]
              tags:
                - key: lifecycle
                  values: [ephemeral, experimental]
              include_untagged: false
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))

    cfg = load_config("t")
    assert cfg.exclude.usage_type_patterns == ("g4dn.", "p4d.")
    assert cfg.exclude.account_ids == ("123456789012",)
    assert len(cfg.exclude.tag_exclusions) == 1
    assert cfg.exclude.tag_exclusions[0].key == "lifecycle"
    assert cfg.exclude.tag_exclusions[0].values == ("ephemeral", "experimental")
    assert cfg.exclude.include_untagged is False


def test_config_loader_defaults_exclude_to_empty(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))

    cfg = load_config("t")
    assert cfg.exclude.is_empty() is True


def test_config_loader_rejects_invalid_pattern(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              usage_type_patterns: ["has space"]
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))

    with pytest.raises(ConfigError):
        load_config("t")


def test_config_loader_rejects_non_mapping_exclude(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              - this is a list, not a mapping
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))
    with pytest.raises(ConfigError) as excinfo:
        load_config("t")
    assert "must be a mapping" in excinfo.value.message


def test_config_loader_rejects_non_bool_include_untagged(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              include_untagged: "yes"
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))
    with pytest.raises(ConfigError) as excinfo:
        load_config("t")
    assert "include_untagged" in excinfo.value.message
    assert "bool" in excinfo.value.message


def test_config_loader_rejects_non_list_tags(tmp_path, monkeypatch):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              tags: lifecycle
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))
    with pytest.raises(ConfigError) as excinfo:
        load_config("t")
    assert "tags must be a list" in excinfo.value.message


def test_config_loader_rejects_empty_tag_values(tmp_path, monkeypatch):
    """Guard P1-1: tag with empty values list would generate `NOT IN ()` SQL."""
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test123456
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              tags:
                - key: lifecycle
                  values: []
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(p))
    with pytest.raises(ConfigError) as excinfo:
        load_config("t")
    assert "empty values" in excinfo.value.message
