"""Safe stdlib transport for the closed Audience Personal API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from string import Formatter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .contract_validation import (
    ContractViolation,
    attachment_content_types,
    response_declares_property,
    validate_operation_path,
    validate_operation_query,
    validate_operation_request,
    validate_operation_response,
)
from .operations import (
    HOST_ATTACHMENT_OPERATIONS,
    OPERATION_SPECS,
    PROJECT_OPERATIONS,
    SELF_CONTEXT_OPERATION,
    Operation,
)

MAX_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 32 * 1024
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_ATTACHMENT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_AUDIENCE_PLATFORM_BASE_URL = "https://audience-workflow-api-prod-us.addx.live"
ALLOWED_AUDIENCE_HOSTS = frozenset(
    {
        "audience-workflow-api-prod-us.addx.live",
        "audience-workflow-api-staging-us.addx.live",
    }
)
_PERSONAL_API_KEY = re.compile(r"^awpk_v2_[a-z2-7]{26}_[A-Za-z0-9_-]{43}$", re.ASCII)
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_SAFE_ATTACHMENT_NAME = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._-]{0,127}$")
_ATTACHMENT_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_ATTACHMENT_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_RETRYABLE_OPERATIONS: frozenset[Operation] = frozenset(
    operation for operation, spec in OPERATION_SPECS.items() if spec.method == "GET"
)
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "transport_error",
        "project_binding_revision_stale",
    }
)


class SafeApiError(Exception):
    """A code-only error safe for a host to surface or record."""

    def __init__(
        self,
        code: str,
        status: int | None = None,
        *,
        classification: str | None = None,
        guidance: str | None = None,
        recovery: str | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.classification = classification
        self.guidance = guidance
        self.recovery = recovery
        super().__init__(code)


def attachment_output_name(output_path: os.PathLike[str] | str) -> str:
    """Reject absolute paths, traversal and separator-bearing attachment names."""
    name = os.fspath(output_path)
    if not name or "\x00" in name:
        raise SafeApiError("invalid_output_path")
    if os.path.isabs(name) or name.startswith("~"):
        raise SafeApiError("invalid_output_path")
    separators = {"/", "\\", os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if any(separator in name for separator in separators):
        raise SafeApiError("invalid_output_path")
    if name in {".", ".."} or ".." in name:
        raise SafeApiError("invalid_output_path")
    if _SAFE_ATTACHMENT_NAME.fullmatch(name) is None:
        raise SafeApiError("invalid_output_path")
    return name


def _open_attachment_dir(attachment_dir: str) -> int:
    if not attachment_dir or "\x00" in attachment_dir or not os.path.isabs(attachment_dir):
        raise SafeApiError("invalid_attachment_dir")
    try:
        return os.open(attachment_dir, _ATTACHMENT_DIR_FLAGS)
    except OSError:
        raise SafeApiError("invalid_attachment_dir") from None


@dataclass(frozen=True)
class AudienceClientConfig:
    base_url: str = DEFAULT_AUDIENCE_PLATFORM_BASE_URL
    personal_api_key: str = field(default="", repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    attachment_timeout_seconds: float = DEFAULT_ATTACHMENT_TIMEOUT_SECONDS
    allow_localhost_http: bool = False
    project_id: str = ""
    attachment_dir: str = ""

    @classmethod
    def from_environment(cls) -> AudienceClientConfig:
        try:
            timeout = float(
                os.environ.get("AUDIENCE_PLATFORM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            )
            attachment_timeout = float(
                os.environ.get(
                    "AUDIENCE_PLATFORM_ATTACHMENT_TIMEOUT_SECONDS",
                    str(DEFAULT_ATTACHMENT_TIMEOUT_SECONDS),
                )
            )
        except ValueError:
            raise SafeApiError("invalid_timeout") from None
        return cls(
            base_url=os.environ.get(
                "AUDIENCE_PLATFORM_BASE_URL", DEFAULT_AUDIENCE_PLATFORM_BASE_URL
            ),
            personal_api_key=os.environ.get("AUDIENCE_API_KEY", ""),
            timeout_seconds=timeout,
            attachment_timeout_seconds=attachment_timeout,
            allow_localhost_http=os.environ.get("AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS") == "1",
            project_id=os.environ.get("AUDIENCE_PROJECT_ID", ""),
            attachment_dir=os.environ.get("AUDIENCE_ATTACHMENT_DIR", ""),
        )


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> Request | None:
        raise SafeApiError("redirect_rejected")


class AudienceClient:
    """A typed, fixed-operation client; origin and credential stay host-owned."""

    def __init__(self, config: AudienceClientConfig) -> None:
        self._config = config
        self._verified_context: dict[str, Any] | None = None
        self._origin = _validate_origin(config)
        self._opener = build_opener(
            _RejectRedirects(), HTTPSHandler(context=ssl.create_default_context())
        )

    def capabilities(self) -> frozenset[Operation]:
        return frozenset(OPERATION_SPECS)

    @property
    def verified_context(self) -> dict[str, Any] | None:
        return dict(self._verified_context) if self._verified_context is not None else None

    def verify_self_context(self) -> dict[str, Any]:
        response = self.call(SELF_CONTEXT_OPERATION)
        if not isinstance(response, dict):
            raise SafeApiError("invalid_response_schema")
        return response

    def call(
        self,
        operation: Operation,
        /,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Call one registered operation. ``operation`` cannot select a method or URL."""
        if operation in HOST_ATTACHMENT_OPERATIONS:
            raise SafeApiError("attachment_output_required")
        if operation != SELF_CONTEXT_OPERATION and self._verified_context is None:
            raise SafeApiError("self_context_required")
        try:
            spec = OPERATION_SPECS[operation]
        except KeyError:
            raise SafeApiError("operation_not_allowed") from None
        _encode_body(body)
        try:
            validated_body = validate_operation_request(
                operation, dict(body) if body is not None else None
            )
        except ContractViolation:
            raise SafeApiError("invalid_request_schema") from None
        if self._verified_context is not None:
            granted = set(self._verified_context["allowed_actions"])
            if not _required_actions(operation, spec.required_action, validated_body).issubset(
                granted
            ):
                raise SafeApiError("operation_not_granted")
        path_values = dict(path or {})
        if operation in PROJECT_OPERATIONS and self._verified_context is not None:
            verified_project = self._verified_context["project_id"]
            supplied_project = path_values.get("project_id")
            if supplied_project not in (None, verified_project):
                raise SafeApiError("project_binding_mismatch")
            path_values["project_id"] = verified_project
        query_values = dict(query or {})
        try:
            validate_operation_path(operation, path_values)
        except ContractViolation:
            raise SafeApiError("invalid_path_parameters") from None
        try:
            validate_operation_query(operation, query_values)
        except ContractViolation:
            raise SafeApiError("invalid_query_parameters") from None
        rendered_path = _render_path(spec.path, path_values) + _render_query(
            spec.query, query_values
        )
        response = self._call_with_read_retry(operation, spec.method, rendered_path, validated_body)
        try:
            validated_response = validate_operation_response(operation, response)
            self._validate_response_binding(
                operation, path_values, validated_body, validated_response
            )
            if operation == SELF_CONTEXT_OPERATION:
                assert isinstance(validated_response, dict)
                actions = validated_response["allowed_actions"]
                if actions != sorted(set(actions)):
                    raise ContractViolation
                if (
                    self._config.project_id
                    and validated_response["project_id"] != self._config.project_id
                ):
                    raise SafeApiError("project_binding_mismatch")
                self._verified_context = validated_response
            return validated_response
        except ContractViolation:
            raise SafeApiError("invalid_response_schema") from None

    def download(
        self,
        operation: Operation,
        output_path: os.PathLike[str] | str,
        /,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Download one reviewed attachment without returning its bytes to the caller."""
        if operation not in HOST_ATTACHMENT_OPERATIONS:
            raise SafeApiError("operation_not_attachment")
        spec = OPERATION_SPECS[operation]
        if self._verified_context is None:
            raise SafeApiError("self_context_required")
        granted = set(self._verified_context["allowed_actions"])
        if spec.required_action and spec.required_action not in granted:
            raise SafeApiError("operation_not_granted")
        path_values = dict(path or {})
        verified_project = self._verified_context["project_id"]
        supplied_project = path_values.get("project_id")
        if supplied_project not in (None, verified_project):
            raise SafeApiError("project_binding_mismatch")
        path_values["project_id"] = verified_project
        query_values = dict(query or {})
        try:
            validate_operation_path(operation, path_values)
            validate_operation_query(operation, query_values)
        except ContractViolation:
            raise SafeApiError("invalid_attachment_parameters") from None
        rendered_path = _render_path(spec.path, path_values) + _render_query(
            spec.query, query_values
        )
        name = attachment_output_name(output_path)
        dir_fd = _open_attachment_dir(self._config.attachment_dir)
        try:
            content_types = attachment_content_types(operation)
        except ContractViolation:
            os.close(dir_fd)
            raise SafeApiError("invalid_attachment_contract") from None
        try:
            return self._download_request(
                spec.method,
                rendered_path,
                dir_fd,
                name,
                content_types,
            )
        finally:
            os.close(dir_fd)

    def _download_request(
        self,
        method: str,
        path: str,
        dir_fd: int,
        name: str,
        content_types: frozenset[str],
    ) -> dict[str, Any]:
        request = Request(
            f"{self._origin}{path}",
            method=method,
            headers={
                "Authorization": f"Bearer {self._config.personal_api_key}",
                "Accept": ", ".join(sorted(content_types)),
            },
        )
        raw, content_type = self._read_attachment(request, content_types)
        temporary = "." + os.urandom(8).hex() + ".partial"
        try:
            descriptor = os.open(temporary, _ATTACHMENT_FILE_FLAGS, 0o600, dir_fd=dir_fd)
        except OSError:
            raise SafeApiError("output_write_failed") from None
        try:
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                raise SafeApiError("output_write_failed") from None
            try:
                os.link(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except FileExistsError:
                raise SafeApiError("output_exists") from None
            except OSError:
                raise SafeApiError("output_write_failed") from None
        finally:
            try:
                os.unlink(os.path.join(self._config.attachment_dir, temporary))
            except OSError:
                pass
        return {
            "content_type": content_type,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _read_attachment(
        self, request: Request, content_types: frozenset[str]
    ) -> tuple[bytes, str]:
        last_error = SafeApiError("transport_error")
        for attempt in range(2):
            try:
                return self._open_attachment(request, content_types)
            except SafeApiError as exc:
                last_error = exc
                retryable = exc.code in _RETRYABLE_ERROR_CODES or exc.status in _RETRYABLE_STATUSES
                if attempt == 1 or not retryable:
                    raise
        raise last_error

    def _open_attachment(
        self, request: Request, content_types: frozenset[str]
    ) -> tuple[bytes, str]:
        try:
            with self._opener.open(
                request, timeout=self._config.attachment_timeout_seconds
            ) as response:
                content_type = response.headers.get_content_type()
                if content_type not in content_types:
                    raise SafeApiError("invalid_attachment_content_type")
                raw = response.read(MAX_ATTACHMENT_BYTES + 1)
        except SafeApiError:
            raise
        except HTTPError as exc:
            raise _project_http_error(exc) from None
        except (URLError, TimeoutError, OSError):
            raise SafeApiError("transport_error") from None
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise SafeApiError("response_too_large")
        return raw, content_type

    def _validate_response_binding(
        self,
        operation: Operation,
        path: Mapping[str, str],
        body: Mapping[str, Any] | None,
        response: dict[str, Any] | None,
    ) -> None:
        if operation not in PROJECT_OPERATIONS or not isinstance(response, dict):
            return
        if response.get("project_id") != path.get("project_id"):
            raise ContractViolation
        if self._verified_context is not None:
            if response.get("binding_revision") != self._verified_context["binding_revision"]:
                raise ContractViolation
        resource = response.get("resource")
        if not isinstance(resource, dict):
            resource = {}
        for identifier in (
            "idea_id",
            "research_id",
            "source_materialization_run_id",
            "operation",
        ):
            expected = path.get(identifier)
            if expected is None and body is not None:
                expected = body.get(identifier)
            observed = response.get(identifier, resource.get(identifier))
            if expected is None or observed == expected:
                continue
            if response_declares_property(operation, identifier) or observed is not None:
                raise ContractViolation
        if operation.value in {
            "personal_action_configuration_get",
            "personal_action_configuration_put",
        }:
            if resource.get("idea_id") != path.get("idea_id"):
                raise ContractViolation
            if resource.get("kind") != path.get("action_kind"):
                raise ContractViolation
        if operation.value == "personal_voc_execution_start":
            if response.get("idea_id") != path.get("idea_id"):
                raise ContractViolation
        if operation.value == "personal_voc_execution_get":
            if response.get("idea_id") != path.get("idea_id"):
                raise ContractViolation
            if response.get("platform_run_id") != path.get("platform_run_id"):
                raise ContractViolation
        if operation.value in {"project_apify_actor_detail", "project_apify_actor_schema"}:
            if resource.get("actor_id") != path.get("actor_id"):
                raise ContractViolation
        if operation.value == "project_voc_native_start" and body is not None:
            if response.get("idea_id") != path.get("idea_id"):
                raise ContractViolation
            for identifier in ("actor_id", "build", "input"):
                if response.get(identifier) != body.get(identifier):
                    raise ContractViolation
        if operation.value == "project_voc_native_read":
            if response.get("idea_id") != path.get("idea_id"):
                raise ContractViolation
            if response.get("request_id") != path.get("request_id"):
                raise ContractViolation
        if operation.value in {"project_voc_dataset_metadata", "project_voc_dataset_items"}:
            if resource.get("dataset_id") != path.get("dataset_id"):
                raise ContractViolation
        if operation.value == "personal_research_journey_create" and body is not None:
            expected_idea_id = body.get("idea_id")
            if expected_idea_id is not None and response.get("idea_id") != expected_idea_id:
                raise ContractViolation
        if operation.value == "personal_research_journey_operation" and body is not None:
            for identifier in ("expected_count", "source_materialization_run_id"):
                expected = body.get(identifier)
                if expected is not None and response.get(identifier) != expected:
                    raise ContractViolation
        if operation.value == "personal_research_journey_campaign_draft" and body is not None:
            if response.get("source_materialization_run_id") != body.get(
                "source_materialization_run_id"
            ):
                raise ContractViolation

    def _call_with_read_retry(
        self,
        operation: Operation,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        attempts = 2 if operation in _RETRYABLE_OPERATIONS else 1
        for attempt in range(attempts):
            try:
                return self._request(method, path, body)
            except SafeApiError as exc:
                retryable = exc.code in _RETRYABLE_ERROR_CODES or exc.status in _RETRYABLE_STATUSES
                if attempt + 1 == attempts or not retryable:
                    raise
        raise SafeApiError("transport_error")

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        data = _encode_body(body)
        request = Request(
            f"{self._origin}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._config.personal_api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._config.timeout_seconds) as response:
                return _decode_response(response.read(MAX_BYTES + 1))
        except SafeApiError:
            raise
        except HTTPError as exc:
            raise _project_http_error(exc) from None
        except (URLError, TimeoutError, OSError):
            raise SafeApiError("transport_error") from None


def _validate_origin(config: AudienceClientConfig) -> str:
    parsed = urlsplit(config.base_url)
    try:
        port = parsed.port
    except ValueError:
        raise SafeApiError("invalid_origin") from None
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise SafeApiError("invalid_origin")
    localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    test_localhost = config.allow_localhost_http and localhost and parsed.scheme == "http"
    audience_https = (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_AUDIENCE_HOSTS
        and port in (None, 443)
    )
    if not test_localhost and not audience_https:
        if parsed.scheme != "https":
            raise SafeApiError("https_required")
        raise SafeApiError("origin_not_allowed")
    if not config.personal_api_key:
        raise SafeApiError("personal_api_key_missing")
    if _PERSONAL_API_KEY.fullmatch(config.personal_api_key) is None:
        raise SafeApiError("invalid_personal_api_key")
    if not 0 < config.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise SafeApiError("invalid_timeout")
    if not 0 < config.attachment_timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise SafeApiError("invalid_timeout")
    return f"{parsed.scheme}://{parsed.netloc}"


def _required_actions(
    operation: Operation,
    configured: str | None,
    body: Mapping[str, Any] | None,
) -> frozenset[str]:
    required = {configured} if configured is not None else set()
    if operation.value == "personal_research_journey_operation" and body is not None:
        if body.get("operation") == "sync_brevo":
            required.add("audience_sync.request")
    return frozenset(required)


def _render_path(template: str, values: Mapping[str, str] | None) -> str:
    values = values or {}
    expected = {
        field_name for _, field_name, _, _ in Formatter().parse(template) if field_name is not None
    }
    if (
        set(values) != expected
        or any(not value for value in values.values())
        or any(_has_traversal_segment(value) for value in values.values())
    ):
        raise SafeApiError("invalid_path_parameters")
    try:
        rendered = template.format(**{key: quote(value, safe="") for key, value in values.items()})
    except (KeyError, TypeError, ValueError):
        raise SafeApiError("invalid_path_parameters") from None
    if (
        "{" in rendered
        or "}" in rendered
        or not rendered.startswith("/")
        or "//" in rendered
        or "?" in rendered
    ):
        raise SafeApiError("invalid_path_parameters")
    return rendered


def _has_traversal_segment(value: str) -> bool:
    candidate = value
    for _ in range(3):
        if any(segment in {".", ".."} for segment in candidate.replace("\\", "/").split("/")):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            return False
        candidate = decoded
    return any(segment in {".", ".."} for segment in candidate.replace("\\", "/").split("/"))


def _render_query(expected: tuple[str, ...], values: Mapping[str, str] | None) -> str:
    values = values or {}
    if not set(values).issubset(expected):
        raise SafeApiError("invalid_query_parameters")
    if not values:
        return ""
    return "?" + urlencode([(name, values[name]) for name in expected if name in values])


def _encode_body(body: Mapping[str, Any] | None) -> bytes | None:
    if body is None:
        return None
    try:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise SafeApiError("invalid_request_body") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise SafeApiError("request_too_large")
    return encoded


def _decode_response(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_BYTES:
        raise SafeApiError("response_too_large")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SafeApiError("invalid_response") from None
    if not isinstance(value, dict):
        raise SafeApiError("invalid_response")
    return value


def _project_http_error(error: HTTPError) -> SafeApiError:
    code = "http_error"
    try:
        raw = error.read(MAX_BYTES + 1)
        if len(raw) <= MAX_BYTES:
            payload = json.loads(raw)
            detail = payload.get("detail") if isinstance(payload, dict) else None
            candidate = detail.get("code") if isinstance(detail, dict) else None
            if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate):
                code = candidate
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    finally:
        error.close()
    return SafeApiError(code, error.code)
