"""Tests for discover_orgs.main — JSON emission and exit codes."""

import json
from unittest.mock import patch

import pytest


def test_main_emits_discovery_result(capsys):
    from scripts import discover_orgs

    with patch.object(discover_orgs, "discover_all") as mock_discover:
        mock_discover.return_value = {
            "profiles_checked": 2,
            "profiles_with_errors": [],
            "discovered_orgs": [
                {"profile": "a", "can_use": True, "account_id": "111111111111"},
            ],
        }
        with pytest.raises(SystemExit) as excinfo:
            discover_orgs.main()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["schema_version"] == 1
    assert output["status"] == "discovery_result"
    assert output["profiles_checked"] == 2
    assert len(output["discovered_orgs"]) == 1
    assert "instruction_for_llm" in output
    assert output["instruction_for_llm"]


def test_main_catches_uncaught_exception(capsys):
    from scripts import discover_orgs

    with patch.object(discover_orgs, "discover_all") as mock_discover:
        mock_discover.side_effect = RuntimeError("boom")
        with pytest.raises(SystemExit) as excinfo:
            discover_orgs.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["status"] == "script_bug"
    assert output["error_type"] == "RuntimeError"
    assert "boom" in output["error_message"]
