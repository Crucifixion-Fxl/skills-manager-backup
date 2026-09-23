"""Safe stdlib transport for the closed Audience Sync Personal API."""

from __future__ import annotations

import json
import math
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .contract_validation import (
    ContractViolation,
    validate_audience_filter,
    validate_operation_path,
    validate_operation_query,
    validate_operation_request,
    validate_operation_response,
)
from .operations import CONTRACT_REVISION, CONTRACT_STATE, OPERATION_SPECS, Operation
from .project_operations import (
    PERSONAL_KEY_OPERATION_SPECS,
    PROJECT_CONTRACT_REVISION,
    PROJECT_OPERATION_SPECS,
    PersonalKeyOperation,
    ProjectOperation,
)

MAX_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 32 * 1024
MAX_TIMEOUT_SECONDS = 30.0
PROJECT_TIMEOUT_SECONDS = 45.0
DEFAULT_AUDIENCE_PLATFORM_BASE_URL = "https://audience-workflow-api-prod-us.addx.live"
DEFAULT_AUDIENCE_SYNC_CONTRACT_REVISION = CONTRACT_REVISION
_PERSONAL_API_KEY = re.compile(
    r"^awpk_v1_[a-z2-7]{26}_[A-Za-z0-9_-]{43}$",
    re.ASCII,
)
_PROJECT_API_KEY = re.compile(r"^awpk_v2_[a-z2-7]{26}_[A-Za-z0-9_-]{43}$", re.ASCII)
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_UNSAFE_RESPONSE_KEY = re.compile(
    r"^(?:authorization|token|secret|password|recipient(?:s|_.*)?|members?|"
    r"contacts?|rows?|emails?|phones?|(?:tenant|user|actor|owner|open|union|"
    r"contact|subject|member|folder|list)_id|list_name|sql|provider_payload|"
    r"payload|headers?|url|uri|hash_email)$",
    re.IGNORECASE,
)


class SafeApiError(Exception):
    """A code-only error safe for a host to surface or record."""

    def __init__(self, code: str, status: Optional[int] = None) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class AudienceSyncConfig:
    base_url: str
    sync_api_key: str = field(repr=False)
    contract_revision: str
    timeout_seconds: float = 10.0
    allow_localhost_http: bool = False
    project_id: str = ""

    @classmethod
    def from_environment(cls, *, key_alias=None, project_operation=False) -> AudienceSyncConfig:
        from .key_configuration import select_key

        key = select_key(key_alias)
        project_id = os.environ.get("AUDIENCE_PROJECT_ID", "")
        revision = os.environ.get(
            "AUDIENCE_SYNC_CONTRACT_REVISION",
            PROJECT_CONTRACT_REVISION
            if project_id or project_operation
            else DEFAULT_AUDIENCE_SYNC_CONTRACT_REVISION,
        )
        default_timeout = PROJECT_TIMEOUT_SECONDS if revision == PROJECT_CONTRACT_REVISION else 10.0
        try:
            timeout = float(
                os.environ.get("AUDIENCE_PLATFORM_TIMEOUT_SECONDS", str(default_timeout))
            )
        except ValueError:
            raise SafeApiError("invalid_timeout") from None
        return cls(
            base_url=os.environ.get(
                "AUDIENCE_PLATFORM_BASE_URL", DEFAULT_AUDIENCE_PLATFORM_BASE_URL
            ),
            sync_api_key=key,
            contract_revision=revision,
            timeout_seconds=timeout,
            allow_localhost_http=(os.environ.get("AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS") == "1"),
            project_id=project_id,
        )


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> Optional[Request]:
        raise SafeApiError("redirect_rejected")


