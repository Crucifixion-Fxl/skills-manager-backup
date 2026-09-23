import pytest
from scripts.exclude_filter import ExcludeFilter, TagExclusion


def test_tag_exclusion_frozen_and_hashable():
    t = TagExclusion(key="lifecycle", values=("ephemeral",))
    assert t.key == "lifecycle"
    assert t.values == ("ephemeral",)
    with pytest.raises((AttributeError, TypeError)):
        t.key = "other"
    # hashable: can be placed in a set
    assert {t, TagExclusion(key="lifecycle", values=("ephemeral",))} == {t}


def test_tag_exclusion_rejects_list_values():
    # values must be tuple to be hashable/frozen
    with pytest.raises(TypeError):
        TagExclusion(key="k", values=["v1"])  # type: ignore[arg-type]


def test_exclude_filter_defaults_empty():
    f = ExcludeFilter()
    assert f.usage_type_patterns == ()
    assert f.account_ids == ()
    assert f.tag_exclusions == ()
    assert f.include_untagged is True
    assert f.is_empty() is True


def test_exclude_filter_not_empty_when_any_axis_set():
    f1 = ExcludeFilter(usage_type_patterns=("g4dn.",))
    f2 = ExcludeFilter(account_ids=("123456789012",))
    f3 = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),)
    )
    assert f1.is_empty() is False
    assert f2.is_empty() is False
    assert f3.is_empty() is False


def test_exclude_filter_frozen_hashable():
    f = ExcludeFilter(usage_type_patterns=("g4dn.",))
    assert {f, ExcludeFilter(usage_type_patterns=("g4dn.",))} == {f}
    with pytest.raises((AttributeError, TypeError)):
        f.usage_type_patterns = ("x",)
