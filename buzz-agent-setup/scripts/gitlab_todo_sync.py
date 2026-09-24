#!/usr/bin/env python3
"""Personal-channel GitLab todo sync: pull the owner's pending todos into the owner's Buzz Channel (ADR-0013).

A `systemd --user` timer starts this script every 600 seconds under an owner-fixed 0600 env.  There is
no LLM in this path.  One run

  1. checks the PAT belongs to the configured GitLab user and that the personal Channel has exactly one
     human member (the owner) and no member outside {owner, publisher, trusted done authors}, otherwise
     fails closed before reading any todo;
  2. reads the pending todo list once, and settles todos it delivered earlier that are no longer pending
     (RESOLVED: handled in GitLab directly; a RESOLVED todo that is listed again goes back to ACKED);
  3. marks a todo done in GitLab only when a trusted author either replied `todo:done:<id>` into the Thread of a
     todo this script itself delivered (an `e` tag names that todo's message or the root it was posted under) or
     put a done reaction (`todo.done_emojis`, default ✅) on that todo's own message, after it was delivered; one
     failing todo does not stop the ones behind it;
  4. posts one message per new pending todo, @ the owner, replying into the target's existing Thread.  A todo
     Buzz refuses twice (reply, then top level) is only settled as REJECTED once a later send in the same run
     succeeds; two refusals in a row, or a refusal with no later success, fail the run.

The owner's PAT needs `api` (GitLab has no narrower scope for marking todos done).  The code therefore
limits the PAT to three endpoints and never hands it to the Buzz CLI child process.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_sync as sync  # noqa: E402


TOKEN_ENV = "GITLAB_TODO_TOKEN"
CONFIG_ENV = "GITLAB_TODO_CONFIG"
STATE_FILE = "todo-state.json"
HEADER_VERSION = "gitlab-todo:v1"

CONFIG_KEYS = frozenset({"version", "channel_id", "publisher_pubkey", "owner_pubkey", "done_authors", "desk_pubkey",
                         "buzz", "gitlab", "todo", "state_dir"})
GITLAB_KEYS = frozenset({"base_url", "username", "token_env"})
TODO_KEYS = frozenset({"actions", "since", "max_per_run", "mark_done", "done_emojis"})
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ACTION_RE = re.compile(r"[a-z_]{1,40}")
DONE_RE = re.compile(r"todo:done:([0-9]{1,12})")
PAGE_RE = re.compile(r"[0-9]{1,6}")
STATES = frozenset({"PENDING", "ACKED", "DONE", "RESOLVED", "REJECTED"})

DEFAULT_ACTIONS = ["*"]
DEFAULT_MAX_PER_RUN = 20
DEFAULT_DONE_EMOJIS = ["✅"]
CHECK_MARK = "✅"  # the emoji whose picker search name ("check") the done hint spells out
DONE_EMOJI_MAX = 32
VARIATION_SELECTOR = "\ufe0f"  # U+FE0F: ✔ and ✔️ are the same reaction to a person
MAX_PER_RUN_LIMIT = 50
TODO_PAGE_MAX = 30  # x 100 per page = 3000 pending todos; more is truncated, not fatal
GITLAB_TIMEOUT_SECONDS = 20
GITLAB_BUDGET_SECONDS = 60
GITLAB_RESPONSE_MAX_BYTES = 5 * 1024 * 1024
RECONCILE_SLACK_SECONDS = 300
DONE_CLOCK_SLACK_SECONDS = 300  # relay time vs this host's clock
DONE_SCAN_MAX_AGE_SECONDS = 30 * 86400
DONE_KEEP_SECONDS = 90 * 86400

TITLE_MAX = 100
LINE1_MAX = 110  # keeps line 1 inside the 120-character Feishu preview
AUTHOR_MAX = 32  # the user name at the end of line 1 (an `_ident`, so no `[ ] :`), cut to this
LINK_MAX = 300
EXCERPT_LINES = 3
EXCERPT_LINE_MAX = 160
EXCERPT_MAX = 300
COMPARE_MAX = 1 << 20  # "as long as it is": only used to compare a body with its title, never shown

ACTION_LABELS = {
    "assigned": "指派给你",
    "mentioned": "提到了你",
    "directly_addressed": "直接点名了你",
    "review_requested": "请你评审",
    "review_submitted": "有新的评审意见",
    "approval_required": "需要你审批",
    "build_failed": "流水线失败",
    "unmergeable": "无法合并",
    "merge_train_removed": "被移出合并队列",
    "member_access_requested": "有人申请加入",
    "marked": "你标记的待办",
    "okr_checkin_requested": "需要更新 OKR",
    "added_approver": "把你加为审批人",
}
TARGET_SLUGS = {"Issue": "issue", "MergeRequest": "merge_request", "Commit": "commit", "Epic": "epic",
                "WorkItem": "work_item", "Project": "project", "Namespace": "namespace",
                "AlertManagement::Alert": "alert", "DesignManagement::Design": "design"}
REF_PREFIX = {"Issue": "#", "MergeRequest": "!"}


class TodoLocked(sync.SyncError):
    """Another run already holds this state directory."""


class BudgetExceeded(sync.SyncError):
    """The 60-second GitLab budget of this run is used up; pagination turns it into `truncated`."""


# ── configuration ────────────────────────────────────────────────────────────


def _hex64(value: Any, name: str) -> str:
    if not isinstance(value, str) or not sync.HEX64_RE.fullmatch(value):
        raise sync.SyncError(f"{name} must be a lowercase 64-character hex pubkey")
    return value


def _validate_base_url(value: Any) -> None:
    """An exact https origin: no userinfo, backslash, whitespace/control character, bad port, path, query or fragment."""

    problem = "config.gitlab.base_url must be an exact https origin (no userinfo, path, query or fragment)"
    if (not isinstance(value, str) or not value or not value.isascii() or "\\" in value or "?" in value
            or "#" in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise sync.SyncError(problem)
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port  # noqa: B018 - raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        raise sync.SyncError(problem) from None
    if (parsed.scheme != "https" or not parsed.hostname or "@" in parsed.netloc
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise sync.SyncError(problem)


def validate_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise sync.SyncError("config must be a JSON object")
    unknown = sorted(str(key) for key in set(config) - CONFIG_KEYS)
    if unknown:
        raise sync.SyncError(f"config has unknown keys: {', '.join(unknown)}")
    if config.get("version") != 1:
        raise sync.SyncError("config.version must be 1")
    if not isinstance(config.get("channel_id"), str) or not UUID_RE.fullmatch(config["channel_id"]):
        raise sync.SyncError("config.channel_id must be a lowercase UUID")
    publisher = _hex64(config.get("publisher_pubkey"), "config.publisher_pubkey")
    _hex64(config.get("owner_pubkey"), "config.owner_pubkey")
    authors = config.get("done_authors")
    if not isinstance(authors, list) or not authors or len(authors) != len(set(map(str, authors))):
        raise sync.SyncError("config.done_authors must be a non-empty list without duplicates")
    for author in authors:
        _hex64(author, "config.done_authors[]")
    if publisher in authors:
        raise sync.SyncError("config.publisher_pubkey must not be a done author")
    desk = _hex64(config.get("desk_pubkey"), "config.desk_pubkey")
    if desk in {config["owner_pubkey"], publisher, *authors}:
        raise sync.SyncError("config.desk_pubkey must be a distinct bot identity")
    buzz = config.get("buzz")
    if not isinstance(buzz, dict) or set(buzz) != {"cli_path", "cli_sha256"}:
        raise sync.SyncError("config.buzz must have exactly cli_path and cli_sha256")
    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict) or set(gitlab) != GITLAB_KEYS:
        raise sync.SyncError("config.gitlab must have exactly base_url, username, token_env")
    if gitlab["token_env"] != TOKEN_ENV:
        raise sync.SyncError(f"config.gitlab.token_env must be {TOKEN_ENV}")
    _validate_base_url(gitlab["base_url"])
    if not isinstance(gitlab["username"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", gitlab["username"]):
        raise sync.SyncError("config.gitlab.username is invalid")
    todo = config.get("todo")
    if not isinstance(todo, dict):
        raise sync.SyncError("config.todo must be an object")
    unknown = sorted(str(key) for key in set(todo) - TODO_KEYS)
    if unknown:
        raise sync.SyncError(f"config.todo has unknown keys: {', '.join(unknown)}")
    if not isinstance(todo.get("since"), str):
        raise sync.SyncError("config.todo.since is required (UTC, e.g. 2026-09-19T00:00:00Z)")
    _parse_time(todo["since"], "config.todo.since")
    actions = todo.get("actions", DEFAULT_ACTIONS)
    if (not isinstance(actions, list) or not actions
            or any(not isinstance(a, str) or not (a == "*" or ACTION_RE.fullmatch(a)) for a in actions)):
        raise sync.SyncError("config.todo.actions must be a non-empty list of action names or '*'")
    limit = todo.get("max_per_run", DEFAULT_MAX_PER_RUN)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PER_RUN_LIMIT:
        raise sync.SyncError(f"config.todo.max_per_run must be 1..{MAX_PER_RUN_LIMIT}")
    if not isinstance(todo.get("mark_done", False), bool):
        raise sync.SyncError("config.todo.mark_done must be a boolean")
    emojis = todo.get("done_emojis", DEFAULT_DONE_EMOJIS)
    if (not isinstance(emojis, list) or not emojis
            or any(not isinstance(e, str) or not _plain_emoji(e) or len(e) > DONE_EMOJI_MAX
                   or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in e) for e in emojis)):
        raise sync.SyncError(f"config.todo.done_emojis must be a non-empty list of emoji strings "
                             f"(at most {DONE_EMOJI_MAX} characters each, no whitespace or control characters)")
    if len({_plain_emoji(e) for e in emojis}) != len(emojis):
        raise sync.SyncError("config.todo.done_emojis must not repeat an emoji (U+FE0F is ignored)")
    state_dir = config.get("state_dir")
    if state_dir is not None and (not isinstance(state_dir, str) or not Path(state_dir).is_absolute()):
        raise sync.SyncError("config.state_dir must be an absolute path")


def _plain_emoji(text: str) -> str:
    return text.replace(VARIATION_SELECTOR, "")


def _done_emojis(config: dict[str, Any]) -> list[str]:
    return config["todo"].get("done_emojis", DEFAULT_DONE_EMOJIS)


def load_config(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise sync.SyncError(f"cannot read config: {type(exc).__name__}") from None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600):
        raise sync.SyncError("config must be an owner-owned regular file with mode 0600")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # ValueError: bad JSON and bad UTF-8 (UnicodeDecodeError)
        raise sync.SyncError("config is not valid JSON") from None
    validate_config(config)
    return config


def _parse_time(value: str, name: str) -> float:
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise sync.SyncError(f"{name} is not an ISO-8601 time") from None
    if moment.tzinfo is None:
        raise sync.SyncError(f"{name} has no timezone")
    return moment.timestamp()


# ── GitLab: the owner's PAT is confined to three endpoints ───────────────────


class TodoGitLab:
    ALLOWED = (
        ("GET", re.compile(r"user")),
        ("GET", re.compile(r"todos")),
        ("POST", re.compile(r"todos/[0-9]{1,12}/mark_as_done")),
    )

    def __init__(self, config: dict[str, Any], env: dict[str, str], *, opener: Any = None,
                 clock: Callable[[], float] = time.monotonic):
        self.token = sync.validate_token(env.get(TOKEN_ENV), TOKEN_ENV)
        self.api = config["gitlab"]["base_url"].rstrip("/") + "/api/v4"
        self.opener = opener or urllib.request.build_opener(sync.NoRedirectHandler())
        self.clock = clock
        self.spent = 0.0  # seconds spent inside GitLab requests; Buzz scans between them do not count
        self.truncated = False

    def request(self, method: str, path: str, *, params: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
        path = path.lstrip("/")
        if not any(method == verb and pattern.fullmatch(path) for verb, pattern in self.ALLOWED):
            raise sync.SyncError(f"GitLab {method} {path} is outside the todo sync allowlist")
        remaining = GITLAB_BUDGET_SECONDS - self.spent
        if remaining <= 0:
            raise BudgetExceeded("GitLab run budget exceeded")
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api}/{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, method=method, headers={"PRIVATE-TOKEN": self.token})
        started = self.clock()
        try:
            with self.opener.open(request, timeout=min(GITLAB_TIMEOUT_SECONDS, remaining)) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read(GITLAB_RESPONSE_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise sync.GitLabHTTPError(method, path, exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            raise sync.SyncError(f"GitLab {method} {path} failed: {type(exc).__name__}") from None
        finally:
            self.spent += max(0.0, self.clock() - started)
        if len(raw) > GITLAB_RESPONSE_MAX_BYTES:
            raise sync.SyncError(f"GitLab {method} {path} response is too large")
        try:
            return (json.loads(raw) if raw else None), headers
        except json.JSONDecodeError:
            raise sync.SyncError(f"GitLab {method} {path} returned non-JSON") from None

    def user(self) -> dict[str, Any]:
        value, _ = self.request("GET", "user")
        return value if isinstance(value, dict) else {}

    def pending_todos(self) -> list[dict[str, Any]]:
        """Every pending todo up to TODO_PAGE_MAX pages; beyond that, or once the run budget is spent after the
        first page, `truncated` is set instead of failing the round."""

        self.truncated = False
        page, found = 1, []
        while True:
            try:
                values, headers = self.request(
                    "GET", "todos", params={"state": "pending", "per_page": "100", "page": str(page)})
            except BudgetExceeded:
                if page == 1:  # nothing was read: that is a failed round, not a partial list
                    raise
                self.truncated = True
                return found
            if not isinstance(values, list):
                raise sync.SyncError("GitLab todos did not return a list")
            found.extend(item for item in values if isinstance(item, dict))
            next_page = headers.get("x-next-page", "")
            if not next_page:
                return found
            if not PAGE_RE.fullmatch(next_page) or int(next_page) <= page:
                raise sync.SyncError("GitLab todos pagination did not advance")
            if int(next_page) > TODO_PAGE_MAX:
                self.truncated = True
                return found
            page = int(next_page)

    def mark_done(self, todo_id: int) -> None:
        try:
            self.request("POST", f"todos/{int(todo_id)}/mark_as_done")
        except sync.GitLabHTTPError as exc:
            if exc.status != 404:  # the todo is already gone: the goal state is reached
                raise


# ── rendering: GitLab text is data, never structure ──────────────────────────


# The Feishu card shows the message as raw markdown (buzz-deploy ADR-0014), so GitLab text from any
# commenter must not be able to form a link, image, <at>/<font> tag, code span, our header or a bare URL.
NEUTRALISE = str.maketrans({"@": "＠", "<": "＜", ">": "＞", "[": "［", "]": "］", "`": "｀"})
NOSTR_RE = re.compile(r"nostr:", re.IGNORECASE)
HEADER_WORD_RE = re.compile(r"gitlab-todo", re.IGNORECASE)
LINK_CHARS_RE = re.compile(r"[A-Za-z0-9\-._~:/?#%&=+,;]+")


def _defuse(text: str) -> str:
    """`nostr:` becomes a clickable entity reference and `gitlab-todo` is what routing keys on: neither survives in GitLab text.

    The non-breaking hyphen (U+2011) never normalises back to ASCII, so the only `gitlab-todo` left in a
    message is the header on its last line.
    """

    text = NOSTR_RE.sub(lambda match: match.group(0)[:-1] + "：", text)
    return HEADER_WORD_RE.sub(lambda match: match.group(0).replace("-", "\u2011"), text)


def _clean(text: Any, limit: int) -> str:
    """One line, no control/format characters, no active markdown/mentions/links, bounded."""

    kept = "".join(ch if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Cn"} else " "
                   for ch in str(text or ""))
    flat = " ".join(kept.translate(NEUTRALISE).replace("://", "：／／").split())
    return _defuse(flat)[:limit]


def _ident(text: Any) -> str:
    """Project path or user name: the character set has no `[ ] :`, so even `gitlab-todo` in it cannot form a header."""

    return re.sub(r"[^A-Za-z0-9_./-]", "", str(text or ""))[:120] or "?"


def action_name(todo: dict[str, Any]) -> str:
    name = todo.get("action_name")
    return name if isinstance(name, str) and ACTION_RE.fullmatch(name) else "unknown"


def target_key(todo: dict[str, Any]) -> str:
    """One Thread per target.  Group-level targets (epics) have no project: their URL keeps two groups' `&5` apart."""

    target = todo.get("target") if isinstance(todo.get("target"), dict) else {}
    project = todo.get("project") if isinstance(todo.get("project"), dict) else {}
    number = target.get("iid") or target.get("id") or "0"
    project_id = project.get("id")
    if isinstance(project_id, int) and not isinstance(project_id, bool):
        return f"{project_id}:{todo.get('target_type')}:{number}"
    url = target.get("web_url")
    where = url if isinstance(url, str) and url else number
    return f"x:{todo.get('target_type')}:{str(where)[:500]}"


