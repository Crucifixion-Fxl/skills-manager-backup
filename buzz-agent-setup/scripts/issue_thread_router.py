#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Poll GitLab from Buzz-side and route each Issue into one durable Buzz thread.

Run one Desk-owned process instance per business Channel.  The instance owns
exactly one GitLab project, one Buzz Channel, and one route configuration.  It
uses that Channel's Desk identity; the polling adapter is a runtime component,
not a separate Agent.  Secrets come from environment variables; the JSON
configuration contains only environment-variable names and public keys.
The first deployment is explicit: `--initialize` emits an immutable baseline
that must be pinned into the version-controlled JSON config. Each meaningful
processed snapshot also gets a Desk-authored GitLab checkpoint containing the
minimal routing policy and its digest. If local state is later lost, that Git
fact plus GitLab/Buzz markers drives an all-or-nothing recovery without bulk
re-waking bound Issues; the process never infers a replacement baseline from
the current Issue universe. GitLab Notes touch Issue.updated_at, so an
observation-only timestamp change is absorbed locally and never emits another
checkpoint/action.

The canonical source is an outbound GitLab Issue snapshot poll.  GitLab 18.0
does not support keyset pagination for project Issues, so each poll lists the
stable `created_at ASC` Issue universe, applies a fixed scan boundary locally,
and advances a durable `(updated_at, iid)` waterline only after all writes are
read back.  There is no inbound webhook listener.  A future webhook source may
feed the same deterministic processing contract, but it is not the current
implementation.

The component deliberately does not make semantic decisions.  It creates or
recovers the Issue -> Thread binding, posts lifecycle facts as Desk, and emits
structured `desk_actions` to the already-running Desk turn.  Desk then validates
the Issue content and explicitly mentions the target role Agent in that Thread;
there is no self-mention and no separate router identity.

Usage:
  issue_thread_router.py --config issue-thread-router.json --initialize
  issue_thread_router.py --config issue-thread-router.json
  issue_thread_router.py --config issue-thread-router.json --dry-run
  issue_thread_router.py --config issue-thread-router.json \
      --resolve-route feature ready opened
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


BINDING_PREFIX = "buzz-thread-binding:v1"
ACTION_NOTE_PREFIX = "buzz-desk-action:v1"
SNAPSHOT_NOTE_PREFIX = "buzz-issue-snapshot:v1"
ROOT_MARKER_PREFIX = "issue-route:v1"
EVENT_MARKER_PREFIX = "issue-route-event:v1"
DESK_ACTION_MARKER_PREFIX = "desk-action:v1"
BUZZ_CLI_RELEASE_DIR = "buzz-0.5.23"
BUZZ_SEARCH_LIMIT = 1000
ELF_MAGIC = b"\x7fELF"
BUZZ_SAFE_ENV_KEYS = (
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "BUZZ_RELAY_URL",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_AUTH_TAG",
)
# SHA-256 of the single allowed GitLab hostname. Keeping the internal hostname
# out of executable source lets the repository's script leak scanner stay strict.
GITLAB_ALLOWED_HOSTNAME_SHA256 = (
    "14185fc247b006a56d9d2625414bb5a1b4420a8fe39317389638b33b55e6b260"
)
BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256 = (
    "f0f4f32760e48aa921429fa1cd7945420231105f5310cea9c0ebb6b0beab99f8"
)
ROUTE_FACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class RouterError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RouterError(f"{path}: top-level JSON value must be an object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_buzz_cli_path(raw_path: Any, expected_sha256: Any) -> Path:
    """Return an immutable raw Buzz CLI path, never an owner-key wrapper."""

    if not isinstance(raw_path, str) or not raw_path:
        raise RouterError("config.buzz.cli_path is required")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise RouterError("config.buzz.cli_sha256 must be a lowercase SHA-256 digest")
    path = Path(raw_path)
    if not path.is_absolute():
        raise RouterError("config.buzz.cli_path must be absolute")
    if BUZZ_CLI_RELEASE_DIR not in path.parts:
        raise RouterError(
            f"config.buzz.cli_path must be pinned under a {BUZZ_CLI_RELEASE_DIR} directory"
        )
    if path.name != "buzz":
        raise RouterError("config.buzz.cli_path must name the raw buzz binary")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RouterError("config.buzz.cli_path is not a readable regular executable") from exc
    if path.is_symlink() or resolved != path:
        raise RouterError("config.buzz.cli_path must not be a symlink or wrapper")
    try:
        metadata = path.stat()
        mode = metadata.st_mode
        with path.open("rb") as handle:
            magic = handle.read(len(ELF_MAGIC))
    except OSError as exc:
        raise RouterError("config.buzz.cli_path is not a readable regular executable") from exc
    if not stat.S_ISREG(mode) or not os.access(path, os.X_OK):
        raise RouterError("config.buzz.cli_path must be a regular executable")
    if magic != ELF_MAGIC:
        raise RouterError("config.buzz.cli_path must be an ELF binary, not a shell wrapper")
    if metadata.st_uid not in {0, os.geteuid()} or mode & 0o022:
        raise RouterError("config.buzz.cli_path must be owner-controlled and not group/world-writable")
    if sha256_file(path) != expected_sha256:
        raise RouterError("config.buzz.cli_sha256 does not match the pinned Buzz binary")
    return path


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so GitLab PRIVATE-TOKEN never crosses an origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def validate_gitlab_urls(gitlab: dict[str, Any]) -> None:
    def split_url(field: str) -> urllib.parse.SplitResult:
        value = gitlab.get(field)
        if not isinstance(value, str) or not value:
            raise RouterError(f"config.gitlab.{field} is required")
        try:
            parsed = urllib.parse.urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise RouterError(f"config.gitlab.{field} is invalid") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or hashlib.sha256(parsed.hostname.encode("utf-8")).hexdigest()
            != GITLAB_ALLOWED_HOSTNAME_SHA256
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
            or parsed.netloc != parsed.hostname
        ):
            raise RouterError(f"config.gitlab.{field} must use the pinned exact HTTPS origin")
        return parsed

    base = split_url("base_url")
    if base.path not in {"", "/"}:
        raise RouterError("config.gitlab.base_url must not contain a path")
    project = split_url("project_web_url")
    if not project.path.startswith("/") or project.path in {"", "/"}:
        raise RouterError("config.gitlab.project_web_url must contain a project path")


def validate_buzz_relay_url(raw_url: Any) -> str:
    if not isinstance(raw_url, str) or not raw_url:
        raise RouterError("BUZZ_RELAY_URL is required")
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise RouterError("BUZZ_RELAY_URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or hashlib.sha256(parsed.hostname.encode("utf-8")).hexdigest()
        != BUZZ_ALLOWED_RELAY_HOSTNAME_SHA256
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.netloc != parsed.hostname
    ):
        raise RouterError("BUZZ_RELAY_URL must use the pinned exact HTTPS relay origin")
    return raw_url.rstrip("/")


def validate_config(config: dict[str, Any]) -> None:
    required = {
        "business",
        "gitlab",
        "buzz",
        "agents",
        "routes",
        "status_order",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise RouterError(f"config missing keys: {', '.join(missing)}")
    if not isinstance(config["business"], str) or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", config["business"]
    ):
        raise RouterError("config.business must be a lowercase filesystem-safe slug")

    gitlab = config["gitlab"]
    buzz = config["buzz"]
    for key in (
        "base_url",
        "project_id",
        "project_web_url",
        "token_env",
        "bot_author_id",
        "bot_username",
        "required_project_visibility",
        "deployment_baseline",
    ):
        if key not in gitlab:
            raise RouterError(f"config.gitlab.{key} is required")
    validate_gitlab_urls(gitlab)
    if not isinstance(gitlab["project_id"], int) or gitlab["project_id"] <= 0:
        raise RouterError("config.gitlab.project_id must be a positive integer")
    if not isinstance(gitlab["token_env"], str) or not re.fullmatch(
        r"[A-Z_][A-Z0-9_]*", gitlab["token_env"]
    ):
        raise RouterError("config.gitlab.token_env must name an uppercase environment variable")
    if gitlab["token_env"] in BUZZ_SAFE_ENV_KEYS:
        raise RouterError("config.gitlab.token_env must not overlap the Buzz CLI env allowlist")
    if not isinstance(gitlab["bot_author_id"], int) or gitlab["bot_author_id"] <= 0:
        raise RouterError("config.gitlab.bot_author_id must be a positive integer")
    if not isinstance(gitlab["bot_username"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", gitlab["bot_username"]
    ):
        raise RouterError("config.gitlab.bot_username is invalid")
    if gitlab["required_project_visibility"] != "public":
        raise RouterError(
            "config.gitlab.required_project_visibility must be public until private audience verification exists"
        )
    if gitlab["deployment_baseline"] is not None:
        normalize_deployment_baseline(gitlab["deployment_baseline"])

    for key in ("channel_id", "desk_agent", "cli_path", "cli_sha256", "desk_pubkey"):
        if key not in buzz:
            raise RouterError(f"config.buzz.{key} is required")
    validate_buzz_cli_path(buzz["cli_path"], buzz["cli_sha256"])
    if not isinstance(buzz["channel_id"], str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
        buzz["channel_id"],
    ):
        raise RouterError("config.buzz.channel_id must be a UUID")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(buzz["desk_pubkey"])):
        raise RouterError("config.buzz.desk_pubkey must be a 64-char hex pubkey")

    agents = config["agents"]
    desk_key = buzz["desk_agent"]
    if desk_key not in agents:
        raise RouterError(f"desk agent {desk_key!r} is absent from config.agents")
    allowed_kinds = {"desk", "role", "investigator", "executor"}
    names: set[str] = set()
    pubkeys: set[str] = set()
    for key, agent in agents.items():
        if not agent.get("name") or not re.fullmatch(r"[0-9a-fA-F]{64}", agent.get("pubkey", "")):
            raise RouterError(f"agent {key!r} needs name and 64-char hex pubkey")
        if agent.get("kind") not in allowed_kinds:
            raise RouterError(
                f"agent {key!r} needs kind in {sorted(allowed_kinds)}"
            )
        name_key = str(agent["name"]).casefold()
        pubkey_key = str(agent["pubkey"]).lower()
        if name_key in names:
            raise RouterError(f"duplicate agent name: {agent['name']!r}")
        if pubkey_key in pubkeys:
            raise RouterError(f"duplicate agent pubkey: {agent['pubkey']!r}")
        names.add(name_key)
        pubkeys.add(pubkey_key)
    if agents[desk_key]["kind"] != "desk":
        raise RouterError(f"desk agent {desk_key!r} must declare kind=desk")
    if str(agents[desk_key]["pubkey"]).lower() != str(buzz["desk_pubkey"]).lower():
        raise RouterError("config.buzz.desk_pubkey must equal the declared Desk Agent pubkey")

    status_order = config["status_order"]
    if (
        not isinstance(status_order, list)
        or not status_order
        or len(status_order) != len(set(status_order))
        or not all(isinstance(value, str) and value for value in status_order)
    ):
        raise RouterError("config.status_order must be a non-empty unique string list")
    if any(not ROUTE_FACT_RE.fullmatch(value) for value in status_order):
        raise RouterError("config.status_order values must match the safe route label grammar")

    route_pairs: set[tuple[str, str]] = set()
    for index, rule in enumerate(config["routes"]):
        if not isinstance(rule, dict):
            raise RouterError(f"routes[{index}] must be an object")
        if rule.get("target") not in agents:
            raise RouterError(f"routes[{index}].target is not present in config.agents")
        target_key = rule["target"]
        target = agents[target_key]
        if (
            (target["kind"] == "desk" and target_key != desk_key)
            or target["kind"] not in {"desk", "role"}
            or target["name"].endswith("-executor")
        ):
            raise RouterError(
                f"routes[{index}] target {target['name']!r} must be kind=desk/role "
                "(only the primary Desk); never executor/another Desk"
            )
        types = rule.get("types")
        statuses = rule.get("statuses")
        if (
            not isinstance(types, list)
            or not types
            or any(not isinstance(value, str) or not value for value in types)
            or not isinstance(statuses, list)
            or not statuses
            or any(not isinstance(value, str) or not value for value in statuses)
        ):
            raise RouterError(
                f"routes[{index}] types/statuses must be non-empty string arrays"
            )
        if any(not ROUTE_FACT_RE.fullmatch(value) for value in [*types, *statuses]):
            raise RouterError(
                f"routes[{index}] types/statuses must match the safe route label grammar"
            )
        unknown_statuses = sorted(set(statuses) - set(status_order))
        if unknown_statuses:
            raise RouterError(
                f"routes[{index}] has statuses absent from status_order: {unknown_statuses}"
            )
        for issue_type in types:
            for status_name in statuses:
                pair = (str(issue_type), str(status_name))
                if pair in route_pairs:
                    raise RouterError(
                        f"overlapping route for type={pair[0]!r}, status={pair[1]!r}"
                    )
                route_pairs.add(pair)


def one_label(labels: list[str], prefix: str) -> tuple[str | None, bool]:
    values = [label[len(prefix):] for label in labels if label.startswith(prefix)]
    if len(values) != 1 or not ROUTE_FACT_RE.fullmatch(values[0]):
        return None, False
    return values[0], True


def strict_issue_labels(issue: dict[str, Any]) -> list[str]:
    """Return labels only when GitLab supplied the expected string array."""

    value = issue.get("labels")
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(label, str) for label in value):
        raise RouterError("GitLab Issue labels must be a string array")
    return list(value)


def require_non_confidential_issue(issue: dict[str, Any]) -> None:
    """Accept only an explicit GitLab boolean false audience decision.

    Missing, null, string, numeric, or true values are not proof that the Issue
    may leave GitLab.  Treat schema drift and partial API responses exactly like
    confidential content and fail before any Buzz or action-Note write.
    """

    if issue.get("confidential") is not False:
        raise RouterError(
            "GitLab Issue confidential must be explicit boolean false before "
            "routing to the default open Buzz Channel"
        )


def require_valid_issue_state(issue: dict[str, Any]) -> str:
    """Return the only GitLab Issue states this adapter understands."""

    state = issue.get("state")
    if state not in {"opened", "closed"}:
        raise RouterError(
            "GitLab Issue state must be exactly 'opened' or 'closed' before routing"
        )
    return str(state)


def require_positive_issue_iid(issue: dict[str, Any], purpose: str = "snapshot") -> int:
    """Return a strict positive GitLab IID without coercing malformed values."""

    iid = issue.get("iid")
    if not isinstance(iid, int) or isinstance(iid, bool) or iid <= 0:
        raise RouterError(f"GitLab Issue {purpose} has no positive integer iid")
    return iid


