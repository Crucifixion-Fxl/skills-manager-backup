from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    build_exclude_sql_clauses,
)


def test_empty_filter_emits_no_clauses():
    clauses, warnings = build_exclude_sql_clauses(ExcludeFilter(), frozenset())
    assert clauses == []
    assert warnings == []


def test_usage_type_patterns_emit_not_like_clauses():
    f = ExcludeFilter(usage_type_patterns=("g4dn.", "p4d."))
    clauses, warnings = build_exclude_sql_clauses(f, frozenset())
    assert clauses == [
        "line_item_usage_type NOT LIKE '%g4dn.%'",
        "line_item_usage_type NOT LIKE '%p4d.%'",
    ]
    assert warnings == []


def test_account_ids_emit_single_not_in_clause():
    f = ExcludeFilter(account_ids=("123456789012", "987654321098"))
    clauses, _ = build_exclude_sql_clauses(f, frozenset())
    assert clauses == [
        "line_item_usage_account_id NOT IN ('123456789012', '987654321098')"
    ]


def test_multiple_axes_all_appear():
    f = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    clauses, _ = build_exclude_sql_clauses(f, frozenset())
    assert "line_item_usage_type NOT LIKE '%g4dn.%'" in clauses
    assert "line_item_usage_account_id NOT IN ('123456789012')" in clauses
    assert len(clauses) == 2


def test_tag_filter_with_include_untagged_true():
    f = ExcludeFilter(
        tag_exclusions=(
            TagExclusion(key="lifecycle", values=("ephemeral", "experimental")),
        ),
        include_untagged=True,
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert clauses == [
        "(resource_tags_user_lifecycle IS NULL OR "
        "resource_tags_user_lifecycle NOT IN ('ephemeral', 'experimental'))"
    ]
    assert warnings == []


def test_tag_filter_with_include_untagged_false():
    f = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),),
        include_untagged=False,
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, _ = build_exclude_sql_clauses(f, cols)
    assert clauses == [
        "resource_tags_user_lifecycle IS NOT NULL",
        "resource_tags_user_lifecycle NOT IN ('ephemeral')",
    ]


def test_tag_filter_skips_missing_column_and_warns():
    f = ExcludeFilter(
        tag_exclusions=(
            TagExclusion(key="lifecycle", values=("ephemeral",)),
            TagExclusion(key="nonexistent", values=("x",)),
        ),
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert len(clauses) == 1
    assert "resource_tags_user_lifecycle" in clauses[0]
    assert len(warnings) == 1
    assert "nonexistent" in warnings[0]
    assert "resource_tags_user_nonexistent" in warnings[0]


def test_tag_key_is_normalized_before_column_lookup():
    f = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="cost-category", values=("research",)),),
    )
    cols = frozenset({"resource_tags_user_cost_category"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert len(clauses) == 1
    assert "resource_tags_user_cost_category" in clauses[0]
    assert warnings == []
