"""Command-line wrapper over the closed Audience Sync operation registry."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from .client import MAX_REQUEST_BYTES, AudienceSyncClient, AudienceSyncConfig, SafeApiError
from .key_configuration import configured_keys, summarize_project_keys
from .operations import OPERATION_SPECS, Operation
from .project_operations import (
    PERSONAL_KEY_OPERATION_SPECS,
    PROJECT_CONTRACT_REVISION,
    PROJECT_OPERATION_SPECS,
    PersonalKeyOperation,
    ProjectOperation,
)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid arguments")


class _QueryValueTypeError(SafeApiError):
    """Local transport-shape error with a constant, value-free correction."""

    def __init__(self) -> None:
        super().__init__("invalid_query_parameters")


_KEY_ONBOARDING_HINT = "https://micro-app-platform-us.addx.live/audience-sync-us"


def _needs_key_onboarding(code: str, status: Optional[int]) -> bool:
    return status == 401 or (
        status is None and code in {"sync_api_key_missing", "invalid_sync_api_key"}
    )


def _parser(*, legacy: bool = False) -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="audience-sync-legacy-api" if legacy else "audience-sync-api")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("capabilities")
    if not legacy:
        subparsers.add_parser("summarize_project_keys")
    operations = OPERATION_SPECS if legacy else {
        **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS
    }
    for operation in operations:
        command = subparsers.add_parser(operation.value)
        command.add_argument("--request-stdin", action="store_true", required=True)
        command.add_argument("--key-alias")
    return parser


def project_capabilities() -> dict[str, Any]:
    """Public CLI inventory; SDK compatibility operations remain separate."""
    return {
        "contract_revision": PROJECT_CONTRACT_REVISION,
        "capability_source": "local_adapter",
        "operations": [
            operation.value
            for operation in (*PROJECT_OPERATION_SPECS, *PERSONAL_KEY_OPERATION_SPECS)
        ],
        "local_operations": ["summarize_project_keys"],
    }


def _request_from_stdin() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise SafeApiError("request_too_large")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("request must be JSON") from None
    if not isinstance(request, dict) or set(request) - {"path", "query", "body"}:
        raise ValueError("request must be an object")
    parts = []
    for name in ("path", "query", "body"):
        value = request.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"request {name} must be an object")
        parts.append(value)
    return parts[0], parts[1], parts[2]


def main(argv: Optional[list[str]] = None, *, legacy: bool = False) -> int:
    try:
        arguments = _parser(legacy=legacy).parse_args(argv)
        if arguments.operation == "capabilities":
            print(
                json.dumps(
                    {
                        "ok": True,
                        **(
                            AudienceSyncClient.local_capabilities()
                            if legacy else project_capabilities()
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if arguments.operation == "summarize_project_keys":
            summary = summarize_project_keys()
            payload = {"ok": True, "result": summary}
            if summary["keys"] and all(
                entry["status"] in {"error", "invalid"}
                and _needs_key_onboarding(entry.get("error", ""), entry.get("http_status"))
                for entry in summary["keys"]
            ):
                payload["hint"] = _KEY_ONBOARDING_HINT
            print(json.dumps(payload, sort_keys=True))
            return 0
        if arguments.operation in PersonalKeyOperation._value2member_map_:
            operation = PersonalKeyOperation(arguments.operation)
        elif arguments.operation in ProjectOperation._value2member_map_:
            operation = ProjectOperation(arguments.operation)
        else:
            operation = Operation(arguments.operation)
        path, query, body = _request_from_stdin()
        if any(not isinstance(value, str) for value in query.values()):
            raise _QueryValueTypeError
        config = AudienceSyncConfig.from_environment(
            key_alias=arguments.key_alias,
            project_operation=isinstance(operation, (ProjectOperation, PersonalKeyOperation)),
        )
        client = AudienceSyncClient(config)
        _, multiple = configured_keys()
        if isinstance(operation, ProjectOperation) and (multiple or not config.project_id):
            context = client.call(PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT)
            if "project_id" not in path:
                path = {**path, "project_id": context["project_id"]}
        result = client.call(
            operation,
            path=path,
            query=query,
            body=body,
        )
        print(json.dumps({"ok": True, "result": result}, sort_keys=True))
        return 0
    except (ValueError, json.JSONDecodeError):
        print(json.dumps({"error": "invalid_arguments", "ok": False}, sort_keys=True))
        return 2
    except SafeApiError as exc:
        error = {"error": exc.code, "ok": False}
        if not legacy and _needs_key_onboarding(exc.code, exc.status):
            error["hint"] = _KEY_ONBOARDING_HINT
        if isinstance(exc, _QueryValueTypeError):
            error["hint"] = (
                'CLI query values must be strings, e.g. {"limit":"100"}. '
                'Keep body values in their schema-defined JSON types.'
            )
        if type(exc.status) is int and 100 <= exc.status <= 599:
            error["http_status"] = exc.status
        print(json.dumps(error, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