def issue_snapshot(
    issue: dict[str, Any], config: dict[str, Any] | None = None
) -> dict[str, Any]:
    state = require_valid_issue_state(issue)
    labels = strict_issue_labels(issue)
    issue_type, type_valid = one_label(labels, "type::")
    status, status_valid = one_label(labels, "status::")
    if config is not None:
        allowed_types = {
            value for rule in config["routes"] for value in rule["types"]
        }
        allowed_statuses = set(config["status_order"])
        if issue_type not in allowed_types:
            issue_type, type_valid = None, False
        if status not in allowed_statuses:
            status, status_valid = None, False
    return {
        "state": state,
        "type": issue_type,
        "status": status,
        "labels_valid": type_valid and status_valid,
        "updated_at": issue.get("updated_at"),
        "content_digest": hashlib.sha256(
            (str(issue.get("title") or "") + "\0" + str(issue.get("description") or "")).encode(
                "utf-8"
            )
        ).hexdigest(),
    }


def physical_first_line(content: str) -> str:
    """Return bytes-before-first-LF semantics without trimming or Unicode folding."""

    return content.partition("\n")[0]


def route_fact_for_message(
    config: dict[str, Any], snapshot: dict[str, Any], field: str
) -> str:
    """Render only allowlisted routing enums into a Buzz protocol message."""

    value = snapshot.get(field)
    if value is None:
        return "(missing)"
    if field == "status":
        allowed = set(config["status_order"])
    elif field == "type":
        allowed = {
            issue_type
            for rule in config["routes"]
            for issue_type in rule["types"]
        }
    else:
        raise RouterError(f"unsupported route message field: {field}")
    return str(value) if value in allowed else "(invalid)"


def action_source_is_stale(
    action: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Return whether a pending action predates the current business snapshot."""

    source = action.get("source_snapshot")
    if not isinstance(source, dict):
        raise RouterError("Desk action source_snapshot must be an object")
    return any(
        source.get(field) != current.get(field)
        for field in ("state", "type", "status", "labels_valid", "content_digest")
    )


def issue_change_id(
    issue: dict[str, Any], config: dict[str, Any] | None = None
) -> str:
    """Return a scan-observation key, including GitLab's updated_at."""

    snapshot = issue_snapshot(issue, config)
    value = {
        "iid": require_positive_issue_iid(issue),
        "updated_at": snapshot.get("updated_at"),
        "state": snapshot.get("state"),
        "type": snapshot.get("type"),
        "status": snapshot.get("status"),
        "content_digest": snapshot.get("content_digest"),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def normalize_cursor(value: Any) -> dict[str, Any]:
    """Normalize the durable `(updated_at, iid)` high-water mark."""

    if isinstance(value, str):
        value = {"updated_at": value, "iid": 0}
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("updated_at"), str)
        or not isinstance(value.get("iid"), int)
        or value["iid"] < 0
    ):
        raise RouterError("state.cursor must be {updated_at, iid}")
    parse_time(value["updated_at"])
    return {"updated_at": value["updated_at"], "iid": value["iid"]}


def normalize_deployment_baseline(value: Any) -> dict[str, Any]:
    """Validate the immutable first-scan boundary used to classify new Issues."""

    if (
        not isinstance(value, dict)
        or not isinstance(value.get("established_at"), str)
        or not isinstance(value.get("max_iid"), int)
        or isinstance(value.get("max_iid"), bool)
        or value["max_iid"] < 0
    ):
        raise RouterError(
            "state.deployment_baseline must be {established_at, max_iid}; "
            "explicit state migration or re-baselining is required"
        )
    parse_time(value["established_at"])
    return {
        "established_at": value["established_at"],
        "max_iid": value["max_iid"],
    }


def annotate_transition(
    config: dict[str, Any],
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> None:
    """Annotate whether status follows the configured single-step state machine.

    Once an illegal jump is observed, unrelated updates at the same illegal
    status remain invalid.  The Issue must return to the last valid status or
    advance exactly one step from it before normal routing resumes.
    """

    order = config["status_order"]
    status = current.get("status")
    previous_valid_status = None
    if previous is not None:
        previous_valid_status = previous.get("last_valid_status")
        if previous_valid_status is None and previous.get("transition_valid", True):
            previous_valid_status = previous.get("status")

    if not current.get("labels_valid") or status not in order:
        valid = False
    elif previous is None or previous_valid_status is None:
        valid = True
    elif previous.get("transition_valid") is False and status == previous.get("status"):
        valid = False
    else:
        previous_index = order.index(previous_valid_status)
        valid = status == previous_valid_status or (
            previous_index + 1 < len(order) and status == order[previous_index + 1]
        )

    current["transition_valid"] = valid
    current["last_valid_status"] = status if valid else previous_valid_status


def resolve_target(config: dict[str, Any], snapshot: dict[str, Any]) -> str:
    desk = config["buzz"]["desk_agent"]
    if (
        snapshot["state"] == "closed"
        or not snapshot["labels_valid"]
        or snapshot.get("transition_valid") is False
    ):
        return desk
    issue_type = snapshot["type"]
    status = snapshot["status"]
    for rule in config["routes"]:
        if issue_type in rule["types"] and status in rule["statuses"]:
            return rule["target"]
    return desk


class GitLab:
    def __init__(self, config: dict[str, Any]):
        gitlab = config["gitlab"]
        token_env = gitlab["token_env"]
        token = os.environ.get(token_env)
        if not token:
            raise RouterError(f"missing GitLab token environment variable {token_env}")
        self.api = gitlab["base_url"].rstrip("/") + "/api/v4"
        self.project_id = str(gitlab["project_id"])
        self.token = token
        self.bot_author_id = gitlab["bot_author_id"]
        self.bot_username = gitlab["bot_username"]
        self.project_web_url = str(gitlab["project_web_url"]).rstrip("/")
        self.required_project_visibility = str(gitlab["required_project_visibility"])
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def verify_identity(self) -> None:
        value, _ = self.request("GET", "user")
        if (
            not isinstance(value, dict)
            or value.get("id") != self.bot_author_id
            or value.get("username") != self.bot_username
        ):
            raise RouterError(
                "GitLab token identity does not match the configured Desk service account"
            )

    def server_time(self) -> str:
        """Return an authenticated, fully elapsed GitLab server boundary.

        HTTP Date has one-second precision.  Lag one full second so an Issue
        created after this response can never share a truncated timestamp with
        the committed upper boundary.
        """

        value, headers = self.request("GET", "user")
        if (
            not isinstance(value, dict)
            or value.get("id") != self.bot_author_id
            or value.get("username") != self.bot_username
        ):
            raise RouterError(
                "GitLab token identity does not match the configured Desk service account"
            )
        raw_date = headers.get("date")
        if not isinstance(raw_date, str) or not raw_date:
            raise RouterError("GitLab response has no Date header for the scan boundary")
        try:
            value_date = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError) as exc:
            raise RouterError("GitLab response Date header is invalid") from exc
        if value_date.tzinfo is None:
            raise RouterError("GitLab response Date header has no timezone")
        safe_boundary = value_date.astimezone(dt.timezone.utc) - dt.timedelta(seconds=1)
        return safe_boundary.isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

    def verify_project_visibility(self) -> None:
        """Refuse audiences wider than the source project's proven visibility."""

        value, _ = self.request("GET", f"projects/{self.project_id}")
        if not isinstance(value, dict) or str(value.get("id")) != self.project_id:
            raise RouterError("GitLab project identity readback mismatch")
        if str(value.get("web_url", "")).rstrip("/") != self.project_web_url:
            raise RouterError("GitLab project web_url readback mismatch")
        visibility = value.get("visibility")
        if visibility != self.required_project_visibility:
            raise RouterError(
                "GitLab project is not public; private/internal Issue routing is disabled "
                "until Buzz Channel visibility and audience equivalence can be verified"
            )

    def note_has_expected_author(self, note: dict[str, Any]) -> bool:
        author = note.get("author")
        return (
            isinstance(author, dict)
            and author.get("id") == self.bot_author_id
            and author.get("username") == self.bot_username
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        payload = urllib.parse.urlencode(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            method=method,
            data=payload,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.opener.open(req, timeout=30) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read()
                return (json.loads(raw) if raw else None, headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RouterError(f"GitLab {method} {path} failed: HTTP {exc.code}: {detail}") from exc

    def paged(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        page = 1
        visited: set[int] = set()
        result: list[dict[str, Any]] = []
        while True:
            if page in visited or page > 1000:
                raise RouterError(f"GitLab {path} pagination repeated or exceeded 1000 pages")
            visited.add(page)
            page_params = {**params, "page": str(page), "per_page": "100"}
            values, headers = self.request("GET", path, params=page_params)
            if not isinstance(values, list):
                raise RouterError(f"GitLab {path} did not return a list")
            result.extend(values)
            next_page = headers.get("x-next-page", "")
            if not next_page:
                return result
            try:
                next_value = int(next_page)
            except ValueError as exc:
                raise RouterError(f"GitLab {path} returned an invalid next page") from exc
            if next_value <= page:
                raise RouterError(f"GitLab {path} pagination did not advance")
            page = next_value

    def updated_issues(
        self,
        cursor: dict[str, Any],
        scan_before: str,
    ) -> list[dict[str, Any]]:
        """Return snapshots in one closed, locally filtered scan window.

        The deployed GitLab 18.0 project-Issues endpoint only has offset
        pagination.  Filtering and ordering that mutable collection by
        `updated_at` can move rows between pages during a scan.  Listing the
        full `state=all` universe by immutable `created_at ASC` keeps ordinary
        Issue updates from changing page membership; a microsecond scan upper
        bound then makes updates that happen during the scan belong to the next
        poll.  The one-second lower overlap is deduplicated by snapshot digest.
        """

        after = parse_time(cursor["updated_at"]) - dt.timedelta(seconds=1)
        before = parse_time(scan_before)
        issues = self.stable_issue_universe(scan_before)
        filtered: list[dict[str, Any]] = []
        for issue in issues:
            iid = issue.get("iid")
            updated_at = issue.get("updated_at")
            created_at = issue.get("created_at")
            if (
                not isinstance(iid, int)
                or iid <= 0
                or not isinstance(updated_at, str)
                or not isinstance(created_at, str)
            ):
                raise RouterError("GitLab Issue universe contains an invalid snapshot")
            updated = parse_time(updated_at)
            if after <= updated <= before:
                filtered.append(issue)
        return sorted(
            filtered,
            key=lambda issue: (str(issue.get("updated_at", "")), int(issue.get("iid", 0))),
        )

    def issue(self, iid: int | str) -> dict[str, Any]:
        value, _ = self.request("GET", f"projects/{self.project_id}/issues/{iid}")
        if not isinstance(value, dict):
            raise RouterError(f"GitLab issue {iid} did not return an object")
        return value

    def all_issues(self) -> list[dict[str, Any]]:
        return self.paged(
            f"projects/{self.project_id}/issues",
            {"state": "all", "order_by": "created_at", "sort": "asc"},
        )

    def stable_issue_universe(self, scan_before: str) -> list[dict[str, Any]]:
        """Require two consecutive equal pre-boundary membership reads.

        `created_at ASC` prevents ordinary updates from reordering offset pages.
        This second check also catches a concurrent create/delete changing page
        membership; instability fails closed instead of advancing the waterline.
        """

        boundary = parse_time(scan_before)
        previous_signature: list[tuple[int, str]] | None = None
        for _attempt in range(3):
            universe: list[dict[str, Any]] = []
            signature: list[tuple[int, str]] = []
            seen_iids: set[int] = set()
            for issue in self.all_issues():
                iid = issue.get("iid")
                created_at = issue.get("created_at")
                if not isinstance(iid, int) or iid <= 0 or not isinstance(created_at, str):
                    raise RouterError("GitLab Issue universe contains an invalid snapshot")
                if iid in seen_iids:
                    raise RouterError("GitLab Issue universe contains a duplicate iid")
                seen_iids.add(iid)
                if parse_time(created_at) <= boundary:
                    universe.append(issue)
                    signature.append((iid, created_at))
            if previous_signature == signature:
                return universe
            previous_signature = signature
        raise RouterError(
            "GitLab Issue universe changed during pagination; refusing to advance waterline"
        )

    def all_notes(self, iid: int | str) -> list[dict[str, Any]]:
        # Ascending creation order keeps newly appended Notes at the tail,
        # instead of shifting every offset page as `sort=desc` would.
        return self.paged(
            f"projects/{self.project_id}/issues/{iid}/notes",
            {"sort": "asc"},
        )

    def notes(self, iid: int | str) -> list[dict[str, Any]]:
        """Return one stable, complete Note universe or fail closed."""

        previous_signature: list[tuple[int, str]] | None = None
        for _attempt in range(3):
            notes = self.all_notes(iid)
            signature: list[tuple[int, str]] = []
            seen_ids: set[int] = set()
            for note in notes:
                note_id = note.get("id")
                if (
                    not isinstance(note_id, int)
                    or isinstance(note_id, bool)
                    or note_id <= 0
                ):
                    raise RouterError("GitLab Note universe contains an invalid note id")
                if note_id in seen_ids:
                    raise RouterError("GitLab Note universe contains a duplicate note id")
                seen_ids.add(note_id)
                encoded = json.dumps(
                    note,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                signature.append((note_id, hashlib.sha256(encoded).hexdigest()))
            if previous_signature == signature:
                return notes
            previous_signature = signature
        raise RouterError(
            f"GitLab Issue #{iid} Note universe changed during pagination; "
            "refusing an incomplete binding/action/checkpoint read"
        )

    def add_machine_note(self, iid: int | str, body: str, prefix: str) -> int:
        if prefix not in body:
            raise RouterError("GitLab machine note is missing its required marker")
        value, _ = self.request(
            "POST", f"projects/{self.project_id}/issues/{iid}/notes", body={"body": body}
        )
        if not isinstance(value, dict) or not isinstance(value.get("id"), int):
            raise RouterError("GitLab binding note POST returned no numeric note id")
        note_id = value["id"]
        readback, _ = self.request(
            "GET", f"projects/{self.project_id}/issues/{iid}/notes/{note_id}"
        )
        if (
            not isinstance(readback, dict)
            or readback.get("id") != note_id
            or readback.get("body") != body
            or prefix not in str(readback.get("body", ""))
            or not self.note_has_expected_author(readback)
        ):
            raise RouterError("GitLab machine note readback did not match author/marker/body")
        return note_id

    def add_note(self, iid: int | str, body: str) -> int:
        return self.add_machine_note(iid, body, BINDING_PREFIX)

    def add_action_note(self, iid: int | str, body: str) -> int:
        return self.add_machine_note(iid, body, ACTION_NOTE_PREFIX)

    def add_snapshot_note(self, iid: int | str, body: str) -> int:
        return self.add_machine_note(iid, body, SNAPSHOT_NOTE_PREFIX)


class Buzz:
    def __init__(
        self,
        config: dict[str, Any],
        dry_run: bool,
        *,
        runner: Any = subprocess.run,
        sleeper: Any = time.sleep,
    ):
        self.channel = config["buzz"]["channel_id"]
        validate_buzz_relay_url(os.environ.get("BUZZ_RELAY_URL"))
        self.cli_path = str(
            validate_buzz_cli_path(
                config["buzz"]["cli_path"], config["buzz"]["cli_sha256"]
            )
        )
        self.desk_pubkey = str(config["buzz"]["desk_pubkey"]).lower()
        self.dry_run = dry_run
        self.runner = runner
        self.sleeper = sleeper

    def command(self, args: list[str], content: str | None = None) -> Any:
        if self.dry_run:
            print("DRY buzz", " ".join(args), (content or "").replace("\n", " | ")[:240])
            dry_event = json.dumps(
                {"args": args, "content": content or ""},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            return {"accepted": True, "event_id": hashlib.sha256(dry_event).hexdigest()}
        result = self.runner(
            [self.cli_path, *args],
            input=content,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            env={key: os.environ[key] for key in BUZZ_SAFE_ENV_KEYS if key in os.environ},
        )
        if result.returncode != 0:
            raise RouterError(f"Buzz CLI {' '.join(args[:2])} failed ({result.returncode})")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RouterError("Buzz CLI returned non-JSON output") from exc
        return value

    def search_desk_events(self, marker: str, purpose: str) -> list[dict[str, Any]]:
        """Search a complete, bounded set of Desk-authored marker candidates."""

        value = self.command(
            [
                "messages",
                "search",
                "--query",
                marker,
                "--author",
                self.desk_pubkey,
                "--limit",
                str(BUZZ_SEARCH_LIMIT),
            ]
        )
        if not isinstance(value, list):
            raise RouterError(f"Buzz {purpose} search did not return a list")
        if len(value) >= BUZZ_SEARCH_LIMIT:
            raise RouterError(
                f"Buzz {purpose} search reached its {BUZZ_SEARCH_LIMIT}-event cap; "
                "refusing an ambiguous result"
            )
        events: list[dict[str, Any]] = []
        for event in value:
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("id"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", event["id"])
                or not isinstance(event.get("pubkey"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", event["pubkey"].lower())
                or not isinstance(event.get("content"), str)
                or not isinstance(event.get("tags"), list)
            ):
                raise RouterError(f"Buzz {purpose} search returned an invalid event schema")
            if event["pubkey"].lower() != self.desk_pubkey:
                raise RouterError(
                    f"Buzz {purpose} search returned an event outside the Desk author filter"
                )
            events.append(event)
        return events

    @staticmethod
    def _find_event(value: Any, event_id: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if str(value.get("id", "")) == event_id:
                return value
            for child in value.values():
                found = Buzz._find_event(child, event_id)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = Buzz._find_event(child, event_id)
                if found is not None:
                    return found
        return None

    def destination_mentions(
        self,
        event: dict[str, Any],
        *,
        root_event_id: str | None,
    ) -> list[str]:
        """Validate the exact Channel/reply destination and return p tags.

        Readback is an authorization boundary: the presence of one expected tag
        is not enough if an event also targets another Channel, root, or person.
        Non-destination metadata tags remain allowed.
        """

        tags = event.get("tags") or []
        if not isinstance(tags, list):
            raise RouterError("Buzz message readback has invalid tags")
        h_tags = [
            tag
            for tag in tags
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "h"
        ]
        if len(h_tags) != 1 or h_tags[0][1] != self.channel:
            raise RouterError(
                "Buzz message readback channel mismatch: must target exactly one expected channel"
            )
        e_tags = [
            tag
            for tag in tags
            if isinstance(tag, list) and tag and tag[0] == "e"
        ]
        if root_event_id is None:
            if e_tags:
                raise RouterError("Buzz root message readback unexpectedly contains a reply tag")
        elif (
            len(e_tags) != 1
            or len(e_tags[0]) < 4
            or e_tags[0][1] != root_event_id
            or e_tags[0][3] != "reply"
        ):
            raise RouterError(
                "Buzz reply readback must target exactly one expected NIP-10 root Thread"
            )
        mentions = [
            str(tag[1]).lower()
            for tag in tags
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "p"
        ]
        if len(mentions) != len(set(mentions)):
            raise RouterError("Buzz message readback contains duplicate mention targets")
        return mentions

    def readback_send(
        self,
        event_id: str,
        content: str,
        *,
        root_event_id: str | None,
        mention_pubkey: str | None,
    ) -> None:
        last_error: Exception | None = None
        event: dict[str, Any] | None = None
        for attempt in range(5):
            try:
                value = self.command(
                    [
                        "messages",
                        "thread",
                        "--channel",
                        self.channel,
                        "--event",
                        event_id,
                        "--limit",
                        "200",
                    ]
                )
                event = self._find_event(value, event_id)
                if event is not None:
                    break
            except RouterError as exc:
                last_error = exc
            if attempt < 4:
                self.sleeper(0.5)
        if event is None:
            raise RouterError("Buzz message write could not be read back") from last_error

        if event.get("content") != content:
            raise RouterError("Buzz message readback content mismatch")
        if str(event.get("pubkey", "")).lower() != self.desk_pubkey:
            raise RouterError("Buzz message readback author mismatch")
        mentions = self.destination_mentions(event, root_event_id=root_event_id)
        expected_mentions = [] if mention_pubkey is None else [mention_pubkey.lower()]
        if mentions != expected_mentions:
            raise RouterError(
                "Buzz message readback mention p tags do not exactly match the send"
            )

    def validate_root(self, root_event_id: str, marker: str) -> None:
        value = self.command(
            [
                "messages",
                "thread",
                "--channel",
                self.channel,
                "--event",
                root_event_id,
                "--limit",
                "200",
            ]
        )
        event = self._find_event(value, root_event_id)
        if event is None:
            raise RouterError("Buzz root readback did not contain the bound event id")
        if str(event.get("pubkey", "")).lower() != self.desk_pubkey:
            raise RouterError("Buzz root readback author mismatch")
        if physical_first_line(str(event.get("content", ""))) != marker:
            raise RouterError("Buzz root readback marker mismatch")
        mentions = self.destination_mentions(event, root_event_id=None)
        if mentions:
            raise RouterError("Buzz root readback unexpectedly mentions another identity")

    def send(
        self,
        content: str,
        *,
        root_event_id: str | None = None,
        mention_pubkey: str | None = None,
    ) -> str:
        args = ["messages", "send", "--channel", self.channel, "--content", "-"]
        if root_event_id:
            args += ["--reply-to", root_event_id]
        if mention_pubkey:
            args += ["--mention", mention_pubkey]
        value = self.command(args, content)
        if (
            not isinstance(value, dict)
            or not value.get("accepted")
            or not value.get("event_id")
        ):
            raise RouterError("Buzz rejected message or returned no event id")
        event_id = str(value["event_id"])
        if not re.fullmatch(r"[0-9a-f]{64}", event_id):
            raise RouterError("Buzz returned an invalid event id")
        if not self.dry_run:
            self.readback_send(
                event_id,
                content,
                root_event_id=root_event_id,
                mention_pubkey=mention_pubkey,
            )
        return event_id

    def recover_root(self, marker: str) -> str | None:
        if self.dry_run:
            return None
        value = self.search_desk_events(marker, "root recovery")
        roots: list[str] = []
        for event in value:
            if physical_first_line(event["content"]) == marker:
                mentions = self.destination_mentions(event, root_event_id=None)
                if mentions:
                    raise RouterError("Buzz root recovery found an unexpected mention target")
                roots.append(str(event["id"]))
        if len(roots) > 1:
            raise RouterError("Buzz contains multiple Desk-authored roots for one Issue")
        return roots[0] if roots else None

    def contains_message(self, marker: str, root_event_id: str) -> bool:
        if self.dry_run:
            return False
        value = self.search_desk_events(marker, "lifecycle recovery")
        for event in value:
            if physical_first_line(event["content"]) == marker:
                mentions = self.destination_mentions(event, root_event_id=root_event_id)
                if mentions:
                    raise RouterError("Buzz lifecycle readback unexpectedly mentions an identity")
                return True
        return False

    def find_action_receipt(
        self,
        action: dict[str, Any],
        allowed_role_pubkeys: set[str],
    ) -> dict[str, Any] | None:
        action_id = str(action["action_id"])
        marker = f"[{DESK_ACTION_MARKER_PREFIX}:{action_id}]"
        root = str(action["root_event_id"])
        value = self.search_desk_events(marker, "Desk action receipt")
        matches: list[dict[str, Any]] = []
        for event in value:
            content = str(event.get("content", ""))
            first_line = physical_first_line(content)
            match = re.fullmatch(
                rf"{re.escape(marker)} outcome=(routed|needs-human|desk-only|final-summary)",
                first_line,
            )
            if not match:
                continue
            outcome = match.group(1)
            mentions_list = self.destination_mentions(event, root_event_id=root)
            mentions = set(mentions_list)
            if mentions - allowed_role_pubkeys:
                raise RouterError("Desk action receipt mentions a non-role or unregistered identity")
            if action.get("mode") == "final-summary":
                if outcome != "final-summary" or mentions:
                    raise RouterError(
                        "final-summary Desk action receipt needs outcome=final-summary and no mention"
                    )
            elif outcome == "routed":
                if len(mentions) != 1:
                    raise RouterError(
                        "routed Desk action receipt must mention exactly one registered role Agent"
                    )
            elif outcome in {"needs-human", "desk-only"}:
                if mentions:
                    raise RouterError(
                        "needs-human/desk-only Desk action receipt must not mention a role Agent"
                    )
            else:
                raise RouterError("route Desk action receipt has an invalid outcome")
            event_id = str(event.get("id", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", event_id):
                raise RouterError("Desk action receipt has an invalid event id")
            matches.append(
                {
                    "receipt_event_id": event_id,
                    "outcome": outcome,
                    "mention_pubkeys": sorted(mentions),
                }
            )
        if len(matches) > 1:
            raise RouterError("Desk action has multiple valid receipt messages")
        return matches[0] if matches else None


def binding_marker(
    project_id: str | int,
    issue_iid: str | int,
    channel_id: str,
    root_event_id: str,
) -> str:
    value = json.dumps(
        {
            "project_id": str(project_id),
            "issue_iid": int(issue_iid),
            "channel_id": channel_id,
            "root_event_id": root_event_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"<!-- {BINDING_PREFIX} {value} -->"


ACTION_NOTE_FIELDS = (
    "action_id",
    "change_id",
    "policy_digest",
    "project_id",
    "issue_iid",
    "channel_id",
    "root_event_id",
    "mode",
    "suggested_target",
    "suggested_target_pubkey",
    "reason",
    "content_trust",
    "source_snapshot",
    "previous_change_id",
)


def action_note_marker(action: dict[str, Any]) -> str:
    value = {field: action[field] for field in ACTION_NOTE_FIELDS}
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"<!-- {ACTION_NOTE_PREFIX} {encoded} -->"


SNAPSHOT_FIELDS = (
    "state",
    "type",
    "status",
    "labels_valid",
    "updated_at",
    "content_digest",
    "transition_valid",
    "last_valid_status",
    "target",
)


def validate_processed_snapshot(value: Any, subject: str) -> dict[str, Any]:
    """Validate one fully annotated Issue snapshot carried by a durable fact."""

    if not isinstance(value, dict) or set(value) != set(SNAPSHOT_FIELDS):
        raise RouterError(f"{subject} has invalid snapshot fields")
    if value.get("state") not in {"opened", "closed"}:
        raise RouterError(f"{subject} has an invalid Issue state")
    if not isinstance(value.get("labels_valid"), bool) or not isinstance(
        value.get("transition_valid"), bool
    ):
        raise RouterError(f"{subject} has invalid validation flags")
    for field in ("type", "status", "last_valid_status"):
        if value.get(field) is not None and not isinstance(value.get(field), str):
            raise RouterError(f"{subject} has an invalid {field}")
    updated_at = value.get("updated_at")
    if not isinstance(updated_at, str):
        raise RouterError(f"{subject} has an invalid updated_at")
    parse_time(updated_at)
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("content_digest", ""))):
        raise RouterError(f"{subject} has an invalid content_digest")
    if not isinstance(value.get("target"), str) or not value["target"]:
        raise RouterError(f"{subject} has an invalid target")
    return dict(value)


def routing_policy_material(config: dict[str, Any]) -> dict[str, Any]:
    """Return the self-contained deterministic policy pinned by checkpoints."""

    referenced_agents = {str(config["buzz"]["desk_agent"])}
    referenced_agents.update(str(rule["target"]) for rule in config["routes"])
    return {
        "schema_version": 1,
        "desk_agent": str(config["buzz"]["desk_agent"]),
        "status_order": [str(value) for value in config["status_order"]],
        "routes": [
            {
                "types": [str(value) for value in rule["types"]],
                "statuses": [str(value) for value in rule["statuses"]],
                "target": str(rule["target"]),
            }
            for rule in config["routes"]
        ],
        "agents": {
            str(key): {
                "kind": str(agent["kind"]),
                "pubkey": str(agent["pubkey"]).lower(),
            }
            for key, agent in sorted(config["agents"].items())
            if str(key) in referenced_agents
        },
    }


def normalize_routing_policy_material(value: Any) -> dict[str, Any]:
    """Validate an embedded historical routing policy without current config."""

    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "desk_agent",
        "status_order",
        "routes",
        "agents",
    }:
        raise RouterError("GitLab Desk snapshot marker has an invalid policy schema")
    if value.get("schema_version") != 1:
        raise RouterError("GitLab Desk snapshot marker has an unsupported policy version")
    desk_agent = value.get("desk_agent")
    status_order = value.get("status_order")
    routes = value.get("routes")
    agents = value.get("agents")
    if not isinstance(desk_agent, str) or not desk_agent:
        raise RouterError("GitLab Desk snapshot marker has an invalid policy Desk")
    if (
        not isinstance(status_order, list)
        or not status_order
        or any(not isinstance(item, str) or not item for item in status_order)
        or len(set(status_order)) != len(status_order)
    ):
        raise RouterError("GitLab Desk snapshot marker has an invalid policy status order")
    if not isinstance(agents, dict) or desk_agent not in agents:
        raise RouterError("GitLab Desk snapshot marker has an invalid policy roster")
    normalized_agents: dict[str, dict[str, str]] = {}
    for key, agent in agents.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(agent, dict)
            or set(agent) != {"kind", "pubkey"}
            or agent.get("kind") not in {"desk", "role"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(agent.get("pubkey", "")).lower())
            or re.search(r"(?:^|[-_])executor$", key) is not None
        ):
            raise RouterError("GitLab Desk snapshot marker has an invalid policy Agent")
        normalized_agents[key] = {
            "kind": str(agent["kind"]),
            "pubkey": str(agent["pubkey"]).lower(),
        }
    if normalized_agents[desk_agent]["kind"] != "desk":
        raise RouterError("GitLab Desk snapshot marker policy Desk is not kind=desk")
    if not isinstance(routes, list):
        raise RouterError("GitLab Desk snapshot marker has invalid policy routes")
    normalized_routes: list[dict[str, Any]] = []
    for rule in routes:
        if not isinstance(rule, dict) or set(rule) != {"types", "statuses", "target"}:
            raise RouterError("GitLab Desk snapshot marker has invalid policy routes")
        types = rule.get("types")
        statuses = rule.get("statuses")
        target = rule.get("target")
        if (
            not isinstance(types, list)
            or not types
            or any(not isinstance(item, str) or not item for item in types)
            or not isinstance(statuses, list)
            or not statuses
            or any(item not in status_order for item in statuses)
            or not isinstance(target, str)
            or target not in normalized_agents
            or (
                normalized_agents[target]["kind"] == "desk"
                and target != desk_agent
            )
            or normalized_agents[target]["kind"] not in {"desk", "role"}
            or re.search(r"(?:^|[-_])executor$", target) is not None
        ):
            raise RouterError("GitLab Desk snapshot marker has invalid policy routes")
        normalized_routes.append(
            {
                "types": list(types),
                "statuses": list(statuses),
                "target": target,
            }
        )
    return {
        "schema_version": 1,
        "desk_agent": desk_agent,
        "status_order": list(status_order),
        "routes": normalized_routes,
        "agents": normalized_agents,
    }


def routing_policy_digest(policy: dict[str, Any]) -> str:
    normalized = normalize_routing_policy_material(policy)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def routing_policy_config(policy: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_routing_policy_material(policy)
    return {
        "buzz": {"desk_agent": normalized["desk_agent"]},
        "status_order": normalized["status_order"],
        "routes": normalized["routes"],
    }


def snapshot_change_id(issue_iid: int, snapshot: dict[str, Any]) -> str:
    """Return the canonical identity of one source Issue version."""

    value = {
        "iid": issue_iid,
        "updated_at": snapshot.get("updated_at"),
        "state": snapshot.get("state"),
        "type": snapshot.get("type"),
        "status": snapshot.get("status"),
        "content_digest": snapshot.get("content_digest"),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_fingerprint(issue_iid: int, snapshot: dict[str, Any]) -> str:
    """Identify business facts while ignoring Note-only updated_at drift."""

    value = {
        "iid": issue_iid,
        "state": snapshot.get("state"),
        "type": snapshot.get("type"),
        "status": snapshot.get("status"),
        "labels_valid": snapshot.get("labels_valid"),
        "content_digest": snapshot.get("content_digest"),
        "transition_valid": snapshot.get("transition_valid"),
        "last_valid_status": snapshot.get("last_valid_status"),
        "target": snapshot.get("target"),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def policy_epoch_change_id(source_change_id: str, policy_digest: str) -> str:
    """Give a policy-only reevaluation its own durable processing identity."""

    if not re.fullmatch(r"[0-9a-f]{64}", source_change_id) or not re.fullmatch(
        r"[0-9a-f]{64}", policy_digest
    ):
        raise RouterError("policy epoch change identity requires lowercase SHA-256 inputs")
    encoded = json.dumps(
        {
            "source_change_id": source_change_id,
            "policy_digest": policy_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_note_marker(checkpoint: dict[str, Any]) -> str:
    """Encode one immutable Issue-processing checkpoint as a Desk Note marker."""

    fields = (
        "project_id",
        "issue_iid",
        "channel_id",
        "root_event_id",
        "change_id",
        "policy_digest",
        "policy",
        "snapshot",
    )
    value = {field: checkpoint[field] for field in fields}
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"<!-- {SNAPSHOT_NOTE_PREFIX} {encoded} -->"


def desk_note_marker_payload(
    note: dict[str, Any],
    prefix: str,
    expected_author_id: int,
    expected_author_username: str,
    marker_name: str,
) -> dict[str, Any] | None:
    """Parse a Desk marker and ignore marker-like Notes from other authors."""

    body = note.get("body")
    if not isinstance(body, str) or prefix not in body:
        return None
    author = note.get("author")
    if (
        not isinstance(author, dict)
        or author.get("id") != expected_author_id
        or author.get("username") != expected_author_username
    ):
        return None
    if body.count(prefix) != 1:
        raise RouterError(f"GitLab Desk {marker_name} marker is duplicated or malformed")
    pattern = re.compile(
        rf"<!--\s*{re.escape(prefix)}\s+(.*?)\s*-->",
        re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        raise RouterError(f"GitLab Desk {marker_name} marker is malformed")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RouterError(f"GitLab Desk {marker_name} marker contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise RouterError(f"GitLab Desk {marker_name} marker must contain an object")
    return value


def parse_action_notes(
    notes: list[dict[str, Any]],
    expected_project_id: str | int,
    expected_channel: str,
    expected_author_id: int,
    expected_author_username: str,
) -> list[dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for note in notes:
        action = desk_note_marker_payload(
            note,
            ACTION_NOTE_PREFIX,
            expected_author_id,
            expected_author_username,
            "action",
        )
        if action is None:
            continue
        note_id = note.get("id")
        if not isinstance(note_id, int) or isinstance(note_id, bool) or note_id <= 0:
            raise RouterError("GitLab Desk action Note has an invalid note id")
        if set(action) != set(ACTION_NOTE_FIELDS):
            raise RouterError("GitLab Desk action marker has an invalid schema")
        try:
            action_note_marker(action)
        except (KeyError, TypeError, ValueError) as exc:
            raise RouterError("GitLab Desk action marker has an invalid schema") from exc
        action_id = action.get("action_id")
        if not isinstance(action_id, str) or not re.fullmatch(r"[0-9a-f]{64}", action_id):
            raise RouterError("GitLab Desk action marker has an invalid action_id")
        if action.get("project_id") != str(expected_project_id):
            raise RouterError("GitLab Desk action marker belongs to another project")
        if action.get("channel_id") != expected_channel:
            raise RouterError("GitLab Desk action marker belongs to another Channel")
        issue_iid = action.get("issue_iid")
        if not isinstance(issue_iid, int) or isinstance(issue_iid, bool) or issue_iid <= 0:
            raise RouterError("GitLab Desk action marker has an invalid issue_iid")
        if not isinstance(action.get("change_id"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", action["change_id"]
        ):
            raise RouterError("GitLab Desk action marker has an invalid change_id")
        if not isinstance(action.get("policy_digest"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", action["policy_digest"]
        ):
            raise RouterError("GitLab Desk action marker has an invalid policy_digest")
        if not re.fullmatch(r"[0-9a-f]{64}", str(action.get("root_event_id", ""))):
            raise RouterError("GitLab Desk action marker has an invalid root_event_id")
        if action.get("mode") not in {"route", "final-summary"}:
            raise RouterError("GitLab Desk action marker has an invalid mode")
        if not isinstance(action.get("suggested_target"), str) or not action[
            "suggested_target"
        ]:
            raise RouterError("GitLab Desk action marker has an invalid suggested_target")
        if not re.fullmatch(
            r"[0-9a-f]{64}", str(action.get("suggested_target_pubkey", "")).lower()
        ):
            raise RouterError(
                "GitLab Desk action marker has an invalid suggested_target_pubkey"
            )
        if not isinstance(action.get("reason"), str) or not action["reason"]:
            raise RouterError("GitLab Desk action marker has an invalid reason")
        if action.get("content_trust") != "untrusted-gitlab-data":
            raise RouterError("GitLab Desk action marker has an invalid content_trust")
        source_snapshot = validate_processed_snapshot(
            action.get("source_snapshot"), "GitLab Desk action marker"
        )
        previous_change_id = action.get("previous_change_id")
        if not isinstance(previous_change_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", previous_change_id
        ):
            raise RouterError(
                "GitLab Desk action marker has an invalid previous_change_id"
            )
        expected_mode = (
            "final-summary" if source_snapshot["state"] == "closed" else "route"
        )
        if action["mode"] != expected_mode:
            raise RouterError("GitLab Desk action marker conflicts with source state")
        if action["suggested_target"] != source_snapshot["target"]:
            raise RouterError("GitLab Desk action marker conflicts with source target")
        source_change_id = snapshot_change_id(issue_iid, source_snapshot)
        allowed_change_ids = {
            source_change_id,
            policy_epoch_change_id(source_change_id, action["policy_digest"]),
        }
        if action["change_id"] not in allowed_change_ids:
            raise RouterError(
                "GitLab Desk action marker has a non-canonical source change_id"
            )
        if action_id != desk_action_id(
            expected_project_id,
            issue_iid,
            action["change_id"],
            action["mode"],
        ):
            raise RouterError("GitLab Desk action marker has a non-canonical action_id")
        if action_id in actions:
            raise RouterError("GitLab Issue has multiple Notes for one Desk action")
        actions[action_id] = {**action, "note_id": note_id}
    return sorted(actions.values(), key=lambda item: item["note_id"])


def parse_snapshot_notes(
    notes: list[dict[str, Any]],
    expected_project_id: str | int,
    expected_issue_iid: str | int,
    expected_channel: str,
    expected_author_id: int,
    expected_author_username: str,
) -> list[dict[str, Any]]:
    """Return strict Desk checkpoints ordered by immutable GitLab Note id."""

    issue_iid = int(expected_issue_iid)
    checkpoints: list[dict[str, Any]] = []
    seen_change_ids: set[str] = set()
    for note in notes:
        value = desk_note_marker_payload(
            note,
            SNAPSHOT_NOTE_PREFIX,
            expected_author_id,
            expected_author_username,
            "snapshot",
        )
        if value is None:
            continue
        note_id = note.get("id")
        if not isinstance(note_id, int) or isinstance(note_id, bool) or note_id <= 0:
            raise RouterError("GitLab Desk snapshot Note has an invalid note id")
        expected_keys = {
            "project_id",
            "issue_iid",
            "channel_id",
            "root_event_id",
            "change_id",
            "policy_digest",
            "policy",
            "snapshot",
        }
        if set(value) != expected_keys:
            raise RouterError("GitLab Desk snapshot marker has an invalid schema")
        if value.get("project_id") != str(expected_project_id):
            raise RouterError("GitLab Desk snapshot marker belongs to another project")
        marker_iid = value.get("issue_iid")
        if (
            not isinstance(marker_iid, int)
            or isinstance(marker_iid, bool)
            or marker_iid != issue_iid
        ):
            raise RouterError("GitLab Desk snapshot marker belongs to another Issue")
        if value.get("channel_id") != expected_channel:
            raise RouterError("GitLab Desk snapshot marker belongs to another Channel")
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("root_event_id", ""))):
            raise RouterError("GitLab Desk snapshot marker has an invalid root_event_id")
        change_id = value.get("change_id")
        if not isinstance(change_id, str) or not re.fullmatch(r"[0-9a-f]{64}", change_id):
            raise RouterError("GitLab Desk snapshot marker has an invalid change_id")
        policy = normalize_routing_policy_material(value.get("policy"))
        policy_digest = value.get("policy_digest")
        if (
            not isinstance(policy_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", policy_digest)
            or policy_digest != routing_policy_digest(policy)
        ):
            raise RouterError("GitLab Desk snapshot marker has an invalid policy digest")
        snapshot = validate_processed_snapshot(
            value.get("snapshot"), "GitLab Desk snapshot marker"
        )
        value = {**value, "snapshot": snapshot}
        if change_id in seen_change_ids:
            raise RouterError("GitLab Issue has multiple Notes for one Desk snapshot")
        seen_change_ids.add(change_id)
        checkpoints.append({**value, "note_id": note_id})
    return sorted(checkpoints, key=lambda item: item["note_id"])


def parse_binding(
    notes: list[dict[str, Any]],
    expected_project_id: str | int,
    expected_issue_iid: str | int,
    expected_channel: str,
    expected_author_id: int,
    expected_author_username: str,
) -> str | None:
    roots: list[str] = []
    for note in notes:
        value = desk_note_marker_payload(
            note,
            BINDING_PREFIX,
            expected_author_id,
            expected_author_username,
            "binding",
        )
        if value is None:
            continue
        if (
            value.get("project_id") != str(expected_project_id)
            or value.get("issue_iid") != int(expected_issue_iid)
        ):
            raise RouterError("GitLab binding marker belongs to another Issue")
        if value.get("channel_id") != expected_channel:
            raise RouterError(
                f"Issue already binds to Channel {value.get('channel_id')}, expected {expected_channel}"
            )
        root = value.get("root_event_id")
        if not isinstance(root, str) or not re.fullmatch(r"[0-9a-f]{64}", root):
            raise RouterError("GitLab Desk binding marker has an invalid root_event_id")
        roots.append(root)
    if len(roots) > 1:
        raise RouterError("GitLab Issue has multiple Desk binding notes")
    return roots[0] if roots else None


def route_changed(previous: dict[str, Any] | None, current: dict[str, Any], target: str) -> bool:
    if previous is None:
        return True
    return previous.get("target") != target


def snapshot_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    keys = (
        "state",
        "type",
        "status",
        "labels_valid",
        "transition_valid",
        "content_digest",
    )
    return any(previous.get(key) != current.get(key) for key in keys)


def state_identity(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_id": str(config["gitlab"]["project_id"]),
        "channel_id": str(config["buzz"]["channel_id"]),
        "desk_pubkey": str(config["buzz"]["desk_pubkey"]).lower(),
    }


def empty_state(config: dict[str, Any]) -> dict[str, Any]:
    """Return a fresh materialized state for initialization or recovery."""

    return {
        "identity": state_identity(config),
        "cursor": None,
        "deployment_baseline": None,
        "seen_change_ids": [],
        "issues": {},
        "outbox": [],
        "acked_action_ids": [],
        "acked_actions": [],
    }


def validate_state_identity(config: dict[str, Any], state: dict[str, Any]) -> None:
    if state.get("identity") != state_identity(config):
        raise RouterError(
            "state identity does not match project/channel/Desk; explicit migration is required"
        )


def desk_action_id(
    project_id: str | int,
    issue_iid: str | int,
    change_id: str,
    mode: str,
) -> str:
    payload = json.dumps(
        {
            "version": 1,
            "project_id": str(project_id),
            "issue_iid": int(issue_iid),
            "change_id": change_id,
            "mode": mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class Router:
    def __init__(
        self,
        config: dict[str, Any],
        state_path: Path,
        *,
        dry_run: bool,
    ):
        validate_config(config)
        self.config = config
        self.state_path = state_path
        self.dry_run = dry_run
        self.gitlab = GitLab(config)
        self.gitlab.verify_identity()
        self.gitlab.verify_project_visibility()
        self.buzz = Buzz(config, dry_run)
        configured_baseline = config["gitlab"].get("deployment_baseline")
        self.pinned_deployment_baseline = (
            normalize_deployment_baseline(configured_baseline)
            if configured_baseline is not None
            else None
        )
        self._defer_state_writes = False
        self.state_was_missing = False
        try:
            self.state = load_json(state_path)
            validate_state_identity(config, self.state)
        except FileNotFoundError:
            self.state_was_missing = True
            self.state = empty_state(config)
        self.state.setdefault(
            "seen_change_ids", self.state.pop("seen_event_ids", [])
        )
        self.state.setdefault("issues", {})
        self.state.setdefault("outbox", [])
        self.state.setdefault("acked_action_ids", [])
        self.state.setdefault("acked_actions", [])
        if not isinstance(self.state["outbox"], list) or not isinstance(
            self.state["acked_action_ids"], list
        ) or not isinstance(self.state["acked_actions"], list):
            raise RouterError("state outbox/acked_action_ids/acked_actions must be arrays")
        self.desk_actions: list[dict[str, Any]] = self.state["outbox"]

    @property
    def project_id(self) -> str:
        return str(self.config["gitlab"]["project_id"])

    def save(self) -> None:
        if not self.dry_run and not getattr(self, "_defer_state_writes", False):
            self.state["identity"] = state_identity(self.config)
            atomic_write_json(self.state_path, self.state)

    def validate_action(
        self,
        action: dict[str, Any],
        *,
        policy: dict[str, Any] | None = None,
    ) -> None:
        allowed_fields = set(ACTION_NOTE_FIELDS)
        local_delivery_fields = {"source_stale", "required_outcome"}
        actual_fields = set(action)
        if not allowed_fields.issubset(actual_fields) or not actual_fields.issubset(
            allowed_fields | {"note_id"} | local_delivery_fields
        ):
            raise RouterError("Desk action has invalid fields")
        has_local_stale = "source_stale" in action
        has_required_outcome = "required_outcome" in action
        if has_local_stale != has_required_outcome or (
            has_local_stale
            and (
                action["source_stale"] is not True
                or action["required_outcome"] != "desk-only"
            )
        ):
            raise RouterError("Desk action has an invalid local stale-delivery guard")
        if "note_id" in action and (
            not isinstance(action["note_id"], int)
            or isinstance(action["note_id"], bool)
            or action["note_id"] <= 0
        ):
            raise RouterError("Desk action note_id must be a positive integer")
        action_id = action.get("action_id")
        if not isinstance(action_id, str) or not re.fullmatch(r"[0-9a-f]{64}", action_id):
            raise RouterError("Desk action requires a stable SHA-256 action_id")
        if not isinstance(action.get("change_id"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", action["change_id"]
        ):
            raise RouterError("Desk action change_id must be a 64-char lowercase digest")
        issue_iid = action["issue_iid"]
        if (
            not isinstance(issue_iid, int)
            or isinstance(issue_iid, bool)
            or issue_iid <= 0
        ):
            raise RouterError("Desk action issue_iid must be a positive integer")
        if action["project_id"] != self.project_id or action["channel_id"] != self.buzz.channel:
            raise RouterError("Desk action project/channel identity mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", str(action["root_event_id"])):
            raise RouterError("Desk action root_event_id must be a 64-char lowercase event id")
        if action["mode"] not in {"route", "final-summary"}:
            raise RouterError("Desk action mode is invalid")
        source_snapshot = validate_processed_snapshot(
            action.get("source_snapshot"), "Desk action"
        )
        previous_change_id = action.get("previous_change_id")
        if not isinstance(previous_change_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", previous_change_id
        ):
            raise RouterError("Desk action previous_change_id must be SHA-256")
        target_key = action["suggested_target"]
        if policy is None:
            roster = self.config["agents"]
            desk_key = self.config["buzz"]["desk_agent"]
        else:
            normalized_policy = normalize_routing_policy_material(policy)
            roster = normalized_policy["agents"]
            desk_key = normalized_policy["desk_agent"]
        expected_policy_digest = routing_policy_digest(
            routing_policy_material(self.config) if policy is None else policy
        )
        if action.get("policy_digest") != expected_policy_digest:
            raise RouterError("Desk action policy_digest does not match its routing policy")
        target = roster.get(target_key)
        if (
            not isinstance(target, dict)
            or (
                target.get("kind") != "role"
                and not (target.get("kind") == "desk" and target_key == desk_key)
            )
            or str(target.get("pubkey", "")).lower()
            != str(action["suggested_target_pubkey"]).lower()
        ):
            raise RouterError(
                "Desk action suggested target is outside the configured roster "
                "(possibly a retired Agent pubkey)"
            )
        if source_snapshot["target"] != target_key:
            raise RouterError("Desk action suggested target conflicts with source snapshot")
        expected_mode = (
            "final-summary" if source_snapshot["state"] == "closed" else "route"
        )
        if action["mode"] != expected_mode:
            raise RouterError("Desk action mode conflicts with source snapshot")
        source_change_id = snapshot_change_id(issue_iid, source_snapshot)
        allowed_change_ids = {
            source_change_id,
            policy_epoch_change_id(source_change_id, expected_policy_digest),
        }
        if action["change_id"] not in allowed_change_ids:
            raise RouterError("Desk action change_id is not canonical for source snapshot")
        if action["content_trust"] != "untrusted-gitlab-data":
            raise RouterError("Desk action must preserve the untrusted GitLab content boundary")
        expected_id = desk_action_id(
            self.project_id,
            issue_iid,
            str(action["change_id"]),
            str(action["mode"]),
        )
        if action_id != expected_id:
            raise RouterError("Desk action_id does not match its canonical payload identity")

    def ensure_action_note(self, action: dict[str, Any]) -> None:
        notes = self.gitlab.notes(action["issue_iid"])
        existing = parse_action_notes(
            notes,
            self.project_id,
            self.buzz.channel,
            self.config["gitlab"]["bot_author_id"],
            self.config["gitlab"]["bot_username"],
        )
        matches = [item for item in existing if item["action_id"] == action["action_id"]]
        if matches:
            if action_note_marker(matches[0]) != action_note_marker(action):
                raise RouterError("GitLab Desk action Note conflicts with the current action")
            return
        marker = action_note_marker(action)
        self.gitlab.add_action_note(
            action["issue_iid"],
            f"{marker}\n🧭 Buzz Desk action pending: {action['action_id']}\n"
            "该 Note 是可恢复的 action outbox 事实；请勿编辑、复制或删除。",
        )

    def validated_snapshot_checkpoints(
        self,
        issue: dict[str, Any],
        notes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Validate checkpoint schema and the whole transition/target chain."""

        iid = require_positive_issue_iid(issue)
        checkpoints = parse_snapshot_notes(
            notes,
            self.project_id,
            iid,
            self.buzz.channel,
            self.config["gitlab"]["bot_author_id"],
            self.config["gitlab"]["bot_username"],
        )
        roots = {str(checkpoint["root_event_id"]) for checkpoint in checkpoints}
        if len(roots) > 1:
            raise RouterError(
                f"GitLab Desk snapshot chain for Issue #{iid} has conflicting roots"
            )
        previous: dict[str, Any] | None = None
        previous_policy_digest: str | None = None
        previous_policy: dict[str, Any] | None = None
        for checkpoint in checkpoints:
            stored = checkpoint["snapshot"]
            policy_digest = str(checkpoint["policy_digest"])
            historical_policy = normalize_routing_policy_material(
                checkpoint["policy"]
            )
            historical_config = routing_policy_config(historical_policy)
            derived = {
                field: stored[field]
                for field in (
                    "state",
                    "type",
                    "status",
                    "labels_valid",
                    "updated_at",
                    "content_digest",
                )
            }
            source_change_id = snapshot_change_id(iid, stored)
            expected_change_id = (
                source_change_id
                if previous_policy_digest is None
                or previous_policy_digest == policy_digest
                else policy_epoch_change_id(source_change_id, policy_digest)
            )
            if checkpoint["change_id"] != expected_change_id:
                raise RouterError(
                    f"GitLab Desk snapshot chain for Issue #{iid} has a non-canonical change_id"
                )
            if (
                previous_policy is not None
                and previous_policy["status_order"]
                != historical_policy["status_order"]
            ):
                raise RouterError(
                    f"GitLab Desk snapshot chain for Issue #{iid} changes status_order; "
                    "explicit state-machine migration is required"
                )
            # Route/roster changes start a new deterministic policy epoch, but
            # they must preserve the previous state-machine verdict. Only an
            # explicit status_order migration may reinterpret that history.
            annotate_transition(historical_config, previous, derived)
            derived["target"] = resolve_target(historical_config, derived)
            if derived != stored:
                raise RouterError(
                    f"GitLab Desk snapshot chain for Issue #{iid} has invalid transition/target data"
                )
            previous = stored
            previous_policy_digest = policy_digest
            previous_policy = historical_policy
        return checkpoints

    def ensure_snapshot_note(
        self,
        issue: dict[str, Any],
        change_id: str,
        snapshot: dict[str, Any],
        root: str,
    ) -> None:
        """Persist the processed snapshot before advancing local state/waterline."""

        iid = require_positive_issue_iid(issue)
        policy = routing_policy_material(self.config)
        checkpoint = {
            "project_id": self.project_id,
            "issue_iid": iid,
            "channel_id": self.buzz.channel,
            "root_event_id": root,
            "change_id": change_id,
            "policy_digest": routing_policy_digest(policy),
            "policy": policy,
            "snapshot": snapshot,
        }
        notes = self.gitlab.notes(iid)
        existing = self.validated_snapshot_checkpoints(issue, notes)
        matches = [item for item in existing if item["change_id"] == change_id]
        if matches:
            comparable = {key: matches[0][key] for key in checkpoint}
            if snapshot_note_marker(comparable) != snapshot_note_marker(checkpoint):
                raise RouterError(
                    "GitLab Desk snapshot Note conflicts with the current checkpoint"
                )
            return
        marker = snapshot_note_marker(checkpoint)
        self.gitlab.add_snapshot_note(
            iid,
            f"{marker}\n✅ Buzz Desk processed Issue snapshot: {change_id}\n"
            "该 Note 是可恢复的 snapshot checkpoint；请勿编辑、复制或删除。",
        )

    def enqueue_action(
        self,
        action: dict[str, Any],
        *,
        persist_saas: bool = True,
    ) -> None:
        self.validate_action(action)
        action_id = action.get("action_id")
        if action_id in self.state.get("acked_action_ids", []):
            return
        if any(item.get("action_id") == action_id for item in self.desk_actions):
            return
        if persist_saas and not self.dry_run:
            self.ensure_action_note(action)
        self.desk_actions.append(action)
        self.state["outbox"] = self.desk_actions
        self.save()

    def record_ack(self, action: dict[str, Any], receipt: dict[str, Any]) -> None:
        action_id = str(action["action_id"])
        acked = [str(value) for value in self.state.get("acked_action_ids", [])]
        if action_id not in acked:
            acked.append(action_id)
        self.state["acked_action_ids"] = acked[-1000:]
        records = [
            item
            for item in self.state.get("acked_actions", [])
            if item.get("action_id") != action_id
        ]
        records.append(
            {
                "action_id": action_id,
                "receipt_event_id": receipt["receipt_event_id"],
                "outcome": receipt["outcome"],
                "mention_pubkeys": receipt["mention_pubkeys"],
                "change_id": action["change_id"],
                "issue_iid": action["issue_iid"],
                "root_event_id": action["root_event_id"],
                "mode": action["mode"],
                "acked_at": utc_now(),
            }
        )
        self.state["acked_actions"] = records[-1000:]

    def ack_action(self, action_id: str) -> dict[str, str]:
        if not re.fullmatch(r"[0-9a-f]{64}", action_id):
            raise RouterError("--ack-action must be a 64-char lowercase hex action_id")
        acked = [str(value) for value in self.state.get("acked_action_ids", [])]
        if action_id in acked:
            records = [
                item
                for item in self.state.get("acked_actions", [])
                if item.get("action_id") == action_id
            ]
            return {
                "ack_status": "already-acked",
                "receipt_event_id": str(records[-1].get("receipt_event_id", ""))
                if records
                else "",
                "outcome": str(records[-1].get("outcome", "")) if records else "",
            }
        matches = [
            item for item in self.desk_actions if item.get("action_id") == action_id
        ]
        if len(matches) != 1:
            raise RouterError("--ack-action does not identify exactly one pending Desk action")
        action = matches[0]
        notes = self.gitlab.notes(action["issue_iid"])
        server_actions = parse_action_notes(
            notes,
            self.project_id,
            self.buzz.channel,
            self.config["gitlab"]["bot_author_id"],
            self.config["gitlab"]["bot_username"],
        )
        server_matches = [
            item for item in server_actions if item.get("action_id") == action_id
        ]
        if len(server_matches) != 1 or action_note_marker(
            server_matches[0]
        ) != action_note_marker(action):
            raise RouterError(
                "pending Desk action has no matching immutable GitLab action Note"
            )
        server_action = server_matches[0]
        checkpoints = self.validated_snapshot_checkpoints(
            {"iid": action["issue_iid"]}, notes
        )
        action_policy = self.policy_for_action(server_action, checkpoints)
        self.validate_action(server_action, policy=action_policy)
        current_issue = self.fresh_public_issue(
            int(action["issue_iid"]), purpose="Desk action acknowledgement"
        )
        current_snapshot = issue_snapshot(current_issue, self.config)
        source_stale = bool(action.get("required_outcome") == "desk-only") or (
            action_source_is_stale(action, current_snapshot)
        )
        allowed_roles = {
            str(agent["pubkey"]).lower()
            for agent in action_policy["agents"].values()
            if agent.get("kind") == "role"
        }
        receipt = self.buzz.find_action_receipt(action, allowed_roles)
        if receipt is None:
            raise RouterError(
                "cannot ack Desk action before a valid Desk-authored Thread receipt exists"
            )
        if source_stale and (
            receipt.get("outcome") != "desk-only"
            or receipt.get("mention_pubkeys")
        ):
            raise RouterError(
                "stale Desk action requires outcome=desk-only with zero role mentions"
            )
        receipt_event_id = str(receipt["receipt_event_id"])
        self.desk_actions[:] = [
            item for item in self.desk_actions if item.get("action_id") != action_id
        ]
        self.state["outbox"] = self.desk_actions
        self.record_ack(action, receipt)
        self.save()
        return {
            "ack_status": "acked",
            "receipt_event_id": receipt_event_id,
            "outcome": str(receipt["outcome"]),
        }

    def policy_for_action(
        self,
        action: dict[str, Any],
        checkpoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Resolve an immutable action against its own checkpoint policy."""

        matches = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint["change_id"] == action.get("change_id")
        ]
        if len(matches) > 1:
            raise RouterError("Desk action matches multiple snapshot checkpoints")
        if not matches:
            return routing_policy_material(self.config)
        checkpoint = matches[0]
        if action.get("source_snapshot") != checkpoint["snapshot"]:
            raise RouterError(
                "Desk action source snapshot conflicts with its snapshot checkpoint"
            )
        action_note_id = action.get("note_id")
        if action_note_id is not None and action_note_id >= checkpoint["note_id"]:
            raise RouterError("Desk action Note must precede its snapshot checkpoint")
        predecessors = [
            item
            for item in checkpoints
            if item["note_id"] < checkpoint["note_id"]
        ]
        expected_previous_change_id = (
            predecessors[-1]["change_id"] if predecessors else "0" * 64
        )
        if (
            action_note_id is not None
            and predecessors
            and action_note_id <= predecessors[-1]["note_id"]
        ):
            raise RouterError(
                "Desk action Note must follow its predecessor snapshot checkpoint"
            )
        if action.get("previous_change_id") != expected_previous_change_id:
            raise RouterError(
                "Desk action predecessor conflicts with the snapshot checkpoint chain"
            )
        expected_mode = (
            "final-summary"
            if checkpoint["snapshot"]["state"] == "closed"
            else "route"
        )
        if (
            action.get("root_event_id") != checkpoint["root_event_id"]
            or action.get("suggested_target") != checkpoint["snapshot"]["target"]
            or action.get("mode") != expected_mode
        ):
            raise RouterError(
                "Desk action conflicts with its snapshot checkpoint root/target/mode"
            )
        return normalize_routing_policy_material(checkpoint["policy"])

    def validate_pending_actions_for_current_policy(self) -> None:
        """Stop stale outbox replay before Desk can mention a retired identity."""

        if not self.desk_actions:
            return
        current_policy = routing_policy_material(self.config)
        current_digest = routing_policy_digest(current_policy)
        for action in self.desk_actions:
            if action.get("policy_digest") != current_digest:
                raise RouterError(
                    "pending Desk action belongs to an older routing policy; use "
                    "--ack-action for an already completed checkpoint-bound action, "
                    "or explicitly migrate/cancel it before normal polling"
                )
            self.validate_action(action, policy=current_policy)

    def recover_actions_from_saas(
        self,
        issues: list[dict[str, Any]],
        *,
        notes_by_iid: dict[int, list[dict[str, Any]]] | None = None,
        checkpoints_by_iid: dict[int, list[dict[str, Any]]] | None = None,
    ) -> dict[int, list[dict[str, Any]]]:
        actions_by_iid: dict[int, list[dict[str, Any]]] = {}
        current_policy = routing_policy_material(self.config)
        current_policy_digest = routing_policy_digest(current_policy)
        for issue in issues:
            require_non_confidential_issue(issue)
            iid = require_positive_issue_iid(issue, "recovery snapshot")
            actions = parse_action_notes(
                notes_by_iid[iid] if notes_by_iid is not None else self.gitlab.notes(iid),
                self.project_id,
                self.buzz.channel,
                self.config["gitlab"]["bot_author_id"],
                self.config["gitlab"]["bot_username"],
            )
            checkpoints = (
                checkpoints_by_iid[iid]
                if checkpoints_by_iid is not None
                else self.validated_snapshot_checkpoints(
                    issue,
                    notes_by_iid[iid]
                    if notes_by_iid is not None
                    else self.gitlab.notes(iid),
                )
            )
            checkpoint_change_ids = {
                str(checkpoint["change_id"]) for checkpoint in checkpoints
            }
            unmatched = [
                action
                for action in actions
                if str(action["change_id"]) not in checkpoint_change_ids
            ]
            if len(unmatched) > 1:
                raise RouterError(
                    f"Issue #{iid} has multiple unmatched Desk actions after its latest checkpoint"
                )
            if unmatched:
                pending = unmatched[0]
                latest = checkpoints[-1] if checkpoints else None
                expected_previous_change_id = (
                    str(latest["change_id"]) if latest is not None else "0" * 64
                )
                if pending.get("previous_change_id") != expected_previous_change_id:
                    raise RouterError(
                        f"Issue #{iid} unmatched Desk action has the wrong predecessor"
                    )
                if latest is not None and pending["note_id"] <= latest["note_id"]:
                    raise RouterError(
                        f"Issue #{iid} unmatched Desk action does not follow its checkpoint"
                    )
                processed_current = issue_snapshot(issue, self.config)
                annotate_transition(
                    self.config,
                    latest["snapshot"] if latest is not None else None,
                    processed_current,
                )
                processed_current["target"] = resolve_target(
                    self.config, processed_current
                )
                if snapshot_fingerprint(iid, pending["source_snapshot"]) != (
                    snapshot_fingerprint(iid, processed_current)
                ):
                    raise RouterError(
                        f"Issue #{iid} unmatched Desk action does not describe the current snapshot"
                    )
                if parse_time(pending["source_snapshot"]["updated_at"]) > parse_time(
                    processed_current["updated_at"]
                ):
                    raise RouterError(
                        f"Issue #{iid} unmatched Desk action is newer than the current snapshot"
                    )
            for action in actions:
                if action.get("issue_iid") != iid:
                    raise RouterError("GitLab Desk action Note belongs to another Issue")
                action_policy = self.policy_for_action(action, checkpoints)
                self.validate_action(action, policy=action_policy)
                allowed_roles = {
                    str(agent["pubkey"]).lower()
                    for agent in action_policy["agents"].values()
                    if agent.get("kind") == "role"
                }
                self.buzz.validate_root(
                    str(action["root_event_id"]), self.root_marker(iid)
                )
                receipt = self.buzz.find_action_receipt(action, allowed_roles)
                source_stale = action_source_is_stale(
                    action, issue_snapshot(issue, self.config)
                )
                durable_action = {
                    field: action[field] for field in ACTION_NOTE_FIELDS
                }
                if receipt is None:
                    if routing_policy_digest(action_policy) != current_policy_digest:
                        raise RouterError(
                            f"Issue #{iid} has an unresolved Desk action from an older "
                            "routing policy; explicitly migrate/cancel or complete it "
                            "before recovery"
                        )
                    self.enqueue_action(durable_action, persist_saas=False)
                else:
                    if source_stale and (
                        receipt.get("outcome") != "desk-only"
                        or receipt.get("mention_pubkeys")
                    ):
                        raise RouterError(
                            f"Issue #{iid} stale Desk action requires outcome=desk-only "
                            "with zero role mentions"
                        )
                    self.record_ack(durable_action, receipt)
            actions_by_iid[iid] = actions
        self.save()
        return actions_by_iid

    def root_marker(self, iid: int | str) -> str:
        return f"[{ROOT_MARKER_PREFIX}:{self.project_id}:{iid}]"

    def fresh_public_issue(self, iid: int, *, purpose: str) -> dict[str, Any]:
        """Read one Issue between two project-audience checks.

        The list endpoint is only event discovery.  It is never authority for
        content or confidentiality at an outbound boundary.
        """

        self.gitlab.verify_project_visibility()
        issue = self.gitlab.issue(iid)
        self.gitlab.verify_project_visibility()
        if require_positive_issue_iid(issue, purpose) != iid:
            raise RouterError(f"GitLab Issue {purpose} identity readback mismatch")
        require_non_confidential_issue(issue)
        require_valid_issue_state(issue)
        strict_issue_labels(issue)
        if not isinstance(issue.get("title"), str):
            raise RouterError(f"GitLab Issue {purpose} title must be a string")
        if issue.get("description") is not None and not isinstance(
            issue.get("description"), str
        ):
            raise RouterError(
                f"GitLab Issue {purpose} description must be a string or null"
            )
        updated_at = issue.get("updated_at")
        if not isinstance(updated_at, str):
            raise RouterError(f"GitLab Issue {purpose} updated_at must be a string")
        try:
            parse_time(updated_at)
        except ValueError as exc:
            raise RouterError(
                f"GitLab Issue {purpose} updated_at is not a valid timestamp"
            ) from exc
        return issue

    def fresh_issue_for_scan(
        self,
        observed: dict[str, Any],
        scan_before: str,
    ) -> dict[str, Any] | None:
        """Replace a list snapshot with an authoritative per-Issue response.

        A refresh newer than the fixed scan boundary belongs to the next poll;
        it is neither processed nor marked seen in this one.
        """

        iid = require_positive_issue_iid(observed, "list snapshot")
        fresh = self.fresh_public_issue(iid, purpose="refresh")
        observed_created = observed.get("created_at")
        fresh_created = fresh.get("created_at")
        observed_updated = observed.get("updated_at")
        fresh_updated = fresh.get("updated_at")
        if (
            not isinstance(observed_created, str)
            or not isinstance(fresh_created, str)
            or observed_created != fresh_created
        ):
            raise RouterError(f"GitLab Issue #{iid} created_at identity mismatch")
        if not isinstance(observed_updated, str) or not isinstance(fresh_updated, str):
            raise RouterError(f"GitLab Issue #{iid} lacks updated_at")
        if parse_time(fresh_updated) < parse_time(observed_updated):
            raise RouterError(f"GitLab Issue #{iid} refresh moved backwards")
        if parse_time(fresh_updated) > parse_time(scan_before):
            return None
        return fresh

    def desk_actions_for_output(self) -> list[dict[str, Any]]:
        """Attach one fresh, audience-gated Issue data frame to each action.

        The untrusted content exists only in stdout JSON.  It is deliberately
        excluded from protocol marker messages and durable machine Notes.
        """

        output: list[dict[str, Any]] = []
        guards_changed = False
        for action in self.desk_actions:
            self.validate_action(action)
            iid = int(action["issue_iid"])
            issue = self.fresh_public_issue(iid, purpose="Desk action")
            current = issue_snapshot(issue, self.config)
            source_stale = action_source_is_stale(action, current) or bool(
                action.get("required_outcome") == "desk-only"
            )
            if source_stale and action.get("required_outcome") != "desk-only":
                action["source_stale"] = True
                action["required_outcome"] = "desk-only"
                guards_changed = True
            title = issue.get("title")
            description = issue.get("description")
            if not isinstance(title, str) or description is not None and not isinstance(
                description, str
            ):
                raise RouterError(f"Issue #{iid} title/description schema is invalid")
            enriched = dict(action)
            enriched["source_stale"] = source_stale
            if source_stale:
                # The stale action is only an auditable barrier.  Desk must ack
                # it without a role mention; the next poll handles the newer
                # snapshot as a fresh action.
                enriched["required_outcome"] = "desk-only"
                enriched["suggested_target"] = self.config["buzz"]["desk_agent"]
                enriched["suggested_target_pubkey"] = None
            enriched["untrusted_issue"] = {
                "title": title,
                "description": description or "",
                "labels": strict_issue_labels(issue),
                "state": current["state"],
                "updated_at": issue.get("updated_at"),
            }
            output.append(enriched)
        if guards_changed:
            self.state["outbox"] = self.desk_actions
            self.save()
        return output

    def find_existing_binding(
        self,
        issue: dict[str, Any],
        *,
        notes: list[dict[str, Any]] | None = None,
        repair_missing_note: bool = True,
    ) -> str | None:
        """Recover and validate an existing binding without creating a Thread."""

        iid = str(require_positive_issue_iid(issue))
        issue_state = self.state["issues"].setdefault(iid, {})
        marker = self.root_marker(iid)
        gitlab_config = self.config["gitlab"]
        cached_root = issue_state.get("root_event_id")
        note_root = parse_binding(
            self.gitlab.notes(iid) if notes is None else notes,
            self.project_id,
            iid,
            self.buzz.channel,
            gitlab_config["bot_author_id"],
            gitlab_config["bot_username"],
        )
        if cached_root and note_root and str(cached_root) != note_root:
            raise RouterError(f"Issue #{iid} has conflicting root_event_id values")

        # The Desk-authored Issue note is the cross-system authority.  The
        # local 0600 cache is only a recovery accelerator and must still be
        # validated against the Buzz root before use.
        root = note_root or (str(cached_root) if cached_root else None)
        if root:
            self.buzz.validate_root(root, marker)
            issue_state["root_event_id"] = root
            if note_root is None and repair_missing_note and not self.dry_run:
                self.write_binding_note(issue, root)
            issue_state["binding_note_written"] = note_root is not None or repair_missing_note
            self.save()
            return root

        root = self.buzz.recover_root(marker)
        if root:
            self.buzz.validate_root(root, marker)
            issue_state["root_event_id"] = root
            self.save()
            if repair_missing_note and not self.dry_run:
                self.write_binding_note(issue, root)
            issue_state["binding_note_written"] = repair_missing_note
            self.save()
            return root
        return None

    def ensure_binding(
        self,
        issue: dict[str, Any],
        current: dict[str, Any],
        *,
        allow_create: bool,
    ) -> tuple[str, bool]:
        iid = str(require_positive_issue_iid(issue))
        issue_state = self.state["issues"].setdefault(iid, {})
        marker = self.root_marker(iid)
        root = self.find_existing_binding(issue)
        if root:
            return root, False

        if not allow_create:
            raise RouterError(
                f"Issue #{iid} is an update but has no recoverable Thread binding; "
                "refusing to create a new Thread"
            )

        if current["state"] == "closed":
            desk_instruction = "Desk intake：请读取最终状态并在本 Thread 写结束摘要；不要再指派角色 Agent。"
        else:
            desk_instruction = (
                "Desk intake：新 Issue；请读取原始内容，校验/补齐单值 type:: 与 status::，"
                "再按本业务 Channel 的路由规则在本 Thread 指派下一位角色 Agent。"
            )
        content = (
            f"{marker}\n"
            f"📌 Issue #{iid}\n"
            f"{self.config['gitlab']['project_web_url'].rstrip('/')}/-/issues/{iid}\n"
            f"state={current['state']} · "
            f"type={route_fact_for_message(self.config, current, 'type')} · "
            f"status={route_fact_for_message(self.config, current, 'status')}\n"
            f"content_digest={current.get('content_digest', '(missing)')}\n\n"
            f"{desk_instruction} Issue 原始内容只从本轮 poller 输出的 "
            "untrusted_issue JSON data frame 读取，不从 Thread 协议消息读取。"
        )
        root = self.buzz.send(content)
        issue_state["root_event_id"] = root
        self.save()  # Persist before the GitLab note to narrow the crash duplicate window.
        if not self.dry_run:
            self.write_binding_note(issue, root)
        issue_state["binding_note_written"] = True
        self.save()
        return root, True

    def write_binding_note(self, issue: dict[str, Any], root: str) -> None:
        channel = self.buzz.channel
        marker = binding_marker(self.project_id, issue["iid"], channel, root)
        link = f"buzz://message?channel={channel}&id={root}&thread={root}"
        self.gitlab.add_note(
            issue["iid"],
            f"{marker}\n🔗 Buzz discussion Thread: {link}\n"
            "该 comment 是 Issue→Thread 的机器绑定；请勿手工复制到其他 Issue。",
        )

    def post_transition(
        self,
        change_id: str,
        action: str,
        issue: dict[str, Any],
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        target_key: str,
        root: str,
        *,
        root_created: bool,
        policy_digest: str,
        source_snapshot: dict[str, Any],
        previous_change_id: str,
        force_reassign: bool = False,
    ) -> None:
        target = self.config["agents"][target_key]
        desk_key = self.config["buzz"]["desk_agent"]
        changed = route_changed(previous, current, target_key) or force_reassign
        content_changed = bool(
            previous
            and previous.get("content_digest") != current.get("content_digest")
        )
        event_marker = f"[{EVENT_MARKER_PREFIX}:{self.project_id}:{issue['iid']}:{change_id}]"
        before = (
            "none"
            if previous is None
            else (
                f"{previous.get('state')}/"
                f"{route_fact_for_message(self.config, previous, 'type')}/"
                f"{route_fact_for_message(self.config, previous, 'status')}"
            )
        )
        after = (
            f"{current['state']}/"
            f"{route_fact_for_message(self.config, current, 'type')}/"
            f"{route_fact_for_message(self.config, current, 'status')}"
        )
        closed = current["state"] == "closed" or action == "closed"
        data_frame_only = "请只使用本轮 untrusted_issue 数据帧，不要自行 refetch Issue；"
        if closed:
            instruction = (
                "Desk action：Issue 已关闭；" + data_frame_only + "在本 Thread 写结束摘要；"
                "不要再指派角色 Agent，也不要自动路由 executor。"
            )
            needs_desk = True
        elif changed or content_changed:
            if target_key == desk_key:
                instruction = (
                    "Desk action：Issue 路由无效或内容已变化；"
                    + data_frame_only
                    + "请复核并写人工处理摘要；"
                    "不要自动路由 executor。"
                )
            else:
                instruction = (
                    f"Desk action：路由目标或 Issue 内容已变化。建议目标：{target['name']} "
                    f"(pubkey={target['pubkey']})。{data_frame_only}"
                    "确认 type/status 与上下文一致；"
                    "确认后使用显式 pubkey 在本 Thread @ 目标角色 Agent。"
                    "executor 永远不能被自动路由。"
                )
            needs_desk = True
        else:
            instruction = (
                f"主责仍是 {target['name']}；仅记录状态，不重复 @，避免 Agent 自唤醒循环。"
            )
            needs_desk = False
        content = (
            f"{event_marker}\n"
            f"🔄 Issue #{issue['iid']} {action}: {before} → {after}\n"
            f"{self.config['gitlab']['project_web_url'].rstrip('/')}/-/issues/{issue['iid']}\n"
            f"{instruction}"
        )
        if not root_created and not self.buzz.contains_message(event_marker, root):
            self.buzz.send(content, root_event_id=root)
        if needs_desk:
            mode = "final-summary" if closed else "route"
            self.enqueue_action(
                {
                    "action_id": desk_action_id(
                        self.project_id, issue["iid"], change_id, mode
                    ),
                    "change_id": change_id,
                    "policy_digest": policy_digest,
                    "project_id": self.project_id,
                    "issue_iid": int(issue["iid"]),
                    "channel_id": self.buzz.channel,
                    "root_event_id": root,
                    "mode": mode,
                    "suggested_target": target_key,
                    "suggested_target_pubkey": target["pubkey"],
                    "reason": "content-changed" if content_changed else action,
                    "content_trust": "untrusted-gitlab-data",
                    "source_snapshot": source_snapshot,
                    "previous_change_id": previous_change_id,
                }
            )

    def process_issue(self, issue: dict[str, Any], *, is_new: bool, change_id: str) -> None:
        require_non_confidential_issue(issue)
        iid = require_positive_issue_iid(issue)
        current = issue_snapshot(issue, self.config)
        if not isinstance(current.get("updated_at"), str):
            raise RouterError(f"Issue #{iid} has no updated_at")
        notes = self.gitlab.notes(iid)
        checkpoints = self.validated_snapshot_checkpoints(issue, notes)
        external_actions = parse_action_notes(
            notes,
            self.project_id,
            self.buzz.channel,
            self.config["gitlab"]["bot_author_id"],
            self.config["gitlab"]["bot_username"],
        )
        issue_state = self.state["issues"].setdefault(str(iid), {})
        previous = issue_state.get("snapshot")
        stored_checkpoint_change_id = issue_state.get("last_checkpoint_change_id")
        if checkpoints:
            latest_checkpoint = checkpoints[-1]
            # A durable checkpoint proves intake already completed even when
            # the final local save (and therefore new/update classification)
            # was lost in a crash.
            is_new = False
            checkpoint_ids = {
                str(checkpoint["change_id"]) for checkpoint in checkpoints
            }
            if stored_checkpoint_change_id not in {None, "0" * 64} and (
                not isinstance(stored_checkpoint_change_id, str)
                or stored_checkpoint_change_id not in checkpoint_ids
            ):
                raise RouterError(
                    f"Issue #{iid} local checkpoint identity is absent from GitLab"
                )
            if stored_checkpoint_change_id != latest_checkpoint["change_id"]:
                # The external checkpoint can be ahead when the process died
                # after Note readback but before the final local atomic save.
                previous = dict(latest_checkpoint["snapshot"])
                issue_state["snapshot"] = previous
                issue_state["policy_digest"] = latest_checkpoint["policy_digest"]
                issue_state["policy"] = dict(latest_checkpoint["policy"])
                issue_state["last_checkpoint_change_id"] = latest_checkpoint[
                    "change_id"
                ]
            elif previous is None or snapshot_fingerprint(iid, previous) != (
                snapshot_fingerprint(iid, latest_checkpoint["snapshot"])
            ):
                raise RouterError(
                    f"Issue #{iid} local snapshot conflicts with its latest checkpoint"
                )
            stored_checkpoint_change_id = latest_checkpoint["change_id"]
        else:
            if stored_checkpoint_change_id not in {None, "0" * 64}:
                raise RouterError(
                    f"Issue #{iid} local state references a missing GitLab checkpoint"
                )
            issue_state["last_checkpoint_change_id"] = "0" * 64
            stored_checkpoint_change_id = "0" * 64
        policy = routing_policy_material(self.config)
        policy_digest = routing_policy_digest(policy)
        previous_policy_digest = issue_state.get("policy_digest")
        previous_policy_value = issue_state.get("policy")
        previous_policy: dict[str, Any] | None = None
        if previous is not None:
            if (
                not isinstance(previous_policy_digest, str)
                or previous_policy_value is None
            ):
                raise RouterError(
                    f"Issue #{iid} local state lacks its historical routing policy; "
                    "explicit state migration is required"
                )
            previous_policy = normalize_routing_policy_material(previous_policy_value)
            if routing_policy_digest(previous_policy) != previous_policy_digest:
                raise RouterError(
                    f"Issue #{iid} local routing policy digest mismatch"
                )
        policy_changed = bool(
            previous is not None
            and isinstance(previous_policy_digest, str)
            and previous_policy_digest != policy_digest
        )
        if (
            policy_changed
            and previous_policy is not None
            and previous_policy["status_order"] != policy["status_order"]
        ):
            raise RouterError(
                f"Issue #{iid} routing policy changes status_order; "
                "explicit state-machine migration is required"
            )
        annotate_transition(self.config, previous, current)
        target = resolve_target(self.config, current)
        processed_snapshot = {**current, "target": target}
        facts_changed = snapshot_changed(previous, current)
        # Source version identity includes updated_at, so a real A→B→A cycle
        # remains three distinct events. If an action Note was written but its
        # checkpoint failed, match the in-flight transition by its business
        # fingerprint plus the exact previous version and reuse its source ID.
        previous_change_id = str(stored_checkpoint_change_id)
        change_id = snapshot_change_id(iid, processed_snapshot)
        target_identity_changed = False
        if policy_changed and previous_policy is not None and previous is not None:
            previous_target = previous.get("target")
            old_target = previous_policy["agents"].get(previous_target)
            new_target = policy["agents"].get(target)
            target_identity_changed = bool(
                previous_target == target
                and isinstance(old_target, dict)
                and isinstance(new_target, dict)
                and old_target.get("pubkey") != new_target.get("pubkey")
            )
        if policy_changed:
            pending_old_policy = [
                action
                for action in self.desk_actions
                if action.get("issue_iid") == iid
                and action.get("policy_digest") != policy_digest
            ]
            if pending_old_policy:
                raise RouterError(
                    f"Issue #{iid} has a pending Desk action from an older policy "
                    "(possibly a retired Agent pubkey); acknowledge or explicitly "
                    "migrate/cancel it before policy rotation"
                )
        if policy_changed:
            change_id = policy_epoch_change_id(change_id, policy_digest)

        checkpoint_snapshot = processed_snapshot
        checkpoint_change_ids = {
            str(checkpoint["change_id"]) for checkpoint in checkpoints
        }
        unmatched_external = [
            action
            for action in external_actions
            if str(action["change_id"]) not in checkpoint_change_ids
        ]
        if len(unmatched_external) > 1:
            raise RouterError(
                f"Issue #{iid} has multiple unmatched Desk actions after its latest checkpoint"
            )
        if unmatched_external:
            pending_external = unmatched_external[0]
            self.validate_action(pending_external, policy=policy)
            if (
                pending_external.get("previous_change_id") != previous_change_id
                or snapshot_fingerprint(iid, pending_external["source_snapshot"])
                != snapshot_fingerprint(iid, processed_snapshot)
            ):
                raise RouterError(
                    f"Issue #{iid} unmatched Desk action conflicts with the current transition"
                )
            if checkpoints and pending_external["note_id"] <= checkpoints[-1]["note_id"]:
                raise RouterError(
                    f"Issue #{iid} unmatched Desk action does not follow its checkpoint"
                )
        action_candidates = list(self.desk_actions)
        known_action_ids = {
            str(action.get("action_id")) for action in action_candidates
        }
        action_candidates.extend(
            action
            for action in unmatched_external
            if str(action["action_id"]) not in known_action_ids
        )
        transition_matches = [
            action
            for action in action_candidates
            if action.get("issue_iid") == iid
            and str(action.get("change_id")) not in checkpoint_change_ids
            and action.get("policy_digest") == policy_digest
            and action.get("previous_change_id") == previous_change_id
            and isinstance(action.get("source_snapshot"), dict)
            and snapshot_fingerprint(iid, action["source_snapshot"])
            == snapshot_fingerprint(iid, processed_snapshot)
        ]
        if len(transition_matches) > 1:
            raise RouterError(
                f"Issue #{iid} has multiple pending actions for one source transition"
            )
        if transition_matches:
            pending = transition_matches[0]
            self.validate_action(pending, policy=policy)
            change_id = str(pending["change_id"])
            checkpoint_snapshot = dict(pending["source_snapshot"])

        root, created = self.ensure_binding(issue, current, allow_create=is_new)
        if is_new:
            mode = "final-summary" if current["state"] == "closed" else "route"
            self.enqueue_action(
                {
                    "action_id": desk_action_id(self.project_id, iid, change_id, mode),
                    "change_id": change_id,
                    "policy_digest": policy_digest,
                    "project_id": self.project_id,
                    "issue_iid": iid,
                    "channel_id": self.buzz.channel,
                    "root_event_id": root,
                    "mode": mode,
                    "suggested_target": target,
                    "suggested_target_pubkey": self.config["agents"][target]["pubkey"],
                    # This field is crash/replay stable. Whether the root was
                    # created in this process is an implementation detail and
                    # must not change the durable action payload.
                    "reason": "new-issue",
                    "content_trust": "untrusted-gitlab-data",
                    "source_snapshot": checkpoint_snapshot,
                    "previous_change_id": previous_change_id,
                }
            )
        elif facts_changed or policy_changed:
            if previous and previous.get("state") != current["state"]:
                action = "closed" if current["state"] == "closed" else "reopened"
            else:
                action = "updated"
            self.post_transition(
                change_id,
                action,
                issue,
                previous,
                current,
                target,
                root,
                root_created=created,
                policy_digest=policy_digest,
                source_snapshot=checkpoint_snapshot,
                previous_change_id=previous_change_id,
                force_reassign=target_identity_changed,
            )
        # GitLab Note creation touches Issue.updated_at. Observation-only
        # updated_at changes are absorbed into local state without writing a
        # new checkpoint, preventing checkpoint -> updated_at -> checkpoint
        # churn. A real routing/content/policy change still gets a checkpoint.
        checkpoint_required = is_new or facts_changed or policy_changed
        if checkpoint_required and not self.dry_run:
            self.ensure_snapshot_note(
                issue,
                change_id,
                checkpoint_snapshot,
                root,
            )
        issue_state["snapshot"] = processed_snapshot
        if checkpoint_required:
            issue_state["last_checkpoint_change_id"] = change_id
        issue_state["policy_digest"] = policy_digest
        issue_state["policy"] = policy
        self.save()

    def recover_missing_state(
        self,
        issues: list[dict[str, Any]],
        scan_started: str,
        baseline: dict[str, Any],
    ) -> None:
        """Rebuild state from pinned Git config plus GitLab/Buzz facts atomically.

        The pinned deployment baseline distinguishes post-deployment Issues from
        deliberately unbound legacy Issues.  All local writes are deferred until
        the complete universe and every SaaS receipt have been reconciled.  If
        the process fails after an external idempotent write, the state file is
        still absent and the next recovery repeats from authoritative markers.
        """

        baseline = normalize_deployment_baseline(baseline)
        if parse_time(baseline["established_at"]) > parse_time(scan_started):
            raise RouterError(
                "config.gitlab.deployment_baseline is later than the current GitLab scan boundary"
            )
        prepared: list[
            tuple[
                dict[str, Any],
                int,
                dict[str, Any],
                str,
                list[dict[str, Any]],
                str | None,
                list[dict[str, Any]],
            ]
        ] = []
        notes_by_iid: dict[int, list[dict[str, Any]]] = {}
        for issue in issues:
            require_non_confidential_issue(issue)
            iid = require_positive_issue_iid(issue, "recovery snapshot")
            current = issue_snapshot(issue, self.config)
            if not isinstance(issue.get("created_at"), str) or not isinstance(
                current.get("updated_at"), str
            ):
                raise RouterError(f"Issue #{iid} lacks created_at/updated_at")
            notes = self.gitlab.notes(iid)
            notes_by_iid[iid] = notes
            note_root = parse_binding(
                notes,
                self.project_id,
                iid,
                self.buzz.channel,
                self.config["gitlab"]["bot_author_id"],
                self.config["gitlab"]["bot_username"],
            )
            checkpoints = self.validated_snapshot_checkpoints(issue, notes)
            # Parse all action markers before any recovery write. Receipt/root
            # validation happens below in recover_actions_from_saas().
            parse_action_notes(
                notes,
                self.project_id,
                self.buzz.channel,
                self.config["gitlab"]["bot_author_id"],
                self.config["gitlab"]["bot_username"],
            )
            prepared.append(
                (
                    issue,
                    iid,
                    current,
                    issue_change_id(issue, self.config),
                    notes,
                    note_root,
                    checkpoints,
                )
            )

        self._defer_state_writes = True
        try:
            self.state["deployment_baseline"] = dict(baseline)
            self.state["cursor"] = None
            actions_by_iid = self.recover_actions_from_saas(
                issues,
                notes_by_iid=notes_by_iid,
                checkpoints_by_iid={
                    iid: checkpoints
                    for _, iid, _, _, _, _, checkpoints in prepared
                },
            )
            seen_change_ids: list[str] = []
            processed = 0
            for (
                issue,
                iid,
                current,
                change_id,
                notes,
                note_root,
                checkpoints,
            ) in prepared:
                fresh = self.fresh_issue_for_scan(issue, scan_started)
                if fresh is None:
                    raise RouterError(
                        f"GitLab Issue #{iid} changed beyond the recovery boundary; "
                        "retry the complete scan"
                    )
                fresh_current = issue_snapshot(fresh, self.config)
                if issue_change_id(fresh, self.config) != change_id or fresh_current != current:
                    raise RouterError(
                        f"GitLab Issue #{iid} changed during recovery; retry the complete scan"
                    )
                issue = fresh
                actions = actions_by_iid.get(iid, [])
                created_after_deployment = iid > baseline["max_iid"]
                business_change_id = snapshot_change_id(iid, current)

                # A checkpoint is the authoritative proof that a snapshot was
                # already processed. Seed local state from it before comparing
                # the current GitLab snapshot; never replay it as a new Issue.
                if checkpoints:
                    latest = checkpoints[-1]
                    root = self.find_existing_binding(
                        issue,
                        notes=notes,
                        repair_missing_note=False,
                    )
                    if root is None or latest["root_event_id"] != root:
                        raise RouterError(
                            f"Issue #{iid} snapshot checkpoint has no matching Thread binding"
                        )
                    issue_state = self.state["issues"].setdefault(str(iid), {})
                    issue_state["root_event_id"] = root
                    issue_state["snapshot"] = dict(latest["snapshot"])
                    issue_state["last_checkpoint_change_id"] = latest["change_id"]
                    issue_state["policy_digest"] = latest["policy_digest"]
                    issue_state["policy"] = dict(latest["policy"])
                    if note_root is None:
                        # The Buzz root and checkpoint agree; restore the
                        # missing cross-system binding Note idempotently.
                        self.find_existing_binding(issue)
                    current_policy = routing_policy_material(self.config)
                    if (
                        snapshot_change_id(iid, latest["snapshot"])
                        != business_change_id
                        or latest["policy_digest"]
                        != routing_policy_digest(current_policy)
                    ):
                        self.process_issue(
                            issue,
                            is_new=False,
                            change_id=change_id,
                        )
                        processed += 1
                else:
                    # Without a checkpoint, only two post-deployment crash
                    # windows are unambiguous: no root yet, or a root with no
                    # action/checkpoint; both resume first intake. A single
                    # matching `new-issue` action proves that intake already
                    # happened and only its checkpoint was lost. Every other
                    # bound legacy shape requires explicit migration.
                    recovered = dict(current)
                    annotate_transition(self.config, None, recovered)
                    recovered["target"] = resolve_target(self.config, recovered)
                    matching_actions = [
                        action
                        for action in actions
                        if action.get("reason") == "new-issue"
                        and action.get("previous_change_id") == "0" * 64
                        and snapshot_fingerprint(iid, action["source_snapshot"])
                        == snapshot_fingerprint(iid, recovered)
                    ]
                    if note_root is not None and not (
                        created_after_deployment
                        and (
                            not actions
                            or (
                                len(actions) == 1
                                and len(matching_actions) == 1
                                and matching_actions[0]["reason"] == "new-issue"
                            )
                        )
                    ):
                        raise RouterError(
                            f"Issue #{iid} has a Thread/action history but no snapshot checkpoint; "
                            "explicit checkpoint migration is required"
                        )

                    root = self.find_existing_binding(
                        issue,
                        notes=notes,
                        repair_missing_note=False,
                    )
                    if root is None:
                        if created_after_deployment:
                            self.process_issue(
                                issue,
                                is_new=True,
                                change_id=change_id,
                            )
                            processed += 1
                        else:
                            # This is a deliberately unbound pre-deployment
                            # Issue. It remains baseline-only; a later update
                            # cannot create a Thread.
                            annotate_transition(self.config, None, current)
                            target = resolve_target(self.config, current)
                            self.state["issues"][str(iid)] = {
                                "snapshot": {**current, "target": target},
                                "last_checkpoint_change_id": "0" * 64,
                                "policy_digest": routing_policy_digest(
                                    routing_policy_material(self.config)
                                ),
                                "policy": routing_policy_material(self.config),
                            }
                    elif created_after_deployment and not actions:
                        self.process_issue(
                            issue,
                            is_new=True,
                            change_id=change_id,
                        )
                        processed += 1
                    elif (
                        created_after_deployment
                        and len(actions) == 1
                        and len(matching_actions) == 1
                        and matching_actions[0]["reason"] == "new-issue"
                    ):
                        target = recovered["target"]
                        action = matching_actions[0]
                        expected_mode = (
                            "final-summary" if recovered["state"] == "closed" else "route"
                        )
                        if (
                            action["root_event_id"] != root
                            or action["mode"] != expected_mode
                            or action["suggested_target"] != target
                        ):
                            raise RouterError(
                                f"Issue #{iid} new-issue action conflicts with current snapshot"
                            )
                        self.find_existing_binding(issue)
                        self.ensure_snapshot_note(
                            issue,
                            action["change_id"],
                            action["source_snapshot"],
                            root,
                        )
                        self.state["issues"][str(iid)]["snapshot"] = recovered
                        self.state["issues"][str(iid)]["last_checkpoint_change_id"] = (
                            action["change_id"]
                        )
                        self.state["issues"][str(iid)]["policy_digest"] = (
                            routing_policy_digest(routing_policy_material(self.config))
                        )
                        self.state["issues"][str(iid)]["policy"] = (
                            routing_policy_material(self.config)
                        )
                    else:
                        raise RouterError(
                            f"Issue #{iid} has a Thread/action history but no snapshot checkpoint; "
                            "explicit checkpoint migration is required"
                        )
                seen_change_ids.append(change_id)
            self.state["seen_change_ids"] = seen_change_ids[-1000:]
            self.state["cursor"] = {"updated_at": scan_started, "iid": 0}
            self.state["recovery_complete"] = True
        finally:
            self._defer_state_writes = False

        self.save()
        print(
            json.dumps(
                {
                    "processed": processed,
                    "cursor": self.state["cursor"],
                    "desk_actions": self.desk_actions_for_output(),
                    "baseline_only": False,
                    "recovered_state": True,
                },
                ensure_ascii=False,
            )
        )

    def run(self, *, bootstrap_existing: bool, initialize: bool = False) -> None:
        if initialize and bootstrap_existing:
            raise RouterError("--initialize and --bootstrap-existing are mutually exclusive")
        if not self.state.get("cursor"):
            scan_started = self.gitlab.server_time()
            observed_issues = self.gitlab.stable_issue_universe(scan_started)
            issues: list[dict[str, Any]] = []
            for observed_issue in observed_issues:
                fresh = self.fresh_issue_for_scan(observed_issue, scan_started)
                if fresh is None:
                    raise RouterError(
                        "GitLab Issue changed beyond the initialization/recovery boundary; "
                        "retry the complete scan"
                    )
                issues.append(fresh)
            pinned_baseline = getattr(self, "pinned_deployment_baseline", None)
            if pinned_baseline is not None:
                if initialize or bootstrap_existing:
                    raise RouterError(
                        "state is missing/incomplete but config already pins deployment_baseline; "
                        "use automatic recovery without initialization flags"
                    )
                # A prior process may have left a pre-cursor state file.  SaaS
                # markers are authoritative, so discard any partial materialized
                # view and rebuild it under the same pinned baseline.
                self.state = empty_state(self.config)
                self.desk_actions = self.state["outbox"]
                self.recover_missing_state(issues, scan_started, pinned_baseline)
                return
            if not initialize and not bootstrap_existing:
                raise RouterError(
                    "router state is missing or incomplete and config has no pinned "
                    "deployment_baseline; use --initialize exactly once, then copy its "
                    "pin_deployment_baseline output into config before enabling schedule"
                )
            # Validate the complete baseline before bootstrap can emit an
            # external write. GitLab project IIDs are monotonically assigned,
            # so max_iid remains an unambiguous deployment boundary even when
            # a concurrent create has a created_at before scan_started.
            baseline_iids: list[int] = []
            for issue in issues:
                require_non_confidential_issue(issue)
                iid = require_positive_issue_iid(issue, "baseline snapshot")
                current = issue_snapshot(issue, self.config)
                if not isinstance(current.get("updated_at"), str):
                    raise RouterError(f"Issue #{iid} has no updated_at")
                baseline_iids.append(iid)
            self.state["deployment_baseline"] = {
                "established_at": scan_started,
                "max_iid": max(baseline_iids, default=0),
            }
            if bootstrap_existing:
                print(f"bootstrap: {len(issues)} existing Issues", file=sys.stderr)
                for observed_issue in issues:
                    issue = self.fresh_issue_for_scan(observed_issue, scan_started)
                    if issue is None:
                        raise RouterError(
                            "GitLab Issue changed beyond the bootstrap boundary; "
                            "retry the complete scan"
                        )
                    self.process_issue(
                        issue,
                        is_new=True,
                        change_id=issue_change_id(issue, self.config),
                    )
            else:
                # Establish a complete baseline without creating Threads. The
                # fixed scan boundary leaves later Issues unseen so the next
                # poll classifies them as new.
                baseline_policy = routing_policy_material(self.config)
                baseline_policy_digest = routing_policy_digest(baseline_policy)
                for observed_issue in issues:
                    issue = self.fresh_issue_for_scan(observed_issue, scan_started)
                    if issue is None:
                        raise RouterError(
                            "GitLab Issue changed beyond the initialization boundary; "
                            "retry the complete scan"
                        )
                    current = issue_snapshot(issue, self.config)
                    annotate_transition(self.config, None, current)
                    target = resolve_target(self.config, current)
                    self.state["issues"][str(issue["iid"])] = {
                        "snapshot": {**current, "target": target},
                        "last_checkpoint_change_id": "0" * 64,
                        "policy_digest": baseline_policy_digest,
                        "policy": baseline_policy,
                    }
            self.state["cursor"] = {"updated_at": scan_started, "iid": 0}
            self.save()
            print(
                json.dumps(
                    {
                        "processed": len(issues) if bootstrap_existing else 0,
                        "cursor": self.state["cursor"],
                        "desk_actions": self.desk_actions_for_output(),
                        "baseline_only": not bootstrap_existing,
                        "pin_deployment_baseline": self.state["deployment_baseline"],
                    },
                    ensure_ascii=False,
                )
            )
            return

        cursor = normalize_cursor(self.state["cursor"])
        deployment_baseline = normalize_deployment_baseline(
            self.state.get("deployment_baseline")
        )
        pinned_baseline = getattr(self, "pinned_deployment_baseline", None)
        if hasattr(self, "pinned_deployment_baseline") and pinned_baseline is None:
            raise RouterError(
                "config.gitlab.deployment_baseline must pin the --initialize output "
                "before normal polling can run"
            )
        if pinned_baseline is not None and pinned_baseline != deployment_baseline:
            raise RouterError(
                "config.gitlab.deployment_baseline does not match durable state; "
                "refusing implicit migration"
            )
        self.validate_pending_actions_for_current_policy()
        scan_before = self.gitlab.server_time()
        if parse_time(scan_before) < parse_time(cursor["updated_at"]):
            raise RouterError(
                "GitLab server clock moved behind the durable waterline; refusing to scan"
            )
        seen_list = [str(value) for value in self.state.get("seen_change_ids", [])]
        seen = set(seen_list)
        issues = self.gitlab.updated_issues(cursor, scan_before)
        processed = 0
        for observed_issue in issues:
            issue = self.fresh_issue_for_scan(observed_issue, scan_before)
            if issue is None:
                continue
            require_non_confidential_issue(issue)
            iid = require_positive_issue_iid(issue)
            change_id = issue_change_id(issue, self.config)
            if change_id in seen:
                continue
            created_at = issue.get("created_at")
            updated_at = issue.get("updated_at")
            if not isinstance(created_at, str) or not isinstance(updated_at, str):
                raise RouterError(f"Issue #{iid} lacks created_at/updated_at")
            issue_state = self.state["issues"].get(str(iid))
            has_snapshot = isinstance(issue_state, dict) and isinstance(
                issue_state.get("snapshot"), dict
            )
            is_new = not has_snapshot and iid > deployment_baseline["max_iid"]
            try:
                self.process_issue(issue, is_new=is_new, change_id=change_id)
                seen.add(change_id)
                seen_list.append(change_id)
                self.state["seen_change_ids"] = seen_list[-1000:]
                self.save()
                processed += 1
            except Exception:
                # Never advance past a failed snapshot. The inclusive next poll
                # replays it from the last committed high-water mark.
                raise
        # The complete stable-universe scan through scan_before succeeded.  It
        # is now safe to move the waterline even when the window had no changes.
        self.state["cursor"] = {"updated_at": scan_before, "iid": 0}
        self.save()
        print(
            json.dumps(
                {
                    "processed": processed,
                    "cursor": self.state["cursor"],
                    "desk_actions": self.desk_actions_for_output(),
                    "baseline_only": False,
                },
                ensure_ascii=False,
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--initialize",
        action="store_true",
        help="establish the first baseline only; pin the emitted value in Git config",
    )
    parser.add_argument("--bootstrap-existing", action="store_true")
    parser.add_argument(
        "--ack-action",
        metavar="ACTION_ID",
        help="remove one durable Desk action only after its Buzz reply/mention readback succeeds",
    )
    parser.add_argument(
        "--resolve-route",
        nargs=3,
        metavar=("TYPE", "STATUS", "STATE"),
        help="print target Agent for a route tuple without contacting GitLab/Buzz",
    )
    return parser.parse_args()


def lock_path_for(config: dict[str, Any]) -> Path:
    """Return the single lock for a Channel, regardless of state-file overrides."""

    channel = str(config["buzz"]["channel_id"])
    channel_key = hashlib.sha256(channel.encode("utf-8")).hexdigest()[:32]
    return Path(os.path.expanduser("~/.config/buzz/agents/.locks")) / (
        f"desk-issue-poller-{channel_key}.lock"
    )


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    validate_config(config)
    if args.ack_action and (
        args.dry_run or args.initialize or args.bootstrap_existing or args.resolve_route
    ):
        raise RouterError(
            "--ack-action cannot be combined with --dry-run, --initialize, "
            "--bootstrap-existing, or --resolve-route"
        )
    if args.initialize and args.bootstrap_existing:
        raise RouterError("--initialize and --bootstrap-existing are mutually exclusive")
    if args.resolve_route:
        issue_type, status, state = args.resolve_route
        snapshot = {
            "type": None if issue_type == "-" else issue_type,
            "status": None if status == "-" else status,
            "state": state,
            "labels_valid": issue_type != "-" and status != "-",
        }
        key = resolve_target(config, snapshot)
        print(json.dumps({"target": key, **config["agents"][key]}, ensure_ascii=False))
        return 0

    state_path = args.state or Path(
        os.path.expanduser(
            f"~/.config/buzz/agents/.desk-issue-poller-{config['business']}.json"
        )
    )
    lock_path = lock_path_for(config)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(lock_path.parent, 0o700)
    with lock_path.open("w", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another Desk issue poller instance is running; skipped")
            return 0
        router = Router(config, state_path, dry_run=args.dry_run)
        if args.ack_action:
            result = router.ack_action(args.ack_action)
            print(
                json.dumps(
                    {"action_id": args.ack_action, **result},
                    ensure_ascii=False,
                )
            )
        else:
            router.run(
                bootstrap_existing=args.bootstrap_existing,
                initialize=args.initialize,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RouterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