def header_line(todo: dict[str, Any]) -> str:
    slug = TARGET_SLUGS.get(str(todo.get("target_type")), "other")
    return f"[{HEADER_VERSION}][action:{action_name(todo)}][target:{slug}][id:{int(todo['id'])}]"


def _checked_link(url: Any, origin: str) -> str | None:
    """`url` if it is a plain link on the configured GitLab origin, with `gitlab-todo` in the path made inert."""

    if not (isinstance(url, str) and url.startswith(origin) and LINK_CHARS_RE.fullmatch(url)):
        return None
    # `gitlab-todo` in the path is a legitimate project name: keep the link, encode its hyphen (%2D is the same URL)
    link = origin + HEADER_WORD_RE.sub(lambda match: match.group(0).replace("-", "%2D"), url[len(origin):])
    return link if len(link) <= LINK_MAX else None


def _link(todo: dict[str, Any], config: dict[str, Any]) -> str | None:
    """The todo's own `target_url` first: a mention's carries `#note_<id>` (straight to the comment that pinged you),
    a failed pipeline's is the MR's /pipelines page.  `target.web_url` (the target page) is the fallback."""

    target = todo.get("target") if isinstance(todo.get("target"), dict) else {}
    origin = config["gitlab"]["base_url"].rstrip("/") + "/"
    if HEADER_WORD_RE.search(origin):
        return None  # a host name cannot carry %2D: no link rather than a broken or routable one
    for url in (todo.get("target_url"), target.get("web_url")):
        link = _checked_link(url, origin)
        if link:
            return link
    return None


