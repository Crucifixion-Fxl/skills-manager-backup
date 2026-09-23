import pytest
from scripts.exclude_filter import (
    validate_usage_type_pattern,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
    cur_tag_column_name,
)


@pytest.mark.parametrize("p", ["g4dn.", "p4d.24xlarge", "BoxUsage:m5.large", "ABC_123-x.y"])
def test_usage_type_pattern_accepts_safe(p):
    validate_usage_type_pattern(p)  # no raise


@pytest.mark.parametrize(
    "p", ["", "a" * 65, "has space", "has'quote", "has;semi", "has%percent", "a\\b"]
)
def test_usage_type_pattern_rejects_unsafe(p):
    with pytest.raises(ValueError):
        validate_usage_type_pattern(p)


@pytest.mark.parametrize("a", ["002497567426", "123456789012"])
def test_account_id_accepts_12_digits(a):
    validate_account_id(a)


@pytest.mark.parametrize("a", ["12345", "12345678901234", "abcdefghijkl", "002-497-567-426"])
def test_account_id_rejects_non_12_digits(a):
    with pytest.raises(ValueError):
        validate_account_id(a)


@pytest.mark.parametrize("k", ["lifecycle", "cost-category", "team:data", "env/stage"])
def test_tag_key_accepts_safe(k):
    validate_tag_key(k)


@pytest.mark.parametrize("k", ["", "has space", "has'quote", "a" * 129])
def test_tag_key_rejects_unsafe(k):
    with pytest.raises(ValueError):
        validate_tag_key(k)


@pytest.mark.parametrize("v", ["ephemeral", "prod", "team alpha", "ns/app"])
def test_tag_value_accepts_safe(v):
    validate_tag_value(v)


@pytest.mark.parametrize("v", ["", "has'quote", "has;semi", "a" * 257])
def test_tag_value_rejects_unsafe(v):
    with pytest.raises(ValueError):
        validate_tag_value(v)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("lifecycle", "resource_tags_user_lifecycle"),
        ("cost-category", "resource_tags_user_cost_category"),
        ("team:data", "resource_tags_user_team_data"),
        ("Env/Stage", "resource_tags_user_env_stage"),
        ("A.B.C", "resource_tags_user_a_b_c"),
    ],
)
def test_cur_tag_column_name(key, expected):
    assert cur_tag_column_name(key) == expected
