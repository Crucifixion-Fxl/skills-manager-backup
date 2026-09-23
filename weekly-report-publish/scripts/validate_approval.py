#!/usr/bin/env python3
"""Validate and optionally consume a digest-bound weekly-report approval receipt."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import unicodedata
from urllib.parse import urlsplit


SCHEMA = "addx.weekly_report_approval.v1"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")
MAX_REPORT_BYTES = 8 * 1024 * 1024
SENSITIVE_REPORT_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:glpat|sk|sk-proj)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|token|password|passwd|secret|authorization)\s*[:=]\s*(?!\[REDACTED_)[^\s,;]+"
    ),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"/(?:home|Users|root)/[^\s]+"),
    re.compile(r"[A-Za-z]:\\Users\\[^\s]+"),
)
SAFE_HTML_TAGS = {
    "a",
    "article",
    "b",
    "body",
    "br",
    "caption",
    "code",
    "col",
    "colgroup",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "i",
    "li",
    "main",
    "mark",
    "meta",
    "ol",
    "p",
    "pre",
    "section",
    "small",
    "span",
    "strong",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "ul",
}
GLOBAL_HTML_ATTRIBUTES = {"class", "dir", "hidden", "id", "lang", "role", "style", "title"}
TAG_HTML_ATTRIBUTES = {
    "a": {"href", "rel", "target"},
    "col": {"span"},
    "colgroup": {"span"},
    "details": {"open"},
    "li": {"value"},
    "meta": {"charset", "content", "name"},
    "ol": {"reversed", "start", "type"},
    "td": {"colspan", "headers", "rowspan"},
    "th": {"colspan", "headers", "rowspan", "scope"},
    "time": {"datetime"},
}
URL_ATTRIBUTES = {"href"}
UNSAFE_CSS_RE = re.compile(
    r"(?i)(?:@import\b|\bcontent\s*:|\bdata\s*:|\bblob\s*:|"
    r"expression\s*\(|(?:url|image|(?:-webkit-)?image-set|cross-fade)\s*\(|"
    r"(?:https?:)?//)"
)


class ApprovalError(ValueError):
    pass


class _ReportHTMLSafetyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._style_chunks: list[str] | None = None
        self._style_blocks: list[str] = []
        self._visible_text_chunks: list[str] = []

    @staticmethod
    def _fail() -> None:
        raise ApprovalError("report contains unsafe active markup")

    def _check_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in SAFE_HTML_TAGS:
            self._fail()
        attribute_names = [name.lower() for name, _ in attrs]
        if len(attribute_names) != len(set(attribute_names)):
            self._fail()
        normalized_attrs = {name.lower(): value for name, value in attrs}
        if normalized_tag == "meta":
            charset_meta = (
                set(normalized_attrs) == {"charset"}
                and (normalized_attrs.get("charset") or "").strip().lower() == "utf-8"
            )
            viewport_meta = (
                set(normalized_attrs) == {"name", "content"}
                and (normalized_attrs.get("name") or "").strip().lower()
                == "viewport"
            )
            if not (charset_meta or viewport_meta):
                self._fail()
        for name, raw_value in attrs:
            attribute = name.lower()
            value = raw_value or ""
            allowed_attribute = (
                attribute in GLOBAL_HTML_ATTRIBUTES
                or attribute in TAG_HTML_ATTRIBUTES.get(normalized_tag, set())
                or attribute.startswith("aria-")
                or attribute.startswith("data-")
            )
            if not allowed_attribute or attribute.startswith("on"):
                self._fail()
            if attribute == "style" and self._unsafe_css(value):
                self._fail()
            if attribute not in URL_ATTRIBUTES or not value.strip():
                continue
            decoded = unescape(value).strip()
            compact = re.sub(r"[\x00-\x20\x7f]+", "", decoded)
            if compact.startswith("#"):
                continue
            try:
                parsed = urlsplit(compact)
            except ValueError:
                self._fail()
                return
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                self._fail()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check_tag(tag, attrs)
        if tag.lower() == "style":
            if self._style_chunks is not None:
                self._fail()
            self._style_chunks = []

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._check_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_chunks is not None:
            self._style_blocks.append("".join(self._style_chunks))
            self._style_chunks = None

    def handle_data(self, data: str) -> None:
        if self._style_chunks is not None:
            self._style_chunks.append(data)
        else:
            self._visible_text_chunks.append(data)

    @staticmethod
    def _unsafe_css(value: str) -> bool:
        without_comments = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
        return "\\" in without_comments or UNSAFE_CSS_RE.search(without_comments) is not None

    def validate_complete(self) -> None:
        if self._style_chunks is not None:
            self._style_blocks.append("".join(self._style_chunks))
            self._style_chunks = None
        if any(self._unsafe_css(block) for block in self._style_blocks):
            self._fail()

    def visible_text(self) -> str:
        return "".join(self._visible_text_chunks)


def _normalized_sensitive_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", unescape(value))
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _contains_sensitive_text(value: str) -> bool:
    normalized = _normalized_sensitive_text(value)
    return any(pattern.search(normalized) for pattern in SENSITIVE_REPORT_PATTERNS)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ApprovalError(message)


def _timestamp(value: Any, label: str) -> datetime:
    _require(isinstance(value, str) and value, f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApprovalError(f"{label} must be an ISO timestamp") from error
    _require(parsed.tzinfo is not None, f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_report(report_path: Path) -> bytes:
    _require(
        report_path.exists() and not report_path.is_symlink(),
        "report must be a regular non-symlink file",
    )
    report_metadata = report_path.stat()
    _require(stat.S_ISREG(report_metadata.st_mode), "report must be a regular file")
    _require(report_metadata.st_uid == os.getuid(), "report must be owned by the current user")
    _require(
        report_metadata.st_mode & 0o077 == 0,
        "report permissions must not allow group or other access",
    )
    _require(report_metadata.st_size <= MAX_REPORT_BYTES, "report exceeds 8 MiB")
    payload = report_path.read_bytes()
    try:
        report = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApprovalError("report must be valid UTF-8 HTML") from error
    _require(
        not _contains_sensitive_text(report),
        "report contains unredacted sensitive data",
    )
    parser = _ReportHTMLSafetyParser()
    parser.feed(report)
    parser.close()
    parser.validate_complete()
    _require(
        not _contains_sensitive_text(parser.visible_text()),
        "report contains unredacted sensitive data",
    )
    return payload


def validate_receipt(
    receipt_path: Path,
    report_path: Path,
    author: str,
    week: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    report_bytes = validate_report(report_path)
    _require(receipt_path.exists() and not receipt_path.is_symlink(), "approval receipt must be a regular non-symlink file")
    metadata = receipt_path.stat()
    _require(stat.S_ISREG(metadata.st_mode), "approval receipt must be a regular file")
    _require(metadata.st_size <= 65536, "approval receipt exceeds 64 KiB")
    _require(metadata.st_uid == os.getuid(), "approval receipt must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, "approval receipt permissions must be 0600")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError("approval receipt must be valid UTF-8 JSON") from error
    _require(isinstance(payload, dict), "approval receipt must be an object")
    _require(payload.get("schema") == SCHEMA, "approval receipt schema is invalid")
    _require(payload.get("consumed") is False, "approval receipt was already consumed")
    _require(
        set(payload)
        == {
            "schema",
            "approval_source",
            "author",
            "week",
            "report_path",
            "round_id",
            "report_digest",
            "quality_review_digest",
            "approved_by",
            "accepted_at",
            "expires_at",
            "nonce",
            "consumed",
        },
        "approval receipt must contain the exact allowed fields",
    )
    _require(payload.get("approval_source") == "top-level-user-message", "approval receipt source is invalid")
    _require(payload.get("author") == author, "approval receipt author does not match")
    _require(payload.get("week") == week, "approval receipt week does not match")
    _require(payload.get("report_path") == str(report_path.resolve()), "approval receipt report path does not match")
    for field in ("round_id", "approved_by"):
        _require(isinstance(payload.get(field), str) and payload[field].strip(), f"approval receipt {field} is required")
    _require(
        isinstance(payload.get("nonce"), str) and NONCE_RE.fullmatch(payload["nonce"]),
        "approval receipt nonce is invalid",
    )
    digest = payload.get("report_digest")
    _require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), "approval receipt report digest is invalid")
    quality_digest = payload.get("quality_review_digest")
    _require(
        isinstance(quality_digest, str) and SHA256_RE.fullmatch(quality_digest),
        "approval receipt quality review digest is invalid",
    )
    actual = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    _require(actual == digest, "approval receipt report digest does not match")

    accepted_at = _timestamp(payload.get("accepted_at"), "accepted_at")
    expires_at = _timestamp(payload.get("expires_at"), "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _require(accepted_at <= current + timedelta(minutes=5), "approval receipt is from the future")
    _require(expires_at > current, "approval receipt has expired")
    _require(expires_at <= accepted_at + timedelta(hours=24), "approval receipt lifetime exceeds 24 hours")
    return payload


def consume_receipt(receipt_path: Path, payload: dict[str, Any]) -> None:
    consumed = dict(payload)
    consumed["consumed"] = True
    consumed["consumed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    serialized = json.dumps(consumed, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=receipt_path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, receipt_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--author")
    parser.add_argument("--week")
    parser.add_argument("--consume", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.report_only:
            _require(
                args.receipt is None
                and args.author is None
                and args.week is None
                and not args.consume,
                "--report-only cannot be combined with receipt options",
            )
            validate_report(args.report)
            print("valid report")
            return 0
        _require(
            args.receipt is not None and args.author is not None and args.week is not None,
            "--receipt, --author and --week are required",
        )
        payload = validate_receipt(
            args.receipt, args.report, args.author, args.week
        )
        if args.consume:
            consume_receipt(args.receipt, payload)
    except (ApprovalError, OSError) as error:
        print(f"invalid approval receipt: {error}", file=os.sys.stderr)
        return 1
    print("valid approval receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