def render_todo(todo: dict[str, Any], config: dict[str, Any]) -> str:
    """Line 1 is what the Feishu card previews (first 120 cleaned characters); the machine header is the last line.

    Layout: title · author, link (else the project path), [excerpt], done hint, header.
    """

    target = todo.get("target") if isinstance(todo.get("target"), dict) else {}
    project = todo.get("project") if isinstance(todo.get("project"), dict) else {}
    author = todo.get("author") if isinstance(todo.get("author"), dict) else {}
    ttype = str(todo.get("target_type"))
    ref = _clean(f"{REF_PREFIX.get(ttype, '')}{target.get('iid')}", 40) if target.get("iid") else _clean(ttype, 40)
    label = ACTION_LABELS.get(action_name(todo), "GitLab 待办")
    # Line 1: "label · ref title · author".  The title gives way to the author suffix, never the other way round.
    who = _ident(author.get("username"))[:AUTHOR_MAX]
    suffix = "" if who == "?" else f" · {who}"
    first = f"{label} · {ref} {_clean(target.get('title'), TITLE_MAX)}".rstrip()
    lines = [first[:LINE1_MAX - len(suffix)].rstrip() + suffix]
    # The project path is already in the link; only a todo without a usable link needs it on a line of its own.
    lines.append(_link(todo, config) or _ident(project.get("path_with_namespace")))
    body = str(todo.get("body") or "")
    # GitLab often repeats the title as the body: then there is nothing to add.  (An empty body yields no lines anyway.)
    if _clean(body, COMPARE_MAX) != _clean(target.get("title"), COMPARE_MAX):
        excerpt, used = [], 0
        for raw in body.replace("\r", "\n").split("\n"):
            text = _clean(raw, EXCERPT_LINE_MAX)
            if not text:
                continue
            excerpt.append(f"> {text}")
            used += len(text)
            if len(excerpt) >= EXCERPT_LINES or used >= EXCERPT_MAX:
                break
        lines.extend(excerpt)
    # done_emojis[0] is validated config (no whitespace or control characters), never GitLab text.  Only the check mark
    # gets a search hint: Buzz Desktop's picker finds it as "check", and no other emoji's search name is known here.
    done_emoji = _done_emojis(config)[0]
    hint = "（表情里搜 check）" if _plain_emoji(done_emoji) == CHECK_MARK else " "
    lines += [f"完成后点 {done_emoji}{hint}或在本 Thread 回复 todo:done:{int(todo['id'])}", header_line(todo)]
    return "\n".join(lines)


# ── state ────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def state_lock(state_dir: Path) -> Iterator[None]:
    state_dir = Path(state_dir)
    sync.prepare_private_dir(state_dir, "todo state dir")
    fd = os.open(state_dir / "todo-sync.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    handle = os.fdopen(fd, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise TodoLocked("todo sync is locked") from None
    try:
        yield
    finally:
        handle.close()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_record(tid: Any, rec: Any) -> bool:
    if not isinstance(tid, str) or not isinstance(rec, dict) or rec.get("state") not in STATES:
        return False
    if not isinstance(rec.get("target"), str) or not isinstance(rec.get("header"), str):
        return False
    if "reply_to" not in rec or (rec["reply_to"] is not None and not isinstance(rec["reply_to"], str)):
        return False
    if not _is_int(rec.get("created")) or not _is_int(rec.get("delivered_at")):
        return False
    if rec["state"] in {"ACKED", "DONE", "RESOLVED"} and not isinstance(rec.get("event_id"), str):
        return False
    return all(rec.get(key) is None or _is_int(rec[key]) for key in ("done_at", "resolved_at", "rejected_at"))


def load_state(state_dir: Path) -> dict[str, Any]:
    """A state file that does not parse or has the wrong shape stops the round: resetting it would re-send everything."""

    path = Path(state_dir) / STATE_FILE
    if not path.exists():
        return {"version": 1, "todos": {}, "threads": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # ValueError: bad JSON and bad UTF-8 (UnicodeDecodeError)
        raise sync.SyncError("todo state is not valid JSON") from None
    if (not isinstance(state, dict) or state.get("version") != 1
            or not isinstance(state.get("todos"), dict) or not isinstance(state.get("threads"), dict)):
        raise sync.SyncError("todo state has an unexpected shape")
    if (not all(_valid_record(tid, rec) for tid, rec in state["todos"].items())
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in state["threads"].items())):
        raise sync.SyncError("todo state has an invalid record")
    return state


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    sync.atomic_write_json(Path(state_dir) / STATE_FILE, state)


# ── run ──────────────────────────────────────────────────────────────────────


def _collect_events(value: Any) -> list[dict[str, Any]]:
    """Every dict in the CLI answer that looks like an event (id, pubkey, content, tags), whatever the wrapping."""

    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if all(key in node for key in ("id", "pubkey", "content", "tags")):
                found.setdefault(str(node["id"]), node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return list(found.values())


class TodoBuzz(sync.BuzzCli):
    """BuzzCli plus the one read the todo sync needs and the GitLab sync does not: reactions in the Channel."""

    def channel_reactions(self, since_unix: int) -> list[dict[str, Any]]:
        """Every kind 7 event in this channel since `since_unix`, paged backwards by time like `channel_messages`.

        A reaction carries a single `e` tag (the message it was put on) and no `h` tag, so unlike `channel_messages`
        nothing is filtered by Channel tag: the `--channel` argument scopes the read and the caller matches the `e`
        tag against the todo messages it delivered.  A withdrawn reaction is no longer returned by the relay.
        """

        found: dict[str, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(sync.CHANNEL_PAGE_MAX):
            args = ["messages", "get", "--channel", self.channel, "--kinds", "7", "--since", str(int(since_unix)),
                    "--limit", str(sync.CHANNEL_PAGE_LIMIT)]
            if before is not None:
                args += ["--before", str(before)]
            page = _collect_events(self.command(args))
            fresh = [event for event in page if str(event["id"]) not in found]
            for event in fresh:
                found[str(event["id"])] = event
            if len(page) < sync.CHANNEL_PAGE_LIMIT:
                break
            # --before is inclusive, so a full page that adds nothing means one second holds more than a page.
            if not fresh:
                raise sync.SyncError("Buzz reaction scan found more same-second reactions than one page holds")
            times = [event.get("created_at") for event in page]
            if not all(_is_int(value) and value > 0 for value in times):
                raise sync.SyncError("Buzz reactions have no created_at to page by")
            before = min(times)
        else:
            raise sync.SyncError("Buzz reaction scan exceeded its page limit")
        return [event for event in found.values() if event.get("kind") == 7]


def make_buzz(config: dict[str, Any], env: dict[str, str], **kwargs: Any) -> Any:
    """The Buzz CLI adapter; BuzzCli builds the child env, which never carries the owner's PAT."""

    view = {"buzz": config["buzz"], "gitlab": {"token_env": TOKEN_ENV},
            "channel_id": config["channel_id"], "publisher_pubkey": config["publisher_pubkey"]}
    return TodoBuzz(view, env, **kwargs)


def check_gates(config: dict[str, Any], gitlab: Any, buzz: Any) -> None:
    if gitlab.user().get("username") != config["gitlab"]["username"]:
        raise sync.SyncError("GitLab PAT does not belong to the configured user")
    members = buzz.channel_members()
    humans = {pubkey for pubkey, role in members.items() if role != "bot"}
    if humans != {config["owner_pubkey"]}:
        raise sync.SyncError("personal Channel must have exactly one human member: the owner")
    if members.get(config["publisher_pubkey"]) != "bot":
        raise sync.SyncError("todo publisher must be a bot member of the personal Channel")
    desk = config["desk_pubkey"]
    if members.get(desk) != "bot":
        raise sync.SyncError("personal Channel Desk must be a bot member")
    allowed = {config["owner_pubkey"], config["publisher_pubkey"], desk, *config["done_authors"]}
    if set(members) - allowed:
        raise sync.SyncError("personal Channel has a member outside the owner, the publisher and the trusted done authors")


def _first_line(event: dict[str, Any]) -> str:
    return str(event.get("content") or "").strip().split("\n", 1)[0].strip()


def _reply_targets(event: dict[str, Any]) -> set[str]:
    """The ids in the event's `e` tags.  Only the id counts, never the root/reply marker; junk entries are skipped."""

    tags = event.get("tags")
    if not isinstance(tags, list):
        return set()
    return {tag[1] for tag in tags
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "e"
            and isinstance(tag[1], str) and sync.HEX64_RE.fullmatch(tag[1])}


def _last_line(event: dict[str, Any]) -> str:
    return str(event.get("content") or "").strip().rsplit("\n", 1)[-1].strip()


def revive_listed(state: dict[str, Any], todos: list[dict[str, Any]]) -> None:
    """RESOLVED is not final: a todo GitLab lists as pending again goes back to ACKED (never re-sent), so its marker counts.

    Presence proves it is pending even when the list is truncated, so this runs on truncated lists too.
    """

    live = {str(todo.get("id")) for todo in todos}
    for tid, rec in state["todos"].items():
        if rec["state"] == "RESOLVED" and tid in live:
            rec["state"] = "ACKED"
            rec.pop("resolved_at", None)


def resolve_gone(state: dict[str, Any], todos: list[dict[str, Any]], now: float) -> int:
    """An ACKED todo that GitLab no longer lists as pending was handled there: settle it, stop scanning for its marker."""

    live = {str(todo.get("id")) for todo in todos}
    gone = [rec for tid, rec in state["todos"].items() if rec["state"] == "ACKED" and tid not in live]
    for rec in gone:
        rec.update(state="RESOLVED", resolved_at=int(now))
    return len(gone)


def reconcile_pending(state: dict[str, Any], config: dict[str, Any], buzz: Any) -> None:
    """A PENDING record whose message is already in the Channel was sent but not acknowledged."""

    pending = {tid: rec for tid, rec in state["todos"].items() if rec["state"] == "PENDING"}
    if not pending:
        return
    since = int(min(rec["created"] for rec in pending.values()) - RECONCILE_SLACK_SECONDS)
    events = [e for e in buzz.channel_messages(since) if e.get("pubkey") == config["publisher_pubkey"]]
    by_header = {_last_line(e): e for e in events}
    for tid, rec in pending.items():
        event = by_header.get(rec["header"])
        if event is None:
            del state["todos"][tid]  # never reached the relay; the delivery pass re-sends it if still pending
            continue
        rec.update(state="ACKED", event_id=event["id"])
        if rec["reply_to"] is None:
            state["threads"].setdefault(rec["target"], event["id"])


def mark_done_phase(state: dict[str, Any], config: dict[str, Any], gitlab: Any, buzz: Any, now: float,
                    save: Callable[[dict[str, Any]], None]) -> int:
    """Two signals, one path: a `todo:done:<id>` reply in the todo's Thread, or a done reaction on the todo's own message.

    Both are read from the same window and handled together in `created_at` order, so one todo is marked once
    however many signals name it, and one failing todo does not stop the ones behind it.
    """

    awaiting = {tid: rec for tid, rec in state["todos"].items() if rec["state"] == "ACKED"}
    if not config["todo"].get("mark_done", False) or not awaiting:
        return 0
    # The read window must reach back as far as the acceptance bound below (`delivered_at - DONE_CLOCK_SLACK_SECONDS`):
    # a narrower window would never even read a valid signal written inside that slack.  One `since` for both reads.
    since = int(max(min(rec["delivered_at"] for rec in awaiting.values()) - DONE_CLOCK_SLACK_SECONDS,
                    now - DONE_SCAN_MAX_AGE_SECONDS))
    authors = set(config["done_authors"])
    done_emojis = {_plain_emoji(emoji) for emoji in _done_emojis(config)}
    by_message: dict[str, list[str]] = {}  # a reaction counts on the todo's own message only, never on its Thread root
    for tid, rec in awaiting.items():
        by_message.setdefault(rec["event_id"], []).append(tid)
    marked, failed, last_error = 0, set(), None
    signals = [(event, False) for event in buzz.channel_messages(since)]
    signals += [(event, True) for event in buzz.channel_reactions(since) if event.get("kind") == 7]
    signals = sorted(((event, is_reaction) for event, is_reaction in signals if _is_int(event.get("created_at"))),
                     key=lambda item: (item[0]["created_at"], str(item[0].get("id"))))
    for event, is_reaction in signals:
        if event.get("pubkey") not in authors:
            continue
        if is_reaction:
            emoji = event.get("content")
            if not isinstance(emoji, str) or _plain_emoji(emoji) not in done_emojis:
                continue
            named = [tid for message in sorted(_reply_targets(event)) for tid in by_message.get(message, ())]
        else:
            match = DONE_RE.fullmatch(_first_line(event))
            named = [match.group(1)] if match else []
        for tid in dict.fromkeys(named):
            record = state["todos"].get(tid)
            if record is None or record["state"] != "ACKED":
                continue
            if event["created_at"] < record["delivered_at"] - DONE_CLOCK_SLACK_SECONDS:
                continue  # written before this todo was delivered: cannot be an answer to it
            if not is_reaction and not _reply_targets(event) & {record["event_id"], record["reply_to"]}:
                continue  # not a reply into this todo's Thread (its own message, or the root it was posted under)
            if tid in failed:
                continue  # already refused this round: a second signal for it does not get a second request
            try:
                gitlab.mark_done(int(tid))
            except sync.SyncError as exc:  # one permanently failing todo must not starve the signals behind it
                failed.add(tid)
                last_error = exc
                continue
            record["state"] = "DONE"
            record["done_at"] = int(now)
            marked += 1
            save(state)
    if last_error is not None:
        raise last_error  # the round still fails; the failed todos stay ACKED and are retried next round
    return marked


def deliver_phase(state: dict[str, Any], config: dict[str, Any], todos: list[dict[str, Any]], buzz: Any,
                  now: float, save: Callable[[dict[str, Any]], None]) -> dict[str, int]:
    todo_cfg = config["todo"]
    actions = set(todo_cfg.get("actions", DEFAULT_ACTIONS))
    since = _parse_time(todo_cfg["since"], "config.todo.since")
    counts = {"seen": 0, "delivered": 0, "filtered": 0, "invalid": 0, "rejected": 0}
    fresh: dict[int, dict[str, Any]] = {}
    for todo in todos:
        counts["seen"] += 1
        tid = todo.get("id")
        if isinstance(tid, bool) or not isinstance(tid, int) or tid < 1:
            counts["invalid"] += 1
            continue
        if str(tid) in state["todos"] or tid in fresh:
            continue
        try:
            created = _parse_time(str(todo.get("created_at")), "todo.created_at")
        except sync.SyncError:
            counts["invalid"] += 1
            continue
        if created < since or ("*" not in actions and action_name(todo) not in actions):
            counts["filtered"] += 1
            continue
        fresh[tid] = todo
    rejected_pending: list[tuple[str, dict[str, Any]]] = []
    for tid in sorted(fresh)[: todo_cfg.get("max_per_run", DEFAULT_MAX_PER_RUN)]:
        todo = fresh[tid]
        key = target_key(todo)
        rec = {"state": "PENDING", "target": key, "header": header_line(todo),
               "reply_to": state["threads"].get(key), "created": int(now), "delivered_at": int(now)}
        state["todos"][str(tid)] = rec
        save(state)  # durable PENDING first: a crash after send is reconciled from the Channel
        text = render_todo(todo, config)
        mentions = (config["owner_pubkey"],)
        try:
            event_id = buzz.send(text, reply_to=rec["reply_to"], mentions=mentions)
        except sync.BuzzSendRejected:
            event_id = None
            if rec["reply_to"] is not None:  # the Thread root may be gone: one top-level retry
                state["threads"].pop(key, None)
                rec["reply_to"] = None
                save(state)
                try:
                    event_id = buzz.send(text, reply_to=None, mentions=mentions)
                except sync.BuzzSendRejected:
                    event_id = None
            if event_id is None:
                # Refused twice.  Whether that is this one todo or the whole Channel is not known yet: forget the
                # PENDING record (next round retries it) and only settle it as REJECTED once a later send succeeds.
                del state["todos"][str(tid)]
                rejected_pending.append((str(tid), rec))
                save(state)
                if len(rejected_pending) >= 2:
                    raise sync.SyncError(f"Buzz rejected {len(rejected_pending)} consecutive sends")
                continue
        rec.update(state="ACKED", event_id=event_id)
        if rec["reply_to"] is None:
            state["threads"][key] = event_id
        for rejected_tid, rejected in rejected_pending:  # an accepted send: the earlier refusals were per message
            rejected.update(state="REJECTED", rejected_at=int(now))
            state["todos"][rejected_tid] = rejected
            counts["rejected"] += 1
        rejected_pending.clear()
        save(state)
        counts["delivered"] += 1
    if rejected_pending:  # no accepted send after it: nothing shows the refusal is not systemic
        raise sync.SyncError("Buzz rejected the last send and accepted none after it")
    return counts


def prune(state: dict[str, Any], now: float) -> None:
    stamps = {"DONE": "done_at", "RESOLVED": "resolved_at"}
    for tid in [t for t, rec in state["todos"].items()
                if rec["state"] in stamps and rec.get(stamps[rec["state"]], now) < now - DONE_KEEP_SECONDS]:
        del state["todos"][tid]


def run(config: dict[str, Any], env: dict[str, str], *, state_dir: Path, gitlab: Any = None, buzz: Any = None,
        clock: Callable[[], float] = time.time) -> dict[str, Any]:
    validate_config(config)
    gitlab = gitlab or TodoGitLab(config, env)
    buzz = buzz or make_buzz(config, env)
    now = clock()
    try:
        with state_lock(Path(state_dir)):
            check_gates(config, gitlab, buzz)
            state = load_state(state_dir)

            def save(value: dict[str, Any]) -> None:
                save_state(Path(state_dir), value)

            deferred: sync.SyncError | None = None
            try:
                todos = gitlab.pending_todos()  # before any Buzz scan, so settled todos leave the scan window
                truncated = bool(gitlab.truncated)
                revive_listed(state, todos)
                resolved = 0 if truncated else resolve_gone(state, todos, now)
                reconcile_pending(state, config, buzz)
                try:
                    marked = mark_done_phase(state, config, gitlab, buzz, now, save)
                except sync.SyncError as exc:
                    deferred, marked = exc, 0  # a failed GitLab write must not stall delivery; fail the round after it
                counts = deliver_phase(state, config, todos, buzz, now, save)
                prune(state, now)
            finally:
                save(state)
            if deferred is not None:
                raise deferred
    except TodoLocked:
        return {"status": "locked"}
    return {"status": "ok", "marked_done": marked, "resolved": resolved, "truncated": truncated, **counts}


# ── entrypoint ───────────────────────────────────────────────────────────────


def _redact(value: Any, secrets: list[str]) -> Any:
    """Mask secrets in every string of the payload before it is serialised (JSON escaping would hide them from a text replace)."""

    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    return value


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None,
         stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)  # zero arguments: the owner-fixed env names the config
    runtime_env = dict(os.environ if env is None else env)
    out = stdout or sys.stdout
    secrets = [value for value in (runtime_env.get(TOKEN_ENV), runtime_env.get("BUZZ_PRIVATE_KEY")) if value]

    def emit(payload: dict[str, Any]) -> None:
        text = json.dumps(_redact(payload, secrets), ensure_ascii=False, separators=(",", ":"))
        out.write(text + "\n")

    path = runtime_env.get(CONFIG_ENV)
    if not path:
        emit({"status": "error", "error": f"{CONFIG_ENV} is required"})
        return 2
    try:
        config = load_config(Path(path))
        if not config.get("state_dir"):
            raise sync.SyncError("config.state_dir is required")
        derived = sync.publisher_pubkey_from_private_key(runtime_env.get("BUZZ_PRIVATE_KEY"))
        if derived != config["publisher_pubkey"]:
            raise sync.SyncError("BUZZ_PRIVATE_KEY does not match config.publisher_pubkey")
        result = run(config, runtime_env, state_dir=Path(config["state_dir"]))
    except sync.SyncError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except (OSError, ValueError, http.client.HTTPException) as exc:
        emit({"status": "error", "error": type(exc).__name__})  # the message may carry paths or peer text
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
