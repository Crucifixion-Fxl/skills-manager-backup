"""Minimal, non-executing loader for the cloned Skill's local configuration."""

from __future__ import annotations

import os
from pathlib import Path

from .client import MAX_BYTES, SafeApiError

ALLOWED_LOCAL_KEYS = frozenset(
    {
        "AUDIENCE_API_KEY",
        "AUDIENCE_PROJECT_ID",
        "AUDIENCE_PLATFORM_BASE_URL",
        "AUDIENCE_PLATFORM_TIMEOUT_SECONDS",
        "AUDIENCE_PLATFORM_ATTACHMENT_TIMEOUT_SECONDS",
        "AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS",
        "AUDIENCE_ATTACHMENT_DIR",
    }
)


def load_local_environment(path: Path) -> None:
    """Load a small allowlist from .env.local without evaluating shell syntax."""
    if not path.exists():
        return
    try:
        raw = path.read_bytes()
    except OSError:
        raise SafeApiError("invalid_env_file") from None
    if len(raw) > MAX_BYTES:
        raise SafeApiError("invalid_env_file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SafeApiError("invalid_env_file") from None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, separator, value = line.partition("=")
        if not separator or name not in ALLOWED_LOCAL_KEYS:
            raise SafeApiError("invalid_env_file")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or "\x00" in value:
            raise SafeApiError("invalid_env_file")
        os.environ.setdefault(name, value)
