#!/usr/bin/env python3
"""Shared fail-closed input helpers for legacy target migration validators."""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,127}$")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?:authorization|password|passwd|secret|token|api[_-]?key|"
    r"access[_-]?key|private[_-]?key)\s*[:=]\s*\S"
)
JWT_VALUE = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.\S+")
AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
QUERY_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s?#]+\?[^\s#]*")
SIGNED_URL_PARAMETER = re.compile(
    r"(?i)(?:x-amz-(?:algorithm|credential|date|expires|signature|signedheaders)|"
    r"x-goog-(?:algorithm|credential|date|expires|signature|signedheaders)|"
    r"awsaccesskeyid|googleaccessid|signature|signedurl|signed_url)(?:=|%3d)"
)
RAW_OUTPUT_MARKER = re.compile(
    r"(?i)(?<![a-z0-9_])(?:stdout|stderr|terminal(?:[- ]output)?|"
    r"(?:raw[- ])?(?:log|manifest|secret|kubectl)[- ]output|"
    r"apiversion\s*:|kind\s*:|metadata\s*:|spec\s*:|data\s*:|"
    r"stringdata\s*:)(?![a-z0-9_])"
)
YAML_DOCUMENT_MARKER = re.compile(r"(?:^|\s)(?:---|\.\.\.)(?:$|\s)")
OPAQUE_BLOB = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{96,}(?![A-Za-z0-9+/_=-])")
SAFE_RECORD_REF = re.compile(
    r"^(?:record:[0-9a-f]{64}|record:[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
SAFE_GITLAB_PATH = re.compile(
    r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/-/"
    r"(?:issues|merge_requests)/[1-9][0-9]*$"
)
SAFE_NOTE_FRAGMENT = re.compile(r"^note_[1-9][0-9]*$")
PLACEHOLDERS = {"todo", "tbd", "missing", "unknown", "stale", "n/a"}
PLACEHOLDER_TERM = re.compile(r"(?i)(?:^|[^a-z0-9])(?:todo|tbd|missing|unknown|stale|n/a)(?:$|[^a-z0-9])")


class SafeInputError(ValueError):
    """Input could not be consumed without weakening the safety contract."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def safe_read_bytes(source: str, *, max_bytes: int) -> bytes:
    """Read stdin or one regular file through bounded no-follow traversal.

    Every parent directory and the leaf are opened relative to an already-open
    directory descriptor with ``O_NOFOLLOW``. Checking only the leaf would let
    a symlinked parent redirect a trusted evidence path outside its checkout.
    """
    if source == "-":
        raw = sys.stdin.buffer.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise SafeInputError("input exceeds byte budget")
        return raw

    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow is None:
        raise SafeInputError("platform cannot enforce no-follow file reads")
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        raise SafeInputError("platform cannot enforce directory traversal")
    path = Path(source)
    parts = path.parts
    if not parts:
        raise SafeInputError("input path is empty")
    if path.is_absolute():
        parent_parts = parts[1:-1]
        leaf = parts[-1]
        start = os.sep
    else:
        parent_parts = parts[:-1]
        leaf = parts[-1]
        start = "."
    if leaf in {"", ".", ".."} or any(part in {"", ".", ".."} for part in parent_parts):
        raise SafeInputError("input path is not canonical")

    directory_flags = os.O_RDONLY | cloexec | nofollow | directory_flag
    file_flags = os.O_RDONLY | cloexec | nofollow
    directory_descriptors: list[int] = []
    descriptor = -1
    try:
        current = os.open(start, directory_flags)
        directory_descriptors.append(current)
        for part in parent_parts:
            current = os.open(part, directory_flags, dir_fd=current)
            directory_descriptors.append(current)
        descriptor = os.open(leaf, file_flags, dir_fd=current)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SafeInputError("input is not a regular file")
        if before.st_size < 0 or before.st_size > max_bytes:
            raise SafeInputError("input exceeds byte budget")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            raise SafeInputError("input changed type while open")
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SafeInputError("input identity changed while open")
        if len(raw) > max_bytes or after.st_size > max_bytes:
            raise SafeInputError("input exceeds byte budget")
        return raw
    except (OSError, OverflowError, MemoryError) as exc:
        raise SafeInputError("input cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for directory_descriptor in reversed(directory_descriptors):
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def safe_read_text(source: str, *, max_bytes: int) -> str:
    raw = safe_read_bytes(source, max_bytes=max_bytes)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SafeInputError("input must be UTF-8") from exc
    if CONTROL_CHARS.search(text):
        raise SafeInputError("input contains forbidden control characters")
    return text


def load_single_yaml(
    source: str,
    *,
    max_bytes: int,
    max_tokens: int,
) -> Any:
    """Load exactly one bounded YAML document without anchors or aliases."""
    text = safe_read_text(source, max_bytes=max_bytes)
    token_count = 0
    document_count = 0
    try:
        for token in yaml.scan(text):
            token_count += 1
            if token_count > max_tokens:
                raise SafeInputError("input exceeds YAML token budget")
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise SafeInputError("YAML anchors and aliases are forbidden")
            if isinstance(token, yaml.tokens.DocumentStartToken):
                document_count += 1
                if document_count > 1:
                    raise SafeInputError("input must contain exactly one YAML document")
        documents = list(yaml.load_all(text, Loader=UniqueKeyLoader))
    except SafeInputError:
        raise
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError, MemoryError) as exc:
        raise SafeInputError("input contains invalid or duplicate YAML") from exc
    nonempty = [document for document in documents if document is not None]
    if len(nonempty) != 1:
        raise SafeInputError("input must contain exactly one YAML document")
    document = nonempty[0]
    stack = [document]
    visited = 0
    while stack:
        current = stack.pop()
        visited += 1
        if visited > max_tokens:
            raise SafeInputError("input exceeds constructed node budget")
        if isinstance(current, str) and CONTROL_CHARS.search(current):
            raise SafeInputError("input contains forbidden control characters")
        if isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return document


def is_stable_evidence_ref(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 512 or CONTROL_CHARS.search(value):
        return False
    if SAFE_RECORD_REF.fullmatch(value):
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "gitlab.addx.ai"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or not SAFE_GITLAB_PATH.fullmatch(parsed.path)
    ):
        return False
    return not parsed.fragment or bool(SAFE_NOTE_FRAGMENT.fullmatch(parsed.fragment))


def contains_sensitive_material(value: str) -> bool:
    lowered = value.lower()
    return bool(
        SENSITIVE_ASSIGNMENT.search(value)
        or JWT_VALUE.search(value)
        or AWS_ACCESS_KEY.search(value)
        or "-----begin " in lowered
        or "x-amz-signature" in lowered
        or "x-goog-signature" in lowered
        or "sig=" in lowered
        or "bearer " in lowered
        or SIGNED_URL_PARAMETER.search(value)
    )


def is_safe_actor(value: object) -> bool:
    return (
        isinstance(value, str)
        and SAFE_ACTOR.fullmatch(value) is not None
        and CONTROL_CHARS.search(value) is None
        and not contains_sensitive_material(value)
        and PLACEHOLDER_TERM.search(value) is None
    )


def is_safe_observed_state(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 512
        and value.strip() == value
        and "\n" not in value
        and "\r" not in value
        and CONTROL_CHARS.search(value) is None
        and not contains_sensitive_material(value)
        and QUERY_URL.search(value) is None
        and RAW_OUTPUT_MARKER.search(value) is None
        and YAML_DOCUMENT_MARKER.search(value) is None
        and "{" not in value
        and "}" not in value
        and OPAQUE_BLOB.search(value) is None
        and PLACEHOLDER_TERM.search(value) is None
    )
