"""Tests for _retry_throttling and aws_error_to_blocker."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from scripts._common import _retry_throttling, aws_error_to_blocker


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "mock"}}, "DescribeThing")


def test_retry_succeeds_on_first_try():
    mock = MagicMock(return_value="result")
    assert _retry_throttling(mock) == "result"
    assert mock.call_count == 1


def test_retry_retries_on_throttling():
    mock = MagicMock()
    mock.side_effect = [_client_error("ThrottlingException"), "result"]
    assert _retry_throttling(mock, max_attempts=3) == "result"
    assert mock.call_count == 2


def test_retry_gives_up_after_max_attempts():
    mock = MagicMock(side_effect=_client_error("ThrottlingException"))
    with pytest.raises(ClientError):
        _retry_throttling(mock, max_attempts=2)
    assert mock.call_count == 2


def test_retry_reraises_non_throttling_immediately():
    mock = MagicMock(side_effect=_client_error("AccessDeniedException"))
    with pytest.raises(ClientError):
        _retry_throttling(mock, max_attempts=3)
    assert mock.call_count == 1


def test_aws_error_to_blocker_permissions():
    err = _client_error("AccessDeniedException")
    b = aws_error_to_blocker(err, "glue:GetTable", "my-profile")
    assert b.code == "permissions_missing"
    assert "my-profile" in b.message
    assert "glue:GetTable" in b.message
    assert b.context["operation"] == "glue:GetTable"
    assert b.user_fix_options


def test_aws_error_to_blocker_unspecified():
    err = _client_error("SomeOtherException")
    b = aws_error_to_blocker(err, "athena:StartQueryExecution", "p")
    assert b.code == "unspecified_aws_error"
    assert b.context["error_code"] == "SomeOtherException"
