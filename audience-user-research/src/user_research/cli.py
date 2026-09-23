"""One bounded JSON-stdin bridge over the closed User Research API registry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .client import (
    MAX_REQUEST_BYTES,
    AudienceClient,
    AudienceClientConfig,
    SafeApiError,
    attachment_output_name,
)
from .environment import load_local_environment
from .operations import (
    HOST_ATTACHMENT_OPERATIONS,
    OPERATION_SPECS,
    PROJECT_OPERATIONS,
    SELF_CONTEXT_OPERATION,
    Operation,
)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="user-research-api")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("capabilities")
    for operation in OPERATION_SPECS:
        command = commands.add_parser(operation.value)
        command.add_argument("--request-stdin", action="store_true", required=True)
        if operation in HOST_ATTACHMENT_OPERATIONS:
            command.add_argument("--output", required=True)
    return parser


def _request_from_stdin() -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise SafeApiError("request_too_large")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_arguments") from None
    if not isinstance(request, dict) or set(request) - {"path", "query", "body"}:
        raise ValueError("invalid_arguments")
    parts: list[dict[str, Any]] = []
    for name in ("path", "query", "body"):
        value = request.get(name, {})
        if not isinstance(value, dict):
            raise ValueError("invalid_arguments")
        parts.append(value)
    path, query, body = parts
    if any(not isinstance(value, str) for value in path.values()):
        raise SafeApiError("invalid_path_parameters")
    if any(not isinstance(value, str) for value in query.values()):
        raise SafeApiError("invalid_query_parameters")
    return path, query, body


_DROPPED_RESULT_KEYS = frozenset(
    {
        "anonymous_email",
        "contact_email",
        "email",
        "forwarding_address",
        "personalized_url",
        "profile",
        "provider_payload",
        "recipient",
        "recipient_email",
        "recipients",
        "relay_email",
        "survey_url",
        "uid",
        "user_email",
    }
)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ENCODED_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._+-]+%(?:25)*40"
    r"(?:[A-Za-z0-9-]+(?:\.|%(?:25)*2[eE]))+[A-Za-z]{2,}",
    re.IGNORECASE,
)


def _decode_until_stable(value: str) -> str:
    current = value
    for _ in range(len(value) + 1):
        decoded = unquote(current)
        if decoded == current or len(decoded) > len(current):
            return current
        current = decoded
    return current


def _url_carries_uid(value: str) -> bool:
    lowered = value.lower()
    if "#uid=" in lowered or "?uid=" in lowered or "&uid=" in lowered:
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    names = {key.lower() for key in parse_qs(parsed.query, keep_blank_values=True)}
    fragment = parsed.fragment
    fragment_query = fragment.split("?", 1)[-1] if "?" in fragment else fragment
    names.update(key.lower() for key in parse_qs(fragment_query, keep_blank_values=True))
    return "uid" in names


def _is_per_user_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return _url_carries_uid(value) or _url_carries_uid(_decode_until_stable(value))


def _has_email(value: str) -> bool:
    if _EMAIL.search(value) is not None or _ENCODED_EMAIL.search(value) is not None:
        return True
    decoded = _decode_until_stable(value)
    return decoded != value and _EMAIL.search(decoded) is not None


def _scrub_text(value: str) -> str:
    if _is_per_user_url(value):
        return ""
    scrubbed = _ENCODED_EMAIL.sub("", _EMAIL.sub("", value))
    decoded = _decode_until_stable(scrubbed)
    if decoded != scrubbed and (_EMAIL.search(decoded) or _url_carries_uid(decoded)):
        return ""
    return scrubbed


def _omit_scrubbed_text(original: Any, scrubbed: Any) -> bool:
    if not isinstance(original, str):
        return False
    if _is_per_user_url(original):
        return True
    if not _has_email(original):
        return False
    return isinstance(scrubbed, str) and not scrubbed.strip()


def _scrub_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        per_user_link = "uid" in value and "url" in value
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROPPED_RESULT_KEYS:
                continue
            if key == "url" and (per_user_link or _is_per_user_url(item)):
                continue
            if _is_per_user_url(item):
                continue
            cleaned = _scrub_sensitive(item)
            if _omit_scrubbed_text(item, cleaned):
                continue
            scrubbed[key] = cleaned
        return scrubbed
    if isinstance(value, list):
        cleaned_items = []
        for entry in value:
            cleaned = _scrub_sensitive(entry)
            if cleaned == {} or _omit_scrubbed_text(entry, cleaned):
                continue
            cleaned_items.append(cleaned)
        return cleaned_items
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _project_cli_result(
    _operation: Operation, result: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    return _scrub_sensitive(result)


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        load_local_environment(Path(__file__).resolve().parents[2] / ".env.local")
        arguments = _parser().parse_args(argv)
        if arguments.operation == "capabilities":
            _emit(
                {
                    "ok": True,
                    "capability_source": "bundled_contracts",
                    "operations": [operation.value for operation in OPERATION_SPECS],
                }
            )
            return 0
        operation = Operation(arguments.operation)
        path, query, body = _request_from_stdin()
        client = AudienceClient(AudienceClientConfig.from_environment())
        if operation == SELF_CONTEXT_OPERATION:
            result = client.verify_self_context()
        else:
            context = client.verify_self_context()
            if operation in PROJECT_OPERATIONS:
                supplied = path.get("project_id")
                if supplied not in (None, context["project_id"]):
                    raise SafeApiError("project_binding_mismatch")
                path = {**path, "project_id": context["project_id"]}
            if operation in HOST_ATTACHMENT_OPERATIONS:
                if body:
                    raise SafeApiError("invalid_request_schema")
                result = client.download(
                    operation,
                    attachment_output_name(arguments.output),
                    path=path,
                    query=query,
                )
            else:
                result = client.call(
                    operation,
                    path=path,
                    query=query,
                    body=body or None,
                )
        _emit({"ok": True, "result": _project_cli_result(operation, result)})
        return 0
    except ValueError:
        _emit({"error": "invalid_arguments", "ok": False})
        return 2
    except SafeApiError as exc:
        error: dict[str, Any] = {"error": exc.code, "ok": False}
        if type(exc.status) is int and 100 <= exc.status <= 599:
            error["http_status"] = exc.status
        _emit(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
