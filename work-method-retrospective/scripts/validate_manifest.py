#!/usr/bin/env python3
"""Validate a work-method-retrospective result manifest."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any
import unicodedata
from urllib.parse import urlsplit


SCHEMA_V1 = "addx.work_method_retrospective.v1"
SCHEMA_V2 = "addx.work_method_retrospective.v2"
SCHEMA_V3 = "addx.work_method_retrospective.v3"
SCHEMAS = {SCHEMA_V1, SCHEMA_V2, SCHEMA_V3}
TASK_NAMES = {"eval_driven_review", "skill_proposal_discovery"}
OUTCOMES = {
    "new-skill-proposal",
    "extend-existing-under-review",
    "needs-evidence",
    "reject",
}
ACCEPTED_PROPOSAL_OUTCOMES = {
    "new-skill-proposal",
    "extend-existing-under-review",
}
CONFIDENCE = {"high", "medium", "low"}
COVERAGE = {"verified", "partial_unverified", "unverified"}
ADDX_COVERAGE = {"full", "partial", "none"}
CHANGE_ACTIONS = {"add", "update", "none", "needs-evidence"}
TRIGGER_SOURCES = {"explicit", "weekly_report", "major_delivery", "eval_mismatch"}
SCAN_COVERAGE = {"complete", "partial", "needs-evidence"}
FRICTION_CATEGORIES = {
    "cicd",
    "telemetry",
    "environment",
    "permissions",
    "tooling",
    "handoff",
    "manual_rework",
    "other",
}
FRICTION_STATUS = {"observed", "needs-evidence"}
FRICTION_UNITS = {"seconds", "minutes", "hours", "count", "percent", "occurrence"}
FRICTION_DATA_STATES = {
    "measured",
    "instrumentation_missing",
    "query_blocked",
    "unresolved",
    "not_applicable",
}
QUALITY_DIMENSIONS = {
    "scene_reconstruction",
    "evidence_traceability",
    "recommendation_actionability",
    "skill_decision_accuracy",
    "human_readability",
}
MINIMUM_QUALITY_SCORE = 3
CRITICAL_GATES = {
    "scene_reconstruction",
    "evidence_traceability",
    "skill_boundary",
}
EFFICIENCY_MODES = {"rerun", "reuse"}
TOKEN_RECEIPT_SOURCES = {"provider_usage"}
MAX_ROUND_ENTRIES = 512
MAX_ROUND_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
_STATE_ROOT_VALUE = os.environ.get("ADDX_WORK_METHOD_STATE_DIR")
STATE_ROOT = (
    Path(_STATE_ROOT_VALUE)
    if _STATE_ROOT_VALUE
    else Path.home() / ".loongsuite-pilot" / "work-method-retrospective"
)
if not STATE_ROOT.is_absolute():
    raise RuntimeError("ADDX_WORK_METHOD_STATE_DIR must be an absolute path")
QUALITY_GATE_ROOT = STATE_ROOT / "quality-gates"
STATE_MARKER = ".addx-work-method-artifacts"
STATE_MARKER_CONTENT = "addx.work_method_artifacts.v1\n"
SKILL_CATALOG = REPOSITORY_ROOT / "skill-analytics" / "catalog" / "addx-skills.json"
FILTER_SOURCES = (
    SKILL_ROOT / "filters" / "claude-session-evidence.jq",
    SKILL_ROOT / "filters" / "codex-session-evidence.jq",
    SKILL_ROOT / "scripts" / "extract_session_evidence.sh",
    SKILL_ROOT / "scripts" / "build_scene_ledger.py",
)
SELECTION_POLICY = SKILL_ROOT / "policies" / "scene-selection-v1.json"
QUALITY_POLICY = SKILL_ROOT / "policies" / "report-quality-v1.json"
QUALITY_POLICY_SOURCES = (
    QUALITY_POLICY,
    SKILL_ROOT / "evals" / "evals.json",
)
WEEK_ID_RE = re.compile(r"^(20[2-9][0-9])-W(0[1-9]|[1-4][0-9]|5[0-3])$")
GATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")
SCENE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
EVIDENCE_REF_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}#[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
)
SAFE_FIELD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SENSITIVE_SCENE_PATTERNS = (
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


class ContractError(ValueError):
    """Raised when a review manifest violates the evidence contract."""


class _ReportHTMLSafetyParser(HTMLParser):
    def __init__(self, label: str) -> None:
        super().__init__(convert_charrefs=True)
        self.label = label
        self._style_chunks: list[str] | None = None
        self._style_blocks: list[str] = []
        self._visible_text_chunks: list[str] = []

    def _fail(self) -> None:
        raise ContractError(f"{self.label} contains unsafe active markup")

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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label} must be an object")
    return value


def _list(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    _require(isinstance(value, list), f"{label} must be an array")
    if nonempty:
        _require(bool(value), f"{label} must not be empty")
    return value


def _text(value: Any, label: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty")
    return value.strip()


def _unique_texts(value: Any, label: str, *, minimum: int = 1) -> list[str]:
    rows = _list(value, label, nonempty=True)
    texts = [_text(item, f"{label} item") for item in rows]
    _require(len(set(texts)) >= minimum, f"{label} needs at least {minimum} unique values")
    return texts


def _nonnegative_int(value: Any, label: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{label} must be a nonnegative integer",
    )
    return value


def _iso_date(value: Any, label: str) -> date:
    text = _text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO date") from error


def _bounded_window(value: Any, label: str, *, maximum_days: int) -> tuple[date, date]:
    window = _mapping(value, label)
    start = _iso_date(window.get("start"), f"{label}.start")
    end = _iso_date(window.get("end"), f"{label}.end")
    _require(end >= start, f"{label} end must not precede start")
    _require((end - start).days < maximum_days, f"{label} exceeds {maximum_days} days")
    return start, end


def _iso_timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO timestamp") from error
    _require(parsed.tzinfo is not None, f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    _require(bool(SHA256_RE.fullmatch(text)), f"{label} must be a sha256 digest")
    return text


def _canonical_record_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def source_record_digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_record_bytes(record)).hexdigest()


def _reject_sensitive_scene_text(value: str, label: str) -> None:
    value = unicodedata.normalize("NFKC", unescape(value))
    value = "".join(
        character for character in value if unicodedata.category(character) != "Cf"
    )
    lower = value.lower()
    indicators = (
        "bearer",
        "sk-",
        "glpat",
        "akia",
        "gh",
        "npm_",
        "xox",
        "eyj",
        "token",
        "password",
        "passwd",
        "secret",
        "authorization",
        "@",
        "/home/",
        "/users/",
        "/root/",
        ":\\users\\",
    )
    if not any(indicator in lower for indicator in indicators):
        return
    _require(
        not any(pattern.search(value) for pattern in SENSITIVE_SCENE_PATTERNS),
        f"{label} contains unredacted sensitive data",
    )


def _safe_report_text(value: Any, label: str, *, maximum: int = 1000) -> str:
    text = _text(value, label)
    _require(len(text) <= maximum, f"{label} exceeds {maximum} characters")
    _reject_sensitive_scene_text(text, label)
    return text


def _safe_report_texts(
    value: Any, label: str, *, minimum: int = 1, maximum: int = 1000
) -> list[str]:
    texts = _unique_texts(value, label, minimum=minimum)
    for index, text in enumerate(texts):
        _safe_report_text(text, f"{label} item {index}", maximum=maximum)
    return texts


def _validate_weekly_report_content(payload: bytes, label: str) -> None:
    try:
        report = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"{label} must be valid UTF-8 HTML") from error
    _reject_sensitive_scene_text(report, label)
    parser = _ReportHTMLSafetyParser(label)
    parser.feed(report)
    parser.close()
    parser.validate_complete()
    _reject_sensitive_scene_text(parser.visible_text(), label)


def _validate_extracted_record(value: Any, label: str) -> tuple[dict[str, Any], datetime]:
    record = _mapping(value, label)
    kind = record.get("kind")
    _require(
        kind in {"human_input", "agent_instruction", "ai_output", "tool_call", "tool_result"},
        f"{label} kind is not selectable evidence",
    )
    required = {"kind", "provider", "at", "session_kind", "trust"}
    optional = {"session_id"}
    if kind in {"human_input", "agent_instruction", "ai_output"}:
        required |= {"text", "text_truncated"}
        if kind == "ai_output":
            optional.add("phase")
    elif kind == "tool_call":
        required |= {"call_id", "tool"}
    else:
        required |= {"call_id", "status", "content_characters"}
    _require(
        required <= set(record) <= required | optional,
        f"{label} fields do not match the extractor schema",
    )
    _require(record.get("provider") in {"codex", "claude"}, f"{label} provider is invalid")
    _require(record.get("session_kind") in {"root", "subagent"}, f"{label} session_kind is invalid")
    _require(record.get("trust") == "untrusted_evidence", f"{label} trust marker is invalid")
    observed_at = _iso_timestamp(record.get("at"), f"{label} at")
    for field in optional:
        if field in record and record[field] is not None:
            _require(
                isinstance(record[field], str) and len(record[field]) <= 128,
                f"{label} {field} is invalid",
            )
    if kind in {"human_input", "agent_instruction", "ai_output"}:
        _require(
            isinstance(record.get("text"), str)
            and bool(record["text"])
            and len(record["text"]) <= 4000,
            f"{label} text must be 1..4000 characters",
        )
        _require(
            isinstance(record.get("text_truncated"), bool),
            f"{label} text_truncated must be boolean",
        )
    elif kind == "tool_call":
        for field in ("call_id", "tool"):
            _require(
                isinstance(record.get(field), str)
                and bool(record[field])
                and len(record[field]) <= 128,
                f"{label} {field} is invalid",
            )
    else:
        _require(record.get("status") in {"success", "error", "unknown"}, f"{label} status is invalid")
        _nonnegative_int(record.get("content_characters"), f"{label} content_characters")
        _require(
            isinstance(record.get("call_id"), str) and len(record["call_id"]) <= 128,
            f"{label} call_id is invalid",
        )
    _reject_sensitive_scene_text(
        _canonical_record_bytes(record).decode("utf-8"), label
    )
    return record, observed_at


def build_scene_from_records(
    scene_id: str,
    summary: str,
    records: list[Any],
    record_lines: list[int] | None = None,
) -> dict[str, Any]:
    _require(
        isinstance(scene_id, str) and SCENE_ID_RE.fullmatch(scene_id) is not None,
        "scene id is invalid",
    )
    _require(1 <= len(records) <= 64, "scene must select 1..64 extractor records")
    _require(
        isinstance(summary, str) and bool(summary.strip()) and len(summary) <= 256_000,
        f"scene {scene_id} summary must be 1..256,000 characters",
    )
    _reject_sensitive_scene_text(summary, f"scene {scene_id} summary")
    lines = record_lines or list(range(1, len(records) + 1))
    _require(
        len(lines) == len(records)
        and lines == sorted(set(lines))
        and all(isinstance(line, int) and line > 0 for line in lines),
        f"scene {scene_id} record lines must be positive, unique, and sorted",
    )
    entries: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    digests: set[str] = set()
    for index, (line, value) in enumerate(zip(lines, records, strict=True)):
        record, observed_at = _validate_extracted_record(
            value, f"scene {scene_id} source record {index}"
        )
        digest = source_record_digest(record)
        _require(digest not in digests, f"scene {scene_id} repeats a source record")
        digests.add(digest)
        entries.append(
            {
                "record_digest": digest,
                "record_line": line,
                "kind": record["kind"],
                "provider": record["provider"],
                "at": record["at"],
                "session_kind": record["session_kind"],
                "session_id": record.get("session_id"),
            }
        )
        timestamps.append(observed_at)
    observed_at = max(timestamps)
    iso_year, iso_week, _ = observed_at.date().isocalendar()
    return {
        "id": scene_id,
        "content": summary,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "weekly_report_id": f"{iso_year}-W{iso_week:02d}",
        "source_records": entries,
    }


def _reject_symlink_components(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            break
        _require(
            not stat.S_ISLNK(metadata.st_mode),
            f"{label} contains a symlink component: {cursor}",
        )


def _private_regular_file(
    path: Path, label: str, *, maximum_bytes: int = 5 * 1024 * 1024
) -> bytes:
    _reject_symlink_components(path, label)
    _require(path.exists(), f"{label} is missing")
    metadata = path.lstat()
    _require(stat.S_ISREG(metadata.st_mode), f"{label} must be a regular file")
    _require(metadata.st_uid == os.getuid(), f"{label} must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, f"{label} must be private")
    _require(metadata.st_size <= maximum_bytes, f"{label} exceeds the size limit")
    return path.read_bytes()


def _validate_private_round(root_value: Path) -> Path:
    """Fail closed when any handoff artifact in a round is not private."""
    root_candidate = Path(root_value)
    _reject_symlink_components(root_candidate, "round artifact root")
    _require(root_candidate.exists(), "round artifact root is missing")
    root = root_candidate.resolve()
    root_metadata = root.stat()
    _require(stat.S_ISDIR(root_metadata.st_mode), "round artifact root must be a directory")
    _require(
        root_metadata.st_uid == os.getuid(),
        "round artifact root must be owned by the current user",
    )
    _require(root_metadata.st_mode & 0o077 == 0, "round artifact root must be private")
    entry_count = 0
    byte_count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                artifact = Path(entry.path)
                metadata = entry.stat(follow_symlinks=False)
                entry_count += 1
                _require(
                    entry_count <= MAX_ROUND_ENTRIES,
                    f"round artifact exceeds the {MAX_ROUND_ENTRIES}-entry limit",
                )
                _require(
                    not stat.S_ISLNK(metadata.st_mode),
                    f"round artifact contains a symlink component: {artifact}",
                )
                _require(
                    metadata.st_uid == os.getuid(),
                    f"round artifact must be owned by the current user: {artifact}",
                )
                _require(
                    metadata.st_mode & 0o077 == 0,
                    f"round artifact must be private: {artifact}",
                )
                _require(
                    stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode),
                    f"round artifact must be a regular file or directory: {artifact}",
                )
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(artifact)
                else:
                    byte_count += metadata.st_size
                    _require(
                        byte_count <= MAX_ROUND_BYTES,
                        "round artifact exceeds the 64 MiB aggregate limit",
                    )
    return root


def read_private_manifest(path: Path, *, state_root: Path = STATE_ROOT) -> Any:
    """Read a manifest only from a current-user, private round."""
    manifest_path = Path(path)
    state_candidate = Path(state_root)
    _reject_symlink_components(state_candidate, "work-method state root")
    _require(state_candidate.is_dir(), "work-method state root is missing")
    state = state_candidate.resolve()
    state_metadata = state.stat()
    _require(
        state_metadata.st_uid == os.getuid()
        and stat.S_ISDIR(state_metadata.st_mode)
        and state_metadata.st_mode & 0o077 == 0,
        "work-method state root must be a private current-user directory",
    )
    marker = state / STATE_MARKER
    _require(
        marker.is_file()
        and not marker.is_symlink()
        and marker.read_text(encoding="utf-8") == STATE_MARKER_CONTENT,
        "work-method state root marker is missing or invalid",
    )
    round_root = manifest_path.resolve().parent
    _require(
        round_root.parent == state
        and round_root.name not in {"ledger", "quality-gates"},
        "manifest must be inside a direct state-root round directory",
    )
    _validate_private_round(round_root)
    payload = _private_regular_file(manifest_path, "manifest")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ContractError("manifest must be valid JSON") from error
    manifest = _mapping(document, "manifest")
    round_id = _text(manifest.get("round_id"), "round_id")
    _require(
        SAFE_FIELD_ID_RE.fullmatch(round_id) is not None
        and round_id == round_root.name,
        "manifest round_id must match its state-root round directory",
    )
    return document


def _current_filter_hash() -> str:
    digest = hashlib.sha256()
    for path in FILTER_SOURCES:
        digest.update(path.relative_to(SKILL_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _current_selection_policy_hash() -> str:
    return f"sha256:{hashlib.sha256(SELECTION_POLICY.read_bytes()).hexdigest()}"


def _current_quality_policy_hash() -> str:
    digest = hashlib.sha256()
    for path in QUALITY_POLICY_SOURCES:
        digest.update(path.relative_to(SKILL_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def validate_quality_gate_document(
    value: Any, *, require_current_policy: bool = False
) -> None:
    receipt = _mapping(value, "accepted quality gate receipt")
    _require(
        receipt.get("schema") == "addx.work_method_quality_gate.v1",
        "accepted quality gate receipt schema is invalid",
    )
    _require(
        set(receipt)
        == {
            "schema",
            "snapshot_id",
            "filter_hash",
            "selection_policy_hash",
            "quality_policy_hash",
            "rubric_version",
            "decision",
            "accepted_at",
            "accepted_by",
            "report_digest",
            "approval_provenance",
            "quality_comparison",
        },
        "accepted quality gate receipt must contain the exact allowed fields",
    )
    for field in ("snapshot_id", "accepted_by", "rubric_version"):
        _text(receipt.get(field), f"accepted quality gate receipt {field}")
    persisted_at = _iso_timestamp(
        receipt.get("accepted_at"), "accepted quality gate receipt accepted_at"
    )
    report_digest = _sha256(
        receipt.get("report_digest"), "accepted quality gate receipt report_digest"
    )
    for field in ("filter_hash", "selection_policy_hash", "quality_policy_hash"):
        _sha256(receipt.get(field), f"accepted quality gate receipt {field}")
    _require(
        receipt.get("decision") == "accept",
        "reused efficiency gate must have an accepted decision",
    )

    approval = _mapping(
        receipt.get("approval_provenance"),
        "accepted quality gate receipt approval_provenance",
    )
    _require(
        set(approval)
        == {
            "receipt_digest",
            "round_id",
            "author",
            "week",
            "weekly_report_digest",
            "approved_by",
            "accepted_at",
        },
        "accepted quality gate approval provenance must contain the exact allowed fields",
    )
    for field in ("round_id", "author", "week", "approved_by"):
        _text(approval.get(field), f"accepted quality gate approval {field}")
    _sha256(
        approval.get("receipt_digest"),
        "accepted quality gate approval receipt_digest",
    )
    _sha256(
        approval.get("weekly_report_digest"),
        "accepted quality gate approval weekly_report_digest",
    )
    week_match = WEEK_ID_RE.fullmatch(approval["week"])
    _require(bool(week_match), "accepted quality gate approval week must use YYYY-Www")
    try:
        date.fromisocalendar(int(week_match.group(1)), int(week_match.group(2)), 1)
    except ValueError as error:
        raise ContractError(
            "accepted quality gate approval week is not a real ISO week"
        ) from error
    approval_at = _iso_timestamp(
        approval.get("accepted_at"), "accepted quality gate approval accepted_at"
    )
    _require(
        approval_at <= persisted_at,
        "accepted quality gate approval cannot postdate gate persistence",
    )
    _require(
        approval.get("approved_by") == receipt.get("accepted_by"),
        "accepted quality gate approval user does not match accepted_by",
    )

    comparison = _mapping(
        receipt.get("quality_comparison"),
        "accepted quality gate receipt quality_comparison",
    )
    _require(
        comparison.get("quality_review_digest") == report_digest,
        "accepted quality gate report digest does not match the quality review digest",
    )
    _validate_receipt_quality_comparison(comparison)
    if require_current_policy:
        _require(
            receipt.get("filter_hash") == _current_filter_hash(),
            "accepted quality gate uses a retired extractor policy",
        )
        _require(
            receipt.get("selection_policy_hash") == _current_selection_policy_hash(),
            "accepted quality gate uses a retired selection policy",
        )
        _require(
            receipt.get("quality_policy_hash") == _current_quality_policy_hash(),
            "accepted quality gate uses a retired quality policy",
        )


def _accepted_gate_receipt(
    value: Any,
    filter_hash: str,
    selection_policy_hash: str,
    quality_policy_hash: str,
    rubric_version: str,
) -> None:
    accepted = _mapping(value, "efficiency_eval.accepted_gate")
    artifact_ref_text = _text(
        accepted.get("artifact_ref"), "efficiency_eval.accepted_gate.artifact_ref"
    )
    artifact_ref = Path(artifact_ref_text)
    _require(
        not artifact_ref.is_absolute()
        and len(artifact_ref.parts) == 1
        and GATE_NAME_RE.fullmatch(artifact_ref_text) is not None,
        "accepted quality gate artifact_ref must be a safe single JSON filename",
    )
    artifact_candidate = QUALITY_GATE_ROOT / artifact_ref
    _require(not artifact_candidate.is_symlink(), "accepted quality gate artifact must not be a symlink")
    artifact = artifact_candidate.resolve()
    _reject_symlink_components(QUALITY_GATE_ROOT, "accepted quality gate root")
    root = QUALITY_GATE_ROOT.resolve()
    _require(root.is_dir(), "accepted quality gate root must be a directory")
    root_metadata = root.stat()
    _require(
        root_metadata.st_uid == os.getuid(),
        "accepted quality gate root must be owned by the current user",
    )
    _require(
        root_metadata.st_mode & 0o077 == 0,
        "accepted quality gate root must be private",
    )
    _require(
        artifact.parent == root or root in artifact.parents,
        "accepted quality gate artifact is outside the quality gate root",
    )
    _require(artifact.is_file() and not artifact.is_symlink(), "accepted quality gate artifact is missing")
    metadata = artifact.stat()
    _require(stat.S_ISREG(metadata.st_mode), "accepted quality gate artifact must be a regular file")
    _require(metadata.st_uid == os.getuid(), "accepted quality gate artifact must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, "accepted quality gate artifact must be private")
    _require(metadata.st_size <= 1024 * 1024, "accepted quality gate artifact exceeds 1 MiB")
    payload = artifact.read_bytes()
    expected_digest = _sha256(
        accepted.get("artifact_sha256"),
        "efficiency_eval.accepted_gate.artifact_sha256",
    )
    actual_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    _require(actual_digest == expected_digest, "accepted quality gate artifact digest does not match")
    try:
        receipt = _mapping(json.loads(payload), "accepted quality gate receipt")
    except json.JSONDecodeError as error:
        raise ContractError("accepted quality gate receipt must be valid JSON") from error
    validate_quality_gate_document(receipt)
    _require(receipt.get("filter_hash") == filter_hash, "reused efficiency gate filter hash does not match")
    _require(
        receipt.get("selection_policy_hash") == selection_policy_hash,
        "reused efficiency gate selection policy hash does not match",
    )
    _require(
        receipt.get("rubric_version") == rubric_version,
        "reused efficiency gate rubric version does not match",
    )
    _require(
        receipt.get("quality_policy_hash") == quality_policy_hash,
        "reused efficiency gate quality policy hash does not match",
    )


def _quality_dimensions(value: Any, label: str) -> dict[str, int]:
    dimensions = _mapping(value, label)
    _require(
        set(dimensions) == QUALITY_DIMENSIONS,
        f"{label} must contain the complete quality rubric",
    )
    scores: dict[str, int] = {}
    for name, score in dimensions.items():
        _require(
            isinstance(score, int)
            and not isinstance(score, bool)
            and MINIMUM_QUALITY_SCORE <= score <= 4,
            f"{label}.{name} must be an integer from {MINIMUM_QUALITY_SCORE} to 4 for an accepted report",
        )
        scores[name] = score
    return scores


def _validate_receipt_quality_comparison(value: Any) -> None:
    comparison = _mapping(value, "accepted quality gate receipt quality_comparison")
    _require(
        set(comparison)
        == {
            "method",
            "metric",
            "snapshot_digest",
            "quality_review_digest",
            "quality_review_ref",
            "packet_digest",
            "claim_ledger_digest",
            "label_map_digest",
            "baseline_label",
            "candidate_label",
            "required_claim_ids",
            "reviewer",
            "baseline",
            "candidate",
        },
        "accepted quality gate comparison must contain the exact allowed fields",
    )
    _require(
        comparison.get("method") == "blind_side_by_side",
        "accepted quality gate comparison must be blind_side_by_side",
    )
    metric = comparison.get("metric")
    _require(
        metric in {"input_tokens", "input_characters"},
        "accepted quality gate comparison metric is invalid",
    )
    _sha256(
        comparison.get("snapshot_digest"),
        "accepted quality gate receipt quality_comparison.snapshot_digest",
    )
    _sha256(
        comparison.get("quality_review_digest"),
        "accepted quality gate receipt quality_comparison.quality_review_digest",
    )
    _sha256(
        comparison.get("packet_digest"),
        "accepted quality gate receipt quality_comparison.packet_digest",
    )
    _sha256(
        comparison.get("claim_ledger_digest"),
        "accepted quality gate receipt quality_comparison.claim_ledger_digest",
    )
    _sha256(
        comparison.get("label_map_digest"),
        "accepted quality gate receipt quality_comparison.label_map_digest",
    )
    baseline_label = _text(
        comparison.get("baseline_label"),
        "accepted quality gate receipt quality_comparison.baseline_label",
    )
    candidate_label = _text(
        comparison.get("candidate_label"),
        "accepted quality gate receipt quality_comparison.candidate_label",
    )
    _require(
        {baseline_label, candidate_label} == {"A", "B"},
        "accepted quality gate labels must be distinct A and B",
    )
    required_claim_ids = _unique_texts(
        comparison.get("required_claim_ids"),
        "accepted quality gate receipt quality_comparison.required_claim_ids",
    )
    _require(
        required_claim_ids == sorted(set(required_claim_ids)),
        "accepted quality gate required_claim_ids must be unique and sorted",
    )
    _text(
        comparison.get("quality_review_ref"),
        "accepted quality gate receipt quality_comparison.quality_review_ref",
    )
    reviewer = _mapping(
        comparison.get("reviewer"),
        "accepted quality gate receipt quality_comparison.reviewer",
    )
    _require(
        set(reviewer) == {"provider", "model", "request_id"},
        "accepted quality gate reviewer must contain the exact provenance fields",
    )
    for field in ("provider", "model", "request_id"):
        _text(reviewer.get(field), f"accepted quality gate reviewer {field}")

    runs: dict[str, tuple[int, int | None, dict[str, int]]] = {}
    token_evidence_by_run: dict[str, dict[str, Any]] = {}
    for name in ("baseline", "candidate"):
        label = f"accepted quality gate receipt quality_comparison.{name}"
        run = _mapping(comparison.get(name), label)
        base_fields = {
            "report_digest",
            "input_digest",
            "input_characters",
            "quality_dimensions",
            "required_claim_count",
            "unsupported_claim_count",
            "privacy_violation_count",
            "critical_gates",
        }
        token_fields = {"input_tokens", "token_receipt_digest", "token_evidence"}
        expected_fields = base_fields | (token_fields if "input_tokens" in run else set())
        _require(
            set(run) == expected_fields,
            f"{label} must contain the exact allowed fields",
        )
        _sha256(run.get("report_digest"), f"{label}.report_digest")
        _sha256(run.get("input_digest"), f"{label}.input_digest")
        characters = _nonnegative_int(
            run.get("input_characters"), f"{label}.input_characters"
        )
        tokens_value = run.get("input_tokens")
        tokens = None
        if tokens_value is not None:
            tokens = _nonnegative_int(tokens_value, f"{label}.input_tokens")
            _sha256(
                run.get("token_receipt_digest"),
                f"{label}.token_receipt_digest",
            )
            token_evidence = _mapping(run.get("token_evidence"), f"{label}.token_evidence")
            _require(
                set(token_evidence)
                == {
                    "source",
                    "provider",
                    "model",
                    "request_id",
                    "counter_name",
                    "counter_version",
                },
                f"{label}.token_evidence must contain the exact provenance fields",
            )
            _require(
                token_evidence.get("source") in TOKEN_RECEIPT_SOURCES,
                f"{label}.token_evidence.source is invalid",
            )
            for field in (
                "provider",
                "model",
                "request_id",
                "counter_name",
                "counter_version",
            ):
                _text(token_evidence.get(field), f"{label}.token_evidence.{field}")
            token_evidence_by_run[name] = token_evidence
        else:
            _require(
                "token_receipt_digest" not in run and "token_evidence" not in run,
                f"{label} cannot claim token evidence without input_tokens",
            )
        scores = _quality_dimensions(
            run.get("quality_dimensions"), f"{label}.quality_dimensions"
        )
        required_claim_count = _nonnegative_int(
            run.get("required_claim_count"), f"{label}.required_claim_count"
        )
        _require(
            required_claim_count == len(required_claim_ids),
            f"{label} required claim count does not match the durable claim ledger",
        )
        _require(
            _nonnegative_int(
                run.get("unsupported_claim_count"),
                f"{label}.unsupported_claim_count",
            )
            == 0,
            f"{label} must not contain unsupported claims",
        )
        _require(
            _nonnegative_int(
                run.get("privacy_violation_count"),
                f"{label}.privacy_violation_count",
            )
            == 0,
            f"{label} must not contain privacy violations",
        )
        gates = _mapping(run.get("critical_gates"), f"{label}.critical_gates")
        _require(
            set(gates) == CRITICAL_GATES and all(item is True for item in gates.values()),
            f"{label} must preserve all critical gates",
        )
        runs[name] = (characters, tokens, scores)

    baseline_chars, baseline_tokens, baseline_scores = runs["baseline"]
    candidate_chars, candidate_tokens, candidate_scores = runs["candidate"]
    _require(
        candidate_chars < baseline_chars,
        "accepted quality gate receipt does not reduce bound input characters",
    )
    if metric == "input_tokens":
        _require(
            baseline_tokens is not None and candidate_tokens is not None,
            "token comparison receipt requires token evidence for both reports",
        )
        _require(
            candidate_tokens < baseline_tokens,
            "accepted quality gate receipt does not reduce input tokens",
        )
        for field in ("source", "provider", "model", "counter_name", "counter_version"):
            _require(
                token_evidence_by_run["baseline"][field]
                == token_evidence_by_run["candidate"][field],
                f"accepted quality gate token evidence must use the same {field}",
            )
        _require(
            token_evidence_by_run["baseline"]["request_id"]
            != token_evidence_by_run["candidate"]["request_id"],
            "accepted quality gate must use distinct provider request IDs",
        )
    else:
        _require(
            baseline_tokens is None and candidate_tokens is None,
            "character comparison receipt is only valid without tokenizer evidence",
        )
    for dimension, baseline_score in baseline_scores.items():
        _require(
            candidate_scores[dimension] >= baseline_score,
            f"accepted quality gate receipt regressed {dimension}",
        )


def _validate_quality_run(
    value: Any, label: str, required_claims: set[str]
) -> tuple[int, int | None, dict[str, int], set[str]]:
    run = _mapping(value, label)
    characters = _nonnegative_int(run.get("input_characters"), f"{label}.input_characters")
    tokens_value = run.get("input_tokens")
    tokens = None
    if tokens_value is not None:
        tokens = _nonnegative_int(tokens_value, f"{label}.input_tokens")
    _sha256(run.get("report_digest"), f"{label}.report_digest")
    report_ref = Path(_text(run.get("report_ref"), f"{label}.report_ref"))
    _require(
        not report_ref.is_absolute() and ".." not in report_ref.parts,
        f"{label}.report_ref must be a safe relative locator",
    )
    _sha256(run.get("input_digest"), f"{label}.input_digest")
    input_ref = Path(_text(run.get("input_ref"), f"{label}.input_ref"))
    _require(
        not input_ref.is_absolute() and ".." not in input_ref.parts,
        f"{label}.input_ref must be a safe relative locator",
    )
    if tokens is not None:
        _sha256(run.get("token_receipt_digest"), f"{label}.token_receipt_digest")
        token_receipt_ref = Path(
            _text(run.get("token_receipt_ref"), f"{label}.token_receipt_ref")
        )
        _require(
            not token_receipt_ref.is_absolute() and ".." not in token_receipt_ref.parts,
            f"{label}.token_receipt_ref must be a safe relative locator",
        )
    else:
        _require(
            "token_receipt_ref" not in run and "token_receipt_digest" not in run,
            f"{label} cannot provide a token receipt without input_tokens",
        )

    supported = set(
        _unique_texts(run.get("supported_claim_ids"), f"{label}.supported_claim_ids")
    )
    missing = sorted(required_claims - supported)
    _require(not missing, f"{label} is missing required claims: {missing}")
    extra = sorted(supported - required_claims)
    _require(not extra, f"{label} contains claims outside the frozen ledger: {extra}")
    _require(
        not _list(run.get("unsupported_claim_ids"), f"{label}.unsupported_claim_ids"),
        f"{label} must not contain unsupported claims",
    )
    _require(
        not _list(run.get("privacy_violations"), f"{label}.privacy_violations"),
        f"{label} must not contain privacy violations",
    )
    gates = _mapping(run.get("critical_gates"), f"{label}.critical_gates")
    _require(
        set(gates) == CRITICAL_GATES,
        f"{label}.critical_gates must contain the complete critical gate set",
    )
    _require(all(value is True for value in gates.values()), f"{label} has a failed critical gate")

    scores = _quality_dimensions(
        run.get("quality_dimensions"), f"{label}.quality_dimensions"
    )
    _unique_texts(run.get("grader_refs"), f"{label}.grader_refs")
    return characters, tokens, scores, set(gates)


def _safe_artifact(
    root_value: Path | None,
    ref_value: Any,
    digest_value: Any,
    label: str,
    *,
    maximum_bytes: int = 5 * 1024 * 1024,
) -> bytes:
    _require(root_value is not None, "artifact validation requires an artifact root")
    root_candidate = Path(root_value)
    _reject_symlink_components(root_candidate, "efficiency artifact root")
    _require(not root_candidate.is_symlink(), "efficiency artifact root must not be a symlink")
    root = root_candidate.resolve()
    _require(root.is_dir(), "efficiency artifact root must be a directory")
    reference = Path(_text(ref_value, f"{label} ref"))
    _require(
        not reference.is_absolute() and ".." not in reference.parts,
        f"{label} ref must be a safe relative path",
    )
    candidate = root / reference
    _require(not candidate.is_symlink(), f"{label} must not be a symlink")
    artifact = candidate.resolve()
    _require(
        artifact.parent == root or root in artifact.parents,
        f"{label} is outside the artifact root",
    )
    _require(artifact.is_file(), f"{label} is missing")
    metadata = artifact.stat()
    _require(stat.S_ISREG(metadata.st_mode), f"{label} must be a regular file")
    _require(metadata.st_uid == os.getuid(), f"{label} must be owned by the current user")
    _require(metadata.st_mode & 0o077 == 0, f"{label} must be private")
    _require(metadata.st_size <= maximum_bytes, f"{label} exceeds the size limit")
    payload = artifact.read_bytes()
    expected = _sha256(digest_value, f"{label} digest")
    actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    _require(actual == expected, f"{label} digest does not match")
    return payload


def _validate_review_task(
    manifest: dict[str, Any], outer_loop: dict[str, Any], artifact_root: Path | None
) -> None:
    review_task = _mapping(outer_loop.get("review_task"), "outer_loop.review_task")
    _require(
        set(review_task)
        == {
            "artifact_ref",
            "artifact_digest",
            "weekly_report_ref",
            "weekly_report_digest",
            "author",
            "week",
        },
        "outer_loop.review_task must contain the exact allowed fields",
    )
    task_payload = _safe_artifact(
        artifact_root,
        review_task.get("artifact_ref"),
        review_task.get("artifact_digest"),
        "outer-loop review task",
        maximum_bytes=256 * 1024,
    )
    weekly_report_payload = _safe_artifact(
        artifact_root,
        review_task.get("weekly_report_ref"),
        review_task.get("weekly_report_digest"),
        "outer-loop weekly report",
        maximum_bytes=8 * 1024 * 1024,
    )
    _validate_weekly_report_content(
        weekly_report_payload, "outer-loop weekly report"
    )
    try:
        task = _mapping(json.loads(task_payload), "outer-loop review task")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("outer-loop review task must be valid UTF-8 JSON") from error
    _require(
        set(task)
        == {
            "schema",
            "round_id",
            "ruleset_id",
            "author",
            "week",
            "weekly_report_digest",
            "status",
            "title",
            "checklist",
        },
        "outer-loop review task must contain the exact allowed fields",
    )
    _require(
        task.get("schema") == "addx.work_method_review_task.v1",
        "outer-loop review task schema is invalid",
    )
    for task_field, manifest_field in (
        ("round_id", "round_id"),
        ("ruleset_id", "ruleset_id"),
    ):
        _require(
            task.get(task_field) == manifest.get(manifest_field),
            f"outer-loop review task {task_field} does not match the manifest",
        )
    for field in ("author", "week", "weekly_report_digest"):
        _require(
            task.get(field) == review_task.get(field),
            f"outer-loop review task {field} does not match its binding",
        )
    _sha256(task.get("weekly_report_digest"), "outer-loop review task weekly_report_digest")
    _require(
        task.get("status") == "awaiting_human_review",
        "outer-loop review task must await human review",
    )
    _safe_report_text(task.get("title"), "outer-loop review task title", maximum=200)
    _safe_report_texts(
        task.get("checklist"), "outer-loop review task checklist", maximum=500
    )
    week = _text(task.get("week"), "outer-loop review task week")
    _require(
        WEEK_ID_RE.fullmatch(week) is not None,
        "outer-loop review task week must use YYYY-Www",
    )
    proposal = _mapping(manifest.get("trigger"), "trigger").get("proposal_window")
    proposal_window = _mapping(proposal, "trigger.proposal_window")
    _require(
        week in proposal_window.get("weekly_report_ids", []),
        "outer-loop review task week is outside the proposal window",
    )


def _validate_model_input(
    payload: bytes,
    comparison: dict[str, Any],
    run: dict[str, Any],
    label: str,
    snapshot_scenes: dict[str, str],
) -> str:
    try:
        document = _mapping(json.loads(payload), f"{label} input")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} input must be valid UTF-8 JSON") from error
    _require(
        set(document)
        == {
            "schema",
            "snapshot_digest",
            "filter_hash",
            "selection_policy_hash",
            "transform_id",
            "selection",
            "model_input",
        },
        f"{label} input must contain the exact allowed fields",
    )
    _require(
        document.get("schema") == "addx.work_method_model_input.v1",
        f"{label} input schema is invalid",
    )
    for field in ("snapshot_digest", "filter_hash", "selection_policy_hash"):
        _require(
            document.get(field) == comparison.get(field),
            f"{label} input {field} does not match the comparison",
        )
    _require(
        document.get("transform_id") == "scene-slices-v1",
        f"{label} input transform_id is invalid",
    )
    selections = _list(document.get("selection"), f"{label} input selection", nonempty=True)
    reconstructed: list[str] = []
    previous_ranges: dict[str, int] = {}
    for row in selections:
        selection = _mapping(row, f"{label} input selection row")
        _require(
            set(selection) == {"scene_id", "start", "end"},
            f"{label} input selection must contain only scene_id/start/end",
        )
        scene_id = _text(selection.get("scene_id"), f"{label} input selection scene_id")
        _require(scene_id in snapshot_scenes, f"{label} input selects an unknown scene")
        start = _nonnegative_int(selection.get("start"), f"{label} input selection start")
        end = _nonnegative_int(selection.get("end"), f"{label} input selection end")
        _require(start < end <= len(snapshot_scenes[scene_id]), f"{label} input selection range is invalid")
        _require(
            start >= previous_ranges.get(scene_id, 0),
            f"{label} input selection ranges must be ordered and non-overlapping",
        )
        previous_ranges[scene_id] = end
        reconstructed.append(snapshot_scenes[scene_id][start:end])
    model_input = document.get("model_input")
    _require(isinstance(model_input, str), f"{label} input model_input must be text")
    _require(
        model_input == "".join(reconstructed),
        f"{label} input is not derived from the frozen snapshot selection",
    )
    _require(
        len(model_input) == run.get("input_characters"),
        f"{label} input character count does not match the bound input artifact",
    )
    return model_input


def _validate_token_receipt(
    payload: bytes,
    comparison: dict[str, Any],
    run: dict[str, Any],
    label: str,
) -> dict[str, str]:
    try:
        receipt = _mapping(json.loads(payload), f"{label} token receipt")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} token receipt must be valid UTF-8 JSON") from error
    _require(
        set(receipt)
        == {
            "schema",
            "source",
            "snapshot_digest",
            "input_digest",
            "provider_response",
            "counter_name",
            "counter_version",
        },
        f"{label} token receipt must contain the exact allowed fields",
    )
    _require(
        receipt.get("schema") == "addx.work_method_input_token_receipt.v1",
        f"{label} token receipt schema is invalid",
    )
    _require(
        receipt.get("source") in TOKEN_RECEIPT_SOURCES,
        f"{label} token receipt source is invalid",
    )
    _require(
        receipt.get("snapshot_digest") == comparison.get("snapshot_digest"),
        f"{label} token receipt snapshot digest does not match",
    )
    _require(
        receipt.get("input_digest") == run.get("input_digest"),
        f"{label} token receipt input digest does not match",
    )
    response = _mapping(receipt.get("provider_response"), f"{label} provider response")
    _require(
        set(response)
        == {"provider", "model", "request_id", "output_digest", "usage"},
        f"{label} provider response must contain the exact allowed fields",
    )
    _require(
        _sha256(response.get("output_digest"), f"{label} provider output_digest")
        == run.get("report_digest"),
        f"{label} provider output digest does not match the report",
    )
    usage = _mapping(response.get("usage"), f"{label} provider usage")
    _require(
        set(usage) == {"prompt_tokens"},
        f"{label} provider usage must contain only prompt_tokens",
    )
    _require(
        _nonnegative_int(usage.get("prompt_tokens"), f"{label} provider prompt_tokens")
        == run.get("input_tokens"),
        f"{label} token receipt count does not match",
    )
    evidence = {"source": str(receipt["source"])}
    for field in ("provider", "model", "request_id"):
        evidence[field] = _text(response.get(field), f"{label} provider response {field}")
    for field in ("counter_name", "counter_version"):
        evidence[field] = _text(receipt.get(field), f"{label} token receipt {field}")
    return evidence


def _validate_scene_ledger_artifact(
    artifact_root: Path | None,
    ref_value: Any,
    digest_value: Any,
    expected_scene_ids: set[str],
    proposal_window: tuple[date, date],
    weekly_report_ids: set[str],
    evidence_cutoff: datetime,
    label: str,
) -> tuple[dict[str, date], dict[str, str]]:
    payload = _safe_artifact(
        artifact_root,
        ref_value,
        digest_value,
        label,
        maximum_bytes=12 * 1024 * 1024,
    )
    try:
        document = _mapping(json.loads(payload), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} must be valid UTF-8 JSON") from error
    _require(
        set(document) == {"schema", "scenes"}
        and document.get("schema") == "addx.work_method_frozen_snapshot.v1",
        f"{label} schema is invalid",
    )
    observed_dates: dict[str, date] = {}
    scene_contents: dict[str, str] = {}
    for row in _list(document.get("scenes"), f"{label} scenes", nonempty=True):
        scene = _mapping(row, f"{label} scene")
        _require(
            set(scene)
            == {"id", "content", "observed_at", "weekly_report_id", "source_records"},
            f"{label} scene must contain the exact evidence provenance fields",
        )
        scene_id = _text(scene.get("id"), f"{label} scene id")
        _require(scene_id not in scene_contents, f"duplicate {label} scene: {scene_id}")
        source_rows = _list(
            scene.get("source_records"),
            f"{label} scene {scene_id} source_records",
            nonempty=True,
        )
        _require(
            len(source_rows) <= 64,
            f"{label} scene {scene_id} has more than 64 source records",
        )
        source_times: list[datetime] = []
        source_lines: list[int] = []
        source_digests: set[str] = set()
        for index, source_value in enumerate(source_rows):
            source = _mapping(
                source_value, f"{label} scene {scene_id} source record {index}"
            )
            _require(
                set(source)
                == {
                    "record_digest",
                    "record_line",
                    "kind",
                    "provider",
                    "at",
                    "session_kind",
                    "session_id",
                },
                f"{label} scene {scene_id} source record fields are invalid",
            )
            digest = _sha256(
                source.get("record_digest"),
                f"{label} scene {scene_id} source record {index} digest",
            )
            _require(
                digest not in source_digests,
                f"{label} scene {scene_id} repeats a source record",
            )
            source_digests.add(digest)
            source_lines.append(
                _nonnegative_int(
                    source.get("record_line"),
                    f"{label} scene {scene_id} source record {index} line",
                )
            )
            _require(
                source_lines[-1] > 0,
                f"{label} scene {scene_id} source record line must be positive",
            )
            _require(
                source.get("kind")
                in {"human_input", "agent_instruction", "ai_output", "tool_call", "tool_result"},
                f"{label} scene {scene_id} source record kind is invalid",
            )
            _require(
                source.get("provider") in {"codex", "claude"},
                f"{label} scene {scene_id} source record provider is invalid",
            )
            _require(
                source.get("session_kind") in {"root", "subagent"},
                f"{label} scene {scene_id} source record session_kind is invalid",
            )
            session_id = source.get("session_id")
            _require(
                session_id is None
                or (isinstance(session_id, str) and len(session_id) <= 128),
                f"{label} scene {scene_id} source record session_id is invalid",
            )
            source_times.append(
                _iso_timestamp(
                    source.get("at"),
                    f"{label} scene {scene_id} source record {index} at",
                )
            )
        _require(
            source_lines == sorted(set(source_lines)),
            f"{label} scene {scene_id} source record lines must be unique and sorted",
        )
        expected_observed_at = max(source_times).isoformat().replace("+00:00", "Z")
        _require(
            scene.get("observed_at") == expected_observed_at,
            f"{label} scene {scene_id} observed_at does not match source records",
        )
        content = scene.get("content")
        _require(
            isinstance(content, str) and bool(content.strip()) and len(content) <= 256_000,
            f"{label} scene {scene_id} content must be 1..256,000 characters",
        )
        _reject_sensitive_scene_text(content, f"{label} scene {scene_id} content")
        observed_at = _iso_timestamp(
            scene.get("observed_at"), f"{label} scene {scene_id} observed_at"
        )
        _require(
            observed_at <= evidence_cutoff,
            f"{label} scene {scene_id} is after the evidence cutoff",
        )
        observed_date = observed_at.date()
        _require(
            proposal_window[0] <= observed_date <= proposal_window[1],
            f"{label} scene {scene_id} is outside the proposal window",
        )
        weekly_report_id = _text(
            scene.get("weekly_report_id"),
            f"{label} scene {scene_id} weekly_report_id",
        )
        _require(
            weekly_report_id in weekly_report_ids,
            f"{label} scene {scene_id} is outside the allowed weekly reports",
        )
        iso_year, iso_week, _ = observed_date.isocalendar()
        _require(
            weekly_report_id == f"{iso_year}-W{iso_week:02d}",
            f"{label} scene {scene_id} weekly report does not match observed_at",
        )
        observed_dates[scene_id] = observed_date
        scene_contents[scene_id] = content
    _require(
        set(scene_contents) == expected_scene_ids,
        f"{label} scenes do not match input.scene_ids",
    )
    return observed_dates, scene_contents


def _validate_quality_artifacts(
    comparison: dict[str, Any],
    artifact_root: Path | None,
    required_claims: set[str],
    expected_scene_ids: set[str],
    proposal_window: tuple[date, date],
    weekly_report_ids: set[str],
    evidence_cutoff: datetime,
) -> None:
    _, snapshot_scenes = _validate_scene_ledger_artifact(
        artifact_root,
        comparison.get("snapshot_ref"),
        comparison.get("snapshot_digest"),
        expected_scene_ids,
        proposal_window,
        weekly_report_ids,
        evidence_cutoff,
        "frozen snapshot",
    )
    report_payloads: dict[str, bytes] = {}
    token_evidence: dict[str, dict[str, str]] = {}
    for name in ("baseline", "candidate"):
        run = _mapping(comparison.get(name), f"efficiency_eval.{name}")
        report_payloads[name] = _safe_artifact(
            artifact_root,
            run.get("report_ref"),
            run.get("report_digest"),
            f"{name} report",
        )
        input_payload = _safe_artifact(
            artifact_root,
            run.get("input_ref"),
            run.get("input_digest"),
            f"{name} input",
            maximum_bytes=12 * 1024 * 1024,
        )
        _validate_model_input(input_payload, comparison, run, name, snapshot_scenes)
        if run.get("input_tokens") is not None:
            receipt_payload = _safe_artifact(
                artifact_root,
                run.get("token_receipt_ref"),
                run.get("token_receipt_digest"),
                f"{name} token receipt",
                maximum_bytes=1024 * 1024,
            )
            token_evidence[name] = _validate_token_receipt(
                receipt_payload, comparison, run, name
            )
    if token_evidence:
        _require(
            set(token_evidence) == {"baseline", "candidate"},
            "token evidence must cover both baseline and candidate",
        )
        for field in ("source", "provider", "model", "counter_name", "counter_version"):
            _require(
                token_evidence["baseline"][field] == token_evidence["candidate"][field],
                f"baseline and candidate token evidence must use the same {field}",
            )
        _require(
            token_evidence["baseline"]["request_id"]
            != token_evidence["candidate"]["request_id"],
            "baseline and candidate must use distinct provider request IDs",
        )

    protocol = _mapping(
        comparison.get("comparison_protocol"),
        "efficiency_eval.comparison_protocol",
    )
    _require(
        set(protocol)
        == {
            "method",
            "same_snapshot",
            "judge_input",
            "packet_ref",
            "packet_digest",
            "label_map_ref",
            "label_map_digest",
            "claim_ledger_ref",
            "claim_ledger_digest",
        },
        "efficiency comparison protocol must contain the exact allowed fields",
    )
    packet_payload = _safe_artifact(
        artifact_root,
        protocol.get("packet_ref"),
        protocol.get("packet_digest"),
        "blind quality input",
        maximum_bytes=12 * 1024 * 1024,
    )
    map_payload = _safe_artifact(
        artifact_root,
        protocol.get("label_map_ref"),
        protocol.get("label_map_digest"),
        "blind label map",
        maximum_bytes=1024 * 1024,
    )
    claim_ledger_payload = _safe_artifact(
        artifact_root,
        protocol.get("claim_ledger_ref"),
        protocol.get("claim_ledger_digest"),
        "required claim ledger",
        maximum_bytes=1024 * 1024,
    )
    try:
        packet = _mapping(json.loads(packet_payload), "blind quality input")
        label_map = _mapping(json.loads(map_payload), "blind label map")
        claim_ledger = _list(
            json.loads(claim_ledger_payload),
            "required claim ledger",
            nonempty=True,
        )
    except json.JSONDecodeError as error:
        raise ContractError("blind quality input and label map must be valid JSON") from error
    _require(
        packet.get("schema") == "addx.work_method_blind_quality_input.v1",
        "blind quality input schema is invalid",
    )
    _require(
        set(packet)
        == {
            "schema",
            "snapshot_digest",
            "quality_policy_hash",
            "rubric_version",
            "judge_input",
            "required_claims",
            "rubric",
            "reports",
        },
        "blind quality input must contain only the exact allowed fields",
    )
    _require(
        label_map.get("schema") == "addx.work_method_blind_label_map.v1",
        "blind label map schema is invalid",
    )
    _require(
        set(label_map)
        == {
            "schema",
            "snapshot_digest",
            "packet_digest",
            "baseline_label",
            "candidate_label",
            "baseline_report_digest",
            "candidate_report_digest",
        },
        "blind label map must contain only the exact allowed fields",
    )
    for document, label in ((packet, "blind quality input"), (label_map, "blind label map")):
        _require(
            document.get("snapshot_digest") == comparison.get("snapshot_digest"),
            f"{label} snapshot digest does not match",
        )
    _require(
        packet.get("quality_policy_hash") == comparison.get("quality_policy_hash"),
        "blind quality input policy hash does not match",
    )
    _require(
        packet.get("rubric_version") == comparison.get("rubric_version"),
        "blind quality input rubric version does not match",
    )
    _require(
        packet.get("judge_input") == protocol.get("judge_input"),
        "blind quality input contract does not match",
    )
    claim_rows = _list(
        packet.get("required_claims"),
        "blind quality input required claims",
        nonempty=True,
    )
    packet_claim_ids: set[str] = set()
    normalized_claims: list[dict[str, str]] = []
    for row in claim_rows:
        claim = _mapping(row, "blind quality input required claim")
        _require(
            set(claim) == {"id", "claim"},
            "blind quality input claim must contain only id and claim",
        )
        claim_id = _text(claim.get("id"), "blind quality input required claim id")
        claim_text = _text(claim.get("claim"), f"blind quality input claim {claim_id}")
        _require(claim_id not in packet_claim_ids, f"duplicate blind quality input claim: {claim_id}")
        packet_claim_ids.add(claim_id)
        normalized_claims.append({"id": claim_id, "claim": claim_text})
    _require(
        packet_claim_ids == required_claims,
        "blind quality input required claims do not match",
    )
    _require(
        claim_ledger == normalized_claims,
        "blind quality input claims do not match the frozen claim ledger",
    )
    rubric = _mapping(packet.get("rubric"), "blind quality input rubric")
    _require(
        rubric.get("schema") == "addx.work_method_report_quality.v1",
        "blind quality input rubric schema is invalid",
    )
    anchors = _mapping(rubric.get("scoring_anchors"), "blind quality input scoring anchors")
    _require(set(anchors) == {"0", "1", "2", "3", "4"}, "blind quality input scoring anchors are incomplete")
    try:
        canonical_rubric = _mapping(
            json.loads(QUALITY_POLICY.read_bytes()), "canonical report quality policy"
        )
    except json.JSONDecodeError as error:
        raise ContractError("canonical report quality policy must be valid JSON") from error
    _require(rubric == canonical_rubric, "blind quality input rubric does not match the canonical policy")
    expected_packet_digest = f"sha256:{hashlib.sha256(packet_payload).hexdigest()}"
    _require(
        label_map.get("packet_digest") == expected_packet_digest,
        "blind label map packet digest does not match",
    )
    labels = {
        _text(comparison[name].get("blind_label"), f"efficiency_eval.{name}.blind_label")
        for name in ("baseline", "candidate")
    }
    _require(labels == {"A", "B"}, "baseline and candidate must use distinct blind labels A and B")
    _require(label_map.get("baseline_label") == comparison["baseline"]["blind_label"], "blind label map baseline label does not match")
    _require(label_map.get("candidate_label") == comparison["candidate"]["blind_label"], "blind label map candidate label does not match")
    packet_reports = _mapping(packet.get("reports"), "blind quality input reports")
    _require(set(packet_reports) == labels, "blind quality input reports must use only labels A and B")
    for name in ("baseline", "candidate"):
        run = comparison[name]
        label = run["blind_label"]
        packet_report = _mapping(packet_reports.get(label), f"blind quality input report {label}")
        _require(
            set(packet_report) == {"report_digest", "content"},
            "blind quality input report must contain only the exact allowed fields",
        )
        digest = f"sha256:{hashlib.sha256(report_payloads[name]).hexdigest()}"
        _require(packet_report.get("report_digest") == digest, f"blind quality input {name} report digest does not match")
        _require(label_map.get(f"{name}_report_digest") == digest, f"blind label map {name} report digest does not match")
        try:
            report_text = report_payloads[name].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContractError(f"{name} report must be UTF-8") from error
        _require(packet_report.get("content") == report_text, f"blind quality input {name} report content does not match")

    quality_review = _mapping(
        comparison.get("quality_review"), "efficiency_eval.quality_review"
    )
    payload = _safe_artifact(
        artifact_root,
        quality_review.get("artifact_ref"),
        quality_review.get("artifact_sha256"),
        "blind quality review",
        maximum_bytes=1024 * 1024,
    )
    try:
        review = _mapping(json.loads(payload), "blind quality review")
    except json.JSONDecodeError as error:
        raise ContractError("blind quality review must be valid JSON") from error
    _require(
        review.get("schema") == "addx.work_method_blind_quality_review.v1",
        "blind quality review schema is invalid",
    )
    _require(
        set(review)
        == {
            "schema",
            "snapshot_digest",
            "packet_digest",
            "rubric_version",
            "method",
            "judge_input",
            "reviewer",
            "runs",
        },
        "blind quality review must contain only the exact allowed fields",
    )
    _require(
        review.get("snapshot_digest") == comparison.get("snapshot_digest"),
        "blind quality review snapshot digest does not match",
    )
    _require(
        review.get("packet_digest") == protocol.get("packet_digest"),
        "blind quality review packet digest does not match",
    )
    _require(
        review.get("rubric_version") == comparison.get("rubric_version"),
        "blind quality review rubric version does not match",
    )
    _require(review.get("method") == protocol.get("method"), "blind quality review method does not match")
    _require(
        review.get("judge_input") == protocol.get("judge_input"),
        "blind quality review input contract does not match",
    )
    reviewer = _mapping(review.get("reviewer"), "blind quality review reviewer")
    _require(
        set(reviewer) == {"provider", "model", "request_id"},
        "blind quality review reviewer must contain the exact provenance fields",
    )
    for field in ("provider", "model", "request_id"):
        _text(reviewer.get(field), f"blind quality review reviewer {field}")
    blind_runs = _mapping(review.get("runs"), "blind quality review runs")
    _require(set(blind_runs) == labels, "blind quality review runs do not match the manifest labels")
    for name in ("baseline", "candidate"):
        run = comparison[name]
        blind = _mapping(blind_runs.get(run["blind_label"]), f"blind quality review {run['blind_label']}")
        _require(
            set(blind)
            == {
                "report_digest",
                "supported_claim_ids",
                "unsupported_claim_ids",
                "privacy_violations",
                "critical_gates",
                "quality_dimensions",
            },
            "blind quality review run must contain only the exact allowed fields",
        )
        _require(blind.get("report_digest") == run.get("report_digest"), f"blind quality review {name} report digest does not match")
        _require(
            set(_unique_texts(blind.get("supported_claim_ids"), f"blind quality review {name} supported claims"))
            == required_claims,
            f"blind quality review {name} required claims do not match",
        )
        _require(blind.get("unsupported_claim_ids") == run.get("unsupported_claim_ids"), f"blind quality review {name} unsupported claims do not match")
        _require(blind.get("privacy_violations") == run.get("privacy_violations"), f"blind quality review {name} privacy result does not match")
        _require(blind.get("critical_gates") == run.get("critical_gates"), f"blind quality review {name} critical gates do not match")
        _require(blind.get("quality_dimensions") == run.get("quality_dimensions"), f"blind quality review {name} scores do not match")


def _validate_efficiency_eval(
    manifest: dict[str, Any], scan: dict[str, Any], artifact_root: Path | None
) -> None:
    comparison = _mapping(manifest.get("efficiency_eval"), "efficiency_eval")
    mode = comparison.get("mode")
    _require(mode in EFFICIENCY_MODES, "efficiency_eval.mode is invalid")
    filter_hash = _sha256(comparison.get("filter_hash"), "efficiency_eval.filter_hash")
    _require(
        filter_hash == _current_filter_hash(),
        "efficiency_eval.filter_hash does not match the current extractor pipeline",
    )
    selection_policy_hash = _sha256(
        comparison.get("selection_policy_hash"),
        "efficiency_eval.selection_policy_hash",
    )
    _require(
        selection_policy_hash == _current_selection_policy_hash(),
        "efficiency_eval.selection_policy_hash does not match the current selection policy",
    )
    rubric_version = _text(
        comparison.get("rubric_version"), "efficiency_eval.rubric_version"
    )
    quality_policy_hash = _sha256(
        comparison.get("quality_policy_hash"), "efficiency_eval.quality_policy_hash"
    )
    _require(
        quality_policy_hash == _current_quality_policy_hash(),
        "efficiency_eval.quality_policy_hash does not match the current rubric and eval suite",
    )
    _require(comparison.get("decision") == "accept", "efficiency eval must accept the candidate")

    if mode == "reuse":
        _require(
            scan.get("input_tokens") is None,
            "reuse mode must use the 120,000 character fallback unless current token usage is bound",
        )
        _accepted_gate_receipt(
            comparison.get("accepted_gate"),
            filter_hash,
            selection_policy_hash,
            quality_policy_hash,
            rubric_version,
        )
        return

    _text(comparison.get("snapshot_id"), "efficiency_eval.snapshot_id")
    _text(comparison.get("snapshot_ref"), "efficiency_eval.snapshot_ref")
    _sha256(comparison.get("snapshot_digest"), "efficiency_eval.snapshot_digest")
    _require(
        comparison.get("snapshot_ref") == manifest["input"].get("scene_ledger_ref")
        and comparison.get("snapshot_digest")
        == manifest["input"].get("scene_ledger_digest"),
        "efficiency snapshot must be the validated input scene ledger",
    )
    protocol = _mapping(
        comparison.get("comparison_protocol"),
        "efficiency_eval.comparison_protocol",
    )
    _require(
        protocol.get("method") == "blind_side_by_side",
        "efficiency comparison must use blind_side_by_side",
    )
    _require(
        protocol.get("same_snapshot") is True,
        "efficiency comparison must use the same frozen snapshot",
    )
    _require(
        protocol.get("judge_input") == "reports_claim_ledger_rubric_only",
        "efficiency judge may receive only reports, claim ledger, and rubric",
    )
    required_claim_rows = _unique_texts(
        comparison.get("required_claim_ids"), "efficiency_eval.required_claim_ids"
    )
    _require(
        len(required_claim_rows) == len(set(required_claim_rows)),
        "efficiency_eval.required_claim_ids must be unique",
    )
    required_claims = set(required_claim_rows)
    baseline_chars, baseline_tokens, baseline_scores, baseline_gates = _validate_quality_run(
        comparison.get("baseline"), "efficiency_eval.baseline", required_claims
    )
    candidate_chars, candidate_tokens, candidate_scores, candidate_gates = _validate_quality_run(
        comparison.get("candidate"), "efficiency_eval.candidate", required_claims
    )
    _require(
        candidate_gates == baseline_gates,
        "efficiency candidate critical gate set must match baseline",
    )
    _require(
        (baseline_tokens is None) == (candidate_tokens is None),
        "efficiency token evidence must be present on both baseline and candidate or neither",
    )
    _require(
        candidate_chars < baseline_chars,
        "efficiency candidate must reduce bound input characters",
    )
    if baseline_tokens is not None and candidate_tokens is not None:
        _require(
            candidate_tokens < baseline_tokens,
            "efficiency candidate must reduce input tokens",
        )
    for dimension, baseline_score in baseline_scores.items():
        _require(
            candidate_scores[dimension] >= baseline_score,
            f"efficiency candidate regressed {dimension}",
        )
    _validate_quality_artifacts(
        comparison,
        artifact_root,
        required_claims,
        set(manifest["input"]["scene_ids"]),
        _bounded_window(
            manifest["trigger"]["proposal_window"],
            "trigger.proposal_window",
            maximum_days=28,
        ),
        set(manifest["trigger"]["proposal_window"]["weekly_report_ids"]),
        _iso_timestamp(
            manifest["input"].get("evidence_cutoff_at"),
            "input.evidence_cutoff_at",
        ),
    )
    _require(
        candidate_chars == scan.get("input_characters"),
        "efficiency candidate characters must match input.scan",
    )
    if scan.get("input_tokens") is not None:
        _require(
            candidate_tokens == scan.get("input_tokens"),
            "efficiency candidate tokens must match input.scan",
        )


def _validate_v3_input(
    manifest: dict[str, Any], input_data: dict[str, Any], artifact_root: Path | None
) -> dict[str, date]:
    trigger = _mapping(manifest.get("trigger"), "trigger")
    source = trigger.get("source")
    _require(source in TRIGGER_SOURCES, "trigger.source is invalid")
    eval_start, eval_end = _bounded_window(
        trigger.get("eval_window"), "trigger.eval_window", maximum_days=7
    )
    proposal_window = _mapping(trigger.get("proposal_window"), "trigger.proposal_window")
    proposal_start, proposal_end = _bounded_window(
        proposal_window, "trigger.proposal_window", maximum_days=28
    )
    _require(
        proposal_start <= eval_start <= eval_end <= proposal_end,
        "eval window must be contained in the proposal window",
    )
    report_ids = _list(
        proposal_window.get("weekly_report_ids"),
        "trigger.proposal_window.weekly_report_ids",
    )
    _require(len(report_ids) <= 4, "proposal window can reference at most 4 weekly reports")
    parsed_report_ids: list[tuple[str, date, date]] = []
    for report_id_value in report_ids:
        report_id = _text(report_id_value, "weekly report id")
        match = WEEK_ID_RE.fullmatch(report_id)
        _require(bool(match), "weekly report id must use YYYY-Www")
        try:
            week_start = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
        except ValueError as error:
            raise ContractError(f"weekly report id is not a real ISO week: {report_id}") from error
        week_end = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 7)
        _require(
            week_end >= proposal_start and week_start <= proposal_end,
            f"weekly report id is outside the proposal window: {report_id}",
        )
        parsed_report_ids.append((report_id, week_start, week_end))
    _require(
        [row[0] for row in parsed_report_ids]
        == sorted({row[0] for row in parsed_report_ids}),
        "weekly report ids must be unique and sorted",
    )
    previous_cutoff = _iso_timestamp(
        proposal_window.get("previous_cutoff_at"),
        "trigger.proposal_window.previous_cutoff_at",
    )
    evidence_cutoff = _iso_timestamp(
        input_data.get("evidence_cutoff_at"), "input.evidence_cutoff_at"
    )
    _require(previous_cutoff < evidence_cutoff, "previous cutoff must precede evidence cutoff")
    if source == "weekly_report":
        _require(
            proposal_window.get("kind") == "rolling_28_days",
            "weekly_report trigger must use a rolling_28_days proposal window",
        )

    scene_dates, _ = _validate_scene_ledger_artifact(
        artifact_root,
        input_data.get("scene_ledger_ref"),
        input_data.get("scene_ledger_digest"),
        set(_unique_texts(input_data.get("scene_ids"), "input.scene_ids")),
        (proposal_start, proposal_end),
        {row[0] for row in parsed_report_ids},
        evidence_cutoff,
        "input scene ledger",
    )

    scan = _mapping(input_data.get("scan"), "input.scan")
    scanned = _nonnegative_int(scan.get("scanned_sessions"), "input.scan.scanned_sessions")
    selected = _nonnegative_int(scan.get("selected_sessions"), "input.scan.selected_sessions")
    skipped = _nonnegative_int(
        scan.get("skipped_cached_sessions"), "input.scan.skipped_cached_sessions"
    )
    _require(selected <= scanned, "selected_sessions cannot exceed scanned_sessions")
    _require(skipped <= scanned, "skipped_cached_sessions cannot exceed scanned_sessions")
    _require(
        selected + skipped <= scanned,
        "selected_sessions plus skipped_cached_sessions cannot exceed scanned_sessions",
    )
    _require(selected <= 8, "input.scan can select at most 8 sessions")
    characters = _nonnegative_int(
        scan.get("input_characters"), "input.scan.input_characters"
    )
    tokens = scan.get("input_tokens")
    if tokens is not None:
        tokens = _nonnegative_int(tokens, "input.scan.input_tokens")
        _require(tokens <= 200_000, "input.scan exceeds the 200,000 token budget")
    else:
        _require(characters <= 120_000, "input.scan exceeds the 120,000 character budget")
    coverage = scan.get("coverage")
    _require(coverage in SCAN_COVERAGE, "input.scan.coverage is invalid")
    if coverage == "complete":
        _require(
            selected + skipped == scanned,
            "complete coverage must account for every scanned session",
        )
    _require(
        scan.get("content_mode") == "redacted_evidence",
        "input.scan.content_mode must be redacted_evidence",
    )
    _text(scan.get("redaction_policy"), "input.scan.redaction_policy")
    _validate_efficiency_eval(manifest, scan, artifact_root)
    return scene_dates


def _validate_eval_driven(task: Any, *, require_weekly_evidence: bool = False) -> None:
    body = _mapping(task, "tasks.eval_driven_review")
    if require_weekly_evidence:
        outcome = body.get("outcome")
        _require(outcome in {"review", "needs-evidence"}, "eval-driven outcome is invalid")
        task_rows = _list(
            body.get("independent_task_ids"), "eval-driven independent tasks"
        )
        task_ids = [
            _text(item, "eval-driven independent task") for item in task_rows
        ]
        _require(len(task_ids) == len(set(task_ids)), "eval-driven independent tasks must be unique")
        if len(set(task_ids)) < 3:
            _require(
                outcome == "needs-evidence",
                "fewer than 3 independent tasks requires needs-evidence",
            )
        task_evidence = _list(body.get("task_evidence"), "eval-driven task_evidence")
        linked_tasks: set[str] = set()
        for row in task_evidence:
            link = _mapping(row, "eval-driven task evidence row")
            task_id = _text(link.get("task_id"), "eval-driven task evidence task_id")
            _require(task_id in set(task_ids), f"eval-driven task evidence references unknown task: {task_id}")
            _require(task_id not in linked_tasks, f"duplicate eval-driven task evidence: {task_id}")
            linked_tasks.add(task_id)
            _unique_texts(link.get("evidence_refs"), f"eval-driven task {task_id} evidence_refs")
        _require(linked_tasks == set(task_ids), "every eval-driven task needs evidence linkage")
        friction_ids: set[str] = set()
        for friction_value in _list(
            body.get("frictions"), "workflow friction findings", nonempty=True
        ):
            friction = _mapping(friction_value, "workflow friction finding")
            _require(
                set(friction)
                == {
                    "id",
                    "category",
                    "status",
                    "data_state",
                    "claim",
                    "evidence_refs",
                    "signals",
                    "impact",
                    "evaluation_action",
                    "confidence",
                },
                "workflow friction finding must contain the exact allowed fields",
            )
            friction_id = _text(friction.get("id"), "workflow friction id")
            _require(
                SAFE_FIELD_ID_RE.fullmatch(friction_id) is not None,
                "workflow friction id must be a safe identifier",
            )
            _require(
                friction_id not in friction_ids,
                f"duplicate workflow friction id: {friction_id}",
            )
            friction_ids.add(friction_id)
            _require(
                friction.get("category") in FRICTION_CATEGORIES,
                f"workflow friction {friction_id} category is invalid",
            )
            status = friction.get("status")
            _require(
                status in FRICTION_STATUS,
                f"workflow friction {friction_id} status is invalid",
            )
            data_state = friction.get("data_state")
            _require(
                data_state in FRICTION_DATA_STATES,
                f"workflow friction {friction_id} data_state is invalid",
            )
            if friction.get("category") == "telemetry":
                _require(
                    data_state != "not_applicable",
                    f"telemetry friction {friction_id} must classify its data state",
                )
            if data_state == "unresolved":
                _require(
                    status == "needs-evidence",
                    f"unresolved workflow friction {friction_id} must be needs-evidence",
                )
            for field in ("claim", "impact", "evaluation_action"):
                _safe_report_text(
                    friction.get(field), f"workflow friction {friction_id} {field}"
                )
            evidence_refs = _unique_texts(
                friction.get("evidence_refs"),
                f"workflow friction {friction_id} evidence_refs",
            )
            signals = _list(
                friction.get("signals"), f"workflow friction {friction_id} signals"
            )
            if status == "observed":
                _require(
                    bool(signals),
                    f"observed workflow friction {friction_id} needs a measurable signal",
                )
            for signal_value in signals:
                signal = _mapping(
                    signal_value, f"workflow friction {friction_id} signal"
                )
                _require(
                    set(signal) == {"name", "value", "unit", "source_ref"},
                    f"workflow friction {friction_id} signal fields are invalid",
                )
                signal_name = _text(
                    signal.get("name"),
                    f"workflow friction {friction_id} signal name",
                )
                _require(
                    SAFE_FIELD_ID_RE.fullmatch(signal_name) is not None,
                    f"workflow friction {friction_id} signal name must be a safe identifier",
                )
                value = signal.get("value")
                _require(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0,
                    f"workflow friction {friction_id} signal value is invalid",
                )
                _require(
                    signal.get("unit") in FRICTION_UNITS,
                    f"workflow friction {friction_id} signal unit is invalid",
                )
                source_ref = _text(
                    signal.get("source_ref"),
                    f"workflow friction {friction_id} signal source_ref",
                )
                _require(
                    source_ref in evidence_refs,
                    f"workflow friction {friction_id} signal must use an evidence_ref",
                )
            if status == "observed" and data_state == "measured":
                _require(
                    any(signal.get("value", 0) > 0 for signal in signals),
                    f"measured workflow friction {friction_id} needs a positive signal",
                )
            _require(
                friction.get("confidence") in CONFIDENCE,
                f"workflow friction {friction_id} confidence is invalid",
            )
    findings = _list(body.get("findings"), "eval-driven findings", nonempty=True)
    finding_ids: set[str] = set()
    for row in findings:
        finding = _mapping(row, "eval-driven finding")
        finding_id = _text(finding.get("id"), "finding id")
        _require(finding_id not in finding_ids, f"duplicate finding id: {finding_id}")
        finding_ids.add(finding_id)
        _safe_report_text(finding.get("claim"), f"finding {finding_id} claim")
        _unique_texts(finding.get("evidence_refs"), f"finding {finding_id} evidence_refs")
        _require(
            finding.get("confidence") in CONFIDENCE,
            f"finding {finding_id} has invalid confidence",
        )

    recommendations = _list(
        body.get("recommendations"), "eval-driven recommendations", nonempty=True
    )
    for row in recommendations:
        recommendation = _mapping(row, "eval-driven recommendation")
        recommendation_id = _text(recommendation.get("id"), "recommendation id")
        linked = _unique_texts(
            recommendation.get("finding_ids"),
            f"recommendation {recommendation_id} finding_ids",
        )
        unknown = sorted(set(linked) - finding_ids)
        _require(
            not unknown,
            f"recommendation {recommendation_id} links unknown finding: {unknown}",
        )
        _safe_report_text(
            recommendation.get("action"), f"recommendation {recommendation_id} action"
        )


def _validate_fixtures(value: Any, candidate_id: str) -> None:
    fixtures = _mapping(value, f"candidate {candidate_id} fixtures")
    for kind in ("positive", "negative", "boundary"):
        _safe_report_texts(
            fixtures.get(kind), f"candidate {candidate_id} {kind} fixtures"
        )


def _validate_addx_comparison_and_decision(
    candidate: dict[str, Any], candidate_id: str
) -> None:
    comparisons = _list(
        candidate.get("addx_skill_comparison"),
        f"candidate {candidate_id} AddX Skill comparison",
        nonempty=True,
    )
    compared_skills: set[str] = set()
    full_coverage = False
    for row in comparisons:
        comparison = _mapping(row, f"candidate {candidate_id} AddX Skill comparison row")
        _require(
            set(comparison)
            == {"skill", "path", "skill_digest", "coverage", "overlap", "gap"},
            f"candidate {candidate_id} AddX Skill comparison must contain the exact allowed fields",
        )
        skill = _text(comparison.get("skill"), f"candidate {candidate_id} compared skill")
        _require(skill not in compared_skills, f"candidate {candidate_id} repeats AddX Skill {skill}")
        compared_skills.add(skill)
        path = _text(comparison.get("path"), f"candidate {candidate_id} {skill} path")
        _require(
            path.startswith("skills/") and path.endswith("/SKILL.md"),
            f"candidate {candidate_id} {skill} must use a repository Skill path",
        )
        relative = Path(path)
        _require(
            not relative.is_absolute() and ".." not in relative.parts,
            f"candidate {candidate_id} {skill} path must stay inside the repository",
        )
        _require(
            relative.parent.name == skill,
            f"candidate {candidate_id} {skill} name must match its Skill path",
        )
        skill_path = REPOSITORY_ROOT / relative
        _reject_symlink_components(skill_path, f"candidate {candidate_id} {skill} path")
        skill_bytes: bytes | None = None
        if skill_path.is_file() and not skill_path.is_symlink():
            skill_bytes = skill_path.read_bytes()
        elif SKILL_CATALOG.is_file() and not SKILL_CATALOG.is_symlink():
            _reject_symlink_components(
                SKILL_CATALOG, f"candidate {candidate_id} AddX Skill catalog"
            )
            catalog_bytes = SKILL_CATALOG.read_bytes()
            _require(
                len(catalog_bytes) <= 16 * 1024 * 1024,
                "AddX Skill catalog exceeds 16 MiB",
            )
            try:
                catalog = _mapping(json.loads(catalog_bytes), "AddX Skill catalog")
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ContractError("AddX Skill catalog must be valid UTF-8 JSON") from error
            _require(
                set(catalog) == {"schema", "source", "skills"}
                and catalog.get("schema") == "addx.skill_catalog.v1"
                and catalog.get("source") == "repository-skills",
                "AddX Skill catalog contract is invalid",
            )
            matched = []
            for entry_value in _list(catalog.get("skills"), "AddX Skill catalog entries"):
                entry = _mapping(entry_value, "AddX Skill catalog entry")
                _require(
                    set(entry) == {"path", "digest", "content"},
                    "AddX Skill catalog entry fields are invalid",
                )
                entry_path = _text(entry.get("path"), "AddX Skill catalog entry path")
                if entry_path == path:
                    matched.append(entry)
            _require(
                len(matched) == 1,
                f"candidate {candidate_id} {skill} Skill file is missing from the repository and catalog",
            )
            catalog_entry = matched[0]
            content = catalog_entry.get("content")
            _require(
                isinstance(content, str),
                f"candidate {candidate_id} {skill} catalog content is invalid",
            )
            skill_bytes = content.encode("utf-8")
            _require(
                catalog_entry.get("digest")
                == "sha256:" + hashlib.sha256(skill_bytes).hexdigest(),
                f"candidate {candidate_id} {skill} catalog digest does not match its content",
            )
        _require(
            skill_bytes is not None,
            f"candidate {candidate_id} {skill} Skill file is missing",
        )
        _require(
            len(skill_bytes) <= 2 * 1024 * 1024,
            f"candidate {candidate_id} {skill} Skill file exceeds 2 MiB",
        )
        expected_skill_digest = _sha256(
            comparison.get("skill_digest"),
            f"candidate {candidate_id} {skill} skill_digest",
        )
        actual_skill_digest = "sha256:" + hashlib.sha256(skill_bytes).hexdigest()
        _require(
            expected_skill_digest == actual_skill_digest,
            f"candidate {candidate_id} {skill} Skill digest does not match",
        )
        coverage = comparison.get("coverage")
        _require(
            coverage in ADDX_COVERAGE,
            f"candidate {candidate_id} {skill} has invalid AddX coverage",
        )
        full_coverage = full_coverage or coverage == "full"
        _safe_report_text(
            comparison.get("overlap"), f"candidate {candidate_id} {skill} overlap"
        )
        _safe_report_text(
            comparison.get("gap"), f"candidate {candidate_id} {skill} gap"
        )

    decision = _mapping(
        candidate.get("change_decision"), f"candidate {candidate_id} change_decision"
    )
    action = decision.get("action")
    _require(action in CHANGE_ACTIONS, f"candidate {candidate_id} has invalid change action")
    targets = _list(decision.get("targets"), f"candidate {candidate_id} decision targets")
    target_names = [_text(item, f"candidate {candidate_id} decision target") for item in targets]
    _safe_report_text(
        decision.get("rationale"), f"candidate {candidate_id} decision rationale"
    )

    if action == "update":
        _require(bool(target_names), f"candidate {candidate_id} update target is required")
        unknown = sorted(set(target_names) - compared_skills)
        _require(
            not unknown,
            f"candidate {candidate_id} update target was not compared: {unknown}",
        )
        _require(
            not full_coverage,
            f"candidate {candidate_id} cannot update when an AddX Skill fully covers it",
        )
    elif action == "add":
        _require(not target_names, f"candidate {candidate_id} add decision cannot name an update target")
        _require(
            not full_coverage,
            f"candidate {candidate_id} cannot add when an AddX Skill fully covers it",
        )
    else:
        _require(
            not target_names,
            f"candidate {candidate_id} {action} decision cannot name update targets",
        )

    outcome = candidate.get("outcome")
    if outcome == "new-skill-proposal":
        _require(action == "add", f"candidate {candidate_id} new proposal must decide add")
    elif outcome == "extend-existing-under-review":
        _require(action == "update", f"candidate {candidate_id} extension must decide update")


def _validate_accepted_candidate(
    candidate: dict[str, Any],
    candidate_id: str,
    *,
    require_addx_comparison: bool,
    require_task_linkage: bool = False,
) -> None:
    task_ids = _unique_texts(
        candidate.get("independent_task_ids"),
        f"candidate {candidate_id} independent tasks",
        minimum=3,
    )
    _safe_report_texts(
        candidate.get("contexts"), f"candidate {candidate_id} contexts", minimum=2
    )
    for field in (
        "human_judgment",
        "trigger",
        "non_trigger",
        "stable_output",
        "stop_condition",
    ):
        _safe_report_text(candidate.get(field), f"candidate {candidate_id} {field}")
    _unique_texts(
        candidate.get("state_delta_evidence"),
        f"candidate {candidate_id} state_delta_evidence",
        minimum=3,
    )
    if require_task_linkage:
        links = _list(candidate.get("task_evidence"), f"candidate {candidate_id} task_evidence")
        linked_tasks: set[str] = set()
        for row in links:
            link = _mapping(row, f"candidate {candidate_id} task evidence row")
            task_id = _text(link.get("task_id"), f"candidate {candidate_id} task evidence task_id")
            _require(task_id in set(task_ids), f"candidate {candidate_id} links unknown task: {task_id}")
            _require(task_id not in linked_tasks, f"candidate {candidate_id} repeats task evidence: {task_id}")
            linked_tasks.add(task_id)
            _unique_texts(link.get("evidence_refs"), f"candidate {candidate_id} task {task_id} evidence_refs")
        _require(linked_tasks == set(task_ids), f"candidate {candidate_id} must link evidence for every task")
    coverage = _mapping(
        candidate.get("existing_skill_coverage"),
        f"candidate {candidate_id} existing_skill_coverage",
    )
    _require(
        coverage.get("status") in COVERAGE,
        f"candidate {candidate_id} has invalid coverage status",
    )
    _require(
        coverage.get("status") == "verified",
        f"candidate {candidate_id} accepted proposal requires verified Skill coverage",
    )
    _safe_report_texts(
        coverage.get("matches"), f"candidate {candidate_id} coverage matches"
    )
    if require_addx_comparison:
        _validate_addx_comparison_and_decision(candidate, candidate_id)
    _validate_fixtures(candidate.get("fixtures"), candidate_id)


def _validate_skill_proposals(
    task: Any,
    *,
    require_addx_comparison: bool,
    allow_empty: bool = False,
    require_task_linkage: bool = False,
) -> None:
    body = _mapping(task, "tasks.skill_proposal_discovery")
    candidates = _list(
        body.get("candidates"),
        "skill proposal candidates",
        nonempty=not allow_empty,
    )
    candidate_ids: set[str] = set()
    for row in candidates:
        candidate = _mapping(row, "skill proposal candidate")
        candidate_id = _text(candidate.get("id"), "candidate id")
        _require(candidate_id not in candidate_ids, f"duplicate candidate id: {candidate_id}")
        candidate_ids.add(candidate_id)
        _safe_report_text(candidate.get("method"), f"candidate {candidate_id} method")
        outcome = candidate.get("outcome")
        _require(outcome in OUTCOMES, f"candidate {candidate_id} has invalid outcome")
        if outcome in ACCEPTED_PROPOSAL_OUTCOMES:
            _validate_accepted_candidate(
                candidate,
                candidate_id,
                require_addx_comparison=require_addx_comparison,
                require_task_linkage=require_task_linkage,
            )
        else:
            _safe_report_texts(
                candidate.get("failed_gates"), f"candidate {candidate_id} failed_gates"
            )


def _validate_v3_scene_evidence_links(
    tasks: dict[str, Any],
    scene_ids: set[str],
    scene_dates: dict[str, date],
    eval_window: tuple[date, date],
) -> None:
    def require_refs(
        value: Any, label: str, *, window: tuple[date, date] | None = None
    ) -> None:
        for reference in _unique_texts(value, label):
            _require(
                EVIDENCE_REF_RE.fullmatch(reference) is not None,
                f"{label} must use a safe scene#locator",
            )
            _reject_sensitive_scene_text(reference, label)
            scene_id = reference.split("#", 1)[0]
            _require(
                "#" in reference and scene_id in scene_ids,
                f"{label} must reference a frozen in-window scene locator",
            )
            if window is not None:
                _require(
                    window[0] <= scene_dates[scene_id] <= window[1],
                    f"{label} must reference a scene inside the eval window",
                )

    review = _mapping(tasks.get("eval_driven_review"), "tasks.eval_driven_review")
    for finding in _list(review.get("findings"), "eval-driven findings"):
        row = _mapping(finding, "eval-driven finding")
        require_refs(
            row.get("evidence_refs"),
            "eval-driven finding evidence_refs",
            window=eval_window,
        )
    for link in _list(review.get("task_evidence"), "eval-driven task_evidence"):
        row = _mapping(link, "eval-driven task evidence row")
        require_refs(
            row.get("evidence_refs"),
            "eval-driven task evidence_refs",
            window=eval_window,
        )
    for friction_value in _list(review.get("frictions"), "workflow friction findings"):
        friction = _mapping(friction_value, "workflow friction finding")
        require_refs(
            friction.get("evidence_refs"),
            "workflow friction evidence_refs",
            window=eval_window,
        )

    proposals = _mapping(
        tasks.get("skill_proposal_discovery"), "tasks.skill_proposal_discovery"
    )
    for candidate_value in _list(proposals.get("candidates"), "skill proposal candidates"):
        candidate = _mapping(candidate_value, "skill proposal candidate")
        if candidate.get("outcome") not in ACCEPTED_PROPOSAL_OUTCOMES:
            continue
        require_refs(
            candidate.get("state_delta_evidence"),
            "skill proposal state_delta_evidence",
        )
        for link in _list(candidate.get("task_evidence"), "skill proposal task_evidence"):
            row = _mapping(link, "skill proposal task evidence row")
            require_refs(row.get("evidence_refs"), "skill proposal task evidence_refs")


def validate_manifest(document: Any, *, artifact_root: Path | None = None) -> None:
    """Validate a parsed manifest or raise ContractError."""
    manifest = _mapping(document, "manifest")
    schema = manifest.get("schema")
    _require(schema in SCHEMAS, f"schema must be one of {sorted(SCHEMAS)}")
    _text(manifest.get("round_id"), "round_id")
    _text(manifest.get("ruleset_id"), "ruleset_id")

    input_data = _mapping(manifest.get("input"), "input")
    scene_ids = _unique_texts(input_data.get("scene_ids"), "input.scene_ids")
    if schema == SCHEMA_V3:
        _require(len(set(scene_ids)) <= 8, "input.scene_ids can contain at most 8 scenes")
    _unique_texts(input_data.get("source_refs"), "input.source_refs")
    _text(input_data.get("evidence_cutoff_at"), "input.evidence_cutoff_at")
    scene_dates: dict[str, date] = {}
    if schema == SCHEMA_V3:
        scene_dates = _validate_v3_input(manifest, input_data, artifact_root)

    tasks = _mapping(manifest.get("tasks"), "tasks")
    _require(set(tasks) == TASK_NAMES, "manifest must contain exactly the two review tasks")
    _validate_eval_driven(
        tasks["eval_driven_review"], require_weekly_evidence=schema == SCHEMA_V3
    )
    _validate_skill_proposals(
        tasks["skill_proposal_discovery"],
        require_addx_comparison=schema in {SCHEMA_V2, SCHEMA_V3},
        allow_empty=schema == SCHEMA_V3,
        require_task_linkage=schema == SCHEMA_V3,
    )
    if schema == SCHEMA_V3:
        _validate_v3_scene_evidence_links(
            tasks,
            set(scene_ids),
            scene_dates,
            _bounded_window(
                manifest["trigger"]["eval_window"],
                "trigger.eval_window",
                maximum_days=7,
            ),
        )

    inner_loop = _mapping(manifest.get("inner_loop"), "inner_loop")
    _require(inner_loop.get("rules_frozen") is True, "inner-loop rules must be frozen")
    checks = _list(inner_loop.get("checks"), "inner-loop checks", nonempty=True)
    for row in checks:
        check = _mapping(row, "inner-loop check")
        check_id = _text(check.get("id"), "inner-loop check id")
        _require(
            check.get("status") == "pass",
            f"inner-loop check {check_id} must pass before handoff",
        )
        _text(check.get("evidence"), f"inner-loop check {check_id} evidence")
    _list(inner_loop.get("corrections"), "inner_loop.corrections")

    outer_loop = _mapping(manifest.get("outer_loop"), "outer_loop")
    _require(
        outer_loop.get("status") == "awaiting_human_review",
        "outer loop must wait for human review",
    )
    if schema == SCHEMA_V3:
        _validate_review_task(manifest, outer_loop, artifact_root)
    else:
        _text(outer_loop.get("review_task"), "outer_loop.review_task")

    publication = _mapping(manifest.get("publication"), "publication")
    _require(
        publication.get("status") == "local_only",
        "publication.status must remain local_only without separate authorization",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        document = read_private_manifest(args.manifest)
        validate_manifest(document, artifact_root=args.manifest.resolve().parent)
    except (OSError, ContractError) as error:
        print(f"invalid: {error}")
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
