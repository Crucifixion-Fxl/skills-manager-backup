#!/usr/bin/env python3
"""Publish bounded prose for the oldest owner-fixed pending activity summary.

The public command accepts text plus one opaque facts receipt echoed from the
runner result; the receipt binds the prose turn to exactly the request whose
facts the model saw, so a stale turn can never publish into a later request.
Channel, project, request, publisher identity, state and Buzz executable all
come from the owner-controlled Desk runner manifest.  A request whose send
outcome is unknown is never sent again: retries may only prove the prior
event by exact readback and ACK it.  A send that was definitively rejected
never created an event, so a retry resends exactly its bound durable content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_desk_runner as desk_runner  # noqa: E402
import gitlab_buzz_sync as sync  # noqa: E402


MAX_SUMMARY_CHARS = 1_000
MAX_SUMMARY_BYTES = 4_000
# Single-quote wrapping is only provably shell-safe when the prose alphabet
# excludes quote, backslash, every shell metacharacter and line separators
# (LF/CRLF are control chars; NEL and U+2028/U+2029 are not).
SUMMARY_FORBIDDEN_CHARS = frozenset("'\\`$;&|<>()")
SUMMARY_FORBIDDEN_CODEPOINTS = frozenset({0x85, 0x2028, 0x2029})
SUMMARY_FORBIDDEN_RE = re.compile(
    r"(?i)(?:@|nostr:|npub1|(?:https?|buzz)://|www\.|\[gitlab-notify:|(?:^|\s)events\s*:|"
    r"\b(?:event|source|request)[-_ ]?id\b|\b[0-9a-f]{64}\b|"
    r"#[0-9]+|![0-9]+|[0-9]{5,}|\b(?:issue|mr|iid|编号)\s*[:#]?\s*[0-9]+)"
)


MAX_SUMMARY_LINKS = 30
MAX_LINK_CHARS = 2_000
LINK_URL_RE = re.compile(r"https?://[^\s\x00-\x1f\x7f]+")
LINK_REF_RE = re.compile(r"[A-Za-z0-9_.\-/]{1,120}")
OBJECT_LABELS = {
    "push": "推送", "pipeline": "流水线", "deployment": "部署", "note": "评论", "milestone": "里程碑",
    "wiki": "Wiki", "member": "成员变更", "sync": "同步", "other": "其他",
}


class PublishError(RuntimeError):
    """The summary cannot be proven safe to publish or ACK."""


def _validated_summary(value: Any) -> str:
    if not isinstance(value, str):
        raise PublishError("summary must be text")
    summary = value.strip()
    if (
        not summary
        or summary != value
        or len(summary) > MAX_SUMMARY_CHARS
        or len(summary.encode("utf-8")) > MAX_SUMMARY_BYTES
        or any(
            ord(char) < 32 or ord(char) == 127
            or ord(char) in SUMMARY_FORBIDDEN_CODEPOINTS
            or char in SUMMARY_FORBIDDEN_CHARS
            for char in summary
        )
        or SUMMARY_FORBIDDEN_RE.search(summary)
    ):
        raise PublishError(
            "summary must be one bounded plain-text line without ids, links, mentions, "
            "protocol fields, numeric internal ids or shell metacharacters"
        )
    return summary


def _open_inventory_locks(entries: list[dict[str, Any]]) -> list[Any]:
    try:
        scopes = [(entry["config"], Path(entry["step"]["state_dir"])) for entry in entries]
        return sync.acquire_scope_locks(scopes, "summary publisher inventory")
    except sync.SyncError as exc:
        raise PublishError(str(exc)) from None


def _find_pending(syncer: sync.Syncer, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ledger = syncer._read_outbox()
    pending = [
        item for item in ledger["pending"]
        if item.get("change_id") == request_id and item.get("kind") == "summary_request"
    ]
    if len(pending) != 1:
        raise PublishError("summary request is no longer pending")
    item = pending[0]
    payload = item.get("payload")
    try:
        sync.public_summary_request(request_id, payload)
    except sync.SyncError as exc:
        raise PublishError(str(exc)) from None
    if item.get("status") not in {"SUMMARIZING", "PUBLISHING"}:
        raise PublishError("summary request has an invalid durable state")
    return item, ledger


SLASH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+")


def verify_summary_against_facts(prose: str, facts: Any) -> None:
    """Reject prose whose counts or ref names disagree with the bound facts.

    Every digit run must equal a count derivable from the facts (the total,
    one object-class count, or one fact's commit count); every path-shaped
    token must name a ref or title that is actually in the facts.  Mentions
    and tags stay impossible through the character filter.
    """

    if not isinstance(facts, list) or not facts:
        raise PublishError("summary facts are not a non-empty list")
    per_object: dict[str, int] = {}
    allowed_numbers = {len(facts)}
    refs_and_titles: list[str] = []
    for fact in facts:
        if not isinstance(fact, dict):
            raise PublishError("summary fact has an invalid schema")
        per_object[fact.get("object")] = per_object.get(fact.get("object"), 0) + 1
        if isinstance(fact.get("commits"), int) and not isinstance(fact.get("commits"), bool):
            allowed_numbers.add(fact["commits"])
        refs_and_titles.extend(
            str(fact.get(key) or "") for key in ("ref", "title")
        )
    allowed_numbers.update(per_object.values())
    # Only free-standing numerals are count claims: digits embedded in an
    # identifier (h3, utf8, feature/h3-evidence-push) are names, not counts.
    # CJK neighbours are NOT word characters for this boundary.
    for token in re.findall(r"(?<![A-Za-z0-9_])[0-9]+(?![A-Za-z0-9_])", prose):
        if int(token) not in allowed_numbers:
            raise PublishError("summary mentions a count that the bound facts do not support")
    for token in SLASH_TOKEN_RE.findall(prose):
        if not any(token in candidate for candidate in refs_and_titles):
            raise PublishError("summary names a ref that the bound facts do not contain")


def summary_link_lines(facts: list[dict[str, Any]]) -> list[str]:
    """Links come from the GitLab facts, never from the prose: one line per distinct http(s) URL."""

    seen: list[str] = []
    lines: list[str] = []
    for fact in facts:
        url = fact.get("url")
        if (
            not isinstance(url, str) or len(url) > MAX_LINK_CHARS
            or not LINK_URL_RE.fullmatch(url) or url in seen
        ):
            continue
        seen.append(url)
        if len(lines) < MAX_SUMMARY_LINKS:
            ref = fact.get("ref")
            label = OBJECT_LABELS.get(fact.get("object"), OBJECT_LABELS["other"])
            parts = [label, *([ref] if isinstance(ref, str) and LINK_REF_RE.fullmatch(ref) else []), url]
            lines.append(" ".join(parts))
    if len(seen) > MAX_SUMMARY_LINKS:
        lines.append(f"另有 {len(seen) - MAX_SUMMARY_LINKS} 个链接未列出")
    return lines


def _render(payload: dict[str, Any], prose: str) -> str:
    prose = _validated_summary(prose)
    verify_summary_against_facts(prose, payload.get("facts"))
    for source_key in payload.get("source_keys") or []:
        if isinstance(source_key, str) and source_key in prose:
            raise PublishError("summary must not expose private source event ids")
    content = "\n".join([prose, *summary_link_lines(payload["facts"])])
    if len(content.encode("utf-8")) > sync.DIGEST_BYTE_LIMIT:
        raise PublishError("rendered summary exceeds the Buzz message size limit")
    return content


PUBLICATION_KEYS = {"content", "content_sha256", "phase"}
PUBLICATION_PHASES = {"bound", "unproven"}


def _prepare(
    syncer: sync.Syncer, request_id: str, prose: str, facts: str,
) -> tuple[dict[str, Any], bool, str]:
    item, ledger = _find_pending(syncer, request_id)
    # Bind the prose turn to exactly the facts the runner showed before any
    # durable state changes: a stale turn's receipt must fail closed here.
    if facts != sync.facts_digest(item["payload"]["facts"]):
        raise PublishError("summary facts receipt does not match the pending request")
    if item["status"] == "PUBLISHING":
        publication = item.get("publication")
        if (
            not isinstance(publication, dict)
            or set(publication) != PUBLICATION_KEYS
            or publication.get("phase") not in PUBLICATION_PHASES
            or not isinstance(publication.get("content"), str)
            or publication.get("content_sha256")
            != hashlib.sha256(publication["content"].encode("utf-8")).hexdigest()
        ):
            raise PublishError("publishing request has invalid durable content")
        content = publication["content"]
        prose = content.split("\n", 1)[0]
        # Bound before links existed (prose only) or rendered with links: both must re-derive exactly.
        if content != _validated_summary(prose) and content != _render(item["payload"], prose):
            raise PublishError("publishing request content does not re-render from its bound facts")
        verify_summary_against_facts(prose, item.get("payload", {}).get("facts"))
        return item, False, content
    if item["status"] != "SUMMARIZING":
        raise PublishError("summary request was not claimed by the fixed runner")
    content = _render(item["payload"], prose)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    item["status"] = "PUBLISHING"
    item["publication"] = {"content": content, "content_sha256": digest, "phase": "bound"}
    syncer._write_outbox(ledger)
    return item, True, content


def _mark_unproven(syncer: sync.Syncer, request_id: str) -> None:
    """Durably record that a send's outcome is unknown: only readback may ever ACK it."""

    ledger = syncer._read_outbox()
    item = next((entry for entry in ledger["pending"] if entry.get("change_id") == request_id), None)
    if not isinstance(item, dict) or item.get("status") != "PUBLISHING":
        raise PublishError("summary request is no longer publishing")
    publication = item.get("publication")
    if not isinstance(publication, dict) or publication.get("phase") != "bound":
        raise PublishError("summary publication cannot be marked unproven")
    publication["phase"] = "unproven"
    syncer._write_outbox(ledger)


def _acked_summary_event_ids(syncer: sync.Syncer) -> set[str]:
    ids: set[str] = set()
    for record in syncer._read_outbox()["acked"]:
        if record.get("kind") == "summary_request" and isinstance(record.get("result_id"), str):
            ids.add(record["result_id"])
    return ids


def _matches(syncer: sync.Syncer, buzz: Any, item: dict[str, Any]) -> list[dict[str, Any]]:
    publication = item["publication"]
    try:
        queued = sync.parse_timestamp(item.get("queued_at"), "summary queued_at")
        events = buzz.channel_messages(int((queued - sync.DEDUPE_OVERLAP).timestamp()))
    except sync.SyncError as exc:
        raise PublishError(str(exc)) from None
    # An identical earlier summary (a different request's ACKed event, or any
    # event older than this request) is never proof that THIS request was sent.
    already_acked = _acked_summary_event_ids(syncer)
    queued_unix = int(queued.timestamp())
    matches = []
    for event in events:
        if (
            isinstance(event.get("id"), str)
            and event["id"] in already_acked
        ):
            continue
        created = event.get("created_at")
        if not sync._positive_int(created) or created < queued_unix:
            continue
        if (
            event.get("pubkey") == syncer.publisher
            and event.get("content") == publication["content"]
            and [tag[:2] for tag in sync._tag_values(event, "h")] == [["h", syncer.channel]]
            and not sync._tag_values(event, "e")
            and not sync._tag_values(event, "p")
            and isinstance(event.get("id"), str)
            and sync.HEX64_RE.fullmatch(event["id"])
        ):
            matches.append(event)
    return matches


def _verify_sent(syncer: sync.Syncer, buzz: Any, event_id: str, content: str) -> None:
    if not isinstance(event_id, str) or not sync.HEX64_RE.fullmatch(event_id):
        raise PublishError("Buzz returned no valid event id")
    try:
        events = buzz.thread(event_id)
    except sync.SyncError as exc:
        raise PublishError(str(exc)) from None
    matches = [
        event for event in events
        if event.get("id") == event_id
        and event.get("pubkey") == syncer.publisher
        and event.get("content") == content
        and [tag[:2] for tag in sync._tag_values(event, "h")] == [["h", syncer.channel]]
        and not sync._tag_values(event, "e")
        and not sync._tag_values(event, "p")
    ]
    if len(matches) != 1:
        raise PublishError("Buzz summary strict readback did not match")


def _check_delivery_boundary(syncer: sync.Syncer, gitlab: Any, buzz: Any, payload: dict[str, Any]) -> None:
    project_id = payload.get("project_id")
    if project_id not in syncer.config["gitlab"]["projects"]:
        raise PublishError("summary request project is outside the fixed config")
    user = gitlab.current_user()
    if user.get("id") != syncer.bot_user_id or user.get("username") != syncer.bot_username:
        raise PublishError("GitLab token identity does not match the configured bot")
    try:
        visibility = syncer._project_visibility(project_id, gitlab.project(project_id))
    except sync.SyncError as exc:
        raise PublishError(str(exc)) from None
    if visibility != payload.get("visibility"):
        raise PublishError(f"GitLab project {project_id} visibility changed before summary publication")


def execute(
    *, manifest_path: Path, summary: str, facts: str,
    env: dict[str, str] | None = None, adapter_factory: Any = None,
) -> dict[str, str]:
    runtime_env = dict(os.environ if env is None else env)
    prose = _validated_summary(summary)
    if not isinstance(facts, str) or not sync.HEX64_RE.fullmatch(facts):
        raise PublishError("facts receipt must be a 64-hex digest echoed from the runner result")
    try:
        manifest = desk_runner.load_manifest(Path(manifest_path))
        entries = desk_runner.inventory(manifest)
    except (desk_runner.RunnerError, sync.SyncError) as exc:
        raise PublishError(str(exc)) from None

    handles = _open_inventory_locks(entries)
    try:
        # The runner durably claims exactly the facts shown to the model.  The
        # publisher may only consume that claim; prose can never choose a target.
        scopes = [(entry["config"], Path(entry["step"]["state_dir"])) for entry in entries]
        try:
            selected = sync.select_summary_request(scopes, claim=False)
        except sync.SyncError as exc:
            raise PublishError(str(exc)) from None
        if selected is None:
            return {"status": "idle"}
        config, state_dir, selected_item = selected["config"], selected["state_dir"], selected["item"]
        if selected_item.get("status") not in {"SUMMARIZING", "PUBLISHING"}:
            raise PublishError("oldest summary request was not claimed by the fixed runner")
        request_id = selected_item["change_id"]
        syncer = sync.Syncer(config, None, None, state_dir=state_dir)
        item, _ = _find_pending(syncer, request_id)
        item, newly_prepared, content = _prepare(syncer, request_id, prose, facts)
        try:
            gitlab, buzz = (adapter_factory or sync.default_adapters)(config, runtime_env, False)
        except sync.SyncError as exc:
            raise PublishError(str(exc)) from None
        _check_delivery_boundary(syncer, gitlab, buzz, item["payload"])

        if not newly_prepared:
            matches = _matches(syncer, buzz, item)
            if len(matches) > 1:
                raise PublishError("summary publication is ambiguous; refusing to resend")
            if len(matches) == 1:
                syncer._ack_delivery(request_id, recovered=True, result=matches[0]["id"])
                return {"status": "duplicate"}
            if item["publication"]["phase"] == "unproven":
                # The send outcome is unknown: only proof by readback may ACK, never a resend.
                raise PublishError("summary send outcome is unknown and no event was proven; refusing to resend")
            # The bound send definitively never happened (local rejection or a
            # crash before send); resending the durable content is idempotent-safe.

        try:
            event_id = buzz.send(content, reply_to=None, mentions=())
        except sync.BuzzSendRejected as exc:
            raise PublishError(str(exc)) from None
        except sync.SyncError as exc:
            _mark_unproven(syncer, request_id)
            raise PublishError(str(exc)) from None
        try:
            _verify_sent(syncer, buzz, event_id, content)
        except PublishError:
            _mark_unproven(syncer, request_id)
            raise
        syncer._ack_delivery(request_id, result=event_id)
        return {"status": "sent"}
    finally:
        for handle in reversed(handles):
            handle.close()


def main(
    argv: list[str] | None = None, *, env: dict[str, str] | None = None,
    adapter_factory: Any = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, action="append", help="one bounded summary line")
    parser.add_argument(
        "--facts", required=True, action="append",
        help="facts receipt (64-hex) echoed verbatim from the runner result",
    )
    args = parser.parse_args(argv)
    if len(args.summary) != 1:
        parser.error("--summary must be supplied exactly once")
    if len(args.facts) != 1:
        parser.error("--facts must be supplied exactly once")
    runtime_env = dict(os.environ if env is None else env)
    manifest_path = runtime_env.get(desk_runner.MANIFEST_ENV)
    if not manifest_path:
        result: dict[str, str] = {"status": "error", "error": f"{desk_runner.MANIFEST_ENV} is required"}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2
    try:
        result = execute(
            manifest_path=Path(manifest_path), summary=args.summary[0], facts=args.facts[0],
            env=runtime_env, adapter_factory=adapter_factory,
        )
    except PublishError as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") in {"sent", "duplicate", "idle"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