class AudienceSyncClient:
    """Fixed-operation client for the published Audience Platform contract."""

    def __init__(self, config: AudienceSyncConfig) -> None:
        self._config = config
        self._verified_binding_revision = None
        self._origin = _validate_origin(config)
        self._opener = build_opener(
            _RejectRedirects(), HTTPSHandler(context=ssl.create_default_context())
        )

    def validate_configuration(
        self, operation: Operation | ProjectOperation | PersonalKeyOperation
    ) -> None:
        """Validate a selected operation family without making a network request."""
        if not isinstance(operation, (Operation, ProjectOperation, PersonalKeyOperation)):
            raise SafeApiError("operation_not_allowed")
        _assert_available(self._config, operation)

    @staticmethod
    def local_capabilities() -> dict[str, Any]:
        return {
            "contract_revision": CONTRACT_REVISION,
            "contract_state": CONTRACT_STATE,
            "capability_source": "local_adapter",
            "create_audience_available": True,
            "business_operations_available": True,
            "operations": [operation.value for operation in OPERATION_SPECS],
            "project_contract_revision": PROJECT_CONTRACT_REVISION,
            "project_operations": [operation.value for operation in PROJECT_OPERATION_SPECS],
            "personal_key_operations": [
                operation.value for operation in PERSONAL_KEY_OPERATION_SPECS
            ],
            "local_operations": ["summarize_project_keys"],
        }

    def call(
        self,
        operation: Operation | ProjectOperation | PersonalKeyOperation,
        /,
        *,
        path: Optional[Mapping[str, str]] = None,
        query: Optional[Mapping[str, str]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        if not isinstance(operation, (Operation, ProjectOperation, PersonalKeyOperation)):
            raise SafeApiError("operation_not_allowed")
        try:
            spec = {
                **OPERATION_SPECS, **PROJECT_OPERATION_SPECS, **PERSONAL_KEY_OPERATION_SPECS
            }[operation]
        except (KeyError, TypeError):
            raise SafeApiError("operation_not_allowed") from None
        self.validate_configuration(operation)
        path_values = dict(path or {})
        query_values = dict(query or {})
        body_values = dict(body or {})
        if isinstance(operation, ProjectOperation):
            if path_values.get("project_id") != self._config.project_id:
                raise SafeApiError("project_binding_mismatch")
        _validate_parameter_names(path_values, frozenset(spec.path_parameters), "path")
        _validate_parameter_names(
            query_values,
            frozenset(spec.query_parameters),
            "query",
            spec.required_query_parameters,
        )
        allowed_body = spec.required_body | spec.optional_body
        _validate_parameter_names(body_values, allowed_body, "body", spec.required_body)
        try:
            validate_operation_path(operation, path_values)
        except ContractViolation:
            raise SafeApiError("invalid_identifier") from None
        try:
            validate_operation_query(operation, query_values)
        except ContractViolation:
            raise SafeApiError("invalid_query_parameters") from None
        try:
            if "audience_filter" in body_values:
                validate_audience_filter(body_values["audience_filter"])
        except ContractViolation:
            raise SafeApiError("invalid_audience_filter") from None
        try:
            validate_operation_request(operation, body_values if spec.method != "GET" else None)
        except ContractViolation:
            raise SafeApiError("invalid_request_schema") from None

        expected_binding = None
        if isinstance(operation, ProjectOperation):
            expected_binding = self._preflight_project(operation, path_values, body_values)

        rendered_path = spec.path
        for name in spec.path_parameters:
            rendered_path = rendered_path.replace(
                "{" + name + "}", quote(path_values[name], safe="")
            )
        if query_values:
            rendered_path += "?" + urlencode(
                {name: query_values[name] for name in spec.query_parameters if name in query_values}
            )
        encoded_body = None
        if spec.method != "GET":
            encoded_body = json.dumps(
                body_values, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            if len(encoded_body) > MAX_REQUEST_BYTES:
                raise SafeApiError("request_too_large")
        request = Request(
            self._origin + rendered_path,
            data=encoded_body,
            method=spec.method,
            headers={
                "Authorization": "Bearer " + self._config.sync_api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read(MAX_BYTES + 1)
        except SafeApiError:
            raise
        except HTTPError as exc:
            error = _project_http_error(exc, self._config.sync_api_key)
            if (
                isinstance(operation, PersonalKeyOperation)
                and error.status in {404, 405, 501}
                and error.code == "audience_sync_request_failed"
            ):
                raise SafeApiError("key_context_unavailable", error.status) from None
            raise error from None
        except (URLError, TimeoutError, OSError):
            raise SafeApiError("transport_error") from None
        if len(raw) > MAX_BYTES:
            raise SafeApiError("response_too_large")
        try:
            decoded = _normalize_project_readback_compatibility(
                operation, json.loads(raw.decode("utf-8"))
            )
            validated = validate_operation_response(operation, decoded)
            if isinstance(operation, ProjectOperation):
                from .project_query import validate_response_binding

                validate_response_binding(
                    operation, validated, path_values, body_values, query_values
                )
                if expected_binding and validated["binding_revision"] != expected_binding:
                    raise ContractViolation
                if (
                    self._verified_binding_revision
                    and validated["binding_revision"] != self._verified_binding_revision
                ):
                    raise ContractViolation
            if isinstance(operation, PersonalKeyOperation):
                if self._config.project_id and validated["project_id"] != self._config.project_id:
                    raise SafeApiError("project_binding_mismatch")
                if validated["allowed_actions"] != sorted(set(validated["allowed_actions"])):
                    raise ContractViolation
        except (UnicodeDecodeError, json.JSONDecodeError, ContractViolation):
            raise SafeApiError("invalid_response") from None
        projected = _safe_projection(validated)
        if projected is None:
            return None
        if not isinstance(projected, dict):
            raise SafeApiError("invalid_response")
        if isinstance(operation, PersonalKeyOperation):
            self._config = replace(self._config, project_id=projected["project_id"])
            self._verified_binding_revision = projected["binding_revision"]
        return projected

    def _preflight_project(self, operation, path, body):
        from .project_query import check_logical_path

        flags = {
            ProjectOperation.CREATE_AUDIENCE_QUERY: "structured_criteria_available",
            ProjectOperation.VALIDATE_AUDIENCE_QUERY: "structured_criteria_available",
            ProjectOperation.PREVIEW_AUDIENCE_QUERY: "preview_available",
            ProjectOperation.MATERIALIZE_AUDIENCE_QUERY: "materialization_available",
            ProjectOperation.SYNC_AUDIENCE_QUERY: "brevo_sync_available",
        }
        if operation not in flags:
            return
        project_path = {"project_id": path["project_id"]}
        response = self.call(ProjectOperation.GET_QUERY_CAPABILITIES, path=project_path)
        capability = response["resource"]
        if capability.get(flags[operation]) is not True:
            raise SafeApiError("project_query_unavailable")
        if "criteria" in body:
            try:
                check_logical_path(body["criteria"], capability)
            except ContractViolation:
                raise SafeApiError("query_path_onboarding_required") from None
        if operation == ProjectOperation.SYNC_AUDIENCE_QUERY:
            destinations = self.call(
                ProjectOperation.GET_PROJECT_SYNC_CAPABILITIES, path=project_path
            )
            if destinations["binding_revision"] != response["binding_revision"]:
                raise SafeApiError("project_binding_mismatch")
            if not any(
                item.get("destination_id") == body["destination_id"]
                and item.get("destination_revision") == body["destination_revision"]
                and item.get("kind") == "brevo"
                and item.get("target_type") == "folder"
                for item in destinations["destinations"]
            ):
                raise SafeApiError("project_destination_unavailable")
        return response["binding_revision"]


def _normalize_project_readback_compatibility(operation, response):
    """Bridge the one required nullable field added to terminal readbacks.

    Pre-rollout Platform responses omit ``safe_error_code``. Its only safe
    missing-value meaning is ``null``; this adds that one field before the
    closed schema validator runs and neither deletes nor admits any other
    response field.
    """
    if operation not in {
        ProjectOperation.MATERIALIZE_AUDIENCE_QUERY,
        ProjectOperation.GET_AUDIENCE_QUERY_MATERIALIZATION,
        ProjectOperation.SYNC_AUDIENCE_QUERY,
        ProjectOperation.GET_AUDIENCE_QUERY_SYNC,
    }:
        return response
    if not isinstance(response, dict) or not isinstance(response.get("resource"), dict):
        return response
    if "safe_error_code" in response["resource"]:
        return response
    normalized = dict(response)
    normalized["resource"] = {**response["resource"], "safe_error_code": None}
    return normalized


def _assert_available(config: AudienceSyncConfig, operation=None) -> None:
    project = isinstance(operation, (ProjectOperation, PersonalKeyOperation))
    if isinstance(operation, ProjectOperation) and not config.project_id:
        raise SafeApiError("project_binding_missing")
    expected_revision = PROJECT_CONTRACT_REVISION if project else CONTRACT_REVISION
    key_pattern = _PROJECT_API_KEY if project else _PERSONAL_API_KEY
    if config.contract_revision != expected_revision:
        raise SafeApiError("contract_revision_mismatch")
    if key_pattern.fullmatch(config.sync_api_key) is None:
        raise SafeApiError("invalid_sync_api_key")


def _validate_origin(config: AudienceSyncConfig) -> str:
    parsed = urlsplit(config.base_url)
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
    if parsed.scheme != "https" and not (
        config.allow_localhost_http and localhost and parsed.scheme == "http"
    ):
        raise SafeApiError("https_required")
    if not config.sync_api_key:
        raise SafeApiError("sync_api_key_missing")
    if (
        _PERSONAL_API_KEY.fullmatch(config.sync_api_key) is None
        and _PROJECT_API_KEY.fullmatch(config.sync_api_key) is None
    ):
        raise SafeApiError("invalid_sync_api_key")
    max_timeout = (
        PROJECT_TIMEOUT_SECONDS
        if config.contract_revision == PROJECT_CONTRACT_REVISION
        else MAX_TIMEOUT_SECONDS
    )
    if not 0 < config.timeout_seconds <= max_timeout:
        raise SafeApiError("invalid_timeout")
    return f"{parsed.scheme}://{parsed.netloc}"


def _validate_parameter_names(
    values: Mapping[str, Any],
    allowed: frozenset[str],
    label: str,
    required: Optional[frozenset[str]] = None,
) -> None:
    if set(values) - allowed or (required is None and set(values) != allowed):
        raise SafeApiError(f"invalid_{label}_parameters")
    if required is not None and not required.issubset(values):
        raise SafeApiError(f"invalid_{label}_parameters")


def _project_http_error(exc: HTTPError, sync_api_key: str) -> SafeApiError:
    try:
        raw = exc.read(MAX_BYTES + 1)
        payload = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        code = detail.get("code") if isinstance(detail, dict) else None
        if isinstance(code, str) and sync_api_key not in code and _SAFE_ERROR_CODE.fullmatch(code):
            return SafeApiError(code, exc.code)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        pass
    return SafeApiError("audience_sync_request_failed", exc.code)


def _safe_projection(value: Any, depth: int = 0, field_name: Optional[str] = None) -> Any:
    if field_name == "criteria" and value is not None:
        from .contract_validation import validate_audience_criteria

        try:
            # Criteria has its own closed schema and bounded expression tree.
            # Only structural depth is governed by the DSL. Scalar safety still
            # applies; reject unsafe criteria rather than changing its meaning.
            return _safe_criteria_projection(validate_audience_criteria(value))
        except (ContractViolation, ValueError):
            raise SafeApiError("invalid_response") from None
    if depth > 8:
        raise SafeApiError("invalid_response")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SafeApiError("invalid_response")
        return value
    if isinstance(value, str):
        if len(value) > 4096:
            raise SafeApiError("invalid_response")
        if "://" in value or field_name in {"audience_detail_url", "provider_resource_url"}:
            safe_url = (
                field_name == "audience_detail_url" and _is_safe_audience_detail_url(value)
            ) or (field_name == "provider_resource_url" and _is_safe_provider_resource_url(value))
            if not safe_url:
                raise SafeApiError("invalid_response")
        return value
    if isinstance(value, list):
        if len(value) > 100:
            raise SafeApiError("invalid_response")
        return [_safe_projection(item, depth + 1, field_name) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise SafeApiError("invalid_response")
        return {
            str(key): _safe_projection(item, depth + 1, str(key))
            for key, item in value.items()
            if not _UNSAFE_RESPONSE_KEY.fullmatch(str(key))
        }
    raise SafeApiError("invalid_response")


def _safe_criteria_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _safe_criteria_projection(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_criteria_projection(item) for item in value]
    if isinstance(value, str) and (
        "awpk_" in value or "@" in value or "\x00" in value
    ):
        raise SafeApiError("invalid_response")
    return _safe_projection(value)


def _is_safe_audience_detail_url(value: str) -> bool:
    if len(value) > 2048 or "\\" in value or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        query = (parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
                 if parsed.query else [])
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and (not parsed.query or (
            len(query) == 1
            and query[0][0] == "aw_target"
            and re.fullmatch(
                r"/projects/[a-z][a-z0-9-]{1,62}/audiences/(?:aud|aqp)_[a-f0-9]{26}",
                query[0][1],
            ) is not None
            and re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", parsed.path)
            is not None
        ))
        and not parsed.fragment
    )


def _is_safe_provider_resource_url(value: str) -> bool:
    if len(value) > 2048 or "\\" in value or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )
