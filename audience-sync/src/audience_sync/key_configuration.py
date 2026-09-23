"""Bounded explicit host credentials and isolated local permission summaries."""

from __future__ import annotations

import json
import os
import re

from .client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
from .project_operations import PersonalKeyOperation

MAX_KEYS = 8
MAX_KEY_MAP_BYTES = 16 * 1024
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,31}$", re.ASCII)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise SafeApiError("invalid_sync_api_keys")
        result[name] = value
    return result


def configured_keys():
    """Read only the dedicated host inputs; never scan credentials or infer routing."""
    raw = os.environ.get("AUDIENCE_SYNC_API_KEYS", "")
    single = os.environ.get("AUDIENCE_SYNC_API_KEY", "")
    if not raw:
        return {"default": single}, False
    if single:
        raise SafeApiError("ambiguous_sync_api_keys")
    if len(raw.encode("utf-8")) > MAX_KEY_MAP_BYTES:
        raise SafeApiError("invalid_sync_api_keys")
    try:
        keys = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise SafeApiError("invalid_sync_api_keys") from None
    if not isinstance(keys, dict) or not 1 <= len(keys) <= MAX_KEYS:
        raise SafeApiError("invalid_sync_api_keys")
    if any(
        not _ALIAS.fullmatch(alias)
        or alias.startswith("awpk_")
        or not isinstance(key, str)
        for alias, key in keys.items()
    ):
        raise SafeApiError("invalid_sync_api_keys")
    return keys, True


def select_key(alias=None):
    keys, multiple = configured_keys()
    if multiple and alias is None:
        raise SafeApiError("key_alias_required")
    if alias is None:
        alias = "default"
    if not isinstance(alias, str) or alias not in keys:
        raise SafeApiError("unknown_key_alias")
    return keys[alias]


def summarize_project_keys():
    """Verify each explicitly supplied key independently; never merge authority."""
    keys, multiple = configured_keys()
    results = []
    for alias in sorted(keys):
        try:
            config = AudienceSyncConfig.from_environment(
                key_alias=alias if multiple else None, project_operation=True
            )
            client = AudienceSyncClient(config)
            context = client.call(PersonalKeyOperation.GET_PROJECT_PERSONAL_KEY_CONTEXT)
            results.append({"alias": alias, "status": "verified", "context": context})
        except SafeApiError as exc:
            status = "error"
            if exc.status == 401:
                status = "invalid"
            elif exc.status == 503 or exc.code in {"transport_error", "key_context_unavailable"}:
                status = "unavailable"
            entry = {"alias": alias, "status": status, "error": exc.code}
            if type(exc.status) is int and 100 <= exc.status <= 599:
                entry["http_status"] = exc.status
            results.append(entry)
    return {"keys": results}
