"""Run the Project native-VOC journey in resumable collect and publish phases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "scripts" / "api.py"
RUN_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$", re.ASCII)
STATE_VERSION = "native-voc-journey-state.v2"
_EVIDENCE_ID = re.compile(r"^ev_[a-f0-9]{64}$")
_EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATE_MAX_BYTES = 128 * 1024


class JourneyError(Exception):
    """A code-only failure safe for acceptance output."""

    def __init__(self, code: str, *, http_status: int | None = None) -> None:
        super().__init__(code)
        self.http_status = http_status


def required_text(name: str, maximum: int) -> str:
    value = os.environ.get(name, "")
    if not value or len(value) > maximum:
        raise JourneyError("invalid_native_voc_input")
    return value


def native_input() -> dict[str, Any]:
    try:
        value = json.loads(required_text("USER_RESEARCH_NATIVE_VOC_INPUT_JSON", 65_536))
    except json.JSONDecodeError:
        raise JourneyError("invalid_native_voc_input") from None
    if not isinstance(value, dict):
        raise JourneyError("invalid_native_voc_input")
    return value


def poll_settings() -> tuple[int, float]:
    try:
        attempts = int(os.environ.get("USER_RESEARCH_TDD_POLL_ATTEMPTS", "120"))
        interval = float(os.environ.get("USER_RESEARCH_TDD_POLL_INTERVAL_SECONDS", "5"))
    except ValueError:
        raise JourneyError("invalid_poll_settings") from None
    if not 1 <= attempts <= 240 or not 0 <= interval <= 30:
        raise JourneyError("invalid_poll_settings")
    return attempts, interval


def operation(available: frozenset[str], name: str) -> str:
    if name not in available:
        raise JourneyError("required_operation_unavailable")
    return name


def cli(
    name: str,
    *,
    path: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {}
    if path:
        request["path"] = path
    if query:
        request["query"] = query
    if body is not None:
        request["body"] = body
    command = [sys.executable, str(API), name, "--request-stdin"]
    env = dict(os.environ)
    if output is not None:
        command.extend(("--output", output.name))
        env["AUDIENCE_ATTACHMENT_DIR"] = os.path.abspath(str(output.parent))
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            input=json.dumps(request, separators=(",", ":"), sort_keys=True),
            check=False,
            capture_output=True,
            text=True,
            timeout=70,
        )
    except subprocess.TimeoutExpired:
        raise JourneyError("cli_timeout") from None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise JourneyError("cli_invalid_output") from None
    if completed.returncode:
        code = payload.get("error") if isinstance(payload, dict) else None
        status = payload.get("http_status") if isinstance(payload, dict) else None
        raise JourneyError(
            code if isinstance(code, str) else "cli_request_failed",
            http_status=status if type(status) is int else None,
        )
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise JourneyError("cli_invalid_output")
    return result


def capabilities() -> frozenset[str]:
    try:
        completed = subprocess.run(
            [sys.executable, str(API), "capabilities"],
            cwd=ROOT,
            env=dict(os.environ),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise JourneyError("cli_timeout") from None
    try:
        operations = json.loads(completed.stdout).get("operations")
    except (json.JSONDecodeError, AttributeError):
        raise JourneyError("cli_invalid_output") from None
    if completed.returncode or not isinstance(operations, list):
        raise JourneyError("capability_discovery_failed")
    if any(not isinstance(item, str) for item in operations):
        raise JourneyError("capability_discovery_failed")
    return frozenset(operations)


def context(available: frozenset[str]) -> tuple[str, str]:
    value = cli(operation(available, "get_project_personal_key_context"))
    project_id = value.get("project_id")
    revision = value.get("binding_revision")
    allowed = value.get("allowed_actions")
    needed = {"idea.write", "voc.collect", "voc.results.read", "report.publish"}
    if (
        not isinstance(project_id, str)
        or not isinstance(revision, str)
        or not isinstance(allowed, list)
        or not needed.issubset(set(allowed))
    ):
        raise JourneyError("insufficient_project_authority")
    return project_id, revision


def _transient_failure(exc: JourneyError) -> bool:
    """5xx and transport loss are unread outcomes. A definite 4xx is not."""
    status = exc.http_status
    if isinstance(status, int):
        return 500 <= status <= 599
    return str(exc) in {"transport_error", "cli_timeout"}


def bound_resource(result: dict[str, Any], project_id: str, revision: str) -> dict[str, Any]:
    resource = result.get("resource")
    if (
        result.get("project_id") != project_id
        or result.get("binding_revision") != revision
        or not isinstance(resource, dict)
    ):
        raise JourneyError("native_voc_binding_mismatch")
    return resource


def paths() -> tuple[str, Path, Path, Path]:
    token = os.environ.get("USER_RESEARCH_TDD_RUN_TOKEN", "native-voc")
    if RUN_TOKEN.fullmatch(token) is None:
        raise JourneyError("invalid_native_voc_input")
    output = Path(required_text("USER_RESEARCH_NATIVE_VOC_OUTPUT_DIR", 2_000))
    if not output.is_dir() or output.is_symlink():
        raise JourneyError("invalid_output_path")
    os.environ["AUDIENCE_ATTACHMENT_DIR"] = os.path.abspath(str(output))
    return (
        token,
        output,
        output / f"native-voc-source-{token}.json",
        output / f"native-voc-state-{token}.json",
    )


def _approved_file_bytes(output_dir: Path, raw: str, maximum: int, error: str) -> bytes:
    """Read one regular file directly inside the approved output directory."""
    candidate = Path(raw)
    if (
        "\x00" in raw
        or not candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.name in {"", ".", ".."}
    ):
        raise JourneyError(error)
    try:
        root = output_dir.resolve(strict=True)
        parent = candidate.parent.resolve(strict=True)
        details = candidate.lstat()
    except OSError:
        raise JourneyError(error) from None
    if parent != root or stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise JourneyError(error)
    if details.st_size > maximum:
        raise JourneyError(error)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise JourneyError(error) from None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            content = handle.read(maximum + 1)
    except OSError:
        raise JourneyError(error) from None
    if len(content) > maximum:
        raise JourneyError(error)
    return content


def approved_report_text(output_dir: Path) -> str:
    """Read Markdown only from a regular file directly inside the approved output directory."""
    raw = required_text("USER_RESEARCH_NATIVE_VOC_REPORT_PATH", 2_000)
    content = _approved_file_bytes(output_dir, raw, 49_152, "invalid_native_voc_report")
    try:
        report = content.decode("utf-8")
    except UnicodeDecodeError:
        raise JourneyError("invalid_native_voc_report") from None
    if not report.strip():
        raise JourneyError("invalid_native_voc_report")
    return report


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        leaves: list[str] = []
        for item in value:
            leaves.extend(_string_leaves(item))
        return leaves
    if isinstance(value, dict):
        leaves = []
        for item in value.values():
            leaves.extend(_string_leaves(item))
        return leaves
    return []


def citable_leaves(items: Any) -> list[str]:
    """Strings actually read from Dataset items, excluding bare URLs and emails."""
    if not isinstance(items, list):
        raise JourneyError("invalid_native_voc_state")
    leaves: list[str] = []
    for raw in _string_leaves(items):
        text = raw.strip()
        if not text or len(text) > 4000:
            continue
        lowered = text.lower()
        if lowered.startswith(("http://", "https://")) or _EMAIL_ADDRESS.fullmatch(text):
            continue
        leaves.append(text)
    return leaves


def _voice_text_is_excerpt(text: str, leaves: list[str]) -> bool:
    for leaf in leaves:
        if text == leaf or (len(text) >= 12 and text in leaf):
            return True
    return False


def _normalize_voice(item: Any, leaves: list[str]) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) - {"text", "theme", "translation", "evidence_id"}:
        raise JourneyError("invalid_native_voc_voices")
    text = item.get("text")
    theme = item.get("theme")
    translation = item.get("translation")
    if (
        not isinstance(text, str)
        or not isinstance(theme, str)
        or not isinstance(translation, dict)
        or set(translation) - {"locale", "text"}
    ):
        raise JourneyError("invalid_native_voc_voices")
    text = text.strip()
    theme = theme.strip()
    translated = translation.get("text")
    locale = translation.get("locale", "zh-CN")
    if (
        not 1 <= len(text) <= 1000
        or not 1 <= len(theme) <= 120
        or locale != "zh-CN"
        or not isinstance(translated, str)
        or not 1 <= len(translated.strip()) <= 160
        or not _voice_text_is_excerpt(text, leaves)
    ):
        raise JourneyError("invalid_native_voc_voices")
    voice: dict[str, Any] = {
        "text": text,
        "theme": theme,
        "translation": {"locale": "zh-CN", "text": translated.strip()},
    }
    if "evidence_id" in item and item.get("evidence_id") is not None:
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or _EVIDENCE_ID.fullmatch(evidence_id) is None:
            raise JourneyError("invalid_native_voc_voices")
        voice["evidence_id"] = evidence_id
    return voice


def load_representative_voices(output_dir: Path, leaves: list[str]) -> list[dict[str, Any]]:
    """Author-supplied voices. Required when the saved items contain a citable excerpt."""
    raw = os.environ.get("USER_RESEARCH_NATIVE_VOC_VOICES_PATH", "")
    if not raw:
        if leaves:
            raise JourneyError("native_voc_voices_required")
        return []
    if len(raw) > 2_000:
        raise JourneyError("invalid_native_voc_voices")
    content = _approved_file_bytes(output_dir, raw, 16_384, "invalid_native_voc_voices")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JourneyError("invalid_native_voc_voices") from None
    if parsed == []:
        if leaves:
            raise JourneyError("native_voc_voices_required")
        return []
    if not leaves or not isinstance(parsed, list) or not 1 <= len(parsed) <= 6:
        raise JourneyError("invalid_native_voc_voices")
    voices = [_normalize_voice(item, leaves) for item in parsed]
    texts = [voice["text"] for voice in voices]
    if len(texts) != len(set(texts)):
        raise JourneyError("invalid_native_voc_voices")
    return voices


def _voices_match(current: dict[str, Any], voices: list[dict[str, Any]]) -> bool:
    observed = current.get("representative_voices", [])
    status = current.get("voices_status", "not_provided")
    if not isinstance(observed, list):
        return False
    if not voices:
        return status == "not_provided" and observed == []
    if status != "available" or len(observed) != len(voices):
        return False
    for expected, item in zip(voices, observed):
        translation = item.get("translation") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("text") != expected["text"]
            or item.get("theme") != expected["theme"]
            or not isinstance(translation, dict)
            or translation.get("locale") != "zh-CN"
            or translation.get("text") != expected["translation"]["text"]
        ):
            return False
    return True


def regular_bytes(path: Path, maximum: int) -> bytes:
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum:
            raise OSError
        return path.read_bytes()
    except OSError:
        raise JourneyError("invalid_output_path") from None


def write_or_verify(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if regular_bytes(path, max(1, len(content))) != content:
            raise JourneyError("existing_artifact_mismatch") from None
        return
    except OSError:
        raise JourneyError("invalid_output_path") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        raise JourneyError("invalid_output_path") from None


def replace_regular(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.replace-{os.getpid()}")
    if temporary.exists():
        raise JourneyError("invalid_output_path")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise JourneyError("invalid_output_path") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise JourneyError("invalid_output_path") from None


def download_or_verify(
    name: str,
    output: Path,
    *,
    path: dict[str, str],
    query: dict[str, str],
) -> dict[str, Any]:
    if not output.exists():
        downloaded = cli(name, path=path, query=query, output=output)
        return {key: value for key, value in downloaded.items() if key != "path"} | {
            "path": str(output)
        }
    existing = regular_bytes(output, 16 * 1024 * 1024)
    temporary = output.with_name(f".{output.name}.verify-{os.getpid()}")
    if temporary.exists():
        raise JourneyError("existing_artifact_mismatch")
    try:
        downloaded = cli(name, path=path, query=query, output=temporary)
        if regular_bytes(temporary, 16 * 1024 * 1024) != existing:
            raise JourneyError("existing_artifact_mismatch")
        return {
            **{key: value for key, value in downloaded.items() if key != "path"},
            "bytes": len(existing),
            "sha256": hashlib.sha256(existing).hexdigest(),
            "path": str(output),
        }
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_source(
    source: dict[str, Any], project_id: str, revision: str, idea_id: str, voc_id: str
) -> None:
    source_context = source.get("context")
    evidence = source_context.get("evidence") if isinstance(source_context, dict) else None
    sources = source_context.get("sources") if isinstance(source_context, dict) else None
    has_raw_sample = isinstance(sources, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("sampled_item_count"), int)
        and item["sampled_item_count"] > 0
        and isinstance(item.get("sample_content_hash"), str)
        and re.fullmatch(r"[0-9a-f]{64}", item["sample_content_hash"]) is not None
        for item in sources
    )
    if (
        source.get("project_id") != project_id
        or source.get("binding_revision") != revision
        or source.get("idea_id") != idea_id
        or source.get("parent_kind") != "voc"
        or source.get("parent_id") != voc_id
        or source.get("source_mode") != "native_dataset"
        or not isinstance(source.get("source_revision_id"), str)
        or not isinstance(source.get("source_fingerprint"), str)
        or not isinstance(evidence, list)
        or (not evidence and not has_raw_sample)
    ):
        raise JourneyError("native_voc_report_source_invalid")


def persist_source(
    path: Path,
    source: dict[str, Any],
    project_id: str,
    revision: str,
    idea_id: str,
    voc_id: str,
) -> dict[str, Any]:
    """Keep the first immutable source evidence when an equivalent read has display drift."""
    if not path.exists():
        write_or_verify(path, json.dumps(source, indent=2, sort_keys=True).encode() + b"\n")
        return source
    try:
        saved = json.loads(regular_bytes(path, 2 * 1024 * 1024))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JourneyError("invalid_native_voc_state") from None
    if not isinstance(saved, dict):
        raise JourneyError("invalid_native_voc_state")
    validate_source(saved, project_id, revision, idea_id, voc_id)
    if saved.get("source_revision_id") != source.get("source_revision_id") or saved.get(
        "source_fingerprint"
    ) != source.get("source_fingerprint"):
        raise JourneyError("native_voc_report_source_changed")
    return saved


def _dataset_items(
    available: frozenset[str],
    dataset_path: dict[str, str],
    project_id: str,
    revision: str,
    dataset_id: str,
) -> list[Any]:
    items: list[Any] = []
    offset = 0
    for _ in range(200):
        page = bound_resource(
            cli(
                operation(available, "project_voc_dataset_items"),
                path=dataset_path,
                query={"offset": str(offset), "limit": "20"},
            ),
            project_id,
            revision,
        )
        page_items = page.get("items")
        if (
            page.get("dataset_id") != dataset_id
            or page.get("offset") != offset
            or not isinstance(page_items, list)
            or page.get("count") != len(page_items)
            or not isinstance(page.get("has_more"), bool)
        ):
            raise JourneyError("native_voc_dataset_binding_mismatch")
        items.extend(page_items)
        if page["has_more"] is False:
            return items
        next_offset = page.get("next_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise JourneyError("native_voc_dataset_page_incomplete")
        offset = next_offset
    raise JourneyError("native_voc_dataset_page_incomplete")


def _discovery_after_unavailable_read(
    available: frozenset[str],
    project_id: str,
    revision: str,
    idea_id: str,
    request_id: str,
    voc_id: Any,
) -> dict[str, Any]:
    """Report the same VOC from discovery. Do not start another paid run."""
    matched: dict[str, Any] | None = None
    offset = 0
    for _ in range(20):
        page = cli(
            operation(available, "project_voc_discovery"),
            path={"project_id": project_id, "idea_id": idea_id},
            query={"kind": "native", "offset": str(offset), "limit": "50"},
        )
        if (
            page.get("project_id") != project_id
            or page.get("binding_revision") != revision
            or page.get("idea_id") != idea_id
            or page.get("kind") != "native"
            or page.get("offset") != offset
            or not isinstance(page.get("items"), list)
            or not isinstance(page.get("has_more"), bool)
        ):
            raise JourneyError("native_voc_binding_mismatch")
        for item in page["items"]:
            if not isinstance(item, dict):
                continue
            if item.get("idea_id") != idea_id:
                continue
            if isinstance(voc_id, str) and item.get("voc_id") == voc_id:
                matched = item
                break
        if matched is not None or page["has_more"] is False:
            break
        next_offset = offset + len(page["items"])
        if next_offset <= offset:
            raise JourneyError("native_voc_dataset_page_incomplete")
        offset = next_offset
    else:
        raise JourneyError("native_voc_dataset_page_incomplete")
    status = matched.get("status") if matched is not None else None
    detail_url = matched.get("voc_detail_url") if matched is not None else None
    return {
        "phase": "collect",
        "next_action": "poll_same_request_do_not_restart",
        "project_id": project_id,
        "idea_id": idea_id,
        "request_id": request_id,
        "voc_id": voc_id if isinstance(voc_id, str) else None,
        "discovery_status": status if isinstance(status, str) else "not_listed",
        "voc_detail_url": detail_url if isinstance(detail_url, str) else None,
        "collection_status": "read_unavailable",
        "analysis_status": "incomplete_until_terminal_dataset",
    }


def collect() -> dict[str, Any]:
    available = capabilities()
    project_id, revision = context(available)
    token, output_dir, source_path, state_path = paths()
    search = required_text("USER_RESEARCH_NATIVE_VOC_SEARCH", 200)
    actor_id = required_text("USER_RESEARCH_NATIVE_VOC_ACTOR_ID", 255)
    actor_input = native_input()
    export_format = os.environ.get("USER_RESEARCH_NATIVE_VOC_EXPORT_FORMAT", "jsonl")
    if export_format not in {"csv", "jsonl"}:
        raise JourneyError("invalid_native_voc_input")

    idea = cli(
        operation(available, "personal_idea_create"),
        path={"project_id": project_id},
        body={
            "title": f"Native VOC TDD {token}",
            "description": "Disposable native VOC evidence and report journey",
            "idempotency_key": f"native-voc-idea-{token}-0001",
        },
    )
    idea_id = bound_resource(idea, project_id, revision).get("idea_id")
    if not isinstance(idea_id, str):
        raise JourneyError("native_voc_binding_mismatch")
    actor_page = bound_resource(
        cli(
            operation(available, "project_apify_actor_search"),
            path={"project_id": project_id},
            query={"search": search, "offset": "0", "limit": "50"},
        ),
        project_id,
        revision,
    )
    matches = [
        item
        for item in actor_page.get("items", [])
        if isinstance(item, dict) and item.get("actor_id") == actor_id
    ]
    if len(matches) != 1:
        raise JourneyError("native_voc_actor_not_discovered")
    actor = bound_resource(
        cli(
            operation(available, "project_apify_actor_detail"),
            path={"project_id": project_id, "actor_id": actor_id},
        ),
        project_id,
        revision,
    )
    if (
        actor.get("actor_id") != actor_id
        or actor.get("is_public") is not True
        or actor.get("is_deprecated") is True
    ):
        raise JourneyError("native_voc_actor_unavailable")
    schema = bound_resource(
        cli(
            operation(available, "project_apify_actor_schema"),
            path={"project_id": project_id, "actor_id": actor_id},
        ),
        project_id,
        revision,
    )
    if schema.get("actor_id") != actor_id or not isinstance(schema.get("input_schema"), dict):
        raise JourneyError("native_voc_actor_schema_invalid")
    build = os.environ.get("USER_RESEARCH_NATIVE_VOC_BUILD") or schema.get("build")
    if not isinstance(build, str):
        raise JourneyError("native_voc_actor_schema_invalid")
    start = cli(
        operation(available, "project_voc_native_start"),
        path={"project_id": project_id, "idea_id": idea_id},
        body={
            "idempotency_key": f"native-voc-{token}-0001",
            "actor_id": actor_id,
            "build": build,
            "input": actor_input,
        },
    )
    request_id = start.get("request_id")
    identity = (project_id, revision, idea_id, actor_id, build, actor_input)
    if (
        start.get("project_id"),
        start.get("binding_revision"),
        start.get("idea_id"),
        start.get("actor_id"),
        start.get("build"),
        start.get("input"),
    ) != identity or not isinstance(request_id, str):
        raise JourneyError("native_voc_binding_mismatch")
    attempts, interval = poll_settings()
    observed = start
    unavailable = 0
    for attempt in range(attempts):
        try:
            observed = cli(
                operation(available, "project_voc_native_read"),
                path={"project_id": project_id, "idea_id": idea_id, "request_id": request_id},
            )
        except JourneyError as exc:
            if not _transient_failure(exc):
                raise
            unavailable += 1
            if attempt + 1 < attempts and interval:
                time.sleep(interval)
            continue
        unavailable = 0
        run_value = observed.get("run")
        if (
            (
                observed.get("project_id"),
                observed.get("binding_revision"),
                observed.get("idea_id"),
                observed.get("actor_id"),
                observed.get("build"),
                observed.get("input"),
            )
            != identity
            or observed.get("request_id") != request_id
            or not isinstance(run_value, dict)
        ):
            raise JourneyError("native_voc_binding_mismatch")
        if run_value.get("terminal") is True:
            break
        if attempt + 1 < attempts and interval:
            time.sleep(interval)
    else:
        if unavailable:
            return _discovery_after_unavailable_read(
                available, project_id, revision, idea_id, request_id, start.get("voc_id")
            )
        raise JourneyError("native_voc_execution_timeout")
    run_value = observed["run"]
    voc_id = observed.get("voc_id")
    dataset_id = run_value.get("dataset_id")
    run_id = run_value.get("run_id")
    if (
        run_value.get("status") != "succeeded"
        or not isinstance(voc_id, str)
        or not isinstance(dataset_id, str)
        or not isinstance(run_id, str)
    ):
        raise JourneyError("native_voc_execution_failed")
    dataset_path = {
        "project_id": project_id,
        "idea_id": idea_id,
        "voc_id": voc_id,
        "dataset_id": dataset_id,
    }
    listing = cli(
        operation(available, "project_voc_dataset_list"),
        path={"project_id": project_id, "idea_id": idea_id, "voc_id": voc_id},
    )
    datasets = listing.get("resource")
    if (
        listing.get("project_id") != project_id
        or listing.get("binding_revision") != revision
        or not isinstance(datasets, list)
        or len([d for d in datasets if isinstance(d, dict) and d.get("dataset_id") == dataset_id])
        != 1
    ):
        raise JourneyError("native_voc_dataset_binding_mismatch")
    metadata = bound_resource(
        cli(operation(available, "project_voc_dataset_metadata"), path=dataset_path),
        project_id,
        revision,
    )
    if metadata.get("dataset_id") != dataset_id:
        raise JourneyError("native_voc_dataset_binding_mismatch")
    items = _dataset_items(available, dataset_path, project_id, revision, dataset_id)
    items_path = output_dir / f"native-voc-items-{token}.json"
    items_bytes = (json.dumps(items, ensure_ascii=False, sort_keys=True) + "\n").encode()
    write_or_verify(items_path, items_bytes)
    excerpt_count = len(citable_leaves(items))
    dataset_output = output_dir / f"native-voc-{token}-{dataset_id}.{export_format}"
    attachment = download_or_verify(
        operation(available, "project_voc_dataset_export"),
        dataset_output,
        path=dataset_path,
        query={"format": export_format},
    )
    report_query = {
        "idea_id": idea_id,
        "parent_kind": "voc",
        "parent_id": voc_id,
        "source_mode": "native_dataset",
    }
    source = cli(
        operation(available, "project_get_report_source"),
        path={"project_id": project_id},
        query=report_query,
    )
    validate_source(source, project_id, revision, idea_id, voc_id)
    source = persist_source(source_path, source, project_id, revision, idea_id, voc_id)
    state = {
        "state_version": STATE_VERSION,
        "project_id": project_id,
        "binding_revision": revision,
        "idea_id": idea_id,
        "actor_id": actor_id,
        "build": build,
        "input": actor_input,
        "request_id": request_id,
        "run_id": run_id,
        "voc_id": voc_id,
        "dataset_id": dataset_id,
        "source_revision_id": source["source_revision_id"],
        "source_fingerprint": source["source_fingerprint"],
        "dataset_path": str(dataset_output),
        "dataset_sha256": attachment.get("sha256"),
        "dataset_items_path": str(items_path),
        "dataset_items_sha256": hashlib.sha256(items_bytes).hexdigest(),
        "citable_excerpt_count": excerpt_count,
        "source_path": str(source_path),
    }
    write_or_verify(state_path, json.dumps(state, indent=2, sort_keys=True).encode() + b"\n")
    return {
        "phase": "collect",
        "next_action": (
            "author_voices_and_markdown_then_publish"
            if excerpt_count
            else "author_grounded_markdown_then_publish"
        ),
        "citable_excerpt_count": excerpt_count,
        "project_id": project_id,
        "idea_id": idea_id,
        "actor_id": actor_id,
        "request_id": request_id,
        "run_id": run_id,
        "voc_id": voc_id,
        "dataset_id": dataset_id,
        "dataset_item_count": metadata.get("item_count"),
        "sampled_item_count": len(items),
        "sample_has_more": False,
        "dataset_items_path": str(items_path),
        "dataset_attachment": attachment,
        "dataset_path": str(dataset_output),
        "report_source_path": str(source_path),
        "state_path": str(state_path),
        "collection_status": "succeeded",
        "analysis_status": "awaiting_agent_authored_report",
    }


def publish() -> dict[str, Any]:
    available = capabilities()
    project_id, revision = context(available)
    token, output_dir, source_path, state_path = paths()
    try:
        state = json.loads(regular_bytes(state_path, STATE_MAX_BYTES))
        saved_source = json.loads(regular_bytes(source_path, 2 * 1024 * 1024))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JourneyError("invalid_native_voc_state") from None
    if (
        not isinstance(state, dict)
        or not isinstance(saved_source, dict)
        or state.get("state_version") != STATE_VERSION
        or state.get("project_id") != project_id
        or state.get("binding_revision") != revision
        or state.get("source_path") != str(source_path)
    ):
        raise JourneyError("invalid_native_voc_state")
    idea_id = state.get("idea_id")
    voc_id = state.get("voc_id")
    if not isinstance(idea_id, str) or not isinstance(voc_id, str):
        raise JourneyError("invalid_native_voc_state")
    validate_source(saved_source, project_id, revision, idea_id, voc_id)
    report_query = {
        "idea_id": idea_id,
        "parent_kind": "voc",
        "parent_id": voc_id,
        "source_mode": "native_dataset",
    }
    current_source = cli(
        operation(available, "project_get_report_source"),
        path={"project_id": project_id},
        query=report_query,
    )
    validate_source(current_source, project_id, revision, idea_id, voc_id)
    for source in (saved_source, current_source):
        if source.get("source_revision_id") != state.get("source_revision_id") or source.get(
            "source_fingerprint"
        ) != state.get("source_fingerprint"):
            raise JourneyError("native_voc_report_source_changed")
    report = approved_report_text(output_dir)
    items_path = output_dir / f"native-voc-items-{token}.json"
    if state.get("dataset_items_path") != str(items_path) or not isinstance(
        state.get("dataset_items_sha256"), str
    ):
        raise JourneyError("invalid_native_voc_state")
    items_bytes = regular_bytes(items_path, 2 * 1024 * 1024)
    if hashlib.sha256(items_bytes).hexdigest() != state["dataset_items_sha256"]:
        raise JourneyError("native_voc_items_changed")
    try:
        saved_items = json.loads(items_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JourneyError("invalid_native_voc_state") from None
    voices = load_representative_voices(output_dir, citable_leaves(saved_items))

    def current_report() -> dict[str, Any]:
        return cli(
            operation(available, "project_get_current_report"),
            path={"project_id": project_id},
            query=report_query,
        )

    def same_publication(current: dict[str, Any]) -> bool:
        body = current.get("report")
        return (
            current.get("project_id") == project_id
            and current.get("binding_revision") == revision
            and current.get("idea_id") == idea_id
            and current.get("parent_id") == voc_id
            and current.get("source_revision_id") == state.get("source_revision_id")
            and current.get("source_fingerprint") == state.get("source_fingerprint")
            and isinstance(body, dict)
            and body.get("content") == report
            and isinstance(current.get("report_revision_id"), str)
            and _voices_match(current, voices)
        )

    saved_revision = state.get("report_revision_id")
    if isinstance(saved_revision, str):
        publication = current_report()
        if (
            not same_publication(publication)
            or publication.get("report_revision_id") != saved_revision
        ):
            raise JourneyError("native_voc_report_binding_mismatch")
    else:
        try:
            publish_body = {
                "idea_id": idea_id,
                "parent_kind": "voc",
                "parent_id": voc_id,
                "source_mode": "native_dataset",
                "source_revision_id": state["source_revision_id"],
                "source_fingerprint": state["source_fingerprint"],
                "report": {"format": "markdown", "content": report},
            }
            if voices:
                publish_body["representative_voices"] = voices
            publication = cli(
                operation(available, "project_publish_report"),
                path={"project_id": project_id},
                body=publish_body,
            )
        except JourneyError as exc:
            if not _transient_failure(exc):
                raise
            publication = current_report()
            if not same_publication(publication):
                raise
    report_revision_id = publication.get("report_revision_id")
    if (
        publication.get("project_id") != project_id
        or publication.get("binding_revision") != revision
        or publication.get("idea_id") != idea_id
        or publication.get("parent_id") != voc_id
        or publication.get("source_revision_id") != state["source_revision_id"]
        or publication.get("source_fingerprint") != state["source_fingerprint"]
        or publication.get("report", {}).get("content") != report
        or not isinstance(report_revision_id, str)
        or not _voices_match(publication, voices)
    ):
        raise JourneyError("native_voc_report_binding_mismatch")
    state["report_revision_id"] = report_revision_id
    replace_regular(
        state_path,
        (json.dumps(state, indent=2, sort_keys=True) + "\n").encode(),
    )
    current = cli(
        operation(available, "project_get_current_report"),
        path={"project_id": project_id},
        query=report_query,
    )
    if (
        current.get("report_revision_id") != report_revision_id
        or current.get("source_fingerprint") != state["source_fingerprint"]
        or current.get("report", {}).get("content") != report
        or not _voices_match(current, voices)
    ):
        raise JourneyError("native_voc_report_binding_mismatch")
    report_output = output_dir / f"native-voc-report-{token}-{report_revision_id}.md"
    report_existed = report_output.exists()
    attachment = download_or_verify(
        operation(available, "project_report_download"),
        report_output,
        path={"project_id": project_id},
        query=report_query,
    )
    if regular_bytes(report_output, 49_152) != report.encode("utf-8"):
        if not report_existed:
            try:
                report_output.unlink()
            except OSError:
                pass
        raise JourneyError("native_voc_report_download_mismatch")
    return {
        "phase": "publish",
        "project_id": project_id,
        "idea_id": idea_id,
        "voc_id": voc_id,
        "dataset_id": state.get("dataset_id"),
        "report_revision_id": report_revision_id,
        "report_attachment": attachment,
        "report_path": str(report_output),
        "collection_status": "succeeded",
        "analysis_status": "published_and_downloaded",
        "voices_status": "available" if voices else "not_provided",
        "voice_count": len(voices),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("collect", "publish"), default="collect")
    args = parser.parse_args(argv)
    try:
        result = collect() if args.phase == "collect" else publish()
        print(json.dumps({"ok": True, "result": result}, separators=(",", ":"), sort_keys=True))
        return 0
    except JourneyError as exc:
        payload: dict[str, Any] = {"error": str(exc), "ok": False}
        if isinstance(exc.http_status, int):
            payload["http_status"] = exc.http_status
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
