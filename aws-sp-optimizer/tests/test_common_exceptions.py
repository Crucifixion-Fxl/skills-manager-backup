"""Tests for the SPOptimizerError hierarchy in scripts._common."""

from scripts._common import (
    AthenaError,
    CacheError,
    ConfigError,
    NoDataError,
    SPOptimizerError,
)


def test_base_exception_carries_structured_payload():
    e = SPOptimizerError(
        code="oops",
        message="something broke",
        context={"key": "value"},
        llm_next_action="do the thing",
        user_fix_options=["option_a", "option_b"],
    )
    payload = e.to_output_dict()
    assert payload["status"] == "aws_api_error"
    assert payload["phase"] == "unknown"
    assert payload["error_code"] == "oops"
    assert payload["error_message"] == "something broke"
    assert payload["context"] == {"key": "value"}
    assert payload["llm_next_action"] == "do the thing"
    # Regression (L8): needs_setup / needs_validation_fix / validation_*
    # / discovery_result outputs need `instruction_for_llm` for their
    # respective INVs (8/11/12/13). SPOptimizerError.to_output_dict()
    # emits it as an alias of llm_next_action.
    assert payload["instruction_for_llm"] == "do the thing"
    assert payload["user_fix_options"] == ["option_a", "option_b"]


def test_config_error_output_includes_instruction_for_llm():
    """L8 regression: ConfigError → needs_setup path must satisfy INV-8
    without hitting the invariant fallback in main()."""
    e = ConfigError(
        code="config_missing",
        message="no file",
        llm_next_action="READ references/first-run-setup.md",
    )
    payload = e.to_output_dict()
    assert payload["status"] == "needs_setup"
    assert payload["instruction_for_llm"] == "READ references/first-run-setup.md"


def test_instruction_for_llm_falls_back_when_llm_next_action_empty():
    """L8 regression (edge case): when a caller raises SPOptimizerError
    WITHOUT passing llm_next_action, instruction_for_llm must still be
    non-empty so INV-8 / INV-11 / INV-12 / INV-13 pass.

    Real-world case: Task 5.9's main() tests raise `ConfigError(code=..., message=...)`
    without llm_next_action; without the synthesis below they'd fall back to
    script_bug even though needs_setup was the intended status."""
    e = ConfigError(code="config_missing", message="no file")
    payload = e.to_output_dict()
    assert payload["instruction_for_llm"]  # truthy (non-empty)
    assert "config_missing" in payload["instruction_for_llm"]
    assert "no file" in payload["instruction_for_llm"]


def test_config_error_status_and_phase():
    e = ConfigError(code="config_missing", message="no file")
    assert e.status == "needs_setup"
    assert e.phase == "config"


def test_cache_error_status_and_phase():
    e = CacheError(code="pricing_api_unreachable", message="network down")
    assert e.status == "aws_api_error"
    assert e.phase == "pricing_cache"


def test_athena_error_status_and_phase():
    e = AthenaError(code="query_timeout", message="too slow")
    assert e.status == "aws_api_error"
    assert e.phase == "cur_query"


def test_nodata_error_status_and_phase():
    e = NoDataError(code="no_matched_rows_for_d", message="nothing")
    assert e.status == "aws_api_error"
    assert e.phase == "cur_query"


def test_positional_args_forbidden():
    # Regression: R17-2. SPOptimizerError.__init__ requires code/message as kwargs
    # (or the first two positional args). Passing just one positional should TypeError.
    import pytest

    with pytest.raises(TypeError):
        NoDataError("only one arg")  # type: ignore[call-arg]
